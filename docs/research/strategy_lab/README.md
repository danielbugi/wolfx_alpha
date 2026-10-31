# Strategy lab: the research loop

One versioned strategy goes in; a reproducible backtest, an out-of-sample verdict against controls, an immutable run record and a report come out.
Code: `mechanism/research/strategy_lab/` (research only: it never writes `strategies` or `signal_ledger`, and nothing in the pipeline, backend or bot imports it).
Ledger: migration 39 (`add_research_strategy_ledger.sql`), **not applied to production**; use a research database.

## The loop

```
extract (read-only, hashed)  ->  run (spec + extract -> result.json + report.md)  ->  record (append-only ledger)  ->  signals (what it would look at now)
```

```bash
# 1. extract prices + earnings from a database, read-only (self-contained; can be piped into a container: docker exec -i <backend> python - --tar-stdout < extract.py > x.tar)
python mechanism/research/strategy_lab/extract.py --out DIR --start 2020-09-01
# 2. run the registered strategy over the extract
PYTHONPATH=mechanism python -m research.strategy_lab run --data DIR --out OUT --code-ref $(git rev-parse HEAD)
# 3. store the spec and the run in the research ledger (research database only)
PYTHONPATH=mechanism python -m research.strategy_lab record --out OUT --host H --dbname D --user U --apply --confirm-database D
# 4. informational: signals whose signal bar is among the last N sessions (nothing is written or sent)
PYTHONPATH=mechanism python -m research.strategy_lab signals --data DIR --sessions 3
```

## What makes a result trustworthy here

* **Declared first.** The strategy definition (thresholds, exit, hold caps, costs, split, and the *decision rule*) is one canonical JSON document in `specs.py`; its sha256 is the strategy's
  identity. Changing it is a new version, never an edit. No parameter is fitted on the data.
* **Point in time.** A signal uses only bars up to its own signal bar and enters at the next open. A test rewrites every later bar and requires identical signals; mutating the code to
  peek forward (the reaction, or a data guard that looks ahead) makes it fail.
* **Existing engine, not a new one.** Exits and position accounting are `research.exit_policies` and `research.experiment_runner` (one open position per symbol and direction, censoring,
  purge-aware chronological split, costs charged in R), the same code the forward paper tracker uses.
* **Controls.** Every report with the same exit and no thresholds (what the thresholds add), and a random date of the same stock (what being there gives). Intervals are month-block
  bootstraps because observations in one month are not independent.
* **Mechanical verdict.** `PASS_FOR_FORWARD_SHADOW` needs every check on the primary cell: enough out-of-sample positions, a net expectancy interval above zero, an interval above zero against the
  random-date control, a positive train expectancy, and a result that survives removing the top 1% of winners. Otherwise `NO_EDGE_DEMONSTRATED` or `INSUFFICIENT_DATA`. A PASS authorises a forward
  *shadow* run only.
* **Reproducible.** Same extract + same code + same seed gives a byte-identical report (verified on the real run); the run record stores the spec, data, code, split, seed and report hashes.

## First run: earnings_drift v1 (2026-10-31)

Post-earnings drift: report surprise of at least 10% and a same-direction first reaction of 3% to 40%, long after a beat and short after a miss, 20-session hold, 20 bps round trip.
Data: 3,963,910 price rows, 2,823 symbols, 59,426 reports with a reported EPS, 2020-09-01 to 2026-10-30. Train 2021 to 2023, embargo January 2024, test from 2024-02-01 (5,189 positions).

**Verdict: NO EDGE DEMONSTRATED.** Test net expectancy +0.023 R with a 95% interval of [-0.123, +0.164]; train -0.003 R; signal minus random-date control +0.019 R, interval [-0.215, +0.241]; the thresholds add
+0.020 R over all reports, interval [-0.020, +0.060]. Longs and shorts differ (+0.083 R and -0.129 R in test), and the sign of the result flips by year. Report:
[earnings_drift_v1_2026-10-31_report.md](earnings_drift_v1_2026-10-31_report.md); numbers: [result.json](earnings_drift_v1_2026-10-31_result.json). Extract sha256 `b27523d0…56f0b6`
(prices `7f2023e2…159e1`, earnings `6a704315…6b6958`); code `9f399e73`.

What it does not say: that earnings drift never works. It says this definition, on this data (yfinance earnings, not point-in-time), shows nothing that survives costs and controls. The next
candidates (a second strategy version is a new spec; the SEC filing times from the EDGAR pipeline would remove the report-time ambiguity) go through the same loop unchanged.

## Adding a strategy

1. A new definition in `specs.py` (a new key or a new version of an existing key), with its decision rule.
2. Its entry rule as a pure function like `engine.detect` (bars up to the signal bar only), plus its controls.
3. Tests: hand-built events, the future-mutation test, a planted-edge panel and a no-edge panel.
4. `run`, read the report, `record`.

## Not done yet (deliberately)

* **Scheduling.** A nightly timer would need a root-installed systemd unit; the loop is a manual or CI command for now.
* **Forward shadow.** Passing strategies get their signals recorded forward through the paper tracker (dormant), never through the live `signal_ledger`.
