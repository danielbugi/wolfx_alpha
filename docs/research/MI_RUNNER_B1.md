# First Light Algo — Market Intelligence writer/runner (Slice 2, track B1)

Status: built on `lab/first-light-algo`, **not merged, not scheduled, not applied to production**. Migrations 24/25 remain unapplied in
production; this runner needs 24 (and INSERT on its tables) and is an operator-run CLI only. No timer, no pipeline stage, no router and no
Telegram path references it (`test_activation_neutral.py` pins this). Strategy-neutral: it is not a Donchian filter, an eligibility rule, a
screener input or an ML feature.

## 1. What it does

`python -m market_intelligence.runner --session YYYY-MM-DD --provenance {observed|reconstructed} [--apply --code-ref <sha>]`

For ONE explicit session it computes and (only with `--apply`) writes, insert-only, to migration 24's tables:

| Output | Table | Source module (version) |
|---|---|---|
| Regime RISK_ON / NEUTRAL / RISK_OFF (+ `UNAVAILABLE`) with raw + normalised components, completeness, reasons | `market_snapshot` (typed cols + `regime_components`, `regime_reasons`) | `risk_regime.py` (`risk_regime_v1`) |
| Breadth with numerator / denominator / eligible / missing counts | `market_snapshot.coverage.measurements.breadth` | `breadth.py` (`breadth_v1`) |
| Sector intelligence (trend, strength, participation, breadth, coverage) | `sector_snapshot` typed cols + `coverage.measurements.sectors` | `rs_v1` + `sector_intel.py` (`sector_v1`) |
| Relative strength: sector vs market, sector vs universe median (stored); stock vs market, stock vs sector (read-only frame) | `sector_snapshot`; `stock_relative_strength()` | `relative_strength.py` (`rs_v1`) |

Layout: `inputs.py` (read-only loaders, a connection is handed in) → pure `risk_regime` / `relative_strength` / `breadth` / `sector_intel` →
`store.py` (the only writer). The runner is the single caller of the writers.

## 2. Properties (each has a test in `tests/test_mi_runner.py`)

* **Session-explicit / wall-clock independent.** `session_date` and `provenance` are required arguments with no default; the clock and the
  environment are never read (`test_isolation.py`). A `str` date raises `TypeError`.
* **Fails closed.** A date that is not a real, fully loaded session of the stock panel (weekend, holiday, partial load, before the data)
  raises `SessionNotAvailable`; it never writes an `UNAVAILABLE` placeholder row.
* **Deterministic.** Same stored inputs → same `content_hash`, independent of feature-set label and run time. Missing measurements hash as
  missing, not 0.
* **Idempotent / rerun-safe / concurrency-safe.** UNIQUE keys + `ON CONFLICT DO NOTHING`: a re-run, or four concurrent runs, write each row
  exactly once. A re-run whose inputs were *restated* (the price provider rewrites history in place) reports `differs_from_stored=True` with a
  warning and leaves the stored row alone. A correction is a new `feature_set_version`, never an edit.
* **Honest provenance.** `observed` is accepted only when ALL hold: the sector map is point-in-time evidenced; the session is the newest
  stored stock bar (an older session cannot be a live capture); no index bars exist after the session; no price discontinuity was detected
  after the session. Anything else is `reconstructed`, carries `reconstruction_basis`, and has `sector_pit_safe = FALSE` (CHECK in M24).
* **Dry run by default.** `--apply` requires `--code-ref`.

## 3. Methodology

### 3.1 Regime (`risk_regime_v1`, unchanged — a declared heuristic, not a validated model)
Seven components, each normalised to a score in [−1, +1] and weighted: S&P 500 trend structure 0.25, S&P 500 20-session change 0.10, stocks
above 50-day average 0.20, above 200-day average 0.10, net 52-week highs minus lows 0.10, VIX level 0.15, Russell 2000 vs S&P 500 (20
sessions) 0.10. The score is the weight-normalised mean over *present* components; ≥ +0.30 → RISK_ON, ≤ −0.30 → RISK_OFF, else NEUTRAL.
A missing component is excluded and the weights renormalised; below 0.70 present weight the state is `UNAVAILABLE` with a NULL score. Raw
inputs, normalised score, weight, presence and reason are stored per component (`regime_components`).

### 3.2 Breadth (`breadth_v1`)
Metrics: above SMA50, above SMA200, new 52-week high, new 52-week low, positive 20-session return, advancing 1d, declining 1d. Every metric
is a record `{numerator, denominator (eligible), universe, missing, pct, state}`. A stock without the needed history/bar **leaves the
denominator and is counted in `missing`** — it is never treated as bearish (and a non-positive close is missing). `pct` is withheld
(`insufficient_eligible`) below 1,000 eligible stocks for the market and 5 for a sector; the counts are still stored. A non-real session
yields `no_session`, never zeros. Market counts reconcile exactly with the regime components' stored `n_above / n_valid` (tested).

### 3.3 Sector (`sector_v1`, over `rs_v1`)
Per sector: member count; returns over 5/20/60 sessions (median of valid member returns, ≥ 5 members, equal weight); vs `^GSPC` and vs the
universe median; rank over 20 sessions; breadth and participation from `breadth_v1` on the sector's members; per-horizon coverage
(`n_valid`, `n_excluded` — discontinuity exclusions). A `strength_state` tercile label (`leading / in_line / lagging`) is derived from the
20-session rank and is `not_available` when fewer than 3 sectors are ranked. A thin sector keeps its counts and has NULL returns / rank —
never zero. Sector `Unknown`/NULL is not a sector.

### 3.4 Relative strength (`rs_v1`)
Sector vs market (vs `^GSPC`, and vs the universe median), stock vs market (`ret − spx_ret`), stock vs its sector (`ret − sector median`),
and a 0–100 percentile within the universe. Per-stock RS is available through `stock_relative_strength()` (read-only) but **is not
persisted**: M24 has no per-stock table. Recommended for Slice 3 as a typed table (needs a migration).

## 4. Sector point-in-time audit (the important finding)

The only sector source is `daily_fundamentals.sector` (a yfinance `info` field). The audit of the dev copy found:

* ~96 % of the rows were **bulk-created on one day (2025-07-10) for dates reaching back to 2023**. A row's `date` is therefore not evidence of
  when its sector was known. Only a row whose write time (`created_at` / `updated_at`) is on or before the session carries PIT evidence.
* Rows are not rewritten in place per date, but a later bulk load back-fills older dates with *today's* classification.
* Consequence: for historical sessions, almost no symbol has a PIT-evidenced sector. Two rules are implemented:
  * `pit_evidenced` (default): the latest row with `date ≤ session` **and** `write time ≤ session`. Symbols without such a row are
    **unclassified** (honest gap, not neutral). The audit records `n_symbols_with_rows`, `n_in_map`, `n_classified`, `n_without_evidence`.
  * `projected`: the latest row with `date ≤ session`, whatever its write time — wider coverage, explicitly **not** PIT; the audit counts
    `n_projected_backwards`, and `observed` is refused under it.
* Neither rule makes history PIT-safe by assertion. A reconstructed row says `sector_pit_safe = FALSE`, names the rule, and states in
  `reconstruction_basis` that prices are restated in place. Only forward capture (a live run for the newest session with a PIT-evidenced
  map, producing `observed`) is PIT-safe, and only from the day capture starts.

## 5. Known limitations (reported, not hidden)

1. Prices are provider-adjusted and restated in place; a reconstructed run is not what a live run saw. `price_basis` is recorded.
2. Sector vs `^GSPC` compares an equal-weight median with a cap-weighted index; vs the universe median is like-for-like.
3. No liquidity / price / market-cap filter in v1: micro-caps can move a thin sector's median.
4. Breadth is JSONB-only (`coverage.measurements`); typed breadth columns would need a migration (not allocated).
5. Per-stock RS is not persisted.
6. The dev index history starts 2025-09-19, so older sessions have missing index components (state `UNAVAILABLE` or reduced coverage).
7. The universe is whatever the stored panel holds (data-source survivorship applies).

## 6. What CI must prove (real Postgres)

`mechanism/market_intelligence/tests` runs in the real-Postgres job (a skip is a failure there). Locally the suite ran against a disposable Postgres 16
cluster with a superuser role (200+ tests); CI must prove it under its own real-Postgres role, where any skip is a failure.

## 7. Activation (out of scope, owner-authorised stages)

Applying migrations 24/25, giving `donchian_app` INSERT on the MI tables, choosing a schedule, and deciding whether the API/Telegram
surfaces read `observed` rows are separate stages. The model-comparison caveat stands: regime is a heuristic and must not be fed to a
screener or an ML feature until its forward behaviour has been measured with `fwd_v1` labels.
