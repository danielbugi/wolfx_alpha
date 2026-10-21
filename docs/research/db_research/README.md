# db_research — read-only research on the production database (Notebooks 01–02)

Research artifacts only. Nothing here is imported by the pipeline, backend, bot or frontend, and nothing writes to any database.

| File | What it is |
|---|---|
| `01_vps_database_and_ledger_research.ipynb` | Database inventory, live signal ledger (censoring-corrected), backtest replication, forward labels, paper tracker, funnel |
| `02_earnings_regime_sector_research.ipynb` | Earnings exposure, market regime, sector strength vs **executable** breakout outcomes; 20 pre-declared tests with Holm correction; combined filters |
| `nb02_lib.py` | Simulator (gap-aware, next-open or close entry), point-in-time features, day-cluster bootstrap, Holm |
| `selfcheck_nb02.py` | Reproduction check: simulator = dataset `r_single` (100%) and = live evaluator (100%) |
| `queries/` | Every SQL used, plus `export_nb01.sh` / `export_nb02.sh` (forced read-only sessions) |
| `NOTEBOOK02_REPORT.md` | Notebook 02 results and GO / NO-GO |
| `EXECUTION_RULE_PARITY_AUDIT.md` | 1R vs 3R, close vs open, gaps, costs: every rule set, the reproduction checks and the rule ladder |
| `EARNINGS_RISK_PRODUCT_SPEC.md` | Earnings Risk indicators (spec only) + point-in-time data audit |
| `TAIL_RISK_ML_RESEARCH_PROPOSAL.md` | Tail-loss target proposal (no training) |
| `PHASE2_RECAP_AND_ROADMAP.md` | Recap of what exists and what is unfinished (2026-10-21) |

## Reproduce

Production exports are **not** committed: `data/` is git-ignored. To rebuild them:

```bash
cd docs/research/db_research/queries
bash export_nb01.sh          # signal_ledger, capture, labels, paper tracker, breakouts_v2 ... -> ../data/*.csv
bash export_nb02.sh          # OHLC prices (2020-11+), earnings, sectors, indices -> ../data/nb02_*.csv.gz
cd ..
python selfcheck_nb02.py     # must print OK
jupyter nbconvert --to notebook --execute --inplace 01_vps_database_and_ledger_research.ipynb
jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=3000 02_earnings_regime_sector_research.ipynb
```

- **Access.** The scripts need the deploy SSH key and read only. Each psql session runs with `default_transaction_read_only=on`. No auth, user, bot or session table is exported.
- **Software.** Python with pandas, numpy, matplotlib, nbformat, nbconvert and ipykernel. Notebook 02 builds a cache, `data/nb02_panel.pkl`, on its first run; delete it to rebuild.
- **Results move with the data.** Re-running on a later export gives slightly different numbers, because the ledger and the backtest dataset grow nightly. The committed outputs are the 2026-10-21 run.
