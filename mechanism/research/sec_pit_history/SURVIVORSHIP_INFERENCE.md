# Survivorship bounds: uncertainty, independence and economics (2026-11-06)

Pre-holdout only (public 2017-09 to 2022-12). **The holdout was not opened.** This is **not validated alpha**: it describes a left-tail rate, part of it rests on imputed outcomes, and nothing has been tested out of sample.
- Full output: [survivorship_inference.txt](survivorship_inference.txt).
- Script: `survivorship_inference.py` (seed 7, 2,000 draws; deterministic).
- Point estimates are those of [SURVIVORSHIP_RESULTS.md](SURVIVORSHIP_RESULTS.md).

## Bottom line
1. **Under S1–S3, the stacked-event (2+) effect is clearly negative:** stacked events halve less often than matched stocks. Every 95% interval excludes zero, including the two-way company × quarter bootstrap.
2. **Under the adversarial scenario (S4) it is not distinguishable from zero.** The point estimates are −0.27 pp (any eligibility) and −0.71 pp (float ≥ $75M), and the probability of a negative difference is 0.62–0.96 depending on the bootstrap. The earlier "robust" verdict was a rule on point estimates; **with uncertainty included, it holds for S1–S3 and fails for S4.**
3. **Single events are not robust:** under S4 the all-events difference is significantly **positive** (+1.41 pp).
4. **At the adversarial bound, the effect is worth about 0.17–0.45% of position value a year.** That is at or below one round trip's cost in this universe. **It is not economically meaningful on its own** unless the milder scenarios hold.

## 1. All eight sensitivity scenarios, with intervals (2+ event types; the all-events cut is in the output file)
The corrected difference is the event halving rate minus its matched expectation over 250 sessions. In every bootstrap draw, imputed outcomes are drawn as Bernoulli(p), not fixed at their expectation.

| Eligibility | Scenario | Point | 95% CI, company clusters | 95% CI, quarter clusters | 95% CI, two-way | P(diff < 0), two-way |
|---|---|---|---|---|---|---|
| any | S1 mild | −1.75 pp | −2.42 … −1.11 | −2.73 … −0.51 | −3.05 … −0.34 | 0.992 |
| any | S2 Shumway | −4.61 pp | −5.42 … −3.81 | −6.45 … −2.37 | −6.77 … −2.20 | 1.000 |
| any | S3 severe | −4.61 pp | −5.44 … −3.79 | −6.42 … −2.49 | −6.70 … −2.19 | 1.000 |
| any | **S4 adversarial** | **−0.27 pp** | −1.06 … +0.50 | −1.49 … +1.36 | **−1.80 … +1.38** | **0.621** |
| float ≥ $75M | S1 mild | −1.87 pp | −2.53 … −1.12 | −2.91 … −0.66 | −3.21 … −0.43 | 0.996 |
| float ≥ $75M | S2 Shumway | −3.07 pp | −3.80 … −2.33 | −4.43 … −1.40 | −4.75 … −1.25 | 0.999 |
| float ≥ $75M | S3 severe | −3.07 pp | −3.81 … −2.27 | −4.44 … −1.43 | −4.70 … −1.32 | 0.999 |
| float ≥ $75M | **S4 adversarial** | **−0.71 pp** | −1.46 … +0.11 | −1.84 … +0.78 | **−2.22 … +0.94** | **0.801** |

**S2 equals S3 by construction:** a binary halving outcome cannot tell a −55% delisting return from a −100% one. Calendar-quarter clustering widens every interval about 1.5–2×, because there are only 22 independent quarters and the 250-session windows of one quarter overlap.

## 2. Independence (observed, survivor-only, 2+ event types)
**Sample:** 2,643 company-quarters, 806 companies, 22 calendar quarters.

| View | Difference |
|---|---|
| Pooled (every company-quarter) | −2.29 pp |
| **One observation per company** (each company's mean) | **−1.48 pp**, 95% CI −2.46 … −0.39; 70% of companies negative, 9% positive, the rest zero |
| First qualifying quarter per company | −1.47 pp (n = 806) |
| Calendar quarters | 15 of 22 negative; mean of quarter means −1.88 pp |

**By year:**

| Year | Events | Companies | Halved | Expected | Difference |
|---|---|---|---|---|---|
| 2017 | 80 | 80 | 5.00% | 2.62% | +2.38 pp |
| 2018 | 349 | 240 | 3.15% | 3.52% | −0.36 pp |
| 2019 | 283 | 214 | 3.89% | 8.91% | −5.02 pp |
| 2020 | 324 | 227 | 0.62% | 0.46% | +0.16 pp |
| 2021 | 977 | 541 | 5.63% | 9.13% | −3.50 pp |
| 2022 | 630 | 382 | 2.86% | 4.94% | −2.08 pp |

**Reading:**
- Weighting each company once shrinks the effect by a third, but it stays below zero.
- The effect comes from three years: 2019, 2021 and 2022. 2021 alone is 37% of the sample and was followed by the 2022 drawdown, when matched small and volatile stocks halved often.
- Four of six years are negative. This is a regime-dependent pattern, not a constant one.

## 3. Matched control method (unchanged from `study.py`, fixed before outcomes)
- **Pool:** the same entry session (the open after the SEC acceptance date), from the SEC-covered eligible pool. Eligibility: close at least $5, 20-session mean dollar volume at least $2M, more than 252 sessions of history.
- **Cells:** within-date 60-session volatility decile × dollar-volume tercile. Volatility decile alone is used when a cell has fewer than 10 names.
- **Expected rate:** an event's expected halving rate is its cell's same-date rate.
- **Exclusions:** windows touching a recorded price discontinuity are dropped, as are quarters first public more than 120 days late.
- **Survivorship correction:** it scales the expected rate by the change the missing companies cause in the pool's halving rate. The matching itself is not redone with the missing companies, whose prices are not available. This is a limit.

## 4. Economic meaningfulness
| Effect used | Halving rate lower by | × mean loss when halved | Value per position per 250 sessions |
|---|---|---|---|
| Adversarial, any eligibility | 0.27 pp | 64% | **0.17%** |
| Adversarial, float ≥ $75M | 0.71 pp | 64% | **0.45%** |
| S1 mild, any eligibility | 1.75 pp | 64% | 1.12% |
| Observed, survivor-only | 2.29 pp | 64% | 1.46% |

- **Trading costs:** a round trip in this universe costs about **0.10–0.60%** (spread, impact and commission; the smaller names sit at the top). Rebalancing quarterly pays it up to four times.
- **Opportunity cost:** a loss filter only removes candidates. The cost is the return of what it excludes, which this study does not measure for the missing companies.
- **For scale:** the observed 250-session excess return of stacked events is +2.20% (event-level interval +0.44 … +4.17). That figure is not clustered and is survivor-biased upward, so it is an upper reference, not an estimate.

**Verdict:**
- **At the adversarial bound, the halving-risk reduction is about the size of one round-trip cost.** It is not economically meaningful as a stand-alone rule.
- **Under the milder scenarios (worth 1–3% a year), it could matter as one input to a risk filter,** but only after the holdout test, whose design is fixed in [HYPOTHESIS_CANDIDATE.md](HYPOTHESIS_CANDIDATE.md).
- **No alpha is claimed.**

## What would change the conclusion
- **Observed delisting returns,** for example from CRSP. That would replace the imputation and remove most of the S1–S4 range. It is a purchase, so it needs a separate decision.
- **The holdout test (2023-01 to 2025-03),** run once and only on the owner's approval.
- **Re-matching with the delisted companies' own prices,** if a free source exists for them. The current correction rescales the expected rate instead.
