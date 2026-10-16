# Market-wide SEC discovery: minimal architecture (design only; nothing implemented)

Status: proposal, 2026-10-16. Builds on Catalyst Phase 1A ([CATALYST_PHASE1A.md](CATALYST_PHASE1A.md)). No code, migration, timer or production change is made or implied. Independent of the 2026-10-21 Stage 2 maintenance.

## 1. Problem and measured facts

Phase 1A polls every tracked company's submissions JSON. At the tracked universe (about 3,000 CIKs) one full poll is roughly 3,000 requests, 10 minutes at 5 requests/s and 430 MB (144 KB per document), and the 120-filer sample produced no new filing in
36 consecutive cycles: almost all of that traffic returns nothing. What the 2026-10-16 work measured on the SEC's own surfaces:

| Fact (measured) | Value |
|---|---|
| EDGAR "current filings" Atom list (`getcurrent`), `type=8-K` | 100 entries per page, **paging with `start=` works** (page 2 returned 97 more, no overlap); each entry carries the accession, CIK, form and `<updated>` = the acceptance instant **with an explicit UTC offset**; `type=8-K` also returns 8-K/A, `type=10-Q` returns 10-Q/A, `type=10-K` returns 10-K/A |
| New filings appear in that list | **0.9-2.4 min after acceptance** (n = 11, 2-minute resolution) |
| Submissions JSON shows the same filings | 1.0-3.0 min; its acceptance field is wrong by one Eastern offset for most filings once older than hours (see Phase 1A section 3) |
| Daily index `daily-index/YYYY/QTRn/form.YYYYMMDD.idx` | exists for the previous business day, 3,659 lines for 2026-10-15; in-scope counts that day: 218 8-K, 8 8-K/A, 17 10-Q, 1 10-Q/A, 1 10-K, 1 10-K/A, 6 NT 10-Q |
| Filing header `<accession>.hdr.sgml` | about 1.2 KB; carries accession, form type, filing date, **items**, **acceptance instant**, CIK and company names; 89 of 89 acceptance values agreed with the filing page |

Volume on the day sampled: about 250 in-scope filings across the whole market, so the in-scope work is tiny compared with polling thousands of companies.

## 2. Architecture (three small stages)

```
 S1 DISCOVERY (market-wide, authoritative for "what is new")     S2 RESOLUTION (only for filers we track)           S3 RECONCILIATION (completeness)
 Atom getcurrent, one request per in-scope form family,          header .hdr.sgml of the accession (1 request)       daily index of the previous session (1-2 requests)
 every 60-120 s in session, 15 min out of session                 -> fact: accession, CIK, form, filing date,         -> every in-scope accession of the day
 -> observation 'filing_discovery' per accession                   items, acceptance instant (authoritative)           -> any accession we did not discover live is
    (first-seen log, observed_at = database clock)                 -> existing ingest: fact -> labels                    recorded as a LATE discovery (never back-dated)
                                                                 submissions JSON only to onboard a CIK, to                weekly: submissions JSON per tracked CIK, rotating
                                                                 cross-check, or when a header is unusable                 subset, to catch anything both missed
```

**S1 Discovery.** One Atom request per form family (8-K, 10-Q, 10-K, NT 10-K, NT 10-Q; the amendment forms ride along). Page with `start=` until the page contains an accession already discovered or the
look-back horizon (default 6 h) is reached; an unbroken overlap proves no gap, a horizon reached without overlap is recorded as `gap_suspected` in `source_poll` and triggers an early S3 run. Each new accession becomes one first-seen
observation (`source='sec_edgar_atom'`, `dataset='filing_discovery'`, subject = CIK, period = accession, value = form, `<updated>` instant, entry title), so the database stamps the real first-seen time and the latency is simply
`observed_at - acceptance`. The Atom page body is kept as a source snapshot only when it contributed a new in-scope accession (provenance for each discovery, a few MB per day).

**S2 Resolution.** Only for CIKs in the tracked universe (identifier links as of that time; a discovered but untracked CIK costs nothing beyond its observation). The header is fetched, snapshotted and parsed to the
`FilingFact` fields; the existing fact -> labels ingest and the existing strict reader are unchanged. The submissions JSON leaves the hot path: it is used to onboard a newly tracked CIK, as the weekly
cross-check, and as the fallback if a header cannot be parsed. The acceptance reconciliation of Phase 1A becomes a cross-check (header vs feed) instead of a precondition.

**S3 Reconciliation.** Once per session after the close the daily index is compared with the discovered set. A miss is not an error to hide: it is ingested with provenance `daily_index`, its `first_observed_at`
is the time we found it (not the acceptance time), and it is counted in the miss-rate metric. A weekly per-CIK pass on a rotating subset of the tracked universe (about 3,000 requests per week) catches anything both lists missed.

## 3. Strict point-in-time and provenance

* Availability to us stays `max(first observed, ingestion)`; the SEC acceptance instant only describes the event, as in Phase 1A. A late or delayed discovery can never appear in an earlier decision (the strict reader's gates are unchanged).
* First-seen is the minimum across discovery sources, each kept as its own series (`sec_edgar_atom`, `sec_edgar_daily_index`, and the Phase 1A `filing_presence`), so duplicate and delayed discoveries are visible and not overwritten.
* Duplicates: the accession is the key; the same accession seen on page 1 and page 2, in two forms, or by Atom and the index writes nothing new (the first-seen log stores changes only, the event upsert is idempotent).
* Delayed: an accession discovered later by S3 is tagged `late_discovery` with the lag; research that needs "known at T" simply never sees it before its own `first_observed_at`.
* Every stage records its source (URL, `User-Agent` contact is never stored), the snapshot hash, the run key and the code reference; every poll writes a `source_poll` row (complete / partial / failed, subjects covered).
* Untracked CIKs: discovery observations are kept (cheap) so a later onboarding does not lose the history, but their facts are fetched on demand and are available only from that fetch time (never back-dated).

## 4. Measuring real first-seen latency

Built in, no separate probe: for every discovery, `observed_at - acceptance` (acceptance taken from the Atom `<updated>`, cross-checked by the header). Reported per run and per day: p50 / p90 / p99 by form family and by time of day (pre-market, session, post-close wave),
plus S3 late-discovery lag and miss rate. Poll cadence is chosen from these numbers, not assumed.

## 5. Scale and request budget

| Item | Per day | Notes |
|---|---|---|
| Atom polls | about 5 forms x 640 cycles = 3,200 | a cycle is 1-2 requests when no gap; far fewer at 15-minute out-of-session cadence |
| Headers | about 250-600 | only filings by tracked CIKs; cached forever by accession |
| Daily index | 1-2 | |
| Weekly CIK pass | about 430 | 3,000 per week |
| **Total** | **about 4,500 requests, under 0.1 request/s on average** | against about 450,000 per day for per-company polling at 150 cycles |

Peaks (post-close wave) stay inside the 5 requests/s cap because only new in-scope accessions of tracked CIKs trigger a header fetch and the Atom pages are fixed in number.

## 6. Failure modes and defences

| Risk | Defence |
|---|---|
| A burst puts more than 100 new filings between two polls | page until overlap; horizon reached without overlap = `gap_suspected` + immediate S3 |
| The Atom list is incomplete or its format changes | S3 daily index and weekly CIK pass; parse failure is a failed poll, never an empty success |
| Header unavailable or unparsable | postpone and retry; after N tries fall back to the submissions JSON with the Phase 1A reconciliation; never guess |
| Header and Atom acceptance disagree | refuse the fact and alert; both values recorded |
| SEC rate limit, 403 | the Phase 1A transport (backoff, circuit) is reused unchanged |
| Duplicate filers (share classes) | the identifier-link layer already maps one CIK to several symbols |

## 7. Phased plan (each phase gated; no production step before its own approval)

| Phase | Scope | Exit criteria |
|---|---|---|
| D0 spike (read-only, no database) | one full session including the post-close wave: Atom vs daily index completeness; maximum burst per minute; paging overlap behaviour; `type=` semantics for each form; header-derived facts compared field by field with the submissions-derived facts for at least 500 filings (form, filing date, items, period, acceptance); first-seen latency distribution | miss rate vs the index under 0.1%; header facts equal the submissions facts; p90 latency under 5 minutes in session; written burst limits |
| D1 build in scratch | `atom_discovery` and `daily_index` (pure parsers, tests with recorded pages), `header_facts` (pure), the discovery runner, a reconciliation report; all against the existing scratch bootstrap; no new table (the first-seen log and snapshots are enough) | tests incl. idempotency, duplicates, delayed and out-of-order discovery, gap detection, malformed pages; mutation check as in Phase 1A |
| D2 scratch shadow | five consecutive sessions beside the Phase 1A per-company poll on the 120-filer sample | completeness 100% against the per-company poll, 0 PIT violations, latency report, request budget as above |
| D3 production gates | the Phase 1A gates G1-G6 unchanged (migration 37 is the only schema need), with the runner replacing the per-company poller | per Phase 1A |

Decision needed from the owner only at D3 (contact to declare, schedule, tracked CIK source); D0-D2 need none beyond the approved scratch authority.
