"""Read-only extract of the SEC raw facts and acceptance dates the filing-version-aware inflection study needs. Self-contained (stdlib + psycopg2), like extract_fundamentals.py.

Files: sec_facts.csv.gz (sec_fact: EVERY filing's own value, one row per filing/metric/period, for one symbol per company), sec_filings.csv.gz (accession -> acceptance date on the SEC's own US-Eastern
clock), sec_meta.json (row counts, ingestion coverage and sha256 of both files). The transaction is READ ONLY. One symbol per company: the universe symbol with the most price history, so preferred-share
lines of the same CIK do not duplicate a company.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import sys

FCOLS = ["symbol", "metric", "tag", "unit", "value", "period_start", "period_end", "fy", "fp", "form", "accession", "filed_date"]
ACOLS = ["accession", "accepted_date_et"]
SYMBOLS_SQL = """SELECT DISTINCT ON (m.cik) m.cik, m.symbol
                 FROM sec_symbol_cik m JOIN (SELECT DISTINCT cik FROM sec_quarter) q ON q.cik = m.cik
                      JOIN (SELECT symbol, COUNT(*) AS n FROM stock_prices GROUP BY symbol) p ON p.symbol = m.symbol
                 ORDER BY m.cik, p.n DESC, LENGTH(m.symbol), m.symbol"""


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


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(SYMBOLS_SQL)
        pairs = cur.fetchall()
        ciks = [int(c) for c, _ in pairs]
        cur.execute("SELECT COUNT(DISTINCT symbol) FROM stock_prices")
        universe = cur.fetchone()[0]
        symbols = [s for _, s in pairs]
        facts_sql = """SELECT m.symbol, f.metric, f.tag, f.unit, f.value::text, f.period_start, f.period_end, f.fy, f.fp, f.form, f.accession, f.filed_date
                       FROM sec_fact f JOIN sec_symbol_cik m ON m.cik = f.cik WHERE m.symbol = ANY(%s)
                       ORDER BY m.symbol, f.metric, f.period_end, f.filed_date, f.accession"""
        acc_sql = """SELECT accession, (accepted_at AT TIME ZONE 'America/New_York')::date FROM sec_filing WHERE accepted_at IS NOT NULL AND cik = ANY(%s) ORDER BY accession"""
        fpath, apath = os.path.join(a.out, "sec_facts.csv.gz"), os.path.join(a.out, "sec_filings.csv.gz")
        n_facts = _dump(conn, fpath, "f_facts", facts_sql, FCOLS, (symbols,))
        n_acc = _dump(conn, apath, "f_acc", acc_sql, ACOLS, (ciks,))
        meta = {"schema": "sec_facts_extract_v1", "symbols": len(pairs), "universe_symbols": universe, "coverage": round(len(pairs) / max(1, universe), 4), "fact_rows": n_facts, "filing_rows": n_acc,
                "facts_sha256": _sha(fpath), "filings_sha256": _sha(apath), "note": "sec_fact keeps every filing's own value (append-only); point-in-time resolution happens in inflection.pit"}
        with open(os.path.join(a.out, "sec_meta.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(meta, fh, indent=1, sort_keys=True)
        print(json.dumps(meta))
    finally:
        conn.rollback()
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
