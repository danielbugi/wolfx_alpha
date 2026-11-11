# Candidate hypothesis: inflections as a downside-risk filter

**Status: CANDIDATE, NOT REGISTERED.** It is not in the Lab registry and not one of the six forward-only hypotheses.

It may only be registered, and the reserved window (events public 2023-01-01 to 2025-03-31) only opened, once **all** data-quality gates pass and the owner approves. Today's gate status:

| Gate | Status |
|---|---|
| Coverage | FAIL (55.4%, needs 80%) |
| Survivorship | FAIL |
| Corporate actions | UNKNOWN |

Nothing below is a validated result.

## 1. The negative result (big winners)
In the exploratory pre-holdout sample (public before 2023: 6,577 company-quarters, 1,196 companies, 64 months), SEC point-in-time inflection events did **not** pick out future big winners:

| Measure | Inflection | Matched control (same date, volatility × size) |
|---|---|---|
| Doubled within 250 sessions | 3.6% | 3.4% (difference +0.37 pp [−0.25, +1.17]) |
| Doubled, excluding 2020 entries | 1.7% (pooled) | 1.4% pooled; month-equal difference −0.22 pp [−0.64, +0.18] |
| Share of future doublers that had a recent event (reverse view) | 26.8% | 25.8% base rate (1.04× lift) |

This holds for each event type except FCF turnaround (+1.76 pp [+0.29, +3.65]). That is one of about 40 intervals in the study, so it is noted, not claimed.

**Conclusion:** do not build or market inflections as a winner-discovery signal.

## 2. What was seen instead (exploratory)
| Cut | Halved within 250 sessions | Matched | Difference [95% CI] |
|---|---|---|---|
| All company-quarters | 4.3% | 5.4% | −0.70 pp [−1.39, −0.00] |
| 2+ event types in one quarter | 3.8% | 6.1% | **−2.21 pp [−3.33, −1.27]** |
| Margin expansion | 4.0% | 5.8% | −1.69 pp [−2.57, −0.85] |
| Operating leverage | 2.7% | 4.6% | −1.51 pp [−2.35, −0.71] |
| Profitability turnaround | 4.6% | 6.8% | −2.28 pp [−3.43, −1.33] |
| Reverse view, within volatility decile | | | −0.97 pp [−1.56, −0.52] |

## 3. Proposed confirmatory specification (draft, to freeze before opening the holdout)
- **H1 (one primary test).** Among SEC-covered eligible stocks, company-quarters with **two or more** inflection event types (`inflection_v2_sec_pit`, timely: first public ≤ 120 days after quarter end) have a **lower 250-session halving rate** (close ≤ −50% from the entry open) than same-date controls matched on 60-session volatility decile × dollar-volume tercile.
- **Population:** events public 2023-01-01 to 2025-03-31, read once.
- **Entry, eligibility, discontinuity and matching rules:** exactly as in `study.py` (next-session open; close ≥ $5; 20-day dollar volume ≥ $2M; more than 252 sessions of history).
- **Statistic:** month-equal mean of (event halved − matched expected), with a 3-month block bootstrap. **Pass** if the upper 95% bound is < 0 **and** the effect is at least −1.0 pp.
- **Secondary (descriptive, no claims):** all company-quarters; each event type; doubling rate; ex-2020 does not apply to this window.
- **What is not claimed even on a pass:** return alpha. This tests a risk filter only.

**Prerequisites before registration:**
1. Coverage ≥ 80% of mapped filers.
2. A survivorship fix: at minimum, delisted companies' SEC events plus imputed delisting returns (see `docs/research/SEC_COVERAGE_AND_SURVIVORSHIP_PLAN.md`).
3. The stale-quarter guard (PR #95) applied to the reconstruction.
4. Owner approval.
