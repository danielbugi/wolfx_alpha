# SEC company identity: predecessor CIKs and identity breaks (Lab, 2026-11-06)

**Status:** Lab only. This is migration 41 (`mechanism/add_sec_cik_lineage.sql`) plus the resolver `mechanism/data_updaters/sec_lineage.py` and a reviewed seed `sec_lineage_seed.csv`.
- **Not applied to production.**
- The live inflection engine does not call the resolver.

Owner approval of 2026-11-06 covers Lab development only. The finding it answers is in [SEC_HISTORY_GAP_INVESTIGATION.md](SEC_HISTORY_GAP_INVESTIGATION.md).

## Problem
`sec_symbol_cik` is built from the SEC's **current** ticker file, so a symbol maps to today's registrant only. Two different failures follow:
1. **Lost history.** After a holding-company reorganisation, the symbol points at the new parent's CIK. The business's filings since 1994 stay under the old CIK.
   - Example: XOM now maps to CIK 2115436, ExxonMobil Holdings Corp, while ExxonMobil's own filings are under CIK 34088.
   - The successor's first 10-Q repeats a year-old quarter as a comparative. Without the predecessor, that quarter looks first public a year late (the stale-guard case).
2. **Borrowed identity.** After a ticker reuse, the price history behind the symbol belongs to a **different** company.
   - Example: GLIBA 2018–2020 was the old GCI Liberty.

## Rules
| Requirement | How it is met |
|---|---|
| Preserve historical company identity | **No fact is moved or copied.** The predecessor's facts stay under its CIK, with their own accession and filing date. The link only says which CIKs a symbol's history is read from, and for which periods. |
| Never merge unrelated entities on the ticker alone | Inheriting history requires a **confirmed** holding-company reorganisation or redomicile. It must name the predecessor CIK and cite the succession filing (8-K12B or 8-K12G3). This is enforced by a table CHECK constraint and by `Link.validate()` for the seed. Mergers of equals, ticker reuse, SPAC shells, tracking stocks and foreign-filer transitions **never** inherit. The detector only proposes candidates for review. |
| Point-in-time availability | **The link applies from `known_at`, the evidence filing date.** Before it, the symbol was the predecessor, so the predecessor alone is read. After it, the predecessor's filings are read only for periods ending before `effective_date`. Every quarter is still dated by the filing that first made it public, since `pit.first_public` takes the earliest. |
| Exclude debt-only listings | `research_eligible = FALSE` for `non_equity_listing` (a CHECK constraint) and for blank-check companies (SIC 6770, in code). |
| Auditability | The table is append-only (ENABLE ALWAYS triggers). A correction is a new row with `supersedes_id`, and `load_db` reads current rows only. |

## The 14 split-history symbols, classified from EDGAR evidence
Read-only SEC submissions lookups and production price history, 2026-11-06.

| Symbol | Relation | Inherits | Evidence |
|---|---|---|---|
| **XOM** | Holding-company reorganisation | **Yes, from CIK 34088** | Successor 8-K12B 2026-07-01 (0001193125-26-291990); predecessor 25-NSE 2026-07-02 |
| **VNOM** | Holding-company reorganisation | **Yes, from CIK 1602065** | Successor (formerly New Cobra Pubco) 8-K12B 2025-08-19 (0001193125-25-183036); predecessor renamed VNOM Sub, Inc. |
| PNFP | Merger successor | No (**needs review**) | Combines Pinnacle (1115055) and Synovus (18349): 8-K12B 2026-01-02. Whose history continues depends on the accounting acquirer. |
| DEC, SN | Foreign-filer transition | No | **Same CIK.** Filed 20-F in 2024 and 2025, then 10-K. The history is in 20-F filings, not under another CIK. |
| GLIBA | Ticker reuse | No; prices cut before 2025-07-15 | The old GCI Liberty (808461) merged away in 2020; a 1,658-day price gap. |
| MTAL | Ticker reuse; **not eligible** | No; prices cut before 2026-02-06 | MAC Copper (1950246) until 2025-10-10; the new symbol is a blank-check company (SIC 6770). |
| SHAZ | SPAC combination | No; prices cut before 2026-02-18 | Prices before that date are the shell's (daily ratios 0.04×–35×). |
| LLYVK | Tracking-stock split-off | No | Liberty Media's filings describe the whole parent. Prices are continuous. |
| AVBH, COSO, PBAM, SWRD | Newly registered | No | Traded OTC or under Reg A; no earlier Exchange Act reports. |
| EAI | Non-equity listing; **not eligible** | No | The listed security is an Entergy Arkansas mortgage bond. |

**Effect:** only 2 of the 14 can recover history: XOM (filings since 1994) and VNOM (since 2014). The other 12 are correct as they are, but 4 need a price cut (GLIBA, MTAL, SHAZ) or exclusion (MTAL, EAI) in research.

## What activation would take (not done; each step needs owner approval)
1. Apply migration 41 to the research database or production, and load the seed.
2. Ingest the predecessor CIKs' `companyfacts` (2 companies, 2 requests).
3. Call `sec_lineage.merge_facts` in `inflection/store.load_sec_filing_versions` and in the PR #94 reconstruction.
4. Apply `price_identity_from` and `research_eligible` in research universes, and add the detector count ("split-history candidates: N") to the Lab data-quality view.

## Tests
`mechanism/data_updaters/tests/test_sec_lineage.py` has 30 tests. One applies migration 41 on PostgreSQL in CI's real-database job. They cover:
- the full seed;
- the "only proven reorganisations inherit" rule, with 7 refusal cases;
- XOM point in time end-to-end through `pit.first_public` and `pit.pit_items`: the comparative is dated by ExxonMobil's own 10-Q, the predecessor's post-succession stub is not read, and only the predecessor is read before `known_at`;
- VNOM; PNFP never inheriting;
- no other case borrowing filings;
- the price-identity cuts;
- debt and blank-check exclusion;
- the detector never linking;
- the migration's constraints, append-only triggers and supersede logic.
