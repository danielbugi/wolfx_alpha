"""The Release B read-only validation tool (mechanism/validate_release_b.py).

Two halves:
  * pure logic (no database): the read-only SQL gate, guard-log parsing and thresholds, config/env parsing (and that no
    secret is ever kept or printed), the capture accounting rules (against a canned fake connection);
  * against a throwaway production-shaped DATABASE (the same fixture the role tests use): the checks pass on a correctly
    activated schema, FAIL on each drift they claim to detect, and -- the point of the tool -- the server itself refuses
    any write on the validator's connection, and a full run leaves the data exactly as it found it.

Run:  python -m pytest mechanism/research/tests/test_validate_release_b.py -q      (from the repo root)
"""
import inspect
import io
import json
import os
import re
import sys
import uuid
from datetime import date, timedelta

import psycopg2
import pytest

from conftest import ROOT, ROLES_SQL, _connect_args
from test_roles_full_schema import full  # noqa: F401  (module-scoped throwaway DB + roles)
from test_roles_full_schema import snapshot_state  # noqa: F401

sys.path.insert(0, os.path.join(ROOT, "mechanism"))
import validate_release_b as v  # noqa: E402
from research import schema_fingerprint as fp  # noqa: E402

D = date(2026, 10, 7)


def _rep():
    return v.Report()


def _by(rep, name):
    return [(s, d) for s, n, d in rep.rows if n == name]


def _status(rep, name):
    rows = _by(rep, name)
    assert rows, f"no check named {name}; have {[n for _, n, _ in rep.rows]}"
    return rows[-1][0]


# ======================================================================= the read-only SQL gate
@pytest.mark.parametrize("sql", [
    "SELECT 1", "  select count(*) from signal_ledger  ", "WITH x AS (SELECT 1) SELECT * FROM x", "SHOW transaction_read_only",
    "SELECT 1;"])
def test_plain_reads_pass_the_gate(sql):
    v.assert_read_only_sql(sql)


@pytest.mark.parametrize("sql", [
    "INSERT INTO signal_ledger DEFAULT VALUES", "UPDATE signal_ledger SET status = 'open'", "DELETE FROM signal_ledger",
    "TRUNCATE signal_ledger", "CREATE TABLE zz (x int)", "ALTER TABLE ml_models ADD COLUMN zz int", "DROP TABLE ml_models",
    "GRANT SELECT ON ml_models TO public", "SELECT 1; DELETE FROM signal_ledger", "SELECT * FROM t FOR UPDATE",
    "SELECT * FROM t FOR SHARE", "SELECT * INTO zz_copy FROM signal_ledger", "COPY signal_ledger TO PROGRAM 'x'",
    "CALL some_proc()", "SET TRANSACTION READ WRITE", "SET default_transaction_read_only = off",
    "RESET default_transaction_read_only", "BEGIN READ WRITE", "COMMIT", "SELECT research_capture_set_state(1,'enabled',NULL,'x')" + "; SELECT 1",
    "WITH d AS (DELETE FROM signal_ledger RETURNING 1) SELECT * FROM d", "DO $$ BEGIN END $$", "", "   "])
def test_anything_that_is_not_a_plain_read_is_refused_before_reaching_the_server(sql):
    with pytest.raises(v.ReadOnlyViolation):
        v.assert_read_only_sql(sql)


def test_the_module_has_a_single_execute_call_and_never_commits():
    """Every statement goes through ReadOnlyDB.q (which gates it); nothing else may touch a cursor or commit."""
    src = inspect.getsource(v)
    assert src.count(".execute(") == 1, "all SQL must go through ReadOnlyDB.q"
    assert "commit(" not in src
    assert "autocommit = True" not in src
    assert "readonly=True" in src and "default_transaction_read_only=on" in src


def test_ml_models_columns_match_the_runtime_registry_check():
    try:
        from ml_training.models import momentum_predictor as mp
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"momentum_predictor not importable here: {type(e).__name__}")
    assert tuple(mp.REGISTRY_REQUIRED_COLUMNS) == v.ML_MODELS_REQUIRED_COLUMNS


def test_the_callable_function_list_matches_the_fingerprint_non_trigger_functions():
    assert set(v.CALLABLE_RESEARCH_FUNCTIONS) == {f for f in fp.FUNCTIONS if "guard" not in f and "audit_append_only" not in f}


# ======================================================================= guard log evidence (S6 / S8)
INERT = ("INFO Universe guards mode: INERT (session 2026-10-07; GUARDS_EFFECTIVE_FROM is unset)\n"
         "INFO Screener behaviour mode: LEGACY (universe_guards, combined_score_ranking, ml_unprocessed_fallback, ledger_integrity_refusals)\n")
ACTIVE = ("INFO Universe guards mode: ACTIVE (session 2026-10-07 >= GUARDS_EFFECTIVE_FROM=2026-10-07)\n"
          "INFO Screener behaviour mode: NEW (universe_guards, combined_score_ranking, ml_unprocessed_fallback, ledger_integrity_refusals)\n")


def _guard(text, expect, **kw):
    rep = _rep()
    v.check_guard_log(rep, text, expect, **kw)
    return rep


def test_an_inert_run_logs_the_mode_and_no_drop_line():
    assert not _guard(INERT, "inert").failed


def test_an_inert_run_that_dropped_symbols_is_a_failure():
    assert _guard(INERT + "Universe guards: dropped 120/3000 symbols\n", "inert").failed


def test_an_active_run_with_a_plausible_drop_passes():
    rep = _guard(ACTIVE + "Universe guards: dropped 150/3010 symbols\n", "active")
    assert not rep.failed and _status(rep, "guards.log.drop_fraction") == v.PASS


def test_a_candidate_symbol_count_far_below_the_stock_universe_is_not_a_failure():
    # 2026-10-02 production: 77/1440. M is the distinct candidate symbols entering the guards, not the ~3,066 universe.
    for line in ("Universe guards: dropped 77/1440 symbols\n", "Universe guards: dropped 10/300 symbols\n",
                 "Universe guards: dropped 100/9000 symbols\n"):
        rep = _guard(ACTIVE + line, "active")
        assert not rep.failed, line
        assert _status(rep, "guards.log.guard_input_symbol_count") == v.PASS
    assert not _by(rep, "guards.log.universe_size")


def test_the_research_band_is_a_warning_never_a_gate():
    inside = _guard(ACTIVE + "Universe guards: dropped 77/1440 symbols\n", "active")
    assert not _by(inside, "guards.log.guard_input_symbol_count_band")                          # 1,440 is inside 1,362-1,871
    outside = _guard(ACTIVE + "Universe guards: dropped 10/300 symbols\n", "active")
    assert _status(outside, "guards.log.guard_input_symbol_count_band") == v.WARN and not outside.failed
    assert v.GUARD_INPUT_RESEARCH_BAND == (1362, 1871)


@pytest.mark.parametrize("line,check", [
    ("Universe guards: dropped 600/3000 symbols\n", "guards.log.drop_fraction"),                # 20% > 15%
    ("Universe guards: dropped 0/0 symbols\n", "guards.log.guard_input_symbol_count"),          # M must be > 0
    ("Universe guards: dropped 11/10 symbols\n", "guards.log.guard_input_symbol_count"),        # N > M is impossible
])
def test_active_run_thresholds_and_impossible_counts_are_stop_conditions(line, check):
    rep = _guard(ACTIVE + line, "active")
    assert rep.failed and _status(rep, check) == v.FAIL


def test_the_boundary_of_the_drop_fraction_is_inclusive():
    assert not _guard(ACTIVE + "Universe guards: dropped 450/3000 symbols\n", "active").failed       # exactly 15%
    assert _guard(ACTIVE + "Universe guards: dropped 451/3000 symbols\n", "active").failed


def test_missing_or_duplicated_log_lines_fail():
    assert _guard("nothing relevant\n", "inert").failed                                    # no mode line at all
    assert _guard(ACTIVE, "active").failed                                                  # active but no dropped line
    assert _guard(INERT + INERT, "inert").failed                                            # two mode lines
    assert _guard(ACTIVE + "Universe guards: dropped 1/3000 symbols\n" * 2, "active").failed


def test_a_mode_that_contradicts_the_expectation_fails():
    assert _guard(INERT, "active").failed
    assert _guard(ACTIVE + "Universe guards: dropped 100/3000 symbols\n", "inert").failed


def test_the_two_mode_lines_must_agree_and_both_must_be_present():
    guards_only = INERT.splitlines(True)[0]
    assert _status(_guard(guards_only, "inert"), "guards.log.behaviour_line") == v.FAIL            # image predates the fix
    mixed = INERT.splitlines(True)[0] + ACTIVE.splitlines(True)[1]                                  # guards INERT, behaviour NEW
    assert _status(_guard(mixed, "inert"), "guards.log.behaviour_mode") == v.FAIL
    mixed = ACTIVE.splitlines(True)[0] + INERT.splitlines(True)[1] + "Universe guards: dropped 100/3000 symbols\n"
    assert _status(_guard(mixed, "active"), "guards.log.behaviour_mode") == v.FAIL
    assert _status(_guard(INERT + INERT.splitlines(True)[1], "inert"), "guards.log.behaviour_line") == v.FAIL  # duplicated


def test_the_post_guard_signal_line_is_the_line_the_screener_emits():
    screener_src = open(os.path.join(ROOT, "mechanism", "screeners", "multi_timeframe_screener.py"), encoding="utf-8").read()
    assert "signals after the liquidity/data-integrity guards" in screener_src
    assert v.GUARD_AFTER_RE.search("INFO 1363 signals after the liquidity/data-integrity guards").group(1) == "1363"


def test_the_log_lines_the_screener_emits_are_the_lines_this_tool_parses():
    from screeners import guards_boundary as gb
    assert v.GUARD_MODE_RE.search(gb.describe(D, None)).group(1) == "INERT"
    assert v.GUARD_MODE_RE.search(gb.describe(D, D)).group(1) == "ACTIVE"
    assert v.BEHAVIOUR_MODE_RE.search(gb.describe_behaviour(D, None)).group(1) == "LEGACY"
    assert v.BEHAVIOUR_MODE_RE.search(gb.describe_behaviour(D, D)).group(1) == "NEW"
    screener_src = open(os.path.join(ROOT, "mechanism", "screeners", "multi_timeframe_screener.py"), encoding="utf-8").read()
    assert "Universe guards: dropped" in screener_src and v.GUARD_DROPPED_RE.search("Universe guards: dropped 12/3000 symbols")


def _results(applied, session="2026-10-07", bull=3, bear=2):
    return {"metadata": {"universe_guards": {"applied": applied, "effective_from": "2026-10-07", "session": session,
                                          "scope": list(__import__("screeners.guards_boundary", fromlist=["x"]).SCOPE)}},
            "signals": {"bullish_breakout": [{}] * bull, "bearish_breakout": [{}] * bear, "near_bullish": [{}] * 9}}


def test_results_metadata_is_checked_and_the_breakout_count_returned():
    rep = _rep()
    assert v.check_guard_results(rep, _results(True), D, "active") == 5 and not rep.failed
    assert v.check_guard_results(rep := _rep(), _results(False), D, "active") == 5 and rep.failed
    assert v.check_guard_results(rep := _rep(), _results(True, session="2026-10-06"), D, "active") == 5 and rep.failed
    rep = _rep()
    v.check_guard_results(rep, {"metadata": {}, "signals": {}}, D, "inert")
    assert rep.failed                                                                       # image predates the metadata


# --- N/M reconciliation between the log, the results file and the post-guard signal count
POST = "1363 signals after the liquidity/data-integrity guards\n"
RUN = ACTIVE + "Universe guards: dropped 77/1440 symbols\n" + POST


def _logged(text=RUN):
    return v.check_guard_log(_rep(), text, "active")


def _rec_results(symbols=1363, signals=(155, 159, 299, 750)):
    r = _results(True, bull=signals[0], bear=signals[1])
    r["signals"]["near_bullish"] = [{}] * signals[2]
    r["signals"]["near_bearish"] = [{}] * signals[3]
    r["metadata"]["total_symbols_screened"] = symbols
    return r


def test_the_log_returns_n_m_and_the_post_guard_count():
    assert _logged() == {"n": 77, "m": 1440, "after": 1363}
    assert _logged(ACTIVE + "Universe guards: dropped 77/1440 symbols\n") == {"n": 77, "m": 1440, "after": None}
    assert _logged(ACTIVE + "Universe guards: dropped 600/3000 symbols\n")["m"] == 3000        # failed drop fraction still reconciles
    assert _logged(ACTIVE + "Universe guards: dropped 11/10 symbols\n") is None                # impossible: nothing to reconcile


def test_a_consistent_log_and_results_file_reconcile():
    # symbols in the results == M - N (1440 - 77 = 1363); signals == the logged post-guard count
    rep = _rep()
    v.check_guard_results(rep, _rec_results(), D, "active", _logged())
    assert not rep.failed
    assert _status(rep, "guards.results.symbols_reconcile") == v.PASS
    assert _status(rep, "guards.results.signals_reconcile") == v.PASS


def test_a_results_file_that_disagrees_with_the_log_fails():
    rep = _rep()
    v.check_guard_results(rep, _rec_results(symbols=1400), D, "active", _logged())               # symbols != M - N
    assert _status(rep, "guards.results.symbols_reconcile") == v.FAIL
    rep = _rep()
    v.check_guard_results(rep, _rec_results(signals=(155, 159, 299, 751)), D, "active", _logged())   # signals != logged count
    assert _status(rep, "guards.results.signals_reconcile") == v.FAIL


def test_missing_reconciliation_evidence_is_never_a_silent_pass():
    results = _rec_results()
    del results["metadata"]["total_symbols_screened"]
    rep = _rep()
    v.check_guard_results(rep, results, D, "active", _logged())
    assert _status(rep, "guards.results.symbols_reconcile") == v.WARN                            # reported, not skipped quietly
    rep = _rep()
    v.check_guard_log(rep, ACTIVE + "Universe guards: dropped 77/1440 symbols\n", "active")
    assert _status(rep, "guards.log.post_guard_signals") == v.WARN
    rep = _rep()
    v.check_guard_log(rep, ACTIVE + "Universe guards: dropped 77/1440 symbols\n" + POST * 2, "active")
    assert _status(rep, "guards.log.post_guard_signals") == v.WARN                               # duplicated line


def test_malformed_or_absent_guard_evidence_fails():
    assert _guard(ACTIVE + "Universe guards: dropped x/y symbols\n", "active").failed          # unparseable line == no line
    assert _guard(ACTIVE + "Universe guards: dropped 77/1440\n", "active").failed              # truncated line
    assert _guard("", "active").failed


# ======================================================================= config / env (S7 / S12)
def _cfg(values, expect_guards=None, expect_capture=None, latest=None):
    rep = _rep()
    boundary = v.check_config(rep, values, expect_guards, expect_capture, latest)
    return rep, boundary


def test_unset_is_inert_and_expected_unset_passes():
    rep, b = _cfg({v.gb.ENV_VAR: [], v.CAPTURE_ENV_VAR: []}, "unset", "unset")
    assert b is None and not rep.failed


def test_a_valid_future_boundary_passes_and_a_past_one_is_a_stop():
    rep, b = _cfg({v.gb.ENV_VAR: ["2026-10-07"], v.CAPTURE_ENV_VAR: []}, "set", None, latest=date(2026, 10, 6))
    assert b == D and not rep.failed
    rep, _ = _cfg({v.gb.ENV_VAR: ["2026-10-07"], v.CAPTURE_ENV_VAR: []}, "set", None, latest=D)
    assert _status(rep, "config.guards.boundary_in_future") == v.FAIL


@pytest.mark.parametrize("raw", ["2026-10-7", "today", "soon", "20261007"])
def test_a_malformed_boundary_is_a_failure_not_inert(raw):
    rep, b = _cfg({v.gb.ENV_VAR: [raw], v.CAPTURE_ENV_VAR: []})
    assert b is None and _status(rep, "config.guards.valid") == v.FAIL


def test_a_duplicated_variable_is_a_stop():
    rep, _ = _cfg({v.gb.ENV_VAR: ["2026-10-07", "2026-10-08"], v.CAPTURE_ENV_VAR: []})
    assert _status(rep, "config.guards.duplicate") == v.FAIL
    rep, _ = _cfg({v.gb.ENV_VAR: [], v.CAPTURE_ENV_VAR: ["1", "1"]})
    assert _status(rep, "config.capture.duplicate") == v.FAIL


def test_capture_kill_switch_expectations():
    assert _cfg({v.gb.ENV_VAR: [], v.CAPTURE_ENV_VAR: ["1"]}, None, "unset")[0].failed
    assert not _cfg({v.gb.ENV_VAR: [], v.CAPTURE_ENV_VAR: ["0"]}, None, "unset")[0].failed
    assert not _cfg({v.gb.ENV_VAR: [], v.CAPTURE_ENV_VAR: ["1"]}, None, "on")[0].failed
    assert _cfg({v.gb.ENV_VAR: [], v.CAPTURE_ENV_VAR: []}, None, "on")[0].failed


def test_env_file_parsing_keeps_only_the_two_keys_and_never_a_secret(tmp_path):
    secret = "s3cr3t-value-" + uuid.uuid4().hex
    f = tmp_path / ".env"
    f.write_text(f"# comment\nDB_PASSWORD={secret}\nTELEGRAM_BOT_TOKEN={secret}\nGUARDS_EFFECTIVE_FROM=2026-10-07\n"
                 f"RESEARCH_CAPTURE_ENABLED=\"1\"\nNOEQUALS\n", encoding="utf-8")
    values = v.read_env_file(str(f), (v.gb.ENV_VAR, v.CAPTURE_ENV_VAR))
    assert values == {v.gb.ENV_VAR: ["2026-10-07"], v.CAPTURE_ENV_VAR: ["1"]}
    assert secret not in json.dumps(values)
    out = io.StringIO()
    code = v.run(["config", "--env-file", str(f), "--expect-guards", "set", "--expect-capture", "on"], env={}, out=out)
    assert code == 0 and secret not in out.getvalue()


# ======================================================================= capture accounting (S13), canned connection
class FakeDB:
    """Answers `q`/`scalar` from substring-keyed canned results; refuses anything it was not told about."""

    def __init__(self, answers):
        self.answers = answers

    def _find(self, sql):
        for key, val in self.answers.items():
            if key in sql:
                return val
        raise AssertionError(f"unexpected query: {sql[:90]}")

    def q(self, sql, params=()):
        v.assert_read_only_sql(sql)
        r = self._find(sql)
        return r if isinstance(r, list) else [(r,)]

    def scalar(self, sql, params=()):
        r = self.q(sql, params)
        return r[0][0] if r else None


def _cap(status="complete", cand=100, captured=100, already=0, stale=0, drift=0, rejected=7, obs=100, snaps=100, linked=100,
         partial=0, weakened=0, nrun=1):
    mine = [(status, cand, captured, already, stale, drift, rejected)] * nrun
    return FakeDB({
        "ORDER BY run_started_at DESC LIMIT 3": [],
        "FROM candidate_capture_run WHERE session_date": mine,
        "FROM candidate_observation": obs,
        "FROM feature_snapshot": snaps,
        "observation_id IS NOT NULL": linked,
        "<> (feature_snapshot_id IS NULL)": partial,
        "tgenabled": weakened,
    })


def _capture(**kw):
    rep = _rep()
    v.check_capture(_cap(**kw), rep, D)
    return rep


def test_a_clean_captured_session_passes():
    assert not _capture().failed


@pytest.mark.parametrize("kw,check", [
    ({"status": "partial"}, "capture.status"),
    ({"status": "failed"}, "capture.status"),
    ({"drift": 1}, "capture.hash_drift"),
    ({"obs": 99}, "capture.observation_count"),
    ({"cand": 101}, "capture.accounting"),
    ({"partial": 2}, "capture.lineage_all_or_none"),
    ({"weakened": 1}, "capture.triggers_always"),
])
def test_each_capture_stop_condition_is_detected(kw, check):
    rep = _capture(**kw)
    assert rep.failed and _status(rep, check) == v.FAIL


def test_accounting_allows_already_captured_and_stale_skips():
    assert not _capture(cand=100, captured=60, already=30, stale=10, obs=90).failed


def test_zero_or_two_run_rows_for_the_session_is_a_failure():
    assert _capture(nrun=2).failed
    rep = _rep()
    v.check_capture(FakeDB({"ORDER BY run_started_at DESC LIMIT 3": [], "FROM candidate_capture_run WHERE session_date": []}), rep, D)
    assert _status(rep, "capture.run_row") == v.FAIL


# ======================================================================= delivery (S8.5), canned connection
def _delivery(rows, dupes=()):
    rep = _rep()
    v.check_delivery(FakeDB({"GROUP BY 1, 2, 3 HAVING": list(dupes), "FROM telegram_post_delivery WHERE market_session": rows}), rep, D)
    return rep


def test_delivery_requires_one_sent_prod_row_and_no_duplicates():
    assert not _delivery([("post_market", "prod", "sent", 1)]).failed
    assert _delivery([("post_market", "dev", "sent", 1)]).failed
    assert _delivery([("post_market", "prod", "reserved", 1)]).failed
    assert _delivery([("post_market", "prod", "sent", 1)], dupes=[(D, "post_market", "prod", 2)]).failed


# ======================================================================= against a real, production-shaped database
@pytest.fixture
def env(full):  # noqa: F811
    a = full.args
    return {"DB_HOST": a["host"], "DB_PORT": str(a["port"]), "DB_NAME": full.dbname, "DB_USER": a["user"], "DB_PASSWORD": a["password"]}


def _run(argv, env):
    out = io.StringIO()
    code = v.run(argv, env=env, out=out)
    return code, out.getvalue()


def test_schema_exact_and_inactive_passes_on_a_correctly_activated_schema(full, env):  # noqa: F811
    code, out = _run(["schema", "--expect", "exact", "--capture", "inactive"], env)
    assert code == 0, out
    assert "PASS migration22.state" in out and "PASS migration23.ml_models_columns" in out
    assert "PASS migration22.triggers_always" in out and "PASS capture.inactive" in out


def test_schema_expecting_absent_fails_when_migration_22_is_there(full, env):  # noqa: F811
    code, out = _run(["schema", "--expect", "absent"], env)
    assert code == 1 and "FAIL migration22.state" in out


def test_schema_detects_a_weakened_trigger(full, env):  # noqa: F811
    conn = full.admin()
    cur = conn.cursor()
    cur.execute("SELECT tgname FROM pg_trigger WHERE tgrelid = 'candidate_observation'::regclass AND NOT tgisinternal "
                "AND tgenabled = 'A' ORDER BY 1 LIMIT 1")
    trig = cur.fetchone()[0]
    conn.rollback()
    conn.close()
    full.exec_admin(f'ALTER TABLE candidate_observation ENABLE TRIGGER "{trig}"')     # 'O': fires normally, not ALWAYS
    try:
        code, out = _run(["schema", "--expect", "exact"], env)
    finally:
        full.exec_admin(f'ALTER TABLE candidate_observation ENABLE ALWAYS TRIGGER "{trig}"')
    assert code == 1 and ("FAIL migration22.triggers_always" in out or "FAIL migration22.state" in out)
    assert _run(["schema", "--expect", "exact"], env)[0] == 0


def test_schema_detects_missing_migration_23_columns(full, env):  # noqa: F811
    full.exec_admin("ALTER TABLE ml_models RENAME COLUMN evaluation TO evaluation_zz")
    try:
        code, out = _run(["schema", "--expect", "exact"], env)
    finally:
        full.exec_admin("ALTER TABLE ml_models RENAME COLUMN evaluation_zz TO evaluation")
    assert code == 1 and "FAIL migration23.ml_models_columns" in out


def test_schema_active_expectation_fails_without_a_boundary(full, env):  # noqa: F811
    code, out = _run(["schema", "--expect", "exact", "--capture", "active"], env)
    assert code == 1 and "FAIL capture.active" in out


def test_roles_absent_fails_and_present_passes_with_the_installed_names(full, env):  # noqa: F811
    db = v.ReadOnlyDB.connect(env)
    try:
        rep = _rep()
        v.check_roles(db, rep, "present", full.names)
        assert not rep.failed, rep.render()
        assert _status(rep, "privileges.runtime_coverage") == v.PASS
        rep = _rep()
        v.check_roles(db, rep, "absent", full.names)
        assert _status(rep, "roles.absent") == v.FAIL
        rep = _rep()                                          # names that exist nowhere in the cluster
        v.check_roles(db, rep, "absent", {r: "zz_none_" + r for r in v.RESEARCH_ROLES})
        assert not rep.failed, rep.render()
    finally:
        db.close()


def _roles_report(full, env):  # noqa: F811
    db = v.ReadOnlyDB.connect(env)
    try:
        rep = _rep()
        v.check_roles(db, rep, "present", full.names)
        return rep
    finally:
        db.close()


@pytest.mark.parametrize("break_sql,check", [
    ("REVOKE SELECT ON {view} FROM {app}", "privileges.runtime_coverage"),
    ("GRANT INSERT ON {view} TO {app}", "privileges.runtime_coverage"),
    ("GRANT TRUNCATE ON ml_models TO {app}", "privileges.runtime_coverage"),
    ("GRANT UPDATE ON candidate_observation TO {app}", "privileges.research_tables_read_insert_only"),
    ("GRANT EXECUTE ON FUNCTION research_capture_set_state(bigint,text,date,text) TO {app}", "privileges.maintenance_functions_denied"),
    ("GRANT CREATE ON SCHEMA public TO {app}", "privileges.no_schema_create"),
    ("GRANT {group} TO {app}", "roles.app_not_in.{group}"),
    ("ALTER ROLE {app} SUPERUSER", "roles.{app}.no_privileged_attributes"),
    ("ALTER TABLE feature_set_registry OWNER TO {app}", "roles.research_objects_owned_by_owner"),
    ("GRANT UPDATE ON market_snapshot TO {app}", "privileges.research_tables_read_insert_only"),
    ("GRANT DELETE ON market_event_revision TO {app}", "privileges.research_tables_read_insert_only"),
    ("REVOKE INSERT ON sector_snapshot FROM {app}", "privileges.market_intelligence_append_only"),
    ("GRANT UPDATE ON universe_snapshot TO {group}", "privileges.market_intelligence_append_only"),
    ("ALTER TABLE market_event OWNER TO {app}", "roles.research_objects_owned_by_owner"),
])
def test_role_drift_is_detected(full, env, snapshot_state, break_sql, check):  # noqa: F811
    view = full.admin().cursor()
    view.execute("SELECT relname FROM pg_class WHERE relkind = 'v' AND relnamespace = 'public'::regnamespace ORDER BY 1 LIMIT 1")
    vname = view.fetchone()[0]
    sql = break_sql.format(view=vname, app=full.app, group=full.group)
    full.exec_admin(sql)
    rep = _roles_report(full, env)
    expected = check.format(app=full.app, group=full.group)
    assert rep.failed and _status(rep, expected) == v.FAIL, rep.render()
    # an ownership change is not undone by the (idempotent) role script, so put it back explicitly
    full.exec_admin(f'ALTER TABLE feature_set_registry OWNER TO "{full.owner}"')
    full.exec_admin(f'ALTER ROLE "{full.app}" NOSUPERUSER')
    full.exec_admin(f'REVOKE "{full.group}" FROM "{full.app}"')


def test_a_clean_roles_state_is_restored_after_the_drift_tests(full, env):  # noqa: F811
    full.run_script(ROLES_SQL)
    rep = _roles_report(full, env)
    assert not rep.failed, rep.render()


def test_connections_flags_a_forbidden_user_still_connected(full, env):  # noqa: F811
    conn = psycopg2.connect(host=env["DB_HOST"], port=env["DB_PORT"], dbname=env["DB_NAME"], user=env["DB_USER"],
                            password=env["DB_PASSWORD"], application_name="donchian-bot")
    try:
        code, out = _run(["connections", "--forbid-user", env["DB_USER"]], env)
        assert code == 1 and "donchian-bot" in out
        code, out = _run(["connections", "--forbid-user", env["DB_USER"], "--allow-app", "donchian-bot"], env)
        assert code == 0, out
    finally:
        conn.close()


# ------------------------------------------------------------------ ledger evidence against the real schema
def _seed_ledger(full, session, n, prefix="Z"):  # noqa: F811
    full.exec_admin(
        "INSERT INTO signal_ledger (symbol, signal_date, direction, entry_price, atr, stop_price, target1_price, target2_price, "
        "target3_price, strategy_id, strategy_version) SELECT %s || g::text, %s, 1, 10, 1, 8, 12, 14, 16, "
        "(SELECT id FROM strategies WHERE strategy_key = 'donchian_breakout' AND strategy_version = 'v1'), 'v1' "
        "FROM generate_series(1, %s) g", prefix, session, n)


def _clear_ledger(full):  # noqa: F811
    full.exec_admin("DELETE FROM price_discontinuities")
    full.exec_admin("DELETE FROM stock_prices WHERE symbol LIKE 'ZD%'")
    full.exec_admin("DELETE FROM signal_ledger WHERE symbol LIKE 'Z%'")


@pytest.mark.parametrize("today_rows,ok", [(100, True), (50, True), (49, False), (200, True), (201, False)])
def test_candidate_count_is_judged_against_the_five_session_median(full, env, today_rows, ok):  # noqa: F811
    try:
        for i in range(1, 6):
            _seed_ledger(full, D - timedelta(days=i), 100, prefix=f"ZP{i}_")
        _seed_ledger(full, D, today_rows, prefix="ZT_")
        db = v.ReadOnlyDB.connect(env)
        try:
            rep = _rep()
            v.check_guard_db(db, rep, D, "inert", None)
        finally:
            db.close()
        assert (_status(rep, "guards.db.candidate_range") == v.PASS) is ok, rep.render()
    finally:
        _clear_ledger(full)


def test_too_little_history_is_a_warning_not_a_pass_or_a_fail(full, env):  # noqa: F811
    try:
        _seed_ledger(full, D - timedelta(days=1), 10, prefix="ZP_")
        _seed_ledger(full, D, 10, prefix="ZT_")
        db = v.ReadOnlyDB.connect(env)
        try:
            rep = _rep()
            v.check_guard_db(db, rep, D, "inert", None)
        finally:
            db.close()
        assert _status(rep, "guards.db.candidate_range") == v.WARN and not rep.failed
    finally:
        _clear_ledger(full)


def test_ledger_rows_the_screener_never_emitted_fail_the_reconciliation(full, env):  # noqa: F811
    try:
        for i in range(1, 6):
            _seed_ledger(full, D - timedelta(days=i), 5, prefix=f"ZP{i}_")
        _seed_ledger(full, D, 5, prefix="ZT_")
        db = v.ReadOnlyDB.connect(env)
        try:
            for breakouts, status in ((5, v.PASS), (6, v.PASS), (4, v.FAIL)):
                rep = _rep()
                v.check_guard_db(db, rep, D, "inert", breakouts)
                assert _status(rep, "guards.db.ledger_vs_results") in (status, v.WARN), rep.render()
                assert rep.failed is (status == v.FAIL)
        finally:
            db.close()
    finally:
        _clear_ledger(full)


def test_an_active_guard_run_whose_ledger_still_holds_a_discontinuity_symbol_fails(full, env):  # noqa: F811
    try:
        for i in range(1, 6):
            _seed_ledger(full, D - timedelta(days=i), 5, prefix=f"ZP{i}_")
        _seed_ledger(full, D, 4, prefix="ZT_")
        _seed_ledger(full, D, 1, prefix="ZD")                      # symbol ZD1
        full.exec_admin("INSERT INTO price_discontinuities (symbol, date, kind, prev_close, close, ratio) "
                        "VALUES ('ZD1', %s, 'jump_down', 100, 10, 0.1)", D - timedelta(days=10))
        full.exec_admin("INSERT INTO stock_prices (symbol, date, close) SELECT 'ZD1', %s::date - g, 10 "
                        "FROM generate_series(0, 12) g", D)
        db = v.ReadOnlyDB.connect(env)
        try:
            rep = _rep()
            v.check_guard_db(db, rep, D, "active", None)
            assert _status(rep, "guards.db.no_discontinuity_in_ledger") == v.FAIL and "ZD1" in rep.render()
            rep = _rep()
            v.check_guard_db(db, rep, D, "inert", None)                    # inert runs are not judged on this
            assert not _by(rep, "guards.db.no_discontinuity_in_ledger")
        finally:
            db.close()
    finally:
        _clear_ledger(full)


def test_capture_for_a_session_with_no_run_row_fails_on_the_real_schema(full, env):  # noqa: F811
    code, out = _run(["capture", "--session", "2099-01-15"], env)
    assert code == 1 and "FAIL capture.run_row" in out


def test_a_capture_session_before_the_guards_boundary_fails(full, env):  # noqa: F811
    _, out = _run(["capture", "--session", "2099-01-15", "--guards-from", "2099-01-16"], env)
    assert "FAIL capture.after_guards_boundary" in out
    _, out = _run(["capture", "--session", "2099-01-15", "--guards-from", "2099-01-15"], env)
    assert "PASS capture.after_guards_boundary" in out


def test_delivery_on_the_real_schema_reports_missing_rows(full, env):  # noqa: F811
    code, out = _run(["delivery", "--session", "2099-01-15"], env)
    assert code == 1 and "FAIL delivery.prod_sent" in out


# ------------------------------------------------------------------ the tool cannot write
def _data_fingerprint(full):  # noqa: F811
    conn = full.admin()
    try:
        cur = conn.cursor()
        cur.execute("SELECT relname FROM pg_class WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace ORDER BY 1")
        counts = {}
        for (t,) in cur.fetchall():
            cur.execute(f'SELECT count(*) FROM "{t}"')
            counts[t] = cur.fetchone()[0]
        cur.execute("SELECT md5(string_agg(c.relname || a.attname || format_type(a.atttypid, a.atttypmod), ',' ORDER BY c.relname, a.attnum)) "
                    "FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid WHERE c.relnamespace = 'public'::regnamespace "
                    "AND c.relkind IN ('r','v') AND a.attnum > 0 AND NOT a.attisdropped")
        shape = cur.fetchone()[0]
        cur.execute("SELECT md5(string_agg(rolname || rolsuper::text || rolcanlogin::text, ',' ORDER BY rolname)) FROM pg_roles "
                    "WHERE rolname LIKE %s", (full.prefix + "%",))
        roles = cur.fetchone()[0]
        cur.execute("SELECT md5(string_agg(relacl::text, ',' ORDER BY relname)) FROM pg_class WHERE relnamespace = 'public'::regnamespace")
        acls = cur.fetchone()[0]
        return counts, shape, roles, acls
    finally:
        conn.rollback()
        conn.close()


def test_the_server_itself_refuses_a_write_on_the_validators_connection(full, env):  # noqa: F811
    """Layer 1 on its own: bypass the statement gate and talk to the raw connection."""
    db = v.ReadOnlyDB.connect(env)
    try:
        assert db.scalar("SHOW transaction_read_only") == "on"
        cur = db.conn.cursor()
        for sql in ("INSERT INTO strategies (strategy_key, strategy_version) VALUES ('zz', 'v0')", "CREATE TABLE zz_probe (x int)",
                    "UPDATE strategies SET description = 'x'", "DELETE FROM strategies", "TRUNCATE strategies",
                    "CREATE ROLE zz_probe_role", "SELECT nextval('signal_ledger_id_seq')"):
            with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
                cur.execute(sql)
            db.conn.rollback()
    finally:
        db.close()


def test_the_statement_gate_refuses_a_write_before_it_reaches_the_server(full, env):  # noqa: F811
    db = v.ReadOnlyDB.connect(env)
    try:
        with pytest.raises(v.ReadOnlyViolation):
            db.q("DELETE FROM signal_ledger")
        with pytest.raises(v.ReadOnlyViolation):
            db.scalar("SELECT 1; DROP TABLE strategies")
    finally:
        db.close()


def test_a_full_validation_run_leaves_the_database_exactly_as_it_found_it(full, env):  # noqa: F811
    before = _data_fingerprint(full)
    for argv in (["schema", "--expect", "exact", "--capture", "any"], ["connections"], ["capture", "--session", "2099-01-15"],
                 ["delivery", "--session", "2099-01-15"], ["guards", "--session", "2099-01-15", "--expect", "inert"],
                 ["config", "--check-boundary-vs-db"]):
        _run(argv, env)
    db = v.ReadOnlyDB.connect(env)
    try:
        v.check_roles(db, _rep(), "present", full.names)
    finally:
        db.close()
    assert _data_fingerprint(full) == before


def test_a_connection_failure_exits_2_without_leaking_the_password(capsys):
    bad = {"DB_HOST": "127.0.0.1", "DB_PORT": "1", "DB_NAME": "x", "DB_USER": "u", "DB_PASSWORD": "hunter2-unique"}
    code = v.run(["schema", "--expect", "exact"], env=bad, out=io.StringIO())
    captured = capsys.readouterr()
    assert code == 2 and "hunter2-unique" not in captured.err + captured.out


def test_a_query_error_is_a_failed_check_never_a_silent_pass(full, env):  # noqa: F811
    code, out = _run(["schema", "--expect", "exact"], dict(env, DB_NAME="postgres"))
    assert code == 1 and "FAIL" in out


def test_connections_refuses_to_judge_from_a_runtime_role_that_cannot_see_other_sessions(full, env):  # noqa: F811
    app_env = dict(env, DB_USER=full.app, DB_PASSWORD="appsecret-" + uuid.uuid4().hex)
    full.exec_admin(f'ALTER ROLE "{full.app}" PASSWORD %s', app_env["DB_PASSWORD"])
    code, out = _run(["connections", "--forbid-user", "trading_user"], app_env)
    assert code == 1 and "FAIL connections.visibility" in out and app_env["DB_PASSWORD"] not in out


def test_ml_models_expectation_can_be_absent_present_or_unchecked(full, env):  # noqa: F811
    assert _run(["schema", "--expect", "exact", "--ml-models", "present"], env)[0] == 0
    code, out = _run(["schema", "--expect", "exact", "--ml-models", "absent"], env)
    assert code == 1 and "FAIL migration23.ml_models_columns" in out
    assert _run(["schema", "--expect", "exact", "--ml-models", "any"], env)[0] == 0
    full.exec_admin("ALTER TABLE ml_models RENAME COLUMN evaluation TO evaluation_zz")
    full.exec_admin("ALTER TABLE ml_models RENAME COLUMN feature_set_version TO feature_set_version_zz")
    full.exec_admin("ALTER TABLE ml_models RENAME COLUMN target TO target_zz")
    try:
        assert _run(["schema", "--expect", "exact", "--ml-models", "absent"], env)[0] == 0      # the pre-S5 production state
        assert _run(["schema", "--expect", "exact"], env)[0] == 1                               # default demands them
    finally:
        full.exec_admin("ALTER TABLE ml_models RENAME COLUMN evaluation_zz TO evaluation")
        full.exec_admin("ALTER TABLE ml_models RENAME COLUMN feature_set_version_zz TO feature_set_version")
        full.exec_admin("ALTER TABLE ml_models RENAME COLUMN target_zz TO target")
