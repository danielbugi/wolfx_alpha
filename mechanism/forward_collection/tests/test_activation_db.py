"""The complete activation preflight against a REAL production-shaped database: a throwaway database bootstrapped from the compose init list,
the real role script under throwaway names, and the preflight connecting as the LEAST-PRIVILEGE runtime role in a read-only session.

There is no monkeypatched YES: the one YES case is a database that really has migrations 22 and 24-32, guarded tables, a least-privilege runtime role,
a derivable calendar, fresh fundamentals, a tmp repo carrying the committed scheduler units and every owner gate supplied. Every NO case breaks exactly
one real thing in the database (and restores it through the real SQL), so each blocker is proven to bite."""
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import forward_world as FW
import full_env as FE
from forward_collection import activation as A

ALL_GATES = {k: "yes" for k in A.GATES}


@pytest.fixture(scope="module")
def env():
    e, teardown = FE.make_env()
    e.load_market()
    try:
        yield e
    finally:
        teardown()


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    """A tmp repo root: the real compose list + migration files + the REAL committed (dormant) collector units and wrapper."""
    r = tmp_path_factory.mktemp("repo")
    shutil.copy(FE.ROOT + "/docker-compose.yml", r / "docker-compose.yml")
    (r / "mechanism").mkdir()
    for f in FE.init_files():
        shutil.copy(f, r / "mechanism" / f.replace("\\", "/").rsplit("/", 1)[1])
    vps = r / "deploy" / "vps"
    vps.mkdir(parents=True)
    for name in A.UNIT_FILES + A.SCAN_FILES:
        shutil.copy(FE.ROOT + "/deploy/vps/" + name, vps / name)
    return r


def spec():
    w = SimpleNamespace(sessions=FE.weekdays(8))
    return FW.status_spec_doc(w, train=(0, 1), validation=(2, 3), test=(4, 5), maturity=5, cutoff=datetime(2026, 3, 20, 6, tzinfo=timezone.utc))


def readonly(factory):
    @contextmanager
    def connect():
        conn = factory()
        conn.set_session(readonly=True)
        try:
            yield conn
        finally:
            conn.rollback()
            conn.close()
    return connect


def run(env, repo, *, gates=ALL_GATES, as_app=True, **kw):
    connect = readonly(env.app_connect_factory() if as_app else env.admin_connect_factory())
    return A.run_activation_preflight(connect, spec(), env={}, repo_root=repo, gates=gates, runtime_role=env.app, **kw)


def by_id(rep):
    return {c["id"]: c for layer in rep["layers"].values() for c in layer}


def restore(env):
    for f in FE.init_files()[21:]:                                          # migrations 22..31 are idempotent (IF NOT EXISTS)
        with open(f, encoding="utf-8") as fh:
            env.exec_admin(fh.read())
    env.exec_admin(env.roles_sql())
    env.exec_admin(f'ALTER ROLE "{env.app}" NOSUPERUSER')


@pytest.fixture
def repaired(env):
    yield
    restore(env)


def counts(env):
    conn = env.admin()
    try:
        cur = conn.cursor()
        out = {}
        for t in ("sector_observation", "sector_poll", "universe_snapshot", "candidate_observation", "research_capture_activation",
                  "daily_fundamentals", "stock_prices"):
            cur.execute(f"SELECT count(*) FROM {t}")
            out[t] = cur.fetchone()[0]
        return out
    finally:
        conn.close()


def test_every_layer_is_yes_when_the_environment_really_is_ready_and_the_preflight_runs_as_the_least_privilege_role(env, repo):
    before = counts(env)
    rep = run(env, repo)
    assert rep["schema"] == "forward_collection_activation_v1" and rep["read_only"] is True
    assert rep["blockers"] == {"architecture": [], "environment": [], "activation": []}, rep["blockers"]
    assert (rep["architecture_ready"], rep["environment_ready"], rep["activation_ready"]) == ("YES", "YES", "YES")
    assert rep["forward_research_collection_ready_for_activation"] == "YES" and rep["collector_stack_ready_for_activation"] == "YES"
    assert counts(env) == before                                          # it read, nothing else
    c = by_id(rep)
    assert c["runtime_role_is_least_privilege_on_research_tables"]["detail"]["problems"] == []
    assert c["migrations_22_and_24_to_32_applied"]["detail"]["missing_by_migration"] == {}
    assert c["fundamentals_refresh_is_recent"]["detail"]["gap_days"] == 0


def test_the_same_database_is_equally_ready_when_asked_by_an_administrator(env, repo):
    rep = run(env, repo, as_app=False)
    assert rep["forward_research_collection_ready_for_activation"] == "YES", rep["blockers"]


def test_the_real_repo_is_architecture_and_environment_ready_but_not_activation_ready_with_no_gates(env):
    """Committing the units satisfied the one automatic gate; every owner gate still defaults to NO, so committing changes nothing about activation."""
    rep = run(env, A.REPO_ROOT, gates={})
    assert rep["architecture_ready"] == "YES" and rep["environment_ready"] == "YES"
    assert rep["activation_ready"] == "NO" and rep["forward_research_collection_ready_for_activation"] == "NO"
    assert sorted(rep["blockers"]["activation"]) == sorted(f"gate:{k}" for k in A.GATES)
    assert by_id(rep)["gate:scheduler_units_committed"]["ok"] is True


@pytest.mark.parametrize("gate", sorted(A.GATES))
def test_each_owner_gate_alone_blocks_the_final_answer(env, repo, gate):
    rep = run(env, repo, gates={k: v for k, v in ALL_GATES.items() if k != gate})
    assert rep["architecture_ready"] == rep["environment_ready"] == "YES" and rep["activation_ready"] == "NO"
    assert rep["blockers"]["activation"] == [f"gate:{gate}"] and rep["forward_research_collection_ready_for_activation"] == "NO"


def test_a_missing_sector_history_migration_is_an_environment_blocker_and_names_the_migration(env, repo, repaired):
    env.exec_admin("DROP TABLE sector_reconstruction")
    rep = run(env, repo, as_app=False)
    assert rep["environment_ready"] == "NO" and rep["forward_research_collection_ready_for_activation"] == "NO"
    assert rep["architecture_ready"] == "YES"
    assert by_id(rep)["migrations_22_and_24_to_32_applied"]["detail"]["missing_by_migration"]["31"]["tables"] == ["sector_reconstruction"]


def test_a_missing_relative_strength_migration_is_an_environment_blocker(env, repo, repaired):
    env.exec_admin("DROP TABLE stock_relative_strength")
    rep = run(env, repo, as_app=False)
    assert "migrations_22_and_24_to_32_applied" in rep["blockers"]["environment"]
    assert by_id(rep)["migrations_22_and_24_to_32_applied"]["detail"]["missing_by_migration"]["29"]["tables"] == ["stock_relative_strength"]


def test_a_runtime_role_that_can_rewrite_history_is_an_environment_blocker(env, repo, repaired):
    env.exec_admin(f'GRANT UPDATE ON sector_observation TO "{env.app}"')
    rep = run(env, repo, as_app=False)
    d = by_id(rep)["runtime_role_is_least_privilege_on_research_tables"]
    assert not d["ok"] and "sector_observation: has UPDATE" in d["detail"]["problems"]
    assert rep["environment_ready"] == "NO"


def test_a_runtime_role_that_can_change_the_capture_boundary_is_an_environment_blocker(env, repo, repaired):
    env.exec_admin(f'GRANT INSERT ON research_capture_activation TO "{env.app}"')
    d = by_id(run(env, repo, as_app=False))["runtime_role_is_least_privilege_on_research_tables"]
    assert not d["ok"] and "research_capture_activation: has INSERT" in d["detail"]["problems"]


def test_a_runtime_role_that_cannot_append_is_an_environment_blocker(env, repo, repaired):
    env.exec_admin(f'REVOKE INSERT ON sector_poll FROM "{env.app}"')
    d = by_id(run(env, repo, as_app=False))["runtime_role_is_least_privilege_on_research_tables"]
    assert not d["ok"] and "sector_poll: lacks INSERT" in d["detail"]["problems"]


def test_a_runtime_role_that_owns_a_research_table_is_an_environment_blocker(env, repo, repaired):
    env.exec_admin(f'ALTER TABLE sector_poll OWNER TO "{env.app}"')
    d = by_id(run(env, repo, as_app=False))["runtime_role_is_least_privilege_on_research_tables"]
    assert not d["ok"] and "sector_poll: owned by the runtime role" in d["detail"]["problems"]


def test_a_superuser_runtime_role_is_an_environment_blocker(env, repo, repaired):
    env.exec_admin(f'ALTER ROLE "{env.app}" SUPERUSER')
    d = by_id(run(env, repo, as_app=False))["runtime_role_exists_with_no_privileged_attributes"]
    assert not d["ok"] and d["detail"]["attributes"]["superuser"] is True


def test_a_runtime_role_that_does_not_exist_is_an_environment_blocker(env, repo):
    rep = A.run_activation_preflight(readonly(env.admin_connect_factory()), spec(), env={}, repo_root=repo, gates=ALL_GATES, runtime_role="no_such_role_xyz")
    c = by_id(rep)
    assert not c["runtime_role_exists_with_no_privileged_attributes"]["ok"] and rep["environment_ready"] == "NO"


def test_an_immutability_trigger_that_is_not_always_enabled_is_an_environment_blocker(env, repo, repaired):
    conn = env.admin()
    cur = conn.cursor()
    cur.execute("SELECT tgname FROM pg_trigger WHERE tgrelid = 'sector_poll'::regclass AND NOT tgisinternal")
    names = [r[0] for r in cur.fetchall()]
    for n in names:
        cur.execute(f'ALTER TABLE sector_poll ENABLE TRIGGER "{n}"')               # origin-only: bypassed by a replica-role session
    conn.commit()
    conn.close()
    rep = run(env, repo, as_app=False)
    assert by_id(rep)["research_tables_guarded_by_enable_always_triggers"]["detail"]["unguarded"] == ["sector_poll"]
    assert rep["environment_ready"] == "NO"
    conn = env.admin()
    cur = conn.cursor()
    for n in names:
        cur.execute(f'ALTER TABLE sector_poll ENABLE ALWAYS TRIGGER "{n}"')
    conn.commit()
    conn.close()
    assert run(env, repo, as_app=False)["environment_ready"] == "YES"


def test_a_fundamentals_refresh_older_than_the_cadence_is_an_environment_blocker_and_fresh_again_clears_it(env, repo):
    env.exec_admin("UPDATE daily_fundamentals SET date = date - 10")
    try:
        rep = run(env, repo, as_app=False)
        d = by_id(rep)["fundamentals_refresh_is_recent"]
        assert not d["ok"] and d["detail"]["gap_days"] == 10 and rep["environment_ready"] == "NO"
    finally:
        env.exec_admin("UPDATE daily_fundamentals SET date = date + 10")
    assert run(env, repo, as_app=False)["environment_ready"] == "YES"


def test_an_empty_market_means_the_calendar_and_freshness_blockers(env, repo):
    other, teardown = FE.make_env("fcb")
    try:
        rep = A.run_activation_preflight(readonly(other.admin_connect_factory()), spec(), env={}, repo_root=repo, gates=ALL_GATES, runtime_role=other.app)
        c = by_id(rep)
        assert not c["trading_calendar_derivable"]["ok"] and not c["fundamentals_refresh_is_recent"]["ok"]
        assert rep["forward_research_collection_ready_for_activation"] == "NO"
        assert c["migrations_22_and_24_to_32_applied"]["ok"]                  # migrations are present even though no data was loaded
    finally:
        teardown()


def test_a_database_without_the_research_migrations_is_environment_no_not_a_crash(env, repo):
    other, teardown = FE.make_env("fcc")
    try:
        other.exec_admin("DROP TABLE sector_observation, sector_poll, sector_reconstruction, forward_return_label CASCADE")
        rep = A.run_activation_preflight(readonly(other.admin_connect_factory()), spec(), env={}, repo_root=repo, gates=ALL_GATES, runtime_role=other.app)
        assert rep["environment_ready"] == "NO" and "required_tables_exist" in rep["blockers"]["environment"]
    finally:
        teardown()


def test_a_real_spec_error_surfaces_in_the_architecture_layer_not_as_a_yes(env, repo):
    bad = spec()
    bad["config"] = {**bad["config"], "market": {"feature_set_version": "mi_v9"}}
    rep = A.run_activation_preflight(readonly(env.admin_connect_factory()), bad, env={}, repo_root=repo, gates=ALL_GATES, runtime_role=env.app)
    assert rep["architecture_ready"] == "NO" and "spec_versions_match_collectors" in rep["blockers"]["architecture"]
    assert rep["forward_research_collection_ready_for_activation"] == "NO"


def test_the_activation_command_prints_the_layers_and_exits_nonzero_unless_everything_is_yes(env, tmp_path):
    import json

    from forward_collection import cli as CLI
    from forward_collection import contract as C
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec()), encoding="utf-8")
    out = []
    gates = [a for k in A.GATES for a in ("--gate", f"{k}=yes")]
    rc = CLI.main(["activation", "--spec", str(path), "--runtime-role", env.app, *gates], connect=readonly(env.app_connect_factory()), out=out.append)
    doc = json.loads(out[-1])
    assert doc["schema"] == "forward_collection_activation_v1" and doc["read_only"] is True
    assert doc["architecture_ready"] == doc["environment_ready"] == doc["activation_ready"] == "YES"      # every gate the TEST supplied, on a faithful throwaway database
    assert doc["blockers"]["activation"] == [] and rc == C.EXIT_COMPLETE
    assert doc["forward_research_collection_ready_for_activation"] == "YES"
    out2 = []
    some = [a for k in A.GATES if k != "s11_passed" for a in ("--gate", f"{k}=yes")]
    rc2 = CLI.main(["activation", "--spec", str(path), "--runtime-role", env.app, *some], connect=readonly(env.app_connect_factory()), out=out2.append)
    doc2 = json.loads(out2[-1])
    assert doc2["blockers"]["activation"] == ["gate:s11_passed"] and rc2 == C.EXIT_INCOMPLETE and doc2["forward_research_collection_ready_for_activation"] == "NO"
