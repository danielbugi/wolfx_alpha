"""For one +4h filing and one +0h filing: print the feed string, the page 'Accepted', and the SGML header ACCEPTANCE-DATETIME (SEC's raw record)."""
import gzip, os, re, urllib.request
import psycopg2

UA = os.environ["SEC_USER_AGENT"]
conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ["DB_PORT"], dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])
cur = conn.cursor()
cur.execute("SELECT source_ref, (payload->>'cik')::bigint, payload->>'form_type', published_at FROM market_event_revision WHERE published_at::date IN (DATE '2026-09-02', DATE '2026-09-03', DATE '2026-09-09', DATE '2026-09-25', DATE '2026-09-04') ORDER BY published_at")
rows = cur.fetchall()


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        b = r.read()
        return (gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b).decode("utf-8", "replace")


seen = 0
for acc, cik, form, pub in rows:
    if seen >= 4:
        break
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}"
    try:
        hdr = get(base + ".hdr.sgml")
    except Exception as e:
        print(acc, "hdr error", type(e).__name__)
        continue
    m = re.search(r"ACCEPTANCE-DATETIME>(\d+)", hdr)
    page = get(base + "-index.htm")
    a = re.search(r"Accepted</div>\s*<div class=\"info\">([^<]+)<", page)
    print(f"{acc} {form:5s} feed={pub:%Y-%m-%d %H:%M:%S}Z  header ACCEPTANCE-DATETIME={m.group(1) if m else '?'}  page Accepted={a.group(1).strip() if a else '?'}")
    seen += 1
