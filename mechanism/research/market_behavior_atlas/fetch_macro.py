"""Downloads the public macro data the Market Behavior Atlas uses, into data/macro/ (committed: public data, small). Re-running re-uses the cache.

    python mechanism/research/market_behavior_atlas/fetch_macro.py            # everything
    python mechanism/research/market_behavior_atlas/fetch_macro.py --only fred

Sources (no API key):
  * FRED `fredgraph.csv` - latest vintage of market series (yields, VIX, S&P 500, NASDAQ, credit spreads, Fed balance sheet, NFCI). Market prices and yields are not revised;
    NFCI and WALCL are (stated in the notebook).
  * ALFRED `alfredgraph.csv?vintage_date=D` - the series exactly as it was published on date D. The release date of month M's CPI / payrolls is found by binary search: the first
    date D on which observation M exists in the D vintage. The value in that vintage is the FIRST-RELEASE value (what the market saw), not today's revised value.
  * federalreserve.gov FOMC calendars - the date of every FOMC statement (press releases titled "...FOMC statement"); the decision itself comes from the target range (DFEDTARU).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import time
import urllib.request
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "macro")
UA = {"User-Agent": "Mozilla/5.0 (research; First Light market behavior atlas)"}
FRED_SERIES = {
    "SP500": "S&P 500 index (close; FRED licence: last 10 years only)", "NASDAQCOM": "NASDAQ Composite", "VIXCLS": "CBOE VIX",
    "DGS10": "10-year Treasury yield", "DGS2": "2-year Treasury yield", "DGS3MO": "3-month Treasury yield", "T10Y2Y": "10y-2y spread", "DFII10": "10-year TIPS real yield",
    "BAA10Y": "Moody's Baa corporate minus 10y Treasury (credit spread, long history)", "BAMLH0A0HYM2": "ICE BofA US high-yield OAS (FRED licence: last 3 years only)",
    "NFCI": "Chicago Fed National Financial Conditions Index (weekly; REVISED)", "WALCL": "Fed total assets (weekly; balance-sheet liquidity)",
    "DFEDTARU": "Fed funds target range, upper", "DFEDTARL": "Fed funds target range, lower", "DTWEXBGS": "Broad trade-weighted US dollar",
}
RELEASES = {"cpi": ("CPIAUCSL", ["CPILFESL"]), "jobs": ("PAYEMS", ["UNRATE"])}


def _get(url: str, tries: int = 4) -> str:
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - transient network errors are retried, the last one raised
            if k == tries - 1:
                raise
            time.sleep(2 + 3 * k)
    raise RuntimeError("unreachable")


def fetch_fred() -> None:
    os.makedirs(OUT, exist_ok=True)
    for sid in FRED_SERIES:
        txt = _get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}")
        with open(os.path.join(OUT, f"fred_{sid}.csv"), "w", encoding="utf-8", newline="\n") as f:
            f.write(txt.replace("\r\n", "\n"))
        time.sleep(0.5)
    json.dump({"retrieved": date.today().isoformat(), "series": FRED_SERIES}, open(os.path.join(OUT, "fred_meta.json"), "w"), indent=1)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ ALFRED
class Alfred:
    """`vintage(sid, d)` = {observation month: value} as published on date d (cached on disk: each (series, date) is asked once)."""

    def __init__(self, cache_path: str):
        self.path = cache_path
        self.cache: Dict[str, Dict[str, Optional[float]]] = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
        self.n_requests = 0

    def vintage(self, sid: str, d: date, cosd: date) -> Dict[str, Optional[float]]:
        key = f"{sid}|{d.isoformat()}|{cosd.isoformat()}"
        if key not in self.cache:
            txt = _get(f"https://alfred.stlouisfed.org/graph/alfredgraph.csv?id={sid}&cosd={cosd.isoformat()}&vintage_date={d.isoformat()}")
            rows = list(csv.reader(io.StringIO(txt)))[1:]
            self.cache[key] = {r[0]: (float(r[1]) if r[1] not in (".", "") else None) for r in rows if len(r) >= 2}
            self.n_requests += 1
            if self.n_requests % 25 == 0:
                self.save()
            time.sleep(0.25)
        return self.cache[key]

    def save(self) -> None:
        tmp = self.path + ".tmp"
        json.dump(self.cache, open(tmp, "w"), sort_keys=True)
        os.replace(tmp, self.path)


def _month(y: int, m: int) -> date:
    return date(y + (m - 1) // 12, (m - 1) % 12 + 1, 1)


def find_release(al: Alfred, sid: str, obs: date, lo: date, hi: date) -> Optional[date]:
    """The first date in [lo, hi] on which observation `obs` exists (non-missing) in the ALFRED vintage of that date; None when it never does (e.g. a cancelled release).
    Valid because publication is monotone: once released, an observation stays in every later vintage."""
    cosd = _month(obs.year, obs.month - 3)
    has = lambda d: al.vintage(sid, d, cosd).get(obs.isoformat()) is not None
    if not has(hi):
        return None
    while lo < hi:
        mid = lo + timedelta(days=(hi - lo).days // 2)
        if has(mid):
            hi = mid
        else:
            lo = mid + timedelta(days=1)
    return lo


def fetch_releases(start: Tuple[int, int] = (2016, 1), end: Tuple[int, int] = (2026, 9)) -> None:
    """One row per release: release date, the reference month, and the FIRST-RELEASE values of the headline series and its companions as published that day."""
    os.makedirs(OUT, exist_ok=True)
    al = Alfred(os.path.join(OUT, "alfred_cache.json"))
    for name, (sid, extra) in RELEASES.items():
        rows: List[Dict[str, object]] = []
        y, m = start
        while (y, m) <= end:
            obs = date(y, m, 1)
            lo, hi = _month(y, m + 1), _month(y, m + 3) - timedelta(days=1)        # published in the following two months (covers the 2025 shutdown delays)
            rd = find_release(al, sid, obs, lo, min(hi, date.today()))
            if rd is not None:
                cosd = _month(y, m - 14)
                v = al.vintage(sid, rd, cosd)
                prev, prev12 = _month(y, m - 1).isoformat(), _month(y, m - 12).isoformat()
                row = {"release_date": rd.isoformat(), "ref_month": obs.isoformat(), sid: v.get(obs.isoformat()), f"{sid}_prev": v.get(prev), f"{sid}_prev12": v.get(prev12)}
                for e in extra:
                    ve = al.vintage(e, rd, cosd)
                    row[e], row[f"{e}_prev"], row[f"{e}_prev12"] = ve.get(obs.isoformat()), ve.get(prev), ve.get(prev12)
                rows.append(row)
            else:
                rows.append({"release_date": "", "ref_month": obs.isoformat()})
            m += 1
            if m > 12:
                y, m = y + 1, 1
        al.save()
        keys = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("release_date", "ref_month"), k))
        with open(os.path.join(OUT, f"releases_{name}.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        print(name, sum(1 for r in rows if r["release_date"]), "releases;", al.n_requests, "requests so far", flush=True)


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ FOMC
def fetch_fomc(years=range(2016, 2027)) -> None:
    os.makedirs(OUT, exist_ok=True)
    pages = ["https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"] + [f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{y}.htm" for y in years if y <= 2020]
    links = set()
    for p in pages:
        links |= set(re.findall(r"monetary(20\d{6})a\.htm", _get(p)))
        time.sleep(0.5)
    rows = []
    for d in sorted(links):
        if int(d[:4]) not in years:
            continue
        html = _get(f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{d}a.htm")
        t = re.search(r"<title>([^<]*)", html)
        title = (t.group(1) if t else "").replace("Federal Reserve Board - ", "").strip()
        rows.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "title": title, "is_statement": "FOMC statement" in title})
        time.sleep(0.4)
    with open(os.path.join(OUT, "fomc_press_releases.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "title", "is_statement"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("fomc", len(rows), "press releases,", sum(r["is_statement"] for r in rows), "statements", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["fred", "fomc", "releases"])
    a = ap.parse_args()
    if a.only in (None, "fred"):
        fetch_fred()
        print("fred done", flush=True)
    if a.only in (None, "fomc"):
        fetch_fomc()
    if a.only in (None, "releases"):
        fetch_releases()
