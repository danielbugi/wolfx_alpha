# Quant Lab: SEC earnings catalyst experiment (2026-10-11)

Status: research only. Nothing here is deployed, migrated or wired into any live signal path. Run on an isolated scratch copy of the
production backup; production tables were not written. Evidence: `docs/research/evidence/quant_lab_2026-10-11/sec_*`.
Report in `/lab`: `catalyst_sec_earnings` (served from `backend/lab_reports/`, read-only).

## What was run

Real SEC data, end to end, reusing the existing contracts (`filing_contract`, `edgar_collector`, `catalyst classification`):

1. Submissions (filing identity + **SEC acceptance timestamp**) and XBRL companyfacts for 400 liquid symbols (391 produced events), fetched live
   (993 requests, 5 req/s, declared User-Agent; the contact is not stored in the repo). Every payload's sha256 is in `sec_raw_manifest.json`; the
   committed result was re-derived from the on-disk cache (0 new requests, 996 cache hits) and matches.
2. Facts copied verbatim into point-in-time quarterly series (`sec_financials`); Q4 = annual − Q1..Q3, flagged derived.
3. Anomaly rules v1 declared a priori (`financial_anomaly`): revenue YoY ≥ 20% and acceleration ≥ 10 pts; net income YoY ≥ 50% / turnaround;
   operating-margin expansion ≥ 3 pts. Nature label: recurring_operating, one_time_suspected (net income moved with no operating move), unclassified.
4. Decision time = acceptance timestamp of the 8-K Item 2.02 release matched to the periodic report (`catalyst_timing`); the reaction bar follows
   BMO / intraday / AMC. Market-adjusted vs an equal-weight index; month-block bootstrap CIs.

## Extraction quality and timestamp integrity

| Measure | Result |
|---|---|
| Periodic filings seen | 16,350 |
| With usable XBRL facts | 14,031 (85.8%) |
| Events used | 9,614 across 391 symbols, 2016-12 to 2026-09 |
| Events matched to their 8-K Item 2.02 release | 97.4% |
| Release timing | AMC 5,556 · BMO 2,451 · intraday 1,605 · unknown 2 |
| Filing date inconsistent with acceptance date | 10 (kept out of the decision time; acceptance wins) |
| Filing records refused by the contract | 2,562, all pre-2005-style 8-K item numbers (outside the window) |
| Dropped | 2,319 no matching facts, 2,526 outside price history, 1,890 illiquid/penny, 1 price discontinuity |

Historical coverage is thin before 2018 (10 events in 2016, 612 in 2017): the SEC facts API and ticker mapping limit it. Ticker→CIK uses today's SEC
file projected back, so every link is on a **reconstructed basis** (delisted names are absent; survivorship applies).

## Results

**Can financial anomalies be detected from real SEC filings? Yes.** Market-adjusted reaction on the release session:

| Class | n | Reaction (95% CI) | 20-day drift after (95% CI) |
|---|---|---|---|
| No anomaly | 6,270 | −0.51% (−0.73, −0.29) | −0.47% (−0.81, −0.14) |
| Recurring operating | 1,544 | **+2.22%** (+1.73, +2.69) | −0.10% (−0.86, +0.70) |
| One-time suspected | 761 | +0.73% (+0.18, +1.26) | −0.28% (−1.09, +0.49) |
| Unclassified | 1,039 | +0.64% (+0.16, +1.18) | −0.75% (−1.43, −0.01) |

- The recurring-operating reaction is positive in 10 of 10 years and its interval is clear of the no-anomaly group; the market reacts about 3x
  more to a recurring operating improvement than to a net-income jump with no operating move. **Supported.**
- **No tradeable drift** follows: the 20-day interval after the reaction includes zero. The information is priced on the release session.
  **Not supported** as an after-the-fact trade.
- The thresholds flag roughly a third of events, so this selects directionally; it does not isolate rare events. The no-anomaly group reacts
  negatively against the index: compare groups, not levels.

**Catalyst × breakout × relative volume × regime.** Mean relative volume on the release day is 3.04x for recurring operating vs 2.71x without an anomaly.
For Donchian breakouts within 15 days of earnings (fixed 3R exit, net 10 bps): bullish recurring_operating +0.155 R (n=1,194, CI −0.14 to +0.44)
vs +0.085 R with no recent earnings (n=44,547). Every interval includes zero. **Inconclusive**; the relative-volume and regime splits are in the
report and are similarly inconclusive. Cells are many and small; none is a confirmed edge.

**Exit policies conditional on catalyst type.** Within bullish recurring-operating breakouts no policy beats fixed 3R with a separated interval.
**Not supported.**

**ML (chronological split, train to 2022-01, test from 2022-02-15, n_test 5,363).**
- Size of the move: AUC 0.7016 vs trailing volatility alone 0.6986. **No lift beyond volatility.**
- Direction of the reaction: AUC 0.563 vs best single-metric baseline 0.546 on a base rate of 0.506. **Suggestive only**; not a promotion candidate.

## Limits to keep in view

- Anomaly rules and the recurring/one-time label are interpretation; "one-time suspected" is inferred from the absence of an operating move, not
  from reading the filing text. No FDA/clinical or headline-derived inference is used anywhere in this study.
- Intraday releases are only partly reflected in the same-day bar.
- The ex-ante use (a live signal) would need forward collection of the same fields; this study does not establish it.

## Reproduce

`ml_training/experiments/sec_earnings_study.py` (needs `SEC_USER_AGENT` in the environment and a scratch database), then
`ml_training/experiments/build_lab_reports.py <evidence_dir> backend/lab_reports <commit>`. Tests: `ml_training/tests/test_sec_earnings_pipeline.py`,
`test_build_lab_reports.py`, `mechanism/market_intelligence/tests/test_sec_financials.py`, `test_catalyst_timing.py`.
