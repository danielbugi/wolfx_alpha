# Market Intelligence — strategy-neutral market context

> **Status: BUILT LOCALLY, NOT DEPLOYED, NOT ACTIVATED.** Migrations 24 and 25 are not applied to production, nothing writes the
> tables, nothing scheduled runs the package, and the Telegram post is not in any package or rotation. Production is frozen at `4d9bf93`.
> Deploying the image that contains this code is behaviour-neutral (proved by
> `mechanism/market_intelligence/tests/test_activation_neutral.py`, §7). Commit ≠ deploy ≠ activate.

## 1. What it is, and what it is not

Session-level, **strategy-neutral** context about the US market, stored immutably and exposed read-only:

| Piece | Module | One line |
|---|---|---|
| `risk_regime_v1` | `risk_regime.py` | Descriptive daily state (`RISK_ON` / `RISK_OFF` / `NEUTRAL` / `UNAVAILABLE`) from seven weighted, normalised components. |
| `rs_v1` | `relative_strength.py` | Per-stock returns (5/20/60 sessions), sector medians, universe median, RS percentile, sector rank. |
| Provenance | `provenance.py` | The one place that decides which rows a read may see: `observed` vs `reconstructed`. |
| Event model | `events.py` | Earnings/catalyst event identity + append-only revisions with a `known_at` basis. **Foundation only.** |
| Store | `store.py` | The only module that imports a DB driver. Insert-only writers, observed-by-default readers. |
| Payload | `payload.py` | One normalised payload shared by the API and the Telegram renderer. |

**It is not** a Donchian filter, an eligibility rule, a screener score input, or an ML feature. Strategy identity
(`donchian_breakout v1`) and eligibility are unchanged. Nothing in `mechanism/screeners`, `data_updaters`, `orchestrators`,
`research` or `strategy_analytics` imports it (pinned by test).

**`risk_regime_v1` is a V1 heuristic, not a validated predictive model.** The weights and thresholds are declarations, not estimates;
they were not fitted to outcomes. Its output is labelled that way in the API (`/api/market-intelligence/definitions`) and in the channel
post footer. Do not use it as a trading signal or to gate anything until it has been evaluated out of sample under a new model version.

## 2. Schema (migrations 24 and 25 — additive, marker-guarded, atomic, NOT applied to production)

| Migration | File | Tables |
|---|---|---|
| 24 | `mechanism/add_market_snapshot_tables.sql` | `universe_snapshot`, `market_snapshot`, `sector_snapshot` |
| 25 | `mechanism/add_market_event_tables.sql` | `market_event`, `market_event_revision` |

Both: `psql -v ON_ERROR_STOP=1 -1 -f …` (one transaction), `IF NOT EXISTS` / marker-guarded, independent of 22/23. Immutability is
enforced in the database by `ENABLE ALWAYS` triggers (`research_market_guard()` blocks UPDATE/DELETE/TRUNCATE;
`research_market_event_stamp()` stamps `ingested_at` / `known_at` from the database clock so a caller cannot back-date). Provenance
rules are CHECK constraints and composite foreign keys (a sector/market row can only reference a universe snapshot of the same
session and provenance). Both files are mounted in `docker-compose.yml`'s init list (fresh dev/CI databases only) and are in the CI
migration set. **They reach production only by an operator applying them manually.**

## 3. Provenance and point-in-time limitations

* **observed** — captured by the pipeline for that session at the time, or an event we recorded ourselves.
* **reconstructed** — recomputed later from stored history. Never presented as what was known then.
* Live and PIT reads are **observed-only by default**; `include_reconstructed=True` is an explicit opt-in and every reader returns the
  row's provenance. The API, the Telegram loader and `events.ml_view` never opt in.

**Sector map — the honest status.** The only sector source is `daily_fundamentals.sector`, a yfinance `info` field that is
**rewritten in place** and bulk-loaded with lag. A sector map for a *past* session is therefore **reconstructed and never PIT-safe**
(`sector_map_semantics` carries `pit_safe: false` and a caveat). Only a snapshot captured forward, by a pipeline run for that
session, can be `observed`. No historical sector assignment may be described as "as of" unless a future source proves it.

Other limits to keep in mind when reading the numbers:

* Returns are price returns on stored closes; a symbol with a detected price discontinuity in the window has that return NULL.
* Sector return = **median** of valid member returns, **minimum 5 members**, equal weight. **No liquidity / price / market-cap filter
  in v1** (a deliberate, versioned change if ever wanted) — micro-cap noise can move a thin sector's median.
* Sector vs `^GSPC` compares an equal-weight median with a cap-weighted index; the like-for-like benchmark is the universe median.
* The universe is whatever the stored panel holds (survivorship of the data source applies).
* A component whose inputs are not on the session is MISSING, never carried forward and never zero; below 70 % of the weight the
  state is `UNAVAILABLE` with a NULL score.
* **Events:** no collector, no vendor, no backfill. The existing `earnings_calendar` is **not** copied in (its first-seen time was never
  recorded, so it cannot prove when anything became known). No model may consume an event lacking PIT provenance (`ml_view`: observed,
  grade A/B, `known_at ≤ cutoff`).

## 4. API (read-only, authenticated)

`GET /api/market-intelligence/definitions` (no DB), `/snapshot?session=…`, `/history`. No write route exists. If the tables are
absent or the app role lacks privilege the service answers `available: false, reason: not_provisioned` — never a 500. The router
include in `backend/main.py` is inside try/except, so a failure cannot stop the app from starting. Canonical domain terms
(`RISK_ON`, regime, …) are allowed here and in the dashboard.

## 5. Telegram — "Market environment" (built, not enabled)

`channel_content.post_market_environment` renders a **text-only, neutral-wording** post ("Broadly positive / Mixed / Broadly negative",
fixed-rules caveat). It is silent (returns None) unless the payload is observed, for exactly that session, and the regime is available.

* **Not in `publish_post_market.POST_MARKET_KINDS`**, not in `pick_kinds`/`ROTATION`. `load_context(with_intel=False)` is the default,
  so the live post-market path never loads it. A manual `send_channel_posts` run for this kind goes through the atomic
  `post_delivery.claim` (`CLAIMABLE_KINDS`).
* **Enabling it later is a separate, explicit decision** and adds one more claim/post per day. Requirements before that:
  1. migrations 24/25 applied; `deploy/db/research_roles.sql` re-run and `research_roles_verify.sql` re-checked (the app role gets
     SELECT+INSERT on the five tables, admin SELECT, PUBLIC nothing);
  2. a writer that captures an **observed** snapshot at the session (does not exist yet);
  3. the pinned START_HERE post is at its length limit (~3.93k of the 4,000-character test limit). A dedicated service note
     (~330 chars) breaks `test_start_here_and_promo_caption_are_valid_telegram_html_within_limits`, so `NOTE_FOR_KIND["market_environment"]`
     currently reuses the `market_health` note. A dedicated note needs a START_HERE restructure;
  4. the owner's copy review against `CHANNEL_VOICE_AND_COPY.md`.

## 6. Advice-wording guard

The existing production guard (the `BANNED` regex, duplicated in several tests) is **not changed** by this work. Its known weaknesses are
documented separately in [ADVICE_GUARD_REVIEW.md](ADVICE_GUARD_REVIEW.md).

## 7. Behaviour-neutral activation

Deploying the new mechanism image with no effective boundary preserves `4d9bf93` behaviour:

| Claim | Evidence |
|---|---|
| `GUARDS_EFFECTIVE_FROM` unset ⇒ legacy screener behaviour | `mechanism/screeners/tests/test_guards_effective_from.py`, `test_universe_guards.py` |
| Nothing writes the MI tables | `test_activation_neutral.py` (no caller of `write_session` / `write_universe_snapshot` / `append_event_revision` outside `store.py`) |
| Nothing scheduled runs it | same (no `deploy/` unit/script, no compose/pipeline reference; CI only *tests* it) |
| No runtime code applies migrations 24/25 | same |
| Backend start-up cannot be broken by it | AST check: include is in try/except; API degrades to `not_provisioned` |
| Telegram cannot reach it | same + `test_market_environment.py` (not in `POST_MARKET_KINDS`, not in 400 days of `pick_kinds`) |

## 8. Tests

```bash
# scratch Postgres 16 only -- never production (see docs/dev/TESTING.md)
python -m pytest mechanism/market_intelligence/tests mechanism/research/tests -q
python -m pytest backend/tests/test_market_intelligence_api.py mechanism/alerts/tests/test_market_environment.py -q
```

`test_import_separation` / `test_isolation` pin that pure modules import no DB driver and that no pipeline package imports this one.

## 9. Writer / runner (lab, not scheduled)

`mechanism/market_intelligence/runner.py` (`mi_v2`) is the single, operator-run, unscheduled caller of the writers: regime + breadth
(`breadth_v1`) + sector (`sector_v1`) + relative strength for one explicit session, dry-run by default, fail-closed on a non-session,
`observed` only with PIT-evidenced sectors on the newest session. The sector point-in-time audit (bulk-loaded `daily_fundamentals`,
~96 % of rows created on one day) and all methodology: [docs/research/MI_RUNNER_B1.md](../research/MI_RUNNER_B1.md). Section 3's
statement that historical sector maps are never PIT-safe stands.
