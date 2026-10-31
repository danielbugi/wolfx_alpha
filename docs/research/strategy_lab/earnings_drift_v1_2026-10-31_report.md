# Strategy research report: earnings_drift v1

**Verdict: NO EDGE DEMONSTRATED. At least one pre-declared check failed on the out-of-sample period. Nothing here supports trading this strategy as defined.**

## Identity (everything needed to reproduce this)
- Strategy spec sha256: `1af275757e075dcf9980507756580b6ab7973f20dd46b9ef4338806b558f07de`
- Data (prices + earnings extract) sha256: `b27523d0f35bdde74c9d11b7cce40f97d235c61250834940eacacf55cc56f0b6`
- Code: `9f399e73bc6d3e1337b117107c32e39ca17d0549`  |  Seed: `20261031`
- Run hash: `23de78ea98595de97da218cdfac6d4a795b1a62ebd51a778fc4bf1297cfa7e74`
- Extract: prices 3963910 rows, 2823 symbols, 2020-09-01 to 2026-10-30; earnings 59426 reports with a reported EPS

## What was tested (declared before any result was looked at)
- Hypothesis: After an earnings report whose surprise and first price reaction agree, the stock keeps drifting in that direction over the following weeks by more than costs.
- Event: a report whose surprise is at least 10% in size and whose first price reaction (3% to 40%) points the same way. Long after a beat, short after a miss.
- Entry: next session's open after the reaction window. Exit: 2 x ATR14 at the signal bar (1R) stop, 3R target, 20-session cap (a 60-session cap is shown as context).
- Liquidity: price at least $5 and average dollar volume of at least $5,000,000 in the 20 sessions before the report.
- Split by signal date: train 2021-01-01 to before 2024-01-01; embargo to 2024-02-01; test from 2024-02-01. a train signal whose position is still open at test_start is dropped from train.
- Costs: 20 bps round trip is the primary case; 10, 20, 40 bps shown.
- Controls: every report with the same exit and no thresholds (what the thresholds add), and a random date of the same stock (what just being in this stock at this time gives).

## Decision rule and checks (primary cell: test period, both directions, 20-session hold, 20 bps)
- PASS: n_test >= 150 (observed: 5189)
- FAIL: test net expectancy 95% interval entirely above 0 (observed: [-0.12252, 0.16372])
- FAIL: signal minus random-date control, 95% interval entirely above 0 (observed: [-0.2148, 0.2409])
- FAIL: train net expectancy > 0 (observed: -0.003)
- FAIL: test expectancy without the top 1% of winners > 0 (observed: -0.008)

## Results (net R after costs; 1R = 2 x ATR14 at the signal)

| Strategy | Period | Direction | Positions | Net expectancy R | 95% interval | Win rate | Profit factor | Without top 1% of winners |
|---|---|---|---|---|---|---|---|---|
| Earnings drift signal | train | both | 5024 | -0.0034 | [-0.163, +0.153] | 0.446 | 0.993 | -0.0352 |
| Earnings drift signal | train | long | 3617 | 0.0082 | [-0.213, +0.223] | 0.442 | 1.016 | -0.0242 |
| Earnings drift signal | train | short | 1407 | -0.0330 | [-0.262, +0.190] | 0.454 | 0.927 | -0.0631 |
| Earnings drift signal | test | both | 5189 | 0.0230 | [-0.123, +0.164] | 0.460 | 1.049 | -0.0077 |
| Earnings drift signal | test | long | 3715 | 0.0833 | [-0.089, +0.257] | 0.468 | 1.178 | 0.0529 |
| Earnings drift signal | test | short | 1474 | -0.1289 | [-0.339, +0.066] | 0.440 | 0.728 | -0.1587 |
| Control: all reports | train | both | 19626 | -0.0120 | [-0.164, +0.140] | 0.439 | 0.976 | -0.0436 |
| Control: all reports | train | long | 14547 | 0.0053 | [-0.208, +0.216] | 0.441 | 1.011 | -0.0258 |
| Control: all reports | train | short | 5079 | -0.0616 | [-0.290, +0.190] | 0.432 | 0.877 | -0.0941 |
| Control: all reports | test | both | 20644 | 0.0036 | [-0.116, +0.119] | 0.450 | 1.007 | -0.0280 |
| Control: all reports | test | long | 15070 | 0.0697 | [-0.080, +0.222] | 0.469 | 1.154 | 0.0388 |
| Control: all reports | test | short | 5574 | -0.1754 | [-0.385, +0.058] | 0.399 | 0.675 | -0.2079 |
| Control: random date | train | both | 4886 | -0.0530 | [-0.177, +0.068] | 0.403 | 0.904 | -0.0875 |
| Control: random date | train | long | 3547 | -0.0657 | [-0.259, +0.137] | 0.397 | 0.883 | -0.0987 |
| Control: random date | train | short | 1339 | -0.0192 | [-0.179, +0.151] | 0.419 | 0.964 | -0.0577 |
| Control: random date | test | both | 4680 | 0.0041 | [-0.105, +0.112] | 0.411 | 1.007 | -0.0299 |
| Control: random date | test | long | 3323 | 0.0628 | [-0.112, +0.231] | 0.423 | 1.117 | 0.0285 |
| Control: random date | test | short | 1357 | -0.1397 | [-0.295, +0.023] | 0.381 | 0.764 | -0.1731 |

### Signal minus controls (same costs and exit, month-block bootstrap)
- train, 20-session hold, signal minus random: +0.0496 R, 95% interval [-0.1622, +0.2701] (n 5024 vs 4886)
- train, 20-session hold, signal minus all reports: +0.0086 R, 95% interval [-0.0210, +0.0372] (n 5024 vs 19626)
- train, 60-session hold, signal minus random: +0.0725 R, 95% interval [-0.1725, +0.3308] (n 4907 vs 4713)
- train, 60-session hold, signal minus all reports: +0.0273 R, 95% interval [-0.0273, +0.0782] (n 4907 vs 18721)
- test, 20-session hold, signal minus random: +0.0190 R, 95% interval [-0.2148, +0.2409] (n 5189 vs 4680)
- test, 20-session hold, signal minus all reports: +0.0195 R, 95% interval [-0.0204, +0.0602] (n 5189 vs 20644)
- test, 60-session hold, signal minus random: +0.0204 R, 95% interval [-0.2200, +0.2665] (n 5101 vs 4608)
- test, 60-session hold, signal minus all reports: +0.0207 R, 95% interval [-0.0376, +0.0696] (n 5101 vs 19734)

### Cost sensitivity (test period, both directions, signal)
- 20-session hold: 10 bps: 0.0357; 20 bps: 0.0230; 40 bps: -0.0023
- 60-session hold: 10 bps: 0.0701; 20 bps: 0.0574; 40 bps: 0.0320

### By signal year (primary hold and cost)
| Year | Positions | Net expectancy R |
|---|---|---|
| 2021 | 1595 | 0.0601 |
| 2022 | 1759 | -0.0779 |
| 2023 | 1670 | 0.0145 |
| 2024 | 1733 | 0.0785 |
| 2025 | 1930 | 0.0472 |
| 2026 | 1526 | -0.0705 |

## How many events were dropped, and why
- dropped data guard: 99
- dropped history too short or no entry bar: 308
- dropped liquidity: 15234
- dropped no eps or surprise: 799
- dropped no trading session near report: 297
- not signal reaction above max split or deal: 165
- not signal reaction below threshold: 7691
- not signal reaction disagrees with surprise: 5634
- not signal surprise below threshold: 18257
- reports: 59240
- signals: 10756
- symbols with reports: 2782

## Limits that apply to this result
- The earnings data come from yfinance: not point-in-time (a restated or moved date looks as if it was always final) and the time of day of the report is unknown, which is why the reaction window spans two sessions. The strategy's signal is taken only after both sessions have closed.
- No parameter was tuned on this data; the thresholds are round numbers fixed beforehand. Even so, one strategy, two directions, two hold caps and three costs were looked at, and only the primary cell decides.
- Fills are modelled (next open, stop at its level or the gap, a flat cost in basis points). Small-cap spreads and borrow costs for shorts can be worse than the costs shown.
- A PASS authorises a forward shadow run only. It is not a trading claim, and past results do not establish future ones.
