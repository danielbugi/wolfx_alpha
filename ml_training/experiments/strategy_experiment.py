"""Strategy experiment (isolated research run; read-only database session; writes /out/strategy_experiment.json and /out/strategy_signal_outcomes.csv.gz).

The five REGISTERED strategy variants (key, version, definition hash) are run over the parent strategy's historical breakout signals with `research.experiment_runner` (independent per-variant position
accounting, one open position per variant/direction/hold cap, chronological train/test with embargo and purge, costs, standardised vs actual initial risk, three hold caps) and the forward paper tracker
(`research.paper_tracker`) is validated by replaying real histories day by day against the batch results. Nothing here writes to any database or opens a trade.

Universe: the 391 liquid symbols of the SEC earnings study (so every signal has point-in-time catalyst coverage), signals of `ml_breakout_dataset_v2` from 2017-03-01. The analysis set keeps signals whose full
longest-cap window exists, has finite bars and contains no recorded price discontinuity; every other signal is counted by reason. Catalysts come from the committed SEC event table with their SEC
acceptance timestamps: only events whose reaction session completed BEFORE the signal session are visible.

Needs env: DB_* (read-only), REGISTRY_ROWS (path to a JSON list of [key, version, hash] rows read from the production registry), CODE_REF.
"""
import gzip
import hashlib
import json
import os
import sys
import time
import warnings
from collections import Counter
from datetime import date, datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path[:0] = ["/app", "/app/mechanism", "/exp"]
from ml_training.features import price_features as pf  # noqa: E402
from research import exit_policies as X  # noqa: E402
from research import experiment_runner as E  # noqa: E402
from research import paper_tracker as PT  # noqa: E402
import _common as C  # noqa: E402

T0 = time.time()
OUT = "/out"
CAPS = [60, 120, 250]
COSTS = [0.0, 10.0, 25.0]
PRIMARY = 10.0
TRAIN_END, TEST_START = date(2022, 1, 1), date(2022, 2, 15)
SIGNALS_SINCE = date(2017, 3, 1)
SEC_TABLE = "/app/docs/research/evidence/quant_lab_2026-10-11/sec_event_outcomes.csv.gz"
REASONS = ["stop", "target", "channel", "trail", "time"]
N_REPLAY_SYMBOLS = 40
MAXCAP = max(CAPS)


def log(*a):
    print(f"[{time.time() - T0:5.0f}s]", *a, flush=True)


variants = E.registered_variants()
registry_rows = json.load(open(os.environ["REGISTRY_ROWS"], encoding="utf-8"))
mismatches = E.identity_mismatches(variants, [tuple(r) for r in registry_rows])
if mismatches:
    print("REGISTRY IDENTITY MISMATCH, refusing to run:", mismatches, file=sys.stderr)
    sys.exit(3)
log("identities verified against the production registry rows:", [v.label() for v in variants])

# ------------------------------------------------------------------------------------------------------------------------------------------------------------ inputs
sec = pd.read_csv(SEC_TABLE, usecols=["symbol", "reaction_date", "nature", "decision_accepted_at", "release_accession"])
universe = sorted(sec.symbol.unique())
if os.environ.get("EXPERIMENT_MAX_SYMBOLS"):                       # a quick smoke run only; the committed result is always the full universe
    universe = universe[:int(os.environ["EXPERIMENT_MAX_SYMBOLS"])]
events = {}
for r in sec.itertuples(index=False):
    events.setdefault(r.symbol, []).append({"reaction_date": date.fromisoformat(str(r.reaction_date)[:10]), "nature": r.nature, "decision_accepted_at": r.decision_accepted_at,
                                            "release_accession": r.release_accession})
conn = C.connect()
sig = pd.read_sql("SELECT symbol, date, direction FROM ml_breakout_dataset_v2 WHERE feature_set_version = %s AND symbol = ANY(%s) AND date >= %s ORDER BY symbol, date", conn,
                  params=(pf.FEATURE_SET_VERSION, universe, SIGNALS_SINCE))
sig["date"] = pd.to_datetime(sig["date"])
disc = pd.read_sql("SELECT symbol, date FROM price_discontinuities", conn)
disc_by = {}
for s_, d_ in zip(disc.symbol, pd.to_datetime(disc.date)):
    disc_by.setdefault(s_, []).append(np.datetime64(d_))
mk = C.market_panel(conn)
regime = {d.date(): float(v) for d, v in mk["idx_above_200"].items() if v == v}
log("universe", len(universe), "symbols; raw signals", len(sig), "; discontinuity symbols", len(disc_by))

NV, NC = len(variants), len(CAPS)
F = ["r_gross", "entry_price", "exit_price", "duration", "reason", "mfe", "mae", "initial_stop", "initial_risk", "atr", "entry_ord", "exit_ord", "taken"]
FI = {k: i for i, k in enumerate(F)}
skipped = Counter()
meta, tensors = [], []
replay_pool = {}                                   # symbol -> arrays, kept only for the symbols used to validate the paper tracker
replay_symbols = set(sorted(universe, key=lambda s: hashlib.md5(s.encode()).hexdigest())[:N_REPLAY_SYMBOLS])

for si, (sym, g) in enumerate(sig.groupby("symbol")):
    px = pd.read_sql("SELECT date, open, high, low, close, volume FROM stock_prices WHERE symbol = %s ORDER BY date", conn, params=(sym,)).dropna(subset=["close"]).drop_duplicates("date")
    px = px.reset_index(drop=True)
    px["date"] = pd.to_datetime(px["date"])
    for k in ("open", "high", "low", "close", "volume"):
        px[k] = pd.to_numeric(px[k], errors="coerce")
    n = len(px)
    if n < 300:
        skipped["short_history"] += len(g)
        continue
    atr = pf.compute_indicators(px)["atr"].to_numpy(float)
    o, h, l, c, v = (px[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    dts = px["date"].to_numpy()
    pydates = [pd.Timestamp(x).date() for x in dts]
    dd = np.array(disc_by.get(sym, []), dtype="datetime64[ns]")
    keep = []
    for r in g.itertuples(index=False):
        i = int(np.searchsorted(dts, np.datetime64(r.date)))
        if i >= n or dts[i] != np.datetime64(r.date):
            skipped["date_not_in_prices"] += 1
        elif i < 60:
            skipped["history_shorter_than_60_bars"] += 1
        elif i + MAXCAP + 1 >= n:
            skipped["window_shorter_than_longest_cap"] += 1
        elif len(dd) and ((dd >= dts[i - 5]) & (dd <= dts[min(n - 1, i + MAXCAP + 1)])).any():
            skipped["price_discontinuity_in_window"] += 1
        elif not (np.isfinite(o[i - 20:i + MAXCAP + 2]).all() and np.isfinite(h[i - 20:i + MAXCAP + 2]).all() and np.isfinite(l[i - 20:i + MAXCAP + 2]).all()
                  and np.isfinite(c[i - 20:i + MAXCAP + 2]).all() and np.isfinite(atr[i]) and atr[i] > 0):
            skipped["non_finite_bars_or_atr"] += 1
        else:
            keep.append((i, int(r.direction)))
    if not keep:
        continue
    standalone, positions = E.simulate_symbol(variants, CAPS, sym, o, h, l, c, pydates, atr, keep)
    taken = {(p.variant.key, p.hold_cap, p.signal_idx) for p in positions}
    order = sorted(keep)
    T = np.full((len(order), NV, NC, len(F)), np.nan)
    for vi, vv in enumerate(variants):
        for ci, cap in enumerate(CAPS):
            for k, (s_idx, d) in enumerate(order):
                t = standalone[(vv.key, cap)][k]
                if t is None:
                    continue
                T[k, vi, ci] = [t.r_gross, t.entry_price, t.exit_price, t.duration, REASONS.index(t.reason), t.mfe_r, t.mae_r, t.initial_stop, t.initial_risk, t.atr,
                                pydates[t.entry_bar].toordinal(), pydates[t.exit_bar].toordinal(), 1.0 if (vv.key, cap, s_idx) in taken else 0.0]
    tensors.append(T)
    for k, (s_idx, d) in enumerate(order):
        f = E.signal_features(o, h, l, c, v, atr, s_idx)
        cat = E.catalyst_as_of(events.get(sym, []), pydates[s_idx])
        meta.append({"symbol": sym, "signal_date": pydates[s_idx], "direction": d, **f, "regime_up_prev_session": regime.get(pydates[s_idx - 1], float("nan")),
                     "catalyst_class": cat["nature"] if cat else "none", "catalyst_days_since": cat["days_since"] if cat else float("nan"),
                     "catalyst_decision_accepted_at": cat["decision_accepted_at"] if cat else "", "catalyst_release_accession": (cat["release_accession"] or "") if cat else ""})
    if sym in replay_symbols:
        replay_pool[sym] = {"o": o, "h": h, "l": l, "c": c, "atr": atr, "dates": pydates, "signals": order}
    if si % 50 == 0:
        log("symbols", si, "analysis signals", sum(len(t) for t in tensors), "skipped", dict(skipped))

S = np.concatenate(tensors, axis=0)
M = pd.DataFrame(meta)
NS = len(M)
log("analysis set:", NS, "signals;", M.symbol.nunique(), "symbols; skipped", dict(skipped))
assert len(S) == NS
fingerprint = {"n_signals": int(NS), "n_symbols": int(M.symbol.nunique()), "signal_list_md5": hashlib.md5("|".join(f"{a}:{b}:{c_}" for a, b, c_ in zip(M.symbol, M.signal_date, M.direction)).encode()).hexdigest(),
               "skipped_by_reason": dict(skipped), "sec_event_table_sha256": hashlib.sha256(open(SEC_TABLE, "rb").read()).hexdigest()}
manifest = E.build_manifest(variants=variants, hold_caps=CAPS, cost_bps=COSTS, primary_cost_bps=PRIMARY, train_end=TRAIN_END, test_start=TEST_START,
                            universe={"definition": "symbols of the SEC earnings study (391 liquid names)", "n_symbols": int(M.symbol.nunique())},
                            signal_source={"table": "ml_breakout_dataset_v2", "feature_set_version": pf.FEATURE_SET_VERSION, "since": SIGNALS_SINCE.isoformat(),
                                           "analysis_set": "full longest-cap window, finite bars, no price discontinuity in [i-5, i+251]", "fingerprint": fingerprint},
                            code_ref=os.environ.get("CODE_REF", "unknown"))

# ------------------------------------------------------------------------------------------------------------------------------------------------------------ views over the tensor
sig_ord = np.array([d.toordinal() for d in M.signal_date])
direction = M.direction.to_numpy()
train_end_o, test_start_o = TRAIN_END.toordinal(), TEST_START.toordinal()


def split_of(vi, ci):
    """Per-signal split label for one (variant, cap): by signal date, with the purge applied to that variant's own exit date."""
    ex = S[:, vi, ci, FI["exit_ord"]]
    out = np.where(sig_ord >= test_start_o, "test", np.where(sig_ord >= train_end_o, "embargo", np.where(ex >= test_start_o, "purged", "train")))
    return out


def positions_for(vi, ci, mask):
    pos = []
    vv = variants[vi]
    for k in np.flatnonzero(mask & (S[:, vi, ci, FI["taken"]] == 1.0)):
        r = S[k, vi, ci]
        t = X.Trade(vv.policy_name, int(direction[k]), 0, 0, r[FI["entry_price"]], r[FI["exit_price"]], r[FI["r_gross"]], r[FI["r_gross"]], int(r[FI["duration"]]), REASONS[int(r[FI["reason"]])],
                    r[FI["mfe"]], r[FI["mae"]], r[FI["initial_stop"]], r[FI["initial_risk"]], r[FI["atr"]])
        pos.append(E.Position(vv, CAPS[ci], M.symbol.iat[k], int(direction[k]), 0, M.signal_date.iat[k], date.fromordinal(int(r[FI["entry_ord"]])), date.fromordinal(int(r[FI["exit_ord"]])), t, False))
    return pos


def with_ci(positions, basis, cost):
    s = E.summarize(positions, basis, cost)
    vals, months = E.series(positions, basis, cost)
    if len(vals) >= 30:
        s["expectancy_ci95"] = C.block_ci(vals, months)["ci95"]
    return s


DIRS = {"all": None, "bullish": 1, "bearish": -1}
res = {"manifest": manifest, "design": {"train_end": TRAIN_END.isoformat(), "test_start": TEST_START.isoformat(), "hold_caps": CAPS, "costs_bps": COSTS, "primary_cost_bps": PRIMARY,
                                         "registered_hold_cap": E.REGISTERED_HOLD, "n_analysis_signals": int(NS), "skipped_by_reason": dict(skipped)},
       "variants": [{"key": v.key, "version": v.version, "definition_hash": v.definition_hash, "policy": v.policy_name, "registry_verified": True} for v in variants]}

# 1. position-level accounting, every variant independent: split x direction x cap x risk basis, primary cost
acct = {}
for vi, v in enumerate(variants):
    acct[v.key] = {}
    for ci, cap in enumerate(CAPS):
        sp = split_of(vi, ci)
        acct[v.key][str(cap)] = {"registered_definition_cap": cap == E.REGISTERED_HOLD}
        for part in ("train", "test"):
            for dn, dv in DIRS.items():
                mask = (sp == part) & (np.ones(NS, bool) if dv is None else direction == dv)
                pos = positions_for(vi, ci, mask)
                acct[v.key][str(cap)][f"{part}|{dn}"] = {"std": with_ci(pos, "std", PRIMARY), "actual": E.summarize(pos, "actual", PRIMARY)}
    log("accounted", v.key)
res["position_accounting_primary_cost"] = acct

# 2. cost sensitivity (test, all directions, every cap)
cs = {}
for vi, v in enumerate(variants):
    cs[v.key] = {}
    for ci, cap in enumerate(CAPS):
        pos = positions_for(vi, ci, split_of(vi, ci) == "test")
        cs[v.key][str(cap)] = {f"{int(b)}bps": {"std_expectancy": E.summarize(pos, "std", b).get("expectancy_r"), "actual_expectancy": E.summarize(pos, "actual", b).get("expectancy_r")} for b in COSTS}
res["cost_sensitivity_test"] = cs

# 3. paired effect of a longer hold cap, per variant, on the standalone (counterfactual) trades; test split by signal date
months_sig = np.array([d.strftime("%Y-%m") for d in M.signal_date])
paired = {}
for vi, v in enumerate(variants):
    paired[v.key] = {}
    for ci in range(1, NC):
        row = {}
        for part in ("train", "test"):
            sel = (sig_ord < train_end_o) if part == "train" else (sig_ord >= test_start_o)
            for dn, dv in DIRS.items():
                m2 = sel & (np.ones(NS, bool) if dv is None else direction == dv)
                a = S[m2, vi, ci]
                b = S[m2, vi, 0]
                ok = np.isfinite(a[:, 0]) & np.isfinite(b[:, 0])
                ra = a[ok, FI["r_gross"]] - (PRIMARY / 1e4) * a[ok, FI["entry_price"]] / (X.STOP_ATR * a[ok, FI["atr"]])
                rb = b[ok, FI["r_gross"]] - (PRIMARY / 1e4) * b[ok, FI["entry_price"]] / (X.STOP_ATR * b[ok, FI["atr"]])
                row[f"{part}|{dn}"] = {**C.block_ci(ra - rb, months_sig[m2][ok]), "share_signals_changed": round(float((np.abs(ra - rb) > 1e-12).mean()), 4) if ok.any() else None}
        paired[v.key][f"cap{CAPS[ci]}_minus_cap60_std_net_r"] = row
res["longer_hold_paired_effect"] = paired
log("paired hold-cap effects done")

# 4. actual initial risk vs the standardised unit (cap 60, all positions, train+test)
risk = {}
for vi, v in enumerate(variants):
    pos = positions_for(vi, 0, np.ones(NS, bool))
    a = E.summarize(pos, "actual", PRIMARY)
    s_ = E.summarize(pos, "std", PRIMARY)
    risk[v.key] = {"std_expectancy": s_.get("expectancy_r"), "std_profit_factor": s_.get("profit_factor"), "actual_expectancy": a.get("expectancy_r"), "actual_winsorised_mean": a.get("winsorised_mean_r"),
                   "actual_profit_factor": a.get("profit_factor"), "n_positions": s_.get("n_positions"), "n_invalid_actual_risk": a.get("n_invalid_risk"), "initial_risk_atr": a.get("initial_risk_atr"),
                   "std_max_loss_r": s_.get("max_loss_r"), "actual_max_loss_r": a.get("max_loss_r")}
res["risk_actual_vs_standardised_cap60"] = risk

# 5. ML-ready outcome table + strategy-selection baselines (cap 60, standalone counterfactual net std R at the primary cost)
def net_std(vi, ci):
    r = S[:, vi, ci]
    return r[:, FI["r_gross"]] - (PRIMARY / 1e4) * r[:, FI["entry_price"]] / (X.STOP_ATR * r[:, FI["atr"]])


R60 = np.column_stack([net_std(vi, 0) for vi in range(NV)])
exit60_max = np.nanmax(np.column_stack([S[:, vi, 0, FI["exit_ord"]] for vi in range(NV)]), axis=1)
split_sel = np.where(sig_ord >= test_start_o, "test", np.where(sig_ord >= train_end_o, "embargo", np.where(exit60_max >= test_start_o, "purged", "train")))
grp_dir = np.where(direction > 0, "bull", "bear")
grp_cat = np.array([f"{a}|{b}" for a, b in zip(grp_dir, M.catalyst_class)])
grp_reg = np.array([f"{a}|{'up' if r == 1.0 else ('down' if r == 0.0 else 'na')}" for a, r in zip(grp_dir, M.regime_up_prev_session)])
names = [v.key for v in variants]
sel = {}
for gname, grp in (("direction", grp_dir), ("direction_x_catalyst", grp_cat), ("direction_x_index_regime", grp_reg)):
    b = E.selection_baselines(R60, grp, split_sel, names)
    ts = b.pop("_test_series", None)
    if ts is not None:
        mo = months_sig[(split_sel == "test") & np.isfinite(R60).all(axis=1)]
        b["test_mean_ci95"] = {k: C.block_ci(v_, mo)["ci95"] for k, v_ in ts.items()}
        b["group_rule_minus_best_single_ci95"] = C.block_ci(ts["best_variant_per_group"] - ts["best_single_variant"], mo)
    sel[gname] = b
res["strategy_selection_baselines_cap60_std_net"] = {"n_by_split": {k: int(v_) for k, v_ in zip(*np.unique(split_sel, return_counts=True))}, "rules": sel,
                                                      "note": "rules are fitted on TRAIN rows only; the per-signal oracle is an unattainable upper bound; signals on their own (counterfactual), not position-accounted"}

cols = {"symbol": M.symbol, "signal_date": M.signal_date.astype(str), "direction": direction, "split_cap60": split_sel, "relvol_signal_bar": M.relvol_signal_bar.round(4),
        "ret5_to_signal": M.ret5_to_signal.round(5), "ret20_to_signal": M.ret20_to_signal.round(5), "atr_pct": M.atr_pct.round(4), "regime_up_prev_session": M.regime_up_prev_session,
        "catalyst_class": M.catalyst_class, "catalyst_days_since": M.catalyst_days_since, "catalyst_decision_accepted_at": M.catalyst_decision_accepted_at,
        "catalyst_release_accession": M.catalyst_release_accession}
for vi, v in enumerate(variants):
    for ci in (0, 2):
        r = S[:, vi, ci]
        act = np.where(r[:, FI["initial_risk"]] > 0, r[:, FI["r_gross"]] * (X.STOP_ATR * r[:, FI["atr"]]) / r[:, FI["initial_risk"]] - (PRIMARY / 1e4) * r[:, FI["entry_price"]] / r[:, FI["initial_risk"]], np.nan)
        cols[f"{v.key}__cap{CAPS[ci]}__r_std_net10"] = np.round(net_std(vi, ci), 4)
        cols[f"{v.key}__cap{CAPS[ci]}__r_actual_net10"] = np.round(act, 4)
    cols[f"{v.key}__cap60__exit_reason"] = [REASONS[int(x)] for x in S[:, vi, 0, FI["reason"]]]
    cols[f"{v.key}__cap60__bars_held"] = S[:, vi, 0, FI["duration"]].astype(int)
    cols[f"{v.key}__cap60__initial_risk_atr"] = np.round(S[:, vi, 0, FI["initial_risk"]] / S[:, vi, 0, FI["atr"]], 3)
tbl = pd.DataFrame(cols).sort_values(["signal_date", "symbol", "direction"]).reset_index(drop=True)
csv_bytes = tbl.to_csv(index=False, float_format="%.6g", lineterminator="\n").encode("utf-8")
with open(f"{OUT}/strategy_signal_outcomes.csv.gz", "wb") as fh:
    fh.write(gzip.compress(csv_bytes, 9, mtime=0))
res["ml_ready_table"] = {"file": "strategy_signal_outcomes.csv.gz", "rows": int(len(tbl)), "columns": list(tbl.columns), "sha256_of_csv": hashlib.sha256(csv_bytes).hexdigest(),
                         "split_counts_cap60": {k: int(v_) for k, v_ in zip(*np.unique(split_sel, return_counts=True))},
                         "point_in_time": "features use bars <= the signal bar; the catalyst is the latest earnings event whose reaction session completed BEFORE the signal session, with its SEC acceptance timestamp",
                         "purge_note": "split_cap60 = 'purged' for a train-period signal whose position under ANY variant (cap 60) is still open at the test start"}
log("table written", len(tbl))

# 6. forward paper tracker validated by day-by-day replay of real history (cap 60 = the registered definition)
days = sorted({d for a in replay_pool.values() for d in a["dates"]})
by_sym_idx = {s: {d: i for i, d in enumerate(a["dates"])} for s, a in replay_pool.items()}
sig_by_day = {}
for s, a in replay_pool.items():
    for i, d in a["signals"]:
        sig_by_day.setdefault(a["dates"][i], []).append((s, i, d))
book = PT.PaperBook(variants)
for day in days:
    bars = {}
    for s, a in replay_pool.items():
        i = by_sym_idx[s].get(day)
        if i is not None:
            bars[s] = (a["o"][i], a["h"][i], a["l"][i], a["c"][i])
    book.on_session(day, bars)
    for s, i, d in sig_by_day.get(day, []):
        a = replay_pool[s]
        book.register_signal(s, d, day, float(a["atr"][i]), a["h"][max(0, i - 10):i + 1], a["l"][max(0, i - 10):i + 1])
rsyms = set(replay_pool)
ref = {}
for k in np.flatnonzero(M.symbol.isin(rsyms).to_numpy()):
    for vi, v in enumerate(variants):
        if S[k, vi, 0, FI["taken"]] == 1.0:
            ref[(v.key, M.symbol.iat[k], int(direction[k]), M.signal_date.iat[k])] = S[k, vi, 0]
closed = {(o.variant_key, o.symbol, o.direction, o.signal_date): o for o in book.closed_observations() if o.trade is not None}
mismatch = []
for k_, r in ref.items():
    ob = closed.get(k_)
    if ob is None:
        mismatch.append(("missing", k_))
        continue
    t = ob.trade
    got = (t.exit_price, REASONS.index(t.reason), t.duration, t.r_gross, t.mfe_r, t.mae_r, t.initial_stop, t.initial_risk, ob.entry_date.toordinal(), ob.exit_date.toordinal())
    want = (r[FI["exit_price"]], r[FI["reason"]], r[FI["duration"]], r[FI["r_gross"]], r[FI["mfe"]], r[FI["mae"]], r[FI["initial_stop"]], r[FI["initial_risk"]], r[FI["entry_ord"]], r[FI["exit_ord"]])
    if any(abs(float(a) - float(b)) > 1e-9 for a, b in zip(got, want)):
        mismatch.append(("different", k_, got, want))
extra = [k_ for k_ in closed if k_ not in ref]
res["paper_tracker_replay_validation"] = {"symbols": len(rsyms), "sessions_replayed": len(days), "signals_registered": int(sum(len(v_) for v_ in sig_by_day.values())),
                                          "variants": len(variants), "batch_positions_compared": len(ref), "closed_by_tracker": len(closed), "mismatches": len(mismatch), "tracker_extra_closed": len(extra),
                                          "first_mismatch": str(mismatch[0])[:300] if mismatch else None, "state_hash": book.state_hash(), "open_at_end": len(book.open_observations()),
                                          "summary": book.summary()["by_variant"], "writes_anything": False}
log("paper tracker replay: compared", len(ref), "mismatches", len(mismatch), "extra", len(extra))
assert not mismatch and not extra, "paper tracker replay differs from the batch experiment"

def clean(o):
    """JSON the browser can parse: numpy scalars to Python, NaN / inf to null."""
    if isinstance(o, dict):
        return {str(k): clean(v_) for k, v_ in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v_) for v_ in o]
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        o = o.item()
    if isinstance(o, float) and not np.isfinite(o):
        return None
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return o


out_name = "strategy_experiment.json" if not os.environ.get("EXPERIMENT_MAX_SYMBOLS") else "strategy_experiment_smoke.json"
json.dump(clean(res), open(f"{OUT}/{out_name}", "w"), indent=1)
log("WROTE", out_name)
