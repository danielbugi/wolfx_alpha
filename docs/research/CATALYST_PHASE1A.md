# Catalyst Intelligence, Phase 1A: SEC filings into a scratch database (validated; nothing deployed)

Status: implemented and validated in an isolated scratch environment only. **Nothing here is deployed, migrated in production, scheduled or imported by the pipeline, the
tracker, the collector or the bot.** Migration 37 has NOT been applied to production. Owner approval is required for every production step in section 9.

## 1. Scope (owner-approved 2026-10-16)

Forms: 8-K, 8-K/A, 10-K, 10-K/A, 10-Q, 10-Q/A, NT 10-K, NT 10-Q. Everything else the feed lists is skipped, not stored. Reuses the existing contract
(`filing_contract`), collector (`edgar_collector`), first-seen log (migration 27), event model (25) and classification layer (28). No ML, no text parsing, no new strategy.

| Piece | File | Notes |
|---|---|---|
| Live SEC transport | `market_intelligence/sec_transport.py` | the only module that opens a connection; declared User-Agent (validated, never printed), 5 requests/s default (hard cap 10), retries with exponential backoff + jitter, `Retry-After` honoured and capped, 403 never retried and opens the circuit, 404 is an answer, circuit breaker after 5 consecutive failures, https + `data.sec.gov`/`www.sec.gov` only, gzip, body-size cap |
| Source snapshots, identifier links | migration 37 `add_catalyst_ingest_tables.sql` | `source_snapshot`, `identifier_link`, `identifier_link_end`; append-only, database-stamped times |
| Identifier logic (pure) | `market_intelligence/identifier_links.py` | ticker-file parsing, update plan (new / ended / unchanged), truncation and staleness refusals, as-of resolution |
| Scope + rules (pure) | `market_intelligence/catalyst_rules.py` | `items_rule` v1 unchanged (8-K Item 2.02), new `form_rule` v1 (NT notices, periodic reports) |
| Ingest (DB, insert-only) | `market_intelligence/edgar_store.py` | first-seen -> fact -> labels, each filing under its own savepoint; idempotent; dry-run mode |
| Strict reader (DB, select-only) | `market_intelligence/catalyst_reader.py` | the point-in-time visibility rules of section 3 |
| Scratch dry-run script | `ml_training/experiments/catalyst_phase1a_dryrun.py` | refuses any database not named `scratch*` |
| Operational scripts | `deploy/db/preflight_37.sql`, `deploy/db/rollback_37.sql` | read-only gate; guarded rollback (refuses while rows exist) |

Small additive edits to existing files: `NT 10-K` / `NT 10-Q` added to `FORM_FAMILY`; migration 37 added to the compose init list, the migration registry, the role scripts (append-only tables are
excluded from the full-DML baseline) and their tests; new guard tests in `test_isolation.py` / `test_activation_neutral.py`.

## 2. Design decisions that matter

* **The event never stores a symbol.** `market_event.symbol` is NULL for SEC events ("filer-scoped"); the CIK is in the payload. The symbol is resolved at READ time from `identifier_link` as of the
  decision cutoff. A late mapping, a ticker change or a re-used symbol therefore cannot rewrite an earlier decision, and the existing tracker reader (which matches `market_event.symbol`) cannot
  consume the feed by accident.
* **Links are dated observations, not facts.** OBSERVED = we captured the SEC ticker file on a date; the pair holds from that date (`valid_from`). RECONSTRUCTED = today's file projected back
  (no start date), only created on request, always labelled, never upgraded. A later file that lacks a pair appends a closure (`absent_on` = that file's date; the true change lies between the
  last file that had it and that date, which is recorded, not guessed).
* **A truncated or wrong ticker file cannot close the world.** More than 20% of open pairs disappearing, a file under 1,000 pairs, or a snapshot older than one already applied is refused with nothing written.
* **The feed does not say what an amendment amends**, so `amends_accession` stays NULL (never guessed). An amendment is its own filing and its own event.
* **A corrected filing** (same accession, different content) is revision n+1 with its own first-seen link and its own labels; an earlier decision keeps seeing revision n.

## 3. Point-in-time contract

A filing is visible to a decision at `cutoff` only if ALL of these held at or before `cutoff`:

| Time | Where it lives | Stamped by |
|---|---|---|
| SEC acceptance | `market_event_revision.known_at` (= `published_at`, grade A, `observed`) | the SEC (verbatim, must be an explicit UTC instant) |
| First observed | `source_observation.observed_at` of the revision's own first-seen link | database |
| Database ingestion | `market_event_revision.ingested_at` | database |
| Symbol link | `identifier_link.recorded_at`, and no closure with `identifier_link_end.recorded_at <= cutoff` that ends it for the filing date | database |
| Classification (for the label only) | `catalyst_classification.classified_at`, not superseded by a row classified at or before cutoff | database |

`available_at` of a returned row is the latest of acceptance, first observed, ingestion and link availability. Reconstructed provenance, grade X/C and unmapped or reconstructed-link filings are
excluded by default (flags exist for research, never for a model input). Re-asking the same question after more rows were appended returns the same answer (tested).

## 4. Existing tracker reader: incompatibility (NOT changed)

`paper_runner._catalysts` cannot safely consume this feed. Findings, each demonstrated by a test:

1. It gates on `known_at <= cutoff` only. A filing accepted before the cutoff but ingested after it (the normal case: acceptance precedes first sight) is returned for a decision that could not have
   seen it. The tracker's own context carries `decision_cutoff` earlier than the time the context is built (observed in production: cutoff 08:53 Jerusalem, built at the 09:30 fire), so this is a real exposure.
   `test_the_existing_tracker_reader_shows_a_decision_a_filing_that_was_ingested_after_its_cutoff` fails the old reader and passes the strict one.
2. It matches `market_event.symbol`, which for SEC events is NULL on purpose. Result today: **the feed is inert for the tracker** (`test_sec_events_have_a_null_symbol_...`), so loading it
   cannot change a forward observation until the reader is replaced. This is the safe default, not an activation.
3. It does not check first-seen, link availability or ticker changes.

Proposed separate, reviewed fix (not implemented, needs its own approval and a tracker image pin change in a maintenance window): replace the SQL in `_catalysts` by a call to
`market_intelligence.catalyst_reader.visible_filings(cur, symbols, cutoff, lookback_days=CATALYST_DAYS)` (read-only; observed links only), mapping its rows to the existing context shape; allow that one
import in `test_isolation.py`. Until then keep the feed unconsumed.

## 5. Validation results

RESULTS_PLACEHOLDER

## 9. Minimal production rollout proposal (each gate needs its own owner approval; none is requested by this document)

ROLLOUT_PLACEHOLDER
