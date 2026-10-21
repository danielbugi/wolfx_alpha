"""Notebook 02 helpers: data loading, executable trade simulation, point-in-time features, statistics.

Pure functions over the read-only CSV exports in ./data (see queries/export_nb02.sh). Nothing here
connects to a database or writes anywhere except the local cache file data/nb02_panel.parquet.
"""
import os
import numpy as np
import pandas as pd

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HOLD = 20          # bars, as in the live plan
COST_BPS = 10      # round trip


# ----------------------------------------------------------------------------- loading
def load_prices():
    p = pd.read_csv(os.path.join(D, "nb02_prices_ohlc.csv.gz"), parse_dates=["date"])
    p = p.dropna(subset=["open", "high", "low", "close"])
    p = p[(p.close > 0) & (p.open > 0)]
    return p.sort_values(["symbol", "date"]).reset_index(drop=True)


def load_breakouts(start="2020-11-01"):
    b = pd.read_csv(os.path.join(D, "breakouts_v2.csv"), parse_dates=["date"])
    return b[b.date >= start].reset_index(drop=True)


def load_earnings():
    e = pd.read_csv(os.path.join(D, "nb02_earnings_calendar.csv.gz"), parse_dates=["report_date"])
    e["fetched_at"] = pd.to_datetime(e.fetched_at, utc=True)
    return e.drop_duplicates(["symbol", "report_date"]).sort_values(["symbol", "report_date"]).reset_index(drop=True)


# ----------------------------------------------------------------------------- executable outcome
def simulate(prices, brk, hold=HOLD, stop_atr=2.0, target_r=1.0, cost_bps=COST_BPS, entry="next_open", gap_fills=True, first_touch_any_target=False, require_full=True):
    """Executable version of the live plan, per breakout:
    entry = NEXT session's open (the signal is known only after the close);
    stop  = entry -/+ stop_atr x ATR (ATR = atr_pct x signal close, i.e. the signal-time ATR);
    target= entry +/- target_r x R;  R = stop distance.
    Gap-aware fills: an open beyond the stop fills at the open (loss can exceed 1R); an open beyond the
    target fills at the open; stop first when one bar touches both. Expiry: close of bar `hold`.
    Costs: cost_bps round trip, charged in R.
    Returns one row per breakout (aligned to brk.index) with exec_r, exec_r_net, bars, mae_r, exit, gap_stop."""
    out = np.full((len(brk), 6), np.nan)
    sym_groups = {s: g for s, g in prices.groupby("symbol", sort=False)}
    for s, bg in brk.groupby("symbol", sort=False):
        g = sym_groups.get(s)
        if g is None:
            continue
        dates = g.date.to_numpy(); o = g.open.to_numpy(float); h = g.high.to_numpy(float)
        l = g.low.to_numpy(float); c = g.close.to_numpy(float)
        pos = np.searchsorted(dates, bg.date.to_numpy())
        for row_i, (bi, i, d, atrp) in enumerate(zip(bg.index, pos, bg.direction.to_numpy(), bg.atr_pct.to_numpy(float))):
            if i >= len(dates) or dates[i] != bg.date.iloc[row_i] or not np.isfinite(atrp) or i + 1 >= len(dates):
                continue
            avail = min(hold, len(dates) - 1 - i)          # forward bars that exist
            if require_full and avail < hold:
                continue
            atr = atrp / 100.0 * c[i]
            e = o[i + 1] if entry == "next_open" else c[i]; R = stop_atr * atr
            first_gap_bar = i + 2 if entry == "next_open" else i + 1
            if R <= 0:
                continue
            stop = e - d * R; tgt = e + d * target_r * R
            mae = 0.0; res = None
            for k in range(i + 1, i + 1 + avail):
                oo, hh, ll, cc = o[k], h[k], l[k], c[k]
                if gap_fills and k >= first_gap_bar:  # gap at the open of a bar after entry
                    if d * (oo - stop) <= 0:
                        res = (d * (oo - e) / R, k - i, 1, 1); break
                    if d * (oo - tgt) >= 0:
                        res = (d * (oo - e) / R, k - i, 2, 0); break
                adverse = (e - ll) if d == 1 else (hh - e)
                mae = max(mae, adverse / R)
                hit_stop = (ll <= stop) if d == 1 else (hh >= stop)
                hit_tgt = (hh >= tgt) if d == 1 else (ll <= tgt)
                if hit_stop:
                    res = (-1.0, k - i, 1, 0); break
                if first_touch_any_target:
                    lv = [m for m in (1, 2, 3) if ((hh >= e + d * m * R) if d == 1 else (ll <= e + d * m * R))]
                    if lv:
                        res = (float(max(lv)), k - i, 2, 0); break
                elif hit_tgt:
                    res = (target_r, k - i, 2, 0); break
            if res is None:
                if avail < hold:
                    continue                                 # still open: not enough bars yet
                res = (d * (c[i + hold] - e) / R, hold, 3, 0)
            r, bars, ex, gap = res
            cost_r = cost_bps / 1e4 * e / R
            if gap:
                mae = max(mae, -r)
            out[bi] = (r, r - cost_r, bars, mae, ex, gap)
    df = pd.DataFrame(out, columns=["exec_r", "exec_r_net", "bars", "mae_r_exec", "exit", "gap_stop"], index=brk.index)
    df["exit"] = df["exit"].map({1: "stop", 2: "target", 3: "expiry"})
    return df


def forward_price_returns(prices, brk, horizons=(1, 5, 10, 20)):
    """Close-to-close directional price returns from the signal close (NOT executable outcomes)."""
    p = prices[["symbol", "date", "close"]].copy()
    g = p.groupby("symbol").close
    for h in horizons:
        p[f"ret_{h}"] = g.shift(-h) / p.close - 1
    m = brk[["symbol", "date", "direction"]].merge(p.drop(columns="close"), on=["symbol", "date"], how="left")
    for h in horizons:
        m[f"dret_{h}"] = m[f"ret_{h}"] * m.direction * 100
    m.index = brk.index
    return m[[f"dret_{h}" for h in horizons]]


# ----------------------------------------------------------------------------- earnings features
def session_index(prices):
    """Market session calendar = dates on which at least 50% of the median symbol count traded."""
    n = prices.groupby("date").size()
    return pd.DatetimeIndex(n[n >= 0.5 * n.median()].index).sort_values()


def earnings_features(brk, earn, sessions):
    """Per breakout (signal date t):
    next_rep_sessions_retro : sessions from t to the next report date >= t (RETROSPECTIVE: the date as finally observed;
                              the table holds no record of what was scheduled/known at t).
    last_rep_sessions       : sessions since the most recent report date < t (point-in-time safe: a past event).
    next_rep_sessions_proj  : PIT-safe projection = (most recent report date < t) + 91 calendar days, i.e. it uses only
                              information available at t (prior report dates).
    covered                 : the symbol has earnings history spanning t (first report <= t - 120d and a report after t)."""
    sess = sessions.to_numpy()
    sidx = lambda d: np.searchsorted(sess, d)  # index of first session >= d
    res = []
    eg = {s: g.report_date.to_numpy() for s, g in earn.groupby("symbol")}
    for s, bg in brk.groupby("symbol", sort=False):
        rd = eg.get(s)
        t = bg.date.to_numpy()
        if rd is None or len(rd) == 0:
            res.append(pd.DataFrame({"next_rep_sessions_retro": np.nan, "last_rep_sessions": np.nan, "next_rep_sessions_proj": np.nan, "covered": False}, index=bg.index)); continue
        j = np.searchsorted(rd, t, side="left")        # first report >= t
        nxt = np.where(j < len(rd), rd[np.minimum(j, len(rd) - 1)], np.datetime64("NaT"))
        prv = np.where(j > 0, rd[np.maximum(j - 1, 0)], np.datetime64("NaT"))
        ti = sidx(t)
        nx = np.where(pd.isna(nxt), np.nan, sidx(nxt) - ti).astype(float)
        pv = np.where(pd.isna(prv), np.nan, ti - sidx(prv)).astype(float)
        proj = prv + np.timedelta64(91, "D")
        pj = np.where(pd.isna(prv), np.nan, sidx(proj) - ti).astype(float)
        covered = (~pd.isna(prv)) & (~pd.isna(nxt)) & ((t - rd[0]) >= np.timedelta64(120, "D"))
        res.append(pd.DataFrame({"next_rep_sessions_retro": nx, "last_rep_sessions": pv, "next_rep_sessions_proj": pj, "covered": covered}, index=bg.index))
    return pd.concat(res).reindex(brk.index)


# ----------------------------------------------------------------------------- regime features (PIT, from the price panel)
def market_regime(prices, sessions):
    """Equal-weight universe index and breadth, using closes up to and including t only."""
    p = prices[prices.date.isin(sessions)][["symbol", "date", "close"]].copy()
    p["r1"] = p.groupby("symbol").close.pct_change().clip(-0.5, 0.5)
    p["sma50"] = p.groupby("symbol").close.transform(lambda s: s.rolling(50, min_periods=50).mean())
    p["above50"] = (p.close > p.sma50).where(p.sma50.notna())
    daily = p.groupby("date").agg(ew_r=("r1", "mean"), breadth50=("above50", "mean"), n=("r1", "count"))
    daily["ew_idx"] = (1 + daily.ew_r.fillna(0)).cumprod()
    daily["ew_sma200"] = daily.ew_idx.rolling(200, min_periods=200).mean()
    daily["ew_trend_up"] = (daily.ew_idx > daily.ew_sma200).where(daily.ew_sma200.notna())
    daily["ew_ret20"] = daily.ew_idx.pct_change(20) * 100
    daily["ew_vol20"] = daily.ew_r.rolling(20).std() * np.sqrt(252) * 100
    return daily


def sector_strength(prices, sessions, sector_map, lookback=20):
    """Sector 20-session return minus universe return, equal weight, PIT in PRICES but with MODERN (current) sector
    membership projected backwards -> biased, labelled 'modern-membership'."""
    p = prices[prices.date.isin(sessions)][["symbol", "date", "close"]].copy()
    p["ret"] = p.groupby("symbol").close.pct_change(lookback).clip(-0.9, 3) * 100
    p["sector"] = p.symbol.map(sector_map)
    p = p.dropna(subset=["ret", "sector"])
    uni = p.groupby("date").ret.mean().rename("uni_ret")
    sec = p.groupby(["date", "sector"]).ret.agg(["mean", "count"]).reset_index().rename(columns={"mean": "sec_ret", "count": "sec_n"})
    sec = sec.merge(uni, on="date")
    sec["sec_rel"] = sec.sec_ret - sec.uni_ret
    stock = p[["symbol", "date", "ret", "sector"]].merge(sec[["date", "sector", "sec_ret"]], on=["date", "sector"])
    stock["stock_vs_sector"] = stock.ret - stock.sec_ret
    return sec, stock[["symbol", "date", "stock_vs_sector"]]


# ----------------------------------------------------------------------------- statistics
_rng = np.random.default_rng(11)


def _day_sums(x, days):
    df = pd.DataFrame({"x": x, "d": days}).dropna()
    g = df.groupby("d").x.agg(["sum", "count"])
    return g


def boot_mean(x, days, n=2000):
    """Mean with a 95% CI from a day-cluster bootstrap."""
    g = _day_sums(x, days)
    if len(g) < 3:
        return np.nan, np.nan, np.nan, int(g["count"].sum())
    s = g["sum"].to_numpy(); c = g["count"].to_numpy()
    idx = _rng.integers(0, len(g), (n, len(g)))
    b = s[idx].sum(1) / c[idx].sum(1)
    return s.sum() / c.sum(), *np.percentile(b, [2.5, 97.5]), int(c.sum())


def boot_diff(x, mask, days, n=2000):
    """Difference in means (mask True minus False), day-cluster bootstrap: (diff, lo, hi, p_two_sided, n_true, n_false).
    Days are resampled jointly so the comparison keeps the same market days on both sides."""
    df = pd.DataFrame({"x": x, "m": mask.astype(bool), "d": days}).dropna(subset=["x"])
    a = df[df.m].groupby("d").x.agg(["sum", "count"]); b = df[~df.m].groupby("d").x.agg(["sum", "count"])
    allg = a.join(b, how="outer", lsuffix="_a", rsuffix="_b").fillna(0)
    if (allg.count_a > 0).sum() < 3 or (allg.count_b > 0).sum() < 3:
        return (np.nan,) * 4 + (int(allg.count_a.sum()), int(allg.count_b.sum()))
    sa, ca, sb, cb = (allg[k].to_numpy() for k in ["sum_a", "count_a", "sum_b", "count_b"])
    idx = _rng.integers(0, len(allg), (n, len(allg)))
    with np.errstate(invalid="ignore", divide="ignore"):
        bd = sa[idx].sum(1) / ca[idx].sum(1) - sb[idx].sum(1) / cb[idx].sum(1)
    bd = bd[np.isfinite(bd)]
    diff = sa.sum() / ca.sum() - sb.sum() / cb.sum()
    p = min(1.0, 2 * min((bd <= 0).mean(), (bd >= 0).mean()))
    p = max(p, 1.0 / len(bd))
    lo, hi = np.percentile(bd, [2.5, 97.5])
    return diff, lo, hi, p, int(ca.sum()), int(cb.sum())


def holm(pvals):
    """Holm-Bonferroni adjusted p-values (same order as input)."""
    p = np.asarray(pvals, float); order = np.argsort(p); m = len(p)
    adj = np.empty(m); run = 0.0
    for rank, i in enumerate(order):
        run = max(run, (m - rank) * p[i]); adj[i] = min(1.0, run)
    return adj


def tail_stats(x):
    x = pd.Series(x).dropna()
    q5 = x.quantile(.05)
    return {"n": len(x), "mean": x.mean(), "median": x.median(), "std": x.std(), "p5": q5, "es5": x[x <= q5].mean(),
            "share_le_-1.5R": (x <= -1.5).mean() * 100, "win%": (x > 0).mean() * 100}
