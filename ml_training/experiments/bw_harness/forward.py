"""Read-only forward research report: which forward candidates have a label, how complete their decision-time evidence is, and when the rest will mature.

The rule that matters: an INCOMPLETE horizon is "not yet known", never a negative. A candidate whose entry bar does not exist yet, or whose 20 / 60 / 120-bar window is not complete, has status `no_entry_yet` or
`immature`; its values are NaN, it is excluded from every rate, and it appears only in the maturity timeline. The label itself is the canonical one (labels.signal_labels): entry at the next open, 1R = 2 x the ATR14
that First Light RECORDED at the decision (`feature_snapshot.features.atr_14`, an observed point-in-time number), tiers 3R / 5R / 10R, MFE kept apart from any realised result.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Dict, List, Mapping, Optional

import numpy as np
import pandas as pd

from . import labels as L
from .config import Config


def forward_labels(cands: pd.DataFrame, bars: Mapping[str, pd.DataFrame], cfg: Config) -> pd.DataFrame:
    """`cands`: columns symbol, session_date, direction, atr14 (the recorded decision-time ATR14). Returns one row per candidate with the canonical label columns, status per horizon and the number of bars still needed."""
    rows: List[pd.DataFrame] = []
    for sym, g in cands.groupby("symbol", sort=True):
        px = bars.get(sym)
        if px is None or len(px) == 0:
            r = g.copy()
            r["entry_status"] = "no_price_history"
            for H in cfg.horizons:
                r[f"status_{H}"] = "no_entry_yet"
                r[f"bars_needed_{H}"] = np.nan
            rows.append(r)
            continue
        px = px.sort_values("date").drop_duplicates("date").reset_index(drop=True)
        n = len(px)
        dates = pd.DatetimeIndex(pd.to_datetime(px["date"]))
        pos = dates.get_indexer(pd.DatetimeIndex(pd.to_datetime(g["session_date"])))
        atr = np.full(n, np.nan)
        have = pos >= 0
        atr[pos[have]] = g["atr14"].to_numpy(dtype=float)[have]
        r = g.reset_index(drop=True).copy()
        r["bar_found"] = have
        idx = np.where(have, pos, 0)
        lab = L.signal_labels(px["open"].to_numpy(float), px["high"].to_numpy(float), px["low"].to_numpy(float), px["close"].to_numpy(float), atr, idx, g["direction"].to_numpy(int), cfg, None)
        for k, v in lab.items():
            r[k] = v
        for k in lab:                                                              # a candidate whose signal bar is missing has no label of any kind (never the values of a stand-in bar)
            if k.startswith(("mfe_", "terminal_", "ge", "ret_label_", "vn_", "persist_")) or k in ("entry_px", "unit_r", "sigma60"):
                r.loc[~have, k] = np.nan
        r["entry_status"] = np.where(~have, "signal_bar_missing", np.where(idx + 1 < n, "entered", "no_entry_yet"))
        for H in cfg.horizons:
            st = r[f"status_{H}"].to_numpy(dtype=object)
            st = np.where(~have, "no_entry_yet", np.where(r["entry_status"] == "no_entry_yet", "no_entry_yet", st))
            r[f"status_{H}"] = st
            avail = np.where(have, n - 1 - idx, 0)                                  # bars after the signal bar
            r[f"bars_needed_{H}"] = np.where(st == "ok", 0, np.maximum(0, H + 1 - avail))      # entry bar + H bars
        r["last_bar_date"] = dates[-1]
        rows.append(r)
    return pd.concat(rows, ignore_index=True)


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(float(c - h), 4), round(float(c + h), 4)]


def summarise(df: pd.DataFrame, cfg: Config, as_of: Optional[str] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"as_of_last_bar": str(df["last_bar_date"].max().date()) if "last_bar_date" in df and len(df) else None, "candidates": int(len(df))}
    trig = df[df["triggered"]] if "triggered" in df else df
    out["triggered_signals"] = int(len(trig))
    out["by_session"] = {}
    for sess, g in trig.groupby(trig["session_date"].astype(str)):
        row: Dict[str, Any] = {"signals": int(len(g))}
        for H in cfg.horizons:
            vc = g[f"status_{H}"].value_counts()
            row[f"h{H}"] = {"ok": int(vc.get("ok", 0)), "immature": int(vc.get("immature", 0)), "no_entry_yet": int(vc.get("no_entry_yet", 0)), "discontinuity": int(vc.get("discontinuity", 0))}
        out["by_session"][sess] = row
    out["horizons"] = {}
    for H in cfg.horizons:
        st = trig[f"status_{H}"]
        ok = trig[st == "ok"]
        h: Dict[str, Any] = {"signals": int(len(trig)), "labelled": int(len(ok)), "immature": int((st == "immature").sum()), "no_entry_yet": int((st == "no_entry_yet").sum()),
                             "share_labelled": round(float(len(ok) / len(trig)), 4) if len(trig) else None}
        if len(ok):
            for d, name in ((1, "long"), (-1, "short")):
                gg = ok[ok["direction"] == d]
                if len(gg):
                    h[name] = {"labelled": int(len(gg)), **{f"ge{int(t)}r": {"hits": int(gg[f"ge{int(t)}r_{H}"].sum()), "rate": round(float(gg[f"ge{int(t)}r_{H}"].mean()), 4),
                                                              "wilson95": wilson(int(gg[f"ge{int(t)}r_{H}"].sum()), len(gg))} for t in cfg.tiers_r}}
        pend = trig[st.isin(["immature", "no_entry_yet"])]
        if len(pend):
            need = pend[f"bars_needed_{H}"]
            h["bars_still_needed"] = {"min": int(need.min()), "median": float(need.median()), "max": int(need.max())}
            last = pd.Timestamp(trig["last_bar_date"].max())
            h["approx_first_maturity_date"] = str((last + pd.tseries.offsets.BDay(int(need.min()))).date())
            h["approx_last_maturity_date"] = str((last + pd.tseries.offsets.BDay(int(need.max()))).date())
            h["maturity_dates_are_approximate"] = "business days from the last stored bar; exchange holidays are not modelled"
        out["horizons"][f"h{H}"] = h
    out["rule"] = "an incomplete horizon is not yet known: it has no value, is excluded from every rate and is never counted as a negative outcome"
    return out


def evidence_coverage(feat: pd.DataFrame) -> Dict[str, Any]:
    """Coverage of the observed decision-time evidence: snapshot status, sector source, and the recorded list of missing features."""
    out: Dict[str, Any] = {"snapshots": int(len(feat))}
    out["snapshot_status"] = {str(k): int(v) for k, v in feat["snapshot_status"].value_counts().items()}
    out["sector_source"] = {str(k): int(v) for k, v in feat["sector_source"].fillna("none").value_counts().items()}
    miss: Dict[str, int] = {}
    for m in feat["missing_features"]:
        for k in (m or []):
            miss[k] = miss.get(k, 0) + 1
    out["missing_feature_counts"] = dict(sorted(miss.items(), key=lambda kv: -kv[1]))
    out["provenance"] = "OBSERVED_PIT (database-stamped at capture)"
    return out


def run_forward_report(conn, cfg: Config) -> Dict[str, Any]:
    """SELECT-only. Needs candidate_observation, feature_snapshot and stock_prices."""
    cur = conn.cursor()
    cur.execute("SELECT c.symbol, c.session_date, c.direction, c.triggered, s.snapshot_status, s.sector_source, (s.features->>'atr_14')::float, to_jsonb(s.missing_features) "
                "FROM candidate_observation c JOIN feature_snapshot s ON s.id = c.snapshot_id ORDER BY c.session_date, c.symbol")
    rows = cur.fetchall()
    conn.rollback()
    cands = pd.DataFrame(rows, columns=["symbol", "session_date", "direction", "triggered", "snapshot_status", "sector_source", "atr14", "missing_features"])
    cands["session_date"] = pd.to_datetime(cands["session_date"])
    cands["direction"] = cands["direction"].astype(int)
    syms = sorted(cands["symbol"].unique())
    d0 = cands["session_date"].min()
    cur.execute("SELECT symbol, date, open, high, low, close, volume FROM stock_prices WHERE symbol = ANY(%s) AND date >= %s ORDER BY symbol, date", (syms, d0.date()))
    px = pd.DataFrame(cur.fetchall(), columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    conn.rollback()
    for c in ("open", "high", "low", "close", "volume"):
        px[c] = pd.to_numeric(px[c], errors="coerce")
    bars = {s: g.drop(columns="symbol").reset_index(drop=True) for s, g in px.groupby("symbol")}
    lab = forward_labels(cands, bars, cfg)
    return {"labels": summarise(lab, cfg), "evidence_coverage": evidence_coverage(cands), "candidates_without_a_price_history": int((lab["entry_status"] == "no_price_history").sum()),
            "definition": "canonical label: entry at the next open, 1R = 2 x recorded ATR14, tiers 3R / 5R / 10R, horizons 20 / 60 / 120 bars, MFE only; no realised strategy return is mixed in",
            "caution": "forward windows are short; counts are tiny and clustered in a few market days; nothing here is evidence of an effect"}
