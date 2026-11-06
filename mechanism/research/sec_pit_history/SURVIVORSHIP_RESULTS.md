# Survivorship bounds: results (2026-11-06)

Exploratory and pre-holdout only (events public 2017-09-01 to 2022-12-31). **The reserved holdout (2023-01-01 to 2025-03-31) was not opened.** This bounds a bias; it is not evidence of alpha. Method and decision rule were fixed beforehand in [SURVIVORSHIP_BOUNDS_SPEC.md](SURVIVORSHIP_BOUNDS_SPEC.md); full output in [survivorship_bounds.txt](survivorship_bounds.txt).

## What was fetched (OBSERVED, SEC EDGAR, read-only, free)
- **Source:** EDGAR quarterly form indexes 2016Q1–2024Q4.
  - The fetch ran on the VPS with the existing authorised SEC contact, passed through the environment and never printed.
  - It made 4,158 requests at the SEC's fair-access rate.
- **Candidates:** 4,735 companies filed a delisting form (25-NSE, 25 or 15-12B).
  - **2,315 candidates** remain after removing companies already in our universe and delistings of other securities (the last periodic filing must fall within 200 days of the delisting).
  - **1,807 remain after exclusions** (blank-check companies with SIC 6770, and parse errors).
- **Delisting reasons**, classified from each company's own filings:

| Reason | Companies |
|---|---|
| Merger | 1,313 |
| Listing failure | 216 |
| Bankruptcy | 172 |
| Deregistration | 58 |
| Unknown | 48 |

- **Events:** the same point-in-time engine, with the 120-day stale guard, finds **13,320 inflection events** for these companies, **5,438** of them in the pre-holdout window.

## Result (missing companies' outcomes are IMPUTED per scenario)
The survivor-only study found that company-quarters with **2+ event types** halve **less** often than their matched expectation: 3.82% against 6.11%, **−2.29 pp**.

| Eligibility | Scenario | Corrected difference, 2+ event types | Corrected difference, all events |
|---|---|---|---|
| any | S1 mild | −1.75 pp | −0.80 pp |
| any | S2 Shumway / S3 severe | −4.61 pp | −2.69 pp |
| any | S4 adversarial | **−0.27 pp** | +1.41 pp |
| float ≥ $75M | S1 mild | −1.87 pp | −0.88 pp |
| float ≥ $75M | S2 Shumway / S3 severe | −3.07 pp | −1.58 pp |
| float ≥ $75M | S4 adversarial | **−0.71 pp** | +0.72 pp |

**Decision rule (fixed in advance): ROBUST to survivorship** for the stacked-event (2+) difference. It stays negative in every scenario and eligibility variant.

## How to read it
- **The gap is large.** The missing companies would add about 48% more stacked events (1,258 against 2,643 observed) and 77% more pool stock-months. Survivor-only results on this universe should never be quoted without this correction.
- **The direction survives, but the size is uncertain.**
  - Under the adversarial scenario, the stacked difference shrinks to −0.27 pp (any eligibility) or −0.71 pp (float ≥ $75M): close to zero.
  - The **single-event cut flips positive** under that scenario, so single events carry no robust signal.
- **S2 and S3 are identical by construction.** The outcome measured is a binary halving, so delisting returns of −55% and −100% both count as halved.
- **What the robust part says:** at most, a candidate loss filter. Companies with several simultaneous inflection types are less likely to halve. It does not identify winners, and the left-tail hypothesis stays a candidate in [HYPOTHESIS_CANDIDATE.md](HYPOTHESIS_CANDIDATE.md).
- **Not tested:** the holdout; returns other than halving; costs. Delisting returns themselves are not observed, which is why each scenario imputes them.

Reproduce:
1. Run `survivorship_fetch.py`, which writes `data/delist/`; the data directories are gitignored.
2. Run `python mechanism/research/sec_pit_history/survivorship_bounds.py`. It is deterministic given those inputs and `data/out/` from `study.py`.
