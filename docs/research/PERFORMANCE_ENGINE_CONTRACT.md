# First Light performance engine: contract (C1)

Status: 2026-10-04, `lab/first-light-algo`. Code: `mechanism/strategy_analytics/performance.py` (new) on top of `analytics.py` / `definitions.py` (unchanged
semantics). Read-only; nothing here writes to the database or touches production.

## 1. Principles

- **Strategy-generic.** Input is a strategy row (`id`, `strategy_key`, `strategy_version`). Donchian Breakout v1 is one strategy; a second strategy gets
  its own `strategies` row and the same engine works with no code change except a declared exit-rule entry.
- **Metrics live only here** (`mechanism/strategy_analytics`). Routers, the dashboard and the bot present; they never recompute.
- **Unavailable, never invented.** If the stored data cannot support a metric or a grouping, the answer is `state: not_available` with the reason and the
  data it would need. No reconstruction, no default value, no zero.
- **PIT.** Every grouping key is either a value frozen on the ledger row at signal time (`direction`, `quality_grade`, `sector`, `signal_date`,
  `model_version`) or part of the outcome (`status`, `bars_held`). Nothing is looked up later. Attributes that would need a later lookup (regime,
  earnings proximity, catalyst, relative strength) are declared unavailable until a PIT-safe source exists.
- **Sample size is always visible.** Every rate/average is `{value, n, state}`; `state` in `ok` (n >= 5), `preliminary` (0 < n < 5), `no_data` (n = 0,
  value null), `not_available`.

## 2. Entry points

| Function | Returns |
|---|---|
| `analytics.summary(fetch, strategy)` | tracking counts (total/open/held/resolved), performance, outcome distribution, bullish/bearish split (existing) |
| `analytics.overall_performance(fetch, strategy_id)` | counts + performance (existing) |
| `performance.contract()` | static: available dimensions, unavailable dimensions (+ what each requires), unavailable metrics, metric list |
| `performance.exit_rules(strategy)` | declared stop / targets / expiry for that strategy version, or `not_available` |
| `performance.breakdown(fetch, strategy, dimension)` | one row group per bucket with the metrics below; `not_available` for undeclared-data dimensions; `ValueError` for an unknown dimension |

## 3. Metrics per group

`signals`, `open`, `held`, `resolved`, `winners`, `win_rate`, `average_r`, `median_r`, `sum_r`, `profit_factor`, `average_holding_bars`, `average_mae_r`.
Rates and R use **resolved** signals only (open and held excluded from numerators and denominators), as in `definitions.DEFINITIONS`.
`profit_factor` = gross winning R / gross losing R; with no losing resolved signal it is `not_available` (undefined, not infinite).

## 4. Dimensions

Available now (all from `signal_ledger` columns): `direction`, `exit`, `quality_grade` (NULL -> `ungraded`), `sector` (NULL -> `unclassified`), `signal_month`,
`holding_bars` (1-5, 6-10, 11-15, 16+, unresolved), `model_scored` (NULL or legacy `'unknown'` -> `unscored`, otherwise `scored`; the legacy value is
never counted as a model, see `MODEL_VERSION_SEMANTICS_AND_HISTORICAL_ROWS.md`).

Declared unavailable: `market_regime`, `volatility_regime` (need `market_snapshot`, migration 24, observed provenance), `relative_strength`
(`sector_snapshot`/`feature_snapshot`), `earnings_proximity` (`market_event` with `known_at` before the signal), `catalyst` (versioned classification of
PIT-safe events), `ml_score_bucket` (the ledger stores the model version, not the probability).

## 5. Exit rules (target / stop / expiry)

The ledger stores per-row `stop_price` and `target1..3_price`, so R is exact per row. The **expiry horizon is not stored per row**; it is a strategy
constant. `EXIT_RULES[(strategy_key, version)]` declares it (Donchian v1: stop 2 x ATR = 1R, targets 2/4/6 x ATR = 1/2/3R, expiry 20 bars) and a
drift test pins those values to `mechanism/shared/trade_plan.py`. A strategy with no declaration reports `not_available`; it never inherits another
strategy's rules.

## 6. What the existing ledger can and cannot calculate

| Calculable exactly | Not calculable (returns `not_available`) |
|---|---|
| Total / open / held / resolved counts, direction split | Maximum favourable excursion (no MFE column; needs the forward path, `fwd_v1`) |
| Win rate, stop/expiry rate, target-milestone counts, ambiguous (same-bar) share | Post-exit trajectory (the ledger stops at exit; a target1 exit may have run to target2) |
| Average/median/sum R, profit factor, expiry R split by sign | Drawdown in R (needs a portfolio/sequencing rule; not a property of independent signals) |
| Holding duration (trading bars), MAE in R | Per-row expiry horizon (strategy constant only) |
| Breakdowns by direction, exit, grade, sector, month, holding bucket, scored/unscored | Breakdowns by regime, volatility, relative strength, earnings, catalyst, ML score |
| Data health: stale / held / invalid-bar / coverage (existing `data_health`) | Anything for signals before capture activation that needs a feature snapshot |

Known data limits that apply to every number: the ledger is the live screener's output only (near-breakouts are never recorded); daily OHLC cannot order a
stop and a target touched on the same bar (recorded conservatively as stopped, flagged `same_bar_stop_and_target`); `quality_grade`/`sector` are captured at
signal time. 165 historical production rows carry `model_version='unknown'` and are treated as `unscored` on read, not rewritten.

## 7. Not in this slice

No HTTP endpoint yet (C2 will add `backend/routers` + `backend/services` pairs delegating to `performance.breakdown`), no UI, no storage of any computed
metric. `forward_return_labels` stays `not_available` in `CAPABILITIES` until `fwd_v1` exists.
