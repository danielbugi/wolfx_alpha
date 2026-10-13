"""Shadow test of the forward paper tracker on a SCRATCH database (never production): `python paper_shadow.py real|historical`.

Needs a scratch restore of the production backup with migrations 35 and 36 applied, the five variants registered and the roles script applied; connects as the least-privilege runtime role
(`donchian_app`) so the privileges are proven too. Refuses to run against a database named trading_production unless SHADOW_SCRATCH=1 is set (the scratch restore keeps that name).

  real        the ONE real forward capture that exists (candidate_observation of the newest captured session): a dry run (writes nothing), then the real write path into the scratch tables, then the
              restore / idempotency / refusal checks. Shows what the real source and its decision-time context look like on production-shaped data.
  historical  hundreds of consecutive sessions of the dataset replay (ml_breakout_dataset_v2 signals) pushed through the SAME process_session / store code, session by session, compared with the batch
              experiment: position by position, field by field; plus idempotent re-runs, dry-run equality, the audit, and sizes.
"""
import hashlib
import json
import os
import sys
import time
import warnings
from datetime import date

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path[:0] = ["/app", "/app/mechanism", "/exp"]
from ml_training.features import price_features as pf  # noqa: E402
from research import experiment_runner as E  # noqa: E402
from research import paper_runner as PR  # noqa: E402
from research import paper_store as ST  # noqa: E402
import psycopg2  # noqa: E402

OUT = "/out"
MODE = sys.argv[1] if len(sys.argv) > 1 else "real"
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:5.0f}s]", *a, flush=True)


if os.environ["DB_NAME"] == "trading_production" and os.environ.get("SHADOW_SCRATCH") != "1":
    print("refusing: this looks like the production database name; set SHADOW_SCRATCH=1 ONLY inside the isolated scratch restore", file=sys.stderr)
    sys.exit(3)
conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ["DB_PORT"], dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])
cur = conn.cursor()
cur.execute("SET search_path = public")
conn.commit()
res = {"mode": MODE, "database_user": os.environ["DB_USER"], "scratch": True}


def _require_empty_tracker():
    for t in ("paper_session", "paper_observation", "paper_event"):
        cur.execute(f"SELECT count(*) FROM {t}")
        if cur.fetchone()[0]:
            print(f"refusing: {t} is not empty; every shadow mode needs a freshly migrated scratch (earlier evidence would make the replay 'already processed')", file=sys.stderr)
            sys.exit(3)
    conn.rollback()


_require_empty_tracker()


def counts():
    out = {}
    for t in ("paper_session", "paper_observation", "paper_event"):
        cur.execute(f"SELECT count(*) FROM {t}")
        out[t] = cur.fetchone()[0]
    return out


def commit_session(d, source, scope, **kw):
    try:
        r = PR.process_session(cur, d, source, scope=scope, code_ref=os.environ.get("CODE_REF", "unknown"), **kw)
        if kw.get("write"):
            conn.commit()
        else:
            conn.rollback()
        return r
    except Exception:
        conn.rollback()
        raise


if MODE == "real":
    cur.execute("SELECT max(session_date) FROM candidate_capture_run WHERE status = 'complete'")
    S = cur.fetchone()[0]
    conn.commit()
    res["session"] = S.isoformat()
    cal = PR.session_calendar(cur, date.fromordinal(S.toordinal() - 10))
    conn.commit()
    res["calendar_tail"] = [x.isoformat() for x in cal[-3:]]
    src = PR.CandidateSource()
    before = counts()
    dry = commit_session(S, src, "triggered", write=False, activation_session=True, calendar=cal)
    after_dry = counts()
    res["dry_run"] = {**dry.summary(), "tables_unchanged_by_dry_run": before == after_dry}
    # what the decision-time context looks like on real data
    regs = [e for e in dry.events if e["type"] == "registered"]
    per_signal = {}
    for e in regs:
        per_signal.setdefault((e["payload"]["symbol"], e["payload"]["direction"]), e["payload"]["context"])
    avail = {"signals": len(per_signal), "market_regime_available": sum(1 for c in per_signal.values() if (c.get("market_regime") or {}).get("available")),
             "relative_strength_available": sum(1 for c in per_signal.values() if c.get("relative_strength")), "catalysts_with_events": sum(1 for c in per_signal.values() if (c.get("catalysts") or {}).get("events")),
             "sector_known": sum(1 for c in per_signal.values() if (c.get("features") or {}).get("sector")), "guard_rejected": sum(1 for c in per_signal.values() if (c["candidate"]).get("passed_guard") is False),
             "tracked_intent": sum(1 for c in per_signal.values() if c["candidate"].get("tracked_intent"))}
    res["context_availability"] = avail
    sample = next(iter(per_signal.values()), None)
    res["sample_context_shape"] = {k: (sorted(v.keys()) if isinstance(v, dict) else type(v).__name__) for k, v in (sample or {}).items()}
    written = commit_session(S, src, "triggered", write=True, activation_session=True, calendar=cal)
    res["write"] = {**written.summary(), "tables_after": counts()}
    res["write_matches_dry_run"] = dry.open_state_hash == written.open_state_hash and [e["type"] for e in dry.events] == [e["type"] for e in written.events]
    last = ST.latest_session(cur)
    res["restore"] = {"snapshot_hash_verified": last["open_state_hash"] == ST.sha(last["open_state"]), "open_observations_in_snapshot": len(last["open_state"]["active"]), "session": last["session_date"].isoformat()}
    again = commit_session(S, src, "triggered", write=True, activation_session=True, calendar=cal)
    res["rerun_same_session"] = {"status": again.status, "tables_unchanged": counts() == res["write"]["tables_after"]}
    nxt = date.fromordinal(S.toordinal() + 3)
    try:
        commit_session(nxt, src, "triggered", write=True, calendar=cal + [nxt])
        res["next_session_without_capture"] = "UNEXPECTEDLY PROCESSED"
    except PR.SessionRefused as e:
        res["next_session_without_capture"] = f"refused: {e}"
    res["audit_problems"] = ST.verify(cur)
    conn.rollback()
    cur.execute("SELECT count(*), count(DISTINCT observation_hash) FROM paper_observation")
    res["observations_distinct_hashes"] = list(cur.fetchone())
    cur.execute("SELECT candidate_observation_id IS NOT NULL, count(*) FROM paper_observation GROUP BY 1")
    res["observations_linked_to_candidate_rows"] = {str(k): v for k, v in cur.fetchall()}
    cur.execute("SELECT pg_total_relation_size('paper_observation'), pg_total_relation_size('paper_session')")
    res["bytes"] = {"paper_observation": cur.fetchone()[0]}
    cur.execute("SELECT pg_total_relation_size('paper_session')")
    res["bytes"]["paper_session"] = cur.fetchone()[0]

elif MODE == "historical":
    START, END = date(2023, 1, 3), date(2026, 10, 9)
    N_SYMBOLS = int(os.environ.get("SHADOW_SYMBOLS", "150"))
    cur.execute("SELECT DISTINCT symbol FROM ml_breakout_dataset_v2 WHERE feature_set_version = %s AND date BETWEEN %s AND %s", (pf.FEATURE_SET_VERSION, START, END))
    symbols = sorted(sorted(r[0] for r in cur.fetchall()), key=lambda s: hashlib.md5(s.encode()).hexdigest())[:N_SYMBOLS]
    cur.execute("SELECT symbol, date, direction FROM ml_breakout_dataset_v2 WHERE feature_set_version = %s AND symbol = ANY(%s) AND date BETWEEN %s AND %s ORDER BY symbol, date",
                (pf.FEATURE_SET_VERSION, symbols, START, END))
    sigs = pd.DataFrame(cur.fetchall(), columns=["symbol", "date", "direction"])
    sigs["date"] = pd.to_datetime(sigs["date"])
    conn.commit()
    by_session, arrays, ref_signals = {}, {}, {}
    for sym, g in sigs.groupby("symbol"):
        cur.execute("SELECT date, open, high, low, close, volume FROM stock_prices WHERE symbol = %s ORDER BY date", (sym,))
        px = pd.DataFrame(cur.fetchall(), columns=["date", "open", "high", "low", "close", "volume"]).dropna(subset=["close"]).drop_duplicates("date").reset_index(drop=True)
        conn.commit()
        px["date"] = pd.to_datetime(px["date"])
        for k in ("open", "high", "low", "close", "volume"):
            px[k] = pd.to_numeric(px[k], errors="coerce")
        if len(px) < 300:
            continue
        atr = pf.compute_indicators(px)["atr"].to_numpy(float)
        o, h, l, c, v = (px[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
        dts = [pd.Timestamp(x).date() for x in px["date"]]
        arrays[sym] = (o, h, l, c, dts, atr)
        for r in g.itertuples(index=False):
            i = int(np.searchsorted(px["date"].to_numpy(), np.datetime64(r.date)))
            if i >= len(dts) - 1 or i < 60 or dts[i] != r.date.date() or not (np.isfinite(atr[i]) and atr[i] > 0 and np.isfinite(h[max(0, i - 9):i + 1]).all() and np.isfinite(l[max(0, i - 9):i + 1]).all()):
                continue
            f = E.signal_features(o, h, l, c, v, atr, i)
            by_session.setdefault(dts[i], []).append(PR.Signal(sym, int(r.direction), float(atr[i]), [float(x) for x in h[i - 9:i + 1]], [float(x) for x in l[i - 9:i + 1]],
                                                               {"source": "dataset_replay", "pit": {k: (None if not np.isfinite(x) else x) for k, x in f.items()}, "decision_cutoff": dts[i].isoformat()}))
            ref_signals.setdefault(sym, []).append((i, int(r.direction)))
    src = PR.ListSource(by_session)
    cal = [d for d in PR.session_calendar(cur, START) if d <= END]
    conn.commit()
    first = min(by_session)
    sessions = [d for d in cal if d >= first]
    res["design"] = {"symbols": len(arrays), "signals": int(sum(len(v) for v in by_session.values())), "sessions": len(sessions), "first_session": first.isoformat(), "last_session": sessions[-1].isoformat()}
    log("replay set:", res["design"])
    times, statuses = [], {}
    dry_check = None
    for n, d in enumerate(sessions):
        t1 = time.time()
        if n == len(sessions) - 1:                                   # dry run the LAST session first, then write it: the two must produce the same events
            dry = commit_session(d, src, "triggered", write=False, activation_session=(n == 0), calendar=cal)
            dry_check = dry
        r = commit_session(d, src, "triggered", write=True, activation_session=(n == 0), calendar=cal)
        statuses[r.status] = statuses.get(r.status, 0) + 1
        times.append(time.time() - t1)
        if n % 100 == 0:
            log("session", d, counts())
    res["sessions_processed"] = statuses
    res["seconds_per_session"] = {"mean": round(float(np.mean(times)), 3), "p95": round(float(np.percentile(times, 95)), 3), "max": round(float(np.max(times)), 3)}
    res["dry_run_equals_write_for_the_last_session"] = bool(dry_check and dry_check.open_state_hash == r.open_state_hash and [(e["type"], e["ident"]) for e in dry_check.events] == [(e["type"], e["ident"]) for e in r.events])
    res["tables"] = counts()
    cur.execute("SELECT event_type, count(*) FROM paper_event GROUP BY 1 ORDER BY 1")
    res["events_by_type"] = {k: v for k, v in cur.fetchall()}
    cur.execute("SELECT count(*), max(session_date) FROM paper_event WHERE event_type = 'hindsight'")
    row = cur.fetchone()
    res["hindsight"] = {"events": row[0], "latest_session": row[1]}
    conn.commit()
    # idempotent re-runs
    before = counts()
    rerun = [commit_session(d, src, "triggered", write=True, calendar=cal).status for d in sessions[::97]]
    res["rerun_sessions"] = {"statuses": sorted(set(rerun)), "tables_unchanged": counts() == before}
    # field-by-field comparison with the batch experiment (same signals, one-open-position rule, 60-bar registered cap)
    variants = E.registered_variants()
    ref = {}
    for sym, (o, h, l, c, dts, atr) in arrays.items():
        sg = [x for x in ref_signals.get(sym, []) if dts[x[0]] >= first]
        _, pos = E.simulate_symbol(variants, [60], sym, o, h, l, c, dts, atr, sg)
        for p in pos:
            ref[(p.variant.key, sym, p.direction, p.signal_date)] = p
    cur.execute("SELECT v.variant_key, o.symbol, o.direction, o.signal_session, e.payload FROM paper_event e JOIN paper_observation o ON o.id = e.observation_id JOIN strategy_variant v ON v.id = o.variant_id "
                "WHERE e.event_type = 'closed'")
    closed = {(k, s, d, sg): pl for k, s, d, sg, pl in cur.fetchall()}
    cur.execute("SELECT count(*) FROM paper_observation")
    n_obs = cur.fetchone()[0]
    conn.commit()
    done = {k for k, p in ref.items() if not p.censored}
    mism = []
    for k, pl in closed.items():
        p = ref.get(k)
        if p is None or p.censored:
            mism.append(("unexpected_close", k))
            continue
        t = p.trade
        got = (pl["exit_price"], pl["reason"], pl["bars_held"], pl["r_gross_std"], pl["mfe_r_std"], pl["mae_r_std"], pl["initial_stop"], pl["initial_risk"], pl["entry_session"], pl["exit_session"])
        want = (t.exit_price, t.reason, t.duration, t.r_gross, t.mfe_r, t.mae_r, t.initial_stop, t.initial_risk, p.entry_date.isoformat(), p.exit_date.isoformat())
        if any((abs(a - b) > 1e-9 if isinstance(a, float) or isinstance(b, float) else a != b) for a, b in zip(got, want)):
            mism.append(("different", k))
    res["comparison_with_batch"] = {"batch_positions": len(ref), "batch_closed": len(done), "stored_observations": n_obs, "stored_closed": len(closed), "missing_closed": len(done - set(closed)),
                                    "mismatches": len(mism), "first_mismatch": str(mism[0]) if mism else None, "observations_equal_batch_positions": n_obs == len(ref)}
    res["audit_problems"] = ST.verify(cur)
    conn.rollback()
    cur.execute("SELECT pg_total_relation_size('paper_session'), pg_total_relation_size('paper_observation'), pg_total_relation_size('paper_event')")
    sz = cur.fetchone()
    res["bytes"] = {"paper_session": sz[0], "paper_observation": sz[1], "paper_event": sz[2]}
    cur.execute("SELECT avg(pg_column_size(open_state)), max(pg_column_size(open_state)) FROM paper_session")
    a, m = cur.fetchone()
    res["open_state_bytes"] = {"avg": int(a or 0), "max": int(m or 0)}
    conn.rollback()
    assert not mism and not res["audit_problems"], "shadow run differs from the batch experiment or the audit found problems"
else:
    print("usage: paper_shadow.py real|historical", file=sys.stderr)
    sys.exit(2)

json.dump(res, open(f"{OUT}/paper_shadow_{MODE}.json", "w"), indent=1, default=str)
log("WROTE", f"paper_shadow_{MODE}.json")
