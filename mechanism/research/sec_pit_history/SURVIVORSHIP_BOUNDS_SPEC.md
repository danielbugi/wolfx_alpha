# Survivorship bounds study: specification, fixed before any data (2026-11-05)

**Status:** approved by the owner on 2026-11-05 as a free, read-only study. **NOT RUN: blocked.**

**Why it is blocked:** the SEC requires every automated request to carry a declared User-Agent with a contact. None is configured on the research machine, and the agent may not invent one or use the owner's address unasked. Two ways to unblock:
1. Set `SEC_USER_AGENT="FirstLight Research <contact email>"` locally.
2. Approve running the fetch on the VPS with the already-declared production contact, outside the 8d crawl window.

**Holdout:** the reserved confirmation window (events public 2023-01-01 to 2025-03-31) stays closed. Only pre-holdout events (public before 2023) are used, exactly as in `study.py`.

## Question
Could the missing (delisted) companies overturn the exploratory pre-holdout findings?

| Finding (`study_output.txt`) | Value |
|---|---|
| Halving rate, all company-quarters | 4.3% vs 5.4% matched (−0.70 pp) |
| Halving rate, 2+ event types | 3.8% vs 6.1% matched (−2.21 pp) |
| Reverse view (SEC-covered stock-months), within volatility decile | −0.97 pp |

## Data (all free, SEC only)
1. **Who left.** EDGAR full-index `form.idx`, 2017 Q3 to 2024 Q4. Every CIK with a Form `25-NSE` or `25` (exchange delisting) or `15-12B` (deregistration of an exchange-listed class) in that window, **minus** CIKs in our price universe today.
   - Excluded: SIC 6770 (blank-check / SPAC), funds and trusts (SIC 67xx other than 6798 REITs), and CIKs with no 10-Q/10-K.
2. **Why they left.** Per CIK, the `submissions` JSON (one request), with filings in the 120 days before the delisting:

| Evidence | Classified as |
|---|---|
| 8-K item 2.01, `DEFM14A` / `DEFM14C`, `SC TO-T` / `SC 14D9`, or the issuer named in a `425` | **merger / acquisition** |
| 8-K item 1.03 | **bankruptcy** |
| 8-K item 3.01 with no merger evidence | **listing-standard failure** |
| A `15-12B` with neither of the above | **deregistration (going dark)** |
| Nothing classifiable | **unknown** |

3. **What they reported.** Their `companyfacts` JSON, run through the **same** point-in-time engine (`pit.pit_items` with the 120-day stale guard), gives their inflection events and public dates.

**Fetch footprint:** about 1,000–2,500 CIKs × 2 requests, at ≤ 5 requests per second, about 15–20 minutes, cached to `data/delist/` (git-ignored). Read-only.

## What is observed and what is imputed (kept apart in every table)
| Quantity | Observed (SEC) | Imputed (assumption) |
|---|---|---|
| That a missing company existed and was exchange-listed | ✔ (Form 25/15 filer, 10-Q filer) | n/a |
| Its delisting date and reason class | ✔ date; reason ✔ when classifiable | Reason "unknown" is imputed per scenario |
| Its inflection events and public dates | ✔ (point-in-time engine) | n/a |
| Its eligibility at entry ($5 close, $2M dollar volume) | ✘ (no prices) | Size proxy: `dei:EntityPublicFloat` ≥ $75M at the latest 10-K, or included regardless (two variants) |
| Its 250-session return | ✘ | **Delisting-return scenarios** (below); windows that end before delisting are "survived, unknown" |
| Its volatility decile | ✘ | Placed in the top three deciles (pessimistic for the matched comparison) or spread like the observed events (neutral) |

## Scenarios (decided now)
Applied to events and controls alike, unless a row says otherwise:

| Scenario | Bankruptcy / listing failure | Going dark | Merger | Unknown reason | Survived the window, no prices |
|---|---|---|---|---|---|
| **S1 mild** | −30% (not halved) | −30% | +20% | as merger | matched base rate |
| **S2 Shumway** (literature convention: −30% NYSE/AMEX, −55% Nasdaq; Nasdaq assumed) | −55% (halved) | −55% | +20% | as performance | matched base rate |
| **S3 severe** | −100% (halved) | −100% | +20% | as performance | matched base rate |
| **S4 adversarial to the hypothesis** | Events: −100%. Controls: −30% | Same split | +20% | Events: performance. Controls: merger | Events: halved at 2× base. Controls: base |

## Outputs
For each scenario × eligibility variant × all / stacked:
- corrected event halving rate;
- corrected control halving rate;
- the difference, with the month-block bootstrap where months exist.

Corrections add the missing company-quarters (events) and missing company-months (controls) with their imputed outcomes to the observed counts. Counts of missing companies are reported by reason, so the reader sees how much is imputed.

## Decision rule (stated in advance; exploratory, not confirmatory)
- **"Robust to survivorship"**: the stacked-event difference stays below zero in S1–S3, and its sign does not flip in S4.
- **"Survivorship-sensitive"**: the sign flips in S2 or S3. Paid delisted-price data (owner decision) would then be needed before any pre-registration.
- Either way, no alpha is claimed and the holdout stays closed.
