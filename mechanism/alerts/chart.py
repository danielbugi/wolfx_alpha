# mechanism/alerts/chart.py
"""
One stock chart as a PNG for the stock card: ~6 months of daily candles, volume, the prior 20-day high (the channel's breakout level)
and, when the user tracks the stock, their own reference price. Rendered locally from stock_prices - never a provider call.

Design (dataviz skill): the "Midnight Dawn" palette of market_card.py, already validated there (aqua up / coral down on the deep-navy
surface, colour-blind dE 14.5, contrast 5.9/5.1:1); direction is ALSO carried by candle position/shape, never colour alone. Two
overlays only, both DIRECTLY LABELLED at the right edge (no legend box); text wears the ink tokens, never a series colour; hairline
grid. The caption the bot sends with the image repeats the numbers, so the chart has a text alternative.

Economics: the bot renders a chart once per (symbol, session) and re-sends Telegram's file_id afterwards (bot_chart_cache); only a
tracked stock's personal chart (with the user's price line) is rendered per request, and those requests are rate-limited.
"""
from __future__ import annotations

import io
import threading
from datetime import date
from typing import Dict, List, Optional

import matplotlib

matplotlib.use("Agg")                                   # headless: never open a window (must precede the pyplot import)
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402

from alerts.market_card import DOWN, HAIRLINE, INK, INK2, MUTED, SURFACE, UP  # noqa: E402

MIN_BARS = 5
_LOCK = threading.Lock()                                # matplotlib's pyplot state is not thread-safe
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _frame(bars: List[Dict]) -> pd.DataFrame:
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)


def render_chart(symbol: str, bars: List[Dict], ref_price: Optional[float] = None, ref_date: Optional[date] = None,
                 ref_label: str = "your price") -> bytes:
    """bars: [{date, open, high, low, close, volume}, ...] oldest first. Returns PNG bytes; raises ValueError with too little data."""
    if len(bars) < MIN_BARS:
        raise ValueError("not enough price history for a chart")
    df = _frame(bars)
    donchian = df["High"].rolling(20).max().shift(1)             # the prior 20-day high: what "breakout" means in the channel
    overlays = []
    if donchian.notna().sum() >= 2:
        overlays.append(mpf.make_addplot(donchian, color=INK2, width=1.1, linestyle="--"))
    kwargs = {}
    if ref_price:
        kwargs["hlines"] = dict(hlines=[float(ref_price)], colors=[INK], linestyle="--", linewidths=1.0)
        if ref_date is not None:
            later = df.index[df.index >= pd.Timestamp(ref_date)]
            if len(later):                                        # a vertical marker on the day the stock was added
                kwargs["vlines"] = dict(vlines=[later[0]], colors=[MUTED], linestyle=":", linewidths=1.0)
    mc = mpf.make_marketcolors(up=UP, down=DOWN, edge="inherit", wick="inherit", volume={"up": UP, "down": DOWN}, ohlc="inherit")
    style = mpf.make_mpf_style(
        marketcolors=mc, facecolor=SURFACE, figcolor=SURFACE, edgecolor=HAIRLINE, gridcolor=HAIRLINE, gridstyle="-", gridaxis="horizontal",
        rc={"axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED, "font.size": 9, "axes.edgecolor": HAIRLINE})
    with _LOCK:
        if overlays:                                            # mplfinance rejects addplot=None (a new listing has no 20-day high yet)
            kwargs["addplot"] = overlays
        fig, axes = mpf.plot(df, type="candle", style=style, volume=True, figsize=(10.8, 6.4),
                             panel_ratios=(4, 1), datetime_format="%b %d", xrotation=0, tight_layout=False, returnfig=True,
                             show_nontrading=False, ylabel="", ylabel_lower=" ", **kwargs)
        try:
            ax = axes[0]
            for a in axes:                                        # mplfinance places its axes explicitly: set the frame ourselves
                a.xaxis.grid(False)                               # horizontal hairlines only
            left, width = 0.075, 0.755                            # leaves the right margin for the direct labels
            for a in axes[:2]:
                a.set_position([left, 0.30, width, 0.585])
            for a in axes[2:4]:
                a.set_position([left, 0.085, width, 0.20])
            last = df["Close"].iloc[-1]
            fig.text(0.07, 0.945, symbol, color=INK, fontsize=17, fontweight="bold", ha="left", va="center")
            fig.text(0.07, 0.905, f"daily candles · last close ${last:,.2f} · {df.index[-1]:%a %d %b %Y}", color=MUTED, fontsize=9.5,
                     ha="left", va="center")
            vol_ax = axes[2]                                      # volume panel: plain "20M" ticks instead of a raw 10^6 offset
            vol_ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos: f"{v / 1e6:.0f}M" if v >= 1e6 else f"{v / 1e3:.0f}K"))
            vol_ax.yaxis.offsetText.set_visible(False)
            vol_ax.set_ylabel("")                                # mplfinance's default 'Volume 10^6' label is replaced by the M/K ticks
            vol_ax.text(0.005, 0.86, "volume", transform=vol_ax.transAxes, color=MUTED, fontsize=8.5, va="center")
            if overlays:                                          # direct labels at the right edge instead of a legend
                y = float(donchian.dropna().iloc[-1])
                ax.text(1.005, y, "prior 20-day high", transform=ax.get_yaxis_transform(), color=INK2, fontsize=8.5, va="center")
            if ref_price:
                ax.text(1.005, float(ref_price), ref_label, transform=ax.get_yaxis_transform(), color=INK, fontsize=8.5, va="center")
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=100, facecolor=SURFACE)
        finally:
            plt.close(fig)
    return buf.getvalue()
