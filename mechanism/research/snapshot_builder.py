"""`t0_v1` snapshot builder: turns a symbol's price history up to the session into the immutable T0 market state.

Values come from `stock_prices` via `price_features.compute_indicators` / `build_breakout_features` called
verbatim -- the same functions the training dataset and the universe guards use -- never from the screener's
signal dict or `technical_indicators`. So the snapshot cannot inherit the screener's silent defaults
(`rsi 50.0`, `volume_ratio 1.0`, `atr = price * 0.02`, sector 'Unknown') and cannot disagree with the training
features for the same bar.

Pure: no database, no clock, no I/O. Returns a `Snapshot` or a `SnapshotSkip` with a reason; never raises for
bad data, never fabricates a value. Missing is `None` (JSON null) plus a name in `missing_features`.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd

from ml_training.features import price_features as pf
from research import registry

OHLC = ("open", "high", "low", "close")


@dataclass(frozen=True)
class SnapshotSkip:
    symbol: str
    reason: str
    detail: str = ""


@dataclass
class Snapshot:
    symbol: str
    session_date: date
    feature_set_version: str
    bar_date: date
    open: float
    high: float
    low: float
    close: float
    volume: Optional[int]
    prev_close: Optional[float]
    bars_available: int
    features: Dict[str, Union[float, bool, None]]
    missing_features: List[str]
    sector: Optional[str]
    sector_source: Optional[str]
    sector_asof: Optional[date]
    manifest_hash: str
    code_ref: str
    content_hash: str = field(default="")

    @property
    def status(self) -> str:
        return "partial" if self.missing_features else "complete"


def _num(x) -> Optional[float]:
    """float, or None for NaN/inf/non-numeric -- JSON never sees NaN."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def canonical(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def content_hash(snapshot: "Snapshot") -> str:
    payload = {
        "symbol": snapshot.symbol, "session_date": snapshot.session_date, "fsv": snapshot.feature_set_version,
        "bar_date": snapshot.bar_date, "ohlc": [snapshot.open, snapshot.high, snapshot.low, snapshot.close],
        "volume": snapshot.volume, "prev_close": snapshot.prev_close, "bars": snapshot.bars_available,
        "features": snapshot.features, "missing": sorted(snapshot.missing_features),
        "sector": snapshot.sector, "sector_source": snapshot.sector_source, "sector_asof": snapshot.sector_asof,
        "manifest_hash": snapshot.manifest_hash,
    }
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def prepare_frame(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """stock_prices rows -> the ascending OHLCV frame `price_features` expects (rows without a close dropped,
    exactly as the universe guard does)."""
    px = pd.DataFrame(rows)
    for c in pf.OHLCV:
        px[c] = pd.to_numeric(px[c], errors="coerce")
    return px.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


def build_t0_v1(symbol: str, session_date: date, rows: List[Dict[str, Any]],
                sector: Optional[str] = None, sector_source: Optional[str] = None,
                sector_asof: Optional[date] = None) -> Union[Snapshot, SnapshotSkip]:
    """`rows`: this symbol's stock_prices rows with date <= session_date (dict per bar: date, open, high, low,
    close, volume). The newest bar MUST be the session's own bar."""
    if not rows:
        return SnapshotSkip(symbol, "no_price_history")
    px = prepare_frame(rows)
    if px.empty:
        return SnapshotSkip(symbol, "no_price_history", "no bar with a close")
    last = pd.Timestamp(px["date"].iloc[-1]).date()
    if last != session_date:
        return SnapshotSkip(symbol, "stale_bar", f"newest bar {last} != session {session_date}")

    n = len(px)
    pos = n - 1
    t0 = px.iloc[pos]
    if any(_num(t0[k]) is None for k in OHLC):
        return SnapshotSkip(symbol, "incomplete_bar", "T0 bar is missing an OHLC value")

    ind = pf.compute_indicators(px)
    # direction-independent block: direction +1 is arbitrary and every direction-dependent column is discarded
    frame = pf.build_breakout_features(ind, np.array([pos]), np.array([1]), sector=None)
    row = frame.iloc[0]
    features: Dict[str, Union[float, bool, None]] = {name: _num(row[name]) for name in registry.FEATURE_NAMES
                                                     if name in row.index}
    features["atr_14"] = _num(ind["atr"].iloc[pos])
    features["dollar_vol_20"] = _num(ind["dollar_vol_20"].iloc[pos])

    disc = pf.find_discontinuities(px)
    features[registry.DISCONTINUITY_FLAG] = bool(len(disc) and (disc["pos"] >= pos - pf.LOOKBACK_BARS).any())

    ordered = {name: features.get(name) for name in registry.FEATURE_NAMES}
    ordered[registry.DISCONTINUITY_FLAG] = features[registry.DISCONTINUITY_FLAG]
    missing = [name for name in registry.FEATURE_NAMES if ordered[name] is None]

    volume = _num(t0["volume"])
    if volume is None:
        missing.append("volume")
    prev_close = _num(px["close"].iloc[pos - 1]) if pos >= 1 else None
    if prev_close is None:
        missing.append("prev_close")
    if not sector:
        sector, sector_source, sector_asof = None, None, None
        missing.append("sector")

    snap = Snapshot(
        symbol=symbol, session_date=session_date, feature_set_version=registry.T0_V1, bar_date=last,
        open=float(t0["open"]), high=float(t0["high"]), low=float(t0["low"]), close=float(t0["close"]),
        volume=int(volume) if volume is not None else None, prev_close=prev_close, bars_available=n,
        features=ordered, missing_features=missing, sector=sector or None, sector_source=sector_source,
        sector_asof=sector_asof, manifest_hash=registry.current_manifest_hash(),
        code_ref=registry.code_ref())
    snap.content_hash = content_hash(snap)
    return snap
