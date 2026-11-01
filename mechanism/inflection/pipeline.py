"""Composition: one symbol's quarterly fundamentals + earnings dates + prices + valuation -> feature records and event records, point in time. PURE (numpy).

`asof` is the date the run is made for. A quarter is only used if its figures were public by then (`available_at <= asof`); the composite "limited repricing" event is only produced once its
repricing window has completed by `asof`. Run with a historical `asof` it reproduces exactly what the engine would have said that day (the backtest uses this).
"""
from __future__ import annotations

import bisect
import hashlib
import json
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from inflection import config as C
from inflection import context as X
from inflection import events as E
from inflection import features as F


def _canon(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, dict):
        return {k: _canon(x) for k, x in sorted(v.items())}
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    if isinstance(v, date):
        return v.isoformat()
    return v


def event_hash(ev: Mapping[str, Any]) -> str:
    """Identity of an event's CONTENT (what makes it a revision when a restated quarter changes it); market context is excluded because it changes every day."""
    doc = {k: ev[k] for k in ("symbol", "quarter_end", "event_type", "method_version", "strength", "metrics", "available_at", "availability_basis")}
    return hashlib.sha256(json.dumps(_canon(doc), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def resolve_availability(quarter_end: date, report_dates: Sequence[date], updated_at: Optional[str]) -> Tuple[date, str]:
    d, basis = F.availability(quarter_end, report_dates)
    if basis != "earnings_calendar" and updated_at:
        try:
            seen = date.fromisoformat(str(updated_at)[:10])
        except ValueError:
            seen = None
        if seen is not None and seen < d:
            return seen, "first_seen"
    return d, basis


def build_symbol(symbol: str, qrows: Sequence[Mapping[str, Any]], report_dates: Sequence[date], sector: Optional[str], prices: Optional[X.Prices], sector_index: Optional[X.SectorIndex],
                 valuation: Optional[X.ValuationStore], asof: date, method_version: str = C.METHOD_VERSION) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    quarters = [F.quarter_from_row(r) for r in qrows]
    feats = F.compute_features(quarters, sector)
    feature_records: List[Dict[str, Any]] = []
    event_records: List[Dict[str, Any]] = []
    for ft in feats:
        avail, basis = resolve_availability(ft["quarter"], report_dates, ft["updated_at"])
        if ft.get("accepted_at") is not None:           # SEC-sourced quarter: the filing's acceptance date IS the point-in-time availability
            avail, basis = ft["accepted_at"], ft.get("accepted_kind") or "sec_acceptance"
        _emit(symbol, ft, avail, basis, sector, prices, sector_index, valuation, asof, method_version, feature_records, event_records)
    return feature_records, event_records


def build_symbol_pit(symbol: str, items: Sequence[Mapping[str, Any]], sector: Optional[str], prices: Optional[X.Prices], sector_index: Optional[X.SectorIndex],
                     valuation: Optional[X.ValuationStore], asof: date, method_version: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Filing-version-aware build: `items` come from pit.pit_items (one per company-quarter: the quarter's features computed ONLY from figures public at its first-public date, with
    provenance notes). No later restatement can change them, so a re-run on unchanged filings changes nothing."""
    feature_records: List[Dict[str, Any]] = []
    event_records: List[Dict[str, Any]] = []
    for it in items:
        ft = it["feature"]
        if it["available_at"] > asof:
            continue
        _emit(symbol, ft, it["available_at"], it["availability_basis"], sector, prices, sector_index, valuation, asof, method_version, feature_records, event_records, source=it["source"])
    return feature_records, event_records


def _emit(symbol, ft, avail, basis, sector, prices, sector_index, valuation, asof, method_version, feature_records, event_records, source: Optional[str] = None) -> None:
    if avail > asof:
        return
    m = ft["metrics"]
    base = E.detect_base_events(m, sector)
    sc = E.score(base, m)
    common = {"symbol": symbol, "quarter_end": ft["quarter"], "method_version": method_version, "fiscal_year": ft["fiscal_year"], "fiscal_quarter": ft["fiscal_quarter"],
              "source": source or (C.SOURCE_SEC if ft.get("accepted_at") is not None else C.SOURCE_QUARTERLY), "source_updated_at": ft["updated_at"], "data_quality": ft["quality"],
              "quality_notes": ft["notes"]}
    feature_records.append({**common, "available_at": avail, "availability_basis": basis, "metrics": m, "score": sc["score"], "components": sc["components"]})

    ctx: Dict[str, Any] = {}
    if prices is not None:
        ctx["reaction"] = X.reaction(prices, avail)
        win_since = _since(prices, avail, asof)
        ctx["excess_vs_sector_since_publication"] = X.excess_return(prices, sector_index, sector, *win_since) if win_since else None
    if valuation is not None:
        ctx["valuation_at_publication"] = valuation.context(symbol, avail, sector)
        ctx["valuation_latest"] = valuation.context(symbol, asof, sector)
    for e in base:
        rec = {**common, **e, "score": sc["score"], "components": sc["components"], "available_at": avail, "availability_basis": basis, "context": ctx}
        rec["event_hash"] = event_hash(rec)
        event_records.append(rec)

    # the composite event: needs the repricing window, so it becomes known only when that window has completed
    if prices is not None and sector_index is not None and base:
        p = C.LIMITED_REPRICING_PARAMS
        win = X.repricing_window(prices, avail, p["reprice_sessions"])
        if win is not None and win[1] <= asof:
            ex = X.excess_return(prices, sector_index, sector, win[0], win[1])
            comp = E.limited_repricing(sc, m, ex, base)
            if comp is not None:
                rec = {**common, **comp, "score": sc["score"], "components": sc["components"], "available_at": win[1], "availability_basis": f"repricing_window_end({basis})", "context": ctx}
                rec["event_hash"] = event_hash(rec)
                event_records.append(rec)


def _since(p: X.Prices, avail: date, asof: date) -> Optional[Tuple[date, date]]:
    """Window from the close of the session after the reaction window to the last session on or before `asof` (None when there is less than a session of it)."""
    D = X.first_session_on_or_after(p.dates, avail)
    if D is None or D + 2 >= len(p.dates):
        return None
    start = p.dates[D + 2]
    end_i = bisect.bisect_right(p.dates, asof) - 1
    if end_i < 0 or p.dates[end_i] <= start:
        return None
    return start, p.dates[end_i]
