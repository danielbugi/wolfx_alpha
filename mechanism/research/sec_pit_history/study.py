"""EXPLORATORY study: SEC point-in-time fundamental inflections vs future stock performance and future big winners. Descriptive, hypothesis-generating; nothing here is a validated edge.

    python mechanism/research/sec_pit_history/study.py --prices <dir with prices.csv.gz, discontinuities.csv.gz, extract_meta.json>  > study_output.txt

Design (fixed BEFORE any outcome was computed; nothing is selected from the results):
  * Events: the reconstructed inflection_v2_sec_pit base events (reconstruct.py). Unit of analysis: the COMPANY-QUARTER (all event types of one quarter share one public date); event type and
    severity are secondary cuts. Public date = SEC acceptance date (US Eastern).
  * HOLDOUT: company-quarters public 2023-01-01 .. 2025-03-31 are the reserved confirmation window. They are dropped BEFORE any price is read (asserted); no outcome of them is computed, printed
    or stored. Exploration periods: pre-holdout (public before 2023-01-01) and post-holdout (public from 2025-04-01, already inspected by the yfinance studies, short horizons only).
  * Timeliness: a company-quarter whose first public filing came more than 120 days after quarter end (a quarter that first appears as a comparative in a later filing, e.g. after an IPO
    or a late registration) is not news and is dropped (162 of 31,039 events; decided from the reconstruction's lag distribution, before any outcome).
  * Entry: the OPEN of the first session strictly after the public date (67% of filings are accepted after the close). Return over H sessions: close of session entry+H-1 / entry open - 1,
    H in 20, 60, 120, 250. Windows touching a recorded price discontinuity (split/data jump) in [entry-61, entry+H-1] are dropped.
  * Eligibility (as in the Market Behavior Atlas): close >= $5, 20-session mean dollar volume >= $2M, more than 252 sessions of history, all measured on the session BEFORE entry.
  * Controls: the same entry session, the SEC-covered eligible pool (same population the events come from), matched on within-date 60-session volatility decile x dollar-volume tercile
    (volatility decile alone when the cell has fewer than 10 names). Excess = event return - cell mean. Big winners at 250 sessions: doubled (>= +100%), halved (<= -50%), and top decile of
    the same-date pool; event rate vs the matched cells' expected rate.
  * Inference: monthly means, 3-month moving-block bootstrap (2,000 draws, seed 7). Reported: events, companies, months; with and without 2020 entries; leave-one-year-out range.
  * Price state (Atlas finding: price state cuts losers more than it adds winners): inside events, within-date tercile of distance from the 52-week high and of the 6-month return.
  * Reverse view: month-end snapshots (pre-holdout only), SEC-covered eligible stocks; does a base event public in the previous 91 days change the 250-session doubling/halving rate,
    within volatility decile?
Known limits (stated in the report): survivor universe (only companies priced today), latest (not point-in-time) sector labels, SEC coverage below the 80% gate, prices from 2016-09.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "out")
SEC = os.path.join(HERE, "data", "sec")
HOLDOUT = (pd.Timestamp("2023-01-01"), pd.Timestamp("2025-04-01"))
HORIZONS = (20, 60, 120, 250)
MIN_CLOSE, MIN_ADV, MIN_HIST = 5.0, 2_000_000.0, 252
RNG = np.random.default_rng(7)
SEV = {"low": 0, "medium": 1, "high": 2}


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def load_prices(d):
    meta = json.load(open(os.path.join(d, "extract_meta.json")))
    assert sha(os.path.join(d, "prices.csv.gz")) == meta["prices_sha256"], "price extract hash mismatch"
    px = pd.read_csv(os.path.join(d, "prices.csv.gz"), parse_dates=["date"])
    wide = {k: px.pivot(index="date", columns="symbol", values=k).sort_index() for k in ("open", "close", "volume")}
    # dates with too few symbols are stray rows (the 2026-02-16 CBL holiday bar, PR #92): drop them
    good = wide["close"].notna().sum(axis=1) >= 500
    wide = {k: v.loc[good] for k, v in wide.items()}
    disc = pd.read_csv(os.path.join(d, "discontinuities.csv.gz"), parse_dates=["date"])
    D = pd.DataFrame(False, index=wide["close"].index, columns=wide["close"].columns)
    for s, dt in zip(disc["symbol"], disc["date"]):
        if s in D.columns:
            i = D.index.searchsorted(dt)
            if i < len(D):
                D.iat[i, D.columns.get_loc(s)] = True
    return wide["open"], wide["close"], wide["volume"], D, meta


def block_ci(monthly: pd.Series, block=3, n=2000):
    x = monthly.dropna().values
    if len(x) < 2 * block:
        return float(np.mean(x)) if len(x) else np.nan, np.nan, np.nan
    k = int(np.ceil(len(x) / block))
    starts = RNG.integers(0, len(x) - block + 1, size=(n, k))
    idx = (starts[:, :, None] + np.arange(block)).reshape(n, -1)[:, : len(x)]
    m = x[idx].mean(axis=1)
    return float(x.mean()), float(np.quantile(m, 0.025)), float(np.quantile(m, 0.975))


def fmt(m, lo, hi, pct=True):
    f = (lambda v: f"{v * 100:+.2f}%") if pct else (lambda v: f"{v:+.3f}")
    return f"{f(m)} [{f(lo)}, {f(hi)}]" if not np.isnan(lo) else f"{f(m)} [n/a]"


def exploration_events(ev: pd.DataFrame):
    """(exploration events, holdout event count, holdout company-quarter count, stale event count). Holdout and stale rows are removed here, before any price is read."""
    in_hold = (ev["available_at"] >= HOLDOUT[0]) & (ev["available_at"] < HOLDOUT[1])
    n_hold_events, n_hold_cq = int(in_hold.sum()), int(ev.loc[in_hold, ["symbol", "quarter_end"]].drop_duplicates().shape[0])
    ev = ev.loc[~in_hold]
    stale = (ev["available_at"] - ev["quarter_end"]).dt.days > 120
    out = ev.loc[~stale].copy()
    assert not ((out["available_at"] >= HOLDOUT[0]) & (out["available_at"] < HOLDOUT[1])).any()
    return out, n_hold_events, n_hold_cq, int(stale.sum())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices", required=True)
    a = ap.parse_args(argv)

    # ------------------------------------------------------------------ events -> company-quarters; the holdout is removed before anything else
    ev = pd.read_csv(os.path.join(OUT, "events.csv.gz"), parse_dates=["available_at", "quarter_end"])
    n_all = len(ev)
    ev, n_hold_events, n_hold_cq, n_stale = exploration_events(ev)
    ev["sev"] = ev["severity"].map(SEV)
    cq = ev.groupby(["symbol", "quarter_end", "available_at"]).agg(n_types=("event_type", "nunique"), max_sev=("sev", "max"), max_strength=("strength", "max"),
                                                                   types=("event_type", lambda s: "+".join(sorted(set(s))))).reset_index()
    print(f"Base events reconstructed: {n_all:,}. HOLDOUT (public 2023-01-01..2025-03-31): {n_hold_events:,} events / {n_hold_cq:,} company-quarters removed before reading prices; "
          f"no outcome computed for them.")
    print(f"Stale (first public > 120 days after quarter end) dropped: {n_stale:,} events.")
    print(f"Exploration set: {len(ev):,} events, {len(cq):,} company-quarters, {cq['symbol'].nunique():,} companies.\n")

    # ------------------------------------------------------------------ prices, eligibility, state at the session before entry
    O, Cl, V, DISC, pmeta = load_prices(a.prices)
    idx = Cl.index
    covered = set(pd.read_csv(os.path.join(SEC, "symbols.csv.gz"))["symbol"])
    cols = [c for c in Cl.columns if c in covered]
    O, Cl, V, DISC = O[cols], Cl[cols], V[cols], DISC[cols]
    adv = (Cl * V).rolling(20, min_periods=15).mean()
    hist = Cl.notna().cumsum()
    elig = (Cl >= MIN_CLOSE) & (adv >= MIN_ADV) & (hist > MIN_HIST)
    lr = np.log(Cl / Cl.shift(1))
    vol60 = lr.rolling(60, min_periods=50).std()
    dist52 = Cl / Cl.rolling(252, min_periods=200).max() - 1
    ret126 = Cl / Cl.shift(126) - 1
    dcum = DISC.astype("int32").cumsum()
    Cv, Ov, Ev, Vv, Av = Cl.values, O.values, elig.values, vol60.values, adv.values
    Dv = dcum.values
    n = len(idx)
    print(f"Prices: {pmeta['prices_min_date']}..{pmeta['prices_max_date']} (sha {pmeta['prices_sha256'][:12]}), SEC-covered symbols with prices: {len(cols):,}\n")

    def clean(i0, i1, js):
        """True for each column in js with no discontinuity in sessions [i0, i1]."""
        lo = Dv[i0 - 1, js] if i0 > 0 else np.zeros(len(js))
        return (Dv[min(i1, n - 1), js] - lo) == 0

    # per entry session: the matched pool (computed once per session and horizon)
    pool_cache = {}

    def pool(e):
        if e in pool_cache:
            return pool_cache[e]
        s = e - 1
        el = Ev[s] & ~np.isnan(Vv[s])
        js = np.flatnonzero(el)
        vr = pd.Series(Vv[s, js]).rank(pct=True).values
        ar = pd.Series(Av[s, js]).rank(pct=True).values
        vdec = np.minimum((vr * 10).astype(int), 9)
        dvt = np.minimum((ar * 3).astype(int), 2)
        d52 = pd.Series(dist52.values[s, js]).rank(pct=True).values
        r6 = pd.Series(ret126.values[s, js]).rank(pct=True).values
        outs = {}
        for H in HORIZONS:
            x = e + H - 1
            if x >= n:
                outs[H] = None
                continue
            r = Cv[x, js] / Ov[e, js] - 1
            ok = ~np.isnan(r) & clean(max(e - 61, 0), x, js)
            r = np.where(ok, r, np.nan)
            top = np.nanquantile(r, 0.9) if np.isfinite(r).sum() > 50 else np.nan
            cell = {}
            for v in range(10):
                for t in range(3):
                    m = (vdec == v) & (dvt == t) & ok
                    cell[(v, t)] = m
            outs[H] = {"r": r, "top": top, "cells": cell, "vdec": vdec, "ok": ok}
        res = {"js": js, "jpos": {j: k for k, j in enumerate(js)}, "vdec": vdec, "dvt": dvt, "d52": d52, "r6": r6, "outs": outs}
        pool_cache[e] = res
        return res

    colpos = {c: k for k, c in enumerate(cols)}
    rows = []
    for r in cq.itertuples(index=False):
        j = colpos.get(r.symbol)
        if j is None:
            continue
        e = idx.searchsorted(r.available_at, side="right")       # first session strictly after the public date
        if e >= n or e < 1:
            continue
        P = pool(e)
        k = P["jpos"].get(j)
        if k is None:                                           # not eligible the session before entry
            continue
        v, t = P["vdec"][k], P["dvt"][k]
        rec = {"symbol": r.symbol, "quarter_end": r.quarter_end, "available_at": r.available_at, "entry": idx[e], "n_types": r.n_types, "max_sev": r.max_sev, "types": r.types,
               "vdec": v, "dvt": t, "d52_t": min(int(P["d52"][k] * 3), 2) if not np.isnan(P["d52"][k]) else None,
               "r6_t": min(int(P["r6"][k] * 3), 2) if not np.isnan(P["r6"][k]) else None}
        for H in HORIZONS:
            o = P["outs"][H]
            if o is None or np.isnan(o["r"][k]):
                continue
            m = o["cells"][(v, t)].copy()
            m[k] = False
            if m.sum() < 10:
                m = (o["vdec"] == v) & o["ok"]
                m[k] = False
            base = o["r"][m]
            ri = o["r"][k]
            rec[f"r{H}"] = ri
            rec[f"x{H}"] = ri - np.nanmean(base)
            rec[f"top{H}"] = float(ri >= o["top"]) if not np.isnan(o["top"]) else np.nan
            if H == 250:
                rec["dbl"], rec["hlv"] = float(ri >= 1.0), float(ri <= -0.5)
                rec["dbl_exp"], rec["hlv_exp"] = float(np.nanmean(base >= 1.0)), float(np.nanmean(base <= -0.5))
        rows.append(rec)
    R = pd.DataFrame(rows)
    R["period"] = np.where(R["available_at"] < HOLDOUT[0], "pre-holdout", "post-holdout")
    R["month"] = R["entry"].dt.to_period("M")
    R["year"] = R["entry"].dt.year
    print(f"Company-quarters with an eligible, priced entry: {len(R):,} (pre-holdout {int((R.period == 'pre-holdout').sum()):,}, post-holdout {int((R.period == 'post-holdout').sum()):,})\n")

    def summarize(sub, H, label):
        s = sub.dropna(subset=[f"x{H}"])
        if len(s) < 30:
            return None
        mm = s.groupby("month")[f"x{H}"].mean()
        m, lo, hi = block_ci(mm)
        ex = s[s["year"] != 2020]
        m2 = block_ci(ex.groupby("month")[f"x{H}"].mean()) if len(ex) >= 30 else (np.nan, np.nan, np.nan)
        loo = [s[s["year"] != y].groupby("month")[f"x{H}"].mean().mean() for y in sorted(s["year"].unique())]
        out = {"cut": label, "H": H, "company-quarters": len(s), "companies": s["symbol"].nunique(), "months": s["month"].nunique(),
               "mean raw": f"{s[f'r{H}'].mean() * 100:+.2f}%", "median raw": f"{s[f'r{H}'].median() * 100:+.2f}%",
               "excess vs matched (month-equal) [95% CI]": fmt(m, lo, hi), "ex-2020": fmt(*m2), "leave-one-year-out range": f"{min(loo) * 100:+.2f}% .. {max(loo) * 100:+.2f}%",
               "top-decile rate (base 10%)": f"{s[f'top{H}'].mean() * 100:.1f}%"}
        return out

    def tails(sub, label):
        s = sub.dropna(subset=["dbl"])
        if len(s) < 30:
            return None
        g = s.groupby("month")
        dd = g.apply(lambda x: (x["dbl"] - x["dbl_exp"]).mean())
        hd = g.apply(lambda x: (x["hlv"] - x["hlv_exp"]).mean())
        return {"cut": label, "company-quarters": len(s), "companies": s["symbol"].nunique(), "months": s["month"].nunique(),
                "doubled": f"{s['dbl'].mean() * 100:.1f}%", "expected (matched)": f"{s['dbl_exp'].mean() * 100:.1f}%", "doubled - expected [CI]": fmt(*block_ci(dd)),
                "halved": f"{s['hlv'].mean() * 100:.1f}%", "expected (matched) ": f"{s['hlv_exp'].mean() * 100:.1f}%", "halved - expected [CI]": fmt(*block_ci(hd)),
                "doubled/halved": f"{s['dbl'].mean() / max(s['hlv'].mean(), 1e-9):.2f} (matched {s['dbl_exp'].mean() / max(s['hlv_exp'].mean(), 1e-9):.2f})",
                "top-decile 250": f"{s['top250'].mean() * 100:.1f}%"}

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.max_colwidth", 60)
    results = {"holdout_removed": {"events": n_hold_events, "company_quarters": n_hold_cq}, "priced_company_quarters": len(R)}
    for period in ("pre-holdout", "post-holdout"):
        S = R[R["period"] == period]
        tab = []
        for H in HORIZONS:
            for label, sub in [("all company-quarters", S), ("max severity high", S[S["max_sev"] == 2]), ("medium/low only", S[S["max_sev"] < 2]),
                               ("2+ event types (stacked)", S[S["n_types"] >= 2]), ("1 event type", S[S["n_types"] == 1])]:
                x = summarize(sub, H, label)
                if x:
                    tab.append(x)
        print(f"=== {period.upper()}: excess return vs same-date volatility x size matched SEC-covered stocks ===")
        print(pd.DataFrame(tab).to_string(index=False) if tab else "  (no complete windows)")
        print()
        results[period] = {"excess": tab}
        tt = [tails(sub, label) for label, sub in [("all company-quarters", S), ("max severity high", S[S["max_sev"] == 2]), ("2+ event types (stacked)", S[S["n_types"] >= 2]),
                                                   ("ex-2020 entries", S[S["year"] != 2020])]]
        tt = [x for x in tt if x]
        print(f"=== {period.upper()}: big winners and big losers at 250 sessions vs matched expectation ===")
        print(pd.DataFrame(tt).to_string(index=False) if tt else "  (no complete 250-session windows)")
        print()
        results[period]["tails"] = tt
        if period == "pre-holdout":
            et = []
            for t_ in ["revenue_acceleration", "margin_expansion", "operating_leverage_inflection", "profitability_turnaround", "fcf_turnaround"]:
                sub = S[S["types"].str.contains(t_)]
                for H in (60, 250):
                    x = summarize(sub, H, t_)
                    if x:
                        et.append(x)
                y = tails(sub, t_)
                if y:
                    et.append({"cut": t_, "H": "250 tails", **{k: v for k, v in y.items() if k != "cut"}})
            print("=== PRE-HOLDOUT by event type (a company-quarter counts under every type it has) ===")
            print(pd.DataFrame([x for x in et if x.get("H") != "250 tails"]).to_string(index=False))
            print(pd.DataFrame([x for x in et if x.get("H") == "250 tails"]).to_string(index=False))
            print()
            results[period]["by_type"] = et
            ps = []
            for name, col in (("distance from 52w high", "d52_t"), ("6-month return", "r6_t")):
                for t_ in (0, 1, 2):
                    y = tails(S[S[col] == t_], f"{name}: {['low', 'mid', 'high'][t_]} tercile")
                    if y:
                        ps.append(y)
            print("=== PRE-HOLDOUT price state at entry (within-date tercile of the pool): does it separate doublers from halvers among inflections? ===")
            print(pd.DataFrame(ps).to_string(index=False))
            print()
            results[period]["price_state"] = ps
            yr = R[R["period"] == period].groupby("year").agg(n=("x60", "count"), x60=("x60", "mean"), dbl=("dbl", "mean"), dbl_exp=("dbl_exp", "mean"), hlv=("hlv", "mean"),
                                                               hlv_exp=("hlv_exp", "mean"))
            print("=== PRE-HOLDOUT by entry year (pooled; 60-session excess, 250-session tails) ===")
            print(yr.round(4).to_string())
            print()
            results[period]["by_year"] = yr.reset_index().astype({"year": int}).round(4).to_dict(orient="records")

    # ------------------------------------------------------------------ reverse view: month-end snapshots, pre-holdout only
    evd = cq[cq["available_at"] < HOLDOUT[0]][["symbol", "available_at"]]
    me = pd.Series(idx, index=idx).groupby(idx.to_period("M")).max()
    me = me[(me >= idx[MIN_HIST]) & (me < HOLDOUT[0])]
    snaps = []
    for t in me:
        i = idx.get_loc(t)
        if i + 250 >= n:
            continue
        js = np.flatnonzero(Ev[i] & ~np.isnan(Vv[i]))
        r = Cv[i + 250, js] / Cv[i, js] - 1
        ok = ~np.isnan(r) & clean(max(i - 60, 0), i + 250, js)
        vdec = np.minimum((pd.Series(Vv[i, js]).rank(pct=True).values * 10).astype(int), 9)
        recent = set(evd[(evd["available_at"] > t - pd.Timedelta(days=91)) & (evd["available_at"] <= t)]["symbol"])
        flag = np.array([cols[j] in recent for j in js])
        snaps.append(pd.DataFrame({"t": t, "r": r[ok], "vdec": vdec[ok], "event": flag[ok]}))
    SN = pd.concat(snaps)
    SN["dbl"], SN["hlv"] = SN["r"] >= 1, SN["r"] <= -0.5
    g = SN.groupby(["t", "vdec", "event"])[["dbl", "hlv"]].mean().unstack("event")
    cnt = SN.groupby(["t", "vdec", "event"]).size().unstack("event").fillna(0)
    valid = (cnt[True] >= 3) & (cnt[False] >= 3)
    w = cnt[True][valid]
    dd = ((g[("dbl", True)] - g[("dbl", False)])[valid] * w).groupby(level="t").sum() / w.groupby(level="t").sum()
    hd = ((g[("hlv", True)] - g[("hlv", False)])[valid] * w).groupby(level="t").sum() / w.groupby(level="t").sum()
    doublers = SN[SN["dbl"]]
    rv = {"snapshots": int(SN["t"].nunique()), "stock-months": len(SN), "with a recent event": int(SN["event"].sum()),
          "doubling rate, recent event / none": f"{SN.loc[SN.event, 'dbl'].mean() * 100:.1f}% / {SN.loc[~SN.event, 'dbl'].mean() * 100:.1f}%",
          "halving rate, recent event / none": f"{SN.loc[SN.event, 'hlv'].mean() * 100:.1f}% / {SN.loc[~SN.event, 'hlv'].mean() * 100:.1f}%",
          "within-vol-decile difference, doubled [CI]": fmt(*block_ci(dd)), "within-vol-decile difference, halved [CI]": fmt(*block_ci(hd)),
          "share of future doublers with a recent event": f"{doublers['event'].mean() * 100:.1f}% (share of all stock-months: {SN['event'].mean() * 100:.1f}%)"}
    print("=== PRE-HOLDOUT reverse view: month-end SEC-covered eligible stocks; base event public in the previous 91 days vs none (250-session outcome) ===")
    for k, v in rv.items():
        print(f"  {k}: {v}")
    results["reverse_view"] = rv
    with open(os.path.join(HERE, "study_results.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(results, fh, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
