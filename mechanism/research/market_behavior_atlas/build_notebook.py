"""Builds and executes market_behavior_atlas.ipynb (code cells + explanatory markdown + interpretations written after reading the executed outputs; interpretations live in
interpretations.md, keyed by section, so the analysis code and the prose are reviewed separately).

    cd mechanism/research/market_behavior_atlas && python build_notebook.py          # needs data/prices (see README) and data/macro (fetch_macro.py)
"""
import os
import re
import sys

import nbformat as nbf
from nbconvert.preprocessors import ExecutePreprocessor

HERE = os.path.dirname(os.path.abspath(__file__))


def interpretations():
    path = os.path.join(HERE, "interpretations.md")
    if not os.path.exists(path):
        return {}
    txt = open(path, encoding="utf-8").read()
    parts = re.split(r"^<!-- (\w+) -->\s*$", txt, flags=re.M)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


CELLS = []


def md(s):
    CELLS.append(nbf.v4.new_markdown_cell(s.strip("\n")))


def code(s):
    CELLS.append(nbf.v4.new_code_cell(s.strip("\n")))


def interp(key):
    t = INTERP.get(key)
    if t:
        md(t)


INTERP = interpretations()

md(r"""
# First Light — Market Behavior Atlas

**What this is.** A descriptive study of how the whole US equity universe in our database behaved from 2017 to 2026 under different market and macro conditions, and of which conditions came before exceptional stock performance. It is **not** a strategy backtest: nothing here is optimised, and no rule here is a trading signal.

**How to read the evidence.** Every result is tagged with one of three classes:

| Class | Meaning |
|---|---|
| **A — Descriptive** | True of *this dataset* (today's survivors, adjusted prices, 2017–2026). |
| **B — Plausibly general** | Consistent with how markets work and robust enough here that it probably holds for the historical market, but unconfirmed. |
| **C — Hypothesis** | Interesting, but needs an independent test on data not used here (ideally forward). |

**Ground rules.** Every regime label uses data up to the date it labels. Stock statistics are averaged month by month, so a month with 2,400 stocks does not outweigh a month with 1,500. Overlapping windows are never treated as independent: intervals use block bootstraps, and tables report the number of independent episodes. Correlation is not causation, and nothing here is presented as predictive alpha.
""")
interp("intro")

md(r"""
## 0. Setup and data

* **Prices:** `stock_prices` from the production database via the read-only, hashed extract `research/right_tail/extract.py`. Prices are vendor back-adjusted for splits, dividends and spin-offs.
* **Macro and market data (public):** FRED (latest vintage), ALFRED (CPI and payrolls *as first published*, with exact release dates) and federalreserve.gov (FOMC statement dates), fetched by `fetch_macro.py` into `data/macro/`.
* **Sector:** the sector observed in October 2026, applied to all history. This is **not** point-in-time.
* **Eligible stock on date t:** close ≥ \$5, 20-session average dollar volume ≥ \$2M, at least 252 earlier sessions, and no flagged price discontinuity in the last 252 sessions or in the forward window being measured.
""")
code(r"""
import os, json, warnings
import numpy as np, pandas as pd
import matplotlib.pyplot as plt, matplotlib.dates as mdates
import atlas_lib as A
warnings.filterwarnings("ignore")
pd.set_option("display.width", 220); pd.set_option("display.max_columns", 40); pd.set_option("display.float_format", lambda x: f"{x:,.4g}")
plt.rcParams.update({"figure.dpi": 100, "axes.grid": True, "grid.alpha": .3, "font.size": 9, "axes.titlesize": 10})
FIG = "figures"; os.makedirs(FIG, exist_ok=True)
def save(fig, name):
    fig.savefig(f"{FIG}/{name}.png", bbox_inches="tight", dpi=110); plt.show()
TREND_COL = {"Bull": "#2e7d32", "Recovery": "#9ccc65", "Weakening": "#ffa726", "Bear": "#c62828"}

p = A.load_panel("data/prices")
reg = A.build_regimes(p)
sec = pd.read_csv("data/sectors_observed_2026-10.csv").set_index("symbol")["sector"]
fp = A.attach(A.forward_panel(p), reg, sec)
print("extract:", {k: p.meta[k] for k in ("prices_rows", "prices_symbols", "prices_min_date", "prices_max_date", "prices_sha256")})
print("stray calendar dates dropped (fewer than 500 symbols priced):", p.meta["stray_dates_dropped"])
print("panel:", p.C.shape[0], "sessions x", p.C.shape[1], "symbols;", "month-end cross-sections:", fp["date"].nunique(), "; stock-month rows:", len(fp))
print("macro files:", sorted(os.listdir("data/macro")))
""")

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------- 1. integrity
md(r"""
## 1. Data integrity and survivorship — read this first

Every later number rests on this section. The key question is whether today's stock list can stand in for the historical investable universe. It cannot.
""")
code(r"""
C = p.C
first = C.apply(lambda s: s.first_valid_index()); last = C.apply(lambda s: s.last_valid_index())
data_end = C.index[-1]
yr = pd.DataFrame({
    "symbols with any price": C.notna().groupby(C.index.year).any().sum(axis=1),
    "first appearance (new listings)": first.dt.year.value_counts().reindex(sorted(set(C.index.year)), fill_value=0),
    "series ending before 2026-09": last[last < pd.Timestamp("2026-09-01")].dt.year.value_counts().reindex(sorted(set(C.index.year)), fill_value=0),
})
yr.loc[yr.index.min(), "first appearance (new listings)"] = np.nan          # the extract starts in 2016: "first appearance" that year is the data start, not a listing
print(yr.to_string())
me = A.month_ends(C.index, "2017-09-01")
elig_me = p.eligible.loc[me].sum(axis=1)
in_fp = fp.groupby("date").size().reindex(me, fill_value=0)
r250_missing = fp.assign(m=fp["r250"].isna()).groupby("date")["m"].mean()
start_set = set(fp.loc[fp["date"] == me[0], "symbol"])
still = sum(1 for s in start_set if last[s] >= pd.Timestamp("2026-09-01"))
print(f"\nStocks eligible on {me[0].date()}: {len(start_set)}; still priced in Sep 2026: {still} ({still / len(start_set):.1%}).")
print(f"Eligible stock-months removed by the discontinuity lookback rule: {1 - in_fp.sum() / elig_me.sum():.2%}")
print(f"Flagged discontinuities: {int(p.disc.values.sum())} symbol-days on {int(p.disc.any().sum())} symbols")
mature = fp[fp["date"] <= C.index[-251]]
print(f"Mature 250-session windows with NO closing price at t+250 (stock vanished or a discontinuity): {mature['r250'].isna().mean():.2%} of {len(mature):,}")
""")
code(r"""
fig, ax = plt.subplots(2, 2, figsize=(13, 7))
yr[["symbols with any price"]].plot.bar(ax=ax[0, 0], color="#90a4ae", legend=False); ax[0, 0].set_title("Symbols with any price, by year")
a2 = ax[0, 0].twinx(); a2.plot(range(len(yr)), yr["series ending before 2026-09"], "r-o", label="series ending (delisted/merged)"); a2.plot(range(len(yr)), yr["first appearance (new listings)"], "b-s", label="new listings"); a2.legend(loc="lower right", fontsize=8); a2.grid(False)
ax[0, 1].plot(elig_me.index, elig_me.values, label="eligible at month-end"); ax[0, 1].plot(in_fp.index, in_fp.values, label="in the analysis (clean lookback)"); ax[0, 1].legend(); ax[0, 1].set_title("Eligible universe size (denominator)")
ewn = reg["ew_index"] / reg["ew_index"].loc[reg["spx"].first_valid_index()]; spn = reg["spx"] / reg["spx"].dropna().iloc[0]
ax[1, 0].plot(ewn.index, ewn, label="equal-weight median of our survivors"); ax[1, 0].plot(spn.index, spn, label="S&P 500 (FRED)"); ax[1, 0].set_yscale("log"); ax[1, 0].legend(); ax[1, 0].set_title("Survivor universe vs a real index (both = 1 at start)")
ax[1, 1].plot(r250_missing.index, r250_missing.values * 100); ax[1, 1].set_title("% of eligible stocks with no price 250 sessions later\n(real markets lose several % a year to delisting; here ~0 until the data end)"); ax[1, 1].set_ylabel("%")
fig.suptitle("Figure 10 — Data coverage and survivorship diagnostics", fontsize=11); fig.tight_layout(); save(fig, "fig10_coverage_survivorship")
""")
interp("integrity")

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------- 2. regimes
md(r"""
## 2. Market regimes (point-in-time, transparent)

| Dimension | Definition (computed only from data up to that day) | Source |
|---|---|---|
| **Trend** | Bull: S&P 500 above its 200-day average and the average rising over 20 sessions. Recovery: above a still-falling average. Weakening: below a still-rising average. Bear: below a falling average. | FRED SP500 |
| **Momentum** | S&P 500 126-session return > +10% "Strong up", < −10% "Down", else "Sideways". | FRED SP500 |
| **Volatility** | VIX < 15 low, 15–25 mid, > 25 high. | FRED VIXCLS |
| **Breadth** | Share of eligible stocks above their own 200-day average: < 40% weak, 40–60% mid, > 60% strong (survivor universe; see §1). | our prices |
| **Financial conditions** | Baa corporate–Treasury spread above its own trailing one-year median = tighter, below = looser. NFCI is shown as a cross-check, but it is *revised*. | FRED BAA10Y, NFCI |

None of these is fitted to future returns. Robustness: the trend labels are recomputed with a 150-day average and 10-day slope, and with a 250-day average and 40-day slope.
""")
code(r"""
dims = ["trend", "momentum", "volatility", "breadth_regime", "fin_conditions"]
tab = []
for d in dims:
    lab = reg[d].dropna()
    for v, n in lab.value_counts().items():
        tab.append({"dimension": d, "regime": v, "sessions": n, "share": n / len(lab), "separate spells": A.spells(lab, v)})
print(pd.DataFrame(tab).to_string(index=False))
print("\nRobustness of the trend definition (share of days with the same label):"); print(A.regime_agreement(p).to_string(index=False))
print("\nMonth-end trend label sequence (B=Bull R=Recovery W=Weakening X=Bear):")
mt = reg["trend"].reindex(me).dropna().map({"Bull": "B", "Recovery": "R", "Weakening": "W", "Bear": "X"})
for y in sorted(set(mt.index.year)): print(y, "".join(mt[mt.index.year == y]))
""")
code(r"""
fig, ax = plt.subplots(4, 1, figsize=(13, 9), sharex=True, gridspec_kw={"height_ratios": [3, 1.2, 1.2, 1.2]})
s = reg["spx"].dropna()
ax[0].plot(s.index, s.values, "k-", lw=.8); ax[0].plot(reg["spx_sma"].index, reg["spx_sma"], color="gray", lw=.8, ls="--", label="200-day average"); ax[0].set_yscale("log")
lab = reg["trend"]
for k, col in TREND_COL.items():
    m = (lab == k).astype(int); ax[0].fill_between(lab.index, 0, 1, where=m.values == 1, transform=ax[0].get_xaxis_transform(), color=col, alpha=.18, lw=0, label=k)
ax[0].legend(ncol=5, fontsize=8, loc="upper left"); ax[0].set_title("Figure 1 — Market regime timeline: S&P 500 with point-in-time trend regime")
ax[1].plot(reg["vix"].index, reg["vix"], lw=.7); [ax[1].axhline(x, color="r", ls=":", lw=.8) for x in (15, 25)]; ax[1].set_ylabel("VIX")
ax[2].plot(reg["breadth"].index, reg["breadth"] * 100, lw=.7, color="purple"); [ax[2].axhline(x, color="r", ls=":", lw=.8) for x in (40, 60)]; ax[2].set_ylabel("% above 200d")
ax[3].plot(reg["baa10y"].index, reg["baa10y"], lw=.8, label="Baa−10y spread"); ax[3].plot(reg.index, reg["baa10y"].rolling(252, min_periods=200).median(), ls="--", lw=.8, label="its 1y median"); ax[3].set_ylabel("%"); ax[3].legend(fontsize=8)
ax[3].xaxis.set_major_locator(mdates.YearLocator()); fig.tight_layout(); save(fig, "fig01_regime_timeline")
""")
interp("regimes")

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------- 3. macro events
md(r"""
## 3. Macro events: FOMC, CPI, employment

**Important — no consensus forecasts.** We have no historical market expectations (no survey consensus, no fed funds futures), so **this is a descriptive event study, not a surprise study**. Where events are split into groups, the split uses:
* For CPI and payrolls: the **first-release** number against a naive benchmark (the previous month as published in the same release). This is *not* what markets expected.
* For FOMC: the actual decision (hike/cut/hold, from the target range), and as a market-implied proxy the direction of the 2-year Treasury yield on the day. That proxy is **contemporaneous**, so it describes the reaction and cannot predict it.

Returns run from the close before the event to the close k sessions after it (k = 0 is the event day itself; CPI and jobs are released at 8:30 ET, FOMC at 14:00 ET). Each group is compared with the *unconditional* average of the same window over all days, because a 120-session window is mostly ordinary market drift. Events are about a month apart, but 60- and 120-session windows overlap heavily, so their intervals are too narrow and are shown for orientation only.
""")
code(r"""
HAVE = {k: os.path.exists(f"data/macro/releases_{k}.csv") for k in ("cpi", "jobs")}
fomc = A.fomc_decisions()
cpi = A.releases_from_cache("cpi") if HAVE["cpi"] else None
jobs = A.releases_from_cache("jobs") if HAVE["jobs"] else None
for k, v in HAVE.items():
    if not v: print(f"*** {k.upper()} release table not collected yet (ALFRED crawl still running): that part of the event study is PENDING, not empty. ***")
spx, ewi = reg["spx"].dropna(), reg["ew_index"]
cov = spx.index[0]
dgs2 = A.fred("DGS2")
fomc["dy2_bp"] = [ (dgs2.loc[:d].iloc[-1] - dgs2.loc[:d - pd.Timedelta(days=1)].iloc[-1]) * 100 if len(dgs2.loc[:d - pd.Timedelta(days=1)]) else np.nan for d in fomc["date"]]
print("FOMC statements:", len(fomc), fomc["decision"].value_counts().to_dict(), "| CPI releases:", None if cpi is None else len(cpi), "| jobs reports:", None if jobs is None else len(jobs))
for nm, t in (("CPI", cpi), ("Jobs", jobs)):
    if t is not None:
        lag = (t["release_date"] - t["ref_month"]).dt.days
        print(f"{nm}: {t['release_date'].min().date()} -> {t['release_date'].max().date()}; days from reference month start to release: median {lag.median():.0f}, max {lag.max():.0f} "
              f"(late releases: {list(t.loc[lag > 50, 'release_date'].dt.date.astype(str))})")
KS = (0, 5, 20, 60, 120)
unc = {nm: A.unconditional(s, KS) for nm, s in (("S&P 500", spx), ("Universe median", ewi.loc[cov:]))}
allday_abs = {nm: float((s / s.shift(1) - 1).abs().mean()) for nm, s in (("S&P 500", spx), ("Universe median", ewi.loc[cov:]))}

def evt_table(groups):
    rows = []
    for nm, s in (("S&P 500", spx), ("Universe median", ewi.loc[cov:])):
        for g, dates in groups.items():
            er = A.event_returns(s, pd.DatetimeIndex(dates)[pd.DatetimeIndex(dates) >= cov], KS)
            if len(er) == 0: continue
            r = {"series": nm, "group": g, "events": len(er), "abs move day0 vs all days": er["k0"].abs().mean() / allday_abs[nm]}
            for k in KS:
                dm, lo, hi = A.diff_ci(er[f"k{k}"].values, unc[nm][k])
                r[f"k{k} mean"] = er[f"k{k}"].mean(); r[f"k{k} minus all-days"] = dm
                if k in (0, 20): r[f"k{k} diff 95% CI"] = f"[{lo:+.4f}, {hi:+.4f}]"
            rows.append(r)
    return pd.DataFrame(rows)

G = {}
G["FOMC"] = {"all statements": fomc["date"], **{k: v["date"] for k, v in fomc.groupby("decision")}, "2y yield rose on the day (hawkish read)": fomc.loc[fomc["dy2_bp"] > 0, "date"], "2y yield fell on the day (dovish read)": fomc.loc[fomc["dy2_bp"] < 0, "date"]}
if cpi is not None:
    cpi["hot"] = np.where(cpi["headline_mom"] - cpi["headline_mom_prev"] > 0.05, "hotter than prior month", np.where(cpi["headline_mom"] - cpi["headline_mom_prev"] < -0.05, "cooler than prior month", "about the same"))
    G["CPI"] = {"all CPI releases": cpi["release_date"], **{k: v["release_date"] for k, v in cpi.groupby("hot")}}
if jobs is not None:
    jobs["pay_vs3"] = np.where(jobs["payrolls_change_k"] > jobs["payrolls_prev3_mean_k"], "payrolls above prior 3-mo avg", "payrolls below prior 3-mo avg")
    jobs["u"] = np.where(jobs["unrate_change"] > 0.05, "unemployment up", np.where(jobs["unrate_change"] < -0.05, "unemployment down", "unemployment unchanged"))
    G["Jobs"] = {"all jobs reports": jobs["release_date"], **{k: v["release_date"] for k, v in jobs.groupby("pay_vs3")}, **{k: v["release_date"] for k, v in jobs.groupby("u")}}
ET = {k: evt_table(v) for k, v in G.items()}
cols = ["series", "group", "events", "abs move day0 vs all days", "k0 mean", "k0 diff 95% CI", "k5 minus all-days", "k20 minus all-days", "k20 diff 95% CI", "k60 minus all-days", "k120 minus all-days"]
for k, t in ET.items():
    print(f"\n=== {k} ===  (all-days means S&P 500: " + ", ".join(f"k{x}={unc['S&P 500'][x]:+.4f}" for x in KS) + ")"); print(t[cols].to_string(index=False))
""")
code(r"""
def path(s, dates, lo=-5, hi=20):
    s = s.dropna(); ix = s.index; out = []
    for e in pd.DatetimeIndex(dates):
        i = ix.searchsorted(e)
        if i + lo - 1 < 0 or i + hi >= len(ix): continue
        base = s.iloc[i - 1]; out.append([s.iloc[i + k] / base - 1 for k in range(lo, hi + 1)])
    return np.array(out)
fig, ax = plt.subplots(1, 3, figsize=(14, 4), sharey=True)
xs = np.arange(-5, 21)
drift = np.array([unc["S&P 500"][0] * 0] + [0] * 0)
dd = (spx / spx.shift(1) - 1).mean()
for a, (title, groups) in zip(ax, [("FOMC", {"Hike": G["FOMC"]["Hike"], "Cut": G["FOMC"]["Cut"], "Hold": G["FOMC"]["Hold"]}),
                                   ("CPI (first release vs prior month)", {k: v for k, v in G.get("CPI", {}).items() if k != "all CPI releases"}),
                                   ("Jobs report", {k: v for k, v in G.get("Jobs", {}).items() if k.startswith("payrolls")})]):
    if not groups:
        a.text(.5, .5, f"{title}:\nrelease table pending\n(ALFRED collection running)", ha="center", va="center", transform=a.transAxes); a.set_title(title); continue
    for g, dts in groups.items():
        P = path(spx, dts)
        if len(P): a.plot(xs, P.mean(0) * 100, marker=".", label=f"{g} (n={len(P)})")
    a.plot(xs, np.where(xs >= 0, (xs + 1) * dd, xs * dd) * 100, "k:", label="average drift, all days")
    a.axvline(0, color="gray", lw=.8); a.set_title(title); a.set_xlabel("sessions from event (0 = event day)"); a.legend(fontsize=7)
ax[0].set_ylabel("S&P 500 cumulative return from close before event, %")
fig.suptitle("Figure 2 — Macro event studies (descriptive; no consensus expectations available)"); fig.tight_layout(); save(fig, "fig02_macro_event_study")
""")
md(r"""
### 3b. Rates, credit and liquidity *states* (not events) and what followed

These are monthly observations, each describing the state at month-end with data available then. Long-horizon windows overlap, so the *spells* column is the honest count of independent episodes.
""")
code(r"""
dgs10, dfii, walcl = A.fred("DGS10"), A.fred("DFII10"), A.fred("WALCL")
M = pd.DataFrame(index=me)
M["d10y_60"] = (dgs10.reindex(C.index).ffill() - dgs10.reindex(C.index).ffill().shift(60)).reindex(me)
M["real10_60"] = (dfii.reindex(C.index).ffill() - dfii.reindex(C.index).ffill().shift(60)).reindex(me)
M["walcl_13w"] = (walcl.reindex(C.index).ffill() / walcl.reindex(C.index).ffill().shift(63) - 1).reindex(me)
M["fin"] = reg["fin_conditions"].reindex(me)
for h in (60, 120):
    M[f"spx_f{h}"] = (spx.reindex(C.index).ffill().shift(-h) / spx.reindex(C.index).ffill() - 1).reindex(me)
    M[f"univ_med_f{h}"] = fp.groupby("date")[f"r{h}"].median().reindex(me)
M["rates"] = np.where(M["d10y_60"] > 0.5, "10y yield up >50bp in 60d", np.where(M["d10y_60"] < -0.5, "10y yield down >50bp in 60d", "10y yield roughly stable"))
M["real"] = np.where(M["real10_60"] > 0.4, "real yield up >40bp", np.where(M["real10_60"] < -0.4, "real yield down >40bp", "real yield stable"))
M["liq"] = np.where(M["walcl_13w"] > 0.02, "Fed balance sheet growing >2%/13w", np.where(M["walcl_13w"] < -0.02, "Fed balance sheet shrinking >2%/13w", "Fed balance sheet ~flat"))
M.loc[M["d10y_60"].isna(), "rates"] = np.nan
rows = []
for dim in ("rates", "real", "liq", "fin"):
    for g, sub in M.dropna(subset=[dim]).groupby(dim):
        rows.append({"state": g, "months": len(sub), "spells": A.spells(M[dim].dropna(), g), "S&P next 60d mean": sub["spx_f60"].mean(), "S&P next 120d mean": sub["spx_f120"].mean(),
                     "universe median stock next 60d": sub["univ_med_f60"].mean(), "universe median next 120d": sub["univ_med_f120"].mean()})
print(pd.DataFrame(rows).to_string(index=False))
""")
interp("macro")

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------- 4. universe
md(r"""
## 4. Whole-universe forward performance

At each month-end, every eligible stock gets an outcome: its close-to-close return over the next 20, 60, 120 and 250 sessions, and the worst drawdown inside the next 250 sessions. Statistics are computed **within each month first and then averaged across months**, so each month counts once and one stock appearing in many months is not treated as many independent stocks. The tables give the number of months, the number of separate regime spells, stock-month observations and unique stocks.
""")
code(r"""
rows = []
for h in A.HORIZONS:
    col = f"r{h}"; d = fp.dropna(subset=[col]); g = d.groupby("date")[col]
    rows.append({"horizon": h, "months": d["date"].nunique(), "stock-months": len(d), "unique stocks": d["symbol"].nunique(), "avg stocks/month": round(len(d) / d["date"].nunique()),
                 "median": g.median().mean(), "mean": g.mean().mean(), "IQR": (g.quantile(.75) - g.quantile(.25)).mean(), "skew": g.skew().mean(),
                 **{f">=+{int(x*100)}%": g.apply(lambda s, x=x: (s >= x).mean()).mean() for x in A.GAINS}, **{f"<=-{int(x*100)}%": g.apply(lambda s, x=x: (s <= -x).mean()).mean() for x in A.LOSSES},
                 "median stock vs universe-median path": d.groupby("date")[f"x{h}"].median().mean(), "mean stock vs its sector median": d.groupby("date")[f"xs{h}"].mean().mean()})
print("Unconditional forward outcomes of an eligible stock (month-equal weighted):"); print(pd.DataFrame(rows).set_index("horizon").T.to_string())
d = fp.dropna(subset=["r250"])
print(f"\nMax drawdown inside the next 250 sessions: median {d.groupby('date')['maxdd250'].median().mean():.1%}; share with a drawdown worse than -50%: {d.groupby('date')['maxdd250'].apply(lambda s: (s <= -.5).mean()).mean():.1%}")
print(f"Max gain reached inside 250 sessions (MFE) vs held to 250: >=+100% reached {d.groupby('date')['maxgain250'].apply(lambda s: (s >= 1).mean()).mean():.1%} vs held {d.groupby('date')['r250'].apply(lambda s: (s >= 1).mean()).mean():.1%}")
def sector_r2(df, col):
    out = []
    for t, x in df.dropna(subset=[col]).groupby("date"):
        x = x[x["sector"] != "Unknown"]
        if len(x) < 100: continue
        tot = x[col].var(); between = x.groupby("sector")[col].transform("mean").var()
        out.append(between / tot)
    return float(np.mean(out))
print("Share of cross-sectional return variance explained by sector (today's sector labels):", {h: round(sector_r2(fp, f'r{h}'), 3) for h in (20, 60, 250)})
""")
code(r"""
BR = {dim: A.by_regime(fp, dim, 250) for dim in ["trend", "momentum", "volatility", "breadth_regime", "fin_conditions"]}
for dim, t in BR.items():
    print(f"\n=== 250-session outcomes by {dim} at the start month ==="); print(t.T.to_string())
print("\n=== 60-session outcomes by trend ==="); print(A.by_regime(fp, "trend", 60).drop(columns=["median_maxdd250"]).T.to_string())
""")
md(r"""
**Robustness: is the regime pattern just 2020?** The 2020 crash and rebound is one episode, but it supplies many stock-months. Below: the ≥100% share by start year, and the regime tables recomputed **without any start month in 2020**.
""")
code(r"""
d250 = fp.dropna(subset=["r250"])
yr_rate = d250.groupby(d250["date"].dt.year).apply(lambda s: pd.Series({"months": s["date"].nunique(), "share >=+100%": s.groupby("date")["r250"].apply(lambda x: (x >= 1).mean()).mean(),
                                                                         "share <=-50%": s.groupby("date")["r250"].apply(lambda x: (x <= -.5).mean()).mean(), "median 250d": s.groupby("date")["r250"].median().mean()}))
print("By start year:"); print(yr_rate.to_string())
ex = fp[fp["date"].dt.year != 2020]
for dim in ("trend", "volatility", "breadth_regime", "fin_conditions"):
    t = A.by_regime(ex, dim, 250)[["months", "spells", "median_return", "gain>=50%", "gain>=100%", "gain>=200%", "loss>=50%"]]
    print(f"\n{dim}, 2020 start months EXCLUDED:"); print(t.to_string())
""")
code(r"""
order = ["Bull", "Recovery", "Weakening", "Bear"]
fig, ax = plt.subplots(1, 2, figsize=(14, 4.5))
d = fp.dropna(subset=["r250", "trend"])
data = [d.loc[d["trend"] == k, "r250"].clip(-1, 3).values * 100 for k in order]
ax[0].boxplot(data, tick_labels=[f"{k}\n{BR['trend'].loc[k,'months']} mo / {BR['trend'].loc[k,'spells']} spells" for k in order], showfliers=False, whis=(5, 95))
ax[0].axhline(0, color="k", lw=.6); ax[0].set_ylabel("250-session return, % (box 25-75, whiskers 5-95)"); ax[0].set_title("Forward 250-session returns by trend regime at start")
for k in order:
    v = np.sort(d.loc[d["trend"] == k, "r250"].values); ax[1].plot(np.linspace(0, 100, len(v)), np.clip(v, -1, 5) * 100, label=k, color=TREND_COL[k])
ax[1].set_xlabel("percentile of stocks"); ax[1].set_ylabel("250-session return %, clipped at +500%"); ax[1].set_yscale("symlog", linthresh=50); ax[1].legend(); ax[1].set_title("Full distribution (quantile curves)")
fig.suptitle("Figure 5 — Distribution of forward stock returns by regime (pooled stock-months; regime months are few, see spells)"); fig.tight_layout(); save(fig, "fig05_return_distribution_by_regime")

fig, axs = plt.subplots(1, 5, figsize=(17, 4), sharey=True)
for a, (dim, t) in zip(axs, BR.items()):
    x = np.arange(len(t)); w = .27
    for j, c in enumerate(["gain>=50%", "gain>=100%", "gain>=200%"]):
        a.bar(x + (j - 1) * w, t[c] * 100, w, label=c)
    a.set_xticks(x); a.set_xticklabels([f"{i}\n({t.loc[i,'months']}m/{t.loc[i,'spells']}sp)" for i in t.index], fontsize=7, rotation=30, ha="right"); a.set_title(dim)
axs[0].set_ylabel("% of eligible stocks, next 250 sessions"); axs[0].legend(fontsize=7)
fig.suptitle("Figure 6 — Share of stocks gaining ≥50% / ≥100% / ≥200% over the next 250 sessions, by regime at the start (m = months, sp = separate spells)"); fig.tight_layout(); save(fig, "fig06_winner_share_by_regime")
""")
interp("universe")

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------- 5. breakouts
md(r"""
## 5. Breakout and breakdown dynamics

These are daily events in eligible stocks:
* **Breakout:** the first close above the prior N-session high, for N = 20, 55 and 252 (a 52-week high).
* **Breakdown:** the mirror image, using lows.

Outcomes:
* **Failed:** the stock closes back through the level within 10 sessions.
* **Sustained** (breakouts): 60 sessions later it is still above the level *and* has beaten the equal-weight universe.
* **Continued** (breakdowns): 60 sessions later it is still below the level and has lagged the universe.
* **Recovered:** 60 sessions later it is back on the other side of the level.

Excess returns are measured against the equal-weight median of the universe. Events cluster heavily in time, so the counts below are not independent observations.
""")
code(r"""
EV = {(n, k): A.breakout_events(p, n, k) for n in (20, 55, 252) for k in ("up", "down")}
dens = pd.DataFrame({f"{'52w' if n == 252 else str(n) + 'd'} {'breakouts' if k == 'up' else 'breakdowns'}": A.density(p, e) for (n, k), e in EV.items()})
elig_days = p.eligible.sum(axis=1)
rows = []
for (n, k), e in EV.items():
    e = e.join(reg[["trend", "volatility", "breadth_regime"]], on="date")
    for g, sub in e.groupby("trend"):
        days = reg.index[reg["trend"] == g]
        rows.append({"event": f"{'52w' if n == 252 else str(n) + 'd'} {'breakout' if k == 'up' else 'breakdown'}", "trend": g, "events": len(sub), "per 100 eligible/day": 100 * len(sub) / elig_days.reindex(days).sum(),
                     "unique companies": sub["symbol"].nunique(), "failed in 10d": sub["failed10"].mean(), ("sustained 60d" if k == "up" else "continued 60d"): sub["sustained60"].mean(),
                     "recovered 60d": sub["recovered60"].mean(), "mean excess 60d": sub["x60"].mean(), "median excess 60d": sub["x60"].median(), "n with 60d": int(sub["x60"].notna().sum())})
BT = pd.DataFrame(rows)
print(BT.to_string(index=False))
""")
code(r"""
fig, ax = plt.subplots(3, 1, figsize=(13, 8), sharex=True, gridspec_kw={"height_ratios": [1.3, 1, 1]})
sm = dens.rolling(20, min_periods=10).mean()
ax[0].plot(sm.index, sm["52w breakouts"], color="#2e7d32", label="52-week highs"); ax[0].plot(sm.index, sm["52w breakdowns"], color="#c62828", label="52-week lows")
ax[1].plot(sm.index, sm["55d breakouts"], color="#2e7d32", label="55-day breakouts"); ax[1].plot(sm.index, sm["55d breakdowns"], color="#c62828", label="55-day breakdowns")
ax[2].plot(reg["spx"].dropna().index, reg["spx"].dropna(), "k", lw=.8); ax[2].set_yscale("log")
for a in ax:
    for k, col in TREND_COL.items():
        m = (reg["trend"] == k).astype(int); a.fill_between(reg.index, 0, 1, where=m.values == 1, transform=a.get_xaxis_transform(), color=col, alpha=.13, lw=0)
ax[0].set_ylabel("per 100 eligible / day\n(20-day avg)"); ax[1].set_ylabel("per 100 eligible / day"); ax[2].set_ylabel("S&P 500"); ax[0].legend(); ax[1].legend()
ax[0].set_title("Figure 3 — Breakout vs breakdown density over time, shaded by point-in-time trend regime (green Bull, light green Recovery, orange Weakening, red Bear)")
fig.tight_layout(); save(fig, "fig03_breakout_breakdown_density")

fig, axs = plt.subplots(1, 3, figsize=(15, 4), sharey=True)
for a, nm in zip(axs, ["55d breakout", "52w breakout", "55d breakdown"]):
    t = BT[BT["event"] == nm].set_index("trend").reindex(order)
    good = "sustained 60d" if "breakout" in nm else "continued 60d"
    x = np.arange(len(order)); a.bar(x - .2, t["failed in 10d"] * 100, .4, label="failed (back through level in 10d)", color="#ef9a9a"); a.bar(x + .2, t[good] * 100, .4, label=good.replace("60d", "(60d, vs universe)"), color="#81c784")
    a.set_xticks(x); a.set_xticklabels([f"{o}\nn={int(t.loc[o,'events']):,}" for o in order], fontsize=8); a.set_title(nm); a.legend(fontsize=7)
axs[0].set_ylabel("% of events")
fig.suptitle("Figure 8 — Momentum continuation vs failure by trend regime"); fig.tight_layout(); save(fig, "fig08_continuation_vs_failure")
""")
code(r"""
e55 = EV[(55, "up")]
elig_sector = pd.Series(np.repeat(p.eligible.columns, p.eligible.sum(axis=0).values)).map(sec).fillna("Unknown").value_counts(normalize=True)
ev_sector = e55["symbol"].map(sec).fillna("Unknown").value_counts(normalize=True)
dn_sector = EV[(55, "down")]["symbol"].map(sec).fillna("Unknown").value_counts(normalize=True)
print("Sector share of 55-day breakouts / breakdowns vs share of eligible stock-days (today's sector labels):")
print(pd.DataFrame({"eligible stock-days": elig_sector, "55d breakouts": ev_sector, "55d breakdowns": dn_sector, "breakout lift": ev_sector / elig_sector}).sort_values("breakout lift").to_string())
""")
interp("breakouts")

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------- 6. winners
md(r"""
## 6. Exceptional winners: what did they look like *before* the move?

A **winner** is an eligible stock-month whose close 250 sessions later is at least 50%, 100% or 200% higher. Each characteristic is measured at the start month with data available then, and is **ranked within that month** (percentiles). That removes date effects: we compare stocks with other stocks on the same date, not 2020 with 2022.

Each table compares the winner rate in each decile with the **base rate of the whole eligible universe**, not with other winners. Losses (≤ −50%) are shown next to gains because a characteristic that raises both is a volatility exposure, not a selection skill.

Market capitalisation is **not available point-in-time**; 20-day dollar volume is used as a size proxy. Fundamentals are excluded, because SEC point-in-time coverage is 20 companies.
""")
code(r"""
d = fp.dropna(subset=["r250"]).copy()
for x in (0.5, 1.0, 2.0): d[f"W{int(x*100)}"] = d["r250"] >= x
d["L50"] = d["r250"] <= -0.5
base = {c: d.groupby("date")[c].mean().mean() for c in ("W50", "W100", "W200", "L50")}
print("Base rates (month-equal):", {k: f"{v:.2%}" for k, v in base.items()})
w = d[d["W200"]].sort_values("date")
epi, lastd = [], {}
for s, t in zip(w["symbol"], w["date"]):
    if s not in lastd or (t - lastd[s]).days > 365: epi.append((s, t))
    lastd[s] = t
print(f"W200 stock-months: {len(w):,}; unique companies: {w['symbol'].nunique()}; independent episodes (a new episode needs a 12-month gap): {len(epi)}")
print("W200 stock-months by start year:", w["date"].dt.year.value_counts().sort_index().to_dict())
FEATS = {"ret126_pct": "6-month return (relative strength)", "vol60_pct": "60-day volatility", "adv20_musd_pct": "dollar volume (size proxy)", "volume_trend_pct": "volume trend 20d/120d",
         "dist_52w_high_pct": "distance from 52-week high (high = near the high)", "ret21_pct": "1-month return"}
rows = []
for f, label in FEATS.items():
    dd = d.dropna(subset=[f]); dd["dec"] = np.minimum((dd[f] * 10).astype(int), 9) + 1
    g = dd.groupby("dec")
    for dec, sub in g:
        rows.append({"feature": label, "decile": dec, "stock-months": len(sub), **{f"{c} rate": sub.groupby("date")[c].mean().mean() for c in ("W50", "W100", "W200", "L50")}})
FT = pd.DataFrame(rows)
for c in ("W100", "W200", "L50"): FT[f"{c} lift"] = FT[f"{c} rate"] / base[c]
print(FT[FT["decile"].isin([1, 2, 5, 9, 10])].to_string(index=False))
for flag, lab in (("new55_high_last20", "made a new 55-day high in the last 20 sessions"),):
    for v, sub in d.groupby(d[flag] > 0):
        print(f"{lab} = {v}: n {len(sub):,}; W100 {sub.groupby('date')['W100'].mean().mean():.2%}; W200 {sub.groupby('date')['W200'].mean().mean():.2%}; L50 {sub.groupby('date')['L50'].mean().mean():.2%}")
d["price_bucket"] = pd.cut(d["price"], [5, 10, 20, 50, 1e9], labels=["$5-10", "$10-20", "$20-50", ">$50"], right=False)
print(d.groupby("price_bucket").apply(lambda s: pd.Series({"n": len(s), "W100": s.groupby("date")["W100"].mean().mean(), "L50": s.groupby("date")["L50"].mean().mean()})).to_string())
""")
code(r"""
fig, axs = plt.subplots(1, len(FEATS), figsize=(18, 3.6), sharey=True)
for a, (f, label) in zip(axs, FEATS.items()):
    t = FT[FT["feature"] == label]
    a.plot(t["decile"], t["W100 rate"] * 100, "g-o", ms=3, label="≥+100% in 250d"); a.plot(t["decile"], t["L50 rate"] * 100, "r-s", ms=3, label="≤−50% in 250d")
    a.axhline(base["W100"] * 100, color="g", ls=":", lw=.8); a.axhline(base["L50"] * 100, color="r", ls=":", lw=.8); a.set_title(label, fontsize=8); a.set_xlabel("within-month decile")
axs[0].set_ylabel("% of stock-months"); axs[0].legend(fontsize=7)
fig.suptitle("Winner and loser rates by within-month decile of each pre-move characteristic (dotted = universe base rate)"); fig.tight_layout(); save(fig, "fig06b_winner_characteristics")

hm = d.dropna(subset=["trend"]).groupby(["sector", "trend"]).apply(lambda s: pd.Series({"rate": s.groupby("date")["W100"].mean().mean(), "n": len(s)})).reset_index()
R = hm.pivot(index="sector", columns="trend", values="rate").reindex(columns=order); N = hm.pivot(index="sector", columns="trend", values="n").reindex(columns=order)
R = R.where(N >= 300); R = R.loc[R.mean(axis=1).sort_values().index]
fig, ax = plt.subplots(figsize=(8, 6)); im = ax.imshow(R.values * 100, cmap="viridis", aspect="auto")
ax.set_xticks(range(len(order))); ax.set_xticklabels(order); ax.set_yticks(range(len(R))); ax.set_yticklabels(R.index)
for i in range(R.shape[0]):
    for j in range(R.shape[1]):
        v, n = R.values[i, j], N.loc[R.index[i], order[j]]
        ax.text(j, i, "n<300" if np.isnan(v) else f"{v*100:.1f}%\n(n={int(n):,})", ha="center", va="center", fontsize=7, color="w" if (np.isnan(v) or v * 100 < np.nanmax(R.values * 100) * .6) else "k")
plt.colorbar(im, label="% of stocks gaining ≥100% in the next 250 sessions")
ax.set_title("Figure 4 — Winner frequency (≥+100%) by sector and trend regime\n(sector = today's label, NOT point-in-time; stock-months, not independent)"); fig.tight_layout(); save(fig, "fig04_winner_heatmap_sector_regime")
""")
code(r"""
# Case studies: a FIXED rule chosen before looking: for each start year 2018-2024, the single largest 250-session gain among eligible stock-months (one per company).
cases, used = [], set()
for y in range(2018, 2025):
    sub = d[(d["date"].dt.year == y) & ~d["symbol"].isin(used)].sort_values("r250", ascending=False)
    if len(sub): r = sub.iloc[0]; cases.append(r); used.add(r["symbol"])
fig, axs = plt.subplots(2, 4, figsize=(17, 7)); axs = axs.ravel()
for a, r in zip(axs, cases):
    s = C[r["symbol"]].dropna(); i = s.index.get_loc(r["date"]); w = s.iloc[max(0, i - 250): i + 251]
    a.plot(w.index, w / s.iloc[i], "k-", lw=.8); a.axvline(r["date"], color="b", ls="--", lw=.8); a.set_yscale("log")
    a.set_title(f"{r['symbol']} ({r.get('sector','?')}) start {r['date'].date()}: {r['r250']:+.0%}", fontsize=8)
    a.text(.02, .97, f"before the move:\n6m return pct {r['ret126_pct']:.0%}\nvolatility pct {r['vol60_pct']:.0%}\nsize pct {r['adv20_musd_pct']:.0%}\nfrom 52w high {r['dist_52w_high']:+.0%}\nregime {r['trend']}", transform=a.transAxes, va="top", fontsize=7, bbox=dict(fc="w", alpha=.7))
axs[-1].axis("off")
fig.suptitle("Figure 9 — Exceptional-winner case studies (rule: largest 250-session gain per start year; price relative to the start, log scale). Illustrations only."); fig.tight_layout(); save(fig, "fig09_winner_case_studies")
print(pd.DataFrame(cases)[["symbol", "date", "r250", "maxdd250", "ret126_pct", "vol60_pct", "adv20_musd_pct", "dist_52w_high", "trend", "sector"]].to_string(index=False))
""")
interp("winners")

# ---------------------------------------------------------------------------------------------------------------------------------------------------------------- 7. relationships
md(r"""
## 7. Relationships: what moves with what, and what comes before what

Each row is a Spearman rank correlation between two **monthly** series, with a moving-block bootstrap 95% interval. The block length covers the forward window, so overlapping windows are not treated as independent. **n_eff** is a rough count of non-overlapping observations, and it is small for 250-session windows.

**Contemporaneous** rows say what moves together. **Forward** rows say what came before what, which is still not causation, and with an n_eff this small it is rarely more than a hint.
""")
code(r"""
MM = pd.DataFrame(index=me)
MM["breadth"] = reg["breadth"].reindex(me); MM["vix"] = reg["vix"].reindex(me); MM["baa"] = reg["baa10y"].reindex(me)
MM["w100_next250"] = fp.dropna(subset=["r250"]).groupby("date")["r250"].apply(lambda s: (s >= 1).mean())
MM["w50_next250"] = fp.dropna(subset=["r250"]).groupby("date")["r250"].apply(lambda s: (s >= .5).mean())
MM["b55_density"] = dens["55d breakouts"].rolling(20, min_periods=10).mean().reindex(me)
MM["hi52_minus_lo52"] = (dens["52w breakouts"] - dens["52w breakdowns"]).rolling(20, min_periods=10).mean().reindex(me)
spxd = spx.reindex(C.index).ffill()
for h in (20, 60): MM[f"spx_next{h}"] = (spxd.shift(-h) / spxd - 1).reindex(me)
MM["spx_past20"] = (spxd / spxd.shift(20) - 1).reindex(me)
nas = A.fred("NASDAQCOM").reindex(C.index).ffill(); d10 = A.fred("DGS10").reindex(C.index).ffill()
mret = lambda s: (s.reindex(me) / s.reindex(me).shift(1) - 1)
MM["nasdaq_minus_spx_month"] = mret(nas) - mret(spxd); MM["d10y_month"] = d10.reindex(me) - d10.reindex(me).shift(1)
MM["nasdaq_minus_spx_next_month"] = MM["nasdaq_minus_spx_month"].shift(-1)
MM["dispersion_next60"] = fp.dropna(subset=["r60"]).groupby("date")["r60"].apply(lambda s: s.quantile(.75) - s.quantile(.25))
past60 = (C / C.shift(60) - 1).where(p.eligible)
MM["dispersion_past60"] = past60.reindex(me).apply(lambda r: r.quantile(.75) - r.quantile(.25), axis=1)
def mom_spread(x):
    x = x.dropna(subset=["ret126", "r60"]);
    if len(x) < 200: return np.nan
    q = x["ret126"].rank(pct=True); return x.loc[q > .9, "r60"].mean() - x.loc[q <= .1, "r60"].mean()
MM["momentum_spread_next60"] = fp.groupby("date").apply(mom_spread)
REL = [("Market breadth → share of stocks gaining ≥100% over next 250d", "breadth", "w100_next250", 12, "forward"),
       ("Market breadth → share gaining ≥50% over next 250d", "breadth", "w50_next250", 12, "forward"),
       ("VIX → share gaining ≥100% over next 250d", "vix", "w100_next250", 12, "forward"),
       ("55d breakout density → S&P 500 next 20d", "b55_density", "spx_next20", 1, "forward"),
       ("55d breakout density → S&P 500 next 60d", "b55_density", "spx_next60", 3, "forward"),
       ("55d breakout density ~ S&P 500 past 20d", "b55_density", "spx_past20", 1, "contemporaneous"),
       ("Net new 52w highs → S&P 500 next 60d", "hi52_minus_lo52", "spx_next60", 3, "forward"),
       ("10y yield change ~ NASDAQ minus S&P 500, same month", "d10y_month", "nasdaq_minus_spx_month", 1, "contemporaneous"),
       ("10y yield change → NASDAQ minus S&P 500, next month", "d10y_month", "nasdaq_minus_spx_next_month", 1, "forward"),
       ("Credit spread (Baa−10y) → dispersion of next-60d returns", "baa", "dispersion_next60", 3, "forward"),
       ("Credit spread ~ dispersion of past-60d returns", "baa", "dispersion_past60", 3, "contemporaneous"),
       ("VIX → momentum spread (top − bottom decile of 6m return), next 60d", "vix", "momentum_spread_next60", 3, "forward"),
       ("Market breadth → momentum spread next 60d", "breadth", "momentum_spread_next60", 3, "forward")]
RT = pd.DataFrame([{"relationship": nm, "type": typ, **A.spearman_block(MM[x], MM[y], b)} for nm, x, y, b, typ in REL])
RT["interval excludes 0"] = (RT["lo"] > 0) | (RT["hi"] < 0)
print(RT.to_string(index=False))
print("\nMomentum spread (next 60d, top minus bottom decile of 6-month return) by volatility regime at the start:")
MM["volreg"] = reg["volatility"].reindex(me)
print(MM.groupby("volreg")["momentum_spread_next60"].agg(["count", "mean", "median"]).to_string())
""")
code(r"""
fig, ax = plt.subplots(1, 2, figsize=(13, 4.5))
x = MM.dropna(subset=["breadth", "w100_next250"])
sc = ax[0].scatter(x["breadth"] * 100, x["w100_next250"] * 100, c=x.index.year, cmap="viridis", s=18)
plt.colorbar(sc, ax=ax[0], label="start year"); ax[0].set_xlabel("market breadth at month-end (% of eligible stocks above their 200d average)"); ax[0].set_ylabel("% of stocks gaining ≥100% over the next 250 sessions")
r = RT.iloc[0]; ax[0].set_title(f"Breadth vs subsequent winner frequency\nSpearman {r['rho']:+.2f} [{r['lo']:+.2f}, {r['hi']:+.2f}], {r['n_months']} months ≈ {r['n_eff']} independent years")
x2 = MM.dropna(subset=["vix", "momentum_spread_next60"]); ax[1].scatter(x2["vix"], x2["momentum_spread_next60"] * 100, s=18, c=x2.index.year, cmap="viridis"); ax[1].axhline(0, color="k", lw=.6)
ax[1].set_xlabel("VIX at month-end"); ax[1].set_ylabel("top − bottom 6m-return decile, next 60d return, %"); ax[1].set_title("Volatility vs momentum persistence")
fig.suptitle("Figure 7 — Market breadth vs subsequent winner frequency; volatility vs momentum persistence"); fig.tight_layout(); save(fig, "fig07_breadth_vs_winners")
""")
interp("relationships")

md("## 8. Conclusions")
interp("conclusions")

nb = nbf.v4.new_notebook()
nb["cells"] = CELLS
nb["metadata"] = {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}, "language_info": {"name": "python"}}

if __name__ == "__main__":
    out = os.path.join(HERE, "market_behavior_atlas.ipynb")
    ep = ExecutePreprocessor(timeout=1800, kernel_name="python3")
    try:
        ep.preprocess(nb, {"metadata": {"path": HERE}})
    finally:
        nbf.write(nb, out)                     # a failed cell still leaves every executed output on disk
        print("written", out)
