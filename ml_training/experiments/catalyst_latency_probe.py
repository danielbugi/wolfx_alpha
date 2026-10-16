"""Catalyst Phase 1A: how long after SEC acceptance does a NEW filing become visible to us? (read-only probe; writes only its JSON report; touches no database)

Two surfaces are timed against the SEC's own acceptance instant:
  * discovery  the EDGAR "current filings" Atom list (one request per cycle for the whole market) -> `atom_listed_lag`
  * submissions the per-company submissions JSON that the collector polls -> `submissions_lag` (polled each cycle until the accession appears, then stopped)
The acceptance instant is the Atom entry's own `<updated>` (it carries an explicit UTC offset), cross-checked with the feed's `acceptanceDateTime`, which is reported as `feed_minus_atom_hours`.
Resolution is one cycle (`--cycle` seconds): a lag is an UPPER bound within one cycle. Filings already listed in the first cycle are history and are not timed.

    SEC_USER_AGENT="<operator name> <real contact email>" python ml_training/experiments/catalyst_latency_probe.py --cycles 20 --cycle 120 --out probe.json

The User-Agent (it contains a contact address) is never printed or written to the report.
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from market_intelligence import edgar_collector as E  # noqa: E402
from market_intelligence.sec_transport import SecFetchError, SecTransport  # noqa: E402

UTC = timezone.utc
ATOM = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={form}&company=&dateb=&owner=include&start=0&count=100&output=atom"
ENTRY = re.compile(r"<entry>.*?</entry>", re.S)


def parse_atom(text):
    """[(accession, cik, form, accepted_utc)] from the Atom list; entries it cannot read completely are skipped (counted by the caller via len)."""
    out = []
    for e in ENTRY.findall(text):
        link = re.search(r'href="[^"]*/data/(\d+)/(\d{18})/', e)
        upd = re.search(r"<updated>([^<]+)</updated>", e)
        form = re.search(r"<category[^>]*term=\"([^\"]+)\"", e)
        if not (link and upd):
            continue
        acc18 = link.group(2)
        acc = f"{acc18[:10]}-{acc18[10:12]}-{acc18[12:]}"
        try:
            when = datetime.fromisoformat(upd.group(1)).astimezone(UTC)
        except ValueError:
            continue
        out.append((acc, int(link.group(1)), form.group(1) if form else "?", when))
    return out


def pct(v, q):
    v = sorted(v)
    return round(v[min(len(v) - 1, int(q * len(v)))], 1) if v else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=20)
    ap.add_argument("--cycle", type=int, default=120)
    ap.add_argument("--forms", default="8-K,10-Q,10-K")
    ap.add_argument("--max-tracked", type=int, default=80)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    ua = os.environ.get("SEC_USER_AGENT", "")
    if not ua:
        raise SystemExit("SEC_USER_AGENT is required: '<operator name> <real contact email>' (SEC fair-access policy)")
    cfg = E.EdgarConfig(user_agent=ua, max_rps=3.0)
    del ua
    tr = SecTransport(cfg)
    forms = [f.strip() for f in args.forms.split(",") if f.strip()]
    seen, tracked = set(), {}
    started = datetime.now(UTC)
    for cycle_no in range(args.cycles):
        t0 = time.monotonic()
        now = datetime.now(UTC)
        for form in forms:
            try:
                text = tr.fetch(ATOM.format(form=form), capture=False).body.decode("utf-8", "replace")
            except SecFetchError as e:
                print(now.isoformat(timespec="seconds"), "atom error", type(e).__name__, file=sys.stderr, flush=True)
                continue
            for acc, cik, f, accepted in parse_atom(text):
                if acc in seen:
                    continue
                seen.add(acc)
                if cycle_no == 0 or len(tracked) >= args.max_tracked:
                    continue
                tracked[acc] = {"cik": cik, "form": f, "accepted_utc": accepted, "atom_first_seen": now, "submissions_first_seen": None, "feed_accepted": None}
        pending = [(a, v) for a, v in tracked.items() if v["submissions_first_seen"] is None]
        by_cik = {}
        for a, v in pending:
            by_cik.setdefault(v["cik"], []).append(a)
        for cik, accs in by_cik.items():
            try:
                doc = tr.get_json(E.submissions_url(cik), {})
            except SecFetchError:
                continue
            recent = doc.get("filings", {}).get("recent", {})
            idx = {a: i for i, a in enumerate(recent.get("accessionNumber", []))}
            for a in accs:
                if a in idx:
                    tracked[a]["submissions_first_seen"] = datetime.now(UTC)
                    tracked[a]["feed_accepted"] = recent["acceptanceDateTime"][idx[a]]
        print(datetime.now(UTC).strftime("%H:%M:%S"), f"cycle {cycle_no}: listed {len(seen)}, tracked {len(tracked)}, confirmed {sum(1 for v in tracked.values() if v['submissions_first_seen'])}", file=sys.stderr, flush=True)
        if cycle_no + 1 < args.cycles:
            time.sleep(max(0.0, args.cycle - (time.monotonic() - t0)))
    rows, atom_lag, sub_lag, shift = [], [], [], Counter()
    for a, v in tracked.items():
        al = (v["atom_first_seen"] - v["accepted_utc"]).total_seconds() / 60
        sl = (v["submissions_first_seen"] - v["accepted_utc"]).total_seconds() / 60 if v["submissions_first_seen"] else None
        fs = None
        if v["feed_accepted"]:
            fs = round((datetime.fromisoformat(v["feed_accepted"].replace("Z", "+00:00")) - v["accepted_utc"]).total_seconds() / 3600, 2)
            shift[fs] += 1
        atom_lag.append(al)
        if sl is not None:
            sub_lag.append(sl)
        rows.append({"accession": a, "form": v["form"], "accepted_utc": v["accepted_utc"].isoformat(), "atom_listed_lag_min": round(al, 1),
                     "submissions_lag_min": round(sl, 1) if sl is not None else None, "feed_minus_atom_hours": fs})
    report = {"started": started.isoformat(timespec="seconds"), "finished": datetime.now(UTC).isoformat(timespec="seconds"), "cycle_seconds": args.cycle, "cycles": args.cycles, "forms": forms,
              "new_filings_timed": len(tracked), "submissions_confirmed": len(sub_lag),
              "atom_listed_lag_minutes": {"p50": pct(atom_lag, 0.5), "p90": pct(atom_lag, 0.9), "max": pct(atom_lag, 1.0), "min": pct(atom_lag, 0.0)},
              "submissions_lag_minutes": {"p50": pct(sub_lag, 0.5), "p90": pct(sub_lag, 0.9), "max": pct(sub_lag, 1.0), "min": pct(sub_lag, 0.0)},
              "feed_minus_atom_hours": {str(k): v for k, v in sorted(shift.items())}, "transport": tr.stats.summary(), "filings": rows,
              "reading": "lags are upper bounds within one cycle; Atom <updated> is the acceptance instant (explicit offset)"}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, default=str)


if __name__ == "__main__":
    main()
