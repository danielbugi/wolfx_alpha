"""Forward paper tracker dashboard: PURE aggregation of the stored forward evidence. No I/O, no clock, no database, no randomness.

Source of truth: the immutable rows the tracker wrote (`paper_session`, `paper_observation`, `paper_event`) handed in as plain dicts, plus the loaded daily bars needed to MARK open positions and to measure
hindsight opportunity. Nothing is recomputed that the tracker already recorded: realised net R at 0 / 10 / 25 bps, MFE and MAE of closed positions and exit reasons are read back from the `closed`
event. The only computations are aggregation (counts, means, medians, intervals) and two bar-based measurements for positions still in flight or just closed:

  * marks of open positions at the last loaded close (an open position is not a result);
  * HINDSIGHT opportunity so far (peak favourable excursion since entry, share captured, continuation after the exit). These need bars after the decision, so every one of them is labelled
    `decision_time_information: false` and `window: "so_far"`; the tracker's own `hindsight` events (which need >= 250 bars) are only counted.

Every payload says `source: "forward"`. Historical replay and shadow-test results are NOT inputs here and must never be merged into this payload.

Input shapes (all plain dicts/lists; dates may be `date` or ISO strings):
    sessions      [{session_date, scope, n_candidates, n_registered, n_skipped_open, n_entered, n_closed, n_not_enterable, n_open_after, open_state_hash, code_ref}]  oldest first or any order
    observations  [{id, variant_key, variant_version, definition_hash, symbol, direction, signal_session, atr, context}]
    events        [{observation_id, event_type, session_date, payload}]
    bars          {symbol: [(date, open, high, low, close), ...]} ascending; need only cover the symbols/dates of entered positions
    capture_runs  [{session_date, status, candidates}]                                  optional
"""
import math
import statistics
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from research import exit_policies as X
from research import opportunity as OP

SCHEMA = "forward_tracker_dashboard_v1"
MIN_INTERVAL_N = 30                 # an interval is shown only from this many closed positions
COST_BPS = ("0", "10", "25")        # keys of the costs recorded on every closed event; "10" is the primary assumption
PRIMARY_COST = "10"
BIG_WINNER_TIERS = (3.0, 5.0)       # peak favourable excursion in R (1R = STOP_ATR x ATR14 of the signal bar)
PREMATURE_R = OP.PREMATURE_R
TOP_N = 20

BANNER = "Forward paper observation: simulated positions only. No order is placed and nothing here is advice."


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ small helpers
def _d(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _iso(v: Any) -> Optional[str]:
    d = _d(v)
    return d.isoformat() if d else None


def _r(x: Any, nd: int = 4) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return round(f, nd) if math.isfinite(f) else None


def short_name(variant_key: str) -> str:
    return variant_key.split("__exit_")[-1]


def summarise(values: Sequence[Optional[float]], min_n: int = MIN_INTERVAL_N) -> Dict[str, Any]:
    """n, mean, median, win rate (> 0), profit factor and a normal-approximation 95% interval of the mean. The interval is withheld below `min_n`, and says so."""
    v = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    n = len(v)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "win_rate": None, "profit_factor": None, "interval95": None, "interval_note": "no_data"}
    mean = sum(v) / n
    gains = sum(x for x in v if x > 0)
    losses = -sum(x for x in v if x < 0)
    out: Dict[str, Any] = {"n": n, "mean": _r(mean), "median": _r(statistics.median(v)), "win_rate": _r(sum(1 for x in v if x > 0) / n),
                           "profit_factor": _r(gains / losses) if losses > 0 else None, "interval95": None, "interval_note": None}
    if n >= min_n and n > 1:
        half = 1.96 * statistics.stdev(v) / math.sqrt(n)
        out["interval95"] = [_r(mean - half), _r(mean + half)]
    else:
        out["interval_note"] = "n_too_small"
    return out


def _mean_median(values: Sequence[Optional[float]]) -> Dict[str, Any]:
    v = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    return {"n": len(v), "mean": _r(sum(v) / len(v)) if v else None, "median": _r(statistics.median(v)) if v else None}


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ position state
class _Pos:
    __slots__ = ("obs", "status", "entered", "closed", "not_enterable")

    def __init__(self, obs: Mapping[str, Any]):
        self.obs = obs
        self.entered: Optional[Mapping[str, Any]] = None
        self.closed: Optional[Mapping[str, Any]] = None
        self.not_enterable: Optional[Mapping[str, Any]] = None
        self.status = "pending_entry"

    def settle(self) -> None:
        self.status = "closed" if self.closed else "not_enterable" if self.not_enterable else "open" if self.entered else "pending_entry"


def _positions(observations: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]) -> Dict[Any, _Pos]:
    pos = {o["id"]: _Pos(o) for o in observations}
    hindsight: Dict[Any, Mapping[str, Any]] = {}
    for e in events:
        p = pos.get(e["observation_id"])
        if p is None:
            continue
        t = e["event_type"]
        if t == "entered":
            p.entered = e["payload"]
        elif t == "closed":
            p.closed = e["payload"]
        elif t == "not_enterable":
            p.not_enterable = e["payload"]
    for p in pos.values():
        p.settle()
    return pos


def _bars_from(bars: Mapping[str, Sequence[Tuple]], symbol: str, start: date) -> List[Tuple]:
    return [b for b in bars.get(symbol, ()) if _d(b[0]) >= start]


def _path(p: _Pos, bars: Mapping[str, Sequence[Tuple]]) -> Optional[Dict[str, Any]]:
    """Bar-based measurements for an ENTERED position, from the entry session to the last loaded bar. 1R = the tracker's own `unit` (STOP_ATR x ATR14). Hindsight-only for closed positions."""
    ent = p.entered
    if not ent:
        return None
    d = int(p.obs["direction"])
    entry = float(ent["entry_price"])
    unit = float(ent.get("unit") or X.STOP_ATR * float(p.obs["atr"]))
    if not (unit > 0 and entry > 0):
        return None
    seq = _bars_from(bars, p.obs["symbol"], _d(ent["entry_session"]))
    if not seq:
        return {"entry": entry, "unit": unit, "direction": d, "last_close": None, "last_date": None, "bars": 0, "peak_fav_r": None, "peak_adv_r": None}
    fav = max((d * ((b[2] if d > 0 else b[3]) - entry)) / unit for b in seq)
    adv = max((d * (entry - (b[3] if d > 0 else b[2]))) / unit for b in seq)
    return {"entry": entry, "unit": unit, "direction": d, "last_close": float(seq[-1][4]), "last_date": _iso(seq[-1][0]), "bars": len(seq),
            "peak_fav_r": max(fav, 0.0), "peak_adv_r": max(adv, 0.0), "seq": seq}


def _net_if_closed(gross_r: float, entry: float, unit: float, bps: float) -> float:
    return gross_r - (bps / 1e4) * entry / unit


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ per-variant blocks
def _closed_block(closed: Sequence[_Pos]) -> Dict[str, Any]:
    pl = [p.closed for p in closed if p.closed]
    net_std = {k: summarise([(x.get("r_net_std") or {}).get(k) for x in pl]) for k in COST_BPS}
    net_act = {k: summarise([(x.get("r_net_actual") or {}).get(k) for x in pl]) for k in COST_BPS}
    reasons: Dict[str, int] = defaultdict(int)
    for x in pl:
        reasons[str(x.get("reason"))] += 1
    return {"n": len(pl), "net_r_std": net_std, "net_r_actual": net_act, "exit_reasons": dict(sorted(reasons.items())),
            "bars_held": _mean_median([x.get("bars_held") for x in pl]), "mfe_r": _mean_median([x.get("mfe_r_std") for x in pl]), "mae_r": _mean_median([x.get("mae_r_std") for x in pl]),
            "stale_sessions_total": sum(int(x.get("stale_sessions") or 0) for x in pl)}


def _open_block(open_pos: Sequence[_Pos], paths: Mapping[Any, Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    marked, unmarked, gross, net10, mfe, mae, bars, details = 0, 0, [], [], [], [], [], []
    for p in open_pos:
        pa = paths.get(p.obs["id"])
        if not pa or pa["last_close"] is None:
            unmarked += 1
            continue
        g = pa["direction"] * (pa["last_close"] - pa["entry"]) / pa["unit"]
        marked += 1
        gross.append(g)
        net10.append(_net_if_closed(g, pa["entry"], pa["unit"], float(PRIMARY_COST)))
        mfe.append(pa["peak_fav_r"])
        mae.append(pa["peak_adv_r"])
        bars.append(pa["bars"])
        details.append((pa["peak_fav_r"], p, pa, g))
    return {"n": len(open_pos), "marked": marked, "unmarked": unmarked,
            "unrealised_gross_r": _mean_median(gross), "unrealised_net_r_10bps": _mean_median(net10), "running_mfe_r": _mean_median(mfe), "running_mae_r": _mean_median(mae),
            "bars_since_entry": _mean_median(bars), "label": "marked at the last loaded close; an open position is not a result", "_details": details}


def _big_winner_block(variant_pos: Sequence[_Pos], paths: Mapping[Any, Optional[Dict[str, Any]]], stored_hindsight: int) -> Dict[str, Any]:
    entered = [(p, paths.get(p.obs["id"])) for p in variant_pos if p.status in ("open", "closed")]
    entered = [(p, pa) for p, pa in entered if pa and pa["peak_fav_r"] is not None]
    tiers = {f"peak_ge_{t:g}r": sum(1 for _, pa in entered if pa["peak_fav_r"] >= t) for t in BIG_WINNER_TIERS}
    caps, prem, cont = [], [], []
    for p, pa in entered:
        if p.status != "closed" or not p.closed:
            continue
        seq = pa["seq"]
        exit_date = _d(p.closed["exit_session"])
        realised_pct = float(p.closed["return_pct"])
        fav_pct = max(pa["direction"] * (((b[2] if pa["direction"] > 0 else b[3]) / pa["entry"]) - 1) * 100 for b in seq)
        if fav_pct > 0:
            caps.append(realised_pct / fav_pct)
        after = [b for b in seq if _d(b[0]) > exit_date]
        if after:
            exit_price = float(p.closed["exit_price"])
            c = max(pa["direction"] * ((b[2] if pa["direction"] > 0 else b[3]) - exit_price) / pa["unit"] for b in after)
            c = max(c, 0.0)
            cont.append(c)
            prem.append(1.0 if c >= PREMATURE_R else 0.0)
    return {"decision_time_information": False, "window": "so_far", "entered_positions": len(entered), **tiers,
            "capture_ratio": _mean_median(caps), "continuation_after_exit_r": _mean_median(cont),
            "premature_exit_share": _r(sum(prem) / len(prem)) if prem else None, "premature_exit_n": len(prem),
            "stored_hindsight_events": stored_hindsight,
            "note": "Peak favourable excursion since entry, share of it captured, and further movement after the exit, measured on bars loaded so far. Needs bars after the decision: hindsight, not known when the position was opened. Windows are still short."}


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ completeness and health
def _completeness(observations: Sequence[Mapping[str, Any]], sessions: Sequence[Mapping[str, Any]], capture_runs: Sequence[Mapping[str, Any]], as_of: Optional[date]) -> Dict[str, Any]:
    by_session: Dict[str, Dict[Tuple[str, int], Mapping[str, Any]]] = defaultdict(dict)
    for o in observations:
        by_session[_iso(o["signal_session"])].setdefault((o["symbol"], int(o["direction"])), o.get("context") or {})
    rows = []
    for s in sorted(sessions, key=lambda s: _iso(s["session_date"])):
        sd = _iso(s["session_date"])
        ctx = by_session.get(sd, {})
        n = len(ctx)
        reg = sum(1 for c in ctx.values() if (c.get("market_regime") or {}).get("available") is True)
        rs = sum(1 for c in ctx.values() if (c.get("relative_strength") or {}).get("available") is True)
        sector = sum(1 for c in ctx.values() if ((c.get("features") or {}).get("sector")))
        guard = sum(1 for c in ctx.values() if ((c.get("candidate") or {}).get("passed_guard") is False))
        cat_events = sum(1 for c in ctx.values() if ((c.get("catalysts") or {}).get("events")))
        cat_unavailable = sum(1 for c in ctx.values() if ((c.get("catalysts") or {}).get("section") == "unavailable"))
        reasons: Dict[str, int] = defaultdict(int)
        for c in ctx.values():
            for sec in ("market_regime", "relative_strength", "catalysts"):
                r = (c.get(sec) or {}).get("reason")
                if r:
                    reasons[f"{sec}: {r}"] += 1
        rows.append({"session": sd, "candidates": n, "market_regime_attached": reg, "relative_strength_attached": rs, "sector_known": sector, "guard_rejected": guard,
                     "catalysts_with_events": cat_events, "catalysts_unavailable": cat_unavailable, "unavailable_reasons": dict(sorted(reasons.items())),
                     "registered": s.get("n_registered"), "entered": s.get("n_entered"), "closed": s.get("n_closed"), "not_enterable": s.get("n_not_enterable"), "skipped_open": s.get("n_skipped_open")})
    processed = {_iso(s["session_date"]) for s in sessions}
    pending = sorted(_iso(c["session_date"]) for c in capture_runs if c.get("status") == "complete" and _iso(c["session_date"]) not in processed
                     and (not processed or _iso(c["session_date"]) > min(processed)))
    return {"sessions": rows, "captured_not_processed": pending, "as_of": _iso(as_of)}


def _health(comp: Dict[str, Any], paths_unmarked: int, hindsight_events: int, trading_sessions: Optional[Sequence[Any]], sessions: Sequence[Mapping[str, Any]], as_of: Optional[date]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    processed = sorted(_iso(s["session_date"]) for s in sessions)
    if trading_sessions is not None and processed:
        expected = [_iso(d) for d in trading_sessions if _iso(d) >= processed[0] and _iso(d) <= processed[-1]]
        missing = [d for d in expected if d not in processed]
        out.append({"name": "session_continuity", "status": "ok" if not missing else "warn", "detail": "every trading session since activation was processed" if not missing else f"not processed: {missing}"})
    pend = comp["captured_not_processed"]
    stale = [d for d in pend if as_of and (as_of - date.fromisoformat(d)).days >= 2]
    out.append({"name": "captured_sessions_processed", "status": "ok" if not stale else "warn", "detail": "no completed capture is waiting" if not pend else f"waiting for processing: {pend}"})
    for key, label, thr in (("market_regime_attached", "market_regime_coverage", 0.95), ("relative_strength_attached", "relative_strength_coverage", 0.95)):
        worst = None
        for r in comp["sessions"]:
            if r["candidates"]:
                share = r[key] / r["candidates"]
                worst = share if worst is None else min(worst, share)
        out.append({"name": label, "status": "ok" if worst is None or worst >= thr else "warn", "detail": "no session processed yet" if worst is None else f"lowest session coverage {worst * 100:.1f}% (threshold {thr * 100:.0f}%)"})
    no_feed = bool(comp["sessions"]) and all(r["catalysts_with_events"] == 0 and r["catalysts_unavailable"] == r["candidates"] for r in comp["sessions"] if r["candidates"])
    out.append({"name": "catalyst_context", "status": "info", "detail": "no catalyst feed: every candidate is explicitly marked unavailable" if no_feed else "catalyst events attached for some candidates"})
    out.append({"name": "open_positions_marked", "status": "ok" if paths_unmarked == 0 else "warn", "detail": "every open position has a loaded bar" if paths_unmarked == 0 else f"{paths_unmarked} open position(s) have no bar to mark against"})
    out.append({"name": "stored_hindsight_events", "status": "info", "detail": f"{hindsight_events} recorded by the tracker (it writes them only 250+ bars after entry)"})
    return out


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ the payload
def build_forward_payload(*, sessions: Sequence[Mapping[str, Any]], observations: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]],
                          bars: Mapping[str, Sequence[Tuple]], capture_runs: Sequence[Mapping[str, Any]] = (), as_of: Any = None,
                          trading_sessions: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
    as_of_d = _d(as_of)
    base: Dict[str, Any] = {"schema": SCHEMA, "source": "forward", "banner": BANNER,
                            "definitions": {"R": f"1R = {X.STOP_ATR:g} x ATR14 of the signal bar (the tracker's standardised unit); the variant's actual initial risk is also recorded on each closed event",
                                            "net_r": "realised R after a round-trip cost charged in R: gross minus bps/10000 x entry / 1R; 0, 10 (primary) and 25 bps are recorded by the tracker on every closed event",
                                            "interval": f"normal-approximation 95% interval of the mean, shown only from {MIN_INTERVAL_N} closed positions", "hindsight": "uses bars after the decision; never known at decision time",
                                            "replay_separation": "historical replay and shadow-test results are never part of this payload"},
                            "cost_bps": list(COST_BPS), "primary_cost_bps": PRIMARY_COST}
    if not sessions:
        return {**base, "available": False, "reason": "no forward session has been processed yet", "sessions_processed": 0}
    ss = sorted(sessions, key=lambda s: _iso(s["session_date"]))
    latest = ss[-1]
    pos = _positions(observations, events)
    paths = {oid: _path(p, bars) for oid, p in pos.items() if p.status in ("open", "closed")}
    obs_variant = {o["id"]: o["variant_key"] for o in observations}
    hindsight_by_variant: Dict[str, int] = defaultdict(int)
    for e in events:
        if e["event_type"] == "hindsight" and e["observation_id"] in obs_variant:
            hindsight_by_variant[obs_variant[e["observation_id"]]] += 1
    hindsight_n = sum(hindsight_by_variant.values())
    by_variant: Dict[str, List[_Pos]] = defaultdict(list)
    for p in pos.values():
        by_variant[p.obs["variant_key"]].append(p)
    variants = []
    unmarked_total = 0
    for key in sorted(by_variant):
        vp = by_variant[key]
        o0 = vp[0].obs
        counts = {s: sum(1 for p in vp if p.status == s) for s in ("pending_entry", "open", "closed", "not_enterable")}
        ob = _open_block([p for p in vp if p.status == "open"], paths)
        unmarked_total += ob["unmarked"]
        details = ob.pop("_details")
        top = sorted(details, key=lambda t: -t[0])[:TOP_N]
        bw = _big_winner_block(vp, paths, hindsight_by_variant.get(key, 0))
        bw["top_open_by_peak_r"] = [{"symbol": p.obs["symbol"], "direction": int(p.obs["direction"]), "entry_session": _iso(p.entered["entry_session"]), "peak_favourable_r_so_far": _r(pk, 2),
                                     "unrealised_gross_r": _r(g, 2), "bars_since_entry": pa["bars"]} for pk, p, pa, g in top if pk >= min(BIG_WINNER_TIERS)]
        closed = _closed_block([p for p in vp if p.status == "closed"])
        variants.append({"variant_key": key, "name": short_name(key), "version": o0.get("variant_version"), "definition_hash": str(o0.get("definition_hash"))[:16], "positions": {"registered": len(vp), **counts},
                         "closed": closed, "open": ob, "big_winner": bw})
    comparison = []
    for v in variants:
        c10 = v["closed"]["net_r_std"][PRIMARY_COST]
        comparison.append({"name": v["name"], "registered": v["positions"]["registered"], "open": v["positions"]["open"], "pending_entry": v["positions"]["pending_entry"], "closed": v["closed"]["n"],
                           "net_r_10bps_mean": c10["mean"], "net_r_10bps_median": c10["median"], "win_rate": c10["win_rate"], "profit_factor": c10["profit_factor"], "interval95": c10["interval95"],
                           "status": "n_too_small" if v["closed"]["n"] < MIN_INTERVAL_N else "interval_shown", "mfe_r_mean": v["closed"]["mfe_r"]["mean"], "mae_r_mean": v["closed"]["mae_r"]["mean"],
                           "unrealised_net_r_10bps_mean": v["open"]["unrealised_net_r_10bps"]["mean"], "peak_ge_3r": v["big_winner"]["peak_ge_3r"], "peak_ge_5r": v["big_winner"]["peak_ge_5r"],
                           "capture_ratio_median": v["big_winner"]["capture_ratio"]["median"], "premature_exit_share": v["big_winner"]["premature_exit_share"]})
    comp = _completeness(observations, ss, capture_runs, as_of_d)
    return {**base, "available": True, "since": _iso(ss[0]["session_date"]), "sessions_processed": len(ss),
            "latest_session": {k: (_iso(latest[k]) if k == "session_date" else latest.get(k)) for k in ("session_date", "scope", "n_variants", "n_candidates", "n_registered", "n_skipped_open",
                                                                                                         "n_entered", "n_closed", "n_not_enterable", "n_open_after", "code_ref") if k in latest}
                               | {"open_state_hash_prefix": str(latest.get("open_state_hash", ""))[:16]},
            "variants": variants, "comparison": comparison, "completeness": comp,
            "health": _health(comp, unmarked_total, hindsight_n, trading_sessions, ss, as_of_d)}
