"""Compare EVERY stored filing's feed acceptance time with the filing's own SGML header ACCEPTANCE-DATETIME (Eastern wall clock). 1 request per filing, 3 per second."""
import gzip, json, os, re, time, urllib.request
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import psycopg2

UA = os.environ["SEC_USER_AGENT"]
NY = ZoneInfo("America/New_York")
conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ["DB_PORT"], dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])
cur = conn.cursor()
cur.execute("SELECT source_ref, (payload->>'cik')::bigint, payload->>'form_type', published_at FROM market_event_revision ORDER BY published_at")
rows = cur.fetchall()


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        b = r.read()
        return (gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b).decode("utf-8", "replace")


diff, by_form, errors, detail = Counter(), Counter(), 0, []
for acc, cik, form, pub in rows:
    try:
        hdr = get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}.hdr.sgml")
        m = re.search(r"ACCEPTANCE-DATETIME>(\d{14})", hdr)
        wall = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
        true_utc = wall.replace(tzinfo=NY).astimezone(timezone.utc)
        d = round((pub - true_utc).total_seconds() / 3600, 2)
        diff[d] += 1
        by_form[(form, d)] += 1
        detail.append((wall.strftime("%a %H:%M"), d)) if isinstance(detail, list) else None
    except Exception as e:  # noqa: BLE001
        errors += 1
    time.sleep(0.33)
print("filings compared:", len(rows) - errors, "errors:", errors)
print("feed minus header-derived UTC (hours):", dict(diff))
print("by form:", {f"{k[0]}:{k[1]:+}": v for k, v in sorted(by_form.items())})
late = [w for w, d in detail if d != 0]
ok = [w for w, d in detail if d == 0]
print("wall-clock hour (ET) of the shifted ones:", dict(sorted(Counter(w.split()[1][:2] for w in late).items())))
print("wall-clock hour (ET) of the exact ones:  ", dict(sorted(Counter(w.split()[1][:2] for w in ok).items())))
print("weekday of the shifted:", dict(Counter(w.split()[0] for w in late)), "| of the exact:", dict(Counter(w.split()[0] for w in ok)))
