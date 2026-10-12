"""S11 read-only ledger reconciliation: screener breakouts (results JSON) vs the ledger rows of the session, reusing the writer's own predicates."""
import json, os, sys
from datetime import date
sys.path[:0] = ["/app", "/app/mechanism"]
import psycopg2
from screeners.signal_ledger_writer import ledger_ineligibility, TRACKED_SIGNAL_TYPES

conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()
for session, path in (("2026-10-05", "/res/multi_timeframe_ml_enhanced_20261005_231621.json"), ("2026-10-06", "/res/multi_timeframe_ml_enhanced_20261006_231937.json")):
    d = date.fromisoformat(session)
    res = json.load(open(path))
    sigs = [s for k, v in res["signals"].items() if isinstance(v, list) for s in v if s.get("signal_type") in TRACKED_SIGNAL_TYPES]
    cur.execute("SELECT symbol, direction FROM signal_ledger WHERE signal_date=%s", (d,))
    written = {(a, b) for a, b in cur.fetchall()}
    cur.execute("SELECT id FROM ledger_strategy_probe LIMIT 1") if False else None
    out = {"session": session, "breakouts_in_results": len(sigs), "ledger_rows": len(written), "ineligible": {}, "open_position_still_open": 0, "open_position_upper_bound": 0,
           "written_matches_results": 0, "unexplained": []}
    keys = set()
    for s in sigs:
        dirn = TRACKED_SIGNAL_TYPES[s["signal_type"]]
        key = (s["symbol"], dirn)
        keys.add(key)
        if key in written:
            out["written_matches_results"] += 1
            continue
        reason = ledger_ineligibility(s, d)
        if reason:
            out["ineligible"][reason] = out["ineligible"].get(reason, 0) + 1
            continue
        cur.execute("SELECT count(*) FILTER (WHERE status='open'), count(*) FILTER (WHERE signal_date < %s AND (status='open' OR resolved_date >= %s)) "
                    "FROM signal_ledger WHERE symbol=%s AND direction=%s AND signal_date != %s", (d, d, s["symbol"], dirn, d))
        still, upper = cur.fetchone()
        if still:
            out["open_position_still_open"] += 1
        elif upper:
            out["open_position_upper_bound"] += 1
        else:
            out["unexplained"].append(s["symbol"])
    out["ledger_rows_not_in_results"] = sorted(written - keys)[:10]
    out["unexplained_count"] = len(out["unexplained"])
    out["unexplained"] = out["unexplained"][:15]
    print(json.dumps(out, sort_keys=True))
