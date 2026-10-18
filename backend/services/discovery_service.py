# File: backend/services/discovery_service.py
"""
Smart Screener v2 (equity discovery): one explicit market session, read-only, one query.

Session alignment (the integrity rules this service exists to enforce):
  * The session S is explicit: the caller's `session`, or the newest REAL session in stock_prices (a date with bars for at
    least half the symbols of the busiest date in the window). A date that is not a real session is refused.
  * Every per-session input is read for exactly S (or the previous real session P where a definition needs it). Nothing
    is ever substituted from another session: a stock without a technical_indicators row for S has no technical metrics,
    a stock without an observed relative-strength row for S has no RS metrics. Those gaps are counted, never filled.
  * Relative strength and the 5/20/60-session returns come from stock_relative_strength rows with provenance 'observed'
    (captured by the daily market-intelligence run for that session): family "observed".
  * Channel, breakout, ATR, volume-ratio and dollar-volume figures are computed from stored daily bars and indicators for S
    (and P): family "derived". They use bars up to and including S only.
  * Market cap, sector (fallback) and industry come from daily_fundamentals rows known by the session: date <= S, written
    (greatest(created_at, updated_at), UTC) before the knowledge cutoff = noon New York on the calendar day after S, and
    not older than FUNDAMENTALS_MAX_AGE_DAYS: family "reference". The table has no publication/filing timestamps; the
    write time is the only availability evidence, and rows rewritten after the cutoff are skipped, not used.
  * Dollar volume = mean(close * volume) over exactly the last 20 real sessions ending at S; a stock needs all 20 bars,
    otherwise the value is unavailable and the shortfall is counted.

Every sort key and filter is checked against allowlists before any SQL is built; values are always bound parameters.
Pagination is bounded (page_size <= MAX_PAGE_SIZE; export <= MAX_EXPORT_ROWS). Nothing here writes.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
REAL_SESSION_SHARE = 0.5
SESSION_WINDOW_DAYS = 60          # calendar days scanned to find S, P and the 20-session window (>= 20 sessions)
DV_SESSIONS = 20
FUNDAMENTALS_MAX_AGE_DAYS = 30
MAX_PAGE_SIZE = 200
MAX_EXPORT_ROWS = 5000
RS_MODEL = "rs_v1"

OBSERVED, DERIVED, REFERENCE, KEY = "observed", "derived", "reference", "key"


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    family: str
    definition: str
    numeric: bool = True
    filterable: bool = True


METRICS: Dict[str, Metric] = {m.key: m for m in [
    Metric("close", "Close", "$", DERIVED, "Closing price on the session."),
    Metric("change_1d", "1-day change", "%", DERIVED, "Close vs the previous session's close."),
    Metric("ret_5", "5-session return", "%", OBSERVED, "Close vs the close 5 sessions earlier, from the observed relative-strength snapshot."),
    Metric("ret_20", "20-session return", "%", OBSERVED, "Close vs the close 20 sessions earlier (observed snapshot)."),
    Metric("ret_60", "60-session return", "%", OBSERVED, "Close vs the close 60 sessions earlier (observed snapshot)."),
    Metric("vs_spx_20", "vs S&P 500, 20 sessions", "pp", OBSERVED, "20-session return minus the S&P 500's (observed snapshot)."),
    Metric("vs_spx_60", "vs S&P 500, 60 sessions", "pp", OBSERVED, "60-session return minus the S&P 500's (observed snapshot)."),
    Metric("vs_sector_20", "vs sector, 20 sessions", "pp", OBSERVED, "20-session return minus the median of its sector (observed snapshot, sector as known on the session)."),
    Metric("rs_20", "RS percentile, 20 sessions", "pctl", OBSERVED, "Percentile (0-100) of the 20-session return among all valid stocks on the session (observed snapshot)."),
    Metric("rs_60", "RS percentile, 60 sessions", "pctl", OBSERVED, "Percentile (0-100) of the 60-session return among all valid stocks on the session (observed snapshot)."),
    Metric("breakout_20", "20-day breakout", "flag", DERIVED, "Close above the previous session's 20-day high (the high of the 20 sessions ending at the previous session).", numeric=False),
    Metric("new_high_20", "New 20-day high", "flag", DERIVED, "The session's high is the highest high of the 20 sessions ending at the session.", numeric=False),
    Metric("channel_pos", "Channel position", "%", DERIVED, "Where the close sits in the 20-day high-low channel (0 = low, 100 = high)."),
    Metric("channel_width", "Channel width", "%", DERIVED, "20-day high minus 20-day low, as % of the price."),
    Metric("channel_width_pctile", "Channel width percentile", "pctl", DERIVED, "Percentile of the channel width among stocks with a value on the session (low = tight)."),
    Metric("atr_pct", "ATR %", "%", DERIVED, "14-day average true range as % of the close."),
    Metric("atr_pct_pctile", "ATR % percentile", "pctl", DERIVED, "Percentile of ATR % among stocks with a value on the session."),
    Metric("volume_ratio", "Volume ratio", "x", DERIVED, "Session volume vs its 10-day average volume."),
    Metric("dollar_vol_20", "Dollar volume (20)", "$", DERIVED, "Mean of close x volume over the last 20 sessions ending at the session; needs all 20 bars."),
    Metric("market_cap", "Market cap", "$", REFERENCE, "From the latest fundamentals snapshot known by the session (written before noon New York the next day, at most 30 days old)."),
]}
SORT_KEYS = ("symbol", "sector", *METRICS.keys())
FLAG_FILTERS = ("breakout_20", "new_high_20")

PRESETS: Dict[str, Dict[str, Any]] = {
    "leaders": {"label": "Leaders", "description": "RS percentile at least 80 over both 20 and 60 sessions.",
                "filters": {"min_rs_20": 80, "min_rs_60": 80}, "sort": ("rs_60", "desc")},
    "new_highs": {"label": "New highs", "description": "Closed above the previous 20-day high on the session.",
                  "filters": {"breakout_20": True}, "sort": ("rs_20", "desc")},
    "tight_channels": {"label": "Tight channels", "description": "Channel width in the tightest 20% and at least $5M dollar volume.",
                       "filters": {"max_channel_width_pctile": 20, "min_dollar_vol_20": 5e6}, "sort": ("channel_width_pctile", "asc")},
    "liquid_large_caps": {"label": "Liquid large caps", "description": "Market cap at least $10B and at least $50M dollar volume.",
                          "filters": {"min_market_cap": 1e10, "min_dollar_vol_20": 5e7}, "sort": ("market_cap", "desc")},
    "high_volatility": {"label": "High volatility", "description": "ATR % in the top 20% and at least $5M dollar volume.",
                        "filters": {"min_atr_pct_pctile": 80, "min_dollar_vol_20": 5e6}, "sort": ("atr_pct", "desc")},
}


class InvalidRequest(ValueError):
    """A request that is well-formed but cannot be served (unknown session, bad filter). Maps to HTTP 422."""


@dataclass
class Query:
    session: Optional[date] = None
    preset: Optional[str] = None
    sort: str = "rs_20"
    order: str = "desc"
    page: int = 1
    page_size: int = 50
    sectors: Sequence[str] = ()
    q: Optional[str] = None
    ranges: Dict[str, Tuple[Optional[float], Optional[float]]] = field(default_factory=dict)
    flags: Dict[str, bool] = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------------------- pure helpers
def knowledge_cutoff_utc(session: date) -> datetime:
    """Noon New York on the calendar day after the session, as a naive UTC datetime (the stored write times are naive UTC)."""
    return datetime.combine(session + timedelta(days=1), time(12, 0), NY).astimezone(timezone.utc).replace(tzinfo=None)


def resolve_sessions(counts: Sequence[Tuple[date, int]], requested: Optional[date]) -> Dict[str, Any]:
    """From (date, symbols with a bar) for the window: the real sessions, S (requested or newest), P (previous) and the
    first date of the 20-session dollar-volume window (None when fewer than 20 sessions end at S)."""
    if not counts:
        raise InvalidRequest("no price data")
    peak = max(n for _, n in counts)
    real = sorted(d for d, n in counts if n >= REAL_SESSION_SHARE * peak)
    if requested is None:
        s = real[-1]
    elif requested in real:
        s = requested
    else:
        raise InvalidRequest(f"{requested.isoformat()} is not a market session with stored prices")
    upto = [d for d in real if d <= s]
    prev = upto[-2] if len(upto) >= 2 else None
    dv_start = upto[-DV_SESSIONS] if len(upto) >= DV_SESSIONS else None
    return {"session": s, "previous_session": prev, "dv_start": dv_start, "recent_sessions": [d.isoformat() for d in reversed(upto[-20:])]}


def normalise(qr: Query) -> Query:
    """Validate against the allowlists and merge a preset (explicit filters win over the preset's)."""
    if qr.preset is not None and qr.preset not in PRESETS:
        raise InvalidRequest(f"unknown preset '{qr.preset}'")
    if qr.sort not in SORT_KEYS:
        raise InvalidRequest(f"unknown sort key '{qr.sort}'")
    if qr.order not in ("asc", "desc"):
        raise InvalidRequest("order must be asc or desc")
    if not (1 <= qr.page <= 10_000):
        raise InvalidRequest("page out of range")
    if not (1 <= qr.page_size <= MAX_EXPORT_ROWS):
        raise InvalidRequest("page_size out of range")
    for k, (lo, hi) in qr.ranges.items():
        m = METRICS.get(k)
        if m is None or not m.numeric or not m.filterable:
            raise InvalidRequest(f"unknown filter '{k}'")
        if lo is not None and hi is not None and lo > hi:
            raise InvalidRequest(f"min_{k} is above max_{k}")
    for k in qr.flags:
        if k not in FLAG_FILTERS:
            raise InvalidRequest(f"unknown flag filter '{k}'")
    if qr.q is not None and (not qr.q or len(qr.q) > 10 or not all(c.isalnum() or c in ".-" for c in qr.q)):
        raise InvalidRequest("q must be 1-10 letters, digits, '.' or '-'")
    if len(qr.sectors) > 20 or any(len(s) > 60 for s in qr.sectors):
        raise InvalidRequest("too many or too long sector values")
    if qr.preset:
        p = PRESETS[qr.preset]
        ranges = dict(qr.ranges)
        flags = dict(qr.flags)
        for k, v in p["filters"].items():
            if isinstance(v, bool):
                flags.setdefault(k, v)
            else:
                metric = k.split("_", 1)[1]
                lo, hi = ranges.get(metric, (None, None))
                if k.startswith("min_") and lo is None:
                    lo = float(v)
                if k.startswith("max_") and hi is None:
                    hi = float(v)
                ranges[metric] = (lo, hi)
        qr = Query(**{**qr.__dict__, "ranges": ranges, "flags": flags})
    return qr


def build_where(qr: Query) -> Tuple[str, List[Any]]:
    """WHERE clause over the `base` CTE's columns. Column names come only from the allowlist; values are bound."""
    clauses, params = [], []
    for k, (lo, hi) in sorted(qr.ranges.items()):
        if lo is not None:
            clauses.append(f"{k} >= %s")
            params.append(lo)
        if hi is not None:
            clauses.append(f"{k} <= %s")
            params.append(hi)
    for k, v in sorted(qr.flags.items()):
        clauses.append(f"{k} IS {'TRUE' if v else 'FALSE'}")
    if qr.sectors:
        clauses.append("sector = ANY(%s)")
        params.append(list(qr.sectors))
    if qr.q:
        clauses.append("symbol LIKE %s")
        params.append(qr.q.upper() + "%")
    return (" AND ".join(clauses) if clauses else "TRUE"), params


def order_clause(sort: str, order: str) -> str:
    assert sort in SORT_KEYS and order in ("asc", "desc")
    return f"{sort} {order.upper()} NULLS LAST, symbol ASC"


BASE_SQL = """
WITH px AS (
    SELECT symbol, close::float8 AS close, high::float8 AS high, volume::float8 AS volume
    FROM stock_prices WHERE date = %(s)s AND close IS NOT NULL AND close > 0
),
pxp AS (SELECT symbol, close::float8 AS close FROM stock_prices WHERE date = %(p)s AND close IS NOT NULL AND close > 0),
dv AS (
    SELECT symbol, AVG(close::float8 * volume::float8) AS dollar_vol_20, COUNT(*) AS dv_bars
    FROM stock_prices
    WHERE %(dv_start)s::date IS NOT NULL AND date BETWEEN %(dv_start)s AND %(s)s AND close IS NOT NULL AND volume IS NOT NULL
    GROUP BY symbol
),
rs AS (
    SELECT symbol,
           MAX(rs_percentile) FILTER (WHERE horizon_sessions = 20) AS rs_20,
           MAX(rs_percentile) FILTER (WHERE horizon_sessions = 60) AS rs_60,
           MAX(ret_pct) FILTER (WHERE horizon_sessions = 5) AS ret_5,
           MAX(ret_pct) FILTER (WHERE horizon_sessions = 20) AS ret_20,
           MAX(ret_pct) FILTER (WHERE horizon_sessions = 60) AS ret_60,
           MAX(vs_spx_pp) FILTER (WHERE horizon_sessions = 20) AS vs_spx_20,
           MAX(vs_spx_pp) FILTER (WHERE horizon_sessions = 60) AS vs_spx_60,
           MAX(vs_sector_pp) FILTER (WHERE horizon_sessions = 20) AS vs_sector_20,
           MAX(sector) AS rs_sector,
           BOOL_AND(sector_pit_safe) AS rs_sector_pit_safe
    FROM stock_relative_strength
    WHERE session_date = %(s)s AND provenance = 'observed' AND model_version = %(rs_model)s
    GROUP BY symbol
),
joined AS (
    SELECT px.symbol, px.close, px.high,
           CASE WHEN pxp.close IS NOT NULL THEN (px.close / pxp.close - 1) * 100 END AS change_1d,
           ti.atr_14, ti.donchian_high_20, ti.price_position, ti.channel_width_pct, ti.volume_ratio,
           tip.donchian_high_20 AS prev_dh20,
           CASE WHEN dv.dv_bars = %(dv_n)s THEN dv.dollar_vol_20 END AS dollar_vol_20, COALESCE(dv.dv_bars, 0) AS dv_bars,
           rs.rs_20, rs.rs_60, rs.ret_5, rs.ret_20, rs.ret_60, rs.vs_spx_20, rs.vs_spx_60, rs.vs_sector_20,
           rs.rs_sector, rs.rs_sector_pit_safe, rs.symbol IS NOT NULL AS has_rs, ti.found IS NOT NULL AS has_tech,
           df.market_cap::float8 AS market_cap, df.sector AS fund_sector, df.industry, df.date AS fundamentals_date
    FROM px
    LEFT JOIN pxp USING (symbol)
    LEFT JOIN LATERAL (
        SELECT 1 AS found, atr_14::float8 AS atr_14, donchian_high_20::float8 AS donchian_high_20, price_position::float8 AS price_position,
               channel_width_pct::float8 AS channel_width_pct, volume_ratio::float8 AS volume_ratio
        FROM technical_indicators t WHERE t.symbol = px.symbol AND t.date = %(s)s
    ) ti ON true
    LEFT JOIN LATERAL (
        SELECT donchian_high_20::float8 AS donchian_high_20 FROM technical_indicators t WHERE t.symbol = px.symbol AND t.date = %(p)s
    ) tip ON true
    LEFT JOIN dv USING (symbol)
    LEFT JOIN rs USING (symbol)
    LEFT JOIN LATERAL (
        SELECT market_cap, sector, industry, date FROM daily_fundamentals d
        WHERE d.symbol = px.symbol AND d.date <= %(s)s AND d.date > %(s)s::date - %(fund_age)s
          AND greatest(d.created_at, coalesce(d.updated_at, d.created_at)) <= %(cutoff)s
        ORDER BY d.date DESC LIMIT 1
    ) df ON true
),
base AS (
    SELECT symbol,
           COALESCE(rs_sector, fund_sector) AS sector,
           CASE WHEN rs_sector IS NOT NULL THEN 'observed' WHEN fund_sector IS NOT NULL THEN 'reference' END AS sector_source,
           industry, close, change_1d, ret_5, ret_20, ret_60, vs_spx_20, vs_spx_60, vs_sector_20, rs_20, rs_60,
           CASE WHEN prev_dh20 IS NULL THEN NULL ELSE close > prev_dh20 END AS breakout_20,
           CASE WHEN donchian_high_20 IS NULL OR high IS NULL THEN NULL ELSE high >= donchian_high_20 END AS new_high_20,
           price_position AS channel_pos, channel_width_pct AS channel_width,
           CASE WHEN channel_width_pct IS NOT NULL THEN 100 * PERCENT_RANK() OVER (PARTITION BY channel_width_pct IS NOT NULL ORDER BY channel_width_pct) END AS channel_width_pctile,
           CASE WHEN atr_14 IS NOT NULL THEN atr_14 / close * 100 END AS atr_pct,
           CASE WHEN atr_14 IS NOT NULL THEN 100 * PERCENT_RANK() OVER (PARTITION BY atr_14 IS NOT NULL ORDER BY atr_14 / close) END AS atr_pct_pctile,
           volume_ratio, dollar_vol_20, dv_bars, market_cap, fundamentals_date, has_rs, has_tech
    FROM joined
)
"""

ROW_COLUMNS = ["symbol", "sector", "sector_source", "industry", *[k for k in METRICS.keys()], "dv_bars", "fundamentals_date"]


def page_sql(where: str, order: str) -> str:
    cols = ", ".join(ROW_COLUMNS)
    return BASE_SQL + f"""
, filtered AS (SELECT * FROM base WHERE {where})
SELECT json_build_object(
    'universe', (SELECT COUNT(*) FROM base),
    'sectors', COALESCE((SELECT json_agg(DISTINCT sector ORDER BY sector) FROM base WHERE sector IS NOT NULL), '[]'::json),
    'coverage', (SELECT json_build_object(
        'rs', COUNT(*) FILTER (WHERE has_rs), 'rs_20', COUNT(rs_20), 'rs_60', COUNT(rs_60),
        'technical', COUNT(*) FILTER (WHERE has_tech), 'breakout_20', COUNT(breakout_20), 'atr_pct', COUNT(atr_pct),
        'dollar_vol_20', COUNT(dollar_vol_20), 'dollar_vol_insufficient', COUNT(*) FILTER (WHERE dollar_vol_20 IS NULL AND dv_bars > 0),
        'dollar_vol_no_bars', COUNT(*) FILTER (WHERE dv_bars = 0),
        'fundamentals', COUNT(fundamentals_date), 'market_cap', COUNT(market_cap),
        'sector', COUNT(sector), 'sector_observed', COUNT(*) FILTER (WHERE sector_source = 'observed'),
        'change_1d', COUNT(change_1d)) FROM base),
    'total', (SELECT COUNT(*) FROM filtered),
    'rows', COALESCE((SELECT json_agg(r) FROM (SELECT {cols} FROM filtered ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s) r), '[]'::json)
)
"""


SESSIONS_SQL = """
SELECT date, COUNT(*) FROM stock_prices
WHERE date BETWEEN %(lo)s AND %(hi)s AND close IS NOT NULL
GROUP BY date ORDER BY date
"""


def definitions() -> Dict[str, Any]:
    return {
        "metrics": [{"key": m.key, "label": m.label, "unit": m.unit, "family": m.family, "definition": m.definition,
                     "numeric": m.numeric} for m in METRICS.values()],
        "families": {
            OBSERVED: "Captured by the daily market-intelligence run for that session (observed relative-strength snapshot).",
            DERIVED: "Computed from stored daily bars and indicators up to and including the session.",
            REFERENCE: "Latest fundamentals snapshot written before noon New York on the day after the session (at most 30 days old).",
        },
        "presets": [{"key": k, "label": p["label"], "description": p["description"], "sort": list(p["sort"])} for k, p in PRESETS.items()],
        "sort_keys": list(SORT_KEYS),
        "limits": {"max_page_size": MAX_PAGE_SIZE, "max_export_rows": MAX_EXPORT_ROWS},
    }


class DiscoveryService:
    def __init__(self, get_connection: Callable):
        self.get_connection = get_connection

    def _run(self, fn):
        conn = self.get_connection()
        if not conn:
            raise RuntimeError("Database connection failed")
        try:
            conn.rollback()                       # start clean: SET TRANSACTION must be the first statement
            cur = conn.cursor()
            try:
                cur.execute("SET TRANSACTION READ ONLY")
                return fn(cur)
            finally:
                cur.close()
                conn.rollback()
        finally:
            conn.close()

    def search(self, qr: Query, export: bool = False) -> Dict[str, Any]:
        qr = normalise(qr)
        if not export and qr.page_size > MAX_PAGE_SIZE:
            raise InvalidRequest(f"page_size must be at most {MAX_PAGE_SIZE}")
        sort, order = qr.sort, qr.order
        if qr.preset and (qr.sort, qr.order) == ("rs_20", "desc"):
            sort, order = PRESETS[qr.preset]["sort"]
        where, wparams = build_where(qr)

        def work(cur):
            hi = qr.session or date.max
            if qr.session is None:
                cur.execute("SELECT MAX(date) FROM stock_prices")
                hi = cur.fetchone()[0]
                if hi is None:
                    raise InvalidRequest("no price data")
            cur.execute(SESSIONS_SQL, {"lo": hi - timedelta(days=SESSION_WINDOW_DAYS), "hi": hi})
            sess = resolve_sessions([(d, int(n)) for d, n in cur.fetchall()], qr.session)
            named = {"s": sess["session"], "p": sess["previous_session"], "dv_start": sess["dv_start"], "dv_n": DV_SESSIONS,
                     "rs_model": RS_MODEL, "fund_age": FUNDAMENTALS_MAX_AGE_DAYS, "cutoff": knowledge_cutoff_utc(sess["session"]),
                     "limit": MAX_EXPORT_ROWS if export else qr.page_size, "offset": 0 if export else (qr.page - 1) * qr.page_size}
            sql, params = _bind(page_sql(where, order_clause(sort, order)), named, wparams)
            cur.execute(sql, params)
            payload = cur.fetchone()[0]
            if isinstance(payload, str):
                payload = json.loads(payload)
            return sess, payload

        sess, payload = self._run(work)
        return {
            "session": sess["session"].isoformat(),
            "previous_session": sess["previous_session"].isoformat() if sess["previous_session"] else None,
            "dollar_volume_window": {"from": sess["dv_start"].isoformat() if sess["dv_start"] else None, "to": sess["session"].isoformat(), "sessions": DV_SESSIONS},
            "fundamentals_cutoff_utc": knowledge_cutoff_utc(sess["session"]).isoformat(timespec="minutes"),
            "recent_sessions": sess["recent_sessions"],
            "universe": payload["universe"],
            "sectors": payload.get("sectors") or [],
            "coverage": payload["coverage"],
            "total": payload["total"],
            "page": 1 if export else qr.page,
            "page_size": len(payload["rows"]) if export else qr.page_size,
            "sort": sort, "order": order, "preset": qr.preset,
            "applied_filters": {"ranges": {k: list(v) for k, v in qr.ranges.items()}, "flags": qr.flags, "sectors": list(qr.sectors), "q": qr.q},
            "rows": payload["rows"],
            "truncated": export and payload["total"] > MAX_EXPORT_ROWS,
        }


def _bind(sql: str, named: Dict[str, Any], positional: List[Any]) -> Tuple[str, Dict[str, Any]]:
    """psycopg2 cannot mix %(name)s and %s: turn the WHERE clause's positional %s into named parameters."""
    params = dict(named)
    parts = sql.split("%s")
    if len(parts) - 1 != len(positional):
        raise AssertionError("placeholder count mismatch")
    out = parts[0]
    for i, part in enumerate(parts[1:]):
        key = f"w{i}"
        params[key] = positional[i]
        out += f"%({key})s" + part
    return out, params


def to_csv(result: Dict[str, Any]) -> str:
    """CSV of the rows with a header block stating the session and data families; missing values are empty cells."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([f"# First Light screener export · session {result['session']} · {result['total']} matching stocks"
                + (f" (first {len(result['rows'])})" if result.get("truncated") else "")])
    w.writerow(["# Information only. Not investment advice. Observed = captured for the session; derived = from stored daily bars; reference = fundamentals snapshot known by the session."])
    w.writerow(ROW_COLUMNS)
    for r in result["rows"]:
        w.writerow(["" if r.get(c) is None else r.get(c) for c in ROW_COLUMNS])
    return buf.getvalue()


# ------------------------------------------------------------------------------------------------- query-string parsing
_BASE_PARAMS = {"session", "preset", "sort", "order", "page", "page_size", "sector", "q", *FLAG_FILTERS}


def parse_params(items: Sequence[Tuple[str, str]]) -> Query:
    """Query string -> Query, accepting only allowlisted parameter names (min_<metric> / max_<metric> for numeric
    metrics, the flag filters, and the base parameters). Anything else is refused, never ignored."""
    qr = Query()
    ranges: Dict[str, List[Optional[float]]] = {}
    sectors: List[str] = []
    for name, raw in items:
        val = raw.strip()
        try:
            if name == "session":
                qr.session = date.fromisoformat(val)
            elif name == "preset":
                qr.preset = val or None
            elif name == "sort":
                qr.sort = val
            elif name == "order":
                qr.order = val
            elif name == "page":
                qr.page = int(val)
            elif name == "page_size":
                qr.page_size = int(val)
            elif name == "sector":
                if val:
                    sectors.append(val)
            elif name == "q":
                qr.q = val or None
            elif name in FLAG_FILTERS:
                if val.lower() not in ("true", "false"):
                    raise InvalidRequest(f"{name} must be true or false")
                qr.flags[name] = val.lower() == "true"
            elif name.startswith(("min_", "max_")) and name[4:] in METRICS and METRICS[name[4:]].numeric:
                v = float(val)
                if v != v or v in (float("inf"), float("-inf")):
                    raise InvalidRequest(f"{name} must be a finite number")
                lo_hi = ranges.setdefault(name[4:], [None, None])
                lo_hi[0 if name.startswith("min_") else 1] = v
            else:
                raise InvalidRequest(f"unknown parameter '{name}'")
        except InvalidRequest:
            raise
        except (TypeError, ValueError):
            raise InvalidRequest(f"invalid value for '{name}'")
    qr.sectors = tuple(sectors)
    qr.ranges = {k: (v[0], v[1]) for k, v in ranges.items()}
    return qr
