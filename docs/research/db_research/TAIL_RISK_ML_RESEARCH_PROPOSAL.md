# Research proposal — tail-loss classification as an ML target

Status: proposal for owner review, 2026-10-21. **No training is requested or done.** Uses existing data only.

## 1. Why change the target

Every return-based target tried so far fails:

| Target | Best result | Verdict |
|---|---|---|
| Legacy momentum score | AUC 0.627, but ≈ 0.50 within direction (the label contains a volume term) | Fails the gate |
| Plan profit (R > 0) | AUC 0.533, below the direction-only baseline (0.539) | Fails the gate |

Notebook 02 found a different, large and stable structure: **losses beyond the stop**. They are rare and gap-driven, are concentrated around earnings, and grew from 0.87% of trades (2021–23) to 1.47% (2024–26). Predicting *how badly a trade can go wrong* may be learnable where *whether it wins* is not. The use is risk labelling and position sizing, never entry selection.

## 2. Targets (declared before any fit)

| Name | Definition (per breakout, executable rule L3: next-open entry, gap-aware, 10 bps) | Base rate (2021-09 → 2026-09) |
|---|---|---|
| **T1 tail loss (primary)** | `exec_r_net ≤ −1.5` within 20 bars | ≈ 1.2% |
| T2 gap stop | Stop filled at an open beyond it (`gap_stop = 1`) | ≈ 6.2% |
| T3 deep excursion | Max adverse excursion ≥ 1.5R within 20 bars (whether or not stopped) | to measure |
| R0 return (comparison) | `exec_r_net > 0` | ≈ 48–49% |

All four are fitted with the same features, folds and gate, so the comparison is like for like. T1 is rare. The headline metrics are therefore PR-AUC, lift in the top decile and calibration, not accuracy.

## 3. Features (point-in-time at the signal close; no outcome-dependent inputs)

| Group | Features | PIT status |
|---|---|---|
| Earnings | Sessions since the last report; **projected** sessions to the next (last + 91 days); "reported ≤ 3 sessions ago" | PIT-safe |
| Earnings (oracle arm only) | Retrospective sessions to the next report | **Not PIT.** Used only in a separate, labelled arm to measure what a true first-seen schedule would be worth. It never enters the candidate model. |
| Volatility / gap history | `atr_pct`, `gap_pct`, `bar_range_atr`, 20-day realised vol, count of ≥ 3-ATR gaps in the last 250 bars | PIT |
| Liquidity / size | `log_dollar_vol_20`, price level | PIT |
| Extension | `pct_from_52w_high`, `dist_sma50_pct`, `ret_20d` (direction-aware) | PIT |
| Market | Equal-weight trend, breadth, realised vol (Notebook 02 definitions) | PIT; thresholds learnt on train only |
| Sector | Modern sector one-hot | **Biased** (modern membership). Included only in a sensitivity arm. |

**Banned:**
- Holding-period earnings exposure (it depends on the outcome).
- `fetched_at`.
- Any feature computed from bars after t.
- Any threshold fitted on test data.

## 4. Leakage prevention

- **Labels:** labels need 20 bars plus a next-open entry. Training rows must have t + 21 sessions before the test start (an embargo of 21 sessions, which fixes the old 30-*day* embargo issue).
- **Overlapping signals:** consecutive breakouts of one symbol overlap. Purge same-symbol training rows within 21 sessions of any test row. Bootstrap by day cluster.
- **Feature leakage:** a unit test runs every feature builder on a truncated price history and requires identical values (the same pattern as the existing no-lookahead tests).
- **Survivorship:** delisted names are absent. Report it, and don't claim absolute rates for live use.

## 5. Validation

- **Design:** chronological walk-forward with expanding training sets. Test years are 2023, 2024, 2025 and 2026 YTD; training always starts 2021-09 (earnings coverage). 2018–2020 can be added for the non-earnings variant only.
- **Hold-out:** a final hold-out matching Notebook 02's OOS (2024-01 → end) is evaluated once, after the design is frozen.
- **Uncertainty:** day-cluster bootstrap confidence intervals on every metric. Holm correction across the four targets.
- **Calibration:** Platt or isotonic fitted on the last training year only. Reliability curves, Brier score and its decomposition, and calibration slope and intercept per fold.

## 6. Baselines (each must be beaten, in order)

1. Base rate (no model).
2. **Rule E**: "report in 1–5 / 6–10 sessions" (retrospective) and its PIT variant. This is the bar to clear. Rule E already halves ≥ 1.5R losses at 15% coverage cost.
3. Logistic regression on 4 features (projected earnings proximity, `atr_pct`, `gap_pct` history, `log_dollar_vol_20`).
4. Gradient boosting (the existing XGBoost stack, using the honest trainer's fold machinery).

## 7. Promotion gate (a priori; all must hold in at least 3 of 4 walk-forward folds and in the hold-out)

- PR-AUC at least **1.5×** that of Rule E, with the day-cluster interval excluding Rule E.
- Calibration slope 0.8–1.2 and an expected/observed tail rate within ±25% in each decile.
- **Economic test:** at equal coverage removed (10%, 20%), the model removes more ≥ 1.5R losses than Rule E, and it does not lower mean R by more than 0.01 R.
- Stable direction: both longs and shorts improve, or the model is restricted to the side that does.

If R0 (the return target) were somehow to pass the same gate, it would be reported. The prior from three earlier attempts is that it won't.

## 8. Deliverables (after approval)

- An experiment registration (the empty `experiment_registration` table, or the repo registry) recorded **before** the first fit.
- A research notebook plus `ml_training/experiments/tail_risk_*.py`, with no change to `momentum_predictor.py` defaults.
- A Lab report (JSON) with the per-fold metrics, calibration plots and the economic table.
- A GO/NO-GO. A GO would mean an internal risk label first (owner view), then the Earnings Risk surfaces. It would never mean entry selection, and never a live-path change without a separate approval.

## 9. Risks

- The tail rate nearly doubled in 2024–26. A model trained on 2021–23 may be miscalibrated by regime.
- The rare-event sample is about 4,600 tail trades in total, enough for 4–6 features.
- The point-in-time earnings schedule only exists after the forward capture in the Earnings Risk spec §6 is approved. Until then, the oracle arm bounds the value.
