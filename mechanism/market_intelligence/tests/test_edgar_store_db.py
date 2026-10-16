"""Migration 37 and edgar_store against real Postgres (throwaway schema): immutability, database-stamped times, snapshots, identifier links (first file,
repeat, ticker change, truncated / stale files), and the ingest of filings: idempotency, revisions, amendments, a failing record that must not spoil the
others, and the dry run that writes nothing."""
import hashlib
import os
from datetime import date

import psycopg2
import psycopg2.errors
import pytest

import mi_fixtures
from mi_fixtures import ROOT, conn, connect, mi_env  # noqa: F401
import catalyst_fixtures as C
from edgar_samples import CIK_A, CIK_B, NOW, doc, r
from market_intelligence import edgar_collector as E
from market_intelligence import edgar_store as ES
from market_intelligence import identifier_links as il

MIG = os.path.join(ROOT, "mechanism", "add_catalyst_ingest_tables.sql")


def _fails(cur, fn, exc=psycopg2.Error, match=None):
    cur.execute("SAVEPOINT s")
    with pytest.raises(exc) as e:
        fn()
    cur.execute("ROLLBACK TO SAVEPOINT s")
    if match:
        assert match in str(e.value), str(e.value)


# ---------------------------------------------------------------- the migration
def test_migration_37_is_re_runnable_and_creates_marked_objects(conn):
    cur = conn.cursor()
    cur.execute(open(MIG, encoding="utf-8").read())                          # second application on top of the fixture's first
    for t in ("source_snapshot", "identifier_link", "identifier_link_end"):
        cur.execute("SELECT obj_description(to_regclass(%s), 'pg_class')", (t,))
        assert cur.fetchone()[0] == "catalyst_ingest_migration_37"
    cur.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid = ANY(ARRAY['source_snapshot'::regclass, 'identifier_link'::regclass, 'identifier_link_end'::regclass]) "
                "AND NOT tgisinternal AND tgenabled = 'A'")
    assert cur.fetchone()[0] == 9


def test_migration_37_refuses_a_foreign_object_with_one_of_its_names(conn):
    cur = conn.cursor()
    cur.execute("CREATE SCHEMA foreign_x")
    cur.execute("SET search_path TO foreign_x")
    cur.execute("CREATE TABLE identifier_link (id int)")
    _fails(cur, lambda: cur.execute(open(MIG, encoding="utf-8").read()), match="migration 37 refused")


def test_all_three_tables_are_append_only_even_for_their_owner(conn):
    cur = conn.cursor()
    C.apply_links(conn, [(CIK_A, "AAA")])
    cur = conn.cursor()
    cur.execute("INSERT INTO identifier_link_end (link_id, absent_on, snapshot_id, code_ref) SELECT id, DATE '2026-09-02', snapshot_id, 'x' FROM identifier_link LIMIT 1")
    for t in ("source_snapshot", "identifier_link", "identifier_link_end"):
        _fails(cur, lambda: cur.execute(f"UPDATE {t} SET id = id"), psycopg2.errors.IntegrityConstraintViolation, "append-only")
        _fails(cur, lambda: cur.execute(f"DELETE FROM {t}"), psycopg2.errors.IntegrityConstraintViolation, "append-only")
        _fails(cur, lambda: cur.execute(f"TRUNCATE {t} CASCADE"), psycopg2.errors.IntegrityConstraintViolation, "append-only")


def test_a_caller_cannot_choose_when_a_snapshot_a_link_or_a_closure_became_available(conn):
    C.apply_links(conn, [(CIK_A, "AAA")])
    cur = conn.cursor()
    cur.execute("SELECT clock_timestamp()")
    now = cur.fetchone()[0]
    cur.execute("INSERT INTO source_snapshot (source, kind, subject, url, sha256, byte_length, body_gz, first_fetched_at, run_key, code_ref) "
                "VALUES ('s','k','-','https://www.sec.gov/x', %s, 1, ''::bytea, TIMESTAMPTZ '2001-01-01 00:00+00', 'r', 'c') RETURNING first_fetched_at", ("a" * 64,))
    assert cur.fetchone()[0] >= now
    cur.execute("INSERT INTO identifier_link (cik, symbol, valid_from, basis, source, snapshot_id, recorded_at, code_ref) "
                "SELECT 99, 'ZZZ', DATE '2026-09-01', 'observed', 's', id, TIMESTAMPTZ '2001-01-01 00:00+00', 'c' FROM source_snapshot LIMIT 1 RETURNING recorded_at")
    assert cur.fetchone()[0] >= now


def test_link_constraints_observed_needs_a_start_reconstructed_has_none_symbols_are_upper_case(conn):
    C.apply_links(conn, [(CIK_A, "AAA")])
    cur = conn.cursor()
    base = "INSERT INTO identifier_link (cik, symbol, valid_from, basis, source, snapshot_id, code_ref) SELECT %s, %s, %s, %s, 's', id, 'c' FROM source_snapshot LIMIT 1"
    _fails(cur, lambda: cur.execute(base, (5, "AAA", None, "observed")), psycopg2.errors.CheckViolation)
    _fails(cur, lambda: cur.execute(base, (5, "AAA", date(2026, 9, 1), "reconstructed")), psycopg2.errors.CheckViolation)
    _fails(cur, lambda: cur.execute(base, (5, "aaa", date(2026, 9, 1), "observed")), psycopg2.errors.CheckViolation)
    _fails(cur, lambda: cur.execute(base, (0, "AAA", date(2026, 9, 1), "observed")), psycopg2.errors.CheckViolation)
    _fails(cur, lambda: cur.execute(base, (5, "AAA", date(2026, 9, 1), "guessed")), psycopg2.errors.CheckViolation)


# ---------------------------------------------------------------- snapshots
def test_snapshots_store_each_distinct_body_once_and_round_trip_exactly(conn):
    cur = conn.cursor()
    body = b'{"cik": "0000320193", "filings": {}}'
    a, made_a = ES.store_snapshot(cur, "sec_edgar", "submissions", "320193", "https://data.sec.gov/submissions/CIK0000320193.json", None, body, "r1", "c")
    b, made_b = ES.store_snapshot(cur, "sec_edgar", "submissions", "320193", "https://data.sec.gov/submissions/CIK0000320193.json", None, body, "r2", "c")
    c2, made_c = ES.store_snapshot(cur, "sec_edgar", "submissions", "320193", "https://data.sec.gov/submissions/CIK0000320193.json", None, body + b" ", "r2", "c")
    assert (made_a, made_b, made_c) == (True, False, True) and a == b != c2
    assert ES.read_snapshot(cur, a) == body
    cur.execute("SELECT sha256, byte_length FROM source_snapshot WHERE id = %s", (a,))
    assert cur.fetchone() == (hashlib.sha256(body).hexdigest(), len(body))


def test_a_snapshot_that_misdescribes_its_body_is_refused(conn):
    cur = conn.cursor()
    with pytest.raises(ValueError, match="does not match"):
        ES.store_snapshot(cur, "sec_edgar", "submissions", "1", "https://data.sec.gov/x", "0" * 64, b"abc", "r", "c")
    assert C.count(conn, "source_snapshot") == 0


# ---------------------------------------------------------------- identifier links in the database
def test_the_first_file_records_observed_links_and_a_repeat_changes_nothing(conn):
    out = C.apply_links(conn, [(CIK_A, "AAA")])
    assert out["links_created"] == 1201 and out["links_ended"] == 0 and C.count(conn, "identifier_link") == 1201
    again = C.apply_links(conn, [(CIK_A, "AAA")], run="links-2")
    assert again["links_created"] == 0 and again["links_unchanged"] == 1201 and C.count(conn, "identifier_link") == 1201 and C.count(conn, "source_snapshot") == 1


def test_a_ticker_change_ends_the_old_pair_and_opens_the_new_one(conn):
    C.apply_links(conn, [(CIK_A, "OLD")], snap_date=date(2026, 9, 1))
    out = C.apply_links(conn, [(CIK_A, "NEW")], snap_date=date(2026, 9, 20), run="links-2")
    assert out["links_created"] == 1 and out["links_ended"] == 1
    cur = conn.cursor()
    cur.execute("SELECT l.symbol, l.valid_from, e.absent_on FROM identifier_link l LEFT JOIN identifier_link_end e ON e.link_id = l.id WHERE l.cik = %s ORDER BY l.id", (CIK_A,))
    assert cur.fetchall() == [("OLD", date(2026, 9, 1), date(2026, 9, 20)), ("NEW", date(2026, 9, 20), None)]


def test_a_truncated_ticker_file_changes_nothing(conn):
    C.apply_links(conn, [], body=C.ticker_file([], filler=2000))
    before = (C.count(conn, "identifier_link"), C.count(conn, "identifier_link_end"))
    with pytest.raises(il.IdentifierError, match="truncated"):
        C.apply_links(conn, [], snap_date=date(2026, 9, 2), run="links-2", body=C.ticker_file([], filler=1400))
    conn.rollback()
    assert (C.count(conn, "identifier_link"), C.count(conn, "identifier_link_end")) == before


def test_a_stale_snapshot_date_and_a_garbled_file_are_refused_and_change_nothing(conn):
    C.apply_links(conn, [(CIK_A, "AAA")], snap_date=date(2026, 9, 10))
    with pytest.raises(il.IdentifierError, match="older"):
        C.apply_links(conn, [(CIK_A, "AAA")], snap_date=date(2026, 9, 9), run="links-2")
    conn.rollback()
    with pytest.raises(il.IdentifierError):
        C.apply_links(conn, [], snap_date=date(2026, 9, 11), run="links-3", body=b"<html>rate limited</html>")
    conn.rollback()
    assert C.count(conn, "identifier_link") == 1201 and C.count(conn, "identifier_link_end") == 0


def test_reconstructed_links_are_recorded_separately_labelled_and_without_a_start(conn):
    C.apply_links(conn, [(CIK_A, "AAA")], reconstruct=True)
    cur = conn.cursor()
    cur.execute("SELECT basis, count(*), count(valid_from) FROM identifier_link GROUP BY basis ORDER BY basis")
    assert cur.fetchall() == [("observed", 1201, 1201), ("reconstructed", 1201, 0)]


def test_a_dry_run_of_the_links_writes_nothing(conn):
    out = C.apply_links(conn, [(CIK_A, "AAA")], apply=False)
    assert out["applied"] is False and out["plan"]["new_links"] == 1201 and C.count(conn, "identifier_link") == 0


# ---------------------------------------------------------------- filings
def test_ingest_writes_event_first_seen_and_labels_and_leaves_the_event_symbol_null(conn):
    res = C.ingest(conn, [C.filing(1, "8-K", "2.02,9.01"), C.filing(2, "10-Q", ""), C.filing(3, "NT 10-K", "")])
    assert (res.events_created, res.revisions_created, res.unchanged, res.classifications_created, res.observations_written, res.errors) == (3, 0, 0, 3, 3, [])
    cur = conn.cursor()
    cur.execute("SELECT symbol, event_type FROM market_event ORDER BY event_key")
    assert cur.fetchall() == [(None, "regulatory")] * 3
    cur.execute("SELECT payload->>'symbol_basis', known_at_basis, pit_grade, provenance, known_at = published_at, known_at <= ingested_at FROM market_event_revision")
    assert set(cur.fetchall()) == {("resolved_at_read", "vendor_published", "A", "observed", True, True)}
    cur.execute("SELECT c.classifier, c.label FROM catalyst_classification c ORDER BY c.id")
    assert cur.fetchall() == [("items_rule", "earnings_release"), ("form_rule", "periodic_report"), ("form_rule", "late_filing_notice")]


def test_first_seen_is_recorded_before_the_fact_and_both_are_database_stamped(conn):
    C.ingest(conn, [C.filing(1)])
    cur = conn.cursor()
    cur.execute("SELECT o.observed_at, r.ingested_at, r.known_at FROM source_observation o JOIN market_event_revision r ON r.source_ref = o.period_key")
    observed, ingested, known = cur.fetchone()
    assert known < observed <= ingested                       # acceptance (Sept) precedes first sight, which precedes the stored fact


def test_ingesting_the_same_filings_again_writes_nothing(conn):
    facts = [C.filing(1), C.filing(2, "10-K", "")]
    C.ingest(conn, facts, run="run-1")
    before = {t: C.count(conn, t) for t in ("market_event", "market_event_revision", "source_observation", "catalyst_classification")}
    res = C.ingest(conn, facts, run="run-2")
    assert (res.events_created, res.revisions_created, res.unchanged, res.classifications_created, res.observations_written) == (0, 0, 2, 0, 0)
    assert {t: C.count(conn, t) for t in before} == before
    assert C.count(conn, "source_poll") == 2                  # each run is still recorded as having looked


def test_a_dry_run_reports_the_plan_and_writes_nothing(conn):
    res = C.ingest(conn, [C.filing(1), C.filing(2, "10-Q", "")], apply=False)
    assert res.applied is False and res.observations_written == 2
    assert all(C.count(conn, t) == 0 for t in ("market_event", "market_event_revision", "source_observation", "source_poll", "catalyst_classification"))


def test_a_corrected_filing_becomes_revision_2_with_its_own_first_seen_link_and_label(conn):
    C.ingest(conn, [C.filing(1, "8-K", "9.01")], run="run-1")
    res = C.ingest(conn, [C.filing(1, "8-K", "2.02,9.01")], run="run-2")             # EDGAR corrected the items of an accession it already showed us
    assert (res.events_created, res.revisions_created, res.classifications_created, res.observations_written) == (0, 1, 1, 1)
    cur = conn.cursor()
    cur.execute("SELECT revision, payload->'items' FROM market_event_revision ORDER BY revision")
    assert [x[0] for x in cur.fetchall()] == [1, 2]
    cur.execute("SELECT seq FROM source_observation ORDER BY seq")
    assert [x[0] for x in cur.fetchall()] == [1, 2]
    cur.execute("SELECT r.revision, c.label FROM catalyst_classification c JOIN market_event_revision r ON r.id = c.revision_id")
    assert cur.fetchall() == [(2, "earnings_release")]                                # the label belongs to the revision that carries Item 2.02, not to revision 1


def test_an_amendment_is_a_separate_filing_and_never_an_edit(conn):
    C.ingest(conn, [C.filing(1, "10-K", ""), C.filing(2, "10-K/A", "", filed="2026-09-25", accepted="2026-09-25T13:00:00.000Z")])
    cur = conn.cursor()
    cur.execute("SELECT event_key, payload->>'form_type', payload->>'amends_accession' FROM market_event_revision ORDER BY event_key")
    rows = cur.fetchall()
    assert rows == [("sec|0000320193-26-000001", "10-K", None), ("sec|0000320193-26-000002", "10-K/A", None)]       # the feed does not say what it amends; never guessed
    cur.execute("SELECT count(*) FROM market_event_revision WHERE revision > 1")
    assert cur.fetchone()[0] == 0


def test_one_failing_filing_is_reported_rolled_back_whole_and_does_not_stop_the_others(conn):
    far = E.parse_submissions(doc(CIK_A, [r(C.ACC.format(9), accepted="2099-01-01T00:00:00.000Z", filed="2099-01-01")]), CIK_A,
                              now=__import__("datetime").datetime(2099, 6, 1, tzinfo=__import__("datetime").timezone.utc))[0][0]
    res = C.ingest(conn, [C.filing(1), far, C.filing(2, "10-Q", "")])
    assert res.events_created == 2 and len(res.errors) == 1 and res.errors[0]["accession"] == C.ACC.format(9)
    cur = conn.cursor()
    cur.execute("SELECT period_key FROM source_observation ORDER BY period_key")
    assert [x[0] for x in cur.fetchall()] == [C.ACC.format(1), C.ACC.format(2)]       # the failing filing left no first-seen row behind
    cur.execute("SELECT n_new_observations, status FROM source_poll")
    assert cur.fetchone() == (2, "complete")


def test_malformed_and_foreign_records_are_rejected_before_anything_reaches_the_database():
    rows = [r("0000320193-26-000001"), r("garbage"), r("0000320193-26-000003", accepted="2026-09-18 20:05:11"), r("0000320193-26-000004", accepted=""),
            r("0000320193-26-000005", form="8-K", items="2.02,xx"), r("0000320193-26-000001", items="9.01")]
    facts, rejected, n = E.parse_submissions(doc(CIK_A, rows), CIK_A, now=NOW)
    assert [f.fact.accession for f in facts] == ["0000320193-26-000001"] and len(rejected) == 5
    with pytest.raises(E.EdgarError, match="refused as a whole"):
        E.parse_submissions(doc(CIK_B, [r("0000789019-26-000001")]), CIK_A, now=NOW)


# ---------------------------------------------------------------- the operational scripts around migration 37 (preflight gate, guarded rollback)
PRE = os.path.join(ROOT, "deploy", "db", "preflight_37.sql")
RB = os.path.join(ROOT, "deploy", "db", "rollback_37.sql")


def _run_script(cur, path):
    cur.connection.notices.clear()
    cur.execute(open(path, encoding="utf-8").read())
    return " ".join(cur.connection.notices)


def test_preflight_37_reports_absent_then_exact_and_stops_on_a_partial_or_foreign_state(conn):
    cur = conn.cursor()
    cur.execute("CREATE SCHEMA pf37")
    cur.execute("SET search_path TO pf37")
    assert "PREFLIGHT_37 OK state=ABSENT" in _run_script(cur, PRE)
    cur.execute(open(MIG, encoding="utf-8").read())
    assert "PREFLIGHT_37 OK state=EXACT" in _run_script(cur, PRE)
    cur.execute("DROP TABLE identifier_link_end")
    _fails(cur, lambda: cur.execute(open(PRE, encoding="utf-8").read()), match="partially applied")
    cur.execute("CREATE SCHEMA pf37b")
    cur.execute("SET search_path TO pf37b")
    cur.execute("CREATE TABLE source_snapshot (id int)")
    _fails(cur, lambda: cur.execute(open(PRE, encoding="utf-8").read()), match="not a migration-37 object")


def test_rollback_37_refuses_while_rows_exist_unless_the_loss_is_approved_and_removes_only_its_own_objects(conn):
    C.apply_links(conn, [(CIK_A, "AAA")])
    C.ingest(conn, [C.filing(1)])
    cur = conn.cursor()
    _fails(cur, lambda: cur.execute(open(RB, encoding="utf-8").read()), match="immutable evidence")
    assert C.count(conn, "identifier_link") == 1201
    cur = conn.cursor()
    cur.execute("SET LOCAL research.rollback_data_loss_approved = 'yes'")
    notices = _run_script(cur, RB)
    assert "migration 37 rolled back" in notices
    for t in ("source_snapshot", "identifier_link", "identifier_link_end"):
        cur.execute("SELECT to_regclass(%s)", (t,))
        assert cur.fetchone()[0] is None
    for t, expected in (("market_event", 1), ("market_event_revision", 1), ("source_observation", 1), ("catalyst_classification", 1)):         # the facts, first-seen rows and labels are untouched
        cur.execute(f"SELECT count(*) FROM {t}")
        assert cur.fetchone()[0] == expected, t
    cur.execute("SELECT to_regprocedure('research_ingest_guard()'), to_regprocedure('research_ingest_stamp()')")
    assert cur.fetchone() == (None, None)


def test_rollback_37_on_an_empty_schema_just_works_and_a_second_run_is_a_no_op(conn):
    cur = conn.cursor()
    assert "migration 37 rolled back (0 row(s) removed)" in _run_script(cur, RB)
    assert "migration 37 rolled back (0 row(s) removed)" in _run_script(cur, RB)
