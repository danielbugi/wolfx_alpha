# mechanism/shared/alpaca_client.py
"""
Alpaca Markets data client — alternative to yfinance for daily OHLCV bars.

Why this exists: yfinance scrapes Yahoo Finance's unofficial, undocumented
API, which changes without notice. A pinned yfinance version going stale
against that API is what caused a ~10-month pipeline outage (2025-11 to
2026-09 — every symbol came back "possibly delisted"). See CLAUDE.md and
MILESTONES.md for the full story.

Alpaca's Market Data API (https://alpaca.markets/data) is a real, supported,
documented API with a free tier that includes unlimited historical daily
bars (200 req/min, IEX feed) — no cost, no reverse-engineering risk. This
module is the swap-in replacement for the yfinance calls in
data_updaters/daily_data_updater.py.

Opt in via .env:
    DATA_PROVIDER=alpaca
    ALPACA_API_KEY=...
    ALPACA_API_SECRET=...
(free keys: https://alpaca.markets/ — sign up, no funding required for
market-data-only access)

Defaults to yfinance (DATA_PROVIDER unset) so this is completely inert
until explicitly enabled — existing behavior doesn't change until someone
sets these three env vars.
"""
import logging
from datetime import datetime, date, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    """Lazily create and cache the Alpaca client. Lazy so importing this
    module never fails/costs anything for installs that aren't using
    Alpaca — only touches alpaca-py and the API keys when actually called."""
    global _client
    if _client is None:
        from alpaca.data.historical import StockHistoricalDataClient
        from .config import config

        if not config.alpaca_api_key or not config.alpaca_api_secret:
            raise RuntimeError(
                "DATA_PROVIDER=alpaca but ALPACA_API_KEY / ALPACA_API_SECRET "
                "are not set in .env. Get free keys at https://alpaca.markets/ "
                "(market-data-only access needs no funded account)."
            )

        _client = StockHistoricalDataClient(config.alpaca_api_key, config.alpaca_api_secret)
        logger.info("Alpaca StockHistoricalDataClient initialized")

    return _client


def period_to_start_date(period: str) -> date:
    """
    Convert the yfinance-style period strings daily_data_updater.py already
    uses ('5d', '1mo', '3mo', '1y') into a start date, so its existing
    incremental-update logic (get_efficient_stock_data) can drive either
    provider without needing its own rewrite. A few extra days of buffer
    are added over the literal period so weekends/holidays don't leave a
    provider gap right at the boundary.
    """
    today = datetime.now().date()
    mapping = {
        '5d': timedelta(days=10),
        '1mo': timedelta(days=40),
        '3mo': timedelta(days=100),
        '1y': timedelta(days=400),
    }
    return today - mapping.get(period, timedelta(days=400))


def get_daily_bars(symbol: str, period: str = '1y') -> Optional[pd.DataFrame]:
    """
    Fetch daily OHLCV bars for one symbol from Alpaca.

    Returns the same shape daily_data_updater.py already expects from its
    yfinance path: columns ['date', 'open', 'high', 'low', 'close',
    'adj_close', 'volume'], with 'date' as a plain date (not datetime), so
    it's a drop-in replacement at the call site — no downstream code needs
    to change. Returns None on no-data/error, matching the yfinance path's
    contract (caller already handles None as "nothing to update").
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import Adjustment, DataFeed

    client = _get_client()
    start = period_to_start_date(period)

    try:
        request = StockBarsRequest(
            symbol_or_symbols=[symbol],
            timeframe=TimeFrame.Day,
            start=start,
            adjustment=Adjustment.SPLIT,  # split-adjusted, matches yfinance's adj_close
            feed=DataFeed.IEX,  # free-tier feed; only use SIP on a paid plan
        )
        bars = client.get_stock_bars(request)
        df = bars.df

        if df is None or df.empty:
            logger.warning(f"Alpaca: no data returned for {symbol}")
            return None

        # get_stock_bars returns a (symbol, timestamp) MultiIndex even for
        # a single-symbol request.
        if isinstance(df.index, pd.MultiIndex):
            if symbol not in df.index.get_level_values('symbol'):
                logger.warning(f"Alpaca: no data returned for {symbol}")
                return None
            df = df.xs(symbol, level='symbol')

        df = df.reset_index()
        df.rename(columns={'timestamp': 'date'}, inplace=True)
        df['date'] = pd.to_datetime(df['date']).dt.date
        df['adj_close'] = df['close']  # already split-adjusted via Adjustment.SPLIT

        return df[['date', 'open', 'high', 'low', 'close', 'adj_close', 'volume']]

    except Exception as e:
        logger.error(f"Alpaca: failed to get data for {symbol}: {e}")
        return None
