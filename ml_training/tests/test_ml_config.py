# ml_training/tests/test_ml_config.py
"""Regression coverage for the Phase 3/4A finding: importing ml_training/config/ml_config.py used
to open a real DB connection as a side effect (an `else` branch called validate_config() ->
test_db_connection() -> psycopg2.connect() on every plain import, not just `python ml_config.py`).
This broke --help/import-only invocations with no reachable DB and cost a real connection on every
import for no reason -- every real consumer already opens its own connection explicitly when it
needs one. Fixed 2026-09-25; this test is the regression guard.

Runs the import in a fresh subprocess with psycopg2.connect replaced by a function that raises, so
a real Postgres is never needed and a reintroduced import-time connection attempt fails loudly
instead of silently passing because a real DB happened to be reachable.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ML_CONFIG_DIR = REPO_ROOT / "ml_training" / "config"

_PROBE = """
import sys, types

def _forbidden_connect(*args, **kwargs):
    raise AssertionError("ml_config import must never open a DB connection")

fake_psycopg2 = types.ModuleType("psycopg2")
fake_psycopg2.connect = _forbidden_connect
sys.modules["psycopg2"] = fake_psycopg2

sys.path.insert(0, {ml_config_dir!r})
import ml_config  # noqa: F401
print("IMPORT_OK")
"""


def test_import_does_not_open_a_db_connection():
    script = _PROBE.format(ml_config_dir=str(ML_CONFIG_DIR))
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"import ml_config exited {result.returncode} "
        f"(stdout={result.stdout!r} stderr={result.stderr!r})"
    )
    assert "IMPORT_OK" in result.stdout
    assert "AssertionError" not in result.stderr


def test_running_as_main_still_does_the_full_validation():
    """`python ml_config.py` (the __main__ branch) is the intentional, explicit way to check DB
    wiring -- that behavior must survive; only the implicit import-time side effect was removed."""
    source = (ML_CONFIG_DIR / "ml_config.py").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__"' in source
    assert "validate_config()" in source
