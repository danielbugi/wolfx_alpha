"""Catalyst Intelligence Phase 1A: a small REAL-SEC dry run into a SCRATCH database, with a coverage and latency report.

SCRATCH ONLY. The script refuses to run unless the database it is connected to is named `scratch...` and PHASE1A_SCRATCH=1 is set; it never reads a
production table and never writes anywhere but the scratch database it was pointed at. It is an offline research run: nothing imports it, nothing
schedules it, and it is not part of the pipeline.

    SEC_USER_AGENT="<operator name> <real contact email>" PHASE1A_SCRATCH=1 DB_HOST=... DB_PORT=... DB_NAME=scratch_catalyst DB_USER=... DB_PASSWORD=... \\
      python ml_training/experiments/catalyst_phase1a_dryrun.py --symbols-file symbols.txt --since 2026-09-01 --polls 12 --interval 300 --out report.json

What it does (each step is reported):
  1. fetch the SEC ticker file, keep it as a source snapshot, record OBSERVED links (valid from today) and RECONSTRUCTED links (today's file projected back, labelled);
  2. resolve the requested symbols to CIKs (the unresolved ones are listed: they can never be matched);
  3. poll every CIK's submissions feed `--polls` times, `--interval` seconds apart, with the real transport (declared User-Agent, rate limit, retries);
     keep every distinct body as a snapshot; for each NEW filing fetch its SEC header record and reconcile the acceptance time (the feed was found 4 h late for 66% of a
     real sample, see acceptance_check); ingest the Phase 1A forms (first-seen log -> event -> rule labels), idempotently;
  4. LATENCY: for filings FIRST OBSERVED after the first poll, observed_at - accepted_at (the true lag lies within one poll interval below that);
  5. PIT AUDIT of everything stored, then the strict reader at a cutoff before the run (must return nothing) and at the end;
  6. idempotency: the last batch is ingested again and must write nothing.
The User-Agent (it contains a contact address) is never printed or written to the report.
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

import psycopg2  # noqa: E402

from market_intelligence import acceptance_check as AC  # noqa: E402
from market_intelligence import catalyst_reader as CR  # noqa: E402
from market_intelligence import catalyst_rules as R  # noqa: E402
from market_intelligence import edgar_collector as E  # noqa: E402
from market_intelligence import edgar_store as ES  # noqa: E402
from market_intelligence import identifier_links as IL  # noqa: E402
from market_intelligence.sec_transport import SecFetchError, SecTransport  # noqa: E402

UTC = timezone.utc
CODE_REF = "catalyst_phase1a_dryrun"


def log(*a):
    print(datetime.now(UTC).strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def connect():
    conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ["DB_PORT"], dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])
    cur = conn.cursor()
    cur.execute("SELECT current_database()")
    name = cur.fetchone()[0]
    if not name.startswith("scratch") or os.environ.get("PHASE1A_SCRATCH") != "1":
        raise SystemExit(f"REFUSED: this run writes to its database, which must be named scratch* (got {name!r}) and PHASE1A_SCRATCH=1 must be set")
    for t in ("market_event_revision", "catalyst_classification", "source_observation", "source_snapshot", "identifier_link", "identifier_link_end"):
        cur.execute("SELECT to_regclass(%s)", (t,))
        if cur.fetchone()[0] is None:
            raise SystemExit(f"REFUSED: scratch database lacks {t}; apply migrations 24, 25, 27, 28 and 37 to it first")
    conn.commit()
    return conn


def pct(vals, q):
    v = sorted(vals)
    return round(v[min(len(v) - 1, int(q * len(v)))], 1) if v else None


def poll_once(conn, cur, tr, cfg, ciks, since, run_key, rejected_all, hdr_stats, offsets):
    """One polling cycle: fetch every CIK's submissions, keep the snapshots, reconcile the acceptance time of each filing against its SEC header (a known filing reuses the stored
    header time; a new one costs one small request), then ingest. Returns (collection report, ingest result, the reconciled facts that were offered to the ingest)."""
    tr.captured.clear()
    rep = E.collect(cfg, tr, ciks, now=AC.collector_now(datetime.now(UTC)), clock=time.monotonic, sleep=time.sleep, forms=R.PHASE1A_FORMS, since=since)
    for fetched in tr.captured:
        cik = fetched.url.rsplit("CIK", 1)[1].split(".")[0].lstrip("0")
        ES.store_snapshot(cur, "sec_edgar", "submissions", cik, fetched.url, fetched.sha256, fetched.body, run_key, CODE_REF)
    conn.commit()                                       # the submissions snapshots stored above are evidence: keep them whatever happens next
    known_headers = ES.load_known_headers(cur)
    ready = []
    for c in rep.facts:
        acc = c.fact.accession
        hdr = known_headers.get(acc)
        if hdr is None:
            url_h = AC.header_url(c.fact.cik, acc)
            try:
                got_h = tr.fetch(url_h, capture=False)
                ES.store_snapshot(cur, "sec_edgar", "filing_header", acc, url_h, got_h.sha256, got_h.body, run_key, CODE_REF)
                hdr = AC.parse_header_acceptance(got_h.body.decode("utf-8", "replace"))
                hdr_stats["header_fetched"] += 1
            except (SecFetchError, AC.AcceptanceError) as e:
                hdr_stats["header_unavailable_retry_next_poll:" + type(e).__name__] += 1
                continue
        try:
            rec_c = AC.reconcile(c, hdr)
        except AC.AcceptanceError as e:
            hdr_stats["refused"] += 1
            rejected_all["acceptance: " + str(e)[:80]] += 1
            continue
        a = rec_c.draft.payload["acceptance"]
        if acc not in known_headers:
            hdr_stats["reconciled_" + a["source"]] += 1
            offsets[a["feed_minus_header_hours"]] += 1
        ready.append(rec_c)
    conn.commit()
    res = ES.ingest_facts(cur, ready, run_key, CODE_REF, apply=True, poll_subjects=[f"cik:{c}" for c in rep.ciks_complete],
                          poll_status="complete" if rep.complete else "partial", poll_detail={"fetch_errors": len(rep.fetch_errors)})
    conn.commit()
    return rep, res, ready


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols-file", required=True)
    ap.add_argument("--since", required=True, help="YYYY-MM-DD: only filings dated on/after this day")
    ap.add_argument("--polls", type=int, default=1)
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--max-rps", type=float, default=5.0)
    ap.add_argument("--snapshot-date", default=None, help="date of the ticker file (default: today, UTC)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    ua = os.environ.get("SEC_USER_AGENT", "")
    if not ua:
        raise SystemExit("SEC_USER_AGENT is required: '<operator name> <real contact email>' (SEC fair-access policy)")
    cfg = E.EdgarConfig(user_agent=ua, max_rps=args.max_rps)            # raises on a placeholder / missing contact
    del ua
    tr = SecTransport(cfg)
    conn = connect()
    cur = conn.cursor()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    since = date.fromisoformat(args.since)
    snap_date = date.fromisoformat(args.snapshot_date) if args.snapshot_date else datetime.now(UTC).date()
    cur.execute("SELECT clock_timestamp()")
    run_started = cur.fetchone()[0]
    conn.commit()
    report = {"run_id": run_id, "scratch_database": True, "since": args.since, "snapshot_date": snap_date.isoformat(), "started_at": run_started.isoformat(), "user_agent_declared": True}

    # 1. ticker file -> snapshot -> links
    got = tr.fetch(IL.TICKERS_URL)
    sid, created = ES.store_snapshot(cur, "sec_edgar", "company_tickers", "-", IL.TICKERS_URL, got.sha256, got.body, f"phase1a-{run_id}", CODE_REF)
    links = ES.refresh_links(cur, got.body, snap_date, sid, CODE_REF, reconstruct=True, apply=True)
    conn.commit()
    parsed = IL.parse_company_tickers(got.body)
    report["ticker_file"] = {"bytes": len(got.body), "sha256": got.sha256, "pairs": len(parsed.pairs), "rejected_entries": len(parsed.rejected), "symbols_on_several_ciks": len(parsed.symbols_on_several_ciks),
                             "links": links}
    log("links", links["plan"])

    # 2. symbols -> CIKs
    want = [s.strip().upper() for s in open(args.symbols_file, encoding="utf-8") if s.strip()]
    by_sym = {}
    for p in parsed.pairs:
        by_sym.setdefault(IL.canonical_symbol(p.symbol), set()).add(p.cik)
    ciks, unresolved, multi = {}, [], {}
    for s in want:
        found = by_sym.get(IL.canonical_symbol(s), set())
        if not found:
            unresolved.append(s)
        else:
            if len(found) > 1:
                multi[s] = sorted(found)
            for c in sorted(found):
                ciks.setdefault(c, []).append(s)
    report["symbols"] = {"requested": len(want), "resolved_to_a_cik": len(want) - len(unresolved), "unresolved": unresolved, "symbols_with_several_ciks": multi, "ciks": len(ciks)}
    log("symbols", report["symbols"]["requested"], "ciks", len(ciks), "unresolved", len(unresolved))

    # 3. polls
    polls, tot = [], Counter()
    rejected_all, errors_all = Counter(), Counter()
    form_counts = Counter()
    last_batch = []
    hdr_stats = Counter()
    offsets = Counter()
    for i in range(args.polls):
        if i:
            time.sleep(args.interval)
        t0 = datetime.now(UTC)
        rep, res, ready = poll_once(conn, cur, tr, cfg, sorted(ciks), since, f"phase1a-{run_id}-{i}", rejected_all, hdr_stats, offsets)
        last_batch = ready
        for r_ in rep.rejected:
            rejected_all[r_.reason[:90]] += 1
        for e in rep.fetch_errors.values():
            errors_all[e.split(":")[0] + ":" + e.split(":", 1)[1][:60]] += 1
        for f in rep.facts:
            form_counts[f.fact.form_type] += 1
        polls.append({"poll": i, "started": t0.isoformat(timespec="seconds"), "seconds": round((datetime.now(UTC) - t0).total_seconds(), 1), "ciks_complete": len(rep.ciks_complete),
                      "ciks_failed": len(rep.fetch_errors), "facts_parsed": len(rep.facts), "events_created": res.events_created, "revisions_created": res.revisions_created,
                      "unchanged": res.unchanged, "classifications_created": res.classifications_created, "ingest_errors": res.errors[:3], "counters": dict(rep.counters)})
        log("poll", i, polls[-1]["ciks_complete"], "ok", polls[-1]["ciks_failed"], "failed", polls[-1]["events_created"], "new events")
    report["polls"] = polls
    report["acceptance_reconciliation"] = {"counters": dict(hdr_stats), "feed_minus_header_hours_for_new_filings": {str(k): v for k, v in sorted(offsets.items())},
                                           "note": "feed_late = the submissions feed value was later than the SEC header record and the header instant was used"}
    report["rejected_records"] = dict(rejected_all)
    report["fetch_errors"] = dict(errors_all)
    report["transport"] = tr.stats.summary()

    # idempotency: the last batch again
    if last_batch:
        again = ES.ingest_facts(cur, last_batch, f"phase1a-{run_id}-again", CODE_REF, apply=True, poll_subjects=[], poll_status="complete")
        conn.commit()
        report["idempotency_reingest_of_last_batch"] = {"facts": again.seen, "events_created": again.events_created, "revisions_created": again.revisions_created,
                                                        "classifications_created": again.classifications_created, "observations_written": again.observations_written,
                                                        "unchanged": again.unchanged, "writes_nothing": again.events_created == again.revisions_created == again.classifications_created == again.observations_written == 0}

    # 4. coverage + latency from what was stored
    cur.execute("SELECT r.payload->>'form_type', r.session_timing, r.known_at, o.observed_at, r.ingested_at, o.run_key FROM market_event_revision r "
                "JOIN source_observation o ON o.period_key = r.source_ref AND o.seq = r.revision WHERE r.revision = 1 AND o.dataset = 'filing_presence'")
    rows = cur.fetchall()
    conn.rollback()
    first_poll_prefix = f"phase1a-{run_id}-0"
    new_after_first = [(f, t, k, ob, ing) for f, t, k, ob, ing, rk in rows if rk != first_poll_prefix and not rk.endswith("-again")]
    lag_min = [(ob - k).total_seconds() / 60 for _, _, k, ob, _ in new_after_first if ob >= run_started]
    pit_bad = [1 for _, _, k, ob, ing, _ in rows if not (k <= ob <= ing)]
    report["stored"] = {"filings": len(rows), "by_form": dict(Counter(r_[0] for r_ in rows)), "by_session_timing": dict(Counter(r_[1] for r_ in rows)), "parsed_by_form_all_polls": dict(form_counts),
                        "pit_violations_known_le_observed_le_ingested": len(pit_bad)}
    report["latency_minutes_observed_minus_accepted"] = {
        "n_first_observed_after_poll_0": len(lag_min), "p50": pct(lag_min, 0.5), "p90": pct(lag_min, 0.9), "max": pct(lag_min, 1.0), "min": pct(lag_min, 0.0),
        "poll_interval_minutes": round(args.interval / 60, 1),
        "reading": "the true acceptance->first-sight lag is in (value - one poll interval, value]; filings first observed in poll 0 are history and carry no latency"}
    # 5. mapping + strict reader
    cur.execute("SELECT count(*) FROM identifier_link WHERE basis='observed'")
    n_obs = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM identifier_link WHERE basis='reconstructed'")
    n_rec = cur.fetchone()[0]
    conn.rollback()
    cutoff_end = datetime.now(UTC)
    mapped_counts = Counter()
    sample_syms = sorted({s for v in ciks.values() for s in v})
    for label, kw in (("observed_links_only", {}), ("with_reconstructed_links", {"include_reconstructed_links": True}), ("with_unmapped", {"include_reconstructed_links": True, "include_unmapped": True})):
        rows_ = CR.visible_filings(cur, sample_syms, cutoff_end, lookback_days=max(1, (cutoff_end.date() - since).days + 2), **kw)
        conn.rollback()
        mapped_counts[label] = len(rows_)
        if label == "with_reconstructed_links":
            report["strict_reader_basis_counts"] = dict(Counter(x["symbol_basis"] for x in rows_))
    cur.execute("SELECT min(ingested_at) FROM market_event_revision")
    first_ingest = cur.fetchone()[0]
    conn.rollback()
    before_first = [] if first_ingest is None else CR.visible_filings(cur, sample_syms, first_ingest - timedelta(microseconds=1), lookback_days=3650, include_reconstructed_links=True, include_unmapped=True)
    conn.rollback()
    report["strict_reader"] = {"links_observed": n_obs, "links_reconstructed": n_rec, "visible_at_end": dict(mapped_counts),
                               "visible_one_microsecond_before_the_first_ingest_in_this_database": len(before_first), "must_be_zero": len(before_first) == 0}
    cur.execute("SELECT count(*) FROM source_snapshot")
    report["snapshots_stored"] = cur.fetchone()[0]
    cur.execute("SELECT kind, count(*), sum(byte_length), sum(length(body_gz)) FROM source_snapshot GROUP BY 1 ORDER BY 1")
    report["snapshot_storage_by_kind"] = {k: {"rows": n, "bytes_raw": int(b), "bytes_gzip": int(g)} for k, n, b, g in cur.fetchall()}
    if report["snapshots_stored"] == 0 and tr.stats.requests > 1:
        raise SystemExit("INVARIANT BROKEN: documents were fetched but no source snapshot was kept")
    cur.execute("SELECT status, count(*), sum(n_new_observations) FROM source_poll GROUP BY 1")
    report["poll_log"] = [list(r_) for r_ in cur.fetchall()]
    conn.rollback()
    report["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, sort_keys=True, default=str)
    log("report written", args.out)


if __name__ == "__main__":
    main()
