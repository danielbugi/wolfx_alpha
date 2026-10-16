"""For filings listed by EDGAR's Atom feed in the last ~3 hours: compare the Atom acceptance instant with the submissions JSON acceptanceDateTime AS SERVED RIGHT NOW, by filing age."""
import gzip, json, os, re, sys, time, urllib.request
from datetime import datetime, timezone
from collections import defaultdict

UA = os.environ["SEC_USER_AGENT"]
UTC = timezone.utc


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        b = r.read()
        return gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b


now = datetime.now(UTC)
text = get("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&start=0&count=100&output=atom").decode("utf-8", "replace")
entries = []
for e in re.findall(r"<entry>.*?</entry>", text, re.S):
    link = re.search(r'/data/(\d+)/(\d{18})/', e)
    upd = re.search(r"<updated>([^<]+)</updated>", e)
    if link and upd:
        acc18 = link.group(2)
        entries.append((f"{acc18[:10]}-{acc18[10:12]}-{acc18[12:]}", int(link.group(1)), datetime.fromisoformat(upd.group(1)).astimezone(UTC)))
print("atom 8-K entries:", len(entries), "oldest", min(e[2] for e in entries).isoformat(timespec="minutes"), "newest", max(e[2] for e in entries).isoformat(timespec="minutes"))
rows = []
for acc, cik, when in sorted(entries, key=lambda e: e[2], reverse=True)[:60]:
    try:
        doc = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
    except Exception as ex:  # noqa: BLE001
        continue
    rec = doc["filings"]["recent"]
    if acc in rec["accessionNumber"]:
        i = rec["accessionNumber"].index(acc)
        feed = datetime.fromisoformat(rec["acceptanceDateTime"][i].replace("Z", "+00:00"))
        rows.append(((now - when).total_seconds() / 3600, round((feed - when).total_seconds() / 3600, 2)))
    time.sleep(0.25)
buckets = defaultdict(lambda: defaultdict(int))
for age, shift in rows:
    b = "age<1h" if age < 1 else "1-3h" if age < 3 else "3-6h" if age < 6 else "6-12h" if age < 12 else "12h+"
    buckets[b][shift] += 1
print("feed-minus-Atom hours by filing age (as served now):")
for b in ("age<1h", "1-3h", "3-6h", "6-12h", "12h+"):
    print("  ", b, dict(buckets[b]))
