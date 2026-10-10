"""Migration 30 + registry_store against a real throwaway schema: the database refuses what the pure validator refuses."""
import psycopg2
import pytest

from lab_samples import H, SHA, exp, manifest
from research.lab import manifest as M
from research.lab import registry_store as R


def _result(reg, kind="validation", **o):
    base = dict(registration_hash=reg.registration_hash, result_kind=kind,
                metrics={"auc": 0.51, "brier": 0.25} if kind in ("validation", "test") else {}, n_configs_tried=1,
                artifact_hashes={"model": H}, code_sha=SHA)
    base.update(o)
    return M.ResultSpec(**base)


@pytest.fixture
def cur(registry_env):
    _, connect = registry_env
    with connect() as c:
        yield c.cursor()
        c.rollback()


def _raw_result(cur, reg_hash, kind, metrics="{}", tried=1):
    cur.execute("INSERT INTO experiment_result (registration_hash, result_kind, metrics, n_configs_tried, artifact_hashes, code_sha) "
                "VALUES (%s,%s,%s::jsonb,%s,'{}'::jsonb,%s)", (reg_hash, kind, metrics, tried, SHA))


def _expect(cur, match, fn):
    cur.execute("SAVEPOINT s")
    with pytest.raises(psycopg2.Error) as e:
        fn()
    assert match in str(e.value), str(e.value)
    cur.execute("ROLLBACK TO SAVEPOINT s")


def test_manifest_round_trips_idempotently_and_the_database_stamps_created_at(cur):
    m = manifest()
    a = R.insert_manifest(cur, m)
    b = R.insert_manifest(cur, m)
    assert a["created"] is True and b["created"] is False and a["id"] == b["id"]
    got = R.get_manifest(cur, m.manifest_hash)
    assert got["manifest"]["manifest_hash"] if "manifest_hash" in got["manifest"] else True
    assert got["manifest"]["code_sha"] == SHA and got["created_at"] is not None
    assert got["manifest"]["windows"] == m.document["windows"]


def test_the_same_name_and_version_with_different_content_is_refused(cur):
    R.insert_manifest(cur, manifest())
    _expect(cur, "dataset_manifest_name_version", lambda: R.insert_manifest(cur, manifest(config={"filter": 1})))


def test_database_checks_repeat_the_leakage_controls(cur):
    row = manifest().row()
    cases = [("embargo_sessions", 59, "embargo_chk"), ("train_start", row["train_end"].replace(year=row["train_end"].year + 1), "window_order_chk"),
             ("code_sha", "A" * 40, "sha_chk"), ("label_horizons", [7], "horizons_chk"),
             ("label_maturity_session", row["test_end"], "cutoff_chk")]
    for col, val, match in cases:
        bad = dict(row, **{col: val})
        bad["manifest_hash"] = H
        _expect(cur, match, lambda bad=bad: R._insert(cur, "dataset_manifest", R.MANIFEST_COLS, bad, "manifest_hash"))
    # the database also bounds the gap in calendar days (a necessary lower bound for the session count)
    bad = dict(row, manifest_hash=H, validation_start=row["train_end"].replace(year=row["train_end"].year + 5))
    _expect(cur, "chk", lambda: R._insert(cur, "dataset_manifest", R.MANIFEST_COLS, bad, "manifest_hash"))


def test_a_future_cutoff_is_refused_by_the_database_clock(cur):
    from datetime import timedelta
    row = dict(manifest().row())
    row["manifest_hash"] = H
    row["knowledge_cutoff_at"] = R.db_now(cur) + timedelta(days=2)
    row["label_maturity_session"] = row["test_end"].replace(year=row["test_end"].year)  # unchanged; only the cutoff is the issue
    row["label_maturity_session"] = row["label_maturity_session"] if row["label_maturity_session"] > row["test_end"] else row["test_end"]
    _expect(cur, "future", lambda: R._insert(cur, "dataset_manifest", R.MANIFEST_COLS, row, "manifest_hash"))


def test_manifests_registrations_and_results_are_append_only(cur):
    m = manifest()
    R.insert_manifest(cur, m)
    reg = M.build_registration(exp(m))
    R.insert_registration(cur, reg)
    R.append_result(cur, _result(reg), reg)
    for table in ("dataset_manifest", "experiment_registration", "experiment_result"):
        _expect(cur, "append-only", lambda t=table: cur.execute(f"UPDATE {t} SET code_sha = code_sha"))
        _expect(cur, "append-only", lambda t=table: cur.execute(f"DELETE FROM {t}"))
        _expect(cur, "append-only", lambda t=table: cur.execute(f"TRUNCATE {t} CASCADE"))


def test_registration_requires_an_existing_manifest_and_matching_label_and_feature_subset(cur):
    reg = M.build_registration(exp(manifest()))
    _expect(cur, "does not exist", lambda: R.insert_registration(cur, reg))
    R.insert_manifest(cur, manifest())
    row = dict(reg.row())
    cases = [("label_version", "fwd_v2", "differs from the manifest"), ("feature_versions", {"zzz": "1"}, "not a subset")]
    for col, val, match in cases:
        bad = dict(row, **{col: val}, registration_hash="c" * 64, experiment_name="x" + col)
        _expect(cur, match, lambda bad=bad: R._insert(cur, "experiment_registration", R.REG_COLS, bad, "registration_hash"))
    assert R.insert_registration(cur, reg)["created"] is True
    assert R.insert_registration(cur, reg)["created"] is False


def test_the_database_requires_a_complete_evaluation_plan_and_budget(cur):
    R.insert_manifest(cur, manifest())
    reg = M.build_registration(exp(manifest()))
    row = dict(reg.row(), registration_hash="d" * 64, experiment_name="other")
    # the database enforces the plan's required keys; that primary_metric is one of the metrics is the pure validator's rule
    for plan in ({"metrics": ["auc"], "primary_metric": "auc"}, {"primary_metric": "auc", "decision_rule": "r"}, {}):
        bad = dict(row, evaluation_plan=plan)
        _expect(cur, "json_chk", lambda bad=bad: R._insert(cur, "experiment_registration", R.REG_COLS, bad, "registration_hash"))
    _expect(cur, "budget_chk", lambda: R._insert(cur, "experiment_registration", R.REG_COLS, dict(row, search_budget=0), "registration_hash"))


def test_results_obey_budget_validation_first_test_once_and_closure_in_the_database_itself(cur):
    R.insert_manifest(cur, manifest())
    reg = M.build_registration(exp(manifest()))
    R.insert_registration(cur, reg)
    h = reg.registration_hash
    _expect(cur, "does not exist", lambda: _raw_result(cur, "e" * 64, "failed"))
    _expect(cur, "prior validation", lambda: _raw_result(cur, h, "test", '{"auc": 0.5}'))
    _expect(cur, "search_budget", lambda: _raw_result(cur, h, "failed", tried=4))
    _expect(cur, "metrics_chk", lambda: _raw_result(cur, h, "validation", "{}"))
    _expect(cur, "metrics_chk", lambda: _raw_result(cur, h, "failed", '{"auc": 0.5}'))
    _raw_result(cur, h, "validation", '{"auc": 0.5, "brier": 0.2}')
    _raw_result(cur, h, "test", '{"auc": 0.5, "brier": 0.2}')
    _expect(cur, "one_test", lambda: _raw_result(cur, h, "test", '{"auc": 0.5, "brier": 0.2}'))
    _raw_result(cur, h, "abandoned")
    _expect(cur, "closed", lambda: _raw_result(cur, h, "validation", '{"auc": 0.5, "brier": 0.2}'))


def test_append_result_applies_the_pure_rules_against_the_stored_history(cur):
    R.insert_manifest(cur, manifest())
    reg = M.build_registration(exp(manifest()))
    R.insert_registration(cur, reg)
    with pytest.raises(M.LabError):
        R.append_result(cur, _result(reg, "test"), reg)                    # no validation yet
    R.append_result(cur, _result(reg), reg)
    R.append_result(cur, _result(reg, "test"), reg)
    with pytest.raises(M.LabError):
        R.append_result(cur, _result(reg, "test"), reg)                    # a test set is spent once
    assert R.result_kinds(cur, reg.registration_hash) == ["validation", "test"]


def test_the_migration_is_reapplicable_and_refuses_a_foreign_object(registry_env):
    from pathlib import Path
    from conftest import ROOT
    sql = (Path(ROOT) / "mechanism" / "add_dataset_experiment_registry_tables.sql").read_text(encoding="utf-8")
    _, connect = registry_env
    with connect() as c:
        c.cursor().execute(sql)
        c.commit()
        cur = c.cursor()
        cur.execute("DROP TABLE experiment_result")
        cur.execute("CREATE TABLE experiment_result (x int)")
        c.commit()
        with pytest.raises(psycopg2.Error) as e:
            c.cursor().execute(sql)
        assert "migration 30 refused" in str(e.value)
        c.rollback()


def test_no_lab_module_updates_deletes_or_truncates(registry_env):
    import inspect
    import ast
    tree = ast.parse(inspect.getsource(R))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    sql = " ".join(n.value.upper() for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and id(n) not in docstrings)
    for w in ("UPDATE ", "DELETE ", "TRUNCATE", "DROP ", "ALTER "):
        assert w not in sql, w
    assert "INSERT INTO" in sql
