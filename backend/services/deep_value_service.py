# File: backend/services/deep_value_service.py
"""
Deep Value Service - finds stocks trading near their 12-month low at a cheap
valuation that do not fail a balance-sheet check, and flags the subset that
just flipped from a quarterly net loss to a net profit -- the turnaround
pattern.

This is a DESCRIPTIVE screen. It makes no claim that the stocks it lists will outperform, and the one historical
test that exists (docs/research/2026-10-03_alerts-deep-value-backtest, ~15 months, one regime) found the page rule
lagging the equal-weight universe, so nothing here, in the API field names (turnaround_alerts is kept only for
compatibility) or in the page may call the list a signal, a higher-conviction set or a recommendation.

Value-trap screening (2026-10-03): this used to filter on
daily_fundamentals.financial_health_score, which is only market cap + P/B
(see fundamentals_updater.calculate_financial_health_score) -- any $2B+ company
with a normal P/B passed automatically, whatever its debt or cash burn. That
score is also a live ML feature, so it is left untouched; this page instead
runs its own explicit checks on the latest quarterly_fundamentals row
(assess_balance_sheet). A stock is dropped only when a check FAILS; a check
with missing inputs is reported as 'unknown', never assumed to pass or fail.

Structural weaknesses (negative equity, high debt/equity, current ratio < 1)
are only a FAIL when the company is not generating operating cash; with
positive operating cash flow they are a WARN and the stock stays listed.
Measured on the local DB (2026-09-24 data): as hard fails they excluded
44/56 utilities and 42/70 REITs on the page -- normal capital structures for
those sectors, and for buyback-funded negative equity (MCD-style), not
distress. A short cash runway is always a FAIL.

API compatibility: the response still carries financial_health_score (unchanged, read-only -- the ML
feature is never recomputed or written here), and scan() still honours the old min_financial_health_score
floor when a caller passes it, so the previously deployed frontend keeps its old behaviour against this
backend. The current frontend no longer sends it.
"""

import time
from datetime import date
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

# The underlying data (daily_fundamentals, monthly_technical_indicators,
# stock_prices, quarterly_fundamentals) only changes once per day via the
# automation pipeline, but this service is re-instantiated per request (see
# routers/deep_value.py's get_deep_value_service Depends). Caching the raw
# SQL fetch here -- module-level, so it survives across those per-request
# instances -- means the (previously uncached, full-table-scan) DB round
# trip only actually happens once per TTL instead of on every request;
# scan()'s cheap in-Python filtering by near_low_pct/valuation score
# still runs fresh every call so query-param changes are still honored.
_RAW_CACHE_TTL_SECONDS = 900  # matches main.py's dashboard cache convention
_raw_cache: Dict[str, Any] = {"rows": None, "quarter_map": None, "ts": 0.0}

# Balance-sheet thresholds -- the page states them verbatim, keep in sync with
# frontend/src/app/alerts/page.tsx.
MAX_DEBT_TO_EQUITY = 2.0
MIN_CURRENT_RATIO = 1.0
MIN_CASH_RUNWAY_QUARTERS = 4.0
# Debt/equity and current ratio do not describe a bank's or insurer's balance
# sheet (deposits are debt, no current/non-current split), so those two
# checks are 'n/a' for this sector rather than a guaranteed fail/unknown.
LEVERAGE_NA_SECTORS = {"Financial Services"}

# Turnaround: the two quarters compared must be adjacent (a missing quarter in
# between would compare non-consecutive periods), and the latest one must be
# recent enough to still be "news" relative to the price date.
MAX_QUARTER_GAP_DAYS = 120
MAX_LATEST_QUARTER_AGE_DAYS = 200
# Year-ago comparison quarter: 365 +/- ~45 days before the latest quarter.
YEAR_AGO_MIN_DAYS, YEAR_AGO_MAX_DAYS = 320, 410
# How many trailing quarters to load per symbol: latest, previous, and enough
# history to reach the year-ago quarter.
QUARTERS_LOADED = 6

PASS, WARN, FAIL, UNKNOWN, NA = "pass", "warn", "fail", "unknown", "n/a"


def _num(v) -> Optional[float]:
    return float(v) if v is not None else None


def assess_balance_sheet(q: Optional[Dict[str, Any]], sector: Optional[str]) -> Dict[str, Any]:
    """
    Four checks on one quarterly_fundamentals row (values are that quarter's,
    not TTM). Returns {'checks': {name: status}, 'values': {...}, 'quarter'}.

      equity     total_equity > 0 (negative equity = liabilities exceed assets)
      leverage   total_debt / total_equity <= MAX_DEBT_TO_EQUITY
      liquidity  current_assets / current_liabilities >= MIN_CURRENT_RATIO
      cash_flow  operating_cash_flow > 0, or, if the company is burning cash,
                 cash covers >= MIN_CASH_RUNWAY_QUARTERS quarters of the
                 free-cash-flow burn

    A breach of equity/leverage/liquidity is WARN when operating cash flow is
    positive (or unknown -- a fail needs evidence) and FAIL when it is <= 0.
    """
    q = q or {}
    equity = _num(q.get("total_equity"))
    debt = _num(q.get("total_debt"))
    cur_assets = _num(q.get("current_assets"))
    cur_liab = _num(q.get("current_liabilities"))
    cash = _num(q.get("cash"))
    ocf = _num(q.get("operating_cash_flow"))
    fcf = _num(q.get("free_cash_flow"))
    na_sector = sector in LEVERAGE_NA_SECTORS
    breach = FAIL if ocf is not None and ocf <= 0 else WARN

    checks: Dict[str, str] = {}

    checks["equity"] = UNKNOWN if equity is None else (PASS if equity > 0 else breach)

    debt_to_equity = None
    if na_sector:
        checks["leverage"] = NA
    elif equity is not None and equity <= 0:
        checks["leverage"] = breach  # ratio is meaningless; negative equity already says it
    elif equity is None or debt is None:
        checks["leverage"] = UNKNOWN
    else:
        debt_to_equity = debt / equity
        checks["leverage"] = PASS if debt_to_equity <= MAX_DEBT_TO_EQUITY else breach

    current_ratio = None
    if na_sector:
        checks["liquidity"] = NA
    elif cur_assets is None or not cur_liab:
        checks["liquidity"] = UNKNOWN
    else:
        current_ratio = cur_assets / cur_liab
        checks["liquidity"] = PASS if current_ratio >= MIN_CURRENT_RATIO else breach

    runway_quarters = None
    if ocf is None:
        checks["cash_flow"] = UNKNOWN
    elif ocf > 0:
        checks["cash_flow"] = PASS
    elif fcf is None or cash is None:
        checks["cash_flow"] = UNKNOWN
    elif fcf >= 0:
        checks["cash_flow"] = PASS  # negative OCF fully offset (e.g. asset sales) -- no burn
    else:
        runway_quarters = cash / -fcf
        checks["cash_flow"] = PASS if runway_quarters >= MIN_CASH_RUNWAY_QUARTERS else FAIL

    return {
        "quarter": q["quarter"].strftime("%Y-%m-%d") if q.get("quarter") else None,
        "checks": checks,
        "values": {
            "debt_to_equity": round(debt_to_equity, 2) if debt_to_equity is not None else None,
            "current_ratio": round(current_ratio, 2) if current_ratio is not None else None,
            "operating_cash_flow": ocf,
            "cash_runway_quarters": round(runway_quarters, 1) if runway_quarters is not None else None,
        },
    }


def assess_turnaround(
    latest: Optional[Dict[str, Any]], previous: Optional[Dict[str, Any]], as_of: date
) -> bool:
    """Latest quarter profitable, the adjacent prior quarter a loss, latest not stale vs as_of."""
    if not latest or not previous:
        return False
    if latest.get("net_income") is None or previous.get("net_income") is None:
        return False
    if (latest["quarter"] - previous["quarter"]).days > MAX_QUARTER_GAP_DAYS:
        return False
    if (as_of - latest["quarter"]).days > MAX_LATEST_QUARTER_AGE_DAYS:
        return False
    return latest["net_income"] > 0 and previous["net_income"] < 0


def find_year_ago_quarter(quarters: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    The quarter ~1 year before quarters[0] (newest-first list), with net income.
    A loss->profit flip whose year-ago quarter was ALSO profitable is often
    seasonal (e.g. CRH, SITE, SAM lose money every Q1), not a turnaround --
    the page shows it so that call is visible, it is not hidden or decided here.
    """
    if not quarters:
        return None
    latest_q = quarters[0]["quarter"]
    candidates = [
        q for q in quarters[1:]
        if q.get("net_income") is not None
        and YEAR_AGO_MIN_DAYS <= (latest_q - q["quarter"]).days <= YEAR_AGO_MAX_DAYS
    ]
    return min(candidates, key=lambda q: abs((latest_q - q["quarter"]).days - 365), default=None)


class DeepValueService:
    """DB-backed deep-value / earnings-turnaround scanner"""

    def __init__(self, db_connection_func):
        self.get_db_connection = db_connection_func

    def _get_quarter_map(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        The QUARTERS_LOADED most recent quarterly_fundamentals rows per symbol,
        newest first. Rows without fiscal_year are excluded: those are the
        ~980-row one-off migration snapshot (quarter = 2025-07-01, created
        2025-07-10 -- see quarterly_fundamentals_updater.py's module docstring),
        not a real period end. They sit 1 day from the real 2025-06-30 rows
        with different (older) values, and the yfinance path never purges them
        (_purge_overlapping_quarters only runs for Tiingo data).
        """
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT symbol, quarter, net_income, total_debt, total_equity,
                       current_assets, current_liabilities, cash,
                       operating_cash_flow, free_cash_flow
                FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY quarter DESC) AS rn
                    FROM quarterly_fundamentals
                    WHERE fiscal_year IS NOT NULL
                ) q
                WHERE rn <= %s
                ORDER BY symbol, quarter DESC
            """, (QUARTERS_LOADED,))
            quarter_map: Dict[str, List[Dict[str, Any]]] = {}
            for row in cursor.fetchall():
                quarter_map.setdefault(row['symbol'], []).append(row)
            return quarter_map
        finally:
            conn.close()

    def _fetch_rows(self) -> List[Dict[str, Any]]:
        """
        Raw candidate rows: daily_fundamentals (narrowed to the last 40 days --
        it refreshes daily, so anything older is stale anyway) drives the
        query, with monthly_technical_indicators and stock_prices looked up
        per-symbol via LATERAL instead of each running its own DISTINCT ON
        over the FULL table (millions of stock_prices rows). Same per-symbol-
        lookup fix already applied to the dashboard queries in main.py and to
        market_service.py -- this was the last remaining full-table-scan spot.
        """
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                WITH latest_fundamentals AS (
                    SELECT DISTINCT ON (symbol)
                        symbol, sector, market_cap, pe_ratio,
                        valuation_score, financial_health_score, overall_quality_score, quality_grade
                    FROM daily_fundamentals
                    WHERE date >= CURRENT_DATE - INTERVAL '40 days'
                    AND valuation_score IS NOT NULL
                    ORDER BY symbol, date DESC
                )
                SELECT
                    lf.symbol, lf.sector, lf.market_cap, lf.pe_ratio,
                    lf.valuation_score, lf.financial_health_score, lf.overall_quality_score, lf.quality_grade,
                    lm.donchian_low_12m, lp.current_price, lp.price_date
                FROM latest_fundamentals lf
                LEFT JOIN LATERAL (
                    SELECT donchian_low_12m
                    FROM monthly_technical_indicators
                    WHERE symbol = lf.symbol
                    ORDER BY month_ending_date DESC
                    LIMIT 1
                ) lm ON true
                LEFT JOIN LATERAL (
                    SELECT close AS current_price, date AS price_date
                    FROM stock_prices
                    WHERE symbol = lf.symbol
                    ORDER BY date DESC
                    LIMIT 1
                ) lp ON true
                WHERE lm.donchian_low_12m IS NOT NULL
                AND lm.donchian_low_12m > 0
                AND lp.current_price IS NOT NULL
            """)
            return cursor.fetchall()
        finally:
            conn.close()

    def _get_raw_data(self) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
        now = time.time()
        if _raw_cache["rows"] is not None and (now - _raw_cache["ts"]) < _RAW_CACHE_TTL_SECONDS:
            return _raw_cache["rows"], _raw_cache["quarter_map"]

        rows = self._fetch_rows()
        quarter_map = self._get_quarter_map()
        _raw_cache["rows"] = rows
        _raw_cache["quarter_map"] = quarter_map
        _raw_cache["ts"] = now
        return rows, quarter_map

    def scan(
        self,
        near_low_pct: float = 15.0,
        min_valuation_score: float = 12.0,
        min_financial_health_score: Optional[float] = None,
    ) -> Dict[str, Any]:
        """min_financial_health_score: DEPRECATED, compatibility only (see module docstring). None = not
        applied. When given, rows without a score are excluded, exactly as the old SQL filter did."""
        rows, quarter_map = self._get_raw_data()

        deep_value_watch = []
        turnaround_alerts = []
        excluded_balance_sheet = 0

        for row in rows:
            price = float(row['current_price'])
            low = float(row['donchian_low_12m'])
            distance_from_low_pct = (price - low) / low * 100

            valuation_score = float(row['valuation_score'])

            if not (distance_from_low_pct <= near_low_pct and valuation_score >= min_valuation_score):
                continue

            health = float(row['financial_health_score']) if row['financial_health_score'] is not None else None
            if min_financial_health_score is not None and (health is None or health < min_financial_health_score):
                continue

            quarters = quarter_map.get(row['symbol'], [])
            latest = quarters[0] if quarters else None
            previous = quarters[1] if len(quarters) > 1 else None

            balance_sheet = assess_balance_sheet(latest, row['sector'])
            if FAIL in balance_sheet['checks'].values():
                excluded_balance_sheet += 1
                continue

            is_turnaround = assess_turnaround(latest, previous, row['price_date'])

            entry = {
                'symbol': row['symbol'],
                'sector': row['sector'] or 'Unknown',
                'current_price': price,
                'price_date': row['price_date'].strftime('%Y-%m-%d'),
                'distance_from_low_pct': round(distance_from_low_pct, 2),
                'donchian_low_12m': round(low, 2),
                'valuation_score': valuation_score,
                'financial_health_score': health,  # unchanged ML-feature value, passed through for old clients
                'overall_quality_score': float(row['overall_quality_score']) if row['overall_quality_score'] is not None else None,
                'quality_grade': row['quality_grade'],
                'pe_ratio': float(row['pe_ratio']) if row['pe_ratio'] is not None else None,
                'market_cap': float(row['market_cap']) if row['market_cap'] is not None else None,
                'balance_sheet': balance_sheet,
                'is_turnaround': is_turnaround,
            }

            if is_turnaround:
                year_ago = find_year_ago_quarter(quarters)
                entry.update({
                    'latest_quarter': latest['quarter'].strftime('%Y-%m-%d'),
                    'latest_net_income': float(latest['net_income']),
                    'previous_quarter': previous['quarter'].strftime('%Y-%m-%d'),
                    'previous_net_income': float(previous['net_income']),
                    'year_ago_quarter': year_ago['quarter'].strftime('%Y-%m-%d') if year_ago else None,
                    'year_ago_net_income': float(year_ago['net_income']) if year_ago else None,
                })
                turnaround_alerts.append(entry)
            else:
                deep_value_watch.append(entry)

        deep_value_watch.sort(key=lambda e: e['distance_from_low_pct'])
        turnaround_alerts.sort(key=lambda e: e['distance_from_low_pct'])

        return {
            'turnaround_alerts': turnaround_alerts,
            'deep_value_watch': deep_value_watch,
            'excluded_balance_sheet': excluded_balance_sheet,
        }
