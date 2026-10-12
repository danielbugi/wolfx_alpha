# Registered strategy variants: verified definitions (production, 2026-10-12)

Five rows were written to the production `strategy_variant` table on 2026-10-12 15:40:41Z with the approved writer (`python -m research.strategy_variants register --apply`), as the runtime role, from the deployed
backend image `e156cc8c6c72` (its `strategy_variants.py` and `exit_policies.py` are byte-identical to main's). Registration is metadata only: it wrote five rows into a table nothing else reads, and nothing in
`strategies`, `signal_ledger` or any other table changed (full catalog and data fingerprints compared before and after). No strategy, signal job, paper-trading job, model or automatic registry writer was activated.

**Parent:** `strategies` id 1, `donchian_breakout` `v1` ("The live multi-timeframe Donchian breakout screener, as shipped today"). The variants take the parent's signals (long and short) and change only the exit.

## Common to all five (the registered `entry` and `exit` blocks)

| Item | Value | Verified against |
|---|---|---|
| Signal | the parent strategy's signal at the close of bar `s`; direction comes from the signal (bullish = long, bearish = short; shorts are the exact mirror) | `simulate()` direction handling; `test_a_short_is_the_exact_mirror_of_a_long`; mirror probe on all five |
| Entry fill | open of bar `s+1` (a research convention, not claimed to equal the live trade plan's fill) | `simulate()`; `design.entry` in `exit_policy_study.json` |
| 1R (risk unit) | 2.0 x ATR(14) at the signal bar; ATR is Wilder ATR(14) | `STOP_ATR = 2.0` = the screener's `stop_loss_price = price -/+ 2*atr` (`multi_timeframe_screener.py`); `price_features._wilder(tr, 14)` |
| Time cap | 60 bars (the entry bar is bar 1); a position still open exits at that bar's close (`time`) | `DEFAULT_MAX_HOLD = 60` |
| Fills | a stop or target fills at its level, or at the open when the bar opens beyond it (gaps are not flattered); stop before target when both are inside one bar; levels use bars before the current one only (no lookahead) | `test_a_gap_through_the_stop...`, `...both_inside_one_bar...`, `test_b_channel_uses_only_earlier_bars_no_lookahead` |
| Costs | not part of the definition (an evaluation parameter, charged in R) | `cost_bps` argument |

## The five variants

| Variant key (v1) | Policy | Initial stop | Take-profit | Trailing / exit rule | Definition hash (sha256) |
|---|---|---|---|---|---|
| `donchian_breakout__exit_a_atr_stop_fixed_2r` | `atr_stop_fixed_r@v1:target_r=2.0` | 1R = 2 x ATR below entry (fixed) | +2R | none | `15a7230d0671626fba0854f4f9f2f2be4f9ee89690fd56745a865b1427d587d8` |
| `donchian_breakout__exit_a_atr_stop_fixed_3r` | `atr_stop_fixed_r@v1:target_r=3.0` | 1R = 2 x ATR below entry (fixed) | +3R | none | `c5f4c0edf79947fd0e02a56aff657c9d45b1e0db227f7dcd36fc6982c2f369da` |
| `donchian_breakout__exit_b_donchian_trail_10` | `donchian_trail@v1:channel=10` | **the lowest low of the 10 bars before the entry bar (not the ATR stop, so it can be farther than 1R)**; short: highest high | none | exit below the lowest low of the previous 10 bars, recomputed every bar | `cf6f60238e13d46a453dedced0b77cf861e7dcc28d12231151d4bc4bc84a39d9` |
| `donchian_breakout__exit_c_atr_trail_3x` | `atr_trail@v1:mult=3.0` | **3 x ATR (= 1.5R) below entry**, not 1R | none | stop ratchets to (best price - 3 x ATR), never loosens | `eafa7e67ca563cda7ebbbd9fe0e66390168269a5e705464ee97d4fc975cc0134` |
| `donchian_breakout__exit_d_hybrid_atr_stop_to_donchian_10` | `hybrid_atr_to_donchian@v1:channel=10,switch_r=1.0` | 1R = 2 x ATR below entry | none | after a favourable excursion of +1R it switches to the 10-bar channel, clipped so it never falls below the initial stop | `0d14824dd24c000c903c9623c9a66d589af08c1d400f244d92a06f8f584bc16b` |

Rows 1 to 5 (ids 1-5), all `variant_version = v1`, `supersedes = NULL`, `parent_strategy_id = 1`, created 2026-10-12 15:40:41Z. A second `register --apply` returned `unchanged` for all five (ids and sequence unchanged: last value 5).

**Reading the stored record.** The stored `definition` names the policy key and parameters and the engine (`exit_policies_v1`); the per-policy initial-stop behaviour above lives in that pinned, tested engine, not in prose in the row.
In particular `entry.stop_atr_multiple_is_1R = 2.0` defines the R unit for every variant; it is the initial stop only for A and D (B starts at the channel, C at 3 x ATR). If the row should carry that wording, the append-only route is a new version (`v2`) of the same key, not an edit.

## Verification (nothing was invented or altered)

- **Implementation:** `mechanism/research/exit_policies.py` (24 tests in `test_exit_policies.py`, mutation-checked: lookahead and target-before-stop are caught). A behaviour probe on the engine (ATR 1, entry 100) gave: initial stop 98.0 for A and D, 97.9 for B (the channel low), 97.0 for C; targets 104 / 106 for A2 / A3; a +50% first-bar run is capped for A only; the trailing probe exits at 102.0 for C and by time for B and D; long and short give identical R for every policy.
- **Hashes:** the five hashes are pinned in `mechanism/research/tests/test_strategy_variants.py`, equal to the `plan` output of the deployed image, and equal to `strategy_variant_outcomes.*.definition_hash` in `sec_earnings_study.json`.
- **Evidence:** `docs/research/evidence/quant_lab_2026-10-11/exit_policy_study.json` (72,262 breakouts x 5 policies, identities `atr_stop_fixed_r@v1:target_r=2.0` ... `hybrid_atr_to_donchian@v1:channel=10,switch_r=1.0`) and the SEC study's per-variant outcomes. Results are exploratory research evidence, not trading advice or a live rule.
