# Catalyst Intelligence, Phase 1A: SEC filings into a scratch database (validated; nothing deployed)

Status: implemented and validated in an isolated scratch environment only (2026-10-16). **Nothing here is deployed, migrated in production, scheduled, or imported by the pipeline,
the tracker, the collector or the bot.** Migration 37 has NOT been applied to production. Production was re-checked read-only at the end of the work (section 8). Every production step in
section 10 needs its own owner approval; none is requested by this document.

## 1. Scope (owner-approved 2026-10-16)

Forms: 8-K, 8-K/A, 10-K, 10-K/A, 10-Q, 10-Q/A, NT 10-K, NT 10-Q. Everything else the feed lists is skipped, not stored. Reuses the existing contract (`filing_contract`), collector
(`edgar_collector`), first-seen log (migration 27), event model (25) and classification layer (28). No ML, no text parsing, no new strategy.

| Piece | File | Notes |
|---|---|---|
| Live SEC transport | `market_intelligence/sec_transport.py` | the only module that opens a connection; declared User-Agent (validated, never printed), 5 requests/s default (hard cap 10), retries with exponential backoff + jitter, `Retry-After` honoured and capped, 403 never retried and opens the circuit, 404 is an answer, circuit breaker after 5 consecutive failures, https + `data.sec.gov`/`www.sec.gov` only, gzip, body-size cap |
| Source snapshots, identifier links | migration 37 `add_catalyst_ingest_tables.sql` | `source_snapshot`, `identifier_link`, `identifier_link_end`; append-only, database-stamped times |
| Identifier logic (pure) | `market_intelligence/identifier_links.py` | ticker-file parsing, update plan (new / ended / unchanged), truncation and staleness refusals, as-of resolution, share classes, claimants |
| Acceptance reconciliation (pure) | `market_intelligence/acceptance_check.py` | reconciles the feed's acceptance time with the SEC header record (section 3) |
| Scope + rules (pure) | `market_intelligence/catalyst_rules.py` | `items_rule` v1 unchanged (8-K Item 2.02), new `form_rule` v1 (NT notices, periodic reports) |
| Ingest (DB, insert-only) | `market_intelligence/edgar_store.py` | first-seen -> fact -> labels, each filing under its own savepoint; idempotent; dry-run mode |
| Strict reader (DB, select-only) | `market_intelligence/catalyst_reader.py` | the point-in-time visibility rules of section 4 |
| Scratch scripts | `ml_training/experiments/catalyst_phase1a_dryrun.py`, `catalyst_latency_probe.py` | the dry run refuses any database not named `scratch*`; the probe touches no database |
| Operational scripts | `deploy/db/preflight_37.sql`, `deploy/db/rollback_37.sql` | read-only gate; guarded rollback (refuses while rows exist) |

Small additive edits to existing files: `NT 10-K` / `NT 10-Q` added to `FORM_FAMILY`; migration 37 added to the compose init list, the migration registry, the role scripts (append-only tables are
excluded from the full-DML baseline), the activation runbook's digest manifest (the same precedent as migrations 35 and 36) and their tests; new guard tests in `test_isolation.py` / `test_activation_neutral.py`.

## 2. Design decisions that matter

* **The event never stores a symbol.** `market_event.symbol` is NULL for SEC events ("filer-scoped"); the CIK is in the payload. The symbol is resolved at READ time from `identifier_link` as of the
  decision cutoff. A late mapping, a ticker change or a re-used symbol therefore cannot rewrite an earlier decision, and the existing tracker reader (which matches `market_event.symbol`) cannot
  consume the feed by accident.
* **Links are dated observations, not facts.** OBSERVED = we captured the SEC ticker file on a date; the pair holds from that date (`valid_from`). RECONSTRUCTED = today's file projected back
  (no start date), only created on request, always labelled, never upgraded. A later file that lacks a pair appends a closure (`absent_on` = that file's date; the true change lies between the
  last file that had it and that date, which is recorded, not guessed).
* **A filer with several listed classes maps to all of them** (GOOG and GOOGL, BRK-A and BRK-B): an issuer-level filing concerns every class. **A symbol claimed by two filers on the same
  date is ambiguous** and is attached to neither (found by the dry run: the first resolver treated multi-class filers as ambiguous and dropped 19 of 123 filings; fixed and tested).
* **A truncated or wrong ticker file cannot close the world.** More than 20% of open pairs disappearing, a file under 1,000 pairs, or a snapshot older than one already applied is refused with nothing written.
* **The feed does not say what an amendment amends**, so `amends_accession` stays NULL (never guessed). An amendment is its own filing and its own event.
* **A corrected filing** (same accession, different content) is revision n+1 with its own first-seen link and its own labels; an earlier decision keeps seeing revision n.

Follow-up designs (proposals only): [SEC_DISCOVERY_ARCHITECTURE.md](SEC_DISCOVERY_ARCHITECTURE.md) (market-wide discovery) and [SEC_EARNINGS_TIMING_RERUN_PLAN.md](SEC_EARNINGS_TIMING_RERUN_PLAN.md).

## 3. Finding from the real SEC data: the feed's acceptance time cannot be trusted alone

The collector took `acceptanceDateTime` from the submissions JSON as the SEC acceptance instant. The dry run compared it with the SEC's own records for the same filings:

| Comparison | Result |
|---|---|
| 123 filings, 2026-09-01 .. 2026-10-15: feed value minus the instant given by the filing's SGML header (`<ACCEPTANCE-DATETIME>`, Eastern wall clock) | **81 are exactly +4.00 h later, 42 are equal, none earlier.** The header, the filing's index page and the live Atom list agree with each other |
| 24 older filings (3 large filers, 1 month to >1 year old) | all 24 shifted, by +4.0 h (18) or +5.0 h (6): the Eastern UTC offset in force on that date, applied a second time |
| 89 filings 2015-2026 across every daylight-saving change since 2022 (five large filers) | **28 standard time: +5.0 h; 61 daylight time: +4.0 h; 0 unexplained; page = header in 89 of 89** |
| 43 filings listed by EDGAR in the last 12 hours (feed as served now) | **0 shifted** (all equal to the Atom instant); of 17 older than 12 h, 10 were shifted |
| 11 brand-new filings caught while polling | feed equals Atom (0.0 h) at first sight |

Reading: the value is correct when a filing first appears and is later replaced, for most filings, by a value one Eastern UTC offset too late (the trigger is not known and is not predictable by time of day or weekday).
It is never earlier than the truth, so using it could only make a feature later than reality (the safe direction for leakage). The damage is elsewhere: session timing (BMO / intraday / AMC) is wrong
for the shifted ones (**BMO 12 vs 41 and INTRADAY 37 vs 9 in this sample**), a late value on a fresh filing would be refused by the database rule `known_at <= ingested_at`, and a poller that looks
only hours after the fact sees the shifted values.

**The shift is explained and exact** (gate validation, `dst_validation_output.txt`: 89 filings of five large filers, 2015-2026, sampled across every daylight-saving change since 2022; the filing page's "Accepted" equals the header wall clock in 89 of 89):
feed minus the true instant is exactly the Eastern UTC offset in force at the acceptance instant, **5.0 h for all 28 standard-time filings and 4.0 h for all 61 daylight-time filings, 0 unexplained**, including the weeks either side of 2022-03-13, 2022-11-06, 2023-03-12,
2023-11-05, 2024-03-10, 2024-11-03, 2025-03-09, 2025-11-02 and 2026-03-08. The feed's "Z" value is the true UTC instant plus the Eastern offset, i.e. the offset applied a second time.

Handling (`acceptance_check.py`, strict, no repair beyond this one evidenced correction): equal -> keep; feed later than the header by EXACTLY the Eastern offset at the header instant (4 h in daylight time, 5 h in standard time) -> use the header instant;
anything else (the header later, 3 h, or 5 h in daylight time, any other gap, an unparsable or ambiguous header) -> refuse the filing. Both values and the decision are stored in `payload.acceptance`; the header is snapshotted. A known filing reuses its stored header time, so a re-poll writes
nothing; if the feed value later drifts, the changed record becomes revision n+1 (append-only), and a decision before that still sees revision n. A filing whose header cannot be fetched is postponed, not guessed.
**Consequence for earlier work:** the 2026-10-11 lab study (reaction bars, BMO / AMC placebo) used feed values; its timing-dependent results should be re-verified against headers before being relied on.

## 4. Point-in-time contract

A filing is visible to a decision at `cutoff` only if ALL of these held at or before `cutoff`:

| Time | Where it lives | Stamped by |
|---|---|---|
| SEC acceptance | `market_event_revision.known_at` (= `published_at`, grade A, `observed`), reconciled per section 3 | the SEC (header record) |
| First observed | `source_observation.observed_at` of the revision's own first-seen link | database |
| Database ingestion | `market_event_revision.ingested_at` | database |
| Symbol link | `identifier_link.recorded_at`, and no closure with `identifier_link_end.recorded_at <= cutoff` that ends it for the filing date | database |
| Classification (for the label only) | `catalyst_classification.classified_at`, not superseded by a row classified at or before cutoff | database |

`available_at` of a returned row is the latest of acceptance, first observed, ingestion and link availability. Reconstructed provenance, grade X/C and unmapped, ambiguous or reconstructed-link filings are
excluded by default (flags exist for research, never for a model input). Re-asking the same question after more rows were appended returns the same answer (tested).

## 5. Existing tracker reader: incompatibility (NOT changed)

`paper_runner._catalysts` cannot safely consume this feed. Each point is demonstrated by a test:

1. It gates on `known_at <= cutoff` only. A filing accepted before the cutoff but ingested after it (the normal case: acceptance precedes first sight) is returned for a decision that could not have
   seen it. The tracker's own context carries a `decision_cutoff` earlier than the time the context is built (observed in production: cutoff 08:53 Jerusalem, built at the 09:30 fire), so the exposure is real.
   `test_the_existing_tracker_reader_shows_a_decision_a_filing_that_was_ingested_after_its_cutoff` fails the old reader and passes the strict one.
2. It matches `market_event.symbol`, which for SEC events is NULL on purpose. Today **the feed is inert for the tracker** (`test_sec_events_have_a_null_symbol_...`): loading it cannot change a forward
   observation until the reader is replaced. This is the safe default, not an activation.
3. It does not check first-seen, link availability, ticker changes or the acceptance reconciliation.

Proposed separate, reviewed fix (not implemented; needs its own approval and a tracker image pin change in a maintenance window): replace the SQL in `_catalysts` by a call to
`market_intelligence.catalyst_reader.visible_filings(cur, symbols, cutoff, lookback_days=CATALYST_DAYS)` (read-only; observed links only), mapping its rows to the existing context shape; allow that one
import in `test_isolation.py`. Until then keep the feed unconsumed.

## 6. Test results (scratch Postgres 16; the CI database job re-runs them with the compose init list)

| Suite | Tests | Covers |
|---|---|---|
| `test_sec_transport.py` | 20 | User-Agent on every request, https + host allow-list, rate spacing, gzip, 503/429 backoff and `Retry-After` cap, jitter floor, bounded retries, 404 as an answer, 403 and repeated failure open the circuit, bad body, snapshots of every body |
| `test_identifier_links.py` | 23 | parsing and malformed entries, first file, repeat, ticker change, symbol reuse, truncated / tiny / stale files, reconstructed labelling, link and closure availability, multi-class filers, symbol claimed by two filers |
| `test_catalyst_rules.py` | 12 | the eight-form scope, `form_rule` v1, `items_rule` v1 unchanged, fact pinning |
| `test_acceptance_check.py` | 30 | header parsing and DST, agree / feed-late / refused, BMO restored, the exact-offset rule at 12 daylight-saving boundary cases (01:00 EST and 07:00 EDT on the change days, both directions), collector-clock allowance |
| `test_edgar_store_db.py` | 27 | migration 37 re-run and collision refusal, append-only on all three tables, database-stamped times, snapshots, link refresh, idempotent ingest, revisions, amendments, a failing filing, dry run, reconciled ingest, preflight and rollback scripts |
| `test_catalyst_pit_db.py` | 26 | late ingest, late correction, late classification and supersession, late link, ticker change, symbol reuse, share classes, reverse ambiguity, each gate on its own, immutable cutoff answers, lookback, a randomised cross-check against an independent oracle (80 checkpoints), the old tracker reader leaking and the strict one not |
| `test_phase1a_poll_e2e_db.py` | 5 | the whole polling cycle against Postgres with the real transport and a fake opener (it would have caught two bugs found live) |
| `test_catalyst_phase1a_dryrun.py` | 10 | the scratch-only guards of the dry-run script |
| guards | 4 | transport is the only network code; reader is select-only; nothing outside the package, its tests and the two scratch scripts references the ingest |

Final local run of the affected suites after the exact-offset rule (scratch Postgres 16): **717 passed, 1 skipped** across `mechanism/market_intelligence/tests`, `mechanism/shared/tests`, `ml_training/tests` and the activation-manifest test.
The role-model tests (`test_roles`, `test_roles_full_schema`, which bootstrap the whole compose init list including migration 37) passed on the previous revision of the same code (778 passed, 1 skipped with them) and are re-run by the pull request's CI.
An earlier broader run (`research`, `forward_collection`, `shared`, `ml_training`, `market_intelligence`: 2,529 passed) had two failures that are
environmental: the two "password never appears" tests fail when the throwaway password is a substring such as `x`, and pass with a distinctive one. The pull request's CI re-runs everything.

**Mutation check** (one gate weakened at a time, the point-in-time tests must fail): 13 of 14 killed. The survivor is the explicit `known_at <= cutoff` clause: it is logically implied by the CHECK
`known_at <= ingested_at` together with `ingested_at <= cutoff`, so removing it cannot change an answer. It stays for explicitness.

## 7. Real SEC dry run (scratch database, 2026-10-16, 07:05-09:40 New York)

Sample: 124 symbols (112 drawn at random from the paper tracker's symbols, names only, plus 12 deliberate edge cases: share classes, funds, acquired names). Evidence: `docs/research/evidence/catalyst_phase1a_2026-10-16/`.

| Measure | Result |
|---|---|
| Transport | about 5,000 requests over all scratch runs, **every one HTTP 200, 0 retries, 0 errors, circuit never opened**; request p50 0.16 s, p95 0.29 s; 5 requests/s cap; 243 MB received in the longest run |
| Ticker file | 10,444 pairs, 0 rejected entries; stored once as a snapshot (0.80 MB raw, 0.22 MB gzip); 10,444 observed + 10,444 reconstructed links; a second application changed nothing |
| Symbols -> CIKs | 124 requested, **121 resolved (97.6%)**, 120 distinct CIKs; unresolved: HES, JNPR, WBA (acquired or delisted, absent from the current SEC file) |
| Filings since 2026-09-01 (Phase 1A forms) | **123 stored** from 65 filers: 108 8-K, 4 8-K/A, 10 10-Q, 1 10-K; none of NT 10-K / NT 10-Q in this sample |
| Rejected records | 32 per full poll, **all legacy 2004-2009 8-K item formats** ("5,7", "12,7") from before the window, refused by the contract and kept in the report with the reason; nothing in the window was rejected |
| Acceptance reconciliation | 81 feed-late (+4.0 h) used the header time, 42 agreed, 0 refused; session timing BMO 41 / AMC 73 / INTRADAY 9 (the feed values would have given BMO 12 / AMC 74 / INTRADAY 37) |
| Labels | 15 `earnings_release` (items rule), 11 `periodic_report` (form rule) |
| Identifier mapping | observed links: **0 filings** (by design: an observed link starts the day its ticker file was taken, and every filing predates it); reconstructed links: **123 of 123 filings mapped, 0 unmapped, 0 ambiguous**; 151 rows because some filers have two classes |
| Idempotency | re-ingesting the last batch wrote nothing (123 unchanged, 0 events / revisions / labels / first-seen rows); a second full poll and a second process over the same database also wrote nothing |
| Point-in-time audit | 0 violations of `known_at <= first observed <= ingested`; the strict reader returns **0 rows one microsecond before the first ingest** and 123 filings after the last |
| Snapshots | 245 kept (about 120 submissions bodies 2.7 MB gzip, 123 headers 0.06 MB, ticker file 0.22 MB) = **3.0 MB for the whole session**; unchanged documents are not stored again |
| Reader / link refresh at real size | link refresh on a synthetic next-day file (renames, a drop, a second class) 0.13 s; strict reader for 82 symbols 0.03 s |
| Latency (see the caveat) | probe over 48 minutes, 11 brand-new filings timed: **appeared in the Atom list within 0.9-2.4 min (p50 1.7) and in the submissions JSON within 1.0-3.0 min (p50 2.0, p90 2.4)** of the SEC acceptance instant, resolution 2 min |

Caveat on latency: the 120-filer sample produced **no new filing during 36 polling cycles (07:07-09:30 New York)**, so the per-company poll itself could not be timed; the probe times the same two SEC surfaces against the
market-wide new filings instead. Eleven filings is a small sample taken in one pre-market window.

Bugs found by the real run and fixed (each now has a test): multi-class filers dropped as ambiguous; the feed acceptance-time shift; the collector refusing a late-feed fresh filing before it could be reconciled;
submissions snapshots rolled back by a premature rollback in the script; the pre-ingest point-in-time check assumed an empty database.

## 8. Production boundaries kept

Read-only checks at the end (2026-10-16 13:38Z): `CURRENT_MECHANISM_SHA` = `FORWARD_COLLECTION_ARMED` = `5c511899505a`; `CURRENT_PAPER_SHA` = `PAPER_TRACKER_ARMED` = `45c2ac39249d`; bot `575fa5dc0599`; no migration-37 table exists;
`market_event`, `market_event_revision`, `catalyst_classification`, `source_observation`, `source_poll` all 0 rows; paper tables unchanged (2 / 4,985 / 3,373); no donchian unit mentions edgar, sec or catalyst. Production was only READ during this phase
(inventory queries and one read-only query for symbol names). The scratch Postgres, its containers, network and the stored contact file were deleted from the VPS afterwards.

## 9. Remaining risks

1. **Acceptance time provenance.** The reconciliation relies on the SGML header record. The shift is exactly the Eastern offset (89 of 89 older filings), but its cause and its trigger (why fresh filings are served correctly and most older ones are not) are not known, and the evidence is one day of SEC behaviour. A changed SEC behaviour would surface as refusals, not as silent errors; it needs monitoring (section 10).
2. **Earlier research timing.** The 2026-10-11 study used feed values; re-verify BMO / AMC and reaction-bar results against headers.
3. **No observed-grade history.** Observed links begin the day the first ticker file is taken, so catalyst data usable as observed (`ml_view`) accrues forward only; everything older is reconstructed and must stay out of model inputs.
4. **Latency evidence is thin** (11 filings, one pre-market window, 2-minute resolution). Collect over a full session, including the post-close wave, before choosing a polling schedule.
5. **Ticker changes are validated by tests and a synthetic file, not by a real one**; the real validation needs a few weeks of daily ticker files.
6. **Per-company polling cost at full universe**: about 3,000 CIKs at 5 requests/s is roughly 10 minutes and 430 MB of JSON per full poll (measured 144 KB per document). Discovery through the market-wide Atom list (one request per cycle) and fetching only the filers that appear is far cheaper; not built here.
7. **Only the `recent` block** of each submissions document is read (about the last 1,000 filings or one year); busy filers can push a filing out between polls if polling stops for long.
8. **The tracker cannot consume the feed** (section 5) until a reviewed fix; this is the safe default but means no forward observation will carry catalysts yet.
9. **Item-level meaning is not interpreted**: only Item 2.02 and the form families are labelled; 8-K items 1.01, 1.03, 3.01, 4.02, 5.02 are stored verbatim and unlabelled.
10. **SEC terms**: the declared contact is the owner's; a production run needs the operator's decision on whose contact is declared, and respect of the 10 requests/s ceiling across all First Light processes.

## 10. Minimal production rollout proposal (each gate needs its own owner approval; none is requested here)

Independent of the 2026-10-21 Stage 2 maintenance, which this work does not touch. Order matters; a later gate never starts before the earlier one is verified.

| Gate | What | Verified by | Rollback |
|---|---|---|---|
| G0 | Review and merge this branch. The code is dormant: nothing imports it, no timer exists, migration 37 is not applied, the next image build would carry the files but nothing calls them | CI green (database job re-applies migration 37 through the compose init list) | revert the commit |
| G1 | Fresh verified backup; `preflight_37.sql` (read-only) shows `ABSENT`; apply migration 37 atomically (`psql -1`); re-run `research_roles.sql` and its verifier | `PREFLIGHT_37 OK state=EXACT`, 9 triggers, role verifier passes | `rollback_37.sql` (refuses once any row exists unless a written data-loss approval is given); do NOT run `research_roles_rollback.sql` |
| G2 | A production runner (`edgar_ingest_run`) written, reviewed and tested; session-explicit, flock, dry-run by default, the same cycle as the scratch script but against the production database, fixed small CIK list. It needs a new mechanism image, so it ships with the next planned image change after Stage 2, never by editing the running pins | tests + a scratch rehearsal of the exact image | not deployed until approved; image rollback is the existing pin rollback |
| G3 | One manual production dry run, then one manual `--apply` for a small fixed list (about 30 CIKs, a few tickers' worth of filings); first ticker file snapshot recorded | counts, `PIT violations = 0`, strict reader at a cutoff before the run returns 0, a re-run writes nothing, tracker and dashboard output unchanged | stop; rows are immutable and inert (symbol NULL, the tracker ignores them); removal only through `rollback_37.sql` with a written approval after a backup |
| G4 | A systemd timer (new unit, committed to `deploy/vps/`, installed by hand): two or three fires per trading day, own lock, own 5-minute `TimeoutStartSec`, independent of the heavy pipeline; widen to the full universe by stages | status line per run (below) for 5 consecutive sessions | disable the timer; nothing else depends on it |
| G5 | The reviewed tracker-reader replacement (section 5) with a tracker image pin change in a maintenance window, after the paper-tracker verifier baseline is recorded | tracker verifier ALL PASS, a diff of one session's context before and after | previous tracker pin |
| G6 | Expose catalyst context in the forward dashboard and start the Big Winner research on forward data | read-only | feature flag off |

**Monitoring from G3 on** (one machine-readable line per run, plus an alert on any breach): run status complete/partial/failed; CIKs requested vs completed; HTTP error counts and circuit state;
acceptance reconciliation counts (agree / feed_late / refused) with an alert on any refusal or on a share of `feed_late` outside the 0-100% band by more than a day; header-unavailable count; rejected records by reason;
unmapped and ambiguous filings per day; first-seen lag p50/p90 (acceptance to first observed); new revisions (the source changing a filing); snapshot bytes per day (3.0 MB for the whole 120-filer, 36-poll scratch session because unchanged documents are stored once; a full universe is
expected in the tens of MB per day, to be measured at G3); ticker-file refresh: pairs, additions, closures, refused files; disk free.

Operator decisions needed before G3: which contact is declared to the SEC; the polling schedule (after the longer latency study); the initial CIK list.
