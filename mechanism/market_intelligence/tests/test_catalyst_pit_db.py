"""Strict point-in-time visibility of the SEC catalyst feed against real Postgres. Availability timestamps are stamped by the database, so every test places
its decision cutoff BETWEEN two operations with the database clock and asserts what that decision could and could not have known: SEC acceptance, first
observed, database ingestion, classification and identifier-link availability must ALL precede the cutoff; later facts, later corrections, later
classifications, later links and later ticker changes never leak backwards.

The last tests show why the existing tracker reader (`paper_runner._catalysts`) cannot be pointed at this feed unchanged."""
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone

import psycopg2
import pytest

import mi_fixtures
from mi_fixtures import ROOT, conn, connect, mi_env  # noqa: F401
import catalyst_fixtures as C
from edgar_samples import CIK_A, CIK_B
from market_intelligence import catalyst_reader as CR
from market_intelligence import classification_store as cs
from market_intelligence import edgar_store as ES
from market_intelligence import first_seen_store as fss
from market_intelligence import filing_contract as F
from market_intelligence import identifier_links as il
from market_intelligence import store

UTC = timezone.utc
FAR = datetime(2026, 12, 31, tzinfo=UTC)


def vis(conn, symbols, cutoff, **kw):
    kw.setdefault("lookback_days", 400)             # the sample filings are from September; the real default (30 days) is exercised in the lookback test
    cur = conn.cursor()
    out = CR.visible_filings(cur, symbols, cutoff, **kw)
    conn.rollback()
    return out


def accs(rows):
    return [x["accession"] for x in rows]


def with_link(conn, sym="AAA", cik=CIK_A):
    C.apply_links(conn, [(cik, sym)])


# ---------------------------------------------------------------- 1. SEC acceptance vs our own ingestion
def test_a_filing_accepted_before_the_cutoff_but_ingested_after_it_is_invisible_to_that_decision(conn):
    with_link(conn)
    before = C.db_now(conn)                                                    # the decision is taken here; the filing is already public (accepted 2026-09-18) but unseen
    C.ingest(conn, [C.filing(1)])
    after = C.db_now(conn)
    assert vis(conn, ["AAA"], before) == []
    got = vis(conn, ["AAA"], after)
    assert accs(got) == [C.ACC.format(1)] and got[0]["symbol"] == "AAA" and got[0]["symbol_basis"] == "observed"


def test_every_availability_timestamp_is_returned_and_available_at_is_the_latest_of_them(conn):
    with_link(conn)
    C.ingest(conn, [C.filing(1)])
    row = vis(conn, ["AAA"], C.db_now(conn))[0]
    assert row["accepted_at"] == row["known_at"] and row["known_at"] < row["first_observed_at"] <= row["ingested_at"]
    assert row["available_at"] == max(row["known_at"], row["first_observed_at"], row["ingested_at"], row["link_recorded_at"])
    assert row["link_recorded_at"] < row["first_observed_at"]


def test_a_filing_with_no_first_seen_record_is_never_visible(conn):
    with_link(conn)
    f = C.filing(1)
    cur = conn.cursor()
    store.append_event_revision(cur, __import__("dataclasses").replace(f.draft, symbol=None))          # a fact written WITHOUT the first-seen log
    conn.commit()
    assert vis(conn, ["AAA"], FAR) == []


def test_only_observed_provenance_and_grade_a_or_b_facts_are_visible(conn):
    with_link(conn)
    f = C.filing(1)
    cur = conn.cursor()
    d = __import__("dataclasses").replace(f.draft, symbol=None, provenance="reconstructed", reconstruction_basis="backfilled from the archive")
    store.append_event_revision(cur, d)
    conn.commit()
    assert vis(conn, ["AAA"], FAR) == []


# ---------------------------------------------------------------- 1b. each gate on its own (the gates overlap in normal ingest, so these split the steps in time)
def _first_seen(conn, c, run="split"):
    cur = conn.cursor()
    fss.record_run(cur, [ES._observation(c)], run, "ref", apply=True)
    conn.commit()


def _fact(conn, c):
    cur = conn.cursor()
    store.append_event_revision(cur, ES._draft(c), declared_basis="vendor_published")
    conn.commit()


def test_gate_ingestion_alone_a_filing_first_seen_but_not_yet_stored_is_not_visible(conn):
    with_link(conn)
    c = C.filing(1)
    _first_seen(conn, c)
    mid = C.db_now(conn)
    _fact(conn, c)
    assert vis(conn, ["AAA"], mid) == [] and accs(vis(conn, ["AAA"], C.db_now(conn))) == [C.ACC.format(1)]


def test_gate_first_observed_alone_a_stored_fact_whose_first_sight_is_logged_later_is_not_visible(conn):
    with_link(conn)
    c = C.filing(1)
    _fact(conn, c)
    mid = C.db_now(conn)
    _first_seen(conn, c)
    assert vis(conn, ["AAA"], mid) == [] and accs(vis(conn, ["AAA"], C.db_now(conn))) == [C.ACC.format(1)]


def test_gate_revision_pin_alone_a_correction_is_invisible_until_ITS_first_sight_is_logged(conn):
    with_link(conn)
    c1, c2 = C.filing(1, "8-K", "9.01"), C.filing(1, "8-K", "2.02,9.01")
    _first_seen(conn, c1, "r1")
    _fact(conn, c1)
    _first_seen(conn, c2, "r2")                                              # the correction is first seen ...
    mid_a = C.db_now(conn)
    _fact(conn, c2)                                                          # ... then stored
    after = C.db_now(conn)
    assert vis(conn, ["AAA"], mid_a)[0]["revision"] == 1 and vis(conn, ["AAA"], after)[0]["revision"] == 2
    # and the other order: stored first, first-seen logged later -> still revision 1 until the log catches up
    c3 = C.filing(2, "8-K", "9.01")
    _first_seen(conn, c3, "r3")
    _fact(conn, c3)
    c4 = C.filing(2, "8-K", "5.02")
    _fact(conn, c4)
    mid_b = C.db_now(conn)
    _first_seen(conn, c4, "r4")
    got = {x["accession"]: x["revision"] for x in vis(conn, ["AAA"], mid_b)}
    assert got[C.ACC.format(2)] == 1 and {x["accession"]: x["revision"] for x in vis(conn, ["AAA"], C.db_now(conn))}[C.ACC.format(2)] == 2


def test_gate_provenance_alone_a_reconstructed_fact_is_hidden_even_when_it_was_first_seen_and_stored(conn):
    with_link(conn)
    c = C.filing(1)
    _first_seen(conn, c)
    cur = conn.cursor()
    d = __import__("dataclasses").replace(ES._draft(c), provenance="reconstructed", reconstruction_basis="replayed from the archive")
    store.append_event_revision(cur, d)
    conn.commit()
    assert vis(conn, ["AAA"], C.db_now(conn)) == []


# ---------------------------------------------------------------- 2. corrections (revisions)
def test_a_later_correction_is_invisible_to_an_earlier_decision_which_keeps_seeing_the_revision_it_could_know(conn):
    with_link(conn)
    C.ingest(conn, [C.filing(1, "8-K", "9.01")], run="run-1")
    t1 = C.db_now(conn)
    C.ingest(conn, [C.filing(1, "8-K", "2.02,9.01")], run="run-2")
    t2 = C.db_now(conn)
    a, b = vis(conn, ["AAA"], t1)[0], vis(conn, ["AAA"], t2)[0]
    assert (a["revision"], a["items"], a["classifications"]) == (1, ["9.01"], [])
    assert (b["revision"], b["items"], [c["label"] for c in b["classifications"]]) == (2, ["2.02", "9.01"], ["earnings_release"])


# ---------------------------------------------------------------- 3. classification availability
def test_a_classification_made_after_the_cutoff_does_not_exist_for_that_decision(conn):
    with_link(conn)
    C.ingest(conn, [C.filing(1)], classify=False)
    t1 = C.db_now(conn)
    cur = conn.cursor()
    cur.execute("SELECT id, event_key FROM market_event_revision")
    rid, ek = cur.fetchone()
    f = C.filing(1).fact
    cs.append_classification(cur, F.classify_by_items(f), {"rule_id": "items_rule.v1"}, rid, ek, "ref")
    conn.commit()
    t2 = C.db_now(conn)
    assert vis(conn, ["AAA"], t1)[0]["classifications"] == []
    late = vis(conn, ["AAA"], t2)[0]["classifications"]
    assert [c["label"] for c in late] == ["earnings_release"] and late[0]["classified_at"] > t1


def test_a_superseding_classification_replaces_the_old_one_only_for_decisions_after_it(conn):
    with_link(conn)
    C.ingest(conn, [C.filing(1)])
    t1 = C.db_now(conn)
    cur = conn.cursor()
    cur.execute("SELECT id, event_key FROM market_event_revision")
    rid, ek = cur.fetchone()
    cur.execute("SELECT id FROM catalyst_classification")
    old = cur.fetchone()[0]
    f = C.filing(1).fact
    cs.append_classification(cur, F.CatalystClassification(f.accession, f.content_hash(), "items_rule", "v2", "rule", "results_of_operations", supersedes=old),
                             {"rule_id": "items_rule.v2"}, rid, ek, "ref")
    conn.commit()
    t2 = C.db_now(conn)
    assert [c["label"] for c in vis(conn, ["AAA"], t1)[0]["classifications"]] == ["earnings_release"]
    assert [c["label"] for c in vis(conn, ["AAA"], t2)[0]["classifications"]] == ["results_of_operations"]


# ---------------------------------------------------------------- 4. identifier-link availability, ticker changes, missing mappings
def test_a_filing_is_unmapped_until_its_link_is_recorded_and_unmapped_filings_are_excluded_by_default(conn):
    C.ingest(conn, [C.filing(1)])
    t1 = C.db_now(conn)
    with_link(conn)
    t2 = C.db_now(conn)
    assert vis(conn, ["AAA"], t1) == []
    flagged = vis(conn, ["AAA"], t1, include_unmapped=True)
    assert len(flagged) == 1 and flagged[0]["symbol"] is None and flagged[0]["symbol_basis"] == "unmapped"
    assert accs(vis(conn, ["AAA"], t2)) == [C.ACC.format(1)]


def test_a_filer_missing_from_the_ticker_file_stays_unmapped_forever_and_is_never_guessed(conn):
    C.ingest(conn, [C.filing(1, cik=CIK_B)])
    with_link(conn, cik=CIK_A)
    assert vis(conn, ["AAA", "BBB"], FAR) == []
    assert vis(conn, ["AAA"], FAR, include_unmapped=True)[0]["symbol_basis"] == "unmapped"


def test_an_observed_link_does_not_reach_back_before_the_file_that_recorded_it(conn):
    C.ingest(conn, [C.filing(1, filed="2026-08-20", accepted="2026-08-20T13:00:00.000Z")])
    with_link(conn)                                                            # first ticker file dated 2026-09-01
    assert vis(conn, ["AAA"], FAR) == []                                       # filed before the first file: not mapped (and not reconstructed unless asked)


def test_a_reconstructed_link_is_excluded_by_default_and_flagged_when_asked_for(conn):
    C.ingest(conn, [C.filing(1, filed="2026-08-20", accepted="2026-08-20T13:00:00.000Z")])
    C.apply_links(conn, [(CIK_A, "AAA")], reconstruct=True)
    assert vis(conn, ["AAA"], FAR) == []
    got = vis(conn, ["AAA"], FAR, include_reconstructed_links=True)
    assert len(got) == 1 and got[0]["symbol_basis"] == "reconstructed"


def test_a_ticker_change_maps_each_filing_to_the_symbol_known_for_its_date_and_never_backwards_in_time(conn):
    C.apply_links(conn, [(CIK_A, "OLD")], snap_date=date(2026, 9, 1))
    C.ingest(conn, [C.filing(1, filed="2026-09-18", accepted="2026-09-18T20:05:11.000Z"),
                    C.filing(2, filed="2026-09-28", accepted="2026-09-28T13:00:00.000Z")], run="run-1")
    t_before_change = C.db_now(conn)
    C.apply_links(conn, [(CIK_A, "NEW")], snap_date=date(2026, 9, 25), run="links-2")           # OLD ends (absent_on 09-25), NEW begins
    t_after_change = C.db_now(conn)
    assert accs(vis(conn, ["OLD"], t_before_change)) == [C.ACC.format(1), C.ACC.format(2)]      # the decision then knew only OLD
    assert vis(conn, ["NEW"], t_before_change) == []
    assert accs(vis(conn, ["OLD"], t_after_change)) == [C.ACC.format(1)]                         # 09-18 < absent_on keeps OLD
    assert accs(vis(conn, ["NEW"], t_after_change)) == [C.ACC.format(2)]                         # 09-28 >= 09-25 is NEW
    assert vis(conn, ["OLD"], t_after_change)[0]["symbol"] == "OLD"


def test_a_symbol_reused_by_another_company_never_attaches_the_old_companys_filings(conn):
    C.apply_links(conn, [(CIK_A, "REUSE")], snap_date=date(2026, 9, 1))
    C.ingest(conn, [C.filing(1, filed="2026-09-10", accepted="2026-09-10T13:00:00.000Z")])
    C.apply_links(conn, [(CIK_B, "REUSE")], snap_date=date(2026, 9, 25), run="links-2")                  # company A drops the symbol, company B takes it
    C.ingest(conn, [C.filing(2, cik=CIK_B, filed="2026-09-28", accepted="2026-09-28T13:00:00.000Z")], run="run-2")
    got = vis(conn, ["REUSE"], C.db_now(conn))
    assert sorted((x["cik"], x["accession"]) for x in got) == [(CIK_A, C.ACC.format(1)), (CIK_B, C.ACC.format(2))]
    cut_between = C.db_now(conn)
    C.ingest(conn, [C.filing(3, filed="2026-09-29", accepted="2026-09-29T13:00:00.000Z")], run="run-3")   # a later filing of company A, dated after it lost the symbol
    assert C.ACC.format(3) not in accs(vis(conn, ["REUSE"], C.db_now(conn)))                            # does not attach to the symbol's new owner... or its old one
    assert accs(vis(conn, ["REUSE"], cut_between)) == [C.ACC.format(1), C.ACC.format(2)]


def test_share_class_spellings_resolve_to_one_listing(conn):
    C.apply_links(conn, [(CIK_A, "BRK-B")])
    C.ingest(conn, [C.filing(1)])
    for spelling in ("BRK.B", "brk-b", "BRK/B"):
        assert accs(vis(conn, [spelling], C.db_now(conn))) == [C.ACC.format(1)]


def test_a_filer_with_two_listed_classes_yields_a_row_for_each_class_the_caller_asks_about(conn):
    """Found by the real-SEC dry run: a CIK that carries GOOG and GOOGL was dropped as 'ambiguous'. An issuer-level filing concerns both classes."""
    C.apply_links(conn, [(CIK_A, "GOOGL"), (CIK_A, "GOOG")])
    C.ingest(conn, [C.filing(1)])
    both = vis(conn, ["GOOG", "GOOGL"], C.db_now(conn))
    assert sorted((x["accession"], x["symbol"]) for x in both) == [(C.ACC.format(1), "GOOG"), (C.ACC.format(1), "GOOGL")]
    assert [x["symbol"] for x in vis(conn, ["GOOGL"], C.db_now(conn))] == ["GOOGL"]


def test_a_symbol_the_file_gives_to_two_filers_at_once_attaches_to_neither_and_is_reported_ambiguous(conn):
    C.apply_links(conn, [(CIK_A, "TWICE"), (CIK_B, "TWICE")])
    C.ingest(conn, [C.filing(1), C.filing(2, cik=CIK_B)])
    assert vis(conn, ["TWICE"], C.db_now(conn)) == []
    flagged = vis(conn, ["TWICE"], C.db_now(conn), include_unmapped=True)
    assert sorted(x["symbol_basis"] for x in flagged) == ["ambiguous", "ambiguous"] and all(x["symbol"] is None for x in flagged)


def test_a_symbol_also_claimed_by_a_filer_with_no_filing_in_the_window_is_still_ambiguous(conn):
    """The other claimant has nothing to show in the window, so its links are loaded only because the reader asks for every link on the wanted symbol."""
    C.apply_links(conn, [(CIK_A, "TWICE"), (CIK_B, "TWICE")])
    C.ingest(conn, [C.filing(1)])                                                  # only company A filed
    assert vis(conn, ["TWICE"], C.db_now(conn)) == []
    assert [x["symbol_basis"] for x in vis(conn, ["TWICE"], C.db_now(conn), include_unmapped=True)] == ["ambiguous"]


# ---------------------------------------------------------------- 5. the answer for a cutoff is immutable
def test_the_answer_for_a_cutoff_never_changes_however_much_is_appended_afterwards(conn):
    with_link(conn)
    C.ingest(conn, [C.filing(1), C.filing(2, "10-Q", "")], run="run-1")
    cut = C.db_now(conn)
    snapshot = vis(conn, ["AAA"], cut)
    C.ingest(conn, [C.filing(1, "8-K", "5.02"), C.filing(3, "NT 10-K", "", filed="2026-09-19", accepted="2026-09-19T14:00:00.000Z")], run="run-2")
    C.apply_links(conn, [(CIK_A, "ZZZ")], snap_date=date(2026, 9, 10), run="links-2")
    cur = conn.cursor()
    cur.execute("SELECT id, event_key FROM market_event_revision WHERE event_key = %s AND revision = 1", ("sec|" + C.ACC.format(1),))
    rid, ek = cur.fetchone()
    first = C.filing(1).fact
    cs.append_classification(cur, F.CatalystClassification(first.accession, first.content_hash(), "analyst", "v1", "human", "reviewed_later"), {"reviewer": "r"}, rid, ek, "ref")
    conn.commit()
    assert vis(conn, ["AAA"], cut) == snapshot                                  # a new filing, a correction, a new ticker file and a new label changed nothing for `cut`
    assert vis(conn, ["AAA"], C.db_now(conn)) != snapshot                       # ...while the present is different, so the equality above is not vacuous


def test_a_lookback_window_bounds_how_old_a_filing_may_be(conn):
    C.apply_links(conn, [(CIK_A, "AAA")], snap_date=date(2026, 8, 1))              # early enough to cover both filing dates
    C.ingest(conn, [C.filing(1, filed="2026-08-25", accepted="2026-08-25T13:00:00.000Z"), C.filing(2, filed="2026-09-18", accepted="2026-09-18T20:05:11.000Z")])
    now = C.db_now(conn)                                                       # the database clock is the decision time: both filings were ingested before it
    days_old = (now.date() - date(2026, 9, 18)).days
    assert accs(vis(conn, ["AAA"], now, lookback_days=3650)) == [C.ACC.format(1), C.ACC.format(2)]
    assert accs(vis(conn, ["AAA"], now, lookback_days=days_old + 2)) == [C.ACC.format(2)]       # the 08-25 filing is older than the window
    assert vis(conn, ["AAA"], now, lookback_days=1) == []
    with pytest.raises(ValueError):
        vis(conn, ["AAA"], datetime(2026, 10, 16), lookback_days=30)
    with pytest.raises(ValueError):
        vis(conn, ["AAA"], now, lookback_days=0)


# ---------------------------------------------------------------- 6. a randomised cross-check against an independent restatement of the rules
def _oracle(conn, symbols, cutoff, lookback_days=30):
    """Recompute the visible set from the raw tables with plain Python (no SQL gating) and the pure link resolver."""
    cur = conn.cursor()
    cur.execute("SELECT event_key, revision, id, source_ref, known_at, ingested_at, payload, provenance, pit_grade, session_timing, published_at FROM market_event_revision WHERE source = 'sec_edgar'")
    revs = cur.fetchall()
    cur.execute("SELECT period_key, seq, observed_at FROM source_observation WHERE dataset = 'filing_presence'")
    obs = {(a, b): c for a, b, c in cur.fetchall()}
    cur.execute("SELECT l.id, l.cik, l.symbol, l.basis, l.valid_from, l.recorded_at, z.absent_on, z.recorded_at FROM identifier_link l LEFT JOIN identifier_link_end z ON z.link_id = l.id")
    links = [il.StoredLink(*x) for x in cur.fetchall()]
    cur.execute("SELECT revision_id, id, label, classified_at, supersedes_id FROM catalyst_classification")
    cls_rows = cur.fetchall()
    conn.rollback()
    best = {}
    for ek, rev, rid, acc, known, ing, pay, prov, grade, timing, pub in revs:
        o = obs.get((acc, rev))
        if prov != "observed" or grade not in ("A", "B") or known is None or known > cutoff or ing > cutoff or known <= cutoff - timedelta(days=lookback_days):
            continue
        if o is None or o > cutoff:
            continue
        if ek not in best or rev > best[ek][0]:
            best[ek] = (rev, rid, acc, pay)
    want = {il.canonical_symbol(s) for s in symbols}
    out = []
    for ek, (rev, rid, acc, pay) in best.items():
        on = date.fromisoformat(pay["filing_date"])
        syms, basis = il.symbols_as_of(links, int(pay["cik"]), on, cutoff)
        live = [c for c in cls_rows if c[0] == rid and c[3] <= cutoff and not any(s[4] == c[1] and s[3] <= cutoff for s in cls_rows)]
        for sym in syms:
            if basis != "observed" or il.canonical_symbol(sym) not in want or len(il.claimants(links, sym, on, cutoff)) > 1:
                continue
            out.append((acc, rev, sym, tuple(sorted(c[2] for c in live))))
    return sorted(out)


def test_the_reader_agrees_with_an_independent_oracle_at_every_checkpoint_of_a_random_history(conn):
    rng = random.Random(20261016)
    marks = []
    C.apply_links(conn, [(CIK_A, "AAA"), (CIK_B, "BBB")], snap_date=date(2026, 9, 1))
    forms = [("8-K", "2.02,9.01"), ("8-K", "5.02"), ("10-Q", ""), ("10-K", ""), ("NT 10-Q", ""), ("8-K/A", "2.02")]
    state = {}
    link_gen = 0
    for step in range(28):
        op = rng.choice(["new", "new", "new", "correct", "relink", "newfiler"])
        if op == "new" or not state:
            n = len(state) + 1
            form, items = rng.choice(forms)
            day = 1 + rng.randrange(25)
            cik = rng.choice([CIK_A, CIK_B])
            f = C.filing(n, form, items, filed=f"2026-09-{day:02d}", accepted=f"2026-09-{day:02d}T14:00:00.000Z", cik=cik)
            state[n] = (form, items, f"2026-09-{day:02d}", cik)
            C.ingest(conn, [f], run=f"s{step}")
        elif op == "correct":
            n = rng.choice(list(state))
            form, items, filed, cik = state[n]
            new_items = rng.choice(["2.02", "5.02", "1.03", "9.01"]) if form.startswith("8-K") else items
            if new_items != items:
                state[n] = (form, new_items, filed, cik)
                C.ingest(conn, [C.filing(n, form, new_items, filed=filed, accepted=f"{filed}T14:00:00.000Z", cik=cik)], run=f"s{step}")
        elif op == "relink":
            link_gen += 1
            sym = rng.choice(["AAA", "AAB", "AAC"])
            C.apply_links(conn, [(CIK_A, sym), (CIK_B, "BBB")], snap_date=date(2026, 9, 1) + timedelta(days=link_gen), run=f"l{step}")
        else:
            continue
        marks.append(C.db_now(conn))
    assert len(marks) >= 15
    checked = 0
    for m in marks + [marks[-1] + timedelta(seconds=5)]:
        for syms in (["AAA"], ["AAB"], ["AAC"], ["BBB"], ["AAA", "AAB", "AAC", "BBB"]):
            got = sorted((x["accession"], x["revision"], x["symbol"], tuple(sorted(c["label"] for c in x["classifications"]))) for x in vis(conn, syms, m, lookback_days=60))
            assert got == _oracle(conn, syms, m, 60), (m, syms)
            checked += 1
    assert checked >= 80


# ---------------------------------------------------------------- 7. the existing tracker reader against this feed (documented incompatibility)
def _old_reader(conn, symbols, cutoff, session=date(2026, 9, 18)):
    sys.path.insert(0, os.path.join(ROOT, "mechanism"))
    from research import paper_runner as P
    cur = conn.cursor()
    out = P.CandidateSource._catalysts(None, cur, session, symbols, cutoff)
    conn.rollback()
    return out


def test_the_existing_tracker_reader_shows_a_decision_a_filing_that_was_ingested_after_its_cutoff(conn):
    """The incompatibility: `known_at <= cutoff` is the only gate. A symbol-bearing event accepted before the cutoff but stored after it IS returned,
    although the decision (taken at `cutoff`) could not have seen it. The strict reader returns nothing for the same question."""
    with_link(conn)
    cutoff = C.db_now(conn)
    cur = conn.cursor()
    f = C.filing(1).fact
    store.append_event_revision(cur, F.to_event_draft(f, symbol="AAA", symbol_basis="observed"))       # ingested AFTER the cutoff, accepted before it
    conn.commit()
    old = _old_reader(conn, ["AAA"], cutoff)
    assert "AAA" in old and len(old["AAA"]["events"]) == 1                                              # leaked into a decision-time context
    assert vis(conn, ["AAA"], cutoff) == []                                                             # the strict reader does not


def test_sec_events_have_a_null_symbol_so_the_existing_reader_cannot_consume_them_by_accident(conn):
    with_link(conn)
    C.ingest(conn, [C.filing(1)])
    old = _old_reader(conn, ["AAA"], C.db_now(conn))
    assert old == {}                                                                                    # inert for the old reader: the feed is not silently active
    assert accs(vis(conn, ["AAA"], C.db_now(conn))) == [C.ACC.format(1)]
