"""Writes the research extract (see data.py) from a database, READ-ONLY. Self-contained (stdlib + psycopg2, no project imports) so the same file can be piped into any
Python that can reach the database, for example `docker exec -i <backend> python - < extract.py` (it reads the DB_* variables of that environment).

Modes
    python extract.py --out DIR [--start 2020-09-01]      write DIR/{prices,earnings}.csv.gz and DIR/extract_meta.json (connection from DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD)
    python extract.py --tar-stdout [--start ...]          the same three files as ONE tar stream on stdout (for a remote run)

Only symbols that have at least one earnings report with a reported EPS are exported. The transaction is READ ONLY and the session default is read only.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile


def _connect():
    import psycopg2
    conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
                            password=os.environ["DB_PASSWORD"], options="-c default_transaction_read_only=on")
    conn.set_session(readonly=True)
    return conn


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract(conn, out_dir, start):
    os.makedirs(out_dir, exist_ok=True)
    cur = conn.cursor()
    cur.execute("SELECT symbol, report_date, eps_estimate, eps_actual, surprise_pct FROM earnings_calendar WHERE eps_actual IS NOT NULL AND report_date >= %s ORDER BY symbol, report_date", (start,))
    rows = cur.fetchall()
    symbols = sorted({r[0] for r in rows})
    e_path = os.path.join(out_dir, "earnings.csv.gz")
    with gzip.GzipFile(e_path, "wb", mtime=0) as gz, io.TextIOWrapper(gz, encoding="utf-8", newline="") as tw:
        w = csv.writer(tw, lineterminator="\n")
        w.writerow(["symbol", "report_date", "eps_estimate", "eps_actual", "surprise_pct"])
        for r in rows:
            w.writerow([r[0], r[1].isoformat()] + ["" if x is None else format(float(x), ".6f") for x in r[2:]])
    p_path = os.path.join(out_dir, "prices.csv.gz")
    n, lo, hi = 0, None, None
    pc = conn.cursor(name="strategy_lab_prices")
    pc.itersize = 50000
    pc.execute("SELECT symbol, date, open, high, low, close, volume FROM stock_prices WHERE date >= %s AND symbol = ANY(%s) ORDER BY symbol, date", (start, symbols))
    with gzip.GzipFile(p_path, "wb", mtime=0) as gz, io.TextIOWrapper(gz, encoding="utf-8", newline="") as tw:
        w = csv.writer(tw, lineterminator="\n")
        w.writerow(["symbol", "date", "open", "high", "low", "close", "volume"])
        for sym, d, o, h, l, c, v in pc:
            w.writerow([sym, d.isoformat()] + ["" if x is None else format(float(x), ".6f") for x in (o, h, l, c)] + ["" if v is None else str(int(v))])
            n += 1
            lo, hi = (d if lo is None or d < lo else lo), (d if hi is None or d > hi else hi)
    meta = {"schema": "strategy_lab_extract_v1", "start": start, "prices_rows": n, "prices_symbols": len(symbols), "prices_min_date": lo.isoformat() if lo else None,
            "prices_max_date": hi.isoformat() if hi else None, "earnings_rows": len(rows), "prices_sha256": _sha(p_path), "earnings_sha256": _sha(e_path)}
    with open(os.path.join(out_dir, "extract_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, sort_keys=True)
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--start", default="2020-09-01")
    ap.add_argument("--tar-stdout", action="store_true")
    a = ap.parse_args(argv)
    conn = _connect()
    try:
        if a.tar_stdout:
            with tempfile.TemporaryDirectory() as td:
                extract(conn, td, a.start)
                with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as tf:
                    for name in ("prices.csv.gz", "earnings.csv.gz", "extract_meta.json"):
                        tf.add(os.path.join(td, name), arcname=name)
        else:
            if not a.out:
                ap.error("--out or --tar-stdout is required")
            print(json.dumps(extract(conn, a.out, a.start), indent=1, sort_keys=True))
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    main()
