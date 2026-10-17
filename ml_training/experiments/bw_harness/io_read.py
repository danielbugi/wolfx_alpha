"""Read-only database access and hashed file output. This is the ONLY module of the harness that touches a database, and it only SELECTs: the session is opened with default_transaction_read_only=on and the
constructor refuses to proceed unless the server confirms it. Output files are written to a directory chosen by the caller."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional

import numpy as np
import pandas as pd

from .config import Config
from .provenance import provenance_table


def connect_readonly():
    import psycopg2
    conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
                            options="-c default_transaction_read_only=on -c statement_timeout=1800000")
    cur = conn.cursor()
    cur.execute("SHOW default_transaction_read_only")
    if cur.fetchone()[0] != "on":
        conn.close()
        raise SystemExit("REFUSED: the session is not read-only")
    conn.rollback()
    return conn


def load_prices(conn) -> Dict[str, pd.DataFrame]:
    """All bars, one float64 frame per symbol, converted AS THEY ARE READ (numeric columns are cast in SQL so no Decimal objects are created: a naive read of 6.5 million rows needs several GB)."""
    sc = conn.cursor(name="bw_px")
    sc.itersize = 200000
    sc.execute("SELECT symbol, date, open::float8, high::float8, low::float8, close::float8, volume::float8 FROM stock_prices ORDER BY symbol, date")
    out: Dict[str, pd.DataFrame] = {}
    cols = ["date", "open", "high", "low", "close", "volume"]

    def frame(buf):
        a = pd.DataFrame(buf, columns=cols)
        a["date"] = pd.to_datetime(a["date"])
        return a

    cur_sym, buf = None, []
    for sym, *rest in sc:
        if sym != cur_sym:
            if cur_sym is not None:
                out[cur_sym] = frame(buf)
            cur_sym, buf = sym, []
        buf.append(rest)
    if cur_sym is not None:
        out[cur_sym] = frame(buf)
    conn.rollback()
    return out


def load_signal_keys(conn) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute("SELECT symbol, date, direction FROM ml_breakout_dataset_v2 ORDER BY symbol, date, direction")
    df = pd.DataFrame(cur.fetchall(), columns=["symbol", "date", "direction"])
    conn.rollback()
    df["date"] = pd.to_datetime(df["date"])
    df["direction"] = df["direction"].astype(int)
    return df


def load_sector_snaps(conn) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute("SELECT symbol, date, sector FROM daily_fundamentals WHERE sector IS NOT NULL AND sector <> ''")
    df = pd.DataFrame(cur.fetchall(), columns=["symbol", "date", "sector"])
    conn.rollback()
    df["date"] = pd.to_datetime(df["date"])
    return df


def input_fingerprint(conn) -> Dict[str, Any]:
    cur = conn.cursor()
    out: Dict[str, Any] = {}
    cur.execute("SELECT count(*), count(DISTINCT symbol), min(date), max(date), round(sum(close)::numeric, 2) FROM stock_prices")
    n, s, d0, d1, sc = cur.fetchone()
    out["stock_prices"] = {"rows": int(n), "symbols": int(s), "min_date": str(d0), "max_date": str(d1), "sum_close": float(sc)}
    cur.execute("SELECT count(*), min(date), max(date), min(created_at), max(created_at) FROM ml_breakout_dataset_v2")
    n, d0, d1, c0, c1 = cur.fetchone()
    out["ml_breakout_dataset_v2"] = {"rows": int(n), "min_date": str(d0), "max_date": str(d1), "created_at_min": str(c0), "created_at_max": str(c1)}
    cur.execute("SELECT count(*), min(date), max(date), count(DISTINCT symbol) FROM daily_fundamentals WHERE sector IS NOT NULL AND sector <> ''")
    n, d0, d1, s = cur.fetchone()
    out["daily_fundamentals_with_sector"] = {"rows": int(n), "min_date": str(d0), "max_date": str(d1), "symbols": int(s)}
    conn.rollback()
    return out


def load_realised(path: str, df_keys: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Realised net R (10 bps, standardised R, hold cap 60) of the five registered variants, from the committed experiment output (391 liquid symbols since 2017-03). Columns are renamed `realised_<variant>`."""
    raw = pd.read_csv(path, compression="gzip")
    rename = {}
    for c in raw.columns:
        if c.endswith("__cap60__r_std_net10"):
            rename[c] = "realised_" + c.split("__exit_")[1].split("__cap60")[0]
    out = raw[["symbol", "signal_date", "direction"] + list(rename)].rename(columns=rename)
    out["signal_date"] = pd.to_datetime(out["signal_date"])
    out["direction"] = out["direction"].astype(int)
    return out


def merge_realised(df: pd.DataFrame, realised: Optional[pd.DataFrame]) -> pd.DataFrame:
    cols = [c for c in (realised.columns if realised is not None else []) if c.startswith("realised_")]
    if realised is None:
        for c in ("realised_a_atr_stop_fixed_2r", "realised_a_atr_stop_fixed_3r", "realised_b_donchian_trail_10", "realised_c_atr_trail_3x", "realised_d_hybrid_atr_stop_to_donchian_10"):
            df[c] = np.nan
        return df
    m = df.merge(realised, how="left", left_on=["symbol", "signal_date", "direction"], right_on=["symbol", "signal_date", "direction"], validate="many_to_one")
    return m


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_default, allow_nan=False)


def _default(o: Any):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        v = float(o)
        return None if not np.isfinite(v) else v
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o).__name__)


def clean_for_json(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): clean_for_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean_for_json(v) for v in o]
    if isinstance(o, float) and not np.isfinite(o):
        return None
    if isinstance(o, np.floating):
        return None if not np.isfinite(float(o)) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def write_dataset(df: pd.DataFrame, path: str, chunk: int = 50000) -> Dict[str, Any]:
    """CSV (gzip, mtime 0) written in chunks while hashing the UNcompressed bytes: the hash identifies the content, not the compression. Columns are written in a fixed order, floats with 10 significant digits."""
    cols = sorted(df.columns, key=lambda c: (0 if c in ("symbol", "signal_date", "direction") else 1, c))
    h = hashlib.sha256()
    n = 0
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=6) as gz:
        for i in range(0, len(df), chunk):
            buf = io.StringIO()
            df.iloc[i:i + chunk][cols].to_csv(buf, index=False, header=(i == 0), float_format="%.10g", lineterminator="\n", date_format="%Y-%m-%d")
            b = buf.getvalue().encode("utf-8")
            h.update(b)
            gz.write(b)
            n += len(b)
    return {"file": os.path.basename(path), "rows": int(len(df)), "columns": len(cols), "bytes_uncompressed": n, "sha256_uncompressed": h.hexdigest(), "bytes_gzip": os.path.getsize(path)}


def write_json(obj: Any, path: str) -> str:
    text = json.dumps(clean_for_json(obj), sort_keys=True, indent=1, allow_nan=False, default=_default)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def provenance_manifest(df: pd.DataFrame) -> Dict[str, Dict[str, str]]:
    return provenance_table(df.columns)
