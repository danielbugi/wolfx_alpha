# SEC history gap and stale feature rows: investigation (2026-11-05)

Read-only queries on production; no changes. Owner request of 2026-11-05: root causes, research impact and proposed fixes for the ~168 companies with short SEC history and the 286 stale feature rows.

## 1. The ~168 companies whose SEC history starts after mid-2025
**169 CIKs** have no SEC fact filed before 2025-06-01. Classified by how far their price history precedes their first SEC filing:

| Class | Companies | Root cause | Fix |
|---|---|---|---|
| **New listings** (prices start within 120 days of the first filing) | **150** | Real: IPOs, spin-offs and de-SPACs since 2025 (for example FIG, CRCL, CHYM, KMTS). Their short history is correct. | None |
| **Prices 4–12 months before the first filing** | **5** (LMRI, RMIX, KMTS, CBC, AKTS) | Traded (OTC or direct listing) before registering periodic reports | None; documented |
| **Split history: prices years before the first filing** | **14**: PBAM, SWRD, **XOM**, EAI, VNOM, AVBH, COSO, GLIBA, MTAL, SHAZ, PNFP, SN, LLYVK, DEC | **The SEC's current ticker map points the symbol at a NEW CIK.** XOM → "ExxonMobil Holdings Corp" (CIK 2115436, 56 facts from 2 filings), while ExxonMobil's history since 2009 is under CIK 34088. The same pattern appears in holding-company reorganisations, redomiciles, split-offs and re-registrations of foreign issuers. EAI is a utility subsidiary whose listed security is a debt instrument, not common stock. | See below |

**Why it happens:** `sec_symbol_cik` is built from `company_tickers.json`, which maps a ticker to its **current** registrant only. `companyfacts` is per CIK, so a predecessor's filings are never fetched.

**Proposed fixes** (code changes; nothing applied):
1. **CIK lineage table** (`sec_cik_lineage`: successor, predecessor, effective date, evidence), seeded by a detector and confirmed by hand.
   - Detector: price history starting more than 365 days before the CIK's first periodic filing.
   - Evidence comes from the successor's own filings: Form 8-K12B ("successor issuer"), the S-4 / 8-K naming the predecessor, or the predecessor's last 8-K item 2.01 / 3.03.
   - Ingest the predecessor's `companyfacts` too, and union the facts under the symbol, each keeping its own accession and acceptance date. The point-in-time engine then works unchanged, because every fact keeps the public date of its own filing.
   - This needs a migration (additive) and an owner decision under the data hold.
2. **Exclude non-equity listings** (debt and preferred instruments such as EAI) from the inflection universe, using the SIC code plus the security title on the cover page. Small code change.
3. **Monitoring:** add the detector count to the Lab data-quality view ("split-history companies: N"), so a new reorganisation is visible within a night.

## 2. The 286 stale feature rows (first public more than 120 days after quarter end)
| Group | Feature rows | Companies | Events produced |
|---|---|---|---|
| New listings (short-history CIKs), more than 365 days late: pre-IPO quarters that first appear as comparatives in the company's first 10-Q | 233 | 127 | **0** |
| New listings, 121–365 days late (mostly a pre-IPO Q1 first seen in the first post-IPO 10-Q) | 21 | 20 | **0** |
| Established companies, more than 365 days late | 22 | 13 | 3 |
| Established companies, 121–365 days late (for example ALNY, CBZ, RICK, DRVN, AREC, OPI, AVXL, GLSI) | 10 | 9 | 3 |

- **Root cause:** first-public dating is correct; these quarters genuinely first appear in a later filing. Evaluating them as new was the bug, and **PR #95's guard (in the release candidate) stops creating them**.
- **The 6 events** are the stale-event cleanup set (PR #97).
- **Open item:** for the 9–13 established companies (lags of 121–400 days), it is unclear why the original 10-Q's facts are absent. Possible causes are a tag change, a filing whose XBRL lacks the three-month duration for our tags, or a split history as above. It needs a per-company `companyfacts` check; this is a small, bounded follow-up.

## 3. Research impact
| Area | Impact |
|---|---|
| Full-history PIT reconstruction (PR #94) | **The 150 new listings have no pre-holdout history to lose.** The 14 split-history companies lose their pre-2025 history: about 0.5% of companies, but some are large (XOM). Pre-holdout conclusions are unchanged in direction; the next reconstruction should include lineage-linked history. |
| Live SEC PIT events | Stale first appearances create no events once the guard is live. Split-history companies emit events only from their new-CIK filings, whose comparatives are guarded. |
| Coverage gate | It counts split-history companies as "covered" although their history is short. This overstates history depth, not coverage. Report the lineage detector next to coverage. |
| Stale feature rows | Harmless to alerts and the API (no events; the guard prevents new ones). Deleting them is optional (owner decision; the same backup/cleanup pattern as PR #97). |
