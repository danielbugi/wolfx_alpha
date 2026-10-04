# mechanism/strategy_analytics/analytics.py
"""
The one place Strategy Intelligence numbers are computed from signal_ledger. Every consumer
(backend dashboard API, the legacy /api/track-record summary, later the private Telegram assistant
and any public report) calls these functions, so the same question gets the same answer everywhere.
Definitions live in definitions.py.

I/O is injected: every function takes `fetch(sql, params) -> list[dict]`, so the backend passes its
pooled connection and the mechanism image passes shared.db -- this module never opens a connection or
imports a pool. All SQL is parameterized; sort keys and filter columns come only from the whitelists
below.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence

from strategy_analytics.definitions import (
    ALL_STATUSES, CAPABILITIES, DEFINITIONS_VERSION, DIRECTION_VALUES, DIRECTIONS, EVALUATION_FLAGS,
    LEGACY_UNSCORED_MODEL_VERSIONS, LIFECYCLE_HELD, LIFECYCLE_OPEN, LIFECYCLE_RESOLVED, LIFECYCLES, RESOLUTION_FLAGS, SESSION_WINDOW,
    STALE_AFTER_SESSIONS, TARGET_STATUSES, lifecycle, metric, not_available, ratio,
)

Fetch = Callable[[str, Any], List[Dict[str, Any]]]

STRATEGY_DISPLAY_NAMES = {"donchian_breakout": "Donchian Breakout"}  # the strategies table has no name column

MAX_PAGE_SIZE = 200
ATTENTION_LIST_LIMIT = 25


def _num(v: Any) -> Any:
    return float(v) if isinstance(v, Decimal) else v


def _clean(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: _num(v) for k, v in row.items()}


# ============================================================================ strategies
STRATEGY_COLUMNS = "id, strategy_key, strategy_version, description, created_at"


def _strategy_out(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "key": row["strategy_key"],
        "version": row["strategy_version"],
        "display_name": STRATEGY_DISPLAY_NAMES.get(row["strategy_key"], row["strategy_key"]),
        "description": row["description"],
        "registered_at": row["created_at"],
    }


def list_strategies(fetch: Fetch) -> List[Dict[str, Any]]:
    rows = fetch(f"""
        SELECT s.id, s.strategy_key, s.strategy_version, s.description, s.created_at,
               count(sl.id) AS signals,
               count(sl.id) FILTER (WHERE sl.status = 'open') AS open,
               count(sl.id) FILTER (WHERE sl.status <> 'open') AS resolved,
               min(sl.signal_date) AS first_tracked_session,
               max(sl.signal_date) AS latest_tracked_session
        FROM strategies s LEFT JOIN signal_ledger sl ON sl.strategy_id = s.id
        GROUP BY s.id ORDER BY s.strategy_key, s.strategy_version
    """, None)
    return [{**_strategy_out(r), "tracking": {
        "total_signals": r["signals"], "open": r["open"], "resolved": r["resolved"],
        "first_tracked_session": r["first_tracked_session"],
        "latest_tracked_session": r["latest_tracked_session"],
        "tracking_status": "tracking" if r["signals"] else "no_signals_yet",
    }} for r in rows]


def get_strategy(fetch: Fetch, key: str, version: str) -> Optional[Dict[str, Any]]:
    rows = fetch(f"SELECT {STRATEGY_COLUMNS} FROM strategies WHERE strategy_key = %s AND strategy_version = %s",
                 (key, version))
    return _strategy_out(rows[0]) if rows else None


def reference_session(fetch: Fetch) -> Optional[date]:
    """The latest market session with prices -- the explicit session every 'this week'/'stale'
    judgement is made against (never the wall-clock date)."""
    rows = fetch("SELECT max(date) AS d FROM stock_prices", None)
    return rows[0]["d"] if rows else None


# ============================================================================ aggregate (one query)
AGGREGATE_SQL = """
    SELECT GROUPING(direction) AS is_total, direction,
        count(*) AS signals,
        count(*) FILTER (WHERE status = 'open') AS open_total,
        count(*) FILTER (WHERE status = 'open' AND evaluation_flag IS NULL) AS normally_open,
        count(*) FILTER (WHERE status = 'open' AND evaluation_flag IS NOT NULL) AS held,
        count(*) FILTER (WHERE status <> 'open') AS resolved,
        count(*) FILTER (WHERE status IN ('target1', 'target2', 'target3')) AS winners,
        count(*) FILTER (WHERE status = 'stopped') AS stopped,
        count(*) FILTER (WHERE status = 'target1') AS target1,
        count(*) FILTER (WHERE status = 'target2') AS target2,
        count(*) FILTER (WHERE status = 'target3') AS target3,
        count(*) FILTER (WHERE status = 'expired') AS expired,
        count(*) FILTER (WHERE status = 'expired' AND outcome_r > 0) AS expired_positive,
        count(*) FILTER (WHERE status = 'expired' AND outcome_r < 0) AS expired_negative,
        count(*) FILTER (WHERE status <> 'open' AND resolution_flag = 'same_bar_stop_and_target') AS ambiguous,
        avg(outcome_r) FILTER (WHERE status <> 'open') AS avg_r,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY outcome_r) FILTER (WHERE status <> 'open') AS median_r,
        avg(outcome_r) FILTER (WHERE status = 'expired') AS expired_avg_r,
        avg(bars_held) FILTER (WHERE status <> 'open') AS avg_bars,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY bars_held) FILTER (WHERE status <> 'open') AS median_bars,
        avg(mae_r) FILTER (WHERE status <> 'open') AS avg_mae_r,
        min(signal_date) AS first_session,
        max(signal_date) AS latest_session,
        count(*) FILTER (WHERE %(ref)s::date IS NOT NULL
                           AND signal_date BETWEEN date_trunc('week', %(ref)s::date)::date AND %(ref)s::date) AS this_week,
        count(*) FILTER (WHERE %(ref)s::date IS NOT NULL
                           AND signal_date BETWEEN date_trunc('month', %(ref)s::date)::date AND %(ref)s::date) AS this_month
    FROM signal_ledger
    WHERE ({scope})
    GROUP BY GROUPING SETS ((), (direction))
"""


def _aggregate(fetch: Fetch, strategy_id: Optional[int], ref: Optional[date]) -> Dict[str, Dict[str, Any]]:
    """{'total': row, 'bullish': row, 'bearish': row} -- every direction present, zero-filled."""
    scope = "strategy_id = %(sid)s" if strategy_id is not None else "TRUE"
    rows = [_clean(r) for r in fetch(AGGREGATE_SQL.format(scope=scope), {"sid": strategy_id, "ref": ref})]
    out: Dict[str, Dict[str, Any]] = {}
    empty = None
    for r in rows:
        if r["is_total"]:
            out["total"] = r
            empty = {k: (0 if isinstance(v, int) and k not in ("is_total", "direction") else None)
                     for k, v in r.items()}
        else:
            out[DIRECTIONS[r["direction"]]] = r
    for name in DIRECTIONS.values():
        out.setdefault(name, dict(empty or {}))
    return out


def _counts(r: Dict[str, Any]) -> Dict[str, int]:
    keys = ("signals", "open_total", "normally_open", "held", "resolved", "winners", "stopped",
            "target1", "target2", "target3", "expired", "expired_positive", "expired_negative", "ambiguous")
    return {k: int(r.get(k) or 0) for k in keys}


def _performance(r: Dict[str, Any]) -> Dict[str, Any]:
    c = _counts(r)
    n = c["resolved"]
    return {
        "resolved_n": n,
        "winners": c["winners"],
        "stopped": c["stopped"],
        "expired": c["expired"],
        "expired_positive": c["expired_positive"],
        "expired_negative": c["expired_negative"],
        "ambiguous": c["ambiguous"],
        "win_rate": ratio(c["winners"], n),
        "stop_rate": ratio(c["stopped"], n),
        "expiry_rate": ratio(c["expired"], n),
        "average_r": metric(r.get("avg_r"), n, 3),
        "median_r": metric(r.get("median_r"), n, 3),
        "average_holding_bars": metric(r.get("avg_bars"), n, 1),
        "median_holding_bars": metric(r.get("median_bars"), n, 1),
        "average_mae_r": metric(r.get("avg_mae_r"), n, 3),
        "average_mfe_r": not_available("maximum favourable excursion is not stored in Release A", "B"),
    }


def _outcomes(r: Dict[str, Any]) -> Dict[str, Any]:
    c = _counts(r)
    n = c["resolved"]
    terminal = {s: {"count": c[s], "share": ratio(c[s], n)} for s in ("stopped",) + TARGET_STATUSES + ("expired",)}
    # Not a win, but its real R counts in average/median R -- so keep its sign visible.
    terminal["expired"].update({
        "average_r": metric(r.get("expired_avg_r"), c["expired"], 3),
        "positive_count": c["expired_positive"],
        "negative_count": c["expired_negative"],
        "flat_count": c["expired"] - c["expired_positive"] - c["expired_negative"],
        "positive_share": ratio(c["expired_positive"], c["expired"]),
    })
    reached = {
        "reached_target1": c["target1"] + c["target2"] + c["target3"],
        "reached_target2": c["target2"] + c["target3"],
        "reached_target3": c["target3"],
    }
    return {
        "resolved_n": n,
        "terminal": terminal,
        "ambiguous": {"count": c["ambiguous"], "share_of_resolved": ratio(c["ambiguous"], n),
                      "share_of_stopped": ratio(c["ambiguous"], c["stopped"])},
        "target_milestones": {k: {"count": v, "rate": ratio(v, n)} for k, v in reached.items()},
        "milestone_semantics": "reached within the trade: exit is at the first target touched, so a "
                               "target1 exit may have gone on to target2 unobserved (post-exit trajectory "
                               "is Release B)",
    }


def _direction_block(r: Dict[str, Any]) -> Dict[str, Any]:
    c = _counts(r)
    perf = _performance(r)
    n = c["resolved"]
    return {
        "signals": c["signals"], "open": c["open_total"], "normally_open": c["normally_open"],
        "held": c["held"], "resolved": n, "winners": c["winners"], "stopped": c["stopped"],
        "expired": c["expired"], "ambiguous": c["ambiguous"],
        "win_rate": perf["win_rate"], "average_r": perf["average_r"], "median_r": perf["median_r"],
        "average_holding_bars": perf["average_holding_bars"],
        "median_holding_bars": perf["median_holding_bars"],
        "stop_rate": perf["stop_rate"], "expiry_rate": perf["expiry_rate"],
        "target_rates": {s: ratio(c[s], n) for s in TARGET_STATUSES},
    }


def summary(fetch: Fetch, strategy: Dict[str, Any]) -> Dict[str, Any]:
    """Identity, tracking, performance, outcome distribution and the bullish/bearish comparison,
    all from one aggregate query."""
    ref = reference_session(fetch)
    agg = _aggregate(fetch, strategy["id"], ref)
    t = agg["total"]
    c = _counts(t)
    return {
        "strategy": strategy,
        "definitions_version": DEFINITIONS_VERSION,
        "reference_session": ref,
        "tracking": {
            "tracking_status": "tracking" if c["signals"] else "no_signals_yet",
            "first_tracked_session": t.get("first_session"),
            "latest_tracked_session": t.get("latest_session"),
            "total_signals": c["signals"],
            "open": c["open_total"],
            "normally_open": c["normally_open"],
            "held": c["held"],
            "resolved": c["resolved"],
            "bullish": _counts(agg["bullish"])["signals"],
            "bearish": _counts(agg["bearish"])["signals"],
            "signals_this_week": int(t["this_week"]) if ref is not None else None,
            "signals_this_month": int(t["this_month"]) if ref is not None else None,
        },
        "performance": _performance(t),
        "outcomes": _outcomes(t),
        "directions": {name: _direction_block(agg[name]) for name in DIRECTIONS.values()},
        "capabilities": CAPABILITIES,
    }


def overall_performance(fetch: Fetch, strategy_id: Optional[int] = None) -> Dict[str, Any]:
    """Counts + performance + terminal status counts for one strategy, or every strategy when
    strategy_id is None (what the legacy track-record summary shows)."""
    agg = _aggregate(fetch, strategy_id, reference_session(fetch))
    t = agg["total"]
    c = _counts(t)
    return {
        "counts": c,
        "first_session": t.get("first_session"),
        "latest_session": t.get("latest_session"),
        "performance": _performance(t),
        "by_status": {s: c[s] for s in ("stopped",) + TARGET_STATUSES + ("expired",) if c[s]},
    }


# ============================================================================ data health
# Mirrors evaluate_signal_ledger._invalid_bar_reason() exactly (pinned by a drift test): NULL,
# non-finite (NaN) or non-positive high/low/close, low > high, close outside [low, high].
INVALID_BAR_PREDICATE = """(
    sp.high IS NULL OR sp.low IS NULL OR sp.close IS NULL
    OR sp.high = 'NaN'::numeric OR sp.low = 'NaN'::numeric OR sp.close = 'NaN'::numeric
    OR sp.high <= 0 OR sp.low <= 0 OR sp.close <= 0
    OR sp.low > sp.high OR sp.close > sp.high OR sp.close < sp.low
)"""

OPEN_DATA_STATE_SQL = f"""
    WITH RECURSIVE sessions(d, k) AS (
        SELECT (SELECT max(date) FROM stock_prices), 1
        UNION ALL
        SELECT (SELECT max(date) FROM stock_prices WHERE date < s.d), s.k + 1
        FROM sessions s WHERE s.d IS NOT NULL AND s.k < %(window)s
    ),
    win AS (SELECT d FROM sessions WHERE d IS NOT NULL),
    ref AS (SELECT max(d) AS l, min(d) AS oldest FROM win),
    o AS (
        SELECT sl.id, sl.symbol, sl.signal_date, sl.last_evaluated_date, lb.latest_bar, ref.l, ref.oldest,
               GREATEST(sl.signal_date, COALESCE(lb.latest_bar, sl.signal_date)) AS anchor,
               EXISTS (SELECT 1 FROM stock_prices sp
                       WHERE sp.symbol = sl.symbol AND sp.date > sl.signal_date AND sp.date <= ref.l
                         AND {INVALID_BAR_PREDICATE}) AS invalid_bar
        FROM signal_ledger sl CROSS JOIN ref
        LEFT JOIN LATERAL (SELECT max(sp.date) AS latest_bar FROM stock_prices sp
                           WHERE sp.symbol = sl.symbol AND sp.date <= ref.l) lb ON TRUE
        WHERE sl.strategy_id = %(sid)s AND sl.status = 'open' AND sl.evaluation_flag IS NULL
    )
    SELECT id, symbol, signal_date, last_evaluated_date, latest_bar, invalid_bar, l AS reference_session,
           (SELECT count(*) FROM win WHERE win.d > o.anchor) AS lag_sessions,
           (o.anchor < o.oldest) AS lag_capped
    FROM o
"""


def _price_data_state(row: Dict[str, Any]) -> str:
    """awaiting_first_session: the signal is on the latest session, no forward bar can exist yet.
    current: the symbol has the latest session's bar. lagging: 1..STALE-1 sessions behind.
    stale: >= STALE_AFTER_SESSIONS behind (or beyond the inspected window) -- possible delisting or
    feed gap. Measured in market sessions, so weekends and holidays never make a signal stale."""
    if row["reference_session"] is None:
        return "no_price_data"
    if row["signal_date"] >= row["reference_session"]:
        return "awaiting_first_session"
    lag = row["lag_sessions"]
    if lag == 0:
        return "current"
    if lag < STALE_AFTER_SESSIONS and not row["lag_capped"]:
        return "lagging"
    return "stale"


def _evaluation_state(row: Dict[str, Any]) -> str:
    """invalid_price_blocked: a bar the evaluator refuses sits on the path (it writes nothing).
    up_to_date: evaluated through the latest session. pending: not yet (normal between prices
    landing and the 03:30 run; persistent when the symbol has no new bar -- the evaluator doesn't
    touch a row it has nothing new for)."""
    if row["invalid_bar"]:
        return "invalid_price_blocked"
    ref = row["reference_session"]
    if ref is not None and row["last_evaluated_date"] >= ref:
        return "up_to_date"
    return "pending"


def data_health(fetch: Fetch, strategy: Dict[str, Any]) -> Dict[str, Any]:
    sid = strategy["id"]
    ledger = _clean(fetch("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE status = 'open' AND evaluation_flag IS NULL) AS normally_open,
               count(*) FILTER (WHERE status = 'open' AND evaluation_flag IS NOT NULL) AS held,
               count(*) FILTER (WHERE status <> 'open') AS resolved,
               count(*) FILTER (WHERE status <> 'open' AND resolution_flag IS NOT NULL) AS ambiguous,
               max(signal_date) AS latest_signal_session,
               max(last_evaluated_date) FILTER (WHERE status = 'open' AND evaluation_flag IS NULL) AS latest_evaluated_open,
               count(strategy_version) AS c_strategy_version,
               count(model_version) FILTER (WHERE lower(btrim(model_version)) <> ALL(%s)) AS c_model_version,
               count(feature_set_version) AS c_feature_set_version, count(observation_id) AS c_observation_id,
               count(feature_snapshot_id) AS c_feature_snapshot_id
        FROM signal_ledger WHERE strategy_id = %s
    """, (list(LEGACY_UNSCORED_MODEL_VERSIONS), sid))[0])
    held_by_flag = {r["evaluation_flag"]: int(r["n"]) for r in fetch("""
        SELECT evaluation_flag, count(*) AS n FROM signal_ledger
        WHERE strategy_id = %s AND status = 'open' AND evaluation_flag IS NOT NULL GROUP BY evaluation_flag
    """, (sid,))}
    held_rows = [_clean(r) for r in fetch("""
        SELECT id, symbol, signal_date, direction, evaluation_flag, last_evaluated_date AS held_since
        FROM signal_ledger WHERE strategy_id = %s AND status = 'open' AND evaluation_flag IS NOT NULL
        ORDER BY last_evaluated_date, id LIMIT %s
    """, (sid, ATTENTION_LIST_LIMIT))]
    inv = _clean(fetch("""
        SELECT
          (SELECT count(*) FROM (SELECT 1 FROM signal_ledger WHERE strategy_id = %(sid)s
                                 GROUP BY symbol, direction, signal_date HAVING count(*) > 1) x) AS duplicate_event_identities,
          (SELECT count(*) FROM (SELECT 1 FROM signal_ledger WHERE strategy_id = %(sid)s AND status = 'open'
                                 GROUP BY symbol, direction HAVING count(*) > 1) x) AS multiple_open_positions,
          (SELECT count(*) FROM signal_ledger sl JOIN strategies s ON s.id = sl.strategy_id
           WHERE sl.strategy_id = %(sid)s AND sl.strategy_version <> s.strategy_version) AS strategy_version_mismatch,
          (SELECT count(*) FROM signal_ledger WHERE strategy_id = %(sid)s AND status <> 'open'
             AND (outcome_r IS NULL OR resolved_date IS NULL OR bars_held IS NULL)) AS resolved_missing_outcome,
          (SELECT count(*) FROM signal_ledger WHERE strategy_id = %(sid)s AND status = 'open'
             AND (outcome_r IS NOT NULL OR resolved_date IS NOT NULL)) AS open_with_outcome,
          (SELECT count(*) FROM signal_ledger WHERE strategy_id = %(sid)s AND resolution_flag IS NOT NULL
             AND status <> 'stopped') AS resolution_flag_on_non_stop,
          (SELECT count(*) FROM signal_ledger WHERE strategy_id = %(sid)s AND evaluation_flag IS NOT NULL
             AND status <> 'open') AS evaluation_flag_on_resolved
    """, {"sid": sid})[0])

    open_rows = [_clean(r) for r in fetch(OPEN_DATA_STATE_SQL, {"sid": sid, "window": SESSION_WINDOW})]
    ref = open_rows[0]["reference_session"] if open_rows else reference_session(fetch)
    price_states = {s: 0 for s in ("awaiting_first_session", "current", "lagging", "stale", "no_price_data")}
    eval_states = {s: 0 for s in ("up_to_date", "pending", "invalid_price_blocked")}
    no_forward_bar = 0
    attention = []
    for r in open_rows:
        ps, es = _price_data_state(r), _evaluation_state(r)
        price_states[ps] += 1
        eval_states[es] += 1
        if ps != "awaiting_first_session" and (r["latest_bar"] is None or r["latest_bar"] <= r["signal_date"]):
            no_forward_bar += 1
        if ps in ("lagging", "stale") or es == "invalid_price_blocked":
            attention.append({"id": r["id"], "symbol": r["symbol"], "signal_date": r["signal_date"],
                              "symbol_latest_bar": r["latest_bar"], "lag_sessions": int(r["lag_sessions"]),
                              "lag_capped": bool(r["lag_capped"]), "price_data_state": ps,
                              "evaluation_state": es})
    attention.sort(key=lambda a: (-a["lag_sessions"], a["id"]))

    total = int(ledger["total"])
    invariants = {k: int(v) for k, v in inv.items()}
    issues = [f"{k}: {v}" for k, v in invariants.items() if v]
    status = "violation" if issues else "healthy"
    for label, n in (("held", int(ledger["held"])), ("stale open signals", price_states["stale"]),
                     ("invalid-price-blocked signals", eval_states["invalid_price_blocked"]),
                     ("lagging open signals", price_states["lagging"])):
        if n:
            issues.append(f"{label}: {n}")
            status = "violation" if status == "violation" else "attention"

    def cov(col: str, release: Optional[str] = None, collected: bool = True) -> Dict[str, Any]:
        c = int(ledger[f"c_{col}"])
        out = {"count": c, "total": total, "share": ratio(c, total), "collected": collected}
        if release:
            out["release"] = release
        return out

    return {
        "strategy": strategy,
        "definitions_version": DEFINITIONS_VERSION,
        "status": status,
        "issues": issues,
        "ledger": {
            "total_rows": total,
            "latest_signal_session": ledger["latest_signal_session"],
            "normally_open": int(ledger["normally_open"]),
            "held": int(ledger["held"]),
            "held_by_flag": {f: held_by_flag.get(f, 0) for f in EVALUATION_FLAGS},
            "resolved": int(ledger["resolved"]),
            "ambiguous_resolutions": int(ledger["ambiguous"]),
        },
        "market_data": {
            "reference_session": ref,
            "stale_after_sessions": STALE_AFTER_SESSIONS,
            "session_window": SESSION_WINDOW,
            "open_signals_by_price_data_state": price_states,
            "open_signals_without_forward_bar": no_forward_bar,
        },
        "evaluation": {
            "open_signals_by_evaluation_state": eval_states,
            "latest_evaluated_session_open": ledger["latest_evaluated_open"],
            "evaluator_runs": not_available(
                "run results are only written to /opt/donchian/logs/channel_sender_runs.log on the VPS, "
                "not the database; progress above is derived from signal_ledger itself"),
        },
        "attention": {"open_signals": attention[:ATTENTION_LIST_LIMIT], "open_signals_total": len(attention),
                      "held_signals": held_rows},
        "invariants": invariants,
        "coverage": {
            "strategy_version": cov("strategy_version"),
            "model_version": cov("model_version"),
            "feature_set_version": cov("feature_set_version", "B", collected=False),
            "observation_id": cov("observation_id", "B", collected=False),
            "feature_snapshot_id": cov("feature_snapshot_id", "B", collected=False),
        },
        "capabilities": CAPABILITIES,
    }


# ============================================================================ signals
SIGNAL_COLUMNS = """sl.id, sl.symbol, sl.signal_date, sl.direction, sl.entry_price, sl.atr, sl.stop_price,
    sl.target1_price, sl.target2_price, sl.target3_price, sl.sector, sl.quality_grade, sl.status,
    sl.outcome_r, sl.mae_r, sl.resolved_date, sl.bars_held, sl.last_evaluated_date,
    sl.resolution_flag, sl.evaluation_flag, sl.strategy_version, sl.model_version"""

SORTS = {
    "newest": "sl.signal_date DESC, sl.id DESC",
    "oldest": "sl.signal_date ASC, sl.id ASC",
    "symbol": "sl.symbol ASC, sl.signal_date DESC, sl.id DESC",
    "r_desc": "sl.outcome_r DESC NULLS LAST, sl.id DESC",
    "r_asc": "sl.outcome_r ASC NULLS LAST, sl.id ASC",
    "holding_desc": "sl.bars_held DESC NULLS LAST, sl.id DESC",
    "holding_asc": "sl.bars_held ASC NULLS LAST, sl.id ASC",
    "grade": "sl.quality_grade ASC NULLS LAST, sl.signal_date DESC, sl.id DESC",
}

LIFECYCLE_SQL = {
    LIFECYCLE_OPEN: "sl.status = 'open' AND sl.evaluation_flag IS NULL",
    LIFECYCLE_HELD: "sl.status = 'open' AND sl.evaluation_flag IS NOT NULL",
    LIFECYCLE_RESOLVED: "sl.status <> 'open'",
}


def _signal_item(r: Dict[str, Any]) -> Dict[str, Any]:
    r = _clean(r)
    r["direction"] = DIRECTIONS[r["direction"]]
    r["lifecycle"] = lifecycle(r["status"], r["evaluation_flag"])
    r["is_winner"] = r["status"] in TARGET_STATUSES if r["lifecycle"] == LIFECYCLE_RESOLVED else None
    return r


def list_signals(fetch: Fetch, strategy: Dict[str, Any], *, symbol: Optional[str] = None,
                 direction: Optional[str] = None, status: Optional[str] = None,
                 lifecycle_state: Optional[str] = None, evaluation_flag: Optional[str] = None,
                 resolution_flag: Optional[str] = None, date_from: Optional[date] = None,
                 date_to: Optional[date] = None, model_version: Optional[str] = None,
                 sector: Optional[str] = None, quality_grade: Optional[str] = None,
                 sort: str = "newest", limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """Paginated, filtered signals for one strategy. Raises ValueError on an unknown filter value or
    sort key (the caller maps that to a 4xx) -- never interpolates caller input into SQL."""
    if sort not in SORTS:
        raise ValueError(f"unknown sort '{sort}'")
    if not 1 <= limit <= MAX_PAGE_SIZE or offset < 0:
        raise ValueError(f"limit must be 1..{MAX_PAGE_SIZE} and offset >= 0")
    where: List[str] = ["sl.strategy_id = %(sid)s"]
    params: Dict[str, Any] = {"sid": strategy["id"]}

    def add(clause: str, key: str, value: Any) -> None:
        where.append(clause)
        params[key] = value

    if symbol:
        add("sl.symbol = %(symbol)s", "symbol", symbol.upper())
    if direction:
        if direction not in DIRECTION_VALUES:
            raise ValueError(f"unknown direction '{direction}'")
        add("sl.direction = %(direction)s", "direction", DIRECTION_VALUES[direction])
    if status:
        if status not in ALL_STATUSES:
            raise ValueError(f"unknown status '{status}'")
        add("sl.status = %(status)s", "status", status)
    if lifecycle_state:
        if lifecycle_state not in LIFECYCLES:
            raise ValueError(f"unknown lifecycle '{lifecycle_state}'")
        where.append(LIFECYCLE_SQL[lifecycle_state])
    if evaluation_flag:
        if evaluation_flag not in EVALUATION_FLAGS:
            raise ValueError(f"unknown evaluation_flag '{evaluation_flag}'")
        add("sl.evaluation_flag = %(eflag)s", "eflag", evaluation_flag)
    if resolution_flag:
        if resolution_flag not in RESOLUTION_FLAGS:
            raise ValueError(f"unknown resolution_flag '{resolution_flag}'")
        add("sl.resolution_flag = %(rflag)s", "rflag", resolution_flag)
    if date_from:
        add("sl.signal_date >= %(date_from)s", "date_from", date_from)
    if date_to:
        add("sl.signal_date <= %(date_to)s", "date_to", date_to)
    if model_version:
        add("sl.model_version = %(model_version)s", "model_version", model_version)
    if sector:
        add("sl.sector = %(sector)s", "sector", sector)
    if quality_grade:
        add("sl.quality_grade = %(grade)s", "grade", quality_grade.upper())

    where_sql = " AND ".join(where)
    total = int(fetch(f"SELECT count(*) AS n FROM signal_ledger sl WHERE {where_sql}", params)[0]["n"])
    rows = fetch(f"""SELECT {SIGNAL_COLUMNS} FROM signal_ledger sl WHERE {where_sql}
                     ORDER BY {SORTS[sort]} LIMIT %(limit)s OFFSET %(offset)s""",
                 {**params, "limit": limit, "offset": offset})
    items = [_signal_item(r) for r in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset, "sort": sort,
            "has_more": offset + len(items) < total}


def get_signal(fetch: Fetch, strategy: Dict[str, Any], ledger_id: int) -> Optional[Dict[str, Any]]:
    rows = fetch(f"""
        SELECT {SIGNAL_COLUMNS}, sl.feature_set_version, sl.observation_id, sl.feature_snapshot_id,
               sl.created_at, ref.l AS reference_session, lb.latest_bar,
               (SELECT count(*) FROM stock_prices sp WHERE sp.symbol = sl.symbol
                  AND sp.date > sl.signal_date AND sp.date <= ref.l) AS forward_bars_available
        FROM signal_ledger sl
        CROSS JOIN (SELECT max(date) AS l FROM stock_prices) ref
        LEFT JOIN LATERAL (SELECT max(sp.date) AS latest_bar FROM stock_prices sp
                           WHERE sp.symbol = sl.symbol AND sp.date <= ref.l) lb ON TRUE
        WHERE sl.id = %s AND sl.strategy_id = %s
    """, (ledger_id, strategy["id"]))
    if not rows:
        return None
    r = _signal_item(rows[0])
    risk = abs(r["entry_price"] - r["stop_price"])
    timeline = [{"date": r["signal_date"], "event": "signal"}]
    if r["lifecycle"] == LIFECYCLE_RESOLVED:
        timeline.append({"date": r["resolved_date"], "event": r["status"]})
    elif r["lifecycle"] == LIFECYCLE_HELD:
        timeline.append({"date": r["last_evaluated_date"], "event": f"held:{r['evaluation_flag']}"})
    elif r["last_evaluated_date"] and r["last_evaluated_date"] > r["signal_date"]:
        timeline.append({"date": r["last_evaluated_date"], "event": "last_evaluated"})
    return {
        "id": r["id"],
        "strategy": strategy,
        "identity": {"symbol": r["symbol"], "signal_date": r["signal_date"], "direction": r["direction"],
                     "strategy_version": r["strategy_version"]},
        "trade_plan": {"entry_price": r["entry_price"], "stop_price": r["stop_price"],
                       "target1_price": r["target1_price"], "target2_price": r["target2_price"],
                       "target3_price": r["target3_price"], "atr": r["atr"], "risk_per_share": round(risk, 4),
                       "risk_pct_of_entry": round(risk / r["entry_price"], 4) if r["entry_price"] else None},
        "context": {"quality_grade": r["quality_grade"], "sector": r["sector"],
                    "model_version": r["model_version"], "feature_set_version": r["feature_set_version"]},
        "lifecycle": {"state": r["lifecycle"], "status": r["status"], "is_winner": r["is_winner"],
                      "outcome_r": r["outcome_r"], "resolved_date": r["resolved_date"],
                      "bars_held": r["bars_held"], "mae_r": r["mae_r"],
                      "last_evaluated_date": r["last_evaluated_date"],
                      "resolution_flag": r["resolution_flag"], "evaluation_flag": r["evaluation_flag"],
                      "reference_session": r["reference_session"],
                      "symbol_latest_bar": r["latest_bar"],
                      "forward_bars_available": int(r["forward_bars_available"])},
        "provenance": {"ledger_created_at": r["created_at"], "observation_id": r["observation_id"],
                       "feature_snapshot_id": r["feature_snapshot_id"],
                       "release_b_lineage": r["observation_id"] is not None or r["feature_snapshot_id"] is not None},
        "timeline": timeline,
    }
