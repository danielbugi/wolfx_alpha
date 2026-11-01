"""Versioned, declared-in-advance parameters of the inflection engine. Changing any threshold or weight is a NEW method version (a new event row family), never an edit:
the version is part of every stored row's identity, so results computed under different rules can never be mixed.
"""
from __future__ import annotations

METHOD_VERSION = "inflection_v1"
SOURCE_QUARTERLY = "quarterly_fundamentals"
SOURCE_SEC = "sec_edgar"                     # quarters from SEC filings: availability = the filing's acceptance date (point in time)
METHOD_VERSION_SEC = "inflection_v1_sec"     # same method, own event set: SEC-sourced events never overwrite the yfinance-sourced ones

SOURCE_SEC_PIT = "sec_edgar_pit"
METHOD_VERSION_SEC_PIT = "inflection_v2_sec_pit"   # SEC quarters resolved per filing version: every figure is the one public at the quarter's first-public date (restatements cannot leak backwards)

# Event types (stored as these strings).
PROFITABILITY_TURNAROUND = "profitability_turnaround"
REVENUE_ACCELERATION = "revenue_acceleration"
MARGIN_EXPANSION = "margin_expansion"
OPERATING_LEVERAGE = "operating_leverage_inflection"
FCF_TURNAROUND = "fcf_turnaround"
LIMITED_REPRICING = "improvement_with_limited_repricing"
EVENT_TYPES = (PROFITABILITY_TURNAROUND, REVENUE_ACCELERATION, MARGIN_EXPANSION, OPERATING_LEVERAGE, FCF_TURNAROUND, LIMITED_REPRICING)
LABELS = {
    PROFITABILITY_TURNAROUND: "Profitability turnaround",
    REVENUE_ACCELERATION: "Revenue acceleration",
    MARGIN_EXPANSION: "Margin expansion",
    OPERATING_LEVERAGE: "Operating leverage inflection",
    FCF_TURNAROUND: "Free cash flow turnaround",
    LIMITED_REPRICING: "Fundamental improvement with limited price repricing",
}

# Quarter matching: a prior-year quarter ends 365 +- 25 days earlier, the previous quarter 91 +- 25 days earlier (fiscal calendars drift, 52/53-week years).
YEAR_DAYS, YEAR_TOL = 365, 25
QUARTER_DAYS, QUARTER_TOL = 91, 25

# When statements became public. The earnings report (earnings_calendar, with a reported EPS) on or after quarter end + 5 days and within +130 days; otherwise a conservative assumed lag.
AVAILABILITY_MIN_DAYS, AVAILABILITY_MAX_DAYS, ASSUMED_LAG_DAYS = 5, 130, 75

# Detection thresholds (the event exists when the condition holds) and the value at which the event's strength reaches 100 (strength 50 at the threshold, linear in between and beyond, 0..100).
THRESHOLDS = {
    REVENUE_ACCELERATION: {"rev_yoy_min": 0.10, "rev_yoy_max": 2.0, "accel_pp_min": 5.0, "accel_pp_strong": 20.0, "qoq_min": 0.0},
    MARGIN_EXPANSION: {"opm_yoy_bps_min": 300.0, "opm_yoy_bps_strong": 1000.0, "opm_min": 0.0, "rev_yoy_min": 0.0},
    OPERATING_LEVERAGE: {"leverage_min": 2.0, "leverage_strong": 5.0, "rev_yoy_min": 0.08},
    PROFITABILITY_TURNAROUND: {"swing_pct_rev_min": 0.02, "swing_pct_rev_strong": 0.10},
    FCF_TURNAROUND: {"fcf_margin_yoy_pp_min": 3.0, "fcf_margin_yoy_pp_strong": 15.0},
}
LOW_CASH_CONVERSION = 0.5            # operating cash flow / net income below this halves nothing but costs a profit or FCF event 15 strength points
CASH_CONVERSION_PENALTY = 15.0
SEQUENTIAL_BONUS = 10.0              # a turnaround that is also a loss in the PREVIOUS quarter (not a one-off year-ago comparison)
INORGANIC_REV_QOQ = 0.60             # a quarter-on-quarter revenue jump above this, or a year-on-year ratio above 4x, is flagged (acquisition / reclassification suspect) and excluded
INORGANIC_REV_YOY = 3.0

# Inflection score 0..100 = sum(weight x event strength / 100) / sum(weights) x 100 over the event types present, 0 for absent ones. Weights sum to 1 with the quality bonus.
SCORE_WEIGHTS = {REVENUE_ACCELERATION: 0.25, MARGIN_EXPANSION: 0.20, PROFITABILITY_TURNAROUND: 0.20, OPERATING_LEVERAGE: 0.15, FCF_TURNAROUND: 0.15}
QUALITY_BONUS_WEIGHT = 0.05          # cash conversion >= 0.8 with positive net income
GOOD_CASH_CONVERSION = 0.8

# Severity of an event by strength (the alert layer uses it): high >= 75, medium >= 55, else low. The composite event uses the inflection score: high >= 60, medium >= 40.
SEVERITY_STRENGTH = (("high", 75.0), ("medium", 55.0))
SEVERITY_SCORE = (("high", 60.0), ("medium", 40.0))

# "Limited repricing": a real inflection (score >= SCORE_MIN with at least 2 event types) whose stock, measured from the close of the first session AFTER the reaction window
# (availability session + 1) over the next REPRICE_SESSIONS sessions, has beaten its sector by no more than REPRICE_MAX_EXCESS. It becomes knowable only when that window has
# completed, so its availability date is the date of the window's last session.
LIMITED_REPRICING_PARAMS = {"score_min": 40.0, "min_event_types": 2, "reprice_sessions": 10, "max_excess_return": 0.03}

# Sectors whose statements do not follow the industrial income-statement shape: operating margin, operating leverage and free cash flow are not meaningful.
NON_STANDARD_SECTORS = ("Financial Services", "Real Estate")

DATA_QUALITY_OK, DATA_QUALITY_PARTIAL = "ok", "partial"


def severity(value: float, table) -> str:
    for name, floor in table:
        if value >= floor:
            return name
    return "low"
