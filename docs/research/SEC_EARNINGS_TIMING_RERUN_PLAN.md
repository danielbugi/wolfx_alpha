# Re-running the SEC earnings timing study with corrected acceptance timestamps (plan only)

Status: plan, 2026-10-16. Nothing is run by this document. Research and scratch only: no production write, no new strategy, no change to any pin, timer or the tracker. Independent of the 2026-10-21 Stage 2 maintenance.

## 1. Why

The 2026-10-11/12 study ([QUANT_LAB_SEC_EARNINGS_FINDINGS.md](QUANT_LAB_SEC_EARNINGS_FINDINGS.md)) took each filing's acceptance instant from the submissions JSON `acceptanceDateTime`. On 2026-10-16 that field was found to be later than the SEC's own
header record by exactly the Eastern UTC offset (+4.0 h in daylight time, +5.0 h in standard time; 89 of 89 older filings, 2015-2026, zero unexplained) for most filings that are more than a few hours old, and correct only for brand-new ones
([CATALYST_PHASE1A.md](CATALYST_PHASE1A.md) section 3, evidence in `evidence/catalyst_phase1a_2026-10-16/`). The study's decision time, BMO / intraday / AMC bucket, reaction bar, release-to-periodic matching window and 20-day drift start all derive from that instant.
What is NOT known is how much the numbers move: an AMC filing stays AMC after a +4 h shift, a before-open filing becomes "intraday" (same reaction bar), but a filing accepted 12:00-15:59 ET becomes AMC (next-day bar) and one after about 20:00 ET
crosses midnight. The size of the effect has to be measured, not assumed; the study's placebo test (overnight gap x5 on the reaction bar) suggests the headline timing was largely intact, which is a reason to measure, not to skip.

## 2. Fixed in advance (to keep the rerun honest)

* Hypotheses, anomaly rules v1, thresholds, cost model (0 / 10 / 25 bps), standardised R, month-block bootstrap and seeds, the five pre-named cells, the train < 2022-01 / embargo / test >= 2022-02-15 split and the 120-bar purge: **unchanged**. Only the acceptance instant changes.
* No new cell, feature or threshold may be added in this rerun. A result is called *robust* if its sign and its "interval includes zero" status are unchanged and its point estimate moves by less than a pre-stated tolerance (half the old interval width); anything else is *changed*, reported, and not re-tuned.
* The old and new numbers are published side by side; the old evidence files stay in place.

## 3. Steps

| # | Step | Detail | Output |
|---|---|---|---|
| 0 | Scratch restore | restore the latest verified backup into an isolated Postgres (the Phase 1A procedure); production is only read for the backup | scratch database |
| 1 | Reproduce the baseline | rerun the study on the original inputs; every number must match the committed evidence (the previous reruns reproduced exactly) | match report; stop if it does not match |
| 2 | Collect headers | fetch `.hdr.sgml` for every accession the study used: about 9.4 k Item 2.02 8-Ks and 16.4 k periodic filings, roughly 26 k requests, about 90 minutes at 5 requests/s, declared contact, the Phase 1A transport; raw bytes cached with sha256 in a manifest (the bytes are not committed) | header cache + manifest |
| 3 | Validate the correction | per accession: feed - header must be 0 or exactly the Eastern offset at the header instant (the `acceptance_check` rule); sample 500 and confirm the filing page "Accepted" equals the header; list every exception, exclude them from the corrected run and count them; filings with no header are excluded and counted | validation table, exceptions list |
| 4 | Impact before any statistics | per event: old and new timing bucket, reaction bar, release-to-periodic match, date; counts of events whose bucket or bar changes, by hour of day and by year; the placebo test recomputed with corrected times | `timing_impact` report |
| 5 | Rerun | same code and inputs, corrected acceptance instants, all tables regenerated; sec_event_outcomes and sec_breakout_outcomes rebuilt with added columns `feed_accepted`, `header_accepted`, `shift_hours`, `timing_old`, `timing_new`, `bar_old`, `bar_new` | corrected evidence files with new hashes |
| 6 | Compare | each result block old vs new with intervals: reaction by anomaly class, 20-day drift, the five cells (train and test), walk-forward by year, exit-policy selection, ML AUC (size and direction), the timestamp placebo | `old_vs_new` table with robust / changed labels |
| 7 | Decide | memo to the owner: which conclusions stand, which change, what to revise in `/lab` and in the findings document; any lab report update only after approval | decision memo |

## 4. Acceptance criteria

1. The baseline reproduces exactly (step 1).
2. At least 99.5% of accessions have a validated header; every exception is listed with its reason; no event is silently dropped or "fixed".
3. Every corrected timestamp satisfies the exact rule (0 or the Eastern offset); the placebo test on corrected times is at least as aligned as the old one, or the difference is explained.
4. Old vs new is complete for every reported number, with the robust / changed label decided by the pre-stated rule.
5. Nothing outside the scratch environment is written; the contact is never stored.

## 5. Risks and limits

* Headers of very old filings may be missing or differently formatted (pre-2001 or very early electronic filings are outside the study window but the 2016-2026 range must be checked); counted, not repaired.
* The SEC's served values can change again; the header cache records the fetch date and hashes so the result is reproducible from what was fetched.
* Ticker -> CIK still uses today's SEC file projected back (survivorship, as before); unchanged by this rerun and reported as such.
* Fair-access: 26 k requests is one extended but modest session; the transport's rate cap and circuit breaker apply.
* A corrected study does not turn exploratory cells into validated edges; the forward tracker remains the only confirmation.

## 6. Effort and approvals

About one working session of compute (headers ~90 min, study rerun ~1 h) plus review. Needs the scratch authority already granted and the SEC contact declared for the run; nothing else. Proposed order: after the Phase 1A merge, before any use of the study's timing results in a decision.
