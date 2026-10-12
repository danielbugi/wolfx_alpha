"""Strategy experiment runner (research only): reproducible historical experiments over the REGISTERED strategy variants. PURE: numpy + stdlib; no database, clock, network or environment.

What it reuses, not re-implements:
  * the five immutable variant identities (key, version, definition hash) from `research.strategy_variants` (the same specs that were registered in `strategy_variant`);
  * the exit engine `research.exit_policies` (`simulate`, `aggregate`): one source of truth, shared with the forward paper tracker;
  * the live signal ledger's position rule: at most ONE open position per (symbol, strategy, direction) — here per (variant, hold cap, symbol, direction), so every variant keeps its OWN
    independent position accounting (a variant's results never depend on which other variants ran);
  * `research.lab.manifest.canonical_hash` for the experiment manifest hash.

Conventions (all stated in the manifest): the signal is the parent strategy's breakout at the close of bar s; entry is the OPEN of bar s+1; costs are round-trip bps charged in R; a trade is
CENSORED (excluded, counted) when the price history ends before its hold cap; chronological splits use the SIGNAL date, an embargo gap, and a PURGE: a train signal whose position is still open at
the start of the test period is dropped from train (its label would otherwise overlap the test period).

Two risk bases are reported side by side: the STANDARDISED unit (1R = 2 x ATR14, identical for every variant) and the ACTUAL initial risk of the variant (where its protective level really sat at entry:
B starts at the 10-bar channel low, C at 3 x ATR). Longer hold caps than the registered 60 are research conditions, not registered identities, and are labelled so.
"""
from __future__ import annotations

import dataclasses
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from research import exit_policies as X
from research import strategy_variants as SV
from research.lab.manifest import canonical_hash

REGISTERED_HOLD = X.DEFAULT_MAX_HOLD          # the hold cap inside every registered definition (max_hold_bars = 60)
PIT_CATALYST_MAX_DAYS = 15


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ identities
@dataclass(frozen=True)
class Variant:
    key: str
    version: str
    definition_hash: str
    policy_name: str                 # the exit policy (a key of exit_policies.POLICIES)
    registered_max_hold: int

    def label(self) -> str:
        return f"{self.key} {self.version} {self.definition_hash[:10]}"


def registered_variants(parent_key: str = SV.DEFAULT_PARENT[0], parent_version: str = SV.DEFAULT_PARENT[1]) -> List[Variant]:
    """The variants exactly as `research.strategy_variants` defines (and production stores) them; every hash is recomputed from the definition before it is returned."""
    out = []
    for s in SV.build_specs(parent_key, parent_version):
        if SV.definition_hash(s.definition) != s.definition_hash:
            raise SV.VariantError(f"{s.key}: definition hash does not match its definition")
        out.append(Variant(s.key, s.version, s.definition_hash, s.definition["exit"]["policy_name"], int(s.definition["exit"]["max_hold_bars"])))
    return out


def identity_mismatches(variants: Sequence[Variant], stored_rows: Iterable[Tuple[str, str, str]]) -> List[str]:
    """Compare the code's identities with rows read from the registry, (key, version, hash) each. Empty = every variant is stored with exactly this hash."""
    stored = {(k, v): h.strip() for k, v, h in stored_rows}
    problems = []
    for v in variants:
        h = stored.get((v.key, v.version))
        if h is None:
            problems.append(f"{v.key} {v.version}: not in the registry")
        elif h != v.definition_hash:
            problems.append(f"{v.key} {v.version}: registry hash {h[:12]} differs from the code's {v.definition_hash[:12]}")
    return problems


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ manifest
def build_manifest(*, variants: Sequence[Variant], hold_caps: Sequence[int], cost_bps: Sequence[float], primary_cost_bps: float, train_end: date, test_start: date,
                   universe: Mapping[str, Any], signal_source: Mapping[str, Any], code_ref: str) -> Dict[str, Any]:
    if train_end > test_start:
        raise ValueError("train_end must not be after test_start")
    doc = {
        "schema": "strategy_experiment_manifest_v1",
        "variants": [{"key": v.key, "version": v.version, "definition_hash": v.definition_hash, "policy": v.policy_name} for v in variants],
        "hold_caps": [int(c) for c in hold_caps], "registered_hold_cap": REGISTERED_HOLD,
        "cost_bps_round_trip": [float(c) for c in cost_bps], "primary_cost_bps": float(primary_cost_bps),
        "split": {"basis": "signal date", "train_end_exclusive": train_end.isoformat(), "test_start_inclusive": test_start.isoformat(),
                  "purge": "a train signal whose position is still open at test_start is dropped from train"},
        "execution": {"entry": "open of the bar after the signal", "one_open_position_per": "variant, hold cap, symbol, direction (the live ledger's rule); a signal on or before the previous exit bar is skipped",
                      "risk_bases": ["standardised: 1R = 2 x ATR14", "actual: distance from entry to the variant's own initial stop"],
                      "censoring": "a trade whose price history ends before its hold cap is excluded and counted"},
        "universe": dict(universe), "signal_source": dict(signal_source), "code_ref": code_ref,
    }
    return {"document": doc, "manifest_hash": canonical_hash(doc)}


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ point-in-time inputs
def signal_features(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray, atr: np.ndarray, i: int) -> Dict[str, float]:
    """Features of signal bar i computed ONLY from bars <= i (the signal is generated after bar i closes). NaN when the history is too short."""
    nan = float("nan")
    rel = float(v[i] / np.nanmean(v[i - 20:i])) if i >= 20 and np.nanmean(v[i - 20:i]) > 0 else nan
    ret20 = float(c[i] / c[i - 20] - 1) if i >= 20 and c[i - 20] > 0 else nan
    atr_pct = float(atr[i] / c[i] * 100) if c[i] > 0 and math.isfinite(float(atr[i])) else nan
    ret5 = float(c[i] / c[i - 5] - 1) if i >= 5 and c[i - 5] > 0 else nan
    return {"relvol_signal_bar": rel, "ret20_to_signal": ret20, "ret5_to_signal": ret5, "atr_pct": atr_pct}


def catalyst_as_of(events: Sequence[Mapping[str, Any]], signal_date: date, max_days: int = PIT_CATALYST_MAX_DAYS) -> Optional[Dict[str, Any]]:
    """The most recent earnings event that was already PUBLISHED and had already REACTED before the signal session: reaction_date strictly before signal_date, within `max_days` calendar days.
    An event whose SEC acceptance timestamp falls after its own reaction date is internally inconsistent and is never used. `events` carry reaction_date (date), nature, decision_accepted_at (ISO, UTC)."""
    best = None
    for e in events:
        rd: date = e["reaction_date"]
        if rd >= signal_date:
            continue
        if (signal_date - rd).days > max_days:
            continue
        accepted = datetime.fromisoformat(str(e["decision_accepted_at"]).replace("Z", "+00:00"))
        if accepted.date() > rd:
            continue
        if best is None or rd > best["reaction_date"]:
            best = {"nature": e["nature"], "reaction_date": rd, "days_since": (signal_date - rd).days, "decision_accepted_at": str(e["decision_accepted_at"]),
                    "release_accession": e.get("release_accession")}
    return best


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ simulation + accounting
@dataclass(frozen=True)
class Position:
    """One row of position-level accounting: a variant's position opened by one signal."""
    variant: Variant
    hold_cap: int
    symbol: str
    direction: int
    signal_idx: int
    signal_date: date
    entry_date: date
    exit_date: date
    trade: X.Trade
    censored: bool

    @property
    def registered_cap(self) -> bool:
        return self.hold_cap == self.variant.registered_max_hold


def is_censored(t: X.Trade, cap: int) -> bool:
    """The history ended before the cap and before any exit rule fired: the outcome is not final."""
    return t.reason == "time" and t.duration < cap


def simulate_symbol(variants: Sequence[Variant], hold_caps: Sequence[int], symbol: str, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray,
                    dates: Sequence[date], atr: np.ndarray, signals: Sequence[Tuple[int, int]]) -> Tuple[Dict[Tuple[str, int], List[Optional[X.Trade]]], List[Position]]:
    """Run every (variant, hold cap) over one symbol's signals, each in its own accounting.
    `signals` = (signal bar index, direction), any order. Returns
      standalone[(variant_key, cap)][k]  the trade the k-th signal (in index order) would produce ON ITS OWN, or None if it cannot be entered (the counterfactual used for strategy-selection tables);
      positions                          the position-level accounting with the one-open-position rule applied per (variant, cap, direction)."""
    ordered = sorted(signals)
    standalone: Dict[Tuple[str, int], List[Optional[X.Trade]]] = {}
    positions: List[Position] = []
    for v in variants:
        for cap in hold_caps:
            last_exit: Dict[int, int] = {}
            row: List[Optional[X.Trade]] = []
            for s_idx, d in ordered:
                t = X.simulate(v.policy_name, o, h, l, c, s_idx, d, float(atr[s_idx]), max_hold=int(cap), cost_bps=0.0)
                row.append(t)
                if t is None:
                    continue
                if s_idx <= last_exit.get(d, -1):                 # a position of this variant/direction is still open (or closes on this very bar): skipped, as the ledger skips it
                    continue
                last_exit[d] = t.exit_bar
                positions.append(Position(v, int(cap), symbol, d, s_idx, dates[s_idx], dates[t.entry_bar], dates[t.exit_bar], t, is_censored(t, int(cap))))
            standalone[(v.key, int(cap))] = row
    return standalone, positions


def assign_split(signal_date: date, exit_date: date, train_end: date, test_start: date) -> str:
    if signal_date >= test_start:
        return "test"
    if signal_date >= train_end:
        return "embargo"
    return "purged" if exit_date >= test_start else "train"


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ metrics
def net_r(t: X.Trade, basis: str, cost_bps: float) -> float:
    """Realised result of a trade net of a round-trip cost, in the standardised unit ('std') or the variant's actual initial risk ('actual', NaN when that risk is not positive)."""
    if basis == "std":
        return t.r_gross - (cost_bps / 1e4) * t.entry_price / t.unit
    if basis == "actual":
        return t.r_actual_net(cost_bps)
    raise ValueError(basis)


def _proxy(t: X.Trade, basis: str, cost_bps: float) -> X.Trade:
    if basis == "std":
        return dataclasses.replace(t, r_net=net_r(t, "std", cost_bps))
    return dataclasses.replace(t, r_net=net_r(t, "actual", cost_bps), mfe_r=t.mfe_actual_r, mae_r=t.mae_actual_r)


def summarize(positions: Sequence[Position], basis: str, cost_bps: float) -> Dict[str, Any]:
    """Position-level metrics for ONE variant's accounted positions (censored ones excluded): expectancy, win rate, profit factor, duration, MFE/MAE, drawdown of the cumulative-R curve, exit reasons.
    On the 'actual' basis positions whose initial risk is not positive are excluded and counted; the mean is also shown winsorised at the 1st/99th percentile because tiny actual risks give huge multiples."""
    use = [p for p in positions if not p.censored]
    out: Dict[str, Any] = {"n_positions": len(use), "n_censored": sum(1 for p in positions if p.censored)}
    if basis == "actual":
        valid = [p for p in use if p.trade.risk_valid]
        out["n_invalid_risk"] = len(use) - len(valid)
        use = valid
    if not use:
        return out
    proxies = [_proxy(p.trade, basis, cost_bps) for p in use]
    out.update(X.aggregate(proxies, [np.datetime64(p.exit_date) for p in use]))
    if basis == "actual":
        r = np.array([t.r_net for t in proxies], dtype=float)
        lo, hi = np.percentile(r, [1, 99])
        out["winsorised_mean_r"] = round(float(np.clip(r, lo, hi).mean()), 4)
        ratios = np.array([p.trade.risk_atr_multiple for p in use], dtype=float)
        out["initial_risk_atr"] = {"median": round(float(np.median(ratios)), 3), "p10": round(float(np.percentile(ratios, 10)), 3), "p90": round(float(np.percentile(ratios, 90)), 3),
                                   "share_below_0_5_atr": round(float((ratios < 0.5).mean()), 4), "share_above_3_atr": round(float((ratios > 3.0).mean()), 4)}
    return out


def series(positions: Sequence[Position], basis: str, cost_bps: float) -> Tuple[np.ndarray, np.ndarray]:
    """(realised net R, 'YYYY-MM' of exit) of the usable positions, for month-block bootstrap intervals computed by the caller."""
    vals, months = [], []
    for p in positions:
        if p.censored:
            continue
        r = net_r(p.trade, basis, cost_bps)
        if math.isfinite(r):
            vals.append(r)
            months.append(p.exit_date.strftime("%Y-%m"))
    return np.array(vals, dtype=float), np.array(months)


def paired_differences(a: Sequence[Optional[X.Trade]], b: Sequence[Optional[X.Trade]], cap_a: int, cap_b: int, basis: str, cost_bps: float,
                       months: Sequence[str]) -> Tuple[np.ndarray, np.ndarray]:
    """Per-signal difference (a - b) of net R over the signals both could take and neither is censored, with each signal's month: the paired comparison of two variants or two hold caps."""
    d, m = [], []
    for x, y, mo in zip(a, b, months):
        if x is None or y is None or is_censored(x, cap_a) or is_censored(y, cap_b):
            continue
        rx, ry = net_r(x, basis, cost_bps), net_r(y, basis, cost_bps)
        if math.isfinite(rx) and math.isfinite(ry):
            d.append(rx - ry)
            m.append(mo)
    return np.array(d, dtype=float), np.array(m)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ strategy-selection baselines
def selection_baselines(R: np.ndarray, groups: Sequence[str], split: Sequence[str], names: Sequence[str], *, min_group_train: int = 50) -> Dict[str, Any]:
    """Chronological baselines for choosing a variant per signal from information available at signal time. R[i, j] = realised net R of signal i under variant j taken on its own (NaN = unavailable).
    A rule is fitted on TRAIN rows only and judged on TEST rows: always-variant-j; the best single variant on train; the best variant per group on train (a group with too few train rows falls back to
    the global best); and the per-signal oracle (an unattainable upper bound shown only to bound the possible gain)."""
    R = np.asarray(R, dtype=float)
    split = np.asarray(split)
    groups = np.asarray(groups)
    ok = np.isfinite(R).all(axis=1)
    tr, te = ok & (split == "train"), ok & (split == "test")
    out: Dict[str, Any] = {"n_train": int(tr.sum()), "n_test": int(te.sum()), "variants": list(names)}
    if tr.sum() == 0 or te.sum() == 0:
        return out
    out["fixed_variant_test_mean"] = {n: round(float(R[te, j].mean()), 4) for j, n in enumerate(names)}
    out["fixed_variant_train_mean"] = {n: round(float(R[tr, j].mean()), 4) for j, n in enumerate(names)}
    best_global = int(np.argmax(R[tr].mean(axis=0)))
    out["best_single_variant_on_train"] = names[best_global]
    chosen = np.full(len(R), best_global)
    group_pick: Dict[str, str] = {}
    for g in sorted(set(groups[tr])):
        rows = tr & (groups == g)
        if rows.sum() >= min_group_train:
            chosen[groups == g] = int(np.argmax(R[rows].mean(axis=0)))
            group_pick[str(g)] = names[int(chosen[groups == g][0])]
    realised_group = R[np.arange(len(R)), chosen]
    out["group_best_pick_on_train"] = group_pick
    out["rules_test_mean"] = {"best_single_variant": round(float(R[te, best_global].mean()), 4), "best_variant_per_group": round(float(realised_group[te].mean()), 4),
                              "per_signal_oracle_UPPER_BOUND": round(float(R[te].max(axis=1).mean()), 4)}
    out["_test_series"] = {"best_single_variant": R[te, best_global], "best_variant_per_group": realised_group[te]}   # for paired intervals by the caller
    return out
