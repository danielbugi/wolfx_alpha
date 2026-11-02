<!-- integrity -->
### What §1 shows

* **[A] Survivorship is close to total.** Of the 1,476 stocks eligible on 2017-09-29, **98.3%** still had a price in September 2026. Across all mature windows, only **0.39%** of eligible stocks lost their price within 250 sessions. Only 38 of 3,114 series end before September 2026, all in 2025–26. Real markets lose several percent of listed companies every year to delisting, bankruptcy and acquisition; this dataset has almost none of them.
* **Consequences, which apply to every later section:**
  * Loss-tail frequencies (≤ −50%, ≤ −75%) are **understated**, because bankruptcies are missing.
  * Winner frequencies and median returns are **overstated**, because the denominator excludes the companies that died.
  * The equal-weight "universe" line rises faster than the S&P 500 partly *because* it only contains survivors.
  * Any comparison *between* groups is affected only if the missing companies are unevenly spread across those groups. Weak-breadth and high-volatility periods are exactly where more companies would have failed, so their apparent advantage below is probably **overstated**.
* **[A] Two data-quality problems were found and fixed.** One vendor row priced a single symbol (CBL) on a market holiday (2026-02-16). Left in, it broke every 200-day average for 200 sessions; the load step now drops such dates and records them. Separately, flagged price discontinuities remove 0.25% of eligible stock-months.

<!-- regimes -->
### What §2 shows

* **[A] The sample holds few independent market episodes.**
  * Bear months (month-end labels) come in 5 separate spells: November 2018–January 2019, March–April 2020, May–October 2022, December 2022 and April 2025.
  * Recovery has 3 spells and Weakening 7.
  * 77% of all sessions are labelled Bull.

  Every regime comparison below rests on a handful of episodes. The **spells** column, not the number of stock-months, is the honest sample size.
* **[A] The trend labels are reasonably stable.** The two alternative definitions agree on 87% of days; most disagreements are at turning points.
* The volatility and breadth regimes switch far more often (37–114 spells) than the trend regime, so they describe short-lived states rather than market eras.

<!-- macro -->
### What §3 shows (descriptive; there are no consensus forecasts)

* **[A] FOMC days are about 1.3× as volatile as an ordinary day** (S&P 500 absolute move, 82 statements since November 2016).
  * The 11 **cut** days averaged −1.6% (95% interval −4.0% to −0.1%) and were 2.6× as volatile as a normal day.
  * These are mostly emergency or early-cycle cuts (March 2020, 2019, late 2024–25). This is a selection effect, not a reaction to cuts as such: the Fed cuts when something is going wrong.
  * The "hawkish read" split (the 2-year yield rose that day) has a negative day-0 return *by construction*: it describes the reaction, it does not predict it.
  * After hold meetings, the 20- to 120-session drift is not distinguishable from an ordinary day.
* **[A] CPI days were only about 1.08× as volatile as an ordinary day across 2016–2026** (119 releases inside the S&P 500 window). Splitting releases by the first-release month-on-month change against the previous month (hotter 57, cooler 51, about the same 11) produces **no group whose 20-session difference from ordinary drift excludes zero**.
  * That split is a naive benchmark, not a surprise: without consensus forecasts we cannot say what was *unexpected*.
* **[A] Jobs-report days were about 1.18× as volatile as an ordinary day** (120 reports in the S&P 500 window; release dates and first-release values from ALFRED, including the shutdown-delayed 2025-11-20 and 2025-12-16 reports).
  * Days when first-release unemployment *rose* were the most volatile (1.42×).
  * Splitting by payrolls above vs below their prior three-month average, or by unemployment up, flat or down, gives **no group whose day-0 or 20-session difference from ordinary drift excludes zero**.
  * Payrolls above trend were followed by slightly better 120-session S&P 500 returns (+2.2% vs −2.0% relative to ordinary drift). These windows overlap heavily and are not evidence.
* **[C] Rates, credit and liquidity states.**
  * Months after the 10-year yield had fallen more than 50 bp in 60 sessions were followed by stronger S&P 500 returns: +7.0% over the next 60 sessions vs +2.8% when yields were stable. That is only 6 separate spells, and they include the 2020 and 2023–24 rate collapses.
  * Months with **tighter** credit (Baa spread above its one-year median) were followed by *better* returns than looser months (S&P 500 +3.8% vs +2.7% over 60 sessions).
  * Fed balance-sheet expansion vs contraction shows no consistent pattern (only 2–3 spells each).

  None of these comes close to being evidence; they are prompts for longer-history data.

<!-- universe -->
### What §4 shows

* **[A] The typical one-year experience of an eligible stock is harsh, even among survivors.**
  * Median 250-session return: about +7.7%.
  * Median *worst drawdown* within that year: **−31%**; 18.5% of stock-years see a drawdown worse than −50%.
  * Gains of ≥ +100% are *reached* at some point within the year in 7.8% of cases but *held* to the year-end in only 4.5%. Big winners are volatile on the way up.
* **[A] Right-skew grows with horizon:** skewness 1.4 at 20 sessions vs 3.2 at 250. Gains ≥ +200% occur in about 1.1% of stock-years.
* **[A] Sector explains only about 8% of cross-sectional return variance** (today's sector labels). Most of what separates one stock's year from another's is stock-specific.
* **[A] By regime (all years):** starts in Bear, weak-breadth, high-VIX or tighter-credit months were followed by higher median returns, more ≥ +100% winners (Bear 7.0% vs Bull 3.9%; high VIX 9.0% vs low 2.6%) and *fewer* large losses.
* **[A] The robustness check changes the reading.** Excluding the 12 start months of 2020:
  * The ≥ +100% share is the same after Bear and Bull months (3.1% vs 3.0%), and **lower** after high-VIX months (1.8%) than mid-VIX months (3.5%).
  * What survives is a gap in *median* return: Bear 9.6% vs Bull 1.4%; weak breadth 10.6% vs strong breadth 0.05%. Lower loss rates after weak markets also survive.
  * By start year, the ≥ +100% share was 1.1–2.5% in 2017–2019 and 2021–2022, 3.4–4.4% in 2023–24, 7.8% in 2025 and **15.4% for 2020 starts**.
* **[B/C] Interpretation.** Exceptional winners are mostly a property of *market episodes*: the rebound after a crash lifts high-beta, beaten-down stocks together. Outside 2020 there is no stable "bad market → more 10-baggers" regularity in this data. There *is* a weaker, more general tendency for the typical stock to do better after broad weakness, consistent with market-level mean reversion. But it rests on 4–5 episodes, and survivorship (missing failures) inflates exactly those starts.

<!-- breakouts -->
### What §5 shows

* **[A] Most breakouts fail quickly in every regime.** 65–75% of 20-day, 55-day and 52-week breakouts close back below the breakout level within 10 sessions, about the same in Bull, Bear, Recovery and Weakening markets.
* **[A] What follows depends on the market regime:**
  * In **Bull** markets, 55-day and 52-week breakouts beat the universe by +1.5–1.6% on average over 60 sessions (median +0.3%).
  * In **Bear** markets they *lag* (52-week highs: mean −1.7%, median −3.8%).
  * 55-day and 52-week breakdowns in **Bear and Weakening** markets mostly *recover*: 61–69% are back above the level 60 sessions later, with positive average excess returns of +1.9% to +3.7%. Twenty-day breakdowns recover less (57–64%). Breakdowns in Bull markets show a much weaker reversal (46–50% recovered, mean excess −0.6% to +0.9%).
* **[A] Breakout density tracks the market's recent past.** 52-week highs and lows swing violently with the market (Figure 3).
* **[A] By sector**, breakouts are spread almost in proportion to the universe (lift 0.90–1.06). No sector is a breakout factory.

<!-- winners -->
### What §6 shows

* **[A] Base rates (eligible stock-months, 250 sessions):** ≥ +50% 14.5%, ≥ +100% 4.5%, ≥ +200% 1.05%, ≤ −50% 4.9%. The 1,931 stock-months with ≥ +200% come from 464 companies in about 522 independent episodes, and 35% of them start in 2020.
* **[B] Volatility is the dominant pre-move characteristic, for both tails.**
  * In the top within-month volatility decile, a ≥ +200% gain is 4.8× as likely as the base rate, and a ≤ −50% loss 3.7× as likely.
  * In the two lowest deciles, ≥ +100% gains are almost absent (0.26% and 0.54%).

  **A right-tail outcome is, first of all, a volatility exposure.**
* **[A] Every momentum-style feature shows a U-shape that raises both tails.** The worst and best 6-month performers, and the worst and best 1-month performers, have about 2× the base rate of ≥ +100% winners *and* 1.6–2.7× the rate of ≤ −50% losers.
* **[A] Near the 52-week high is the *least* likely place for a one-year doubling.**
  * Stocks far below their high (bottom decile) have 2.5× the base ≥ +100% rate and 3.5× the ≤ −50% rate.
  * Stocks at the top decile (near the high) have half the base winner rate *and* about a third of the loser rate.

  Being near the high lowers variance; it does not select winners.
* **[A] A new 55-day high in the last 20 sessions does not raise the ≥ +100% rate** (4.26% vs 4.52%), but it lowers the ≤ −50% rate (3.95% vs 5.55%). This matches PR #90: breakouts did not select the right tail.
* **[A] Lower share prices show higher winner rates** ($5–10: 10.0% ≥ +100%, vs >$50: 2.4%) and higher loser rates. This is the same volatility story.
* **[A] Sector × regime (Figure 4):** the highest ≥ +100% rates are in Consumer Cyclical and Technology starting in Bear or Recovery months (10–12%); the lowest are in Utilities and Real Estate (~1%). The cells are dominated by 2020 and use today's sector labels.
* **Case studies (fixed rule, one per start year):** TNDM, FCEL, NVAX, AMR, IMVT, SMCI and OKLO all sat in the top volatility decile (92nd–100th percentile) *before* the move. Five of the seven had extreme 6-month returns (above the 95th or below the 5th percentile). Four were 37–94% below their 52-week high. All suffered drawdowns of −28% to −64% *inside* the winning year.

<!-- relationships -->
### What §7 shows

* **[A] Breakout density is contemporaneous, not predictive.** It correlates with the past 20 sessions' S&P 500 return (+0.42, interval +0.24 to +0.58) but *not* with the next 20 or 60 sessions (−0.15 and −0.15, intervals include 0).
* **[A, weak] Lower breadth → more ≥ +50% winners the next year:** ρ = −0.32 (−0.57 to −0.05). But n_eff is only about 8 independent years, and the ≥ +100% version (−0.22) includes 0. Given the 2020 dominance in §4, this is not a regularity we can rely on.
* **[A] No detectable relationships:**
  * 10-year yield changes vs NASDAQ-minus-S&P 500 performance (same month −0.14; next month −0.08; both include 0).
  * Credit spreads vs future dispersion of returns.
  * VIX or breadth vs momentum persistence.
* **[C]** Momentum's 60-session spread (top minus bottom decile of 6-month return) averaged about 0 after high-VIX months, vs +2.4% to +2.8% after low- and mid-VIX months. That is consistent with the "momentum crash" literature, but the correlation interval includes 0 and there are only 20 high-VIX months.

<!-- conclusions -->
### The ten most important empirical findings

1. **[A] Today's stock list is not the historical universe.** 98.3% of 2017's eligible stocks are still priced in 2026. Loss tails are understated and winner rates overstated everywhere in this dataset.
2. **[B] The right tail is mostly a volatility exposure.** The top volatility decile has 4.8× the base rate of +200% years and 3.7× the rate of −50% years; low-volatility stocks almost never double.
3. **[A] Exceptional winners are clustered in market episodes.** The ≥ +100% share was 15.4% for 2020 starts, 7.8% for 2025, and 1–4% in other years. With 2020 excluded, the regime differences in winner frequency disappear or reverse.
4. **[A/C] Starts in Bear or weak-breadth months had higher *median* one-year returns even outside 2020** (Bear +9.6% vs Bull +1.4%), with fewer large losses. This rests on 4–5 episodes, and survivorship favours exactly these starts.
5. **[A] Being near the 52-week high halves the chance of doubling and cuts the chance of halving to about a third.** "Near highs" lowers variance; it does not select winners.
6. **[A] 65–75% of breakouts close back below the level within 10 sessions, in every regime.** Breakouts in Bull markets beat the universe slightly over 60 sessions; in Bear markets they lag.
7. **[A] 55-day and 52-week breakdowns in Bear and Weakening markets mostly recover:** 61–69% are back above the level after 60 sessions, with positive excess returns.
8. **[A] Breakout density and net new highs measure the past, not the future:** ρ +0.42 with the trailing month, about −0.15 (including 0) with the next one.
9. **[A] Macro event days:** FOMC days are 1.3× as volatile as normal (cuts 2.6×, with negative day-0 returns, a selection effect). Jobs-report days are 1.18× (1.42× when unemployment rose). CPI days are barely more volatile than normal over 2016–2026 (1.08×). No first-release split of CPI or jobs data shows a forward return difference.
10. **[A] A winner's path is brutal.** The median eligible stock suffers a −31% drawdown within a year. ≥ +100% is reached at some point in 7.8% of stock-years but held to the year-end in 4.5%.

### Surprising or counterintuitive

* **Near the 52-week high** was where one-year doublings were *least* likely, not most.
* **The apparent "bad market → more big winners" pattern is almost entirely 2020.** Without it, high-VIX starts produce *fewer* doublings.
* **Tighter credit** was followed by better returns, not worse.
* **CPI days** were hardly more volatile than normal days over the full decade.

### Strongest relationships (still descriptive)

* Volatility → both tails, about a 3–5× lift, monotonic and within-date.
* Distance from the 52-week high → both tails, U-shaped momentum features → both tails.
* Breakout density ~ past returns, contemporaneous.
* Start year (episode) → winner frequency.

### Weak or unsupported

* Breadth, VIX and credit as forecasters of winners or of momentum.
* Rate changes vs growth stocks.
* FOMC, CPI and jobs outcome splits as forward signals.
* Breakout density as a market forecaster.
* Sector as a breakout source.

### Major limitations

1. **Survivorship:** essentially no delisted companies.
2. **Only 2016-09 → 2026-10:** 4–5 bear episodes, one of them extreme (2020).
3. **Adjusted prices only.**
4. **No point-in-time market cap, sector or fundamentals.**
5. **No consensus macro expectations**, so §3 is descriptive and not a surprise study.
6. **This price history has been examined by earlier First Light studies.**
7. **High-yield spreads are available only for 3 years;** the Baa spread is used instead. NFCI is revised.
8. **Overlapping windows:** the intervals use block bootstraps, but the effective sample for one-year outcomes is about 8 independent years.

### Three hypotheses worth testing independently (class C)

1. **Volatility-matched selection.** Any candidate right-tail signal (fundamental acceleration, insider buying, a catalyst) must raise the ≥ +100% rate *within the same volatility decile* without raising the ≤ −50% rate as much. Otherwise it is only a volatility tilt.
2. **Post-drawdown typical return.** After the market has been in a Bear or weak-breadth state, the typical stock's one-year return is higher than after Bull or strong-breadth states, with fewer large losses. This needs either more history (pre-2016, other markets) or a pre-registered forward record.
3. **Breakdown reversal in weak markets.** 55-day and 52-week breakdowns that occur in Bear or Weakening regimes outperform volatility-matched peers over 60 sessions. This should be tested forward only, because this history has been looked at.

### Recommended next data improvements

1. **Delisted-company price history** (the single biggest fix).
2. **A longer price history,** at least back to 2000, to include 2000–02 and 2008–09.
3. **Point-in-time market cap** from SEC filings (`dei:EntityCommonStockSharesOutstanding`) and point-in-time sector going forward (`sector_observation` is already recording).
4. **Consensus macro forecasts,** if a licensable source exists, to turn §3 into a genuine surprise study.
5. **ALFRED vintages for NFCI,** and a licensed long high-yield-spread history.
