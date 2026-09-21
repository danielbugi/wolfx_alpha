# ml_training/data_preparation/build_dataset.py
"""
Build ml_breakout_dataset_v2 from `stock_prices` ONLY (see ml_training/features/price_features.py).

Replaces the breakouts -> momentum_labeler -> feature_builder chain, which mixed three
tables computed on different price bases. Per symbol, in one pass over one consistent
OHLCV frame: detect breakouts, compute features + labels, drop samples that cross a
price discontinuity, and record the discontinuities in `price_discontinuities`.

Usage (repo root):
    python ml_training/data_preparation/build_dataset.py --test AAPL MSFT
    python ml_training/data_preparation/build_dataset.py --replace          # full rebuild
    python ml_training/data_preparation/build_dataset.py --start-date 2020-01-01
"""
import argparse
import json
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

warnings.filterwarnings("ignore")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ml_training", "config"))

from ml_config import ml_config  # noqa: E402
from ml_training.features import price_features as pf  # noqa: E402

MIN_BAR_INDEX = 60  # need 60 prior bars for ret_60d


def load_sectors(conn) -> dict:
    """Latest known sector per symbol. Sector membership is stable enough to treat as point-in-time;
    market cap / beta / quality scores are NOT (only three snapshot windows exist in daily_fundamentals)."""
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ON (symbol) symbol, sector FROM daily_fundamentals "
                "WHERE sector IS NOT NULL AND sector <> '' ORDER BY symbol, date DESC")
    return dict(cur.fetchall())


def load_prices(conn, symbols):
    cur = conn.cursor()
    cur.execute("SELECT symbol, date, open, high, low, close, volume FROM stock_prices "
                "WHERE symbol = ANY(%s) ORDER BY symbol, date", (symbols,))
    df = pd.DataFrame(cur.fetchall(), columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    for c in pf.OHLCV:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    return df


def process_symbol(symbol, px, sector, start_date):
    """Returns (dataset_rows_df | None, discontinuities_df)."""
    px = px.dropna(subset=["close"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    if len(px) < MIN_BAR_INDEX + pf.PLAN_HORIZON:
        return None, pd.DataFrame()
    disc = pf.find_discontinuities(px)
    bad = pf.contaminated_mask(len(px), disc["pos"])
    ind = pf.compute_indicators(px)
    d = pf.detect_breakouts(ind)
    dates = pd.to_datetime(ind["date"])
    liquid = (ind["dollar_vol_20"] >= pf.MIN_DOLLAR_VOLUME_20).to_numpy()
    keep = (d != 0) & ~bad & liquid & (np.arange(len(px)) >= MIN_BAR_INDEX) & (dates >= start_date).to_numpy()
    pos = np.flatnonzero(keep)
    disc = disc.assign(symbol=symbol)
    if len(pos) == 0:
        return None, disc
    feats = pf.build_breakout_features(ind, pos, d[pos], sector)
    out = pd.concat([
        pd.DataFrame({"symbol": symbol, "date": dates.to_numpy()[pos], "direction": d[pos]}),
        pf.legacy_labels(ind, pos, d[pos]),
        pf.plan_outcomes(ind, pos, d[pos]),
    ], axis=1)
    out["features"] = [json.dumps({k: (None if pd.isna(v) else float(v)) for k, v in row.items()})
                       for row in feats.to_dict("records")]
    return out, disc


def save(conn, ds: pd.DataFrame, disc: pd.DataFrame):
    cur = conn.cursor()
    nn = lambda v: None if pd.isna(v) else v  # noqa: E731
    if len(ds):
        rows = [(r.symbol, pd.Timestamp(r.date).date(), int(r.direction), pf.FEATURE_SET_VERSION, r.features,
                 nn(r.momentum_score), None if pd.isna(r.target_binary) else int(r.target_binary),
                 nn(r.plan_r), nn(r.r_single), None if pd.isna(r.stopped) else int(r.stopped),
                 None if pd.isna(r.tp3_hit) else int(r.tp3_hit), nn(r.mae_r)) for r in ds.itertuples()]
        execute_values(cur, """
            INSERT INTO ml_breakout_dataset_v2 (symbol, date, direction, feature_set_version, features,
                momentum_score, target_binary, plan_r, r_single, stopped, tp3_hit, mae_r)
            VALUES %s
            ON CONFLICT (symbol, date) DO UPDATE SET direction=EXCLUDED.direction,
                feature_set_version=EXCLUDED.feature_set_version, features=EXCLUDED.features,
                momentum_score=EXCLUDED.momentum_score, target_binary=EXCLUDED.target_binary,
                plan_r=EXCLUDED.plan_r, r_single=EXCLUDED.r_single, stopped=EXCLUDED.stopped,
                tp3_hit=EXCLUDED.tp3_hit, mae_r=EXCLUDED.mae_r, created_at=NOW()""", rows, page_size=2000)
    if len(disc):
        rows = [(r.symbol, pd.Timestamp(r.date).date(), r.kind, nn(r.prev_close), nn(r.close), nn(r.ratio))
                for r in disc.itertuples()]
        execute_values(cur, """
            INSERT INTO price_discontinuities (symbol, date, kind, prev_close, close, ratio) VALUES %s
            ON CONFLICT (symbol, date, kind) DO UPDATE SET prev_close=EXCLUDED.prev_close,
                close=EXCLUDED.close, ratio=EXCLUDED.ratio, detected_at=NOW()""", rows)
    conn.commit()


def main():
    ap = argparse.ArgumentParser(description="Build ml_breakout_dataset_v2 from stock_prices only")
    ap.add_argument("--test", nargs="+", help="only these symbols")
    ap.add_argument("--limit", type=int, help="first N symbols (debug)")
    ap.add_argument("--start-date", default="2018-01-01", help="earliest breakout date to include")
    ap.add_argument("--replace", action="store_true",
                    help="delete existing ml_breakout_dataset_v2 rows (and re-detect discontinuities) first")
    args = ap.parse_args()

    conn = psycopg2.connect(**ml_config.db_config)
    cur = conn.cursor()
    if args.test:
        symbols = sorted(args.test)
    else:
        cur.execute("SELECT DISTINCT symbol FROM stock_prices WHERE symbol IS NOT NULL AND symbol <> '' ORDER BY symbol")
        symbols = [r[0] for r in cur.fetchall()]
        if args.limit:
            symbols = symbols[:args.limit]
    if args.replace and not args.test:
        cur.execute("DELETE FROM ml_breakout_dataset_v2")
        cur.execute("DELETE FROM price_discontinuities")
        conn.commit()
        print("Cleared ml_breakout_dataset_v2 and price_discontinuities (own tables) for full rebuild")
    sectors = load_sectors(conn)
    start = pd.Timestamp(args.start_date)
    t0, n_rows, n_disc, n_sym = time.time(), 0, 0, 0
    for i in range(0, len(symbols), 150):
        chunk = symbols[i:i + 150]
        prices = load_prices(conn, chunk)
        parts, discs = [], []
        for sym, px in prices.groupby("symbol", sort=False):
            ds, disc = process_symbol(sym, px, sectors.get(sym), start)
            if ds is not None:
                parts.append(ds)
            if len(disc):
                discs.append(disc)
        ds_all = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        disc_all = pd.concat(discs, ignore_index=True) if discs else pd.DataFrame()
        save(conn, ds_all, disc_all)
        n_rows += len(ds_all); n_disc += len(disc_all); n_sym += len(chunk)
        print(f"  {n_sym}/{len(symbols)} symbols | {n_rows} samples | {n_disc} discontinuities | {time.time() - t0:.0f}s", flush=True)
    print(f"Done: {n_rows} samples, {n_disc} discontinuities recorded, feature_set={pf.FEATURE_SET_VERSION}")
    conn.close()


if __name__ == "__main__":
    main()
