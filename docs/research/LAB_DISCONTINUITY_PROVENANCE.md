# Price-discontinuity first-detection provenance (lab branch, not deployed)

Status: implemented on `lab/first-light-algo`; **nothing is deployed, re-pinned, migrated or modified in production.** Part 1 (sections 1-3) needs no schema change; part 2 (sections 4-9) adds migration 32 (owner approved the design and the lab implementation on 2026-10-08; production application is NOT yet authorized).

## 1. Root cause
`observe` (the collector's market / sector / relative-strength snapshot step) runs `market_intelligence.runner.compute_session(..., provenance="observed")`, which refuses a
session when any `price_discontinuities` row in its lookback was "detected after the session". Two defects combined:

1. **The writer destroyed the provenance.** Pipeline step 10 runs `ml_training/data_preparation/build_dataset.py --replace` every night: it `DELETE`d the whole table and re-inserted every row
   (and its upsert also did `detected_at = NOW()`). `detected_at` therefore meant "the time of the last rebuild", never "the first time this system saw the discontinuity".
2. **The reader compared a calendar date.** `detected_at::date > session_date` in the database's Asia/Jerusalem date. The successful nightly run is after midnight Jerusalem (about 01:00),
   so *every* in-window row looked retroactive for *every* session: `observed` was refused whenever the 420-day lookback held any discontinuity (18 on 2026-10-08, 1,040 rows in the table, all stamped 2026-10-08 03:09-03:11).

Production evidence (read-only, 2026-10-08): `by detected day: 2026-10-08 | 1040 rows`; a dry run for session 2026-10-07 returned `observed refused: 18 price discontinuities were detected after the session`.

## 2. Part 1: first-detection preserved (no schema change)
The two facts are kept apart: `date` is when the discontinuity happened, `detected_at` is the first time the system saw it. The event date is never compared with the session date.

**Writer (`build_dataset.py`)**
* `--replace` still replaces `ml_breakout_dataset_v2`, but **no longer deletes `price_discontinuities`**.
* `upsert_discontinuities`: a new row is stamped `clock_timestamp()` (the real instant, not the transaction start, which can precede the detection). An existing row **keeps its `detected_at`**, and its values
  are refreshed. The stamp moves (to now) only when the row is materially changed: the ratio changed by more than `1e-4` relative, or a missing ratio became known. Adjusted-price jitter from ordinary dividends moves the ratio by about 1e-12, so it cannot re-stamp.
* `prune_discontinuities`: after a **complete** `--replace` (not `--test`, not `--limit`, only after every chunk succeeded) rows that are no longer detected are deleted; a later re-appearance is a new fact with a new stamp.

**Reader (`market_intelligence/inputs.py`, `runner.py`)**
* `knowledge_cutoff(session)` = noon New York time on the calendar day after the session (an aware instant, derived from the session only, no clock). The overnight cycle that loads the session (pipeline, dataset rebuild, both collector fires: the latest at 08:15 Jerusalem) is over by then and the next cycle starts hours later (23:45 Jerusalem), in every US/Israel daylight-saving regime (tested).
* `load_discontinuities` returns `(by_symbol, late, untrusted)`: `late` = detection instant after the cutoff (retroactive information); `untrusted` = a stamp in the future of the database clock (the column is `NOT NULL`, so a missing stamp cannot exist). **Both refuse an `observed` run** (fail closed) and only warn on `reconstructed`.
* Judgement call for the owner: a discontinuity first found by the *same overnight cycle* that loaded the session counts as known (the run that writes the observation had it). A stricter rule (known only if detected before the session's own close) would refuse every night a new discontinuity appears (about two a week) and could never be satisfied for an event on the session day, so it is not used.

## 3. The 1,040 existing rows (provenance already overwritten)
They are **not touched and not given any timestamp**. Their stamp is the last destructive rebuild (2026-10-08 ~03:10 Jerusalem). That is a *late upper bound* on the true first detection, never an earlier one, so:
* a row passing the cutoff test is certainly known by then (sound);
* the only possible error is toward "late" (fail closed), for sessions before 2026-10-08 that were never going to be collected anyway.
For the first session after deployment the legacy stamps (2026-10-08 03:10 Jerusalem) are before the cutoff of any session on or after 2026-10-08, so they do not block it.

## 4. Audit of the existing writer and reader (what the current-state table can and cannot preserve)
| Case | Behaviour after part 1 | Evidence kept? |
|---|---|---|
| Repeated `--replace` | no table delete; upsert keeps `detected_at` | yes: stamps never move for a re-detected row |
| Late discovery (an old event found later) | a new row, stamped `clock_timestamp()` after the cutoff for sessions already observed | yes: the stamp is the discovery time; such a session's evidence stays "late" |
| Deletion (the provider restated it away) and reappearance | prune deletes after a complete run; a reappearance is a new row with a new stamp | the deletion itself is not stored, but migration 32 records that the table as it was after each scan matched a fingerprint, so a change after a scan is detected |
| Ratio change (restated bar) | re-stamped to now when the ratio moves more than 1e-4 relative; dividend jitter (about 1e-12) does not | yes (the earlier first-detection of that exact value is superseded, which only errs toward "late") |
| Correction of price history | invisible in a current-state table | **no**: this is what the price-input fingerprint in migration 32 adds |
| Scan failed / skipped / partial price input | nothing in the current-state table distinguishes "no discontinuity found" from "the detector did not run" | **no**: this is what the scan heartbeat adds |
| Legacy rows (provenance overwritten) | untouched, never backdated; their stamp is a late upper bound | n/a: only errs toward "late" |

The minimal append-only evidence that closes the two "no" rows is one table, `price_discontinuity_scan`. No per-row history table is needed: the row-level facts above are either preserved (stamps) or conservative (restamping), and the scan fingerprints make any silent change between the scan and the read detectable.

## 5. Migration 32: `price_discontinuity_scan`
File `mechanism/add_price_discontinuity_scan.sql` (additive, `IF NOT EXISTS`, refuses to adopt a same-named foreign object, atomic with `-1`). One immutable row per scan OUTCOME:

| column | meaning |
|---|---|
| `session_date` | the intended US session, passed explicitly by the pipeline (`--session`), never inferred from a clock |
| `status` | `complete` or `failed`; a skipped scan leaves no row |
| `failure_reason` | coded, exactly for `failed` (`price_data_not_at_session`, `input_changed_during_scan`, `scan_exception`) |
| `newest_bar`, `n_symbols`, `n_price_rows` | what the price input looked like |
| `input_fingerprint` | sha256 over every `stock_prices` row dated <= session (symbol, date, open, high, low, close): **recomputed by the database at insert**, so the claim cannot be a guess |
| `n_discontinuities`, `result_fingerprint` | sha256 over the `price_discontinuities` key set plus each row's first-detected stamp: recomputed by the database at insert |
| `started_at` | the builder's start (database clock read before scanning); validated <= `finished_at` |
| `finished_at` | **overwritten by a trigger with `clock_timestamp()`**: the database-stamped completion time |
| `run_id`, `writer`, `code_ref` | audit |

Guarantees enforced IN THE DATABASE, not by convention (triggers are `ENABLE ALWAYS`; the runtime role has `SELECT, INSERT` only; the admin group has `SELECT`):
1. A `complete` row is accepted only if the newest bar of the WHOLE price table equals `session_date` (the detector reads the whole table, so the session must be loaded and nothing newer mixed in), the claimed input fingerprint equals the one recomputed now (prices did not change while the scan ran), and the claimed result fingerprint equals the discontinuity table as it is now.
2. A `failed` row cannot carry a fingerprint, so it can never be mistaken for a heartbeat.
3. Retries are idempotent: unique partial index on `(session_date, input_fingerprint, result_fingerprint) WHERE status='complete'` plus `ON CONFLICT DO NOTHING` in the writer. The FIRST completion time stands; an identical retry after the cutoff cannot launder a late scan. A vendor correction changes the fingerprint, so it needs a new scan, and the old row stays as history.
4. Rows are immutable (UPDATE / DELETE / TRUNCATE raise, even for the owner).

The builder (`build_dataset.py --replace --session S`) records the scan as the LAST step of a complete run, in the SAME transaction as the prune: the prune and the evidence become visible together or not at all. It fingerprints the price input at the START of the run; the database re-fingerprints at the end, so a price change in between is refused (`input_changed_during_scan` is recorded as a failed scan, the run exits 1, and the prune rolls back). A session that is not the newest loaded bar exits 3 (failed row). A crash leaves a `scan_exception` row. A manual run without `--session`, or a partial run, records nothing (and prunes nothing).

## 6. How the heartbeat proves the scan is for the right data
`inputs.discontinuity_scan_evidence(conn, session)` (read-only; used by every `observed` run in `runner.compute_session`, hence by the collector's `observe` step BEFORE anything is written) requires ALL of:
1. the newest stored price bar is the session (else `price_data_not_at_session`);
2. a scan row exists FOR THIS SESSION (else `scan_missing`; a scan of another session is never counted);
3. a COMPLETE scan exists (else `scan_failed`);
4. its `input_fingerprint` equals the fingerprint of the prices **as stored now** (else `scan_input_mismatch`: prices changed after the scan, a partial ingestion was completed later, or the scan is of an earlier price version);
5. its `result_fingerprint` equals the discontinuity table **as stored now** (else `scan_result_mismatch`: the table was touched after the scan, including a deletion, a reappearance or a restamp);
6. the EARLIEST such scan completed (database clock) no later than `knowledge_cutoff(session)` (else `scan_after_cutoff`);
7. migration 32 is applied (else `scan_evidence_unavailable`).
Any failure raises `ProvenanceRefused`, which the collector reports as a failed `observe` step: the session is INCOMPLETE (exit 2) until a valid scan exists, or MISSED (exit 4) after the decision deadline. Nothing is written, and nothing downgrades a provenance failure to success. The per-row `late` / `untrusted` stamp checks of part 1 still apply in addition.

## 7. Cutoff semantics (noon New York, next calendar day)
Verified against: the nightly pipeline schedule (first attempt 23:45 and retry 01:00 Asia/Jerusalem; slowest observed finish 03:15 on the 14-day earnings re-fetch night; both collector fires 04:00 / 08:15 are before the cutoff, the next cycle starts after it), the session identity (the cutoff is derived from the session date alone, in New York time, and applies to the session whose bar is the newest), DST (US/Israel, all four regimes tested; the cutoff is at least five hours after the slowest pipeline finish in each), delayed runs (a pipeline delayed within its cycle still qualifies; one a whole cycle late does not) and late vendor corrections (a correction after the scan changes the price fingerprint, so the old scan stops matching).
**What the cutoff means.** It is *operational observation eligibility*: "this was available to the overnight cycle that produced the observation, before that cycle's last collector attempt". It is **not** a claim that the information existed at the original market close. A discontinuity or scan completed between the close and the cutoff is, by construction, learned after the close (the stored `captured_at` / `finished_at` say when). Consumers that need close-time information must not treat an `observed` snapshot as a close-time claim; `observed_forward` means "recorded forward by this system in that cycle", which is exactly what it is.

## 8. Requirement matrix
| # | Requirement | How / test |
|---|---|---|
| 1 | Existing stamps survive rebuilds | upsert keeps `detected_at`; no table delete; `test_discontinuity_provenance_db.py` (mutation-checked) and end-to-end `test_repeated_rebuilds_keep_the_first_detected_stamp_end_to_end` |
| 2 | New discontinuities get a genuine stamp | `clock_timestamp()`; bounded between two database clock reads |
| 3 | No fabricated stamp for overwritten rows | legacy rows untouched; test |
| 4 | Fail closed without trustworthy timing | seven coded reasons in section 6, each tested; collector refuses; `scan_evidence_unavailable` without the migration |
| 5 | A later rebuild never makes a historical session eligible | stamps never move earlier; a retry cannot re-record or backdate a scan; fingerprints must match NOW |
| 6 | Idempotent | repeated rebuild leaves rows and stamps equal; identical scan retry writes nothing and keeps the first time |
| 7 | Tests | normal / skipped / failed / wrong session / wrong price fingerprint / partial ingestion / repeated scan / changed prices / deletion and reappearance / ratio change / delayed detection / exact cutoff / DST regimes / immutability / DB-stamped time / collector fail-closed / builder end-to-end / rollback |

## 9. Deployment order (every step needs an owner approval; none has been performed)
1. Review and approve the final lab commit (CI green); merge.
2. Build the mechanism image from the exact approved SHA; verify digest and source identity (as for 8dec4e2b38fe).
3. Production backup, labelled and restore-verified (existing `pre_activation_backup.sh`).
4. **Apply migration 32** with the bootstrap identity from the image's own copy: `docker run --rm --entrypoint cat $IMG /app/mechanism/add_price_discontinuity_scan.sql | docker exec -i $PG psql -U trading_user -d trading_production -X -v ON_ERROR_STOP=1 -1`. Additive; the table is empty. Rollback while empty: `deploy/db/rollback_32.sql`.
5. **Re-run `deploy/db/research_roles.sql`, then `research_roles_verify.sql`** (owner `donchian_owner`, runtime `SELECT, INSERT`, admin `SELECT`, `EXECUTE` on the two fingerprint functions). Do NOT use `research_roles_rollback.sql`.
6. **Pin the new image** (one root file write; the pipeline wrapper passes `--session` through `automation_pipeline.sh`). The collector stays unarmed. The bot and backend pins are unchanged.
7. Observe ONE normal nightly cycle read-only: a `complete` scan row for the session, `finished_at` before the cutoff, the 1,040 legacy stamps unchanged, no `failed` scan, no new permission errors.
8. Read-only collector dry run for that session: `observe` must print `dry_run`, not `failed`.
9. Only then, with a separate approval: arm the collector (arming file, then the timer).
Rollback at any step: remove nothing the evidence needs; to stop collection leave the collector unarmed; the image pin can go back to `8dec4e2b38fe`, but note that image does not know the scan table (it would keep refusing `observed` because the old stamp rule and no scan are both absent; it neither reads nor writes migration-32 objects).

## 10. Residual risks (stated, not hidden)
* Fingerprints are 64-bit row hashes summed (not an adversary-proof MAC): they detect accidental change, a vendor correction, a partial load, a missing bar; the writer is the runtime role.
* The fingerprint covers the whole price table up to the session, so a vendor correction to ANY historical bar after the scan (even in a symbol irrelevant to the snapshot) makes the session INCOMPLETE until a new scan runs. That is deliberately strict; the next pipeline run's scan is for the next session, so a session whose data changed after its scan and after the 08:15 recovery fire is MISSED, never silently accepted.
* The 05:00 price safety net (`firstlight1-prices`, 02:00Z) can re-fetch the session's bars between the 04:00 and 08:15 collector fires; if it changes any value, 08:15 reports INCOMPLETE/MISSED for that session. Watch the first nights.
* A scan proves the detector ran over the stored prices; it does not prove the vendor prices are right.
