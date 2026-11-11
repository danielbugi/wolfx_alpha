"""Writes the right-tail study extract from a database, READ-ONLY. Self-contained (stdlib + psycopg2, no project imports) so it can be piped into any Python that reaches the
database, for example `docker exec -i <backend> python - --tar-stdout < extract.py > x.tar` (it reads the DB_* variables of that environment).

Files: prices.csv.gz (symbol,date,open,high,low,close,volume; the same layout as the strategy lab's), discontinuities.csv.gz (symbol,date,kind,ratio) and extract_meta.json with
their sha256. Unlike the strategy lab's extract, EVERY symbol is exported (not only those with earnings). The transaction and the session default are read only.
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
    cur.execute("SHOW transaction_read_only")
    if cur.fetchone()[0] != "on":
        raise SystemExit("refused: the session is not read only")
    d_path = os.path.join(out_dir, "discontinuities.csv.gz")
    cur.execute("SELECT symbol, date, kind, ratio FROM price_discontinuities ORDER BY symbol, date, kind")
    drows = cur.fetchall()
    with gzip.GzipFile(d_path, "wb", mtime=0) as gz, io.TextIOWrapper(gz, encoding="utf-8", newline="") as tw:
        w = csv.writer(tw, lineterminator="\n")
        w.writerow(["symbol", "date", "kind", "ratio"])
        for s, d, k, r in drows:
            w.writerow([s, d.isoformat(), k, "" if r is None else format(float(r), ".6f")])
    p_path = os.path.join(out_dir, "prices.csv.gz")
    n, syms, lo, hi = 0, set(), None, None
    pc = conn.cursor(name="right_tail_prices")
    pc.itersize = 50000
    pc.execute("SELECT symbol, date, open, high, low, close, volume FROM stock_prices WHERE date >= %s ORDER BY symbol, date", (start,))
    with gzip.GzipFile(p_path, "wb", mtime=0) as gz, io.TextIOWrapper(gz, encoding="utf-8", newline="") as tw:
        w = csv.writer(tw, lineterminator="\n")
        w.writerow(["symbol", "date", "open", "high", "low", "close", "volume"])
        for sym, d, o, h, l, c, v in pc:
            w.writerow([sym, d.isoformat()] + ["" if x is None else format(float(x), ".6f") for x in (o, h, l, c)] + ["" if v is None else str(int(v))])
            n += 1
            syms.add(sym)
            lo, hi = (d if lo is None or d < lo else lo), (d if hi is None or d > hi else hi)
    meta = {"schema": "right_tail_extract_v1", "start": start, "prices_rows": n, "prices_symbols": len(syms), "prices_min_date": lo.isoformat() if lo else None,
            "prices_max_date": hi.isoformat() if hi else None, "discontinuity_rows": len(drows), "prices_sha256": _sha(p_path), "discontinuities_sha256": _sha(d_path)}
    with open(os.path.join(out_dir, "extract_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, sort_keys=True)
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--tar-stdout", action="store_true")
    a = ap.parse_args(argv)
    conn = _connect()
    try:
        if a.tar_stdout:
            with tempfile.TemporaryDirectory() as td:
                extract(conn, td, a.start)
                with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as tf:
                    for name in ("prices.csv.gz", "discontinuities.csv.gz", "extract_meta.json"):
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
