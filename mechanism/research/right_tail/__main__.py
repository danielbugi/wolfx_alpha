"""Run the asymmetric breakout study over a hashed extract.

    PYTHONPATH=mechanism python -m research.right_tail run --data DIR --out OUT --code-ref $(git rev-parse HEAD)

Writes OUT/result.json (every number) and prints a short summary. Same extract + code + seed -> byte-identical result.json.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from typing import Any, Dict, List

import numpy as np

from research.right_tail import spec as SP
from research.right_tail import study as S

SEED = 20261102
CASES = ("WDC", "DOCN", "SEZL")
PRIMARY_SET, PRIMARY_EXIT = "BASE+RS+VOL", "X4_trend_sma50"
DESCRIPTIVE_FEATURES = ("rs", "vol_ratio", "atr_pct", "depth40", "dist_52w_high", "ret20", "adv20_musd", "risk_pct", "breadth")


def _feat(p: S.Pos, f: str) -> float:
    if f == "rs":
        return p.rs
    if f == "risk_pct":
        return p.risk / p.entry * 100
    if f == "breadth":
        return p.breadth
    return p.feats.get(f, float("nan"))


def winners_vs_rest(ps: List[S.Pos], bps: float) -> Dict[str, Any]:
    """DESCRIPTIVE, discovery segment only: signal-time features of positions that realised >= 5R versus the rest, and the >= 5R rate by tercile of each feature."""
    ps = [p for p in S.usable(ps) if p.segment == "discovery"]
    if len(ps) < 100:
        return {"n": len(ps)}
    big = np.array([p.r_net(bps) >= 5 for p in ps])
    out: Dict[str, Any] = {"n": len(ps), "n_ge_5R": int(big.sum()), "base_rate": round(float(big.mean()), 4), "features": {}}
    for f in DESCRIPTIVE_FEATURES:
        x = np.array([_feat(p, f) for p in ps], dtype=float)
        ok = np.isfinite(x)
        if ok.sum() < 100:
            continue
        q1, q2 = np.quantile(x[ok], [1 / 3, 2 / 3])
        terc = {}
        for name, k in (("low", ok & (x <= q1)), ("mid", ok & (x > q1) & (x <= q2)), ("high", ok & (x > q2))):
            terc[name] = {"n": int(k.sum()), "rate_ge_5R": round(float(big[k].mean()), 4) if k.any() else None}
        out["features"][f] = {"median_winners": round(float(np.median(x[ok & big])), 4) if (ok & big).any() else None,
                              "median_rest": round(float(np.median(x[ok & ~big])), 4), "tercile_edges": [round(float(q1), 4), round(float(q2), 4)], "terciles": terc}
    return out


def case_rows(ps: List[S.Pos], bps: float) -> List[Dict[str, Any]]:
    return [{"symbol": p.sym, "set": p.set_name, "exit": p.exit_kind, "signal_date": p.signal_date.isoformat(), "entry": round(p.entry, 2), "stop": round(p.stop0, 2),
             "risk_pct": round(p.risk / p.entry * 100, 1), "exit_date": p.exit_date.isoformat(), "reason": p.out.reason, "censored": p.out.censored,
             "r_net": round(p.r_net(bps), 2), "mfe_r": round(p.mfe_r, 2), "mfe250_r": None if not math.isfinite(p.mfe250_r) else round(p.mfe250_r, 2),
             "segment": p.segment, "path_discontinuity": p.path_disc} for p in sorted(ps, key=lambda q: (q.sym, q.signal_date))]


def run(data_dir: str, out_dir: str, code_ref: str, n_boot: int = 1000, symbols: List[str] = None) -> Dict[str, Any]:
    t0 = time.time()
    spec = SP.get_spec()
    uni = S.load_extract(data_dir)
    syms = sorted(symbols) if symbols else sorted(uni.bars)
    sd = {}
    for s in syms:
        x = S.prepare_symbol(s, uni.bars[s], uni.disc.get(s, []), spec)
        if x is not None:
            sd[s] = x
    mk = S.build_market(sd)
    sim = S.Simulator(spec, sd, mk)
    prim_bps = float(spec["costs_bps_round_trip"]["primary"])
    costs = [prim_bps] + [float(b) for b in spec["costs_bps_round_trip"]["sensitivity"]]
    sets, m_cells = spec["entries"]["sets"], len(spec["entries"]["sets"]) * len(S.EXITS)
    res: Dict[str, Any] = {"schema": "right_tail_result_v1", "spec_hash": SP.spec_hash(), "data_hash": uni.data_hash, "extract_meta": dict(uni.meta), "code_ref": code_ref, "seed": SEED,
                           "n_boot": n_boot, "symbols_loaded": len(uni.bars), "symbols_used": len(sd), "cells": {}, "sensitivity": {}, "skips": {}, "portfolio": {}, "regime": {}, "years": {}}
    all_pos: Dict[str, List[S.Pos]] = {}
    for si, set_name in enumerate(sets):
        for ei, kind in enumerate(S.EXITS):
            counts: Counter = Counter()
            ps = S.account(sim, set_name, kind, counts)
            all_pos[f"{set_name}|{kind}"] = ps
            res["skips"][f"{set_name}|{kind}"] = dict(sorted(counts.items()))
            for seg in ("discovery", "validation"):
                sel = [p for p in ps if p.segment == seg]
                for bps in costs:
                    res["cells"][f"{set_name}|{kind}|{seg}|{int(bps)}bps"] = S.cell_stats(sel, bps, n_boot, SEED + 31 * si + ei, m_cells)
                inc = [p for p in sel if not p.out.censored]
                r = np.array([p.r_net(prim_bps) for p in inc]) if inc else np.array([])
                res["sensitivity"][f"{set_name}|{kind}|{seg}"] = {
                    "include_path_discontinuities": {"n": len(r), "expectancy_r": round(float(r.mean()), 4) if len(r) else None,
                                                     "share_ge_5R": round(float((r >= 5).mean()), 4) if len(r) else None},
                    "censored_marked_to_last_close": S.censored_marked(sel, prim_bps)}
            res["portfolio"][f"{set_name}|{kind}"] = S.portfolio([p for p in ps if p.segment in ("discovery", "validation", "purged")], sd, mk, prim_bps,
                                                                 spec["portfolio"]["max_concurrent"], spec["portfolio"]["risk_per_position_pct_of_initial_equity"])
        print(f"  {set_name} done ({time.time() - t0:.0f}s)", file=sys.stderr)

    # primary cell, its random-entry control and the decision
    prim = all_pos[f"{PRIMARY_SET}|{PRIMARY_EXIT}"]
    rnd = S.random_controls(sim, prim, PRIMARY_EXIT, SEED)
    p_cells = {seg: res["cells"][f"{PRIMARY_SET}|{PRIMARY_EXIT}|{seg}|{int(prim_bps)}bps"] for seg in ("discovery", "validation")}
    for seg in ("discovery", "validation"):
        p_cells[f"random_{seg}"] = S.cell_stats([p for p in rnd if p.segment == seg], prim_bps, n_boot, SEED + 900, 1)
    diffs = {}
    for seg in ("discovery", "validation"):
        a = S.usable([p for p in prim if p.segment == seg])
        b = S.usable([p for p in rnd if p.segment == seg])
        av, am = S.series(a, prim_bps)
        bv, bm = S.series(b, prim_bps)
        diffs[seg] = S.block_diff_ci(av, am, bv, bm, n_boot, SEED + 901)
    verdict, checks = S.decide(p_cells, diffs["validation"])
    res["primary"] = {"set": PRIMARY_SET, "exit": PRIMARY_EXIT, "cost_bps": prim_bps, "cells": p_cells, "minus_random": diffs, "verdict": verdict, "decision_checks": checks}

    # random-entry baselines for every exit (context: what any liquid long entry with D55's stop does)
    for kind in S.EXITS:
        rr = S.random_controls(sim, all_pos[f"D55|{kind}"], kind, SEED + 1)
        for seg in ("discovery", "validation"):
            res["cells"][f"RANDOM(D55)|{kind}|{seg}|{int(prim_bps)}bps"] = S.cell_stats([p for p in rr if p.segment == seg], prim_bps, n_boot, SEED + 950, 1)

    # descriptive strata (no selection): regime, breadth, year
    for key in (f"{PRIMARY_SET}|{PRIMARY_EXIT}", f"D55|{PRIMARY_EXIT}", f"BASE|{PRIMARY_EXIT}", "D55|X1_stop_only", "D55|X2_atr_trail"):
        ps = all_pos[key]
        res["regime"][key] = {seg: {"regime": S.by_bucket([p for p in ps if p.segment == seg], prim_bps, lambda p: "regime_on" if p.regime_on else "regime_off"),
                                    "breadth": S.by_bucket([p for p in ps if p.segment == seg], prim_bps, S.breadth_bucket)} for seg in ("discovery", "validation")}
        res["years"][key] = S.by_bucket(ps, prim_bps, lambda p: str(p.signal_date.year))
    res["winners_vs_rest_discovery"] = {k: winners_vs_rest(all_pos[k], prim_bps) for k in (f"D55|{PRIMARY_EXIT}", f"BASE|{PRIMARY_EXIT}", "D55|X1_stop_only")}
    res["top_trades"] = {k: case_rows(sorted(S.usable(all_pos[k]), key=lambda p: -p.r_net(prim_bps))[:15], prim_bps) for k in (f"{PRIMARY_SET}|{PRIMARY_EXIT}", f"D55|{PRIMARY_EXIT}")}
    res["case_studies"] = {c: case_rows([p for k in (f"{PRIMARY_SET}|{PRIMARY_EXIT}", f"D55|{PRIMARY_EXIT}", "D55|X1_stop_only") for p in all_pos[k] if p.sym == c], prim_bps)
                           for c in CASES if c in sd}
    res["market"] = {"dates": [mk.dates[0].isoformat(), mk.dates[-1].isoformat()], "regime_on_share": round(float(mk.regime_on.mean()), 4)}
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, indent=1, sort_keys=True, default=str)
    print(f"done in {time.time() - t0:.0f}s: verdict {verdict}", file=sys.stderr)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research.right_tail")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--data", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--code-ref", required=True)
    r.add_argument("--n-boot", type=int, default=1000)
    r.add_argument("--symbols", nargs="*")
    a = ap.parse_args(argv)
    run(a.data, a.out, a.code_ref, a.n_boot, a.symbols)
    return 0


if __name__ == "__main__":
    sys.exit(main())
