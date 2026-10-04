"""Typed per-stock relative-strength rows (pure): the `rs_v1` `.stocks` frame as the exact records migration 29 stores.

One record per (symbol, horizon) for every stock in the session's universe -- including the ones whose measurement is unavailable, which are
stored as `state='unavailable'` with NULLs, never as zeros and never omitted (an omitted row cannot be told from a stock that was not in the
universe). The model's own definitions (see `relative_strength.py`) are copied, not recomputed:

    ret_pct        (C_t / C_{t-h} - 1) * 100
    vs_spx_pp      ret_pct - ^GSPC return, percentage points
    vs_sector_pp   ret_pct - sector median return, percentage points (NULL without a sector or with fewer than MIN_SECTOR_MEMBERS valid members)
    rs_percentile  0..100 within the valid universe (NULL when fewer than MIN_UNIVERSE valid stocks)

A different definition is a new `model_version` and therefore new rows; rows are never rewritten.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Optional

from market_intelligence.relative_strength import SPX, RelativeStrength

ROW_SCHEMA = "stock_rs_row_v1"


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _sector(v: Any) -> Optional[str]:
    return v if isinstance(v, str) and v else None


def stock_rs_records(rs: RelativeStrength) -> List[Dict[str, Any]]:
    """Records ordered by (symbol, horizon) -- deterministic, so `records_hash` is reproducible. Empty when the session had no real universe."""
    if rs.stocks is None or len(rs.stocks) == 0:
        return []
    out: List[Dict[str, Any]] = []
    pit_safe_map = bool((rs.coverage.get("sector_map") or {}).get("pit_safe"))
    for sym in sorted(rs.stocks.index):
        row = rs.stocks.loc[sym]
        sector = _sector(row.get("sector"))
        for h in sorted(rs.horizons):
            ret = _num(row.get(f"ret_{h}"))
            if ret is None:
                vs_spx = vs_sec = pct = None
            else:
                vs_spx = _num(row.get(f"vs_spx_{h}"))
                vs_sec = _num(row.get(f"vs_sector_{h}")) if sector is not None else None
                pct = _num(row.get(f"rs_pctile_{h}"))
            out.append({
                "symbol": str(sym), "horizon_sessions": int(h), "sector": sector, "sector_pit_safe": bool(pit_safe_map and sector is not None),
                "state": "ok" if ret is not None else "unavailable", "ret_pct": ret, "vs_spx_pp": vs_spx, "vs_sector_pp": vs_sec,
                "rs_percentile": pct, "n_universe_valid": int((rs.universe.get(h) or {}).get("n_valid") or 0), "benchmark_symbol": SPX})
    return out


def records_hash(records: List[Dict[str, Any]], session_date: str, model_version: str, feature_set_version: str, provenance: str) -> str:
    """Hash of the whole session batch (stored on every row as run_content_hash)."""
    doc = {"schema": ROW_SCHEMA, "session_date": session_date, "model_version": model_version, "feature_set_version": feature_set_version,
           "provenance": provenance, "records": records}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
