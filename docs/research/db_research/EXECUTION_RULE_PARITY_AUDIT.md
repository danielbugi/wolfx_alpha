# Execution-rule parity audit (1R vs 3R, close vs open, gaps, costs)

Status: research audit, 2026-10-21. No live rule was changed. Evidence: Notebook 02 §2a (re-runnable).

## 1. Why this audit exists

Notebook 01 put the 9-year backtest (−0.011 R) next to the live ledger (+0.064 R) as if they measured the same thing. They don't: the two use different exit rules. This audit lists every rule set in use, proves each engine reproduces its own reference, and gives one comparison where only one assumption changes at a time.

## 2. Rule sets in use

| | Live ledger (`evaluate_signal_ledger.py`) | Backtest dataset `r_single` (`plan_outcomes`) | Backtest `plan_r` | Notebook 01 | Notebook 02 primary | Notebook 02 secondary | Lab exit study / paper tracker (`exit_policies.py`) |
|---|---|---|---|---|---|---|---|
| Signals | Screener output, guard-passed, one open slot per symbol/direction | Every Donchian-20 breakout (offline detector) | same | Ledger rows + dataset rows | Dataset rows 2021-09+ | same | Dataset sample / every triggered candidate |
| Entry | Signal-day **close** | Signal-day **close** | close | as source | **Next open** | Next open | **Next open** |
| ATR | `technical_indicators.atr_14` (Wilder 14) | Wilder 14, recomputed (same formula) | same | as source | Dataset ATR (= `atr_pct` × close) | same | Signal-time ATR |
| Stop | 2×ATR = 1R | 2×ATR = 1R | same | as source | 2×ATR = 1R | 2×ATR = 1R | Per variant (A/D 1R; B channel low; C 3×ATR) |
| Exit target | **First target touched** (+1R; +2R/+3R only if crossed in the same bar) | **+3R only** (`r_single = rs[2]`) | Mean of 1R/2R/3R tranches | as source | First +1R (same-bar 2R/3R not credited) | +3R | Per variant (2R, 3R, trails) |
| Same-bar stop and target | Stop (flagged) | Stop | Stop | — | Stop | Stop | Stop |
| Stop fill | At the stop price (**no gaps**) | At the stop price (**no gaps**) | same | — | **At the open if it gaps beyond** | same | At the open if it gaps beyond |
| Target fill | At level | At level | same | — | At the open if it gaps beyond | same | same |
| Horizon | 20 bars, then close | 20 bars | 20 | — | 20 | 20 | 60 (study) / variant cap (tracker) |
| Costs | **None** | **None** | None | 10 bps only in §11 composite | **10 bps** round trip, in R | 10 bps | 0 / 10 / 25 bps (tracker); 10 bps (study) |
| Unit of R | Entry−stop | Entry−stop | same | — | Entry(open)−stop | same | Actual initial risk |

## 3. Engine reproduction (each engine matches its own reference)

| Check | Result |
|---|---|
| Notebook 02 simulator, set to the dataset rule (close entry, 3R target, no gap fills, no cost) vs `r_single`, 20,000 rows | **100.00% identical** (max difference 0.000) |
| Same simulator with gap fills on | 92.9% identical: the whole difference is gap fills (not the ATR, which uses the same Wilder formula) |
| Same simulator, set to the live rule, replaying **every resolved ledger row** with the ledger's own entry and ATR | **1,408 / 1,408 identical** outcome and holding period |
| Ledger entry price vs `stock_prices` close on the signal day | 100% identical |

The engines are consistent; only the rules differ.

## 4. The rule ladder (one assumption at a time, same 60,000 historical signals, 2021-09 → 2026-09)

| Step | Both | Long | Short |
|---|---|---|---|
| L0 live rule (close, first target, stop at level, no cost) | +0.006 | +0.062 | −0.058 |
| L1 + gap fills | −0.017 | +0.038 | −0.079 |
| L2 + next-open entry | −0.019 | +0.029 | −0.074 |
| L3 + 10 bps (≈ Notebook 02 primary) | −0.036 | +0.010 | −0.088 |
| D0 dataset rule `r_single` (close, 3R, no gaps, no cost) | −0.022 | +0.057 | −0.112 |
| D3 3R + gaps + next open + 10 bps | −0.059 | +0.011 | −0.139 |

Mean R per trade. Day-cluster 95% intervals are printed in the notebook.

Reading:
- Gap fills cost about 0.02 R, next-open entry about 0.00–0.01 R, and 10 bps about 0.02 R.
- The 1R first-target exit scores better than the 3R exit on the idealised rule (+0.006 vs −0.022). Executed, they are about equal.
- The long-side "edge" in every idealised number (+0.06 R) shrinks to about +0.01 R once executed. Shorts are negative under every rule.

## 5. Consequences

1. **Never compare** the live ledger's `outcome_r` with the dataset's `r_single`. Use L0 for live-vs-history comparisons, and L3 or the paper tracker for "what a trader would get".
2. The live ledger and dashboards report the idealised L0 rule. That is a legitimate definition, but it overstates what is executable by about 0.04 R per trade. Label it as such wherever it is shown.
3. Lab reports quoting `r_single` describe the **3R** rule; their wording should say so.
4. Notebook 01's "backtest −0.011 R vs live +0.064 R" mixes D0 and L0. The apples-to-apples historical L0 figure for 2021-09 → 2026-09 is +0.006 R.

## 6. Proposed apples-to-apples comparison (not executed beyond §4; no rule change)

1. **Same signals.** The live ledger rows with their own entry and ATR, plus the dataset rows for the same (symbol, date, direction). This overlap matures from about 2026-10-26.
2. **Same engine.** `nb02_lib.simulate`, run under L0, L1, L2, L3, D0 and D3 for both sets.
3. **Report as a matrix:** rule × signal set × direction, with censoring-corrected locked-in R for live rows and day-cluster intervals.
4. **Explain** the 64 live signals the offline detector did not flag and the 3,559 offline breakouts the ledger did not take (94% are the open-slot de-duplication).
5. **Add the paper tracker's variant A (2R)** as the independent executable reference once it has about 60 sessions.

Owner decision needed later, not now: whether dashboards should show L0 (current), L3, or both.
