"""Migration 23 / no-runtime-DDL guards.

register_model() used to run `ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS ...` at runtime. That needs table ownership,
which the least-privilege runtime role (donchian_app) does not have, and its failure was swallowed as a WARNING. The
schema now lives in mechanism/add_ml_models_registry_columns.sql and the application only does a read-only check.
No real Postgres needed for the mock tests; the real-catalog test skips when Postgres is unreachable (CI has one).
"""
import os
import re
import sys
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ml_training", "models"))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

import momentum_predictor as mp  # noqa: E402

MIGRATION = os.path.join(ROOT, "mechanism", "add_ml_models_registry_columns.sql")
DDL = re.compile(r"(?i:ALTER|CREATE|DROP|TRUNCATE)\s+(?i:TABLE|INDEX|SEQUENCE|VIEW|MATERIALIZED|FUNCTION|SCHEMA|EXTENSION|"
                 r"ROLE|TYPE|TRIGGER|DATABASE)|(?:GRANT|REVOKE)\s+[A-Z_, ]+\s+(?:ON|TO|FROM)")
# Legacy modules that are not on any live path (CLAUDE.md "Known legacy paths"); everything else must be DDL-free.
LEGACY_ALLOWED = {"momentum_labeler.py", "momentum_labeler_backup.py", "map_project.py"}
RUNTIME_DIRS = ("mechanism", "backend", "ml_training")


def _mock_conn(rows):
    conn, cur = MagicMock(), MagicMock()
    conn.cursor.return_value = cur
    cur.fetchall.return_value = rows
    return conn, cur


def test_check_reports_missing_columns_and_only_selects():
    conn, cur = _mock_conn([("target",)])
    assert mp.missing_registry_columns(conn) == ["evaluation", "feature_set_version"]
    for call in cur.execute.call_args_list:
        assert call.args[0].lstrip().upper().startswith("SELECT"), call.args[0]
    conn.commit.assert_not_called()


def test_check_is_empty_when_all_present():
    conn, _ = _mock_conn([(c,) for c in mp.REGISTRY_REQUIRED_COLUMNS])
    assert mp.missing_registry_columns(conn) == []


def test_assert_registry_ready_raises_with_actionable_message():
    pred = mp.MomentumBreakoutPredictor.__new__(mp.MomentumBreakoutPredictor)
    pred.db_config = {}
    conn, _ = _mock_conn([])
    with patch.object(mp.psycopg2, "connect", return_value=conn):
        with pytest.raises(mp.RegistrySchemaError, match="migration 23"):
            pred.assert_registry_ready()
    conn.close.assert_called_once()


def test_run_fails_fast_before_loading_data_when_promoting():
    pred = mp.MomentumBreakoutPredictor.__new__(mp.MomentumBreakoutPredictor)
    pred.load_dataset = MagicMock(side_effect=AssertionError("must not load data first"))
    pred.assert_registry_ready = MagicMock(side_effect=mp.RegistrySchemaError("x"))
    with pytest.raises(mp.RegistrySchemaError):
        pred.run(promote=True)
    pred.load_dataset.assert_not_called()


def test_no_promote_does_not_touch_the_registry():
    pred = mp.MomentumBreakoutPredictor.__new__(mp.MomentumBreakoutPredictor)
    pred.target = "momentum"
    pred.assert_registry_ready = MagicMock()
    pred.load_dataset = MagicMock(side_effect=StopIteration)   # stop right after the registry decision
    with pytest.raises(StopIteration):
        pred.run(promote=False)
    pred.assert_registry_ready.assert_not_called()


def test_register_model_issues_no_ddl():
    pred = mp.MomentumBreakoutPredictor.__new__(mp.MomentumBreakoutPredictor)
    pred.db_config, pred.model_version, pred.feature_names, pred.target = {}, "v", ["a"], "momentum"
    conn, cur = _mock_conn([])
    report = {"holdout": {"auc": 0.6}, "n_train": 10}
    with patch.object(mp.psycopg2, "connect", return_value=conn):
        pred.register_model(report, None, None, "p")
    assert cur.execute.call_count == 2   # deactivate previous + insert -- nothing else
    for call in cur.execute.call_args_list:
        assert not DDL.search(call.args[0]), call.args[0]


def test_migration_23_is_additive_idempotent_ddl_only():
    sql = "\n".join(l for l in open(MIGRATION, encoding="utf-8").read().splitlines() if not l.lstrip().startswith("--"))
    stmts = [s.strip() for s in sql.split(";") if s.strip()]
    assert len(stmts) == 3
    for s in stmts:
        assert re.fullmatch(r"ALTER TABLE ml_models ADD COLUMN IF NOT EXISTS (evaluation JSONB|feature_set_version "
                            r"VARCHAR\(20\)|target VARCHAR\(30\))", s), s
    cols = {re.search(r"EXISTS (\w+)", s).group(1) for s in stmts}
    assert cols == set(mp.REGISTRY_REQUIRED_COLUMNS)
    assert not re.search(r"\b(DROP|TRUNCATE|DELETE|UPDATE|INSERT)\b", sql, re.I)


def test_migration_23_is_mounted_in_compose_in_order():
    text = open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8").read()
    nums = re.findall(r"- \./mechanism/(\S+\.sql):/docker-entrypoint-initdb\.d/(\d+)_", text)
    assert ("add_ml_models_registry_columns.sql", "23") in nums
    order = [int(n) for _, n in nums]
    assert order == sorted(order) and len(set(order)) == len(order)


def test_no_runtime_python_issues_schema_or_privilege_ddl():
    """Repo-wide regression guard: the live path never mutates schema or grants. Tests, migrations (*.sql) and the
    named legacy modules are exempt."""
    offenders = []
    for top in RUNTIME_DIRS:
        for dirpath, dirs, files in os.walk(os.path.join(ROOT, top)):
            dirs[:] = [d for d in dirs if d not in {"tests", "__pycache__", "node_modules", ".venv", "venv"}]
            for f in files:
                if not f.endswith(".py") or f.startswith("test_") or f in LEGACY_ALLOWED:
                    continue
                path = os.path.join(dirpath, f)
                for n, line in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
                    code = line.split("#", 1)[0]
                    if DDL.search(code) and not code.strip().startswith(('"""', "'''", "--")):
                        offenders.append(f"{os.path.relpath(path, ROOT)}:{n}: {line.strip()[:100]}")
    assert not offenders, "runtime DDL/DCL found:\n" + "\n".join(offenders)


def test_real_catalog_check_against_postgres():
    psycopg2 = pytest.importorskip("psycopg2")
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    try:
        conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
                                dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable: {type(e).__name__}")
    try:
        cur = conn.cursor()
        cur.execute("CREATE SCHEMA mlreg_t")
        cur.execute("SET search_path TO mlreg_t")
        cur.execute("CREATE TABLE ml_models (id BIGSERIAL PRIMARY KEY, model_name VARCHAR(50) NOT NULL, "
                    "version VARCHAR(50) NOT NULL, is_active BOOLEAN DEFAULT FALSE)")
        assert mp.missing_registry_columns(conn) == list(mp.REGISTRY_REQUIRED_COLUMNS)
        cur.execute(open(MIGRATION, encoding="utf-8").read())
        assert mp.missing_registry_columns(conn) == []
        cur.execute(open(MIGRATION, encoding="utf-8").read())   # idempotent re-run
        assert mp.missing_registry_columns(conn) == []
        cur.execute("DROP TABLE ml_models")
        assert mp.missing_registry_columns(conn) == list(mp.REGISTRY_REQUIRED_COLUMNS)   # to_regclass NULL => all missing
    finally:
        conn.rollback()
        conn.close()
