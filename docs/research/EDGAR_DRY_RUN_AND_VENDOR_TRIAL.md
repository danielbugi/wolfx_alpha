# SEC EDGAR dry-run collector and the provider-neutral vendor trial harness

Both modules are **pure** (`test_isolation.py` enforces: no psycopg2, clock, env, file or network). Neither writes anything or is wired to
a schedule. Contracts they build on: [SEC_EDGAR_EVENT_CONTRACT.md](SEC_EDGAR_EVENT_CONTRACT.md) and
[EARNINGS_SOURCE_ASSESSMENT.md](EARNINGS_SOURCE_ASSESSMENT.md).

## 1. EDGAR dry-run collector — `market_intelligence/edgar_collector.py`

Flow: `EdgarConfig` → `collect(cfg, transport, ciks, now=, clock=, sleep=, …)` → `DryRunReport`.

1. `EdgarConfig` refuses a user agent without an operator name and a real contact email (SEC fair-access policy) and `max_rps` outside (0, 10].
2. For each CIK: rate-limit via an injected clock/sleep, fetch `submissions_url(cik)` through the injected **`Transport`** protocol.
   No live transport exists in the module; attaching one is a separate owner-approved step.
3. `parse_submissions` validates the document's own CIK equals the requested CIK (else the whole document is refused: no cross-company
   attachment), then maps each row of the `recent` block through `filing_contract.build_fact`.
4. A record that cannot make a fact is **rejected with a reason** (kept in the report), never repaired. One failing CIK does not stop the
   others, and `complete` is false if any CIK failed — a partial run cannot be mistaken for a full one.
5. `would_write` reports the upper bound of rows a real run would insert (one event + revision per new accession, plus classifications if
   requested). The dry run reads no database, so `known_accessions` can be passed to subtract what is already stored.

Fidelity: facts are verbatim — accession, CIK, form type, filing date, **SEC acceptance timestamp** (must be an explicit UTC `…Z` instant;
zone-less is rejected, not assumed), items, report period, primary document. A symbol attaches only through supplied `IdentifierLink`s,
recorded with its basis. `amends_accession` is not in the feed and is left unset. Only the `recent` block is read; older pages are counted
in `older_pages_not_read`. `raw_hash` fingerprints the source bytes. A classification (optional) is a separate object pinning the
fact's hash and cannot change it.

Not done: live transport, persistence of the report, scheduling, ticker↔CIK link maintenance, 8-K item parsing beyond the feed's `items`.

## 2. Vendor trial harness — `market_intelligence/vendor_trial.py`

Purpose: grade an earnings consensus / guidance source **before any purchase**, without coupling to one provider.

- The harness never holds credentials (the adapter is built by the caller), never names a vendor (`provider_id` is an opaque label), and
  never writes.
- A provider is wrapped in a `ProviderAdapter` exposing three read calls. `collect_snapshot` pulls a fixed sample; run it again **≥ 7 days
  later** so check 3 compares genuinely time-separated, hashed pulls.
- Checks (`TrialPolicy`, versioned thresholds): 1 report time vs SEC acceptance; 2 as-of behaviour (values vary across as-of dates and
  equal our own first-seen snapshots where we have them); 3 restatement (re-pull changes nothing silently); 4 actuals vs SEC XBRL with the
  basis difference stated; 5 coverage (revenue and guidance separately); 6 operational facts (a silent-empty outage is a failure);
  7 licence (human-recorded; an unanswered question blocks adoption).
- Output: `TrialReport` with an observed PIT **grade per field** — A (checks 1–3 pass on enough evidence), C, or X (unproven). Unproven is
  X, never a benefit of the doubt.

Dormant: no adapter for any real provider exists, no trial has been run, no vendor has been chosen or purchased, no credentials were added.
