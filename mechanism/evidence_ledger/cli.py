"""Evidence Ledger v0 command line. Reads the database READ ONLY; writes only the ledger file. Scheduled nowhere (a nightly run needs approval).

    python mechanism/evidence_ledger/cli.py capture --session 2026-11-11 --store /path/ledger.jsonl
    python mechanism/evidence_ledger/cli.py report  --store /path/ledger.jsonl [--json out.json]

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
from typing import Any, Dict, List, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence_ledger import consensus as Cn  # noqa: E402
from evidence_ledger import model as M  # noqa: E402
from evidence_ledger.store import Store  # noqa: E402

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


def _rows(conn, sql: str, params: Sequence[Any]) -> List[Dict[str, Any]]:
    from psycopg2.extras import RealDictCursor
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------- capture
def capture(connect, session: date, store: Store, captured_at: datetime, days: int = 14) -> Dict[str, Any]:
    from opportunity import brief as OB
    from opportunity import repository as R
    rep = OB.run(connect, session, days)
    symbols = sorted({b["symbol"] for b in rep["briefs"]})
    ny_today = captured_at.astimezone(M.NY).date()
    with connect() as conn, R.read_only(conn):
        sectors = {r["symbol"]: r for r in _rows(conn, SECTOR_SQL, (symbols, captured_at))} if symbols else {}
        sessions = [r["date"] for r in _rows(conn, SESSIONS_SQL, (session - timedelta(days=30), session))]
        cons = _rows(conn, CONSENSUS_SQL, (ny_today, ny_today + timedelta(days=CONSENSUS_DAYS_AHEAD)))
    obs = Counter()
    for b in rep["briefs"]:
        s = sectors.get(b["symbol"])
        rec = M.observation_record(b, session, captured_at, sessions, {"sector": s["sector"], "source": s["source"],
                                                                       "observed_at": s["captured_at"].astimezone(timezone.utc).isoformat()} if s else None)
        obs[(store.append("observation", rec["key"], rec, compare="evidence_hash"), rec["basis"])] += 1
    con = Counter()
    for row in cons:
        rec = Cn.consensus_record(row, captured_at)
        if rec is None:
            con["not_pre_release"] += 1
            continue
        con[store.append("consensus", rec["key"], rec, compare="eps_estimate")] += 1
    return {"session": session.isoformat(), "captured_at": captured_at.isoformat(), "briefs": len(rep["briefs"]),
            "observations": {f"{a}/{b}": n for (a, b), n in sorted(obs.items())}, "consensus": dict(sorted((k, v) for k, v in con.items() if v)),
            "store_lines": len(store.lines), "store_head": store.head}


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
    return dict(out, asof_session=last.isoformat(), observations=len(observations),
                by_basis=dict(Counter(o["basis"] for o in observations)), by_group=dict(Counter(o["group"] for o in observations)),
                with_sector=sum(1 for o in observations if o.get("sector")),
                revisions=sum(1 for l in store.lines if l["kind"] == "observation_revision"),
                prospective=M.summarize(observations, outcomes, M.PROSPECTIVE), late_first_capture=M.summarize(observations, outcomes, M.LATE),
                consensus_stream={"stream": Cn.STREAM, "pre_release_snapshots": len(consensus), "reports_since_passed": len(reported),
                                  "revisions": sum(1 for l in store.lines if l["kind"] == "consensus_revision")},
                outcomes={k: [asdict(x) for x in v] for k, v in outcomes.items()})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Evidence Ledger v0")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--session", required=True, type=date.fromisoformat)
    c.add_argument("--store", required=True)
    c.add_argument("--days", type=int, default=14)
    r = sub.add_parser("report")
    r.add_argument("--store", required=True)
    r.add_argument("--asof", type=date.fromisoformat)
    r.add_argument("--json")
    a = ap.parse_args(argv)
    from shared.database import db
    if not db.initialize_sync_pool():
        print("EVIDENCE_LEDGER refused: no database connection")
        return 2
    try:
        store = Store(a.store)
        if a.cmd == "capture":
            res = capture(db.get_sync_connection, a.session, store, datetime.now(timezone.utc), a.days)
            print("EVIDENCE_CAPTURE " + json.dumps(res, sort_keys=True))
        else:
            res = report(db.get_sync_connection, store, a.asof)
            if a.json:
                with open(a.json, "w", encoding="utf-8") as fh:
                    json.dump(res, fh, indent=1, sort_keys=True, default=str)
            print("EVIDENCE_REPORT " + json.dumps({k: v for k, v in res.items() if k != "outcomes"}, sort_keys=True, default=str))
    finally:
        db.close_pools()
    return 0


if __name__ == "__main__":
    sys.exit(main())
