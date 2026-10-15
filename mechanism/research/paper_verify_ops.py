"""READ-ONLY operator check of the forward paper tracker after an automatic session. Not imported by any job; run it by piping it into the pinned image, e.g.

    docker compose ... run --rm -T channel-sender - baseline  < paper_verify_ops.py       # record hashes of existing evidence (read-only)
    docker compose ... run --rm -T channel-sender - verify    < paper_verify_ops.py       # verify the latest processed session

It opens a read-only transaction, writes nothing, and prints one PASS / FAIL line per check. Exit status 1 when any check fails.
Checks: continuity, expected entries, per-variant independence, duplicates, hash chains and snapshot hashes, an idempotent re-process refusal, market-context timestamps, and
that the immutable evidence that existed at `baseline` time is byte-for-byte unchanged."""
import hashlib
import json
import os
import sys
from datetime import date, datetime

import psycopg2

sys.path.insert(0, "/app/mechanism")
from research import paper_runner as PR  # noqa: E402
from research import paper_store as ST  # noqa: E402

BASELINE = os.environ.get("PAPER_VERIFY_BASELINE", "/tmp/paper_evidence_baseline.json")
# Immutable evidence whose past rows must never change: (table, order column, row filter at baseline time)
EVIDENCE = [
    ("candidate_observation", "id", "session_date <= %(cut)s"),
    ("feature_snapshot", "id", "id IN (SELECT snapshot_id FROM candidate_observation WHERE session_date <= %(cut)s)"),
    ("strategy_variant", "id", "TRUE"),
    ("market_snapshot", "id", "session_date <= %(cut)s"),
]


def connect():
    return psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ["DB_PORT"], dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
                            options="-c default_transaction_read_only=on -c search_path=public")


def table_hash(cur, table, order, where, cut):
    cur.execute(f"SELECT count(*), md5(string_agg(t::text, '|' ORDER BY t.{order})) FROM {table} t WHERE {where.replace('%(cut)s', '%s')}", (cut,) if "%s" in where.replace("%(cut)s", "%s") else None)
    n, h = cur.fetchone()
    return {"rows": n, "md5": h}


def baseline(cur):
    cur.execute("SELECT max(session_date) FROM paper_session")
    cut = cur.fetchone()[0]
    out = {"cut": cut.isoformat(), "tables": {}}
    for table, order, where in EVIDENCE:
        out["tables"][table] = table_hash(cur, table, order, where, cut)
    cur.execute("SELECT session_date, open_state_hash FROM paper_session WHERE session_date <= %s ORDER BY session_date", (cut,))
    out["paper_sessions"] = [[d.isoformat(), h] for d, h in cur.fetchall()]
    cur.execute("SELECT count(*), md5(string_agg(observation_hash, '|' ORDER BY observation_hash)) FROM paper_observation o WHERE o.signal_session <= %s", (cut,))
    out["paper_observations_to_cut"] = list(cur.fetchone())
    with open(BASELINE, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=str)
    print("BASELINE written:", json.dumps({k: v for k, v in out.items() if k != "paper_sessions"}, default=str))
    print("paper sessions in baseline:", out["paper_sessions"])


def main(mode):
    conn = connect()
    cur = conn.cursor()
    if mode == "baseline":
        baseline(cur)
        conn.rollback()
        return 0
    fails = []

    def check(name, ok, detail=""):
        print(("PASS " if ok else "FAIL ") + name + ((" :: " + str(detail)) if detail != "" else ""))
        if not ok:
            fails.append(name)

    cur.execute("SELECT session_date, open_state_hash, n_candidates, n_registered, n_skipped_open, n_entered, n_closed, n_not_enterable, n_open_after, open_state FROM paper_session ORDER BY session_date")
    sessions = cur.fetchall()
    S = sessions[-1]
    sd = S[0]
    print(f"latest processed session: {sd} ({len(sessions)} sessions processed in total)")
    # 1. continuity
    cal = PR.session_calendar(cur, sessions[0][0])
    expected = [d for d in cal if d >= sessions[0][0]][: len(sessions)]
    check("continuity: processed sessions are consecutive trading sessions from the activation session", [s[0] for s in sessions] == expected, [s[0].isoformat() for s in sessions])
    # 2. the stored snapshot hashes
    check("snapshot hashes recompute for every processed session", all(ST.sha(s[9]) == s[1] for s in sessions))
    # 3. the audit (chains, order, linkage)
    problems = ST.verify(cur)
    check("audit: event chains, ordering and session linkage verify clean", not problems, problems[:3])
    # 4. entries expected from the previous session's registrations
    if len(sessions) >= 2:
        P = sessions[-2][0]
        cur.execute("SELECT count(*) FROM paper_observation WHERE signal_session = %s", (P,))
        registered_prev = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM paper_event e JOIN paper_observation o ON o.id = e.observation_id WHERE e.event_type = 'entered' AND e.session_date = %s AND o.signal_session = %s", (sd, P))
        entered = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM paper_event e JOIN paper_observation o ON o.id = e.observation_id WHERE e.event_type = 'not_enterable' AND e.session_date = %s AND o.signal_session = %s", (sd, P))
        not_ent = cur.fetchone()[0]
        check("every observation registered on the previous session either entered at this session's open or is explicitly not_enterable", entered + not_ent == registered_prev, f"registered {registered_prev}, entered {entered}, not_enterable {not_ent}")
        cur.execute("SELECT count(*) FROM paper_event e JOIN paper_observation o ON o.id = e.observation_id WHERE e.event_type = 'entered' AND e.session_date = %s AND o.signal_session = %s AND "
                    "(e.payload->>'entry_price')::float <> (SELECT p.open::float FROM stock_prices p WHERE p.symbol = o.symbol AND p.date = %s)", (sd, P, sd))
        check("entry price equals the session's actual open for every entered position", cur.fetchone()[0] == 0)
        cur.execute("SELECT count(*) FROM paper_event e WHERE e.event_type = 'closed' AND e.session_date = %s", (sd,))
        closed = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM paper_event e JOIN paper_observation o ON o.id = e.observation_id WHERE e.event_type = 'closed' AND e.session_date = %s AND "
                    "NOT ((e.payload->>'exit_price')::float BETWEEN (SELECT p.low::float FROM stock_prices p WHERE p.symbol = o.symbol AND p.date = %s) - 1e-6 AND (SELECT p.high::float FROM stock_prices p WHERE p.symbol = o.symbol AND p.date = %s) + 1e-6 OR e.payload->>'reason' = 'time')", (sd, sd, sd))
        check(f"every close ({closed}) happened at a price inside that session's range (time exits excepted)", cur.fetchone()[0] == 0)
        check("session counters equal the stored events", (S[5], S[6]) == (entered, closed), f"session row entered {S[5]} closed {S[6]}")
    # 5. independence and duplicates
    state = S[9]
    active = state["active"]
    per = {}
    keys = set()
    dup = 0
    for ob in active:
        k = (ob.get("variant_key") or ob.get("variant"), ob["symbol"], ob["direction"])
        if k in keys:
            dup += 1
        keys.add(k)
        per[k[0]] = per.get(k[0], 0) + 1
    check("open positions: at most one per (variant, symbol, direction)", dup == 0, f"{len(active)} open: {per}")
    cur.execute("SELECT count(*) - count(DISTINCT observation_hash) FROM paper_observation")
    check("no duplicate observations", cur.fetchone()[0] == 0)
    cur.execute("SELECT count(*) - count(DISTINCT event_hash) FROM paper_event")
    check("no duplicate events", cur.fetchone()[0] == 0)
    cur.execute("SELECT count(*) FROM (SELECT observation_id FROM paper_event WHERE event_type IN ('entered','closed') GROUP BY observation_id, event_type HAVING count(*) > 1) x")
    check("at most one entered and one closed event per observation", cur.fetchone()[0] == 0)
    cur.execute("SELECT v.variant_key, v.definition_hash, count(*) FROM paper_observation o JOIN strategy_variant v ON v.id = o.variant_id WHERE o.signal_session = %s GROUP BY 1, 2 ORDER BY 1", (sd,))
    rows = cur.fetchall()
    check("all five registered variants registered the same candidate set", len(rows) == 5 and len({r[2] for r in rows}) == 1, [(r[0].split("__exit_")[1], r[1][:10], r[2]) for r in rows])
    # 6. idempotent re-process is a read-only refusal to write
    conn.rollback()
    cur = conn.cursor()
    cur.execute("SELECT (SELECT count(*) FROM paper_session), (SELECT count(*) FROM paper_observation), (SELECT count(*) FROM paper_event)")
    before = cur.fetchone()
    try:
        res = PR.process_session(cur, sd, PR.CandidateSource(), write=False, code_ref="verify")
        check("idempotent: processing the same session again reports already_processed", res.status == "already_processed", res.status)
    except Exception as e:  # noqa: BLE001
        check("idempotent: processing the same session again reports already_processed", False, repr(e)[:160])
    conn.rollback()
    cur = conn.cursor()
    cur.execute("SELECT (SELECT count(*) FROM paper_session), (SELECT count(*) FROM paper_observation), (SELECT count(*) FROM paper_event)")
    check("and changed no row", cur.fetchone() == before, before)
    # 7. market-context timestamps: every attached item was available at the decision cutoff, and the session is the one processed
    cur.execute("SELECT count(*), count(*) FILTER (WHERE (context->>'session')::date = o.signal_session), "
                "count(*) FILTER (WHERE context->'market_regime'->>'available' = 'true'), "
                "count(*) FILTER (WHERE context->'market_regime'->>'available' = 'true' AND (context->'market_regime'->>'available_at')::timestamptz > (context->>'decision_cutoff')::timestamptz), "
                "count(*) FILTER (WHERE (context->'features'->>'available_at')::timestamptz > (context->>'decision_cutoff')::timestamptz), "
                "count(*) FILTER (WHERE context->'relative_strength'->>'available' = 'true'), "
                "count(*) FILTER (WHERE context->'catalysts'->>'section' = 'unavailable' AND context->'catalysts'->>'reason' IS NULL) "
                "FROM paper_observation o WHERE o.signal_session = %s", (sd,))
    n, same, reg, reg_late, feat_late, rs, cat_unexplained = cur.fetchone()
    check("context session equals the processed session", n == same, f"{n} observations")
    check("market regime attached where observed, and never later than the decision cutoff", reg_late == 0, f"{reg}/{n} with regime")
    check("feature snapshot never later than the decision cutoff", feat_late == 0)
    check("every unavailable catalyst section carries a reason", cat_unexplained == 0)
    print(f"INFO relative strength attached for {rs}/{n}; unavailable ones carry a reason")
    # 8. existing evidence unchanged
    if os.path.exists(BASELINE):
        with open(BASELINE, encoding="utf-8") as fh:
            base = json.load(fh)
        cut = date.fromisoformat(base["cut"])
        for table, order, where in EVIDENCE:
            now = table_hash(cur, table, order, where, cut)
            check(f"existing evidence unchanged: {table} (rows up to {cut})", now == base["tables"][table], f"baseline {base['tables'][table]['rows']} rows, now {now['rows']}")
        cur.execute("SELECT session_date, open_state_hash FROM paper_session WHERE session_date <= %s ORDER BY session_date", (cut,))
        check("earlier paper sessions unchanged (snapshot hashes)", [[d.isoformat(), h] for d, h in cur.fetchall()] == base["paper_sessions"])
        cur.execute("SELECT count(*), md5(string_agg(observation_hash, '|' ORDER BY observation_hash)) FROM paper_observation o WHERE o.signal_session <= %s", (cut,))
        check("earlier paper observations unchanged (content hashes)", list(cur.fetchone()) == base["paper_observations_to_cut"])
    else:
        print("INFO no baseline file at", BASELINE, "- run `baseline` first to compare existing evidence")
    conn.rollback()
    print("RESULT:", "ALL CHECKS PASSED" if not fails else f"{len(fails)} CHECK(S) FAILED: {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "verify"))
