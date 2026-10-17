"""
Alerts (deep value / turnaround) screen -- historical-sample backtest. RESEARCH ONLY: read-only, not production
runtime code, not imported by anything. Methodology, results and limitations: README.md in this folder.

Reproduce (any read access to a trading_production copy; DB_* from the repo-root .env):
    python docs/research/2026-10-03_alerts-deep-value-backtest/backtest.py              # writes results.txt here
    python .../backtest.py --price-end 2026-09-23 --out /tmp/results.txt

Two studies on the same price panel:
  A. Event study of the page's TURNAROUND rule (point in time via earnings_calendar report dates).
  B. Baseline: every stock near its 12-month low, monthly cohorts (no fundamentals at all).
The rules come from backend/services/deep_value_service.py itself (assess_balance_sheet, find_year_ago_quarter,
MAX_QUARTER_GAP_DAYS), so this tests the shipped definitions, not a re-implementation.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "backend"))
from services.deep_value_service import (  # noqa: E402
    FAIL, MAX_QUARTER_GAP_DAYS, assess_balance_sheet, find_year_ago_quarter,
)

HORIZONS = (21, 63)            # trading days after entry
NEAR_LOW = 0.15                # page default: within 15% of the 12-month low
LOOKBACK = 252                 # trading days in the 12-month low
REPORT_WINDOW_DAYS = 100       # a quarter's report must land within (quarter_end, quarter_end + 100d]
SPLIT_RATIOS = [2, 3, 4, 5, 8, 10, 15, 20, 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 8, 1 / 10, 1 / 15, 1 / 20]
SPLIT_TOL = 0.03               # a one-day close ratio within 3% of a split ratio = split-like day
RETURN_CLIP = (-0.95, 5.0)     # benchmark members only: blunt residual data errors in the universe mean

LEGACY_EXCLUSION = "fiscal_year IS NOT NULL"   # drops the 2025-07-10 migration snapshot (see deploy/db/qf_legacy_*)


def connect():
    load_dotenv(os.path.join(REPO, ".env"))
    c = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                         user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"), connect_timeout=10)
    c.set_session(readonly=True)
    return c


def load(c, price_end):
    q = pd.read_sql(f"""
        SELECT q.symbol, q.quarter, q.net_income, q.total_debt, q.total_equity, q.current_assets,
               q.current_liabilities, q.cash, q.operating_cash_flow, q.free_cash_flow,
               (SELECT min(report_date) FROM earnings_calendar e
                 WHERE e.symbol = q.symbol AND e.report_date > q.quarter
                   AND e.report_date <= q.quarter + {REPORT_WINDOW_DAYS}) AS report_date
        FROM quarterly_fundamentals q WHERE {LEGACY_EXCLUSION}
        ORDER BY q.symbol, q.quarter DESC""", c)
    sectors = pd.read_sql("SELECT DISTINCT ON (symbol) symbol, sector FROM daily_fundamentals "
                          "ORDER BY symbol, date DESC", c)
    px = pd.read_sql("SELECT symbol, date, low, close FROM stock_prices "
                     "WHERE date >= DATE %(end)s - INTERVAL '28 months' AND date <= %(end)s", c,
                     params={"end": price_end})
    px["date"] = pd.to_datetime(px["date"])
    close = px.pivot(index="date", columns="symbol", values="close").astype(float).sort_index()
    low = px.pivot(index="date", columns="symbol", values="low").astype(float).sort_index()
    return q, dict(zip(sectors.symbol, sectors.sector)), close, low


def split_mask(close):
    ratio = close / close.shift(1)
    m = pd.DataFrame(False, index=close.index, columns=close.columns)
    for r in SPLIT_RATIOS:
        m |= (ratio / r - 1).abs() < SPLIT_TOL
    return m


def _clean(d):
    return {k: (None if (v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT) else v) for k, v in d.items()}


def event_study(q, sector, close, low, split):
    dates = close.index
    events = []
    for sym, g in q.groupby("symbol"):
        if sym not in close.columns:
            continue
        rows = [_clean(r) for r in g.to_dict("records")]
        for i in range(len(rows) - 1):
            latest, prev = rows[i], rows[i + 1]
            if latest["net_income"] is None or prev["net_income"] is None:
                continue
            if (latest["quarter"] - prev["quarter"]).days > MAX_QUARTER_GAP_DAYS:
                continue
            if not (latest["net_income"] > 0 and prev["net_income"] < 0):
                continue
            if latest["report_date"] is None:
                continue                                    # availability unknown -> dropped, never assumed
            pos = dates.searchsorted(pd.Timestamp(latest["report_date"]), side="right")  # first session AFTER
            if pos < LOOKBACK or pos >= len(dates):
                continue
            entry = close[sym].iloc[pos]
            if np.isnan(entry) or split[sym].iloc[pos - LOOKBACK + 1:pos + max(HORIZONS) + 1].any():
                continue
            low_12m = np.nanmin(low[sym].iloc[pos - LOOKBACK + 1:pos + 1].values)
            bs = assess_balance_sheet(latest, sector.get(sym))
            ya = find_year_ago_quarter(rows[i:])
            ev = {"symbol": sym, "quarter": latest["quarter"], "report_date": latest["report_date"],
                  "entry_date": dates[pos].date(), "dist_low": entry / low_12m - 1,
                  "bs_fail": FAIL in bs["checks"].values(),
                  "seasonal": None if ya is None else bool(ya["net_income"] > 0)}
            for h in HORIZONS:
                if pos + h < len(dates):
                    fwd = close[sym].iloc[pos + h] / entry - 1
                    univ = close.iloc[pos + h] / close.iloc[pos] - 1
                    univ = univ[~split.iloc[pos + 1:pos + h + 1].any()].dropna().clip(*RETURN_CLIP)
                    ev[f"r{h}"], ev[f"x{h}"] = fwd, fwd - univ.mean()
                else:
                    ev[f"r{h}"] = ev[f"x{h}"] = np.nan
            events.append(ev)
    ev = pd.DataFrame(events)
    ev["near_low"] = ev["dist_low"] <= NEAR_LOW
    return ev


def summarize(name, d):
    out = {"group": name, "n": len(d)}
    for h in HORIZONS:
        s = d[f"x{h}"].dropna()
        out[f"n{h}"] = len(s)
        out[f"mean_x{h}%"] = s.mean() * 100 if len(s) else np.nan
        out[f"median_x{h}%"] = s.median() * 100 if len(s) else np.nan
        out[f"beat{h}%"] = (s > 0).mean() * 100 if len(s) else np.nan
        out[f"t{h}"] = s.mean() / (s.std(ddof=1) / np.sqrt(len(s))) if len(s) > 2 else np.nan
    return out


def near_low_baseline(close, low, split):
    dates = close.index
    low252 = low.rolling(LOOKBACK, min_periods=LOOKBACK - 12).min()
    starts = [d for i, d in enumerate(dates) if i >= LOOKBACK and dates[i - 1].month != d.month]
    rows = []
    for d in starts:
        pos = dates.get_loc(d)
        for h in HORIZONS:
            if pos + h >= len(dates):
                continue
            ok = ~split.iloc[pos - LOOKBACK + 1:pos + h + 1].any()
            fwd = (close.iloc[pos + h] / close.iloc[pos] - 1)[ok].dropna().clip(*RETURN_CLIP)
            near = (close.iloc[pos] / low252.iloc[pos] - 1)[ok] <= NEAR_LOW
            near = near[near].index.intersection(fwd.index)
            rows.append({"cohort": d.date(), "h": h, "n_near": len(near),
                         "excess%": (fwd[near].mean() - fwd.mean()) * 100,
                         "beat%": (fwd[near] > fwd.mean()).mean() * 100})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--price-end", default="2026-09-23", help="last price date used (default: the 2026-10-03 run)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.txt"))
    a = ap.parse_args()

    c = connect()
    q, sector, close, low = load(c, a.price_end)
    split = split_mask(close)
    ev = event_study(q, sector, close, low, split)
    base = near_low_baseline(close, low, split)

    pd.set_option("display.width", 220)
    lines = [f"price panel {close.index.min().date()}..{close.index.max().date()}, {close.shape[1]} symbols, "
             f"split-like days excluded {int(split.values.sum())}",
             f"quarterly rows used {len(q)} ({LEGACY_EXCLUSION}), with a report date {int(q.report_date.notna().sum())}",
             "",
             f"A. TURNAROUND EVENT STUDY -- loss->profit flips with a report date and clean prices: {len(ev)} "
             f"(entries {ev.entry_date.min()}..{ev.entry_date.max()})",
             "   excess = stock return minus equal-weight mean of all clean symbols over the same window; "
             "t = mean/SE (overlapping windows -> overstated)"]
    groups = [
        ("all flips", ev),
        ("flips near 12m low", ev[ev.near_low]),
        ("  + no balance-sheet fail (= page rule)", ev[ev.near_low & ~ev.bs_fail]),
        ("    of which yr-ago quarter profitable (seasonal?)", ev[ev.near_low & ~ev.bs_fail & (ev.seasonal == True)]),  # noqa: E712
        ("    of which yr-ago quarter a loss", ev[ev.near_low & ~ev.bs_fail & (ev.seasonal == False)]),  # noqa: E712
        ("    of which yr-ago quarter unknown", ev[ev.near_low & ~ev.bs_fail & ev.seasonal.isna()]),
        ("flips NOT near low", ev[~ev.near_low]),
        ("near low, balance-sheet FAIL", ev[ev.near_low & ev.bs_fail]),
    ]
    lines.append(pd.DataFrame([summarize(n, d) for n, d in groups]).set_index("group").round(2).to_string())
    pr = ev[ev.near_low & ~ev.bs_fail]
    lines += ["", "   page rule by fiscal quarter:",
              pr.groupby("quarter").agg(n=("symbol", "size"), mean_x21=("x21", lambda s: s.mean() * 100),
                                        mean_x63=("x63", lambda s: s.mean() * 100)).round(2).to_string(),
              "", "B. BASELINE -- all stocks within 15% of the 252-day low, first trading day of each month"]
    for h in HORIZONS:
        s = base[base.h == h]
        lines.append(f"   h={h}: {len(s)} cohorts, avg {s.n_near.mean():.0f} stocks/cohort, mean excess "
                     f"{s['excess%'].mean():.2f}%, cohorts negative {int((s['excess%'] < 0).sum())}/{len(s)}, "
                     f"mean beat {s['beat%'].mean():.1f}%")
    lines.append(base[base.h == 63].round(2).to_string(index=False))
    text = "\n".join(lines) + "\n"
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
