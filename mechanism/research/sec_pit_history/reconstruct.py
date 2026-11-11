"""Offline, full-history reconstruction of the SEC filing-version-aware (point-in-time) inflection events: method inflection_v2_sec_pit, the SAME code the nightly step 8e runs
(inflection.pit.pit_items -> inflection.pipeline.build_symbol_pit), but over every quarter instead of the last 200 days. Reads only the read-only extract in data/sec (extract.sh); writes only
data/out. Nothing touches a database.

    python mechanism/research/sec_pit_history/reconstruct.py [--workers 6] [--limit N]

Outputs (data/out, git-ignored; the summary is committed as reconstruction.json):
  events.csv.gz      one row per base event (the composite 'limited repricing' event needs prices and a sector index, so it is not reconstructed here)
  features.csv.gz    one row per evaluated company-quarter (quarter, first-public date and kind, data quality, restated-since flag)
  reconstruction.json  counts, leakage audit, data quality and the check against the production run of 2026-11-02/03

Point in time: each quarter is evaluated with ONLY the filings accepted (US Eastern date) on or before its first-public date; pit.audit_leakage re-checks every provenance accession.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from typing import Any, Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from data_updaters import sec_statements as SS  # noqa: E402
from inflection import config as C  # noqa: E402
from inflection import pipeline as P  # noqa: E402
from inflection import pit as PIT  # noqa: E402

SEC = os.path.join(HERE, "data", "sec")
OUT = os.path.join(HERE, "data", "out")
ASOF = date(2026, 11, 2)                  # the session of the first full production run
HOLDOUT = (date(2023, 1, 1), date(2025, 4, 1))   # the reserved confirmation window (public dates); events in it are COUNTED here, never given an outcome


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _f(s: str):
    return float(s) if s not in ("", None) else None


def load() -> Tuple[Dict[str, List[Any]], Dict[str, date], Dict[str, str], Dict[str, Dict[str, str]]]:
    facts: Dict[str, List[Any]] = defaultdict(list)
    with gzip.open(os.path.join(SEC, "sec_facts.csv.gz"), "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            facts[r["symbol"]].append(SS.RawFact(r["metric"], r["tag"], r["unit"], float(r["value"]), date.fromisoformat(r["period_start"]) if r["period_start"] else None,
                                                 date.fromisoformat(r["period_end"]), int(r["fy"]) if r["fy"] else None, r["fp"] or None, r["form"], r["accession"],
                                                 date.fromisoformat(r["filed_date"])))
    accepted: Dict[str, date] = {}
    filing: Dict[str, Dict[str, str]] = {}
    with gzip.open(os.path.join(SEC, "sec_filings.csv.gz"), "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            accepted[r["accession"]] = date.fromisoformat(r["accepted_date_et"])
            filing[r["accession"]] = r
    sector: Dict[str, str] = {}
    with gzip.open(os.path.join(SEC, "sectors.csv.gz"), "rt", newline="") as fh:
        for r in csv.DictReader(fh):
            sector[r["symbol"]] = r["sector"]
    return dict(facts), accepted, sector, filing


def one(args) -> Dict[str, Any]:
    sym, facts, accepted, sector = args
    acc = {f.accession: accepted[f.accession] for f in facts if f.accession in accepted}
    pub = PIT.with_availability(facts, acc)
    items = PIT.pit_items(facts, acc, sector, None, PIT.known_quarters(pub, ASOF))
    feats, evs = P.build_symbol_pit(sym, items, sector, None, None, None, ASOF, C.METHOD_VERSION_SEC_PIT)
    leaks = PIT.audit_leakage(items, pub)
    fq = []
    for it in items:
        ft = it["feature"]
        fq.append({"symbol": sym, "quarter_end": ft["quarter"].isoformat(), "available_at": it["available_at"].isoformat(), "basis": it["availability_basis"], "accession": it["accession"],
                   "quality": ft["quality"], "score": None, "restated": any(n.startswith("restated_since:") for n in ft["notes"])})
    score = {f["quarter_end"].isoformat(): f["score"] for f in feats}
    for r in fq:
        r["score"] = score.get(r["quarter_end"])
    er = [{"symbol": e["symbol"], "quarter_end": e["quarter_end"].isoformat(), "event_type": e["event_type"], "strength": round(float(e["strength"]), 1), "severity": e["severity"],
           "score": e["score"], "available_at": e["available_at"].isoformat(), "availability_basis": e["availability_basis"], "data_quality": e["data_quality"],
           "fiscal_year": e["fiscal_year"], "fiscal_quarter": e["fiscal_quarter"]} for e in evs]
    return {"symbol": sym, "features": fq, "events": er, "leaks": leaks}


def write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    with gzip.GzipFile(path, "wb", mtime=0) as gz:
        import io
        with io.TextIOWrapper(gz, encoding="utf-8", newline="") as tw:
            w = csv.DictWriter(tw, fieldnames=list(rows[0].keys()), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)


def compare_with_production(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The production run evaluated quarters first public on or after 2026-04-17 (asof 2026-11-03 - 200 days). The same code on the same data must give the same events there."""
    since = date(2026, 4, 17)
    key = lambda r: (r["symbol"], r["quarter_end"], r["event_type"])  # noqa: E731
    with gzip.open(os.path.join(SEC, "prod_v2_events.csv.gz"), "rt", newline="") as fh:
        prod = {key(r): r for r in csv.DictReader(fh) if r["availability_basis"] in ("sec_acceptance", "sec_filed_date")}
    # base events whose quarter was first public in the production window (a base event's available_at IS the first-public date)
    mine = {key(r): r for r in events if date.fromisoformat(r["available_at"]) >= since}
    both = set(prod) & set(mine)
    same = [k for k in both if prod[k]["available_at"] == mine[k]["available_at"] and abs(float(prod[k]["strength"]) - mine[k]["strength"]) < 0.06 and prod[k]["severity"] == mine[k]["severity"]]
    return {"production_base_events": len(prod), "offline_base_events_in_window": len(mine), "matched_keys": len(both), "identical": len(same),
            "only_production": sorted(["|".join(k) for k in set(prod) - set(mine)])[:20], "only_offline": sorted(["|".join(k) for k in set(mine) - set(prod)])[:20],
            "n_only_production": len(set(prod) - set(mine)), "n_only_offline": len(set(mine) - set(prod))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    facts, accepted, sector, filing = load()
    syms = sorted(facts)[: a.limit] if a.limit else sorted(facts)
    jobs = [(s, facts[s], accepted, sector.get(s)) for s in syms]
    results: List[Dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for i, r in enumerate(ex.map(one, jobs, chunksize=8)):
            results.append(r)
            if (i + 1) % 200 == 0:
                print(f"{i + 1}/{len(jobs)}", flush=True)
    feats = [f for r in results for f in r["features"]]
    events = [e for r in results for e in r["events"]]
    leaks = [f"{r['symbol']}: {l}" for r in results for l in r["leaks"]]
    write_csv(os.path.join(OUT, "features.csv.gz"), feats)
    write_csv(os.path.join(OUT, "events.csv.gz"), events)

    def period(d: str) -> str:
        x = date.fromisoformat(d)
        return "holdout" if HOLDOUT[0] <= x < HOLDOUT[1] else "pre_holdout" if x < HOLDOUT[0] else "post_holdout"
    by_year = defaultdict(lambda: {"company_quarters": 0, "companies": set(), "events": 0, "event_companies": set()})
    for f in feats:
        y = by_year[f["available_at"][:4]]
        y["company_quarters"] += 1
        y["companies"].add(f["symbol"])
    for e in events:
        y = by_year[e["available_at"][:4]]
        y["events"] += 1
        y["event_companies"].add(e["symbol"])
    lags = sorted((date.fromisoformat(f["available_at"]) - date.fromisoformat(f["quarter_end"])).days for f in feats)
    pct = lambda p: lags[min(len(lags) - 1, int(p * len(lags)))]  # noqa: E731
    first_pub_time = Counter()
    for f in feats:
        t = (filing.get(f["accession"]) or {}).get("accepted_time_et")
        first_pub_time["unknown" if not t else "before_open" if t < "09:30:00" else "during_session" if t < "16:00:00" else "after_close"] += 1
    summary = {
        "schema": "sec_pit_reconstruction_v1", "asof": ASOF.isoformat(), "method_version": C.METHOD_VERSION_SEC_PIT,
        "inputs": {k: sha256(os.path.join(SEC, k)) for k in ("sec_facts.csv.gz", "sec_filings.csv.gz", "sectors.csv.gz", "symbols.csv.gz", "prod_v2_events.csv.gz", "universe.csv.gz")},
        "outputs": {k: sha256(os.path.join(OUT, k)) for k in ("features.csv.gz", "events.csv.gz")},
        "companies": len(syms), "companies_with_features": len({f["symbol"] for f in feats}), "company_quarters": len(feats), "base_events": len(events),
        "events_by_period": dict(Counter(period(e["available_at"]) for e in events)), "company_quarters_by_period": dict(Counter(period(f["available_at"]) for f in feats)),
        "events_by_type": dict(Counter(e["event_type"] for e in events)), "events_by_severity": dict(Counter(e["severity"] for e in events)),
        "leakage_violations": len(leaks), "leakage_examples": leaks[:10],
        "availability_basis": dict(Counter(f["basis"] for f in feats)), "first_public_time_of_day_et": dict(first_pub_time),
        "filing_lag_days": {"p05": pct(0.05), "p50": pct(0.5), "p95": pct(0.95), "max": lags[-1]},
        "partial_data_share": round(sum(f["quality"] != "ok" for f in feats) / max(1, len(feats)), 4),
        "restated_since_share": round(sum(f["restated"] for f in feats) / max(1, len(feats)), 4),
        "by_year": {y: {"company_quarters": v["company_quarters"], "companies": len(v["companies"]), "events": v["events"], "event_companies": len(v["event_companies"])}
                    for y, v in sorted(by_year.items())},
        "production_check": compare_with_production(events),
    }
    with open(os.path.join(HERE, "reconstruction.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)
    print(json.dumps({k: summary[k] for k in ("companies_with_features", "company_quarters", "base_events", "events_by_period", "leakage_violations", "production_check")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
