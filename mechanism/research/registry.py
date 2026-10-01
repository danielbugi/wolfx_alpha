"""Feature-set registry: what `t0_v1` IS, proven by hash before anything is written under it.

The manifest below is the definition. Its canonical-JSON sha256 is stored in `feature_set_registry` the first
time the version is used; every later capture run recomputes the hash from this code and REFUSES to write if it
differs from the stored one. A silent redefinition (a renamed feature, a changed constant, a different
integrity window) therefore cannot mix two definitions under one `feature_set_version`: change the manifest ->
register a new version (`t0_v2`, `extends='t0_v1'`), never edit `t0_v1`.
"""
from __future__ import annotations

import functools
import hashlib
import inspect
import json
from typing import Any, Dict, List

from psycopg2.extras import Json

T0_V1 = "t0_v1"


class ManifestMismatch(RuntimeError):
    """The code's manifest hash differs from the one registered for this version."""


# (name, kind, unit, min_bars, definition). Order is part of the manifest. `kind`: raw_column = typed column on
# feature_snapshot, feature = key in feature_snapshot.features, meta = typed provenance column.
_RAW = [
    ("bar_date", "raw_column", "date", "date of the symbol's own T0 bar; equals session_date"),
    ("open", "raw_column", "price", "T0 open, as known at capture"),
    ("high", "raw_column", "price", "T0 high"),
    ("low", "raw_column", "price", "T0 low"),
    ("close", "raw_column", "price", "T0 close"),
    ("volume", "raw_column", "shares", "T0 volume; null if the vendor bar had none"),
    ("prev_close", "raw_column", "price", "close of the previous bar in stock_prices; null if only one bar"),
    ("bars_available", "raw_column", "count", "number of bars <= T0 for the symbol"),
]

_FEATURES = [
    ("atr_14", "price", 15, "Wilder ATR(14) at T0 (price_features.compute_indicators.atr)"),
    ("atr_pct", "percent", 15, "atr_14 / close * 100"),
    ("rsi_14", "index", 15, "Wilder RSI(14)"),
    ("chan_width_pct", "percent", 21, "(20-bar high - 20-bar low at T0-1) / close(T0-1) * 100"),
    ("chan_squeeze_pctile", "percentile", 41, "percentile rank of chan_width_pct over 120 bars (min 20)"),
    ("vol_ratio_10", "ratio", 10, "volume / SMA10(volume)"),
    ("vol_ratio_50", "ratio", 50, "volume / SMA50(volume)"),
    ("dollar_vol_20", "usd", 20, "mean(close*volume) over the 20 bars ending at T0, raw"),
    ("log_dollar_vol_20", "log10_usd", 20, "log10(dollar_vol_20); null when <= 0"),
    ("ret_5d", "percent", 6, "(close / close[-5] - 1) * 100"),
    ("ret_20d", "percent", 21, "(close / close[-20] - 1) * 100"),
    ("ret_60d", "percent", 61, "(close / close[-60] - 1) * 100"),
    ("sma10_vs_20", "percent", 20, "(SMA10 / SMA20 - 1) * 100"),
    ("dist_sma50_pct", "percent", 50, "(close / SMA50 - 1) * 100"),
    ("dist_sma200_pct", "percent", 200, "(close / SMA200 - 1) * 100"),
    ("macd_norm", "percent", 26, "(EMA12 - EMA26) / close * 100, min_periods=26 on both"),
    ("bollinger_pos", "percent", 20, "(close - (SMA20 - 2 sd)) / (4 sd) * 100; null when sd = 0"),
    ("gap_pct", "percent", 2, "(open / prev_close - 1) * 100"),
    ("bar_range_atr", "ratio", 15, "(high - low) / atr_14"),
    ("close_location", "fraction", 1, "(close - low) / (high - low); null when the range is 0"),
    ("pct_from_52w_high", "percent", 126, "(close / max(high, 252 bars, min 126) - 1) * 100"),
    ("pct_from_52w_low", "percent", 126, "(close / min(low, 252 bars, min 126) - 1) * 100"),
]

FEATURE_NAMES: List[str] = [f[0] for f in _FEATURES]
RAW_COLUMN_NAMES: List[str] = [r[0] for r in _RAW]
DISCONTINUITY_FLAG = "has_discontinuity_253"


def _params() -> Dict[str, Any]:
    # Constants that change what the values MEAN. Imported lazily so a missing ml_training package is a clear
    # error at use, not at import. They are part of the hash on purpose.
    from ml_training.features import price_features as pf
    return {
        "source_table": "stock_prices",
        "indicator_impl": "ml_training.features.price_features.compute_indicators",
        "discontinuity_lookback_bars": pf.LOOKBACK_BARS,
        "max_day_up_ratio": pf.MAX_DAY_UP_RATIO,
        "max_day_down_ratio": pf.MAX_DAY_DOWN_RATIO,
        "missing_semantics": "null + name in missing_features; never 0, never a sentinel",
    }


def build_manifest() -> Dict[str, Any]:
    return {
        "feature_set_version": T0_V1,
        "raw_columns": [{"name": n, "kind": k, "unit": u, "definition": d} for n, k, u, d in _RAW],
        "features": [{"name": n, "kind": "feature", "unit": u, "min_bars": b, "definition": d}
                     for n, u, b, d in _FEATURES]
                    + [{"name": DISCONTINUITY_FLAG, "kind": "feature", "unit": "bool", "min_bars": 0,
                        "definition": "find_discontinuities() hit within the last 253 bars; never null"}],
        "provenance_columns": ["sector", "sector_source", "sector_asof"],
        "params": _params(),
    }


def canonical_json(manifest: Dict[str, Any]) -> str:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False)


def manifest_hash(manifest: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


@functools.lru_cache(maxsize=None)
def current_manifest_hash() -> str:
    return manifest_hash(build_manifest())


@functools.lru_cache(maxsize=None)
def impl_fingerprint() -> str:
    """sha256 (12 hex) of the source of the code that turns bars into a snapshot -- a real, reproducible
    identity of the implementation. There is no git SHA available inside the container, and none is invented."""
    from ml_training.features import price_features as pf
    from research import snapshot_builder
    parts = [inspect.getsource(pf.compute_indicators), inspect.getsource(pf.build_breakout_features),
             inspect.getsource(pf.find_discontinuities), inspect.getsource(snapshot_builder)]
    return hashlib.sha256("\n".join(parts).replace("\r\n", "\n").encode("utf-8")).hexdigest()[:12]


def code_ref() -> str:
    return f"{T0_V1}:impl={impl_fingerprint()}"


def ensure_registered(conn, version: str = T0_V1) -> str:
    """Register `t0_v1` if absent, else prove the stored hash equals this code's hash. Returns the hash.
    Runs inside the caller's transaction (the caller commits). Raises ManifestMismatch on drift."""
    if version != T0_V1:
        raise ValueError(f"unknown feature_set_version {version!r}: only {T0_V1} is implemented")
    manifest = build_manifest()
    mhash = manifest_hash(manifest)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO feature_set_registry (feature_set_version, manifest, manifest_hash, impl_ref, description) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (feature_set_version) DO NOTHING",
        (version, Json(manifest, dumps=canonical_json), mhash, code_ref(),
         "T0 price-structure snapshot: raw bar + 22 causal derived features + discontinuity flag + sector"))
    cur.execute("SELECT manifest_hash FROM feature_set_registry WHERE feature_set_version = %s", (version,))
    stored = cur.fetchone()[0].strip()
    if stored != mhash:
        raise ManifestMismatch(
            f"{version}: code manifest hash {mhash[:12]} != registered {stored[:12]} -- refusing to capture; "
            f"a changed definition needs a new feature_set_version, never an edit of {version}")
    return mhash
