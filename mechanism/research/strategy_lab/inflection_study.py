"""Does a fundamental inflection event precede sector-adjusted excess returns? A reproducible EVENT STUDY of the Fundamental Inflection Engine, registered as the strategy family
`fundamental_inflection`. Research only; numpy + the inflection package; no database, clock or network (the caller hands in the data).

Declared before any return was computed (see SPEC): the events, the entry convention (the open of the session after the market's two-session reaction window), the measure (forward
return of the stock minus its sector's, net of a 20 bps round-trip cost, at 5 / 20 / 60 / 120 sessions), the split by the date the figures were public, the baselines, the sample-size
requirements and the decision rule. The primary cell decides; every other cell is context, with intervals widened for the number of cells looked at.

Point in time: an event is used only from the date its figures were public (`available_at`, or the end of the repricing window for the composite event); every feature of it uses data up to
that date; the outcome window starts after the signal session. Horizons that the data cannot yet complete are reported as not testable, never extrapolated.

Baselines: matched companies in the same sector whose figures became public within +-10 days and that had NO inflection event; stocks with a simple earnings surprise (>= +10%); stocks in the
top decile of 126-session price momentum; stocks in the top quintile of 60-session relative strength versus their sector.
"""
from __future__ import annotations

import bisect
import copy
import math
import zlib
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from inflection import config as IC
from inflection import context as X
from inflection import runner as R
from research.lab.manifest import canonical_hash

HORIZONS = (5, 20, 60, 120)
COST = 0.0020
BASE_TYPES = (IC.PROFITABILITY_TURNAROUND, IC.REVENUE_ACCELERATION, IC.MARGIN_EXPANSION, IC.OPERATING_LEVERAGE, IC.FCF_TURNAROUND)
ALL_TYPES = BASE_TYPES + (IC.LIMITED_REPRICING,)
PRIMARY = {"group": "union", "period": "test", "horizon": 20}
MIN_PRIMARY, MIN_CELL = 150, 30

SPEC: Dict[str, Any] = {
    "schema": "event_study_spec_v1", "key": "fundamental_inflection", "version": "v1",
    "hypothesis": "A company whose fundamentals just inflected (turned profitable, accelerated revenue, widened margins, gained operating leverage or turned free-cash-flow positive) outperforms its sector over the "
                  "following weeks and months by more than costs, at least while the market has not repriced it.",
    "event": {"source": "mechanism/inflection events, method inflection_v1, built from the stored quarterly statements (yfinance; research grade C: not point-in-time statements, availability = earnings report date or an assumed lag)",
              "types": list(ALL_TYPES), "union": "a company-quarter with at least one of the five base event types (counted once)"},
    "entry": "the open of the second session after the first session on or after the public date (the signal is the close after the two-session reaction window); the composite event, which is only known when its "
             "repricing window ends, is entered at the next session's open",
    "measure": {"forward_excess_return": "stock return from the entry open to the close `h` sessions later, minus the equal-weight return of its sector over the same sessions (the stock itself included), minus 20 bps round trip",
                "horizons_sessions": list(HORIZONS)},
    "split": {"basis": "date the figures were public", "train_end_exclusive": "2026-06-15", "test_start_inclusive": "2026-07-23", "validation": "between the two; looked at, never used to decide",
              "reason": "the stored history holds essentially two reporting seasons; the cut points are the two natural gaps between them"},
    "controls": {"matched_non_event": "same sector, figures public within +-10 days, no inflection event, one drawn at random per event (seeded)",
                 "earnings_surprise": "reported surprise >= +10%, same entry convention", "price_momentum": "top decile of 126-session return, sampled every 10 sessions",
                 "relative_strength": "top quintile of 60-session return relative to the sector, sampled every 10 sessions"},
    "sample_requirements": {"primary_min_events": MIN_PRIMARY, "cell_min_events": MIN_CELL},
    "decision_rule": {
        "primary_cell": "test period, union of base events, 20 sessions, net sector-adjusted excess return",
        "pass_for_forward_shadow_requires_all": [
            "n_test >= 150 company-quarters", "mean net excess return > 0 with the week-block bootstrap 95% interval entirely above 0",
            "mean excess minus the matched non-event control > 0 with its 95% interval entirely above 0", "the same sign on the train period when it has at least 30 events",
            "mean excess without the top 1% of events > 0"],
        "otherwise": "NO_EDGE_DEMONSTRATED, or INSUFFICIENT_DATA when the primary cell has fewer than 150 events or the horizon cannot be completed. A PASS authorises a forward shadow run only.",
        "multiple_testing": "29 cells are reported (6 types plus the union, 4 horizons, 3 periods looked at separately); only the primary cell decides and the others carry Bonferroni-widened intervals."},
}


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ statistics
def week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def block_ci(vals: Sequence[float], keys: Sequence[str], n_boot: int, seed: int, level: float = 0.05) -> Dict[str, Any]:
    v = np.asarray(vals, float)
    if len(v) == 0:
        return {"n": 0}
    k = np.asarray(keys)
    uniq = sorted(set(k.tolist()))
    blocks = [v[k == u] for u in uniq]
    rng = np.random.default_rng(seed)
    means = np.array([np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))]).mean() for _ in range(n_boot)])
    lo, hi = np.percentile(means, [100 * level / 2, 100 * (1 - level / 2)])
    return {"n": int(len(v)), "mean": round(float(v.mean()), 5), "median": round(float(np.median(v)), 5), "win_rate": round(float((v > 0).mean()), 4),
            "ci95": [round(float(lo), 5), round(float(hi), 5)], "blocks": len(uniq)}


def diff_ci(a: Sequence[float], ak: Sequence[str], b: Sequence[float], bk: Sequence[str], n_boot: int, seed: int) -> Dict[str, Any]:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) == 0 or len(b) == 0:
        return {"n_a": int(len(a)), "n_b": int(len(b))}
    ak, bk = np.asarray(ak), np.asarray(bk)
    keys = sorted(set(ak.tolist()) | set(bk.tolist()))
    ab, bb = [a[ak == k] for k in keys], [b[bk == k] for k in keys]
    rng = np.random.default_rng(seed)
    d = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(keys), len(keys))
        ca, cb = np.concatenate([ab[i] for i in pick]), np.concatenate([bb[i] for i in pick])
        if len(ca) and len(cb):
            d.append(ca.mean() - cb.mean())
    lo, hi = np.percentile(d, [2.5, 97.5])
    return {"n_a": int(len(a)), "n_b": int(len(b)), "diff": round(float(a.mean() - b.mean()), 5), "ci95": [round(float(lo), 5), round(float(hi), 5)]}


def without_top(vals: Sequence[float], share: float = 0.01) -> Optional[float]:
    v = np.sort(np.asarray(vals, float))
    if len(v) < 30:
        return None
    return round(float(v[: -max(1, int(len(v) * share))].mean()), 5)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ events -> entries -> outcomes
def first_idx(dates: Sequence[date], d: date, max_gap: int = 5) -> Optional[int]:
    i = bisect.bisect_left(dates, d)
    return i if i < len(dates) and (dates[i] - d).days <= max_gap else None


def entry_for(p: X.Prices, available_at: date, composite: bool) -> Optional[Tuple[int, int]]:
    """(signal bar, entry bar). Base events: signal = the close after the two-session reaction window, entry the next open; the composite event is known at the end of its window."""
    D = first_idx(p.dates, available_at)
    if D is None:
        return None
    s = D if composite else D + 1
    return (s, s + 1) if s + 1 < len(p.dates) else None


def excess(p: X.Prices, idx: Optional[X.SectorIndex], sector: Optional[str], entry: int, h: int) -> Optional[float]:
    """Net forward excess return from the entry open to the close h sessions later; None when the window is not complete or any piece is missing."""
    last = entry + h - 1
    if p.open is None or last >= len(p.dates) or entry < 1 or idx is None:
        return None
    o = float(p.open[entry])
    if not (o > 0):
        return None
    sector_ret = idx.ret(sector, p.dates[entry - 1], p.dates[last])
    if sector_ret is None:
        return None
    return float(p.close[last] / o - 1.0) - sector_ret - COST


def period_of(spec: Mapping[str, Any], d: date) -> str:
    sp = spec["split"]
    if d < date.fromisoformat(sp["train_end_exclusive"]):
        return "train"
    return "test" if d >= date.fromisoformat(sp["test_start_inclusive"]) else "validation"


def features_at(inp: R.Inputs, symbol: str, sig: int, sector: Optional[str], idx: X.SectorIndex) -> Dict[str, Optional[float]]:
    """Signal-time features used by the follow-up combinations (bars up to the signal bar only): 60-session return relative to the sector, volume confirmation on the two reaction sessions."""
    p = inp.prices[symbol]
    out: Dict[str, Optional[float]] = {"rs60": None, "relvol": None}
    if sig >= 60 and p.close[sig - 60] > 0:
        sec = idx.ret(sector, p.dates[sig - 60], p.dates[sig])
        if sec is not None:
            out["rs60"] = float(p.close[sig] / p.close[sig - 60] - 1.0) - sec
    if p.volume is not None and sig >= 22:
        base = float(np.mean(p.volume[sig - 22: sig - 1]))
        if base > 0:
            out["relvol"] = float(np.mean(p.volume[sig - 1: sig + 1]) / base)
    return out


def collect(inp: R.Inputs, asof: date, spec: Mapping[str, Any]) -> Dict[str, Any]:
    idx = X.SectorIndex(inp.prices, inp.sector_of)
    feats, events = R.run_universe(inp, asof, sector_index=idx)
    ev_quarters = {(e["symbol"], e["quarter_end"]) for e in events}
    units: Dict[Tuple[str, date], Dict[str, Any]] = {}
    singles: List[Dict[str, Any]] = []
    for e in events:
        p = inp.prices.get(e["symbol"])
        if p is None:
            continue
        composite = e["event_type"] == IC.LIMITED_REPRICING
        ent = entry_for(p, e["available_at"], composite)
        if ent is None:
            continue
        sector = inp.sector_of.get(e["symbol"])
        row = {"symbol": e["symbol"], "quarter_end": e["quarter_end"], "type": e["event_type"], "available_at": e["available_at"], "sector": sector, "signal": ent[0], "entry": ent[1],
               "week": week_key(e["available_at"]), "period": period_of(spec, e["available_at"]), "strength": e["strength"], "context": e.get("context") or {}}
        singles.append(row)
        if not composite:
            u = units.setdefault((e["symbol"], e["quarter_end"]), {**row, "types": set()})
            u["types"].add(e["event_type"])
    for u in units.values():
        u.update(features_at(inp, u["symbol"], u["signal"], u["sector"], idx))
        u["valuation"] = (u["context"].get("valuation_at_publication") or {})
    nonev = []
    for f in feats:
        if (f["symbol"], f["quarter_end"]) in ev_quarters:
            continue
        p = inp.prices.get(f["symbol"])
        sector = inp.sector_of.get(f["symbol"])
        if p is None or sector is None:
            continue
        ent = entry_for(p, f["available_at"], False)
        if ent is not None:
            nonev.append({"symbol": f["symbol"], "available_at": f["available_at"], "sector": sector, "entry": ent[1], "signal": ent[0], "week": week_key(f["available_at"])})
    return {"idx": idx, "singles": singles, "units": list(units.values()), "nonevents": nonev, "n_features": len(feats), "n_events": len(events)}


def baselines(inp: R.Inputs, idx: X.SectorIndex, lo: date, hi: date, spec: Mapping[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {"earnings_surprise": [], "price_momentum": [], "relative_strength": []}
    for sym, lst in inp.surprises.items():
        p = inp.prices.get(sym)
        if p is None or sym not in inp.sector_of:
            continue
        for d, s in lst:
            if s >= 10.0 and lo <= d <= hi:
                ent = entry_for(p, d, False)
                if ent is not None:
                    out["earnings_surprise"].append({"symbol": sym, "available_at": d, "sector": inp.sector_of[sym], "entry": ent[1], "week": week_key(d)})
    cal = sorted({d for p in inp.prices.values() for d in p.dates if lo <= d <= hi})
    for d in cal[::10]:
        rows = []
        for sym, p in inp.prices.items():
            if sym not in inp.sector_of:
                continue
            i = bisect.bisect_left(p.dates, d)
            if i >= len(p.dates) or p.dates[i] != d or i < 126 or i + 1 >= len(p.dates) or p.close[i - 126] <= 0 or p.close[i - 60] <= 0:
                continue
            sec60 = idx.ret(inp.sector_of[sym], p.dates[i - 60], d)
            if sec60 is None:
                continue
            rows.append((sym, i, float(p.close[i] / p.close[i - 126] - 1.0), float(p.close[i] / p.close[i - 60] - 1.0) - sec60))
        if len(rows) < 200:
            continue
        mom = sorted(rows, key=lambda r: -r[2])[: len(rows) // 10]
        rs = sorted(rows, key=lambda r: -r[3])[: len(rows) // 5]
        for key, grp in (("price_momentum", mom), ("relative_strength", rs)):
            for sym, i, _, _ in grp:
                out[key].append({"symbol": sym, "available_at": d, "sector": inp.sector_of[sym], "entry": i + 1, "week": week_key(d)})
    return out


def outcomes(inp: R.Inputs, idx: X.SectorIndex, rows: Sequence[Mapping[str, Any]], h: int) -> List[Tuple[float, str, Mapping[str, Any]]]:
    out = []
    for r in rows:
        v = excess(inp.prices[r["symbol"]], idx, r["sector"], r["entry"], h)
        if v is not None and math.isfinite(v):
            out.append((v, r["week"], r))
    return out


def matched(units: Sequence[Mapping[str, Any]], pool: Sequence[Mapping[str, Any]], seed: int) -> Dict[Tuple[str, date], Mapping[str, Any]]:
    """One non-event company-quarter per unit: same sector, public within +-10 days, drawn with a seeded generator keyed on the unit (order-independent)."""
    by_sector: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for n in pool:
        by_sector[n["sector"]].append(n)
    out = {}
    for u in units:
        cands = [n for n in by_sector.get(u["sector"], []) if abs((n["available_at"] - u["available_at"]).days) <= 10 and n["symbol"] != u["symbol"]]
        if not cands:
            continue
        cands.sort(key=lambda n: (n["symbol"], n["available_at"]))
        rng = np.random.default_rng([seed, zlib.crc32(f"{u['symbol']}{u['quarter_end']}".encode())])
        out[(u["symbol"], u["quarter_end"])] = cands[int(rng.integers(0, len(cands)))]
    return out


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ the study
def run_study(spec: Mapping[str, Any], inp: R.Inputs, asof: date, *, seed: int = 20261031, n_boot: int = 1000) -> Dict[str, Any]:
    data = collect(inp, asof, spec)
    idx, units, singles, pool = data["idx"], data["units"], data["singles"], data["nonevents"]
    all_dates = [u["available_at"] for u in units] or [asof]
    lo, hi = min(all_dates), max(all_dates)
    base = baselines(inp, idx, lo, hi, spec)
    mt = matched(units, pool, seed)
    n_cells_secondary = 29
    cells: Dict[str, Any] = {}
    diffs: Dict[str, Any] = {}
    feasible: Dict[str, Any] = {}
    last_data = max(p.dates[-1] for p in inp.prices.values())
    for h in HORIZONS:
        complete = [u["available_at"] for u in units if excess(inp.prices[u["symbol"]], idx, u["sector"], u["entry"], h) is not None]
        feasible[str(h)] = {"events_with_complete_window": len(complete), "latest_public_date_complete": max(complete).isoformat() if complete else None}
        for period in ("train", "validation", "test"):
            sel = [u for u in units if u["period"] == period]
            o = outcomes(inp, idx, sel, h)
            key = f"union|{period}|h{h}"
            is_primary = (period, h) == (PRIMARY["period"], PRIMARY["horizon"])
            c = block_ci([v for v, _, _ in o], [w for _, w, _ in o], n_boot, seed + h, 0.05 if is_primary else 0.05 / n_cells_secondary)
            c["without_top_1pct"] = without_top([v for v, _, _ in o])
            cells[key] = c
            paired, pk = [], []
            for v, w, r in o:
                m = mt.get((r["symbol"], r["quarter_end"]))
                if m is None:
                    continue
                mv = excess(inp.prices[m["symbol"]], idx, m["sector"], m["entry"], h)
                if mv is not None and math.isfinite(mv):
                    paired.append(v - mv)
                    pk.append(w)
            if paired:
                diffs[f"{period}|h{h}|union_minus_matched"] = {**block_ci(paired, pk, n_boot, seed + 100 + h, 0.05 if is_primary else 0.05 / n_cells_secondary), "n_matched": len(paired)}
            for t in ALL_TYPES:
                ts = [s for s in singles if s["type"] == t and s["period"] == period]
                to = outcomes(inp, idx, ts, h)
                cells[f"{t}|{period}|h{h}"] = block_ci([v for v, _, _ in to], [w for _, w, _ in to], n_boot, seed + 200 + h, 0.05 / n_cells_secondary)
            for name, rows in base.items():
                bs = [r for r in rows if period_of(spec, r["available_at"]) == period]
                bo = outcomes(inp, idx, bs, h)
                cells[f"{name}|{period}|h{h}"] = block_ci([v for v, _, _ in bo], [w for _, w, _ in bo], n_boot, seed + 300 + h, 0.05 / n_cells_secondary)
    result: Dict[str, Any] = {"cells": cells, "diffs": diffs, "feasibility": feasible,
                              "counts": {"features": data["n_features"], "events": data["n_events"], "company_quarters_with_a_base_event": len(units), "matched_non_event_pool": len(pool),
                                         "by_type": {t: sum(1 for s in singles if s["type"] == t) for t in ALL_TYPES}, "by_period": {p: sum(1 for u in units if u["period"] == p) for p in ("train", "validation", "test")},
                                         "baseline_sizes": {k: len(v) for k, v in base.items()}, "public_dates": [lo.isoformat(), hi.isoformat()], "last_price_date": last_data.isoformat()}}
    result["verdict"], result["decision_checks"] = decide(spec, result)
    result["followups"] = followups(spec, inp, idx, units, n_boot, seed)
    from research.strategy_lab.inflection_report import horizon_followups        # lazy: the report module imports this one
    result["horizon_followups"] = horizon_followups(result)
    return result


def decide(spec: Mapping[str, Any], result: Mapping[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    cells, diffs = result["cells"], result["diffs"]
    h, period = PRIMARY["horizon"], PRIMARY["period"]
    test, train = cells.get(f"union|{period}|h{h}", {}), cells.get(f"union|train|h{h}", {})
    n = int(test.get("n", 0))
    if n < MIN_PRIMARY:
        return "INSUFFICIENT_DATA", [{"check": f"n_test >= {MIN_PRIMARY} company-quarters with a complete {h}-session window", "value": n, "ok": False}]
    d = diffs.get(f"{period}|h{h}|union_minus_matched", {})
    checks = [
        {"check": f"n_test >= {MIN_PRIMARY}", "value": n, "ok": True},
        {"check": "test mean net excess return, 95% interval entirely above 0", "value": test.get("ci95"), "ok": bool(test.get("ci95") and test["ci95"][0] > 0)},
        {"check": "minus the matched non-event control, 95% interval entirely above 0", "value": d.get("ci95"), "ok": bool(d.get("ci95") and d["ci95"][0] > 0)},
        {"check": "same sign on the train period (when it has at least 30 events)", "value": train.get("mean"), "ok": bool(train.get("n", 0) < MIN_CELL or (train.get("mean") is not None and train["mean"] > 0))},
        {"check": "test mean without the top 1% of events > 0", "value": test.get("without_top_1pct"), "ok": bool(test.get("without_top_1pct") is not None and test["without_top_1pct"] > 0)},
    ]
    return ("PASS_FOR_FORWARD_SHADOW" if all(c["ok"] for c in checks) else "NO_EDGE_DEMONSTRATED"), checks


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ follow-up hypotheses
COMBOS = [
    ("profitability_plus_revenue", "Profitability Turnaround + Revenue Acceleration", lambda u: {IC.PROFITABILITY_TURNAROUND, IC.REVENUE_ACCELERATION} <= u["types"]),
    ("margin_plus_relative_strength", "Margin Expansion + Relative Strength (top quintile of 60-session strength versus the sector)", lambda u: IC.MARGIN_EXPANSION in u["types"] and (u.get("rs60") or -9) >= 0.10),
    ("revenue_plus_volume", "Revenue Acceleration + Volume Confirmation (reaction-day volume at least 2x normal)", lambda u: IC.REVENUE_ACCELERATION in u["types"] and (u.get("relvol") or 0) >= 2.0),
    ("fcf_plus_valuation_compression", "FCF Turnaround + Valuation Compression (price/sales in the lower third of its own history or below 0.8x the sector)", lambda u: IC.FCF_TURNAROUND in u["types"] and (((u["valuation"].get("ps_percentile_own_history")) is not None and u["valuation"]["ps_percentile_own_history"] <= 0.34) or ((u["valuation"].get("ps_vs_sector_median")) is not None and u["valuation"]["ps_vs_sector_median"] <= 0.8))),
]


def followups(spec: Mapping[str, Any], inp: R.Inputs, idx: X.SectorIndex, units: Sequence[Mapping[str, Any]], n_boot: int, seed: int) -> List[Dict[str, Any]]:
    """Registered follow-up hypotheses. The test period has been looked at by the primary cell, so these are NOT confirmed on it: they get exploratory numbers from the train and validation periods
    (labelled as such) and are registered for FORWARD evaluation only, on events made public after the registration date."""
    out = []
    for key, label, fn in COMBOS:
        sel = [u for u in units if fn(u)]
        row: Dict[str, Any] = {"key": f"fundamental_inflection_{key}", "version": "v1", "label": label, "status": "registered_forward_only", "n_company_quarters": len(sel),
                               "n_by_period": {p: sum(1 for u in sel if u["period"] == p) for p in ("train", "validation", "test")}}
        explore = [u for u in sel if u["period"] in ("train", "validation")]
        o = outcomes(inp, idx, explore, 20)
        row["exploratory_train_validation_h20"] = block_ci([v for v, _, _ in o], [w for _, w, _ in o], n_boot, seed + 900, 0.05 / len(COMBOS)) if o else {"n": 0}
        row["note"] = "exploratory only (not confirmatory): the test period is not used; confirmation needs events public after the registration date and at least 150 of them"
        out.append(row)
    return out


def combo_spec(parent: Mapping[str, Any], c: Mapping[str, Any], registered_after: str) -> Dict[str, Any]:
    s = copy.deepcopy(dict(parent))
    s["key"], s["version"] = c["key"], c["version"]
    s["hypothesis"] = f"{c['label']} outperforms its sector over the following weeks by more than costs."
    s["parent"] = {"key": parent["key"], "version": parent["version"], "spec_hash": canonical_hash(parent)}
    s["event"] = {**parent["event"], "combination": c["label"]}
    s["forward_only_after"] = registered_after
    s["split"] = {"basis": "date the figures were public", "forward_only_after": registered_after, "note": "no historical period is used as confirmation: the existing test period has been looked at"}
    s["decision_rule"] = {**parent["decision_rule"], "evaluated_on": f"events made public after {registered_after} only; INSUFFICIENT_DATA until at least 150 have a complete window"}
    return s
