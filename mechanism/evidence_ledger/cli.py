"""Evidence Ledger v0 command line. Reads the database READ ONLY; writes only the ledger file. The nightly unit (deploy/vps/donchian-evidence-ledger.*)
is committed but dormant: installing and arming it needs owner approval.

    python mechanism/evidence_ledger/cli.py capture --session 2026-11-11 --store /path/ledger.jsonl
    python mechanism/evidence_ledger/cli.py capture --latest-completed --store /path/ledger.jsonl      # the nightly form
    python mechanism/evidence_ledger/cli.py report  --store /path/ledger.jsonl [--json out.json]

--latest-completed: the session is the latest COMPLETED US session on the market calendar (shared.market_calendar), never the wall-clock date. Refused
when only the weekday fallback is available (no trustworthy calendar). The session's own ^GSPC bar and stock bars must be in the database, else
'not ready' (exit 2) and a later fire retries; the first-capture-wins store makes every re-run of a captured night a no-op.
Exit statuses: 0 captured (or nothing new) | 2 data for the session not in the database yet | 3 another capture holds the store lock |
5 refused (no trustworthy calendar, bad arguments) | 8 no database connection | 9 ledger file corrupt (a human looks; nothing appended).

capture: the Opportunity Intelligence briefs for the explicit session (every company-quarter in the window: the research universe, all groups),
         each stamped with the ACTUAL capture time, its point-in-time sector, and the evidence hash; plus every pre-release EPS estimate on file
         for reports in the next 60 days (consensus stream). First capture wins; changed evidence is a revision, never a replacement.
report:  forward outcomes at 30/90/180 calendar days for every discovery, against the S&P 500 and the sector peer median, with the full
         denominator (pending / complete / unreliable / ended_early / no_entry), prospective and late captures reported separately.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence_ledger import consensus as Cn  # noqa: E402
from evidence_ledger import model as M  # noqa: E402
from evidence_ledger.store import LedgerCorrupt, Store  # noqa: E402

CONSENSUS_DAYS_AHEAD = 60
SECTOR_SQL = """SELECT DISTINCT ON (symbol) symbol, sector, source, captured_at FROM sector_observation
                 WHERE symbol = ANY(%s) AND captured_at <= %s AND sector IS NOT NULL ORDER BY symbol, captured_at DESC, seq DESC"""
SECTOR_MEMBERS_SQL = """SELECT DISTINCT ON (symbol) symbol, sector FROM sector_observation WHERE captured_at <= %s ORDER BY symbol, captured_at DESC, seq DESC"""
CONSENSUS_SQL = """SELECT symbol, report_date, eps_estimate, fetched_at FROM earnings_calendar
                    WHERE report_date > %s AND report_date <= %s AND eps_estimate IS NOT NULL ORDER BY report_date, symbol"""
SESSIONS_SQL = "SELECT date FROM market_index_prices WHERE symbol = '^GSPC' AND date BETWEEN %s AND %s AND close IS NOT NULL ORDER BY date"
CLOSES_SQL = "SELECT symbol, date, close FROM stock_prices WHERE symbol = ANY(%s) AND date BETWEEN %s AND %s AND close IS NOT NULL"
CLOSES_ON_SQL = "SELECT symbol, date, close FROM stock_prices WHERE symbol = ANY(%s) AND date = ANY(%s) AND close IS NOT NULL"
BENCH_SQL = "SELECT date, close FROM market_index_prices WHERE symbol = '^GSPC' AND date BETWEEN %s AND %s AND close IS NOT NULL"
DISC_SQL = "SELECT symbol, date, detected_at FROM price_discontinuities WHERE symbol = ANY(%s) AND date BETWEEN %s AND %s"
READY_SQL = """SELECT (SELECT count(*) FROM market_index_prices WHERE symbol = '^GSPC' AND date = %s AND close IS NOT NULL) AS bench,
                      (SELECT count(*) FROM stock_prices WHERE date = %s AND close IS NOT NULL) AS stocks"""
MIN_STOCK_BARS = 1000            # a session with fewer stored bars than this is still loading (the universe is ~3,000)

EXIT_OK, EXIT_NOT_READY, EXIT_LOCKED, EXIT_REFUSED, EXIT_NO_DB, EXIT_CORRUPT = 0, 2, 3, 5, 8, 9


class NotReady(RuntimeError):
    pass


def resolve_latest_completed(now: datetime, calendar=None) -> Tuple[date, List[date]]:
    """(latest completed session, the calendar's sessions up to it) from a TRUSTWORTHY calendar (Alpaca or its cache). ValueError on the weekday
    fallback or an empty one."""
    if calendar is None:
        from shared import market_calendar as calendar
    sessions, source = calendar.get_sessions(now.astimezone(M.NY).date())
    if source == "weekday-fallback":
        raise ValueError("no trustworthy market calendar (weekday fallback only)")
    s = calendar.latest_completed(sessions, now)
    if s is None:
        raise ValueError("the market calendar has no completed session")
    return s, sorted(d for d in sessions if d <= s)


def check_ready(conn, session: date) -> Dict[str, int]:
    r = _rows(conn, READY_SQL, (session, session))[0]
    got = {"bench": int(r["bench"]), "stocks": int(r["stocks"])}
    if got["bench"] < 1 or got["stocks"] < MIN_STOCK_BARS:
        raise NotReady(f"session {session} not loaded yet (^GSPC bars {got['bench']}, stock bars {got['stocks']})")
    return got


def _rows(conn, sql: str, params: Sequence[Any]) -> List[Dict[str, Any]]:
    from psycopg2.extras import RealDictCursor
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- capture
def capture(connect, session: date, store: Store, captured_at: datetime, days: int = 14, delayed: bool = False) -> Dict[str, Any]:
    """One session's briefs into the ledger. delayed=True: a session whose own night was missed, processed now from the same point-in-time brief
    (labelled delayed_processing, the actual captured_at kept, the sector as known by the session's cutoff, and NO consensus: estimates are only ever
    captured live, for reports still ahead)."""
    from opportunity import brief as OB
    from opportunity import repository as R
    with connect() as conn, R.read_only(conn):
        check_ready(conn, session)
    rep = OB.run(connect, session, days)
    cutoff = rep.get("knowledge_cutoff")
    symbols = sorted({b["symbol"] for b in rep["briefs"]})
    ny_today = captured_at.astimezone(M.NY).date()
    sector_asof = min(captured_at, datetime.fromisoformat(cutoff)) if (delayed and cutoff) else captured_at
    with connect() as conn, R.read_only(conn):
        sectors = {r["symbol"]: r for r in _rows(conn, SECTOR_SQL, (symbols, sector_asof))} if symbols else {}
        sessions = [r["date"] for r in _rows(conn, SESSIONS_SQL, (session - timedelta(days=30), session))]
        cons = [] if delayed else _rows(conn, CONSENSUS_SQL, (ny_today, ny_today + timedelta(days=CONSENSUS_DAYS_AHEAD)))
    obs = Counter()
    for b in rep["briefs"]:
        s = sectors.get(b["symbol"])
        rec = M.observation_record(b, session, captured_at, sessions, {"sector": s["sector"], "source": s["source"],
                                                                       "observed_at": s["captured_at"].astimezone(timezone.utc).isoformat()} if s else None,
                                   delayed=delayed, evidence_cutoff=cutoff)
        obs[(store.append("observation", rec["key"], rec, compare="evidence_hash"), rec["basis"])] += 1
    mode = M.RUN_DELAYED if delayed else M.RUN_ON_TIME
    run = store.append("capture_run", session.isoformat(), M.capture_run_record(session, captured_at, mode, len(rep["briefs"]), cutoff), compare="session")
    con = Counter()
    for row in cons:
        rec = Cn.consensus_record(row, captured_at)
        if rec is None:
            con["not_pre_release"] += 1
            continue
        con[store.append("consensus", rec["key"], rec, compare="eps_estimate")] += 1
    return {"session": session.isoformat(), "mode": mode, "run_record": run, "captured_at": captured_at.isoformat(), "briefs": len(rep["briefs"]),
            "observations": {f"{a}/{b}": n for (a, b), n in sorted(obs.items())}, "consensus": dict(sorted((k, v) for k, v in con.items() if v)),
            "store_lines": len(store.lines), "store_head": store.head}


def dealt_with(store: Store) -> List[date]:
    """Sessions the ledger has a capture_run for, plus the sessions of observations written before capture_run existed (the first manual capture)."""
    runs = {date.fromisoformat(k) for k in store.history("capture_run")}
    return sorted(runs | legacy_sessions(store))


def legacy_sessions(store: Store) -> set:
    return {date.fromisoformat(r["session"]) for r in store.records("observation") if "processing" not in r}


def nightly(connect, store: Store, captured_at: datetime, latest: date, calendar: Sequence[date], days: int = 14) -> Dict[str, Any]:
    """The scheduled capture: first the gap since the ledger's last session (the newest MAX_DELAYED_SESSIONS processed late, older ones recorded as
    missed; a past session whose prices never arrived is recorded as missed too), then the latest completed session on time. NotReady from the latest
    session propagates (exit 2: the later fire retries); what was appended before it stays (first capture wins, a re-run skips it)."""
    delayed, missed = M.plan_sessions(calendar, latest, dealt_with(store))
    res: Dict[str, Any] = {"missed": [], "delayed": []}
    for d in missed:
        store.append("capture_run", d.isoformat(), M.capture_run_record(d, captured_at, M.RUN_MISSED,
                     reason=f"more than {M.MAX_DELAYED_SESSIONS} sessions before the next ledger run"), compare="session")
        res["missed"].append(d.isoformat())
    for d in delayed:
        try:
            r = capture(connect, d, store, captured_at, days, delayed=True)
            res["delayed"].append({k: r[k] for k in ("session", "briefs", "observations")})
        except NotReady as e:
            store.append("capture_run", d.isoformat(), M.capture_run_record(d, captured_at, M.RUN_MISSED, reason=f"session data absent: {e}"),
                         compare="session")
            res["missed"].append(d.isoformat())
    res["latest"] = capture(connect, latest, store, captured_at, days)
    return res


# ---------------------------------------------------------------- report
def report(connect, store: Store, asof: Optional[date] = None) -> Dict[str, Any]:
    from opportunity import repository as R
    observations = [M.classified(h) for h in store.history("observation").values()]
    consensus = list(store.records("consensus"))
    out: Dict[str, Any] = {"ledger_version": M.LEDGER_VERSION, "store_lines": len(store.lines), "store_head": store.head}
    if not observations:
        return dict(out, observations=0)
    first = min(datetime.fromisoformat(o["discovered_at"]) for o in observations).date() - timedelta(days=7)
    with connect() as conn, R.read_only(conn):
        last = asof or _rows(conn, "SELECT max(date) AS d FROM market_index_prices WHERE symbol = '^GSPC'", ())[0]["d"]
        sessions = [r["date"] for r in _rows(conn, SESSIONS_SQL, (first, last))]
        syms = sorted({o["symbol"] for o in observations})
        bars: Dict[str, Dict[date, float]] = defaultdict(dict)
        for r in _rows(conn, CLOSES_SQL, (syms, first, last)):
            bars[r["symbol"]][r["date"]] = float(r["close"])
        bench = {r["date"]: float(r["close"]) for r in _rows(conn, BENCH_SQL, (first, last))}
        disc: Dict[str, List[date]] = defaultdict(list)
        for r in _rows(conn, DISC_SQL, (syms, first, last)):
            disc[r["symbol"]].append(r["date"])
        outcomes = {}
        need: Dict[str, set] = defaultdict(set)                     # sector -> dates whose peer closes are needed
        for o in observations:
            outcomes[o["key"]] = [M.outcome(o, h, sessions, bars.get(o["symbol"], {}), bench, disc.get(o["symbol"], ())) for h in M.HORIZONS_DAYS]
            if o.get("sector"):
                for x in outcomes[o["key"]]:
                    if x.status == M.COMPLETE:
                        need[o["sector"]["sector"]] |= {date.fromisoformat(x.entry), date.fromisoformat(x.exit)}
        if need:
            cap = max(datetime.fromisoformat(o["captured_at"]) for o in observations)
            members = defaultdict(list)
            for r in _rows(conn, SECTOR_MEMBERS_SQL, (cap,)):
                members[r["sector"]].append(r["symbol"])
            peer: Dict[str, Dict[str, Dict[date, float]]] = {}
            for sec, dates in need.items():
                p: Dict[str, Dict[date, float]] = defaultdict(dict)
                for r in _rows(conn, CLOSES_ON_SQL, (members.get(sec, []), sorted(dates))):
                    p[r["symbol"]][r["date"]] = float(r["close"])
                peer[sec] = p
            for o in observations:
                if o.get("sector") and o["sector"]["sector"] in peer:
                    outcomes[o["key"]] = [M.outcome(o, h, sessions, bars.get(o["symbol"], {}), bench, disc.get(o["symbol"], ()), peer[o["sector"]["sector"]])
                                          for h in M.HORIZONS_DAYS]
    reported = [c for c in consensus if date.fromisoformat(c["report_date"]) <= last]
    runs = {k: v[0] for k, v in store.history("capture_run").items()}
    return dict(out, asof_session=last.isoformat(), observations=len(observations),
                by_basis=dict(Counter(o["basis"] for o in observations)), by_group=dict(Counter(o["group"] for o in observations)),
                with_sector=sum(1 for o in observations if o.get("sector")),
                revisions=sum(1 for l in store.lines if l["kind"] == "observation_revision"),
                session_coverage=M.coverage([d for d in sessions if d <= last], runs, legacy_sessions(store)),
                prospective=M.summarize(observations, outcomes, M.PROSPECTIVE), delayed_processing=M.summarize(observations, outcomes, M.DELAYED),
                late_first_capture=M.summarize(observations, outcomes, M.LATE),
                consensus_stream={"stream": Cn.STREAM, "pre_release_snapshots": len(consensus), "reports_since_passed": len(reported),
                                  "revisions": sum(1 for l in store.lines if l["kind"] == "consensus_revision")},
                outcomes={k: [asdict(x) for x in v] for k, v in outcomes.items()})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Evidence Ledger v0")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--session", type=date.fromisoformat)
    g.add_argument("--latest-completed", action="store_true", help="the latest completed US session on the market calendar")
    c.add_argument("--store", required=True)
    c.add_argument("--days", type=int, default=14)
    r = sub.add_parser("report")
    r.add_argument("--store", required=True)
    r.add_argument("--asof", type=date.fromisoformat)
    r.add_argument("--json")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    session = getattr(a, "session", None)
    if a.cmd == "capture":
        try:
            latest, calendar = resolve_latest_completed(now)
        except ValueError as e:
            print(f"EVIDENCE_CAPTURE refused: {e}")
            return EXIT_REFUSED
        if session is not None and session > latest:
            print(f"EVIDENCE_CAPTURE refused: {session} is not a completed session yet (latest completed {latest})")
            return EXIT_REFUSED
    lock = None
    if a.cmd == "capture":
        lock = store_lock(a.store)
        if lock is False:
            print(f"EVIDENCE_CAPTURE skipped: another capture holds {a.store}.lock")
            return EXIT_LOCKED
    from shared.database import db
    if not db.initialize_sync_pool():
        print("EVIDENCE_LEDGER refused: no database connection")
        return EXIT_NO_DB
    try:
        try:
            store = Store(a.store)
        except LedgerCorrupt as e:
            print(f"EVIDENCE_LEDGER refused: {e} (nothing appended)")
            return EXIT_CORRUPT
        if a.cmd == "capture":
            try:
                if a.latest_completed:
                    res = nightly(db.get_sync_connection, store, now, latest, calendar, a.days)
                else:                                   # an explicit session: on time only if it is the latest completed one
                    res = capture(db.get_sync_connection, session, store, now, a.days, delayed=session < latest)
            except NotReady as e:
                print(f"EVIDENCE_CAPTURE not ready: {e}")
                return EXIT_NOT_READY
            print("EVIDENCE_CAPTURE " + json.dumps(res, sort_keys=True))
        else:
            res = report(db.get_sync_connection, store, a.asof)
            if a.json:
                with open(a.json, "w", encoding="utf-8") as fh:
                    json.dump(res, fh, indent=1, sort_keys=True, default=str)
            print("EVIDENCE_REPORT " + json.dumps({k: v for k, v in res.items() if k != "outcomes"}, sort_keys=True, default=str))
    finally:
        db.close_pools()
        if lock:
            lock.close()
    return EXIT_OK


def store_lock(path: str):
    """An exclusive, non-blocking lock on '<store>.lock' (the nightly backup copies the file under the same lock, so it never copies a half-written line).
    Returns the open handle, False when another holder has it, or None where fcntl does not exist (Windows development)."""
    try:
        import fcntl
    except ImportError:
        return None
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fh = open(path + ".lock", "a")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return False
    return fh


if __name__ == "__main__":
    sys.exit(main())
