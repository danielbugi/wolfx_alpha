"""Does the submissions feed's acceptanceDateTime differ from the filing header by +4h for OLD filings too? 3 large filers, filings spread over the whole 'recent' block (months to years back)."""
import gzip, json, os, re, time, urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

UA = os.environ["SEC_USER_AGENT"]
NY = ZoneInfo("America/New_York")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        b = r.read()
        return (gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b).decode("utf-8", "replace")


rows = []
for cik in (320193, 789019, 1018724):
    rec = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))["filings"]["recent"]
    idx = [i for i, f in enumerate(rec["form"]) if f in ("8-K", "10-Q", "10-K") and rec["acceptanceDateTime"][i]]
    pick = idx[:: max(1, len(idx) // 8)][:8]
    for i in pick:
        acc = rec["accessionNumber"][i]
        feed = datetime.fromisoformat(rec["acceptanceDateTime"][i].replace("Z", "+00:00"))
        try:
            hdr = get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}.hdr.sgml")
        except Exception:  # noqa: BLE001
            continue
        m = re.search(r"ACCEPTANCE-DATETIME>(\d{14})", hdr)
        if not m:
            continue
        true = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=NY).astimezone(timezone.utc)
        age_days = (datetime.now(timezone.utc) - true).days
        rows.append((age_days, round((feed - true).total_seconds() / 3600, 2)))
        time.sleep(0.3)
bk = defaultdict(lambda: defaultdict(int))
for age, sh in rows:
    b = "<30d" if age < 30 else "30-90d" if age < 90 else "90-365d" if age < 365 else "1y+"
    bk[b][sh] += 1
print("compared:", len(rows))
for b in ("<30d", "30-90d", "90-365d", "1y+"):
    print("  ", b, dict(bk[b]))
