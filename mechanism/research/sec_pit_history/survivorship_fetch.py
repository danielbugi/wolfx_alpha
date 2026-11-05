"""READ-ONLY SEC fetch for the survivorship bounds study (SURVIVORSHIP_BOUNDS_SPEC.md). No database is touched.

Runs inside the mechanism image (it imports data_updaters.sec_statements and inflection.pit/pipeline, the same code as the live engine):

    SEC_USER_AGENT must be in the environment (declared operator + contact; never printed or written anywhere)
    python survivorship_fetch.py --out /work/out --cache /work/cache --universe-ciks /work/universe_ciks.txt

Steps (cached; a re-run only fetches what is missing):
  1. EDGAR full-index form.gz, 2017 Q3 .. 2024 Q4: every Form 25-NSE / 25 / 15-12B filer and every 10-Q / 10-K filer.
  2. Candidates: a delisting-form filer that filed 10-Q/10-K in 2016-2024, is NOT a CIK in our universe, and stopped filing periodic reports within
     200 days of the delisting form (otherwise the form concerned another security class, e.g. notes or warrants).
  3. submissions JSON per candidate: SIC (6770 blank-check excluded), acceptance times, and the delisting reason from the filings around the date.
  4. companyfacts JSON per candidate: point-in-time inflection events (pit.pit_items + build_symbol_pit, 120-day stale guard) and EntityPublicFloat.
Outputs (small CSVs + meta.json): candidates.csv, events.csv, floats.csv.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, "/app/mechanism")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

DELIST_FORMS = {"25-NSE", "25", "15-12B"}
PERIODIC = {"10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT", "10-QT"}
MERGER_FORMS = {"DEFM14A", "DEFM14C", "SC TO-T", "SC 14D9", "SC TO-T/A", "SC 14D9/A", "PREM14A", "425", "S-4", "SC 13E3"}
STALE_DAYS = 120


class Sec:
    def __init__(self, ua: str, cache: str, max_rps: float = 4.0) -> None:
        if not ua or "@" not in ua:
            raise SystemExit("SEC_USER_AGENT must be set (operator name and contact e-mail); nothing was requested")
        self.ua, self.cache, self.gap, self._last, self.requests = ua, cache, 1.0 / max_rps, 0.0, 0
        os.makedirs(cache, exist_ok=True)

    def get(self, url: str, key: str) -> Optional[bytes]:
        path = os.path.join(self.cache, key)
        if os.path.exists(path):
            return open(path, "rb").read() or None
        for attempt in range(4):
            wait = self.gap - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            try:
                req = urllib.request.Request(url, headers={"User-Agent": self.ua, "Accept-Encoding": "gzip"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        data = gzip.decompress(data)
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    open(path, "wb").close()
                    return None
                if e.code in (403, 429, 500, 502, 503) and attempt < 3:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        open(path, "wb").write(data)
        return data


def quarters(start: Tuple[int, int], end: Tuple[int, int]):
    y, q = start
    while (y, q) <= end:
        yield y, q
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)


def parse_form_index(text: str):
    """Rows of EDGAR form.idx: (form, company, cik, date_filed). Fixed-width, columns separated by 2+ spaces."""
    started = False
    for line in text.splitlines():
        if line.startswith("-----"):
            started = True
            continue
        if not started or not line.strip():
            continue
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 5:
            continue
        form, company, cik, filed = parts[0], parts[1], parts[-3], parts[-2]
        try:
            yield form, company, int(cik), date.fromisoformat(filed)
        except ValueError:
            continue


def classify(recent: Dict, delist: date) -> Tuple[str, str]:
    """Reason class from the issuer's own filings in [delisting - 120 days, delisting + 30 days]."""
    forms, dates, items = recent.get("form", []), recent.get("filingDate", []), recent.get("items", [])
    lo, hi = delist - timedelta(days=120), delist + timedelta(days=30)
    seen_merger, seen_bk, seen_301, evidence = False, False, False, []
    for i, f in enumerate(forms):
        try:
            d = date.fromisoformat(dates[i])
        except (ValueError, IndexError):
            continue
        if not (lo <= d <= hi):
            continue
        it = items[i] if i < len(items) else ""
        if f in MERGER_FORMS or (f.startswith("8-K") and "2.01" in it):
            seen_merger = True
            evidence.append(f"{f}{' ' + it if it else ''} {d}")
        if f.startswith("8-K") and "1.03" in it:
            seen_bk = True
            evidence.append(f"{f} {it} {d}")
        if f.startswith("8-K") and "3.01" in it:
            seen_301 = True
            evidence.append(f"{f} {it} {d}")
    if seen_bk:
        return "bankruptcy", "; ".join(evidence[:4])
    if seen_merger:
        return "merger", "; ".join(evidence[:4])
    if seen_301:
        return "listing_failure", "; ".join(evidence[:4])
    if any(f == "15-12B" for f in forms if f):
        return "deregistration", ""
    return "unknown", ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--universe-ciks", required=True)
    ap.add_argument("--max-candidates", type=int, default=4000)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    sec = Sec(os.environ.get("SEC_USER_AGENT", ""), a.cache)
    universe = {int(x) for x in open(a.universe_ciks).read().split() if x.strip().isdigit()}

    delist: Dict[int, List[Tuple[date, str]]] = defaultdict(list)
    periodic_first: Dict[int, date] = {}
    periodic_last: Dict[int, date] = {}
    names: Dict[int, str] = {}
    for y, q in quarters((2016, 1), (2024, 4)):
        raw = sec.get(f"https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/form.idx", f"index/form_{y}Q{q}.idx")
        if not raw:
            continue
        for form, company, cik, filed in parse_form_index(raw.decode("latin-1")):
            if form in PERIODIC:
                periodic_first[cik] = min(periodic_first.get(cik, filed), filed)
                periodic_last[cik] = max(periodic_last.get(cik, filed), filed)
                names.setdefault(cik, company)
            elif form in DELIST_FORMS and (y, q) >= (2017, 3):
                delist[cik].append((filed, form))
                names.setdefault(cik, company)

    cands = []
    for cik, rows in delist.items():
        if cik in universe or cik not in periodic_last:
            continue
        rows.sort()
        d, form = rows[0]
        if periodic_last[cik] > d + timedelta(days=200):           # kept filing: the form concerned another security class
            continue
        cands.append((cik, d, form))
    cands = sorted(cands, key=lambda x: x[1])[: a.max_candidates]
    print(f"index: {len(delist)} delisting-form filers, {len(cands)} candidates after universe/other-class filters", flush=True)

    from data_updaters import sec_statements as SS
    from inflection import config as IC
    from inflection import pipeline as IP
    from inflection import pit as PIT

    out_c, out_e, out_f = [], [], []
    for n, (cik, d, form) in enumerate(cands, 1):
        sub_raw = sec.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", f"sub/{cik}.json")
        if not sub_raw:
            continue
        sub = json.loads(sub_raw)
        sic = str(sub.get("sic") or "")
        recent = (sub.get("filings") or {}).get("recent") or {}
        reason, evidence = classify(recent, d)
        row = {"cik": cik, "name": sub.get("name") or names.get(cik, ""), "sic": sic, "delist_date": d.isoformat(), "delist_form": form, "reason": reason,
               "reason_evidence": evidence, "first_periodic": periodic_first.get(cik, d).isoformat(), "last_periodic": periodic_last.get(cik, d).isoformat(),
               "excluded": "blank_check" if sic == "6770" else ""}
        out_c.append(row)
        if row["excluded"]:
            continue
        accepted = {}
        for i, accn in enumerate(recent.get("accessionNumber", [])):
            try:
                accepted[accn] = datetime.fromisoformat(recent["acceptanceDateTime"][i].replace("Z", "+00:00")).date()
            except (ValueError, KeyError, IndexError):
                pass
        cf_raw = sec.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", f"cf/{cik}.json")
        if not cf_raw:
            continue
        doc = json.loads(cf_raw)
        for fv in (((doc.get("facts") or {}).get("dei") or {}).get("EntityPublicFloat") or {}).get("units", {}).get("USD", []):
            out_f.append({"cik": cik, "end": fv.get("end"), "filed": fv.get("filed"), "value": fv.get("val")})
        try:
            facts = SS.parse_companyfacts(doc, cik)
        except Exception as e:  # noqa: BLE001
            row["excluded"] = f"parse_error:{type(e).__name__}"
            continue
        sector = "Real Estate" if sic == "6798" else "Financial Services" if sic[:2] in ("60", "61", "62", "63", "64", "67") else None
        items = [it for it in PIT.pit_items(facts, accepted, sector) if (it["available_at"] - it["feature"]["quarter"]).days <= STALE_DAYS]
        _, evs = IP.build_symbol_pit(str(cik), items, sector, None, None, None, date(2026, 11, 1), IC.METHOD_VERSION_SEC_PIT)
        for e in evs:
            out_e.append({"cik": cik, "quarter_end": e["quarter_end"].isoformat(), "event_type": e["event_type"], "severity": e["severity"],
                          "available_at": e["available_at"].isoformat()})
        if n % 100 == 0:
            print(f"{n}/{len(cands)} companies, {sec.requests} requests", flush=True)

    def dump(name, rows):
        with open(os.path.join(a.out, name), "w", newline="", encoding="utf-8") as fh:
            if rows:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
    dump("candidates.csv", out_c)
    dump("events.csv", out_e)
    dump("floats.csv", out_f)
    json.dump({"generated": datetime.utcnow().isoformat() + "Z", "candidates": len(out_c), "events": len(out_e), "requests": sec.requests,
               "reasons": {r: sum(1 for c in out_c if c["reason"] == r and not c["excluded"]) for r in ("bankruptcy", "merger", "listing_failure", "deregistration", "unknown")}},
              open(os.path.join(a.out, "meta.json"), "w"), indent=1)
    print(json.dumps({"candidates": len(out_c), "events": len(out_e), "requests": sec.requests}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
