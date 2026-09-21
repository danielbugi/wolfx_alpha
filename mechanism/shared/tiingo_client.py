# mechanism/shared/tiingo_client.py
"""
Tiingo data client — replacement for both yfinance's price history AND its
fundamentals (ticker.info) calls.

Why this exists: yfinance scrapes Yahoo Finance's unofficial API (see
CLAUDE.md / MILESTONES.md for the ~10-month outage that caused). Alpaca's
free tier (shared/alpaca_client.py) fixed reliability for prices but its
free IEX-only feed has real coverage gaps (~38/1005 symbols return empty —
see the comment in daily_data_updater.py's get_efficient_stock_data). Tiingo
is a paid ($30/mo Power plan), documented API with a consolidated (not
IEX-only) feed and 10,000 req/hour — enough headroom for a ~3,000-symbol
Russell 3000 universe — and it bundles core fundamentals + sector/industry
at the same tier, so it can replace BOTH daily_data_updater.py's price
fetch and fundamentals_updater.py's yf.Ticker(...).info fetch with one
vendor instead of two different Yahoo Finance scrapers.

Opt in via .env:
    DATA_PROVIDER=tiingo
    TIINGO_API_KEY=...
(paid plan required for this pipeline's volume — the free tier caps at 500
symbols/month. See CLAUDE.md §6a for the full vendor comparison.)

Defaults to yfinance (DATA_PROVIDER unset) so this is inert until someone
opts in, same as alpaca_client.py.

Known field gap vs. yfinance's ticker.info: Tiingo's Fundamentals API does
not expose beta, dividendYield, sharesOutstanding, or floatShares — its
daily-metrics endpoint only returns marketCap/enterpriseVal/peRatio/
pbRatio/trailingPEG1Y, and sector/industry come from a separate /meta
endpoint. get_fundamentals() returns None for those four fields.
fundamentals_updater.py's calculate_*_score methods already treat missing
fields as reduced-but-valid inputs (additive scoring with fallback
branches), so this degrades score precision rather than breaking anything.

**MUCH BIGGER GAP, found 2026-09-17 by calling the live API (not
documented anywhere in Tiingo's docs or marketing pages):** the Fundamentals
API -- /daily, /statements, AND /meta's sector+industry fields -- is
restricted to the **Dow 30 only** on the Free and Power ($30/mo) plans.
Every other symbol gets HTTP 400 with
`{"detail": "Error: Free and Power plans are limited to the DOW 30. ..."}`
on /daily and /statements, and /meta returns sector/industry as the literal
string "Field not available for free/evaluation" instead of real data.
Getting real fundamentals coverage across the full universe needs Tiingo's
separate paid Fundamental Data API add-on (price unknown -- requires
emailing support@tiingo.com) or a different vendor entirely (yfinance, or
Financial Modeling Prep per CLAUDE.md §6a's original candidate).

Practical effect: get_fundamentals() and get_quarterly_statements() return
None/[] for ~99% of a Russell-3000-sized universe, and
fundamentals_updater.py / quarterly_fundamentals_updater.py's existing
Tiingo-then-yfinance-fallback logic means this DOESN'T break anything --
those ~2,970 non-Dow-30 symbols just silently keep using yfinance for
fundamentals, exactly as before this migration. But it does mean "Tiingo
replaces yfinance for fundamentals" is NOT true at this plan tier --
only prices are actually migrated at scale. The Piotroski F-Score bonus
(see get_quarterly_statements) is real but only populates for Dow 30 names.
"""
import logging
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.tiingo.com"


def _get_api_key() -> str:
    from .config import config

    if not config.tiingo_api_key:
        raise RuntimeError(
            "DATA_PROVIDER=tiingo but TIINGO_API_KEY is not set in .env. "
            "Get a key at https://www.tiingo.com/ (Power plan, $30/mo — "
            "needed for the request volume this pipeline uses; the free "
            "tier caps at 500 symbols/month)."
        )
    return config.tiingo_api_key


def _normalize_symbol(symbol: str) -> str:
    """
    Tiingo uses a hyphen for share classes (BRK-B, BF-B); the rest of this
    pipeline (and the source index lists) carry the NYSE/Wikipedia
    convention of a dot or slash instead (BRK.B, BRK/A, BF.B). Confirmed
    2026-09-18: querying Tiingo with the dot/slash form returns a clean 404
    ("Ticker not found") rather than any kind of redirect, so these
    silently dropped out of the Russell 3000 price backfill (4 symbols:
    BRK.B, BF.B, BRK/A, BRK/B) until this normalization was added.
    """
    return symbol.replace('.', '-').replace('/', '-')


def period_to_start_date(period: str) -> date:
    """
    Same mapping as alpaca_client.py's — kept identical so either provider
    drives daily_data_updater.py's existing incremental-update logic
    (get_efficient_stock_data) without it needing to know which is active.
    """
    today = datetime.now().date()
    mapping = {
        '5d': timedelta(days=10),
        '1mo': timedelta(days=40),
        '3mo': timedelta(days=100),
        '1y': timedelta(days=400),
    }
    return today - mapping.get(period, timedelta(days=400))


def get_daily_bars(symbol: str, period: str = '1y', start_date: Optional[date] = None) -> Optional[pd.DataFrame]:
    """
    Fetch daily OHLCV bars for one symbol from Tiingo.

    Returns the same shape daily_data_updater.py already expects from its
    yfinance/Alpaca paths: columns ['date', 'open', 'high', 'low', 'close',
    'adj_close', 'volume'] — a drop-in replacement at the call site. Uses
    Tiingo's split-and-dividend-adjusted series (adjOpen/adjHigh/adjLow/
    adjClose/adjVolume) for all of open/high/low/close/volume, matching how
    the yfinance and Alpaca paths both already set adj_close == close (see
    their respective comments). Returns None on no-data/error.

    `start_date`, when given, overrides the `period`-derived start date --
    used by repopulate_from_tiingo.py for a one-time multi-year history
    pull, where none of the short incremental-update periods apply.
    """
    start = start_date or period_to_start_date(period)
    url = f"{_BASE_URL}/tiingo/daily/{_normalize_symbol(symbol)}/prices"
    params = {
        'token': _get_api_key(),
        'startDate': start.isoformat(),
        'format': 'json',
    }

    try:
        resp = requests.get(url, params=params, timeout=20)
        if resp.status_code == 404:
            logger.warning(f"Tiingo: no data returned for {symbol}")
            return None
        resp.raise_for_status()
        rows = resp.json()

        if not rows:
            logger.warning(f"Tiingo: no data returned for {symbol}")
            return None

        raw = pd.DataFrame(rows)
        df = pd.DataFrame({
            'date': pd.to_datetime(raw['date']).dt.date,
            'open': raw['adjOpen'],
            'high': raw['adjHigh'],
            'low': raw['adjLow'],
            'close': raw['adjClose'],
            'adj_close': raw['adjClose'],
            'volume': raw['adjVolume'],
        })

        return df

    except Exception as e:
        logger.error(f"Tiingo: failed to get data for {symbol}: {e}")
        return None


def get_fundamentals(symbol: str) -> Optional[Dict[str, Any]]:
    """
    Fetch the fields fundamentals_updater.py's fetch_company_info() needs,
    from Tiingo's Fundamentals API (daily metrics + meta) instead of
    yf.Ticker(symbol).info.

    See module docstring for the field gap: shares_outstanding,
    float_shares, ps_ratio, beta, and dividend_yield come back None.
    """
    api_key = _get_api_key()
    tiingo_symbol = _normalize_symbol(symbol)

    try:
        daily_resp = requests.get(
            f"{_BASE_URL}/tiingo/fundamentals/{tiingo_symbol}/daily",
            params={'token': api_key, 'format': 'json'},
            timeout=20,
        )
        if daily_resp.status_code == 404:
            logger.warning(f"Tiingo: no fundamentals data for {symbol}")
            return None
        if daily_resp.status_code == 400:
            # Expected for ~99% of the universe on the Power plan -- see
            # module docstring's "MUCH BIGGER GAP" note. Quiet by design;
            # the caller's yfinance fallback handles this symbol instead.
            logger.debug(f"Tiingo: {symbol} not covered by Fundamentals API on this plan (Dow-30-only)")
            return None
        daily_resp.raise_for_status()
        daily_rows = daily_resp.json()

        if not daily_rows:
            logger.warning(f"Tiingo: no fundamentals data for {symbol}")
            return None

        latest = daily_rows[-1]  # ascending by date

        meta = {}
        try:
            meta_resp = requests.get(
                f"{_BASE_URL}/tiingo/fundamentals/meta",
                params={'token': api_key, 'tickers': tiingo_symbol, 'format': 'json'},
                timeout=20,
            )
            meta_resp.raise_for_status()
            meta_rows = meta_resp.json()
            if meta_rows:
                meta = meta_rows[0]
                # On the Power plan, non-Dow-30 tickers get this literal
                # placeholder string instead of real sector/industry data
                # (see module docstring) -- null it out rather than storing
                # it as if it were a real classification.
                if meta.get('sector') == 'Field not available for free/evaluation':
                    meta['sector'] = None
                if meta.get('industry') == 'Field not available for free/evaluation':
                    meta['industry'] = None
        except Exception as e:
            # Sector/industry are nice-to-have, not worth failing the whole
            # fundamentals fetch over.
            logger.warning(f"Tiingo: failed to get meta for {symbol}: {e}")

        return {
            'symbol': symbol,
            'market_cap': latest.get('marketCap'),
            'shares_outstanding': None,  # not exposed by Tiingo Fundamentals API
            'float_shares': None,        # not exposed by Tiingo Fundamentals API
            'pe_ratio': latest.get('peRatio'),
            'pb_ratio': latest.get('pbRatio'),
            'ps_ratio': None,            # not exposed by Tiingo Fundamentals API
            'peg_ratio': latest.get('trailingPEG1Y'),
            'beta': None,                # not exposed by Tiingo Fundamentals API
            'dividend_yield': None,      # not exposed by Tiingo Fundamentals API
            'sector': meta.get('sector'),
            'industry': meta.get('industry'),
            'last_updated': datetime.now(),
        }

    except Exception as e:
        logger.error(f"Tiingo: failed to get fundamentals for {symbol}: {e}")
        return None


# Tiingo statements dataCode -> the raw-line-item keys
# quarterly_fundamentals_updater.py already builds from yfinance's
# ticker.quarterly_income_stmt / _balance_sheet / _cashflow. Confirmed
# 2026-09-17 against the live API for AAPL (Tiingo's own /statements docs
# don't enumerate dataCodes, so this was verified empirically, not just
# read off a doc page).
_STATEMENT_FIELD_MAP = {
    'revenue': ('incomeStatement', 'revenue'),
    'gross_profit': ('incomeStatement', 'grossProfit'),
    'operating_income': ('incomeStatement', 'opinc'),
    'net_income': ('incomeStatement', 'netinc'),
    'ebitda': ('incomeStatement', 'ebitda'),
    'eps': ('incomeStatement', 'epsDil'),
    'total_assets': ('balanceSheet', 'totalAssets'),
    'total_debt': ('balanceSheet', 'debt'),
    'total_equity': ('balanceSheet', 'equity'),
    'current_assets': ('balanceSheet', 'assetsCurrent'),
    'current_liabilities': ('balanceSheet', 'liabilitiesCurrent'),
    'cash': ('balanceSheet', 'cashAndEq'),
    'operating_cash_flow': ('cashFlow', 'ncfo'),
    'free_cash_flow': ('cashFlow', 'freeCashFlow'),
    'capital_expenditures': ('cashFlow', 'capex'),
    # Bonus over yfinance: Tiingo's overview section includes a ready-made
    # Piotroski F-Score (0-9), a well-established fundamental-turnaround
    # signal -- directly relevant to catching "loss-to-profit" names. Not
    # in the yfinance path at all, so it's None on that fallback.
    'piotroski_f_score': ('overview', 'piotroskiFScore'),
}


def get_quarterly_statements(symbol: str) -> List[Dict[str, Any]]:
    """
    Fetch trailing quarterly income statement / balance sheet / cash flow
    line items from Tiingo, in the same raw-field shape
    quarterly_fundamentals_updater.py's fetch_quarterly_rows() already
    builds from yfinance -- before that method's own margin/ratio/
    YoY-growth computation, which stays provider-agnostic and unchanged
    (this function only supplies the raw inputs to it). Returns [] on
    no-data/error, matching that method's "nothing usable" contract.

    Rows come back newest-first (by 'quarter' date), matching yfinance's
    column order, since fetch_quarterly_rows()'s YoY calc indexes 4
    positions back assuming that order.
    """
    api_key = _get_api_key()

    try:
        resp = requests.get(
            f"{_BASE_URL}/tiingo/fundamentals/{_normalize_symbol(symbol)}/statements",
            params={'token': api_key, 'format': 'json'},
            timeout=30,
        )
        if resp.status_code == 404:
            logger.warning(f"Tiingo: no statements data for {symbol}")
            return []
        if resp.status_code == 400:
            # Expected for ~99% of the universe on the Power plan -- see
            # module docstring's "MUCH BIGGER GAP" note. Quiet by design;
            # the caller's yfinance fallback handles this symbol instead.
            logger.debug(f"Tiingo: {symbol} not covered by Fundamentals API on this plan (Dow-30-only)")
            return []
        resp.raise_for_status()
        periods = resp.json()

        # quarter == 0 marks an annual/TTM period in Tiingo's response --
        # only true quarters are wanted here.
        quarterly = [p for p in periods if p.get('quarter')]

        def pick(statement_data: Dict[str, Any], section: str, code: str) -> Optional[float]:
            for item in statement_data.get(section, []):
                if item.get('dataCode') == code:
                    return item.get('value')
            return None

        rows = []
        for period in quarterly:
            data = period.get('statementData', {})
            values = {
                key: pick(data, section, code)
                for key, (section, code) in _STATEMENT_FIELD_MAP.items()
            }

            if values['revenue'] is None and values['net_income'] is None:
                continue  # nothing usable for this period

            rows.append({
                'symbol': symbol,
                'quarter': pd.to_datetime(period['date']).date(),
                'fiscal_year': period['year'],
                'fiscal_quarter': period['quarter'],
                **values,
            })

        rows.sort(key=lambda r: r['quarter'], reverse=True)
        return rows

    except Exception as e:
        logger.error(f"Tiingo: failed to get statements for {symbol}: {e}")
        return []
