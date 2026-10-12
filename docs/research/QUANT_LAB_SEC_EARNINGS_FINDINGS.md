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

## Addendum 2026-10-12: out-of-sample tests, exit-policy selection, ML-ready outcome table

The study was re-run from scratch on a fresh isolated copy of the production backup (993 SEC requests; SEC served byte-identical payloads for all 993 URLs,
and every earlier statistic reproduced exactly). Three outputs were added; all rules were fixed before the test period was read (train = signal dates before
2022-01-01, test = from 2022-02-15; five pre-named cells, none searched for).

**Bullish Donchian breakout after earnings, fixed 3R, net of 10 bps (mean R, 95% month-block CI)**

| Cell | Train n / R | Test n / R |
|---|---|---|
| Every bullish breakout (baseline) | 22,456 / +0.114 | 27,629 / +0.072 (−0.07 to +0.23) |
| Recent recurring-operating catalyst | 466 / +0.057 | 717 / +0.239 (−0.19 to +0.64) |
| Catalyst + relative volume ≥ 1.5 | 134 / +0.028 | 230 / **+0.300 (+0.01 to +0.62)** |
| Catalyst + relative volume + index uptrend | 128 / +0.053 | 145 / +0.257 (−0.16 to +0.57) |
| No recent earnings, relative volume ≥ 1.5 | 4,945 / +0.076 | 6,470 / +0.118 (−0.01 to +0.25) |

Reading: the catalyst cells are better than the plain breakout in the test period, but the train period points the other way (+0.03 to +0.06 R against +0.11 R),
the test intervals reach the plain breakout's mean, and the one interval that excludes zero is one of five cells. **Inconclusive**, not an edge. The sample is
small (230 trades); the forward collection is what can settle it.

**Exit policy chosen on the train period.** For recurring-operating catalysts and for no-recent-earnings breakouts the train period's best policy is the fixed 3R
exit itself (train means: 3R +0.057/+0.114, 2R −0.012/+0.079, Donchian-10 trail −0.138/−0.006, ATR trail −0.143/+0.026, hybrid −0.115/+0.012), so there is nothing to
beat out of sample. **Not supported**: no exit policy conditional on catalyst type improves on fixed 3R.

**ML-ready outcome table** (`sec_event_outcomes.csv.gz`, 9,614 rows, csv sha256 recorded in the study JSON): one row per earnings event with identities (symbol, CIK,
accession numbers, SEC filing URL), the release acceptance timestamp, point-in-time features, anomaly class, realised outcomes (reaction, +5 and +20 session drift,
big-move flag) and a train / embargo / test split label (4,128 / 123 / 5,363). Its integrity is tested against the recorded hash.

## Addendum 2026-10-12 (2): walk-forward by year, timestamp placebo, variant-keyed outcomes

Third run of the study on a fresh isolated restore (993 requests). All 15 earlier result blocks reproduced exactly; 992 of the 993 SEC payloads were byte-identical, the 993rd being the
daily-refreshed ticker file. The catalyst + relative-volume cell is **exploratory evidence, not a validated trading edge**: the rules below were fixed before the numbers existed, and year counting
can never upgrade a claim to "supported".

**Timestamp integrity, tested empirically (placebo).** If the acceptance-time to reaction-bar mapping is right, the overnight gap of BMO / AMC releases lands on the reaction bar and a
baseline-sized gap sits on the bar before it. Mean absolute gap, reaction bar vs the bar before: **AMC 4.89% vs 0.98% (x5.0, n=5,556); BMO 4.02% vs 0.75% (x5.4, n=2,451)**; intraday releases 3.45% vs 0.78%.
Claim "supported". 92.6% of Item 2.02 releases were accepted on or before the day of the periodic filing (median lag 1 day, 10th to 90th percentile 0 to 14 days).

**Walk-forward by calendar year (cell vs every bullish breakout, fixed 3R, net 10 bps; a year counts when both have at least 30 trades).**

| Cell | Years counted | Better than the plain breakout |
|---|---|---|
| Recent recurring-operating catalyst | 9 (2018-2026) | 6 (inconclusive: below the 75% bar) |
| Catalyst + relative volume ≥ 1.5 | 5 (2021-2025; 2018-2020 and 2026 have under 30 trades) | 4 ("suggestive" only) |
| Catalyst + volume + index uptrend | 3 | 2 (too few years) |

The catalyst + relative volume cell is thin (17 to 66 trades per year) and is one of five cells; the pooled out-of-sample interval still reaches the plain breakout's mean (previous addendum).

**Strategy-specific outcome tracking.** Outcomes are now keyed by the registered variant identities (`donchian_breakout__exit_<policy>`, version, definition hash), per direction and per year, and
after a recurring-operating catalyst, so every number traces to one exact definition. A breakout-level ML-ready table (`sec_breakout_outcomes.csv.gz`, 88,604 signals: catalyst class, relative
volume, regime, plan R, and the net R under each of the five variants, with a train / embargo / test label) is committed with its sha256 and tested against it.
