# Strategy Intelligence — analytics architecture

> Status: backend + canonical analytics layer (Phase 2, 2026-09-29). No dashboard page and no
> Telegram command consume it yet. Release B (candidate observations, feature snapshots) is **not**
> built — the API reports those capabilities as unavailable rather than as zero.

## One calculation layer

```
signal_ledger (+ strategies, stock_prices)
        │
mechanism/strategy_analytics/        ← the ONLY place strategy numbers are computed
  definitions.py   vocabulary, sample-size policy, capability flags (pure)
  analytics.py     SQL + shaping; takes an injected fetch(sql, params) -> rows
        │
        ├── backend/services/strategy_intelligence_service.py → /api/strategies/...   (dashboard)
        ├── backend/services/track_record_service.py          → /api/track-record/summary (stricter presentation)
        └── (next) mechanism/alerts bot commands              → private Telegram assistant
```

It lives in `mechanism/`, not `backend/services/`, because the Telegram assistant runs in the
mechanism image, which does not contain `backend/`. The backend imports it by path (same pattern
as `telegram_control_service.py`). It is not under `mechanism/shared/`, because importing that package
opens a connection pool. Each consumer passes its own connection via `fetch`.

## Canonical definitions

The authoritative text is `DEFINITIONS` in `mechanism/strategy_analytics/definitions.py`, also served
at `GET /api/strategies/definitions` (with `definitions_version`). Summary:

| Term | Definition |
|---|---|
| Open | `status='open'` and `evaluation_flag IS NULL` |
| Held | `status='open'` and `evaluation_flag IS NOT NULL` (`split_suspect`): open, excluded from evaluation until reviewed, still occupies its open-position slot |
| Resolved | `status` ∈ stopped, target1, target2, target3, expired |
| Winner | Resolved with `status` ∈ target1–3 (first target touched before the stop). **A positive-R expiry is not a winner.** |
| Ambiguous | Resolved with `resolution_flag='same_bar_stop_and_target'`; recorded as stopped (−1R) and counted as a loss |
| `outcome_r` | Single-exit R, where R = \|entry − stop\| = 2×ATR: target_k = +k, stopped = −1, expired = direction × (close at bar 20 − entry) / R. Not `plan_outcomes()`'s 3-tranche average. (Migration 19's column comment still says "mean of 3 tranches"; that comment is stale and applied migrations are not edited.) |
| Target win rate | winners / resolved. The denominator includes stopped (ambiguous stops too) and expired; open and held are excluded from both sides |
| Average / median R | Over all resolved signals. An expiry contributes its actual mark-to-market R, so a profitable expiry raises average R without being a target win. Expiries are also split into positive / negative / flat counts |
| Holding period | `bars_held`, in forward **trading bars** up to and including the resolving bar (not calendar days) |
| Reference session | `MAX(stock_prices.date)`, never the wall clock; "this week/month" is the ISO week/month containing it |

**Sample-size discipline:** every rate and average is `{value, n, state}`.
- `no_data`: n = 0 and `value` is null, so it is never rendered as 0% or 0R.
- `preliminary`: 0 < n < 5.
- `ok`: n ≥ 5.
- `not_available`: the stored data cannot support the metric (e.g. MFE is Release B).

The track record shows only `ok` values. Strategy Intelligence also shows `preliminary` ones, labelled as such.

## Endpoints (all behind `require_authenticated_user`)

| Route | Returns |
|---|---|
| `GET /api/strategies` | Every registered strategy with light tracking counts |
| `GET /api/strategies/definitions` | The definitions, vocabularies, sorts and capability flags |
| `GET /api/strategies/{key}/{version}/summary` | Tracking, performance, outcome distribution, bullish vs bearish, and capabilities, all from one grouped aggregate query |
| `GET /api/strategies/{key}/{version}/data-health` | Internal only. Ledger state, price-data and evaluation states of open signals, attention lists, 7 invariant checks, lineage coverage |
| `GET /api/strategies/{key}/{version}/signals` | Paginated (`limit` ≤ 200, `offset`, `total`, `has_more`). Filters: symbol, direction, status, lifecycle, evaluation/resolution flag, date range, model_version, sector, quality_grade. Sorts: newest, oldest, symbol, r_desc/asc, holding_desc/asc, grade |
| `GET /api/strategies/{key}/{version}/signals/{id}` | Identity, trade plan, context, lifecycle, provenance (Release-B lineage returned as null), timeline |

`sector` and `quality_grade` are denormalized onto the ledger row at signal time, so filtering on them
is point-in-time safe. `grade` sorts by the screener's own persisted grade; no new ranking is invented.
`/api/strategy` (singular, `routers/strategy.py`) is an unrelated endpoint that ranks today's screener output.

## Open-signal data health

Two independent dimensions per normally-open signal, measured in **market sessions** (distinct
`stock_prices` dates) so weekends and holidays never read as stale.

**Price data:**
- `awaiting_first_session`: the signal is on the latest session, so no forward bar can exist yet.
- `current`: the symbol has the latest session's bar.
- `lagging`: 1–2 sessions behind.
- `stale`: 3 or more sessions behind, or beyond the 30-session window inspected. Possible delisting or feed gap.

**Evaluation:**
- `up_to_date`: `last_evaluated_date` ≥ the reference session.
- `pending`: normal between prices landing and the 03:30 run. It persists when the symbol has no new bar, because the evaluator does not write a row it has nothing new for.
- `invalid_price_blocked`: a bar the evaluator refuses is on the path. The SQL predicate is pinned to `evaluate_signal_ledger._invalid_bar_reason()` by a drift test.

Held signals are listed separately with `held_since`.

## Evaluator run visibility — proposal, not built

Run results (`N examined → resolved / advanced / unchanged / blocked / held`) exist only in
`/opt/donchian/logs/channel_sender_runs.log` on the VPS. The API does not scrape that log. Data health
reports `evaluator_runs` as `not_available` and derives progress from the ledger itself
(`latest_evaluated_session_open`, evaluation states).

The smallest proper fix, if wanted, is a separately gated change:
- a migration adding an `evaluator_runs` table: `started_at`, `finished_at`, `reference_session`, the six counters, `error`;
- one `INSERT` at the end of `evaluate_signal_ledger.run()`, including on failure;
- data health reading the latest row.

## Query/index findings (production, 2026-09-29, 490 rows)

`EXPLAIN ANALYZE`, read-only:
- Summary aggregate: 1 ms.
- Open-signal data health: 11 ms.
- Signal page: 1 ms.
- Reference session: 0.2 ms.

Every `stock_prices` access uses the existing `(symbol, date DESC)` / `(date DESC)` indexes, including the
recursive skip-scan of recent sessions. `signal_ledger` is sequentially scanned, which is correct at this size. No index is
added now. When the ledger reaches hundreds of thousands of rows or several strategies,
`(strategy_id, signal_date DESC, id DESC)` is the index to evaluate first for the signal list.

## Known Release-A limitations

- **No post-exit trajectory.** Only the terminal outcome is stored, so "reached target k" means reached within the trade, and MFE is Release B.
- **No strategy name column.** The display name comes from a code map (`STRATEGY_DISPLAY_NAMES`).
- **`model_version` is never populated by a real model.** No validated model is scoring live signals, so the canonical value for every ledger row is NULL and coverage should report 0. **Known defect (found 2026-10-03):** since the first session under the new screener behaviour (2026-10-02, `GUARDS_EFFECTIVE_FROM`), rows for Grade A–C-alignment signals that ML attempted but could not score (`ml_confidence='no_model'`) carry the literal `'unknown'` instead of NULL — an incidental default in `multi_timeframe_screener.py` (`ml_data.get('ml_model_version', 'unknown')`, hit because `MLSignalEnhancer._unavailable()` returns no such key), not a model name. `'unknown'` therefore means "no model scored this", it makes `coverage.model_version` read non-zero, and it must not be read as "ML-scored". Research capture is unaffected (`research/observer.py` records NULL unless scored). Not yet fixed; do not normalise existing rows without an explicit decision.
- **No whole-feed staleness check.** A total market-data feed outage cannot currently be told apart cleanly from there simply being no newer market session: the reference session just stays at the last landed date. Feed-level health will be handled separately; pipeline monitoring covers it today.
- **No evaluator run history.** It is reported as `not_available` until a decision is made on persisting it (no migration 22 yet).
- **Offset pagination.** Deterministic (id tie-break). Keyset pagination can replace it if deep paging ever matters.
