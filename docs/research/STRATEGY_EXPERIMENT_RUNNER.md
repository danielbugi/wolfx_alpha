# Strategy Experiment Runner and Forward Paper Tracker (research slice, 2026-10-12)

Status: implemented and tested; an isolated research run on a restore of the production backup produced the results below. **Nothing is activated**: no production job, no paper trade written anywhere, no mechanism
image change, no change to the R1-R3 collector, the bot, the timers, the arming file or any forward evidence. The `/lab` page shows the results as report `strategy_experiments`.

## What was built, and what it reuses

| Component | File | Reuses / replaces |
|---|---|---|
| Stateful exit engine: `open_position`, `advance`, `make_trade`; `simulate` is now a loop over `advance`; every trade carries its **actual initial stop and risk** | `mechanism/research/exit_policies.py` | extends the one existing engine; proven identical to the original on thousands of random paths (`reference_exit_simulation.py` is the frozen original) |
| Experiment runner: registered identities, manifest + hash, independent per-variant position accounting, split/purge/censoring, metrics on both risk bases, paired comparisons, selection baselines, point-in-time features and catalyst join | `mechanism/research/experiment_runner.py` | `strategy_variants` (identities), `exit_policies` (engine + `aggregate`), `lab.manifest.canonical_hash`, the ledger's one-open-position rule |
| Forward paper tracker: pure state machine, per-variant independent observations, explicit idempotent sessions, JSON-serialisable state | `mechanism/research/paper_tracker.py` | the same `open_position`/`advance` as the batch runner; no I/O, no database, no clock |
| Experiment script (database-facing, read-only) | `ml_training/experiments/strategy_experiment.py` | `ml_breakout_dataset_v2` signals, `price_features`, `_common` (market panel, month-block bootstrap), the committed SEC event table |
| Report + `/lab` view + claims | `build_lab_reports.py`, `LabReportView.tsx` | the existing report pipeline and read-only `/api/lab` |

Not rebuilt: the signal ledger, `strategy_variant`, `candidate_observation`, the dataset/manifest framework and the fwd_v1 labels all stay as they are. The ledger and `candidate_observation` are the future SOURCE of forward signals; the experiment uses their historical twin (`ml_breakout_dataset_v2`).

## Conventions (all recorded in the manifest, hash `db062f711ef9d9bb…`)

- Identities: the five registered variants (key, version, definition hash) from `strategy_variants`; before a run the hashes are compared with the production registry rows and the run refuses on any mismatch.
- Signal at the close of bar s, entry at the OPEN of bar s+1, costs round-trip bps charged in R (0 / 10 / 25; 10 is primary).
- One open position per (variant, hold cap, symbol, direction) (the live ledger's rule; a signal on or before the previous exit bar is skipped), each variant accounted independently.
- Risk bases side by side: standardised (1R = 2 x ATR14) and each variant's actual initial risk.
- Hold caps 60 (the registered definition) / 120 / 250 (research conditions, not registered identities). Positions whose history ends before their cap are excluded; every cap is evaluated on the same 74,032 signals.
- Chronological split by signal date: train before 2022-01-01, embargo, test from 2022-02-15; a train position still open at the test start is purged.
- Point in time: features use bars up to the signal bar; the catalyst is the latest earnings event whose reaction session completed before the signal session, with its SEC acceptance timestamp (3,526 signals carry one; none violates this).
- Analysis set: 74,032 signals of 391 liquid symbols since 2017-03. Skipped: {'window_shorter_than_longest_cap': 10760, 'price_discontinuity_in_window': 37}.

## Results (net of 10 bps; position-level; test period; registered 60-bar cap; standardised R)

| Variant | Positions | Net expectancy (R) | 95% interval | Win rate | Profit factor | Avg bars | Train expectancy |
|---|---|---|---|---|---|---|---|
| `a_atr_stop_fixed_2r` | 11,738 | -0.037 | -0.112 to +0.046 | 34% | 0.946 | 18.33 | -0.006 |
| `a_atr_stop_fixed_3r` | 10,331 | -0.049 | -0.123 to +0.043 | 29% | 0.934 | 23.7 | -0.016 |
| `b_donchian_trail_10` | 11,069 | -0.024 | -0.110 to +0.062 | 36% | 0.954 | 17.2 | -0.010 |
| `c_atr_trail_3x` | 10,156 | -0.045 | -0.119 to +0.043 | 36% | 0.924 | 20.76 | -0.025 |
| `d_hybrid_atr_stop_to_donchian_10` | 10,637 | -0.031 | -0.119 to +0.059 | 38% | 0.947 | 18.78 | -0.013 |

Every variant is slightly negative out of sample and every interval includes zero. Breakouts cluster on consecutive days, so the one-open-position rule keeps only about 28% of the signals as positions.

**Longer holding periods** (test period, net R; paired difference on the same signals):

| Variant | 60 bars | 120 bars | 250 bars | 120 minus 60 | 250 minus 60 | Avg bars at 250 |
|---|---|---|---|---|---|---|
| `a_atr_stop_fixed_2r` | -0.037 | -0.040 | -0.041 | -0.005 (-0.011 to +0.001) | -0.005 (-0.011 to +0.002) | 19.55 |
| `a_atr_stop_fixed_3r` | -0.049 | -0.071 | -0.072 | -0.015 (-0.031 to +0.002) | -0.016 (-0.033 to +0.002) | 28.77 |
| `b_donchian_trail_10` | -0.024 | -0.024 | -0.024 | -0.002 (-0.006 to +0.002) | -0.002 (-0.006 to +0.002) | 17.3 |
| `c_atr_trail_3x` | -0.045 | -0.043 | -0.043 | -0.003 (-0.010 to +0.004) | -0.005 (-0.012 to +0.001) | 22.54 |
| `d_hybrid_atr_stop_to_donchian_10` | -0.031 | -0.031 | -0.030 | -0.003 (-0.008 to +0.001) | -0.003 (-0.008 to +0.001) | 19.1 |

Longer caps change almost nothing: the exit rules (the 10-bar channel, the 3 x ATR trail, the stop) end positions long before the cap, after 17.3 to 28.77 bars on average. The trend-following variants do not benefit from a longer hold.

**Actual initial risk vs the standardised 2 x ATR unit** (60-bar cap, all positions):

| Variant | Median initial risk (ATR) | Over 3 ATR | Expectancy, std R | Expectancy, actual R | Actual R, winsorised | Worst loss, std R | Worst loss, actual R | Invalid risk |
|---|---|---|---|---|---|---|---|---|
| `a_atr_stop_fixed_2r` | 2.0 | 0% | -0.022 | -0.022 | -0.022 | -7.426 | -7.426 | 0 |
| `a_atr_stop_fixed_3r` | 2.0 | 0% | -0.029 | -0.029 | -0.029 | -7.426 | -7.426 | 0 |
| `b_donchian_trail_10` | 3.315 | 64% | -0.020 | -0.013 | -0.029 | -6.536 | -4.861 | 29 |
| `c_atr_trail_3x` | 3.0 | 43% | -0.036 | -0.024 | -0.028 | -7.426 | -4.95 | 0 |
| `d_hybrid_atr_stop_to_donchian_10` | 2.0 | 0% | -0.024 | -0.024 | -0.045 | -7.426 | -7.426 | 0 |

B starts at the 10-bar channel low (median 3.3 ATR, 64% of positions over 3 ATR) and C at 3 x ATR, so the standardised R understates their real risk; the actual-risk expectancy differs, and a handful of B entries start at or beyond the stop (invalid risk).

**Strategy selection** (fitted on train only, judged on test, per-signal counterfactual, 60-bar cap):

| Grouping | Best single variant (train) | Best single, test | Best per group, test | Oracle (not attainable) | Group rule minus best single |
|---|---|---|---|---|---|
| direction | `a_atr_stop_fixed_3r` | -0.069 | -0.066 | +0.627 | +0.003 (-0.036 to +0.036) |
| direction x catalyst | `a_atr_stop_fixed_3r` | -0.069 | -0.064 | +0.627 | +0.005 (-0.034 to +0.037) |
| direction x index regime | `a_atr_stop_fixed_3r` | -0.069 | -0.066 | +0.627 | +0.003 (-0.036 to +0.036) |

Choosing a variant per direction, catalyst class or index regime does not beat always using the best single variant. The per-signal oracle shows the gap between hindsight and any rule; it is an upper bound, not a result.

**Paper tracker replay validation.** Real history was replayed day by day through the tracker (39 symbols, 2,528 sessions, 8,627 signals, 5 variants in parallel): 12,157 positions compared field by field with the batch results, **0 mismatches**, 0 unexpected closes.

**ML-ready table** `strategy_signal_outcomes.csv.gz`: 74,032 signals x 48 columns (point-in-time features, catalyst class with its SEC acceptance time and accession, and realised net R under every variant at 60 and 250 bars on both risk bases, with a chronological split label and purge).

## Tests

`test_exit_policies_equivalence.py` (12: the refactored engine equals the frozen original on 7,200 random simulations incl. NaN bars, plus the actual-risk fields), `test_experiment_runner.py` (21), `test_paper_tracker.py` (17: four random-history replays equal the batch runner; independence, one-open-position, sessions, snapshots, no I/O), report tests (builder claims, table hash and point-in-time check over all rows), a frontend rendering test on the real report. The original 24 exit-policy tests and the pinned variant hashes are unchanged.

## Requirements for a future ISOLATED paper-tracking activation (nothing below is built or approved)

1. **Source of signals.** The forward signals are the session's `candidate_observation` rows with `triggered` and `tracked_intent` (the same predicate the ledger writer uses), read after the nightly capture has finished; ATR from the observation (`atr_source = measured` only), channel window from `stock_prices` bars up to the signal session. The tracker is fed an EXPLICIT session date; it never reads a clock.
2. **Storage: new append-only tables only** (a proposed migration 36, written like migration 35: marker comments, ENABLE ALWAYS triggers, runtime role SELECT + INSERT, roles scripts extended, preflight, rollback, CI). Event-sourced: one row per `registered`, `entered`, `closed`, `stale` event keyed by (variant id from `strategy_variant`, symbol, direction, signal session), with a state-hash chain; positions are reconstructed by replaying events through `paper_tracker`. Unique keys make every write idempotent. No write to `strategies`, `signal_ledger`, `candidate_observation` or any existing evidence table.
3. **A separate activation switch.** Its own arming file and pin, off by default, independent of `FORWARD_COLLECTION_ARMED`; an explicit activation session (no backfill into forward evidence; historical replays stay in the research domain); a documented kill switch.
4. **A separate job.** A new systemd unit and timer scheduled AFTER the capture, never before, never blocking it: its failure must not change any existing unit's result. It needs the research modules in an image: the mechanism pin is shared with the collector, so activating it means either a new mechanism pin (collector re-verification and a fresh arming decision) or per-service pins first (see `BOT_DUAL_PIN.md`, PR #11). This is a separate approval.
5. **Registered definitions only.** The registered 60-bar cap. Any longer cap is a new variant version (v2) registered first; the tracker flags `registered_cap` on every observation.
6. **Acceptance before any production write.** (a) Replay validation against the batch runner on production-shaped data (done in scratch: 0 mismatches); (b) a DRY-RUN mode that computes the session's events and compares them with the batch result without writing, for N sessions; (c) a shadow run into a scratch database from the nightly backup; (d) then the owner's approval to write to production, with a backup and the preflight.
7. **Monitoring.** A read-only freshness check (last session processed, open observations per variant, stale count) in `/lab`, and a mismatch alarm if the dry-run comparison ever differs. No Telegram, no alerts, no execution.
8. **Owner decisions needed:** universe of symbols, cost assumption to display, retention, whether registered variants beyond the five (or a v2 with a different cap) should exist, and the activation session.

## Next research slices

Walk-forward over several train windows; a regime-aware selection study with a fitted model against these baselines (the table is ML-ready; none is promoted); realistic portfolio accounting (position sizing, concurrency caps) on top of the position-level R; the catalyst-conditioned variants after the SEC coverage grows.
