"""READ-ONLY fetch layer for the forward tracker dashboard: turns the stored `paper_*` rows (plus the bars needed to mark open positions and the capture runs) into the plain inputs of
`paper_dashboard.build_forward_payload`. SELECT only; the caller owns the connection and should open it read-only. It never writes and never touches the collector, the ledger or the bot.

Only the context fields the dashboard needs are extracted in SQL (the stored context JSON is large); the stored rows themselves are never modified."""
from datetime import date
from typing import Any, Dict, List, Optional

from research import paper_dashboard as D

MIN_SYMBOLS_FOR_A_SESSION = 1000          # the same notion of a loaded trading session the tracker uses

_CONTEXT_SQL = """jsonb_build_object(
    'session', o.context ->> 'session',
    'candidate', jsonb_build_object('passed_guard', o.context -> 'candidate' -> 'passed_guard'),
    'features', jsonb_build_object('sector', o.context -> 'features' -> 'sector'),
    'market_regime', jsonb_build_object('available', o.context -> 'market_regime' -> 'available', 'reason', o.context -> 'market_regime' -> 'reason'),
    'relative_strength', jsonb_build_object('available', o.context -> 'relative_strength' -> 'available', 'reason', o.context -> 'relative_strength' -> 'reason'),
    'catalysts', jsonb_build_object('section', o.context -> 'catalysts' -> 'section', 'reason', o.context -> 'catalysts' -> 'reason',
                                    'events', CASE WHEN jsonb_typeof(o.context -> 'catalysts' -> 'events') = 'array' AND jsonb_array_length(o.context -> 'catalysts' -> 'events') > 0 THEN '[1]'::jsonb ELSE '[]'::jsonb END))"""


def fetch_forward_inputs(cur) -> Dict[str, Any]:
    cur.execute("SELECT session_date, scope, n_variants, n_candidates, n_registered, n_skipped_open, n_entered, n_closed, n_not_enterable, n_open_after, open_state_hash, code_ref "
                "FROM paper_session ORDER BY session_date")
    cols = ["session_date", "scope", "n_variants", "n_candidates", "n_registered", "n_skipped_open", "n_entered", "n_closed", "n_not_enterable", "n_open_after", "open_state_hash", "code_ref"]
    sessions = [dict(zip(cols, r)) for r in cur.fetchall()]
    observations: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    bars: Dict[str, List[tuple]] = {}
    capture_runs: List[Dict[str, Any]] = []
    trading_sessions: List[date] = []
    if sessions:
        cur.execute(f"SELECT o.id, v.variant_key, v.variant_version, v.definition_hash, o.symbol, o.direction, o.signal_session, o.atr, {_CONTEXT_SQL} "
                    "FROM paper_observation o JOIN strategy_variant v ON v.id = o.variant_id ORDER BY o.id")
        for oid, key, ver, h, sym, d, sig, atr, ctx in cur.fetchall():
            observations.append({"id": oid, "variant_key": key, "variant_version": ver, "definition_hash": h.strip(), "symbol": sym, "direction": int(d), "signal_session": sig, "atr": float(atr), "context": ctx})
        cur.execute("SELECT e.observation_id, e.event_type, e.session_date, CASE WHEN e.event_type = 'hindsight' THEN '{}'::jsonb ELSE e.payload END "
                    "FROM paper_event e WHERE e.event_type IN ('entered', 'closed', 'not_enterable', 'hindsight') ORDER BY e.id")
        events = [{"observation_id": a, "event_type": b, "session_date": c, "payload": p} for a, b, c, p in cur.fetchall()]
        entered = [e for e in events if e["event_type"] == "entered"]
        if entered:
            first_entry = min(date.fromisoformat(e["payload"]["entry_session"]) for e in entered)
            cur.execute("SELECT DISTINCT o.symbol FROM paper_observation o JOIN paper_event e ON e.observation_id = o.id AND e.event_type = 'entered'")
            syms = sorted(r[0] for r in cur.fetchall())
            cur.execute("SELECT symbol, date, open, high, low, close FROM stock_prices WHERE symbol = ANY(%s) AND date >= %s AND close IS NOT NULL ORDER BY symbol, date", (syms, first_entry))
            for sym, d, o_, h_, l_, c_ in cur.fetchall():
                bars.setdefault(sym, []).append((d, float(o_), float(h_), float(l_), float(c_)))
        cur.execute("SELECT r.session_date, r.status, r.candidates FROM candidate_capture_run r JOIN strategies s ON s.id = r.strategy_id "
                    "WHERE s.strategy_key = 'donchian_breakout' AND s.strategy_version = 'v1' AND r.session_date >= %s ORDER BY r.session_date", (sessions[0]["session_date"],))
        capture_runs = [{"session_date": a, "status": b, "candidates": c} for a, b, c in cur.fetchall()]
        cur.execute("SELECT date FROM stock_prices WHERE date >= %s GROUP BY date HAVING count(*) >= %s ORDER BY date", (sessions[0]["session_date"], MIN_SYMBOLS_FOR_A_SESSION))
        trading_sessions = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT (now() AT TIME ZONE 'UTC')::date")
    as_of = cur.fetchone()[0]
    return {"sessions": sessions, "observations": observations, "events": events, "bars": bars, "capture_runs": capture_runs, "trading_sessions": trading_sessions, "as_of": as_of}


def forward_payload(cur) -> Dict[str, Any]:
    """Fetch (read-only) and aggregate. Answers `available: false` with a reason when the tables are absent or unreadable instead of raising."""
    import psycopg2.errors
    cur.execute("SAVEPOINT forward_dashboard")
    try:
        inputs = fetch_forward_inputs(cur)
        cur.execute("RELEASE SAVEPOINT forward_dashboard")
    except (psycopg2.errors.UndefinedTable, psycopg2.errors.InsufficientPrivilege):
        cur.execute("ROLLBACK TO SAVEPOINT forward_dashboard")
        return {"schema": D.SCHEMA, "source": "forward", "available": False, "reason": "the forward paper tracker storage is not provisioned or not readable on this database", "sessions_processed": 0}
    return D.build_forward_payload(**inputs)
