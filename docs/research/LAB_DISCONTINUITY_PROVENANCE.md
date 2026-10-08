# Price-discontinuity first-detection provenance (lab branch, not deployed)

Status: implemented on `lab/first-light-algo`; **nothing is deployed, re-pinned or modified in production.** No schema change.

## 1. Root cause
`observe` (the collector's market / sector / relative-strength snapshot step) runs `market_intelligence.runner.compute_session(..., provenance="observed")`, which refuses a
session when any `price_discontinuities` row in its lookback was "detected after the session". Two defects combined:

1. **The writer destroyed the provenance.** Pipeline step 10 runs `ml_training/data_preparation/build_dataset.py --replace` every night: it `DELETE`d the whole table and re-inserted every row
   (and its upsert also did `detected_at = NOW()`). `detected_at` therefore meant "the time of the last rebuild", never "the first time this system saw the discontinuity".
2. **The reader compared a calendar date.** `detected_at::date > session_date` in the database's Asia/Jerusalem date. The successful nightly run is after midnight Jerusalem (about 01:00),
   so *every* in-window row looked retroactive for *every* session: `observed` was refused whenever the 420-day lookback held any discontinuity (18 on 2026-10-08, 1,040 rows in the table, all stamped 2026-10-08 03:09-03:11).

Production evidence (read-only, 2026-10-08): `by detected day: 2026-10-08 | 1040 rows`; a dry run for session 2026-10-07 returned `observed refused: 18 price discontinuities were detected after the session`.

## 2. The fix (no migration)
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

## 4. Requirement matrix
| # | Requirement | How |
|---|---|---|
| 1 | Existing observations keep their first-detected timestamp across rebuilds | upsert keeps `detected_at`; `--replace` no longer deletes; tests `repeated_rebuilds_keep...`, `jitter`, mutation check (old SQL fails 3 tests) |
| 2 | New discontinuities get a genuine stamp | `clock_timestamp()`; test bounds it between two database clock reads |
| 3 | No fabricated stamp for overwritten rows | legacy rows untouched; `a_legacy_row_with_overwritten_provenance_is_never_given_an_earlier_stamp` |
| 4 | Fail closed without trustworthy timing | future stamps counted `untrusted` and refuse `observed`; `NOT NULL` column; collector tests; **see 5 for the one gap** |
| 5 | A later rebuild never makes a historical session eligible | stamps never move earlier; `a_later_rebuild_can_never_make...`, collector `rebuilding_after_the_cutoff...`; `observed` also needs the session's bar to be the newest |
| 6 | Idempotent | repeated upserts leave rows and stamps equal |
| 7 | Tests | late discovery, repeated rebuilds, historical ambiguity, session boundaries (exact cutoff, DST regimes, per-session eligibility): 20 + 4 DB tests |

## 5. What this does NOT cover: a migration is *recommended* (not required for the defect), design only
The reader can prove the timing of the rows that exist. It cannot prove **that the detector ran** after the session's prices were loaded: if pipeline step 10 fails or is skipped, new discontinuities from that night are simply absent (not late), and an `observed` snapshot would be written without them. There is no existing column that records a completed scan, so closing this needs one small additive object:

```sql
-- migration 32 (PROPOSED, NOT WRITTEN, NOT APPLIED) -- additive, IF NOT EXISTS, append-only like the other research tables
CREATE TABLE IF NOT EXISTS price_discontinuity_scan (
    id            BIGSERIAL PRIMARY KEY,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at   TIMESTAMPTZ,
    newest_bar    DATE        NOT NULL,      -- newest stock_prices date the scan saw
    n_symbols     INTEGER     NOT NULL,
    n_found       INTEGER     NOT NULL,
    complete      BOOLEAN     NOT NULL       -- true only for a full run that pruned
);
```
The builder would insert one row at the end of a complete run; `observe` would additionally require a complete scan with `newest_bar = session` finished before the cutoff, and otherwise fail closed with `discontinuity_scan_missing`. The collector's own preflight would list it as a gate. **Until then the remaining risk is a skipped or failed step 10, which is visible in the pipeline log and in the validator, but is not enforced by the collector.** Recommendation: approve migration 32 before arming; the alternative is to accept the risk and rely on the pipeline's own step-10 failure alert.

## 6. Deployment path (owner approvals needed; none performed)
1. Merge candidate commit; CI green. 2. Build the mechanism image from it (manual CD, tag). 3. Pin it (one root file write), as for 8dec4e2b38fe. The very next nightly `--replace` then keeps the 1,040 stamps and prunes nothing it still detects.
4. (If approved) migration 32, then the scan-heartbeat commit. 5. Install is already done; arm the collector only after a read-only dry run for the new session prints `observe: dry_run`, not `failed`.
