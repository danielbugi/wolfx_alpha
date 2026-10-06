"""READ-ONLY sector coverage observations (Slice 11, Part 4). Observational only: it changes no threshold and writes nothing.

Three evidence classes, kept apart in the output (`provenance` on every block):
  FROZEN_DEV   a read-only (set_session(readonly=True)) SELECT-only pass over the frozen Windows development database (daily_fundamentals,
               stock_prices). NOT production; the data stopped moving on its last loaded session.
  LIVE_VENDOR  the 2026-10-06 yfinance probe saved beside this file (yfinance_live_probe_2026-10-06.json), compared with the frozen sector.
  SYNTHETIC    the real cross-check run over hand-built (candidate, history) pairs: what the policy does, not what the world looks like.

Run from the lab worktree:  PYTHONPATH=mechanism python docs/research/evidence/sector_coverage_probe.py --env <path-to-.env> [--out file.json]
The .env is read for the connection only; no credential is ever printed."""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "mechanism"))

from data_updaters import sector_history_recorder as R                      # noqa: E402
from research.lab import sector_history as SH                              # noqa: E402
from research.lab import sector_provenance as SP                           # noqa: E402

STALE_DAYS = SP.SECTOR_MAX_AGE_DAYS
YF_TAXONOMY = {"Technology", "Financial Services", "Energy", "Healthcare", "Consumer Defensive", "Consumer Cyclical", "Communication Services",
               "Real Estate", "Utilities", "Industrials", "Basic Materials"}


def classify_stored(value):
    if value is None:
        return "null"
    if not isinstance(value, str) or not value.strip():
        return "blank"
    if value.strip().lower() == "unknown":
        return "unknown_label"
    return "available"


def frozen_dev(env_path):
    import psycopg2
    from dotenv import dotenv_values
    env = dotenv_values(env_path)
    conn = psycopg2.connect(host=env.get("DB_HOST", "127.0.0.1"), port=env.get("DB_PORT", "5432"), dbname=env["DB_NAME"], user=env["DB_USER"],
                            password=env["DB_PASSWORD"], connect_timeout=5)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT max(date) FROM stock_prices")
    last_price = cur.fetchone()[0]
    cur.execute("SELECT DISTINCT symbol FROM stock_prices WHERE date = %s", (last_price,))
    universe = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT DISTINCT ON (symbol) symbol, date, sector FROM daily_fundamentals ORDER BY symbol, date DESC")
    latest = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    cur.execute("SELECT symbol, count(DISTINCT sector) FILTER (WHERE sector IS NOT NULL AND btrim(sector) <> ''), "
                "count(*) FILTER (WHERE sector IS NULL OR btrim(sector) = ''), count(*) FROM daily_fundamentals GROUP BY symbol")
    hist = {r[0]: r[1:] for r in cur.fetchall()}
    conn.close()
    buckets, stale, values, offtax, examples = Counter(), 0, Counter(), Counter(), {}
    for sym in sorted(universe):
        row = latest.get(sym)
        if row is None:
            buckets["no_fundamentals_row"] += 1
            examples.setdefault("no_fundamentals_row", []).append(sym)
            continue
        d, sector = row
        kind = classify_stored(sector)
        age = (last_price - d).days
        if kind == "available" and age > STALE_DAYS:
            buckets["available_but_stale"] += 1
            stale += 1
        else:
            buckets[kind] += 1
        if kind == "available":
            values[sector.strip()] += 1
            if sector.strip() not in YF_TAXONOMY:
                offtax[sector.strip()] += 1
        if kind != "available":
            examples.setdefault(kind, []).append(sym)
    reclass = [s for s, (nd, nnull, n) in hist.items() if s in universe and nd > 1]
    flips = [s for s, (nd, nnull, n) in hist.items() if s in universe and nd >= 1 and nnull > 0]
    return {
        "provenance": "FROZEN_DEV (read-only SELECT on the frozen Windows development database; NOT production)",
        "last_loaded_price_session": last_price.isoformat(), "universe_symbols": len(universe),
        "latest_stored_sector_by_class": dict(buckets), "stale_threshold_days": STALE_DAYS,
        "stale_measured_against": "the dev DB's own last price session, not the wall clock",
        "distinct_sector_values": len(values), "top_values": values.most_common(15),
        "values_outside_the_yfinance_taxonomy": dict(offtax),
        "symbols_with_more_than_one_distinct_sector_in_history": len(reclass), "reclass_examples": sorted(reclass)[:10],
        "symbols_whose_history_mixes_set_and_missing": len(flips),
        "unavailable_examples": {k: v[:12] for k, v in examples.items()},
        "universe_symbols_with_a_dot_or_slash": sorted(x for x in universe if "." in x or "/" in x),
        "of_those_without_an_available_stored_sector": sorted(x for x in universe if ("." in x or "/" in x) and classify_stored((latest.get(x) or (None, None))[1]) != "available"),
    }


def live_vs_frozen(env_path):
    import psycopg2
    from dotenv import dotenv_values
    with open(os.path.join(HERE, "yfinance_live_probe_2026-10-06.json"), encoding="utf-8") as fh:
        doc = json.load(fh)
    env = dotenv_values(env_path)
    conn = psycopg2.connect(host=env.get("DB_HOST", "127.0.0.1"), port=env.get("DB_PORT", "5432"), dbname=env["DB_NAME"], user=env["DB_USER"],
                            password=env["DB_PASSWORD"], connect_timeout=5)
    conn.set_session(readonly=True, autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ON (symbol) symbol, sector FROM daily_fundamentals WHERE symbol = ANY(%s) ORDER BY symbol, date DESC",
                ([c["symbol"] for c in doc["cases"]],))
    stored = {r[0]: r[1] for r in cur.fetchall()}
    conn.close()
    out, rows = Counter(), []
    for c in doc["cases"]:
        raw = dict(c["raw"])
        raw.update({f"_pad{i}": None for i in range(max(0, c["n_keys"] - len(raw)))})
        o = R.dry_run(c["symbol"], R.SRC_YFINANCE, raw=raw)["outcome"]
        s = stored.get(c["symbol"])
        if c["symbol"] not in stored:
            rel = "not_in_frozen_dev"
        elif o["response_state"] == "sector":
            rel = ("agree" if (s or "").strip() == o["sector"] else ("frozen_has_no_sector_live_has_one" if classify_stored(s) != "available" else "DISAGREE"))
        elif o["response_state"] == "no_sector":
            rel = "both_no_sector" if classify_stored(s) != "available" else "LIVE_EXPLICIT_NO_SECTOR_vs_FROZEN_SECTOR"
        else:
            rel = "live_" + o["response_state"]
        out[rel] += 1
        rows.append({"symbol": c["symbol"], "live": o["sector"] or o["response_state"], "frozen": s, "relation": rel})
    return {"provenance": "LIVE_VENDOR (yfinance 2026-10-06, 40 symbols) compared with FROZEN_DEV daily_fundamentals", "tally": dict(out),
            "disagreements": [r for r in rows if r["relation"] not in ("agree", "both_no_sector")]}


def synthetic_matrix():
    """The real cross-check over (candidate evidence x history selection). Documents the policy; says nothing about the world."""
    t0 = date(2026, 3, 10)

    def ev(state, sector):
        return SP.SectorEvidence(state, state, sector, None)

    def hs(kind, state, sector):
        return SH.HistorySelection(kind, ev(state, sector), sector, 1, None, None, 0, ())
    cands = {"cand_Tech_fresh": ev(SP.OBSERVED_FRESH, "Technology"), "cand_Tech_stale": ev(SP.OBSERVED_STALE, "Technology"),
             "cand_no_sector": ev(SP.UNAVAILABLE, None), "cand_Energy_fresh": ev(SP.OBSERVED_FRESH, "Energy")}
    hists = {"hist_Tech_fresh": hs(SH.K_OBSERVED, SP.OBSERVED_FRESH, "Technology"), "hist_Tech_stale": hs(SH.K_OBSERVED, SP.OBSERVED_STALE, "Technology"),
             "hist_explicit_no_sector": hs(SH.K_EXPLICIT_NO_SECTOR, SP.UNAVAILABLE, None), "hist_no_history": hs(SH.K_NO_HISTORY, SP.UNAVAILABLE, None)}
    rows = []
    for cn, c in cands.items():
        for hn, h in hists.items():
            x = SH.crosscheck(c, h)
            rows.append({"candidate": cn, "history": hn, "relation": x.relation, "effective_state": x.effective.state, "usable": x.effective.fresh})
    return {"provenance": "SYNTHETIC (real SH.crosscheck over hand-built pairs)", "t0": t0.isoformat(), "pairs": rows}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    doc = {"generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "frozen_dev": frozen_dev(a.env),
           "live_vs_frozen": live_vs_frozen(a.env), "synthetic": synthetic_matrix(),
           "not_measurable_here": ["yfinance-only vs Tiingo-only vs both-agree vs both-disagree on the SAME symbols (needs live Tiingo access; not performed)",
                                   "the 'frozen_dev' sector has no per-row vendor label, so a vendor-vs-vendor split cannot be derived from it"]}
    text = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False, default=str)
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
