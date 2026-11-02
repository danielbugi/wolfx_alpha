"""Computation for the Market Behavior Atlas notebook. Read-only inputs, no database, no network (macro data comes from data/macro/, written by fetch_macro.py).

Conventions (fixed before any result was looked at)
  * Prices: the hashed right-tail extract (stock_prices, vendor back-adjusted; see research/right_tail/extract.py). Wide float32 frames, dates x symbols.
  * Eligible on date t: close >= $5, 20-session mean dollar volume >= $2M, >= 252 earlier sessions in the extract, no non-positive print.
  * A forward window (t, t+h] or a lookback window that touches a flagged price discontinuity is excluded (counted).
  * Observations for cross-sectional statistics: the last session of each month (one row per stock per month); every month gets equal weight in averages; uncertainty
    is a moving-block bootstrap over months whose block length covers the horizon, because overlapping windows are not independent.
  * Every regime label at t uses data up to and including t only.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
MACRO = os.path.join(HERE, "data", "macro")
HORIZONS = (20, 60, 120, 250)
GAINS = (0.25, 0.5, 1.0, 2.0)
LOSSES = (0.25, 0.5, 0.75)
MIN_CLOSE, MIN_ADV, MIN_HIST = 5.0, 2_000_000.0, 252
MIN_SYMBOLS_PER_DATE = 500


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ prices
def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Panel:
    O: pd.DataFrame
    H: pd.DataFrame
    L: pd.DataFrame
    C: pd.DataFrame
    V: pd.DataFrame
    disc: pd.DataFrame          # bool: flagged discontinuity (or a non-positive print) on that date
    eligible: pd.DataFrame      # bool
    meta: Mapping


def load_panel(extract_dir: str) -> Panel:
    meta = json.load(open(os.path.join(extract_dir, "extract_meta.json")))
    for k in ("prices", "discontinuities"):
        if _sha(os.path.join(extract_dir, f"{k}.csv.gz")) != meta[f"{k}_sha256"]:
            raise ValueError(f"{k}.csv.gz does not match extract_meta.json; refused")
    df = pd.read_csv(os.path.join(extract_dir, "prices.csv.gz"), dtype={"symbol": str}, parse_dates=["date"])
    df = df.dropna(subset=["open", "high", "low", "close"])
    # A calendar date with prices for only a handful of symbols is a vendor glitch (e.g. one symbol priced on a market holiday). Kept, it would put a row of NaNs into
    # every rolling window of every other stock (a 200-session mean then breaks for 200 sessions). Dropped and recorded.
    per_date = df.groupby("date").size()
    stray = per_date[per_date < MIN_SYMBOLS_PER_DATE]
    df = df[~df["date"].isin(stray.index)]
    meta = {**meta, "stray_dates_dropped": {d.date().isoformat(): int(n) for d, n in stray.items()}}
    wide ={k: df.pivot(index="date", columns="symbol", values=k).astype("float32") for k in ("open", "high", "low", "close", "volume")}
    C = wide["close"]
    bad = (wide["open"] <= 0) | (wide["high"] <= 0) | (wide["low"] <= 0) | (C <= 0) | (wide["high"] < wide["low"])
    d = pd.read_csv(os.path.join(extract_dir, "discontinuities.csv.gz"), dtype={"symbol": str}, parse_dates=["date"])
    disc = bad.copy()
    for sym, dt in zip(d["symbol"], d["date"]):
        if sym in disc.columns:
            i = disc.index.searchsorted(dt)
            if i < len(disc.index):
                disc.iat[i, disc.columns.get_loc(sym)] = True
    for k in wide:
        wide[k] = wide[k].mask(bad)
    hist = C.notna().cumsum()
    adv = (C * wide["volume"]).rolling(20, min_periods=15).mean()
    eligible = (C >= MIN_CLOSE) & (adv >= MIN_ADV) & (hist > MIN_HIST)
    return Panel(wide["open"], wide["high"], wide["low"], C, wide["volume"], disc, eligible, meta)


def window_clean(disc: pd.DataFrame, back: int, fwd: int) -> pd.DataFrame:
    """True where no discontinuity lies in (t-back, t+fwd]."""
    cd = disc.astype("int32").cumsum()
    hi = cd.shift(-fwd) if fwd else cd
    lo = cd.shift(back) if back else cd
    return (hi - lo.fillna(0)) == 0


def month_ends(index: pd.DatetimeIndex, start: str) -> pd.DatetimeIndex:
    s = pd.Series(index, index=index)
    me = s.groupby([index.year, index.month]).max()
    d = pd.DatetimeIndex(me.values)
    return d[d >= pd.Timestamp(start)]


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ macro + market series
def fred(sid: str) -> pd.Series:
    s = pd.read_csv(os.path.join(MACRO, f"fred_{sid}.csv"), parse_dates=[0], index_col=0, na_values=["."]).iloc[:, 0]
    return s.dropna().astype(float)


def ew_index(p: Panel) -> pd.Series:
    """Equal-weight universe index: cumulated daily MEDIAN return of names eligible the previous day (survivor universe: drifts upward; never used as the regime anchor)."""
    r = p.C / p.C.shift(1) - 1
    r = r.where(p.eligible.shift(1, fill_value=False) & ~p.disc)
    return (1 + r.median(axis=1).fillna(0)).cumprod()


@dataclass
class Regimes:
    daily: pd.DataFrame          # one row per trading date of the panel


def build_regimes(p: Panel, sma: int = 200, slope: int = 20) -> pd.DataFrame:
    """Point-in-time regime labels per trading date. Trend/momentum/drawdown from the S&P 500 (FRED; not a survivor index), volatility from VIX, breadth from the
    eligible universe, financial conditions from the Baa-Treasury spread versus its own trailing year (and NFCI, revised, as a cross-check)."""
    idx = p.C.index
    spx = fred("SP500").reindex(idx).ffill(limit=3)
    m = spx.rolling(sma, min_periods=sma).mean()
    up = m > m.shift(slope)
    above = spx > m
    trend = pd.Series(np.where(above & up, "Bull", np.where(above & ~up, "Recovery", np.where(~above & up, "Weakening", "Bear"))), index=idx).where(m.notna())
    r126 = spx / spx.shift(126) - 1
    mom = pd.Series(np.where(r126 > 0.10, "Strong up", np.where(r126 < -0.10, "Down", "Sideways")), index=idx).where(r126.notna())
    dd = spx / spx.rolling(252, min_periods=150).max() - 1
    vix = fred("VIXCLS").reindex(idx).ffill(limit=3)
    vol = pd.Series(np.where(vix < 15, "Low vol (<15)", np.where(vix <= 25, "Mid vol (15-25)", "High vol (>25)")), index=idx).where(vix.notna())
    s200 = p.C.rolling(200, min_periods=200).mean()
    abv = (p.C > s200).where(p.eligible & s200.notna())
    breadth = abv.mean(axis=1)
    br = pd.Series(np.where(breadth < 0.4, "Weak breadth (<40%)", np.where(breadth <= 0.6, "Mid breadth", "Strong breadth (>60%)")), index=idx).where(breadth.notna() & (abv.notna().sum(axis=1) > 200))
    baa = fred("BAA10Y").reindex(idx).ffill(limit=5)
    baa_med = baa.rolling(252, min_periods=200).median()
    fin = pd.Series(np.where(baa > baa_med, "Tighter (spread above 1y median)", "Looser (spread below 1y median)"), index=idx).where(baa_med.notna())
    nfci = fred("NFCI").reindex(idx).ffill(limit=10)
    return pd.DataFrame({"spx": spx, "spx_sma": m, "trend": trend, "spx_r126": r126, "momentum": mom, "spx_drawdown": dd, "vix": vix, "volatility": vol,
                         "breadth": breadth, "breadth_regime": br, "baa10y": baa, "fin_conditions": fin, "nfci_revised": nfci, "ew_index": ew_index(p)})


def regime_agreement(p: Panel, alts: Sequence[Tuple[int, int]] = ((150, 10), (250, 40))) -> pd.DataFrame:
    base = build_regimes(p)["trend"]
    rows = []
    for s, sl in alts:
        alt = build_regimes(p, s, sl)["trend"]
        ok = base.notna() & alt.notna()
        rows.append({"alternative": f"SMA{s}, slope over {sl} sessions", "days_compared": int(ok.sum()), "same_label_share": round(float((base[ok] == alt[ok]).mean()), 3)})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ forward outcomes (monthly cross-sections)
def forward_panel(p: Panel, start: str = "2017-09-01") -> pd.DataFrame:
    """Long table: one row per (month-end t, eligible stock). Forward close-to-close returns, max drawdown and max gain inside the 250-session window, and
    point-in-time features at t. A horizon is NaN when the stock has no close at t+h (immature at the data end, or the series ended) or the window touches a discontinuity."""
    C, idx = p.C, p.C.index
    dates = month_ends(idx, start)
    clean_back = window_clean(p.disc, 252, 0)
    feats = point_features(p)
    out = []
    Cv = C.values
    for t in dates:
        i = idx.get_loc(t)
        el = p.eligible.iloc[i].values & clean_back.iloc[i].values
        if el.sum() == 0:
            continue
        cols = np.flatnonzero(el)
        c0 = Cv[i, cols].astype(float)
        row = {"date": np.repeat(t, len(cols)), "symbol": C.columns[cols]}
        for h in HORIZONS:
            if i + h < len(idx):
                ch = Cv[i + h, cols].astype(float)
                ok = window_clean(p.disc.iloc[i:i + h + 1, cols], 0, h).iloc[0].values
                row[f"r{h}"] = np.where(ok, ch / c0 - 1, np.nan)
            else:
                row[f"r{h}"] = np.full(len(cols), np.nan)
        if i + 250 < len(idx):
            W = Cv[i:i + 251, cols].astype(float)
            runmax = np.fmax.accumulate(np.where(np.isnan(W), -np.inf, W), axis=0)
            dd = np.nanmin(np.where(np.isnan(W), np.nan, W / runmax - 1), axis=0)
            mg = np.nanmax(W[1:], axis=0) / c0 - 1
            okw = ~np.isnan(row["r250"])
            row["maxdd250"], row["maxgain250"] = np.where(okw, dd, np.nan), np.where(okw, mg, np.nan)
        else:
            row["maxdd250"] = row["maxgain250"] = np.full(len(cols), np.nan)
        for k, f in feats.items():
            row[k] = f.iloc[i, cols].values.astype(float)
        out.append(pd.DataFrame(row))
    df = pd.concat(out, ignore_index=True)
    # within-date percentile ranks of the features (removes date-level effects from cross-sectional comparisons)
    for k in feats:
        df[f"{k}_pct"] = df.groupby("date")[k].rank(pct=True)
    return df


def point_features(p: Panel) -> Dict[str, pd.DataFrame]:
    """Signal-time characteristics, bars <= t only."""
    C, H, L, V = p.C, p.H, p.L, p.V
    r = C / C.shift(1) - 1
    hh252 = H.rolling(252, min_periods=200).max()
    return {
        "ret126": C / C.shift(126) - 1,
        "ret21": C / C.shift(21) - 1,
        "vol60": r.rolling(60, min_periods=50).std() * np.sqrt(252),
        "adv20_musd": (C * V).rolling(20, min_periods=15).mean() / 1e6,
        "volume_trend": V.rolling(20, min_periods=15).mean() / V.rolling(120, min_periods=100).mean(),
        "dist_52w_high": C / hh252 - 1,
        "new55_high_last20": (C > H.rolling(55, min_periods=55).max().shift(1)).rolling(20, min_periods=1).max(),
        "price": C,
    }


def attach(df: pd.DataFrame, reg: pd.DataFrame, sectors: Optional[pd.Series], cols=("trend", "momentum", "volatility", "breadth_regime", "fin_conditions", "breadth", "vix", "baa10y")) -> pd.DataFrame:
    out = df.join(reg[list(cols)], on="date")
    if sectors is not None:
        out["sector"] = out["symbol"].map(sectors).fillna("Unknown")
    ew = {h: reg["ew_index"].shift(-h) / reg["ew_index"] - 1 for h in HORIZONS}
    for h in HORIZONS:
        out[f"x{h}"] = out[f"r{h}"] - out["date"].map(ew[h])                          # excess over the equal-weight universe median path
        if sectors is not None:
            out[f"xs{h}"] = out[f"r{h}"] - out.groupby(["date", "sector"])[f"r{h}"].transform("median")
    return out


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ statistics
def block_bootstrap(values_by_month: pd.Series, block: int, n_boot: int = 1000, seed: int = 20261102) -> Tuple[float, float, float]:
    """Mean of monthly values with a moving-block bootstrap 95% interval (block = months a window spans), for overlapping-window statistics."""
    v = values_by_month.dropna().values
    if len(v) < max(3, 2 * block):                                  # fewer than two independent blocks: an interval would be fiction
        return (float(np.mean(v)) if len(v) else float("nan"), float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    b = max(1, min(block, len(v)))
    starts = np.arange(len(v) - b + 1)
    k = int(np.ceil(len(v) / b))
    means = np.empty(n_boot)
    for j in range(n_boot):
        s = rng.choice(starts, k)
        means[j] = np.concatenate([v[x:x + b] for x in s])[:len(v)].mean()
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(v.mean()), float(lo), float(hi)


def spearman_block(x: pd.Series, y: pd.Series, block: int, n_boot: int = 1000, seed: int = 11) -> Dict[str, float]:
    """Spearman rank correlation of two monthly series with a moving-block bootstrap 95% interval (block = months one observation's window spans).
    `n_eff` = months / block, a rough count of non-overlapping observations."""
    d = pd.concat([x, y], axis=1).dropna()
    n = len(d)
    if n < 12:
        return {"rho": float("nan"), "lo": float("nan"), "hi": float("nan"), "n_months": n, "n_eff": n / max(block, 1)}
    a, b = d.iloc[:, 0].rank().values, d.iloc[:, 1].rank().values
    rho = float(np.corrcoef(a, b)[0, 1])
    rng = np.random.default_rng(seed)
    blk = max(1, min(block, n))
    starts = np.arange(n - blk + 1)
    k = int(np.ceil(n / blk))
    xs, ys = d.iloc[:, 0].values, d.iloc[:, 1].values
    rs = []
    for _ in range(n_boot):
        ix = np.concatenate([np.arange(s, s + blk) for s in rng.choice(starts, k)])[:n]
        xa, ya = pd.Series(xs[ix]).rank().values, pd.Series(ys[ix]).rank().values
        if xa.std() > 0 and ya.std() > 0:
            rs.append(np.corrcoef(xa, ya)[0, 1])
    lo, hi = np.percentile(rs, [2.5, 97.5])
    return {"rho": round(rho, 3), "lo": round(float(lo), 3), "hi": round(float(hi), 3), "n_months": n, "n_eff": round(n / blk, 1)}


def spells(labels: pd.Series, value) -> int:
    """Number of separate runs of `value` in a date-ordered label series (independent episodes, roughly)."""
    s = (labels.sort_index() == value).astype(int)
    return int(((s == 1) & (s.shift(1, fill_value=0) == 0)).sum())


def by_regime(df: pd.DataFrame, regime: str, h: int = 250) -> pd.DataFrame:
    """Per regime: month-equal-weighted cross-sectional statistics of the h-session forward return, with block-bootstrap intervals for the key rates."""
    col = f"r{h}"
    d = df.dropna(subset=[col, regime])
    block = max(1, round(h / 21))
    rows = []
    for g, sub in d.groupby(regime):
        per_month = sub.groupby("date")
        def m(fn):
            return per_month.apply(fn)
        rate = lambda thr: m(lambda x: float((x[col] >= thr).mean()))
        rows.append({
            regime: g, "months": int(sub["date"].nunique()), "spells": spells(d.drop_duplicates("date").set_index("date")[regime], g), "stock_obs": len(sub), "unique_stocks": int(sub["symbol"].nunique()),
            "mean_stocks_per_month": round(len(sub) / sub["date"].nunique(), 0),
            "median_return": round(float(m(lambda x: x[col].median()).mean()), 4), "mean_return": round(float(m(lambda x: x[col].mean()).mean()), 4),
            "dispersion_iqr": round(float(m(lambda x: x[col].quantile(0.75) - x[col].quantile(0.25)).mean()), 4),
            "skew": round(float(m(lambda x: x[col].skew()).mean()), 2),
            **{f"gain>={int(g_ * 100)}%": round(float(rate(g_).mean()), 4) for g_ in GAINS},
            **{f"loss>={int(l_ * 100)}%": round(float(m(lambda x, l_=l_: float((x[col] <= -l_).mean())).mean()), 4) for l_ in LOSSES},
            "share>=100% CI": tuple(round(x, 4) for x in block_bootstrap(rate(1.0), block)[1:]),
            "median_maxdd250": round(float(m(lambda x: x["maxdd250"].median()).mean()), 4) if h == 250 else None,
        })
    return pd.DataFrame(rows).set_index(regime)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ breakouts / breakdowns (daily events)
def breakout_events(p: Panel, n: int, kind: str) -> pd.DataFrame:
    """Fresh breakouts (close above the prior n-session high; first close above) or breakdowns (close below the prior n-session low), eligible names, clean lookback.
    Outcomes: failed = closes back through the level within 10 sessions; sustained/continued and recovered are judged 60 sessions later; returns relative to the equal-weight median."""
    C, idx = p.C, p.C.index
    if kind == "up":
        lvl = p.H.rolling(n, min_periods=n).max().shift(1)
        ev = (C > lvl) & (C.shift(1) <= lvl.shift(1))
        back10 = C[::-1].rolling(10, min_periods=1).min()[::-1].shift(-1) < lvl
    else:
        lvl = p.L.rolling(n, min_periods=n).min().shift(1)
        ev = (C < lvl) & (C.shift(1) >= lvl.shift(1))
        back10 = C[::-1].rolling(10, min_periods=1).max()[::-1].shift(-1) > lvl
    ev &= p.eligible & window_clean(p.disc, n + 1, 0)
    clean_f = window_clean(p.disc, 0, 120)
    ewi = ew_index(p)
    st = ev.stack()
    st = st[st]
    di = st.index.get_level_values(0)
    sy = st.index.get_level_values(1)
    ii = idx.get_indexer(di)
    jj = C.columns.get_indexer(sy)
    Cv, Lv = C.values, lvl.values
    out = pd.DataFrame({"date": di, "symbol": sy, "level": Lv[ii, jj], "close": Cv[ii, jj], "failed10": back10.values[ii, jj].astype(bool)})
    for h in (20, 60, 120):
        ok = (ii + h < len(idx))
        ch = np.where(ok, Cv[np.minimum(ii + h, len(idx) - 1), jj], np.nan)
        cl = clean_f.values[ii, jj]
        r = np.where(ok & cl, ch / out["close"].values - 1, np.nan)
        er = ewi.shift(-h).values[ii] / ewi.values[ii] - 1
        out[f"r{h}"], out[f"x{h}"] = r, r - er
        if h == 60:
            above = ch > out["level"].values
            out["sustained60"] = np.where(np.isnan(r), np.nan, (above if kind == "up" else ~above) & ((r - er) > 0 if kind == "up" else (r - er) < 0))
            out["recovered60"] = np.where(np.isnan(r), np.nan, (~above) if kind == "up" else above)
    out["kind"], out["n"] = kind, n
    return out


def density(p: Panel, events: pd.DataFrame) -> pd.Series:
    """Events per 100 eligible stocks per session."""
    cnt = events.groupby("date").size().reindex(p.C.index, fill_value=0)
    return 100 * cnt / p.eligible.sum(axis=1).replace(0, np.nan)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ event study
def event_returns(series: pd.Series, events: pd.DatetimeIndex, ks=(0, 5, 20, 60, 120)) -> pd.DataFrame:
    """Cumulative return from the close BEFORE the event session to the close k sessions after it (k=0: the event session itself). Events on a non-trading day are moved
    to the next session (flagged). NaN when the window is not complete."""
    s = series.dropna()
    ix = s.index
    rows = []
    for e in events:
        i = ix.searchsorted(e)
        if i <= 0 or i >= len(ix):
            continue
        r = {"event": e, "session": ix[i], "moved": ix[i] != e}
        for k in ks:
            r[f"k{k}"] = float(s.iloc[i + k] / s.iloc[i - 1] - 1) if i + k < len(ix) else np.nan
        rows.append(r)
    return pd.DataFrame(rows)


def unconditional(series: pd.Series, ks=(0, 5, 20, 60, 120)) -> Dict[int, float]:
    s = series.dropna()
    return {k: float((s.shift(-k) / s.shift(1) - 1).mean()) for k in ks}


def diff_ci(a: np.ndarray, b_mean: float, n_boot: int = 2000, seed: int = 7) -> Tuple[float, float, float]:
    """Mean of event windows minus the unconditional mean, iid bootstrap over events (events are about a month apart; long windows overlap, so long-horizon
    intervals are too narrow and are flagged in the notebook)."""
    a = a[~np.isnan(a)]
    if len(a) < 5:
        return (float("nan"),) * 3
    rng = np.random.default_rng(seed)
    bs = np.array([rng.choice(a, len(a)).mean() for _ in range(n_boot)]) - b_mean
    return float(a.mean() - b_mean), float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


def releases_from_cache(kind: str) -> pd.DataFrame:
    """First-release values per release, rebuilt from the ALFRED vintage cache: CPI m/m (headline, core) and the previous month's m/m in the SAME vintage; payroll change,
    the mean of the three previous changes in the same vintage, and the unemployment rate and its change. Naive comparisons only: no consensus forecasts exist here."""
    rel = pd.read_csv(os.path.join(MACRO, f"releases_{kind}.csv"), parse_dates=["release_date", "ref_month"]).dropna(subset=["release_date"])
    cache = json.load(open(os.path.join(MACRO, "alfred_cache.json")))
    idx: Dict[Tuple[str, str], Dict[str, Optional[float]]] = {}
    for key, v in cache.items():
        sid, d, cosd = key.split("|")
        cur = idx.get((sid, d))
        if cur is None or len(v) > len(cur):
            idx[(sid, d)] = v
    def series_at(sid, d):
        v = idx.get((sid, d.date().isoformat()), {})
        return pd.Series({pd.Timestamp(k): x for k, x in v.items() if x is not None}).sort_index()
    rows = []
    for _, r in rel.iterrows():
        d, m = r["release_date"], r["ref_month"]
        row = {"release_date": d, "ref_month": m}
        if kind == "cpi":
            for sid, name in (("CPIAUCSL", "headline"), ("CPILFESL", "core")):
                s = series_at(sid, d)
                if m in s.index and len(s.loc[:m]) >= 3:
                    a = s.loc[:m]
                    row[f"{name}_mom"] = float(a.iloc[-1] / a.iloc[-2] - 1) * 100
                    row[f"{name}_mom_prev"] = float(a.iloc[-2] / a.iloc[-3] - 1) * 100
                    row[f"{name}_yoy"] = float(a.iloc[-1] / a.iloc[-13] - 1) * 100 if len(a) >= 13 else np.nan
        else:
            s = series_at("PAYEMS", d)
            u = series_at("UNRATE", d)
            if m in s.index and len(s.loc[:m]) >= 5:
                a = s.loc[:m]
                ch = a.diff()
                row["payrolls_change_k"] = float(ch.iloc[-1])
                row["payrolls_prev3_mean_k"] = float(ch.iloc[-4:-1].mean())
            if m in u.index and len(u.loc[:m]) >= 2:
                b = u.loc[:m]
                row["unrate"], row["unrate_change"] = float(b.iloc[-1]), float(b.iloc[-1] - b.iloc[-2])
        rows.append(row)
    return pd.DataFrame(rows)


def fomc_decisions() -> pd.DataFrame:
    pr = pd.read_csv(os.path.join(MACRO, "fomc_press_releases.csv"), parse_dates=["date"])
    st = pr[pr["is_statement"]].copy()
    up = fred("DFEDTARU")
    def tgt(d):
        s = up.loc[:d]
        return float(s.iloc[-1]) if len(s) else np.nan
    st["upper_before"] = [tgt(d - pd.Timedelta(days=1)) for d in st["date"]]
    st["upper_after"] = [tgt(d + pd.Timedelta(days=3)) for d in st["date"]]
    st["change_bp"] = ((st["upper_after"] - st["upper_before"]) * 100).round()
    st["decision"] = np.where(st["change_bp"] > 0, "Hike", np.where(st["change_bp"] < 0, "Cut", "Hold"))
    return st.reset_index(drop=True)
