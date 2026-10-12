"""Slice 9 sector-coverage laboratory: a pure, deterministic, database-free simulation that drives the REAL dataset assembler
(`research.lab.dataset_assemble.assemble`) and the REAL readiness data checks (`dataset_readiness.assess_data`) over synthetic raw rows in which
a chosen share of candidates has no sector.

It exists to answer "what information does a model or researcher actually receive at 100/95/90/89/75/50 % sector coverage, and does it matter
whether the missing cells are scattered or clustered", NOT to tune a threshold. The data are synthetic: they characterise the contract's
behaviour, never the vendor's. (Explicit imports only; never a conftest name.)

Run it directly to print the table the Slice 9 design document quotes:  python sector_coverage_sim.py
"""
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from statistics import mean

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
for p in (os.path.join(ROOT, "mechanism"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from dataset_world import PRIMARY, STRATEGY, config, make_spec  # noqa: E402
from lab_samples import CAL, MAT, NOW, TE, TR, VA  # noqa: E402
from research.lab import dataset_assemble as A  # noqa: E402
from research.lab import dataset_contract as C  # noqa: E402
from research.lab import dataset_readiness as RY  # noqa: E402
from research.lab import manifest as M  # noqa: E402
from research.lab.manifest import assign_split  # noqa: E402

N_SYMBOLS = 100
SECTORS = ("Technology", "Energy", "Health Care", "Financials")
SESSION_IDX = tuple(range(0, 200, 8)) + tuple(range(260, 360, 8)) + tuple(range(420, 520, 8))
SYMBOLS = tuple(f"S{i:03d}" for i in range(N_SYMBOLS))
SECTOR_OF = {s: SECTORS[i % len(SECTORS)] for i, s in enumerate(SYMBOLS)}
LEVELS = (1.00, 0.95, 0.90, 0.89, 0.75, 0.50)

NO_SECTOR, VALUE_UNAVAILABLE, SNAPSHOT_ABSENT = "no_sector", "value_unavailable", "sector_snapshot_absent"


def at(idx, hour=21, minute=0):
    d = CAL[idx]
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc)


def _cfg_doc():
    doc = config(universe_members=list(SYMBOLS))
    doc.pop("catalyst"), doc.pop("first_seen")
    return doc


MANIFEST = M.build_manifest(make_spec({"labels": "b" * 64}, _cfg_doc()), now=NOW)
CFG = C.config_for(MANIFEST.document)
# Sessions whose primary-horizon row really lands in train / validation / test (the others sit in an embargo gap and are dropped). Masks cover
# ONLY these, so the coverage a scenario names is the coverage the readiness check measures.
KEPT = tuple(k for k, idx in enumerate(SESSION_IDX) if assign_split(MANIFEST, CAL[idx], PRIMARY) in ("train", "validation", "test"))


# ------------------------------------------------------------------ missingness masks
def cells():
    return [(s, k) for k in KEPT for s in SYMBOLS]


def mask(shape, observed_fraction, seed=7, order=None):
    """The set of (symbol, session position) cells that LACK a sector. Exactly round((1-f)*N) cells (or whole symbols / sessions)."""
    rng = random.Random(seed)
    n_sess = len(KEPT)
    if shape == "random":
        allc = cells()
        return set(rng.sample(allc, round((1 - observed_fraction) * len(allc))))
    if shape == "symbol_cluster":
        m = round((1 - observed_fraction) * N_SYMBOLS)
        chosen = list(order[-m:] if order and m else []) if order else rng.sample(SYMBOLS, m)
        return {(s, k) for s in chosen for k in KEPT}
    m = round((1 - observed_fraction) * n_sess)
    if shape == "session_scatter":
        ks = rng.sample(KEPT, m)
    elif shape == "outage_early":
        ks = KEPT[:m]
    elif shape == "outage_late":
        ks = KEPT[n_sess - m:]
    else:
        raise ValueError(shape)
    return {(s, k) for k in ks for s in SYMBOLS}


# ------------------------------------------------------------------ the synthetic raw world
def raw_world(missing=frozenset(), kind=NO_SECTOR, return_of=None):
    """Raw rows exactly as the reader would hand them to `assemble`. `kind` says HOW a masked cell lacks its sector:
    no_sector          the candidate snapshot has no sector and the RS row has none (vendor gave nothing) -- vs_sector_pp NULL, market-relative kept
    value_unavailable  the sector is known and fresh but the RS model produced no sector-relative value (too few members)
    sector_snapshot_absent  the sector aggregate snapshot of that session is absent for that sector (the candidate and RS rows are fine)"""
    cands, labels, rs = [], [], []
    for k, idx in enumerate(SESSION_IDX):
        for i, s in enumerate(SYMBOLS):
            miss = (s, k) in missing
            has_sector = not (miss and kind == NO_SECTOR)
            sec = SECTOR_OF[s]
            cands.append({"strategy_key": STRATEGY[0], "strategy_version": STRATEGY[1], "symbol": s, "session_date": CAL[idx], "direction": 1,
                          "signal_type": "bullish_breakout", "triggered": True, "passed_guard": True, "tracked_intent": True,
                          "atr_source": "measured", "breakout_dist_atr": 0.5, "distance_to_channel_pct": 1.0, "quality_grade": "A",
                          "alignment_score": 60, "captured_at": at(idx),
                          "fs_sector": sec if has_sector else None, "fs_sector_source": "test_map" if has_sector else None,
                          "fs_sector_asof": CAL[idx] if has_sector else None, "fs_captured_at": at(idx)})
            rs.append({"session_date": CAL[idx], "symbol": s, "horizon_sessions": PRIMARY, "model_version": "rs_v1", "feature_set_version": "mi_v2",
                       "provenance": "observed", "sector": sec if has_sector else None, "sector_pit_safe": has_sector, "state": "ok",
                       "ret_pct": (idx + i) % 13 - 6.0, "vs_spx_pp": (idx + i) % 9 - 4.0,
                       "vs_sector_pp": None if (not has_sector or (miss and kind == VALUE_UNAVAILABLE)) else (idx + i) % 7 - 3.0,
                       "rs_percentile": float((idx * 13 + i * 29) % 101), "n_universe_valid": N_SYMBOLS, "run_content_hash": f"rs{idx}",
                       "created_at": at(idx, 21, 30)})
            for h in (5, 20, 60):
                hz = idx + h
                r = return_of(i, idx, h) if return_of else ((idx * 7 + i * 3 + h) % 21 - 10) / 200.0
                labels.append({"strategy_key": STRATEGY[0], "strategy_version": STRATEGY[1], "symbol": s, "t0_session": CAL[idx], "direction": 1,
                               "horizon_sessions": h, "label_version": "fwd_v1", "methodology_version": "fwd_v1.m1", "label_status": "final",
                               "void_reason": None, "horizon_session": CAL[hz], "computed_as_of_session": CAL[hz], "calendar_source": "cal",
                               "raw_return": r, "directional_return": r, "benchmark_state": "ok", "directional_excess_return": r - 0.01,
                               "path_state": "complete", "mfe": 0.04, "mae": -0.03, "data_quality": "ok", "input_hash": f"l{s}{idx}{h}",
                               "computed_at": at(hz, 22)})
    market = [{"session_date": CAL[idx], "provenance": "observed", "feature_set_version": "mi_v2", "regime_model_version": "risk_regime_v1",
               "rs_model_version": "rs_v1", "regime_state": "RISK_ON", "regime_score": 0.4, "regime_strength": 0.3,
               "regime_components": {"c3_breadth_sma50": {"present": True, "value": 50.0}, "c4_breadth_sma200": {"present": True, "value": 45.0},
                                     "c5_net_highs_lows": {"present": True, "value": 1.0}},
               "content_hash": f"m{idx}", "captured_at": at(idx)} for idx in SESSION_IDX]
    sector = []
    for k, idx in enumerate(SESSION_IDX):
        for si, name in enumerate(SECTORS):
            if kind == SNAPSHOT_ABSENT and (SYMBOLS[si], k) in missing:
                continue                                                  # the sector aggregate of that session is absent
            sector.append({"session_date": CAL[idx], "provenance": "observed", "feature_set_version": "mi_v2", "sector": name, "n_valid_20": 25,
                           "sec_ret_20": (idx + si) % 7 - 3.0, "sec_vs_spx_20": (idx + si) % 5 - 2.0, "sec_vs_univ_20": (idx + si) % 3 - 1.0,
                           "rank_20": si + 1, "captured_at": at(idx)})
    return {"candidates": cands, "labels": labels, "market": market, "sector": sector, "stock_rs": rs}


def primary_rows(raw):
    asm = A.assemble(MANIFEST, CFG, raw)
    return [r for r in asm.rows if r["horizon_sessions"] == PRIMARY]


def assess(prim):
    return RY.assess_data(cfg=CFG, windows_train_end=TR[1].isoformat(), maturity=MAT.isoformat(), prim=prim)


# ------------------------------------------------------------------ measurements
def _share(counter, top):
    total = sum(counter.values())
    return None if total == 0 else round(sum(sorted(counter.values(), reverse=True)[:top]) / total, 4)


def measure(prim):
    d = assess(prim)
    n = len(prim)
    lacking = [r for r in prim if r["rs_vs_sector__state"] != C.OK]
    by_sym, by_day = {}, {}
    for r in lacking:
        by_sym[r["symbol"]] = by_sym.get(r["symbol"], 0) + 1
        by_day[r["t0_session"]] = by_day.get(r["t0_session"], 0) + 1
    per_day = {}
    for r in prim:
        per_day[r["t0_session"]] = per_day.get(r["t0_session"], 0) + 1
    per_sym = {}
    for r in prim:
        per_sym[r["symbol"]] = per_sym.get(r["symbol"], 0) + 1
    n_days = len(per_day)
    market_rel = sum(1 for r in prim if r["rs__state"] == C.OK and r["rs_vs_spx_pp"] is not None)
    return {
        "rows": n,
        "sector_context_observed": d["coverage"]["sector"]["observed_fraction"],
        "sector_relative_usable": d["sector_relative"]["observed_sector_relative_fraction"],
        "market_relative_rs": round(market_rel / n, 6),
        "rows_without_sector_relative": len(lacking),
        "symbols_affected": len(by_sym), "symbols_fully_affected": sum(1 for s, c in by_sym.items() if c == per_sym[s]),
        "sessions_affected": len(by_day), "sessions_fully_affected": sum(1 for t, c in by_day.items() if c == per_day[t]),
        "worst_session_missing_fraction": max((by_day.get(t, 0) / per_day[t] for t in per_day), default=0.0),
        "share_of_gaps_in_worst_10pct_sessions": _share(by_day, max(1, n_days // 10)),
        "share_of_gaps_in_worst_10_symbols": _share(by_sym, 10),
        "sector_relative_states": d["sector_relative"]["states"],
        "failed_checks": sorted(c["id"] for c in d["checks"] if not c["passed"]),
        "earliest_trustworthy_pit_date": d["earliest"]["all_sources"],
        "mean_directional_return_all": round(mean(r["directional_return"] for r in prim), 5),
        "mean_directional_return_with_sector_relative": round(mean(r["directional_return"] for r in prim if r["rs_vs_sector__state"] == C.OK), 5)
        if any(r["rs_vs_sector__state"] == C.OK for r in prim) else None,
    }


def run(shape, f, kind=NO_SECTOR, **kw):
    return measure(primary_rows(raw_world(mask(shape, f, **kw), kind)))


def small_company_return(i, idx, h):
    """Planted structure for the bias demonstration ONLY: the 10 highest-numbered ('smallest') symbols have a lower forward return."""
    base = ((idx * 7 + i * 3 + h) % 21 - 10) / 200.0
    return base - 0.02 if i >= N_SYMBOLS - 10 else base


def main():
    print(f"{'shape':16}{'kind':24}{'target':>7}{'ctx':>8}{'sec-rel':>8}{'mkt-rel':>8}{'rows':>6}{'sym':>5}{'fullsym':>8}{'sess':>5}{'worst10%s':>10}{'top10sym':>9}  failed")
    for shape in ("random", "symbol_cluster", "session_scatter", "outage_early", "outage_late"):
        for f in LEVELS:
            m = run(shape, f)
            print(f"{shape:16}{NO_SECTOR:24}{f:7.2f}{m['sector_context_observed']:8.4f}{m['sector_relative_usable']:8.4f}{m['market_relative_rs']:8.4f}"
                  f"{m['rows_without_sector_relative']:6}{m['symbols_affected']:5}{m['symbols_fully_affected']:8}{m['sessions_affected']:5}"
                  f"{m['share_of_gaps_in_worst_10pct_sessions'] or 0:10.3f}{m['share_of_gaps_in_worst_10_symbols'] or 0:9.3f}  {m['failed_checks']}"
                  f"  earliest={m['earliest_trustworthy_pit_date']}")
    for kind in (VALUE_UNAVAILABLE, SNAPSHOT_ABSENT):
        for f in (0.90, 0.75, 0.50):
            m = run("random", f, kind)
            print(f"{'random':16}{kind:24}{f:7.2f}{m['sector_context_observed']:8.4f}{m['sector_relative_usable']:8.4f}{m['market_relative_rs']:8.4f}"
                  f"{m['rows_without_sector_relative']:6}{m['symbols_affected']:5}{m['symbols_fully_affected']:8}{m['sessions_affected']:5}  {m['failed_checks']}")
    order = list(SYMBOLS)
    for label, shape, kw in (("planted: small symbols missing", "symbol_cluster", {"order": order}), ("planted: random 10%", "random", {})):
        raw = raw_world(mask(shape, 0.90, **kw), NO_SECTOR, return_of=small_company_return)
        m = measure(primary_rows(raw))
        print(label, m["sector_context_observed"], m["failed_checks"], m["mean_directional_return_all"], m["mean_directional_return_with_sector_relative"])


if __name__ == "__main__":
    main()
