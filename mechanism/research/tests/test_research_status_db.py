"""The Slice 6 research status, driven end to end through the real owner CLI against throwaway Postgres schemas.

Every world is seeded with HISTORICAL stamps (status_world.py), so a test states exactly which sessions are observed, late, reconstructed,
back-dated, partial or missing, then reads the document back through `status --json`. Covers: empty / new history, partial coverage, reconstructed,
unknown / conflicting / malformed observations, missing sessions, late rows, post-cutoff rows, source-specific gaps, a complete observed history,
repeatability, independent-database reproducibility, SQL-vs-Python PIT boundary equivalence, read-only enforcement (including a real SELECT-only
role) and non-contradiction with the Slice 5 readiness contract."""
import hashlib
import json
import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import psycopg2
import pytest
from psycopg2 import extensions

import research.lab.dataset_contract as C
import research.lab.dataset_cli as CLI
import research.lab.research_status as ST
import research.lab.research_status_reader as SRD
import status_world as SW
from cli_world import PW_VAR, Cli, write_spec
from conftest import _connect_args
from dataset_world import CAL, CUTOFF, MAT, dataset_env, denv, fresh_denv  # noqa: F401
from test_dataset_cli_db import authored, hashes_of  # noqa: F401

LAST = SW.LAST_DUE
REGISTRY = ("dataset_manifest", "experiment_registration", "experiment_result")


def recent(n=60):
    return list(range(LAST - n + 1, LAST + 1))


def seed_recent(conn, n=60, **over):
    r = recent(n)
    base = dict(cand=r, market=r, rs=r, activation=r[0])
    base.update(over)
    return SW.seed_status_world(conn, **base)


@pytest.fixture
def senv():
    yield from dataset_env()


@pytest.fixture
def senv2():
    yield from dataset_env()


@pytest.fixture(scope="module")
def healthy_env():
    gen = dataset_env()
    schema, connect = next(gen)
    with connect() as conn:
        SW.healthy(conn)
    try:
        yield schema, connect
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def status(monkeypatch, schema, tmp_path, *extra, name="o", cli=None, spec=None):
    """(rc, doc|None, stdout, stderr, out_dir) of `status --json` against `schema`."""
    spec = spec or write_spec(tmp_path / f"spec_{name}")
    cli = cli or Cli(monkeypatch, schema=schema)
    out_dir = tmp_path / name
    rc, so, se = cli.run("status", "--spec", str(spec), "--out-dir", str(out_dir), "--json", *extra)
    return rc, (json.loads(so) if rc in (0, 4) and so.strip().startswith("{") else None), so, se, out_dir


def run_ok(monkeypatch, schema, tmp_path, *extra, name="o"):
    rc, doc, so, se, out = status(monkeypatch, schema, tmp_path, *extra, name=name)
    assert rc == 0, (so[:800], se[:800])
    assert ST.verify_status_hash(doc)
    return doc


def src(doc, name):
    return next(s for s in doc["sources"] if s["source"] == name)


def integ(doc, cid):
    return next(f for f in doc["integrity"] if f["id"] == cid)


def fail_ids(doc):
    return [f["id"] for f in doc["readiness_failures"]]


def table_fingerprint(connect, schema):
    """Row count + md5 of every table's content: proves a status run changed nothing."""
    out = {}
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY 1", (schema,))
        for (t,) in cur.fetchall():
            cur.execute(f'SELECT count(*), coalesce(md5(string_agg(x::text, \'|\' ORDER BY x::text)), \'\') FROM "{t}" x')
            out[t] = cur.fetchone()
        conn.rollback()
    return out


# ------------------------------------------------------------------ empty / new history
def test_an_empty_database_reports_nothing_captured_and_estimates_nothing(senv, monkeypatch, tmp_path):
    schema, connect = senv
    doc = run_ok(monkeypatch, schema, tmp_path)
    for name in ("candidates", "market", "sector", "stock_rs"):
        s = src(doc, name)
        assert s["capture_state"] == "never_captured" and s["dormant"] is True and s["observed_sessions"] == 0 and s["first_observed_session"] is None
    assert doc["remaining_history_estimate"]["remaining_sessions"] is None and doc["contract"]["evaluated"] is False
    assert "slice5_data_checks_not_evaluable" in fail_ids(doc) and doc["predictive_edge_claim"] == "none"


def test_an_activation_row_alone_is_not_capture(senv, monkeypatch, tmp_path):
    schema, connect = senv
    with connect() as conn:
        SW.seed_status_world(conn, activation=LAST - 3)
    doc = run_ok(monkeypatch, schema, tmp_path)
    s = src(doc, "candidates")
    assert s["dormant"] is True and s["observed_sessions"] == 0 and s["activation"][0]["state"] == "enabled"
    assert s["expected_sessions"] in (None, 0) or s["capture_state"] in ("never_captured", "stalled")


# ------------------------------------------------------------------ unhealthy vs healthy (the owner-facing examples)
def test_the_first_weeks_after_activation_are_unhealthy_and_say_why(senv, monkeypatch, tmp_path):
    schema, connect = senv
    with connect() as conn:
        SW.unhealthy(conn)
    rc, doc, so, se, out = status(monkeypatch, schema, tmp_path, "--require-no-readiness-failures")
    assert rc == 4, (so[:500], se[:500])
    assert src(doc, "candidates")["observed_sessions"] == 8
    for name in ("market", "sector", "stock_rs"):
        assert src(doc, name)["capture_state"] == "never_captured" and f"{name}_capture_never_captured" in fail_ids(doc)
    assert {"market_no_scheduled_collector", "labels_no_scheduled_collector"} <= set(fail_ids(doc))
    assert doc["contract"]["evaluated"] is False and doc["remaining_history_estimate"]["remaining_sessions"] is None
    assert (out / "status.json").exists() and (out / "status.txt").exists() and (out / CLI.STATUS_CONTEXT_ARTIFACT).exists()


def test_a_complete_observed_history_has_no_readiness_failures(healthy_env, monkeypatch, tmp_path):
    schema, connect = healthy_env
    rc, doc, so, se, out = status(monkeypatch, schema, tmp_path, "--require-no-readiness-failures")
    assert rc == 0, (so[:1500], se[:500])
    assert doc["readiness_failures"] == []
    for name in ("candidates", "market", "sector", "stock_rs"):
        s = src(doc, name)
        assert s["capture_state"] == "active" and s["observed_sessions"] == s["expected_sessions"] == LAST + 1 and s["coverage_since_first_observed"] == 1.0
        assert s["missing_sessions"]["count"] == 0 and s["reconstructed_rows"] in (0, None) and s["unknown_provenance_rows"] in (0, None)
    c = doc["contract"]
    assert c["evaluated"] is True and c["data_checks_all_pass"] is True
    assert all(v["final_labelled_rows"] >= v["required"] for v in c["sample"].values())
    assert doc["remaining_history_estimate"]["remaining_sessions"] == 0
    assert not any(f["count"] for f in doc["integrity"] if f["severity"] == "blocker")
    assert [n["id"] for n in doc["structural_notes"]]            # still reported, no longer what fails the contract


def test_the_status_text_is_human_readable_and_makes_no_eligibility_claim(healthy_env, monkeypatch, tmp_path):
    schema, connect = healthy_env
    cli = Cli(monkeypatch, schema=schema)
    rc, so, se = cli.run("status", "--spec", str(write_spec(tmp_path / "s")))
    assert rc == 0 and so.startswith("STATUS  hash ") and "predictive edge claimed: none" in so
    assert "ELIGIBLE: YES" not in so and "MODEL RESEARCH ELIGIBLE" not in so


# ------------------------------------------------------------------ partial coverage / missing sessions / source-specific gaps
def test_missing_sessions_and_partial_coverage_are_measured_per_source(senv, monkeypatch, tmp_path):
    schema, connect = senv
    r = recent(60)
    gone = {r[10], r[11], r[30]}
    with connect() as conn:
        SW.seed_status_world(conn, cand=r, market=[i for i in r if i not in gone], rs=r, activation=r[0])
    doc = run_ok(monkeypatch, schema, tmp_path)
    m = src(doc, "market")
    assert m["observed_sessions"] == 57 and m["missing_sessions"]["count"] == 3 and m["coverage_since_first_observed"] == round(57 / 60, 6)
    assert m["missing_sessions"]["ranges"] == [[CAL[r[10]].isoformat(), CAL[r[11]].isoformat()], [CAL[r[30]].isoformat(), CAL[r[30]].isoformat()]]
    assert src(doc, "stock_rs")["missing_sessions"]["count"] == 0 and src(doc, "candidates")["missing_sessions"]["count"] == 0     # the gap is the market's alone
    assert doc["history"]["jointly_observed_sessions"] == 57 and doc["history"]["consecutive_jointly_observed_sessions"]["longest"] == 29


def test_a_source_that_starts_late_is_counted_from_its_own_first_session(senv, monkeypatch, tmp_path):
    schema, connect = senv
    r = recent(60)
    with connect() as conn:
        SW.seed_status_world(conn, cand=r, market=r[20:], rs=r, activation=r[0])
    doc = run_ok(monkeypatch, schema, tmp_path)
    m = src(doc, "market")
    assert m["first_observed_session"] == CAL[r[20]].isoformat() and m["expected_sessions"] == 40 and m["coverage_since_first_observed"] == 1.0
    assert m["coverage_of_due_calendar"] < 1.0 and doc["history"]["joint_observed_floor_session"] == CAL[r[20]].isoformat()


def test_a_source_that_stops_is_stalled(senv, monkeypatch, tmp_path):
    schema, connect = senv
    r = recent(60)
    with connect() as conn:
        SW.seed_status_world(conn, cand=r, market=r[:50], rs=r, activation=r[0])
    doc = run_ok(monkeypatch, schema, tmp_path)
    assert src(doc, "market")["capture_state"] == "stalled" and "market_capture_stalled" in fail_ids(doc)


# ------------------------------------------------------------------ reconstructed / late / back-dated
def test_reconstructed_history_is_never_counted_as_observed(senv, monkeypatch, tmp_path):
    schema, connect = senv
    r = recent(30)
    with connect() as conn:
        SW.seed_status_world(conn, cand=r, market=r, rs=r, recon_only=r, activation=r[0])
    doc = run_ok(monkeypatch, schema, tmp_path)
    m = src(doc, "market")
    assert m["observed_sessions"] == 0 and m["capture_state"] == "never_captured" and m["reconstructed_rows"] >= 30
    assert integ(doc, "market_reconstructed_rows")["count"] >= 30 and "market_capture_never_captured" in fail_ids(doc)


def test_an_observed_snapshot_next_to_a_reconstructed_one_is_observed_and_the_overlap_is_reported(senv, monkeypatch, tmp_path):
    schema, connect = senv
    r = recent(30)
    with connect() as conn:
        SW.seed_status_world(conn, cand=r, market=r, rs=r, reconstructed=r[:4], activation=r[0])
    doc = run_ok(monkeypatch, schema, tmp_path)
    m = src(doc, "market")
    assert m["observed_sessions"] == 30 and integ(doc, "market_sessions_observed_and_reconstructed")["count"] == 4


def test_late_rows_are_not_forward_history(senv, monkeypatch, tmp_path):
    schema, connect = senv
    r = recent(40)
    late = set(r[5:9])
    with connect() as conn:
        SW.seed_status_world(conn, cand=r, market=r, rs=r, late=late, activation=r[0])
    doc = run_ok(monkeypatch, schema, tmp_path)
    m = src(doc, "market")
    assert m["observed_sessions"] == 36 and m["session_breakdown_since_first"]["late"] == 4 and m["missing_sessions"]["count"] == 4
    assert integ(doc, "market_rows_late")["count"] >= 4 and integ(doc, "stock_rs_rows_late")["count"] >= 12


def test_backdated_availability_is_impossible_and_blocks(senv, monkeypatch, tmp_path):
    schema, connect = senv
    r = recent(30)
    with connect() as conn:
        SW.seed_status_world(conn, cand=r, market=r, rs=r, market_backdated={r[3]}, cand_backdated={r[4]}, activation=r[0])
    doc = run_ok(monkeypatch, schema, tmp_path)
    assert integ(doc, "market_rows_backdated")["count"] >= 1 and integ(doc, "candidate_rows_backdated")["count"] == 3
    assert {"market_rows_backdated", "candidate_rows_backdated"} <= set(fail_ids(doc))
    assert src(doc, "market")["session_breakdown_since_first"]["backdated"] == 1


def test_candidates_captured_before_their_activation_are_reported(senv, monkeypatch, tmp_path):
    schema, connect = senv
    r = recent(30)
    with connect() as conn:
        SW.seed_status_world(conn, cand=r, market=r, rs=r, activation=r[6])
    doc = run_ok(monkeypatch, schema, tmp_path)
    assert integ(doc, "candidate_rows_before_activation")["count"] == 18 and "candidate_rows_before_activation" in fail_ids(doc)


# ------------------------------------------------------------------ malformed / conflicting observations
def test_partial_failed_and_running_capture_runs_are_not_observed_sessions(senv, monkeypatch, tmp_path):
    schema, connect = senv
    r = recent(30)
    with connect() as conn:
        SW.seed_status_world(conn, cand=r, market=r, rs=r, activation=r[0], run_status={r[5]: "partial", r[6]: "failed", r[7]: "running"})
    doc = run_ok(monkeypatch, schema, tmp_path)
    c = src(doc, "candidates")
    br = c["session_breakdown_since_first"]
    assert br["partial"] == 1 and br["failed"] == 1 and br["running"] == 1 and br["observed"] == 27
    assert integ(doc, "candidate_run_not_complete")["count"] == 3 and "candidate_run_not_complete" in fail_ids(doc)


def test_a_conflicting_rerun_is_reported_as_hash_drift(senv, monkeypatch, tmp_path):
    schema, connect = senv
    r = recent(30)
    with connect() as conn:
        SW.seed_status_world(conn, cand=r, market=r, rs=r, activation=r[0], drift={r[9]})
    doc = run_ok(monkeypatch, schema, tmp_path)
    assert integ(doc, "candidate_run_hash_drift")["count"] == 1 and "candidate_run_hash_drift" in fail_ids(doc)


def test_relative_strength_rows_from_two_different_runs_are_conflicting(senv, monkeypatch, tmp_path):
    schema, connect = senv
    r = recent(30)
    with connect() as conn:
        SW.seed_status_world(conn, cand=r, market=r, rs=r, activation=r[0], rs_extra_run={r[11], r[12]})
    doc = run_ok(monkeypatch, schema, tmp_path)
    assert integ(doc, "stock_rs_sessions_with_multiple_runs")["count"] == 2 and "stock_rs_sessions_with_multiple_runs" in fail_ids(doc)


def test_observed_relative_strength_cells_for_symbols_with_no_sector_are_flagged_and_fail_check_6(senv, monkeypatch, tmp_path):
    schema, connect = senv
    with connect() as conn:
        SW.healthy(conn, no_sector=("C",))
    doc = run_ok(monkeypatch, schema, tmp_path)
    f = integ(doc, "stock_rs_ok_cells_without_sector")
    assert f["count"] > 0
    c = doc["contract"]
    assert c["evaluated"] is True and c["data_checks_all_pass"] is False
    failing = [f["id"] for f in doc["readiness_failures"] if f["scope"] == "slice5_data_check"]
    assert "relative_strength_sector_pit_safe" in failing


def test_labels_stamped_before_their_horizon_are_flagged(senv, monkeypatch, tmp_path):
    schema, connect = senv
    r = recent(40)
    with connect() as conn:
        w = SW.seed_status_world(conn, cand=r, market=r, rs=r, activation=r[0], labels=r[:3])
        cur = conn.cursor()
        cur.execute("SELECT 1")
        triggers = SW.DW._stamp_triggers(cur)
        for trg, rel in triggers:
            cur.execute(f'ALTER TABLE "{rel}" DISABLE TRIGGER "{trg}"')
        cur.execute("ALTER TABLE forward_return_label DISABLE TRIGGER USER")
        cur.execute("UPDATE forward_return_label SET computed_at = (t0_session::timestamp AT TIME ZONE 'UTC') WHERE t0_session = %s", (CAL[r[0]],))
        cur.execute("ALTER TABLE forward_return_label ENABLE TRIGGER USER")
        for trg, rel in triggers:
            cur.execute(f'ALTER TABLE "{rel}" ENABLE ALWAYS TRIGGER "{trg}"')
        conn.commit()
    doc = run_ok(monkeypatch, schema, tmp_path)
    assert integ(doc, "labels_computed_before_horizon")["count"] >= 1 and "labels_computed_before_horizon" in fail_ids(doc)


# ------------------------------------------------------------------ post-cutoff data / determinism / independent reproducibility
def test_rows_arriving_after_the_cutoff_cannot_change_the_canonical_status(senv, senv2, monkeypatch, tmp_path):
    (s1, c1), (s2, c2) = senv, senv2
    r = recent(30)
    with c1() as conn:
        SW.seed_status_world(conn, cand=r, market=r, rs=r, activation=r[0])
    with c2() as conn:
        SW.seed_status_world(conn, cand=r, market=r, rs=r, activation=r[0])
        SW.seed_status_world(conn, cand=[LAST + 1, LAST + 2], market=[LAST + 1, LAST + 2], rs=[LAST + 1, LAST + 2], activation=None,
                             post_cutoff={LAST + 1, LAST + 2})
    d1 = run_ok(monkeypatch, s1, tmp_path, name="a")
    d2 = run_ok(monkeypatch, s2, tmp_path, name="b")
    assert d1["status_hash"] == d2["status_hash"]
    assert (tmp_path / "a" / "status.json").read_bytes() == (tmp_path / "b" / "status.json").read_bytes()
    ctx1 = json.loads((tmp_path / "a" / CLI.STATUS_CONTEXT_ARTIFACT).read_text(encoding="utf-8"))
    ctx2 = json.loads((tmp_path / "b" / CLI.STATUS_CONTEXT_ARTIFACT).read_text(encoding="utf-8"))
    assert sum(ctx2["post_cutoff_rows_ignored"].values()) > 0 and sum(ctx1["post_cutoff_rows_ignored"].values()) == 0


def test_repeated_runs_on_unchanged_data_are_byte_identical_and_volatile_metadata_is_separate(healthy_env, monkeypatch, tmp_path):
    schema, connect = healthy_env
    d1 = run_ok(monkeypatch, schema, tmp_path, name="a")
    d2 = run_ok(monkeypatch, schema, tmp_path, name="b")
    assert d1["status_hash"] == d2["status_hash"]
    assert (tmp_path / "a" / "status.json").read_bytes() == (tmp_path / "b" / "status.json").read_bytes()
    assert (tmp_path / "a" / "status.txt").read_bytes() == (tmp_path / "b" / "status.txt").read_bytes()
    ctx = json.loads((tmp_path / "a" / CLI.STATUS_CONTEXT_ARTIFACT).read_text(encoding="utf-8"))
    assert ctx["schema"] == CLI.STATUS_CONTEXT_SCHEMA and ctx["mode"] == "read_only" and ctx["status_hash"] == d1["status_hash"]
    blob = (tmp_path / "a" / "status.json").read_text(encoding="utf-8")
    assert ctx["database_time"] not in blob and str(tmp_path) not in blob and _connect_args()["dbname"] not in blob


def test_two_independent_databases_with_the_same_history_give_the_same_status(senv, senv2, monkeypatch, tmp_path):
    (s1, c1), (s2, c2) = senv, senv2
    for c in (c1, c2):
        with c() as conn:
            seed_recent(conn, 40)
    d1 = run_ok(monkeypatch, s1, tmp_path, name="a")
    d2 = run_ok(monkeypatch, s2, tmp_path, name="b")
    assert d1["status_hash"] == d2["status_hash"] and (tmp_path / "a" / "status.json").read_bytes() == (tmp_path / "b" / "status.json").read_bytes()


def test_a_different_history_gives_a_different_hash(senv, senv2, monkeypatch, tmp_path):
    (s1, c1), (s2, c2) = senv, senv2
    with c1() as conn:
        seed_recent(conn, 40)
    with c2() as conn:
        seed_recent(conn, 40, market=recent(40)[:-1])
    assert run_ok(monkeypatch, s1, tmp_path, name="a")["status_hash"] != run_ok(monkeypatch, s2, tmp_path, name="b")["status_hash"]


def test_the_session_time_zone_cannot_change_the_status(senv, monkeypatch, tmp_path):
    schema, connect = senv
    with connect() as conn:
        seed_recent(conn, 30, late=set(recent(30)[4:6]))

    class Tz(Cli):
        tz = "UTC"

        def _select(self):
            super()._select()
            self._mp.setenv("PGOPTIONS", f"-c search_path={self._schema} -c timezone={self.tz}")
    hashes = set()
    for i, tz in enumerate(("UTC", "Asia/Jerusalem", "America/Los_Angeles", "Pacific/Kiritimati", "Etc/GMT+12")):
        cli = Tz(monkeypatch, schema=schema)
        cli.tz = tz
        hashes.add(run_ok_cli(monkeypatch, cli, tmp_path, f"tz{i}"))
    assert len(hashes) == 1


def run_ok_cli(monkeypatch, cli, tmp_path, name):
    rc, doc, so, se, out = status(monkeypatch, None, tmp_path, name=name, cli=cli)
    assert rc == 0, (so[:500], se[:500])
    return doc["status_hash"]


# ------------------------------------------------------------------ the SQL spelling of the PIT rule == the Python rule
@pytest.mark.parametrize("tz", ["UTC", "Asia/Jerusalem", "America/New_York", "Pacific/Kiritimati", "Etc/GMT+12"])
def test_the_sql_decision_deadline_and_is_known_match_the_contract_at_every_boundary(senv, tz):
    schema, connect = senv
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT set_config('TimeZone', %s, false)", (tz,))
        micro = timedelta(microseconds=1)
        for d in (CAL[0], CAL[1], date(2026, 3, 8), date(2026, 11, 1), date(2026, 12, 31)):
            for grace in (0, 1, 2):
                cur.execute(f"SELECT {SRD._dl('%(d)s::date', '%(g1)s')}", dict(d=d, g1=1 + grace))
                sql_deadline = cur.fetchone()[0]
                py_deadline = C.decision_deadline(d, grace)
                assert sql_deadline == py_deadline, (tz, d, grace)
                cutoff = py_deadline + timedelta(days=3)
                for avail in (py_deadline - micro, py_deadline, py_deadline + micro, cutoff, cutoff + micro, py_deadline - timedelta(days=30)):
                    cur.execute(f"SELECT %(a)s::timestamptz <= %(c)s::timestamptz AND %(a)s::timestamptz < {SRD._dl('%(d)s::date', '%(g1)s')}",
                                dict(a=avail, c=cutoff, d=d, g1=1 + grace))
                    assert cur.fetchone()[0] == C.is_known(avail, d, grace, cutoff), (tz, d, grace, avail)
        conn.rollback()


# ------------------------------------------------------------------ read-only enforcement
class LoggingCursor(extensions.cursor):
    log = []

    def execute(self, query, vars=None):
        LoggingCursor.log.append(query if isinstance(query, str) else query.decode("utf-8", "replace"))
        return super().execute(query, vars)


def test_a_status_run_issues_only_read_statements_and_changes_nothing(healthy_env, monkeypatch, tmp_path):
    schema, connect = healthy_env
    LoggingCursor.log = []
    modes = []

    def logging_connect(a, *, readonly):
        conn = CLI.default_connect(a, readonly=readonly)
        modes.append(readonly)
        conn.cursor_factory = LoggingCursor
        return conn
    before = table_fingerprint(connect, schema)
    cli = Cli(monkeypatch, schema=schema)
    rc, so, se = cli.run("status", "--spec", str(write_spec(tmp_path / "s")), "--out-dir", str(tmp_path / "o"), "--quiet", connect=logging_connect)
    assert rc == 0, (so[:500], se[:500])
    assert modes == [True]
    assert len(LoggingCursor.log) > 15
    for q in LoggingCursor.log:
        head = q.lstrip().split(None, 1)[0].upper()
        assert head in ("SELECT", "WITH", "SET", "SHOW"), q[:120]
    assert table_fingerprint(connect, schema) == before, "a status run must not change a single row, registry included"


def test_status_works_under_a_role_that_can_only_select(senv, monkeypatch, tmp_path):
    schema, connect = senv
    with connect() as conn:
        seed_recent(conn, 30)
    role, pw = f"rb_ro_{uuid.uuid4().hex[:10]}", uuid.uuid4().hex
    args = _connect_args()
    admin = psycopg2.connect(host=args["host"], port=args["port"], dbname=args["dbname"], user=args["user"], password=args["password"])
    admin.autocommit = True
    cur = admin.cursor()
    cur.execute(f'CREATE ROLE "{role}" LOGIN PASSWORD %s', (pw,))
    try:
        cur.execute(f'GRANT CONNECT ON DATABASE "{args["dbname"]}" TO "{role}"')
        cur.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
        cur.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
        before = table_fingerprint(connect, schema)
        cli = Cli(monkeypatch, schema=schema)
        cli.password = pw
        cli.argv_db[cli.argv_db.index("--user") + 1] = role
        rc, so, se = cli.run("status", "--spec", str(write_spec(tmp_path / "s")), "--out-dir", str(tmp_path / "o"), "--json")
        assert rc == 0, (so[:500], se[:800])
        assert ST.verify_status_hash(json.loads(so))
        assert table_fingerprint(connect, schema) == before
        # the same role cannot write: the guarantee is the database's, not only the code's
        ro = psycopg2.connect(host=args["host"], port=args["port"], dbname=args["dbname"], user=role, password=pw, options=f"-c search_path={schema}")
        try:
            rcur = ro.cursor()
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                rcur.execute("DELETE FROM candidate_capture_run")
        finally:
            ro.close()
    finally:
        cur.execute(f'DROP OWNED BY "{role}"')
        cur.execute(f'DROP ROLE "{role}"')
        admin.close()


def test_status_never_registers_anything_even_when_given_the_register_vocabulary(healthy_env, monkeypatch, tmp_path):
    schema, connect = healthy_env
    cli = Cli(monkeypatch, schema=schema)
    rc, so, se = cli.run("status", "--spec", str(write_spec(tmp_path / "s")), "--register")
    assert rc == 2                                           # not a status option: usage error, nothing ran


# ------------------------------------------------------------------ CLI behaviour
def test_the_cli_fails_closed_or_refuses_cleanly(healthy_env, monkeypatch, tmp_path):
    schema, connect = healthy_env
    cli = Cli(monkeypatch, schema=schema)
    spec = write_spec(tmp_path / "s")
    full = tmp_path / "full"
    full.mkdir()
    (full / "x").write_text("x")
    rc, so, se = cli.run("status", "--spec", str(spec), "--out-dir", str(full))
    assert rc == 2 and "not empty" in se
    rc, so, se = cli.run("status", "--spec", str(spec), "--cutoff", "2999-01-01T00:00:00+00:00")
    assert rc == 1 and "FAILED CLOSED" in se
    rc, so, se = cli.run("status", "--spec", str(spec), "--cutoff", "2026-01-01T00:00:00")
    assert rc == 2 and "UTC offset" in se
    rc, so, se = cli.run("status", "--spec", str(tmp_path / "missing.json"))
    assert rc == 2 and "not found" in se


def test_quiet_and_json_modes(healthy_env, monkeypatch, tmp_path):
    schema, connect = healthy_env
    cli = Cli(monkeypatch, schema=schema)
    spec = write_spec(tmp_path / "s")
    rc, so, se = cli.run("status", "--spec", str(spec), "--out-dir", str(tmp_path / "q"), "--quiet")
    assert rc == 0 and so.startswith("STATUS  hash ") and (tmp_path / "q" / "status.txt").read_text(encoding="utf-8") not in so
    rc, js, se = cli.run("status", "--spec", str(spec), "--out-dir", str(tmp_path / "j"), "--json")
    assert rc == 0 and js.strip() == (tmp_path / "j" / "status.json").read_text(encoding="utf-8").strip()


def test_the_password_never_appears_in_a_status_output(healthy_env, monkeypatch, tmp_path):
    schema, connect = healthy_env
    cli = Cli(monkeypatch, schema=schema)
    rc, so, se = cli.run("status", "--spec", str(write_spec(tmp_path / "s")), "--out-dir", str(tmp_path / "o"))
    blob = so + se + "".join(p.read_text(encoding="utf-8", errors="replace") for p in (tmp_path / "o").iterdir())
    assert cli.password and cli.password not in blob


def test_status_from_an_earlier_cutoff_sees_only_the_history_known_by_then(healthy_env, monkeypatch, tmp_path):
    schema, connect = healthy_env
    early = C.decision_deadline(CAL[99], 1).isoformat()
    cli = Cli(monkeypatch, schema=schema)
    rc, doc, so, se, out = status(monkeypatch, schema, tmp_path, "--cutoff", early, cli=cli)
    assert rc == 0, (so[:500], se[:500])
    assert src(doc, "candidates")["observed_sessions"] == 100 and doc["scope"]["calendar"]["latest_due_session"] == CAL[99].isoformat()


# ------------------------------------------------------------------ non-contradiction with Slice 5
def test_the_status_contract_section_agrees_with_the_slice5_readiness_document(denv, monkeypatch, tmp_path):
    cli = Cli(monkeypatch, schema=denv.schema)
    spec = write_spec(tmp_path / "s")
    out = tmp_path / "manifest.json"
    rc, so, se = cli.run("author", "--spec", str(spec), "--out", str(out))
    assert rc == 0, (so, se)
    build = tmp_path / "build"
    rc, so, se = cli.run("build", "--manifest", str(out), "--out-dir", str(build))
    assert rc == 0, (so, se)
    readiness = json.loads((build / "readiness.json").read_text(encoding="utf-8"))
    rc, doc, so, se, _ = status(monkeypatch, denv.schema, tmp_path, spec=spec, cli=cli)
    assert rc == 0, (so[:500], se[:500])
    slice5 = {c["id"]: c for c in readiness["eligibility"]["checks"]}
    c = doc["contract"]
    assert c["evaluated"] is True and c["data_checks"]
    for chk in c["data_checks"]:
        assert chk["passed"] == slice5[chk["id"]]["passed"] and chk["detail"] == slice5[chk["id"]]["detail"], chk["id"]
    assert '"model_research_eligible"' not in json.dumps(doc)             # the phrase appears only in prose, never as a key or value of its own
    for chk in c["build_level_checks"]["not_evaluated_here_need_a_real_build"]:
        assert chk in slice5


def test_the_registry_tables_are_untouched_by_status(denv, monkeypatch, tmp_path):
    before = table_fingerprint(denv.connect, denv.schema)
    cli = Cli(monkeypatch, schema=denv.schema)
    rc, so, se = cli.run("status", "--spec", str(write_spec(tmp_path / "s")), "--quiet")
    assert rc == 0, (so, se)
    assert table_fingerprint(denv.connect, denv.schema) == before
