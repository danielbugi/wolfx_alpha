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
from datetime import date, timezone

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


# A discontinuity is a fact with two times: `date` (when it happened) and `detected_at` (the first time THIS SYSTEM saw it). Only the second says what a
# live run could have known, so a rebuild must never move it for a row it merely re-detects. It moves only when the row is genuinely NEW (a fresh insert)
# or MATERIALLY CHANGED (the provider restated the bar: a different fact is known from now on). The adjusted-price jitter of ordinary dividends leaves
# the ratio of two adjacent closes unchanged to ~1e-12, so the tolerance below cannot be tripped by it. See docs/research/LAB_DISCONTINUITY_PROVENANCE.md.
RATIO_RELATIVE_TOLERANCE = 1e-4
_UPSERT_DISCONTINUITIES = f"""
    INSERT INTO price_discontinuities (symbol, date, kind, prev_close, close, ratio, detected_at) VALUES %s
    ON CONFLICT (symbol, date, kind) DO UPDATE SET prev_close = EXCLUDED.prev_close, close = EXCLUDED.close, ratio = EXCLUDED.ratio,
        detected_at = CASE WHEN (price_discontinuities.ratio IS NULL) <> (EXCLUDED.ratio IS NULL)
                             OR abs(EXCLUDED.ratio - price_discontinuities.ratio) > {RATIO_RELATIVE_TOLERANCE} * greatest(abs(price_discontinuities.ratio), 1e-12)
                           THEN clock_timestamp() ELSE price_discontinuities.detected_at END"""


def upsert_discontinuities(cur, rows):
    """rows: (symbol, date, kind, prev_close, close, ratio). A new row is stamped with the real detection instant (clock_timestamp(), not the
    transaction start, which can precede the detection); an existing row keeps its stamp unless materially changed."""
    execute_values(cur, _UPSERT_DISCONTINUITIES, rows, template="(%s, %s, %s, %s, %s, %s, clock_timestamp())")


def prune_discontinuities(cur, seen):
    """Delete the rows a COMPLETE rebuild no longer detects (the provider restated them away). A later re-appearance is a new fact with a new stamp.
    Never called for a partial run (--test / --limit) or before every chunk succeeded."""
    cur.execute("CREATE TEMP TABLE _disc_seen (symbol VARCHAR(20), date DATE, kind VARCHAR(20)) ON COMMIT DROP")
    if seen:
        execute_values(cur, "INSERT INTO _disc_seen VALUES %s", sorted(seen))
    cur.execute("DELETE FROM price_discontinuities d WHERE NOT EXISTS "
                "(SELECT 1 FROM _disc_seen s WHERE s.symbol = d.symbol AND s.date = d.date AND s.kind = d.kind)")
    return cur.rowcount


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
        upsert_discontinuities(cur, rows)
    conn.commit()


SCAN_WRITER = "build_dataset"
SCAN_CODE_REF = "ml_training/data_preparation/build_dataset.py#scan@v1"


def record_failed_scan(session, started_at, reason, run_id):
    """Best effort, on its own connection (the scan's own transaction may be unusable). A failed scan claims no fingerprint, so it can never satisfy a reader."""
    try:
        c = psycopg2.connect(**ml_config.db_config)
        c.cursor().execute(
            "INSERT INTO price_discontinuity_scan (session_date, status, failure_reason, started_at, run_id, writer, code_ref) "
            "VALUES (%s, 'failed', %s, %s, %s, %s, %s)", (session, reason, started_at, run_id, SCAN_WRITER, SCAN_CODE_REF))
        c.commit()
        c.close()
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: could not record the failed scan ({reason}): {type(e).__name__}", file=sys.stderr)


def finish_scan(cur, session, started_at, run_id, input_claim):
    """The last step of a COMPLETE --replace run, inside the caller's transaction (the prune is already done): claim the price input fingerprinted at the START of
    the run and the discontinuity table as it is now. The database recomputes both and refuses the row if either differs (prices changed during the run, wrong
    session, wrong claim). A retry with an identical input and result is a no-op (ON CONFLICT DO NOTHING keeps the first completion time).
    Returns the new row's id, or None when an identical scan already existed."""
    cur.execute("SELECT n_rows, fingerprint FROM research_discontinuity_result_fingerprint()")
    n_found, result_fp = cur.fetchone()
    newest, n_symbols, n_rows, input_fp = input_claim
    cur.execute(
        "INSERT INTO price_discontinuity_scan (session_date, status, newest_bar, n_symbols, n_price_rows, input_fingerprint, n_discontinuities, result_fingerprint, "
        "started_at, run_id, writer, code_ref) VALUES (%s, 'complete', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (session_date, input_fingerprint, result_fingerprint) WHERE status = 'complete' DO NOTHING RETURNING id",
        (session, newest, n_symbols, n_rows, input_fp, n_found, result_fp, started_at, run_id, SCAN_WRITER, SCAN_CODE_REF))
    row = cur.fetchone()
    return row[0] if row else None


def main():
    ap = argparse.ArgumentParser(description="Build ml_breakout_dataset_v2 from stock_prices only")
    ap.add_argument("--test", nargs="+", help="only these symbols")
    ap.add_argument("--limit", type=int, help="first N symbols (debug)")
    ap.add_argument("--start-date", default="2018-01-01", help="earliest breakout date to include")
    ap.add_argument("--replace", action="store_true",
                    help="delete existing ml_breakout_dataset_v2 rows first and, after a complete run, drop discontinuities no longer detected "
                         "(rows still detected keep their first-detected timestamp)")
    ap.add_argument("--session", help="the US market session this run scans (YYYY-MM-DD). With a complete --replace run it records the append-only "
                                      "price_discontinuity_scan evidence the collector requires; without it no evidence is recorded (a manual rebuild)")
    args = ap.parse_args()

    conn = psycopg2.connect(**ml_config.db_config)
    cur = conn.cursor()
    complete_run = bool(args.replace and not args.test and not args.limit)
    session = date.fromisoformat(args.session) if args.session else None
    if session is not None and not complete_run:
        print("NOTE: --session is only used by a complete --replace run (no --test / --limit); no scan evidence will be recorded")
        session = None
    scan = None                                          # (started_at, run_id, input_claim) once the scan is open
    if complete_run and session is None:
        print("NOTE: no --session given: price_discontinuity_scan evidence will NOT be recorded for this run")
    if complete_run and session is not None:
        cur.execute("SELECT clock_timestamp()")
        started_at = cur.fetchone()[0]
        run_id = f"dsc-{session.isoformat()}-{started_at.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
        cur.execute("SELECT newest_bar, n_symbols, n_price_rows, fingerprint FROM research_price_input_fingerprint(%s)", (session,))
        claim = cur.fetchone()
        cur.execute("SELECT max(date) FROM stock_prices")                       # the detector reads the whole table, so the session's bar must be the newest in it
        newest_all = cur.fetchone()[0]
        conn.commit()
        if claim[0] != session or newest_all != session:
            print(f"FATAL: the newest stored price bar is {newest_all}, not the scan session {session}: refusing to scan "
                  f"(the session is not loaded, or a later bar is mixed in)", file=sys.stderr)
            record_failed_scan(session, started_at, "price_data_not_at_session", run_id)
            sys.exit(3)
        scan = (started_at, run_id, claim)
        print(f"Scan {run_id}: session {session}, {claim[2]} price rows, {claim[1]} symbols, input fingerprint {claim[3][:16]}")
    try:
        _build(args, conn, cur, scan, session)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        if scan is not None:
            record_failed_scan(session, scan[0], "scan_exception", scan[1])
        print(f"FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        raise
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _build(args, conn, cur, scan, session):
    if args.test:
        symbols = sorted(args.test)
    else:
        cur.execute("SELECT DISTINCT symbol FROM stock_prices WHERE symbol IS NOT NULL AND symbol <> '' ORDER BY symbol")
        symbols = [r[0] for r in cur.fetchall()]
        if args.limit:
            symbols = symbols[:args.limit]
    if args.replace and not args.test:
        cur.execute("DELETE FROM ml_breakout_dataset_v2")
        conn.commit()
        print("Cleared ml_breakout_dataset_v2 for full rebuild (price_discontinuities is NOT cleared: it keeps first-detection provenance)")
    sectors = load_sectors(conn)
    start = pd.Timestamp(args.start_date)
    t0, n_rows, n_disc, n_sym = time.time(), 0, 0, 0
    seen = set()
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
        seen.update((r.symbol, pd.Timestamp(r.date).date(), r.kind) for r in disc_all.itertuples())
        n_rows += len(ds_all); n_disc += len(disc_all); n_sym += len(chunk)
        print(f"  {n_sym}/{len(symbols)} symbols | {n_rows} samples | {n_disc} discontinuities | {time.time() - t0:.0f}s", flush=True)
    if args.replace and not args.test and not args.limit:
        pruned = prune_discontinuities(cur, seen)
        print(f"Pruned {pruned} discontinuities no longer detected; {len(seen)} detected rows kept their first-detected timestamp")
        if scan is not None:
            started_at, run_id, claim = scan
            try:
                wrote = finish_scan(cur, session, started_at, run_id, claim)
                conn.commit()                                   # the prune and the scan evidence become visible together, or not at all
            except psycopg2.Error as e:
                conn.rollback()
                print(f"FATAL: the scan evidence was REFUSED by the database ({getattr(e, 'pgcode', None)}): {str(e).splitlines()[0]}", file=sys.stderr)
                record_failed_scan(session, started_at, "input_changed_during_scan", run_id)
                sys.exit(1)
            print(f"Scan evidence {'recorded' if wrote is not None else 'already recorded for this exact input and result (no new row)'}: session {session}")
        else:
            conn.commit()
    print(f"Done: {n_rows} samples, {n_disc} discontinuities recorded, feature_set={pf.FEATURE_SET_VERSION}")


if __name__ == "__main__":
    main()
