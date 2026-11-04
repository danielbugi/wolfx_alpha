# SEC coverage and survivorship: status and plan (2026-11-04)

Read-only measurements from production on 2026-11-04. Nothing was changed: no schedule, crawl budget, data or purchase.

## 1. Coverage
### Where it stands
The confirmation gate (`data_quality_service`) measures live price-universe symbols mapped to a CIK that have SEC revenue quarters, as a share of all mapped universe symbols.

| SEC state of the 3,048 mapped universe symbols | Symbols | CIKs |
|---|---|---|
| `ok`, has revenue quarters | **2,593** | 2,561 |
| `ok`, statements ingested but **no revenue line** | 287 | 286 |
| `no_filings` (no 10-Q/10-K: foreign private issuers filing 20-F/40-F, funds, trusts) | 159 | 159 |
| `no_xbrl` | 9 | 8 |
| Never checked | **0** | 0 |

**Coverage = 2,593 / 3,048 = 85.1%, so the 80% gate now PASSES.** On 2026-11-03 it was 55.4%. The crawl did not need a budget change: it finished on its own in two nights.

| Night (UTC) | Companies checked | Fact sets fetched | New companies ingested |
|---|---|---|---|
| 2026-11-03 | 1,598 | 1,519 | 1,856 (the first full run, including the 2026-11-01 test of 20) |
| 2026-11-04 | 1,402 | 1,055 | 971 |

**Throughput.** Roughly 1,400–1,600 companies per night inside the 1,500-second budget, at about 1 company per second (submissions plus companyfacts requests, client capped at 5 requests per second). The steady-state load needs about 430 checks a night: a weekly sweep of about 3,000 CIKs plus earnings-triggered rechecks. **No budget change is needed.**

### What remains, by cause
| Gap | Size | Cause | Fix | Expected gain |
|---|---|---|---|---|
| Financial Services with no revenue line | 185 symbols | Banks, insurers and BDCs report `InterestAndDividendIncomeOperating`, `Revenues` net of interest expense, `PremiumsEarnedNet` and similar, which our revenue tag list does not map. Net income is present for 283 of the 286. | Add a sector-aware revenue definition to `sec_statements` (total interest + non-interest income for banks; premiums + investment income for insurers; total investment income for BDCs). Needs tests on real filings. Inflection events for these stay limited: margin, FCF and operating leverage are excluded for Financial Services and Real Estate. | about +6 pp (to ~91%) |
| Healthcare with no revenue line | 65 | Mostly pre-revenue biotechs: there is genuinely no revenue. | None. Consider excluding them from the gate's denominator, documented. | +2 pp if excluded |
| Real Estate / other with no revenue line | 37 | REIT rental-revenue tags (`OperatingLeasesIncomeStatementLeaseRevenue`) and a few unmapped industry tags | Add the tags with tests | about +1 pp |
| `no_filings` / `no_xbrl` | 168 | Foreign private issuers (20-F/40-F, often IFRS) and non-operating vehicles | Out of scope for a US-GAAP point-in-time engine. Exclude from the denominator, documented. | n/a |

**Recommended order (code PRs only; no schedule or budget change):**
1. Parser: financial-sector revenue definitions.
2. Parser: REIT lease-revenue tags.
3. Gate: define the denominator as US-GAAP periodic filers, excluding `no_filings` and `no_xbrl`; reported separately.

### Other blockers
- **Re-run the full-history reconstruction (PR #94).** Its extract predates last night's 971 companies. Running it again is read-only and takes minutes.
- **Re-check the readiness gates afterwards.** With 2,561 CIKs the holdout counts will rise, but survivorship still fails (section 2).

## 2. Survivorship
### The problem
Our universe is "companies with prices in our database today". In the Atlas extract, 98.3% of 2017's eligible stocks are still priced in 2026. Companies that were delisted, went bankrupt or were acquired are absent from both the event set and the controls. For the candidate left-tail hypothesis this is the worst possible bias: the stocks that went to zero are exactly the ones missing.

### What our current vendor does (tested 2026-11-02)
Tiingo keeps **acquired** companies (ATVI to 2023-10-13, TWTR to 2022-10-28) but loses or corrupts **bankruptcies**:
- SIVB: metadata "not found", yet prices continue at $0.006 with zero volume.
- BBBY: 0 bars, because the ticker was reused.
- FRC: not found.

Using Tiingo alone would add merger targets back while still missing failures, which skews the left tail further.

### Free option: SEC records
**What SEC data provides:**

| Need | Free SEC source | Coverage |
|---|---|---|
| Who existed (a historical universe by CIK) | EDGAR full-index (`form.idx` per quarter, 1993 onward): every 10-Q/10-K filer per quarter | Complete for US registrants |
| Their fundamentals, point in time | `companyfacts` by CIK, which also exists for delisted CIKs | XBRL from 2009–2011 onward |
| When and why they left | Form 25 / 25-NSE (exchange delisting), Form 15-12B/12G (deregistration), 8-K item 1.03 (bankruptcy), 2.01 (acquisition completed), 3.01 (delisting notice); DEFM14A / S-4 for mergers | Dates exact; reason needs classification rules |

**What SEC cannot provide:**
- Prices, and especially the **final trading price and the delisting return**.
- Ticker history: `company_tickers.json` is current only, so old tickers must be matched by name and CIK.
- Split and dividend adjustment.
- Post-delisting OTC trading.

**Using SEC data alone:** rebuild the historical universe by CIK and attach an *imputed* delisting outcome by reason, with Shumway's convention: −30% (NYSE/AMEX) / −55% (Nasdaq) for performance delistings, about the deal price for mergers. Then run the study as **bounds**: best case (failures at −30%) and worst case (−100%).
- This answers whether survivorship could plausibly erase the left-tail effect, at zero cost.
- It cannot produce exact returns for the months before delisting.

### Paid options (no purchase made; prices as last checked, to verify on vendor sites)
| Vendor | Delisted coverage | Delisting return / reason | Indicative cost | Fit |
|---|---|---|---|---|
| Norgate Platinum / Diamond | US delisted since 1990 / 1950, with a permanent asset ID across ticker changes | Prices up to the last trading day; reason not confirmed | about USD 630 / 787 per year | Best price-history fit; files export; access ends when the subscription lapses; personal/internal licence |
| Sharadar (Nasdaq Data Link) | Active and delisted tickers since 1998 | ACTIONS table (delisting/acquisition events) not verified | Not verified | Clean API; check the licence terms |
| EODHD | About 60k delisted US symbols | No reason field | about USD 20/month (EOD) | Cheap; **ticker reuse** is the known trap |
| CRSP (WRDS) | Gold standard | DLSTCD reason codes plus DLRET delisting return | Institutional only | Probably not accessible |

### Recommendation
1. **Now, free.** Build the SEC-only historical universe and delisting-reason table as a read-only research extract. Run the left-tail study as bounds.
   - If even the worst case keeps the effect, survivorship cannot overturn it.
   - If not, paid data is needed.
2. **Only if step 1 is inconclusive:** an owner decision on Norgate Platinum (best price-history fit) or Sharadar, for a **separate research table keyed by vendor ID**, never mixed into `stock_prices`. This is subject to the 2026-10-11 "no new datasets" hold.
3. Keep the candidate hypothesis unregistered until this is resolved.
