"""Gate 1+2 evidence: feed acceptanceDateTime vs the filing's own SGML header, the index page and (for fresh filings) the Atom list, across daylight-saving transitions.
For every sampled filing: true_utc = header wall clock interpreted in America/New_York; feed_minus_true in hours; expected_offset = Eastern UTC offset at true_utc (4 in EDT, 5 in EST).
The correction rule claims: feed_minus_true is either 0 or exactly expected_offset. Any other value is an UNEXPLAINED discrepancy."""
import gzip, json, os, re, sys, time, urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UA = os.environ["SEC_USER_AGENT"]
NY = ZoneInfo("America/New_York")
UTC = timezone.utc
FILERS = (320193, 789019, 1018724, 1045810, 19617)          # Apple, Microsoft, Amazon, NVIDIA, JPMorgan
TRANSITIONS = [date(2022, 3, 13), date(2022, 11, 6), date(2023, 3, 12), date(2023, 11, 5), date(2024, 3, 10), date(2024, 11, 3), date(2025, 3, 9), date(2025, 11, 2), date(2026, 3, 8)]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        b = r.read()
        return (gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b).decode("utf-8", "replace")


cands = []
for cik in FILERS:
    rec = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))["filings"]["recent"]
    time.sleep(0.25)
    for i, f in enumerate(rec["form"]):
        if f not in ("8-K", "10-Q", "10-K", "8-K/A") or not rec["acceptanceDateTime"][i]:
            continue
        cands.append((cik, rec["accessionNumber"][i], f, rec["acceptanceDateTime"][i], rec["filingDate"][i]))
print("candidate filings in the five 'recent' blocks:", len(cands), "oldest filing date:", min(c[4] for c in cands))
sel = []
for t in TRANSITIONS:                                                     # up to 10 filings within 6 days either side of each transition
    near = [c for c in cands if abs((date.fromisoformat(c[4]) - t).days) <= 6]
    sel += near[:10]
step = max(1, len(cands) // 60)
sel += cands[::step][:60]                                                 # plus a spread over the whole range
seen, rows, errors = set(), [], 0
for cik, acc, form, feed_s, filed in sel:
    if acc in seen:
        continue
    seen.add(acc)
    try:
        hdr = get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}.hdr.sgml")
        m = re.search(r"ACCEPTANCE-DATETIME>(\d{14})", hdr)
        wall = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
        a, b = wall.replace(tzinfo=NY, fold=0), wall.replace(tzinfo=NY, fold=1)
        if a.utcoffset() != b.utcoffset():
            continue                                                       # ambiguous wall time: skipped, counted below
        true = a.astimezone(UTC)
        feed = datetime.fromisoformat(feed_s.replace("Z", "+00:00"))
        off = -a.utcoffset().total_seconds() / 3600
        page = get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.htm")
        pm = re.search(r"Accepted</div>\s*<div class=\"info\">([^<]+)<", page)
        page_wall = datetime.strptime(pm.group(1).strip(), "%Y-%m-%d %H:%M:%S") if pm else None
        rows.append({"acc": acc, "form": form, "true": true, "off": off, "diff": round((feed - true).total_seconds() / 3600, 3), "page_equals_header": page_wall == wall,
                     "near": min(TRANSITIONS, key=lambda t: abs((true.date() - t).days)), "days_from": (true.date() - min(TRANSITIONS, key=lambda t: abs((true.date() - t).days))).days})
    except Exception:  # noqa: BLE001
        errors += 1
    time.sleep(0.3)
print("compared:", len(rows), "errors:", errors, "| page 'Accepted' equals header wall clock in", sum(1 for r in rows if r["page_equals_header"]), "of", len(rows))
by = defaultdict(Counter)
for r in rows:
    by["EDT(4)" if r["off"] == 4 else "EST(5)"][r["diff"]] += 1
for k, v in by.items():
    print(f"  {k}: feed-minus-true hours -> {dict(v)}")
viol = [r for r in rows if r["diff"] not in (0.0, r["off"])]
print("UNEXPLAINED (diff not in {0, expected Eastern offset}):", len(viol), [(r["acc"], r["true"].isoformat(), r["diff"], r["off"]) for r in viol][:8])
print("filings within 1 day of a DST transition:", sum(1 for r in rows if abs(r["days_from"]) <= 1), "->",
      sorted((str(r["true"])[:16], r["off"], r["diff"]) for r in rows if abs(r["days_from"]) <= 1)[:12])
print("by transition (count, diffs):")
for t in TRANSITIONS:
    sub = [r for r in rows if r["near"] == t and abs(r["days_from"]) <= 6]
    print("  ", t, len(sub), dict(Counter((r["off"], r["diff"]) for r in sub)))
