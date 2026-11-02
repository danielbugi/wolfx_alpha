# Market Behavior Atlas

A descriptive, executed study of how the whole stock universe in First Light's database behaved from 2017 to 2026 under different market and macro conditions, and of which conditions came before exceptional stock performance. **Not a strategy backtest.** Nothing here is optimised, and no rule here is a trading signal.

**Read:** [`market_behavior_atlas.ipynb`](market_behavior_atlas.ipynb). It is executed, with outputs, 11 figures (also in [`figures/`](figures/)) and interpretations. The ten key findings, limitations and hypotheses are in its §8 and in [`interpretations.md`](interpretations.md).

Research only. No database writes, schedules, Telegram changes, hypotheses or holdouts were touched. The production database was read only through the forced read-only extract.

## Data

| Dataset | Coverage | Point-in-time? | Notes |
|---|---|---|---|
| `stock_prices` (production, read-only extract `research/right_tail/extract.py`) | 6,519,681 rows, 3,114 symbols, 2016-09-20 → 2026-10-30; sha256 `aefa1522…cb74` | Revised (vendor back-adjusted) | **Survivor universe:** 98.3% of 2017's eligible stocks are still priced in 2026. One stray holiday row (CBL, 2026-02-16) is dropped. 1,044 flagged discontinuities are excluded from windows |
| Sector | 3,054 symbols, observed 2026-10 (`sector_observation`, yfinance) | **No**: today's label applied to all history | |
| FRED | S&P 500 (2016-11 →, FRED 10-year licence), NASDAQ, VIX, 2y/10y/3m yields, TIPS 10y, Baa−10y, HY OAS (2023-11 → only), NFCI, WALCL, fed funds target, broad dollar | Market series yes; NFCI and WALCL revised | `data/macro/fred_*.csv` |
| ALFRED (first release) | CPI headline and core, payrolls, unemployment, 2016–2026; **exact release dates found by binary search on vintages** (e.g. jobs 2020-02-07, CPI 2020-02-13; the 2025 shutdown delays and the cancelled October 2025 CPI are handled) | Yes: values as first published | `data/macro/releases_*.csv`, `alfred_cache.json` |
| federalreserve.gov | 93 press releases 2016–2026 → 89 FOMC statements (3 emergency statements in March 2020) | Yes | `data/macro/fomc_press_releases.csv` |

**Not available:** consensus macro expectations (so §3 is descriptive, not a surprise study); point-in-time market cap and fundamentals (SEC coverage is 20 companies); delisted companies; index history before 2016-11.

## Reproduce

```bash
cd mechanism/research/market_behavior_atlas
# 1. prices (read-only, git-ignored): the same extract as the right-tail study
ssh -i ~/.ssh/donchian_deploy deploy@116.203.220.219 'docker exec -i donchian-screener-backend-1 python - --tar-stdout --start 2016-01-01' \
    < ../right_tail/extract.py > x.tar && mkdir -p data/prices && tar -xf x.tar -C data/prices
#    sector snapshot (read-only psql, git-ignored): SELECT DISTINCT ON (symbol) symbol, sector, effective_session, source FROM sector_observation
#    WHERE sector IS NOT NULL ORDER BY symbol, captured_at DESC, seq DESC  ->  data/sectors_observed_2026-10.csv
# 2. public macro data (committed; re-running re-uses the cache): ~2,300 throttled ALFRED requests, about 45 minutes the first time
python fetch_macro.py
# 3. build and execute the notebook (~2 minutes)
python build_notebook.py
```

Tests: `python -m pytest mechanism/research/tests/test_market_behavior_atlas.py` (8 tests, synthetic data). They cover:
- the release-date search;
- discontinuity windows;
- event-window arithmetic;
- the bootstrap guard;
- spells;
- the breakout definitions;
- the stray-date regression;
- features never using later bars.

## Files

| File | Role |
|---|---|
| `atlas_lib.py` | All computation (pure pandas/numpy; no DB, no network) |
| `fetch_macro.py` | FRED / ALFRED / Fed downloads into `data/macro/` |
| `build_notebook.py` | Composes and executes the notebook |
| `interpretations.md` | The prose interpretations inserted into the notebook, written after reading the executed outputs |
