"""Declared configuration of the research missions: domains, sources, models, prices and limits. Changing a price or a limit is a reviewed
code change; the environment can only LOWER the limits (see budget.limits_from_env)."""
from __future__ import annotations

from typing import Dict, Tuple

# ------------------------------------------------------------------------------------------------------------------------------------------------ domains and sources
DOMAINS: Dict[str, Dict] = {
    "ai_semiconductors": {
        "label": "AI & Semiconductors",
        # yfinance-style industry labels in daily_fundamentals that make up the candidate universe of the domain
        "industries": ["Semiconductors", "Semiconductor Equipment & Materials", "Electronic Components", "Computer Hardware"],
        "sources": ["first_light_db", "news_items", "gdelt", "tiingo_news"],
    },
    "general": {
        "label": "General (company deep-dive outside a configured domain)",
        "industries": [],
        "sources": ["first_light_db", "news_items", "gdelt", "tiingo_news"],
    },
}

# Every source the router may use. 'license' is stored on each document; internal_only content is never redistributed.
SOURCES: Dict[str, Dict] = {
    "first_light_db": {"license": "internal_only", "paid": False, "note": "our own prices, sectors, SEC point-in-time facts and inflection events"},
    "news_items": {"license": "internal_only", "paid": False, "note": "existing headline feed (Benzinga via the news pipeline): headline, URL, date only"},
    "gdelt": {"license": "public", "paid": False, "note": "GDELT DOC 2.0 article metadata (title, URL, date, domain); approved 2026-11-05"},
    "tiingo_news": {"license": "internal_only", "paid": False, "note": "Tiingo News: internal research use only (licence); approved 2026-11-05"},
}

# ------------------------------------------------------------------------------------------------------------------------------------------------ models and calls
MODELS: Dict[str, str] = {"planner": "claude-opus-5-5", "analyst": "claude-sonnet-5-5", "verifier": "claude-sonnet-5-5"}
EFFORT: Dict[str, str] = {"planner": "medium", "analyst": "medium", "verifier": "low"}
MAX_TOKENS: Dict[str, int] = {"planner": 8000, "analyst": 24000, "verifier": 12000}   # thinking counts as output and is inside this cap

# Server-side refusal fallback: on a policy decline the API re-runs the request on another model inside the same call. The fallback model
# is chosen by the API, so its price is unknown in advance: reservations assume the CEILING price for a second full call.
USE_FALLBACKS = True
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# List prices, USD per million tokens: (input, output, cache read, cache write 5 min). Claude API reference cached 2026-10-06.
PRICES: Dict[str, Tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.50, 2.50, 0.05, 0.625),     # the >100K-token prompt tier, the conservative one
}
CEILING_PRICE: Tuple[float, float, float, float] = (10.00, 50.00, 1.00, 12.50)   # the highest list price of any current model (Fable 5.1)

# ------------------------------------------------------------------------------------------------------------------------------------------------ hard limits
MONTHLY_BUDGET_USD = 150.0        # owner decision 2026-11-05
MISSION_BUDGET_USD = 5.0          # default per mission (a mission row may carry a lower one)
MAX_CALL_USD = 2.0                # no single call may reserve more than this
CHARS_PER_TOKEN_ESTIMATE = 3.0    # conservative (English runs ~4 characters per token): over-estimates input cost

# ------------------------------------------------------------------------------------------------------------------------------------------------ evidence sizing (stated in every prompt and report)
MAX_EVIDENCE_ITEMS = 80
EXCERPT_CHARS = 300
MAX_PLAN_QUERIES = 5
MAX_CANDIDATES = 20
GDELT_PER_QUERY = 15
NEWS_LOOKBACK_DAYS = 120
