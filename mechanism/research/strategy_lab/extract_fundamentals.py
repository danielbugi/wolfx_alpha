"""Read-only extract of the historical fundamentals the Fundamental Inflection Engine and its research use. Self-contained (stdlib + psycopg2), like extract.py, so it can be piped
into a container:  docker exec -i <backend> python - --tar-stdout < extract_fundamentals.py > f.tar

Files: quarterly.csv.gz (quarterly_fundamentals), valuation.csv.gz (daily_fundamentals: market cap, ratios, sector; one row per symbol and stored day), meta json with their sha256.
The transaction is READ ONLY.
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

QCOLS = ["symbol", "quarter", "fiscal_year", "fiscal_quarter", "revenue", "gross_profit", "operating_income", "net_income", "ebitda", "eps", "operating_cash_flow",
         "free_cash_flow", "capital_expenditures", "total_assets", "total_debt", "total_equity", "cash", "updated_at"]
VCOLS = ["symbol", "date", "market_cap", "shares_outstanding", "pe_ratio", "ps_ratio", "pb_ratio", "sector", "industry"]


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


def _fmt(x):
    if x is None:
        return ""
    if hasattr(x, "isoformat"):
        return x.isoformat()
    return str(x)


def _dump(conn, path, name, sql, cols, params=()):
    cur = conn.cursor(name=name)
    cur.itersize = 50000
    cur.execute(sql, params)
    n = 0
    with gzip.GzipFile(path, "wb", mtime=0) as gz, io.TextIOWrapper(gz, encoding="utf-8", newline="") as tw:
        w = csv.writer(tw, lineterminator="\n")
        w.writerow(cols)
        for row in cur:
            w.writerow([_fmt(x) for x in row])
            n += 1
    return n


def extract(conn, out_dir, start="2023-07-01"):
    os.makedirs(out_dir, exist_ok=True)
    q = os.path.join(out_dir, "quarterly.csv.gz")
    v = os.path.join(out_dir, "valuation.csv.gz")
    nq = _dump(conn, q, "fi_q", f"SELECT {', '.join(QCOLS)} FROM quarterly_fundamentals ORDER BY symbol, quarter", QCOLS)
    nv = _dump(conn, v, "fi_v", f"SELECT {', '.join(VCOLS)} FROM daily_fundamentals WHERE date >= %s ORDER BY symbol, date", VCOLS, (start,))
    meta = {"schema": "inflection_extract_v1", "start": start, "quarterly_rows": nq, "valuation_rows": nv, "quarterly_sha256": _sha(q), "valuation_sha256": _sha(v)}
    with open(os.path.join(out_dir, "fundamentals_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, sort_keys=True)
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--start", default="2023-07-01")
    ap.add_argument("--tar-stdout", action="store_true")
    a = ap.parse_args(argv)
    conn = _connect()
    try:
        if a.tar_stdout:
            with tempfile.TemporaryDirectory() as td:
                extract(conn, td, a.start)
                with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as tf:
                    for name in ("quarterly.csv.gz", "valuation.csv.gz", "fundamentals_meta.json"):
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
