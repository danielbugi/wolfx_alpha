"""The single-writer rule for the forward sector history (migration 31), enforced STATICALLY (no database).

Database roles cannot tell two Python modules of the same service apart (`donchian_app` may INSERT into these tables, as it must for the one
authoritative writer), so "only the fundamentals updater writes forward sector history" is a property of the CODE, and this test is its guard:

  1. every non-test file that names a history table is on an explicit, reviewed list (a new module touching them fails here and must be reviewed);
  2. the only code that writes `sector_observation` / `sector_poll` is `sector_history_recorder.py`, and it only INSERTs;
  3. nothing at all writes `sector_reconstruction` (the import path is deliberately unbuilt) and nothing copies `daily_fundamentals` into the history;
  4. the recorder is reachable only from `FundamentalsUpdater` (flag-gated); the collector, screener, backend and research lab never import it.
"""
import os
import re

from conftest import ROOT

TABLES = ("sector_observation", "sector_poll", "sector_reconstruction")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".next", ".venv", "venv", "backups", "SKILLS"}
EXT = (".py", ".sql", ".sh", ".yml", ".yaml", ".service", ".timer", ".ps1", ".proposed")

# non-test files allowed to NAME a history table, and why
ALLOWED_MENTIONS = {
    "mechanism/add_sector_history_tables.sql": "the migration itself (DDL, trigger functions)",
    "mechanism/data_updaters/sector_history_recorder.py": "THE authoritative writer",
    "mechanism/research/lab/dataset_reader.py": "read-only SELECTs",
    "mechanism/research/lab/dataset_cli.py": "a label naming the DB inputs a spec needs",
    "mechanism/research/lab/sector_history.py": "pure selector: docstring only",
    "deploy/db/research_roles.sql": "role grants (the same immutable-table treatment as every research table)",
    "deploy/db/research_roles_verify.sql": "role verification",
    "deploy/db/research_roles_rollback.sql": "role rollback",
    "deploy/db/rollback_24_31.sql": "schema rollback: DROP-only (no row is ever written; test_rollback_24_31_db pins that it refuses while any table holds a row)",
    "mechanism/validate_release_b.py": "read-only production validator: classifies the tables as append-only (privilege checks only)",
}
WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?|COPY|MERGE\s+INTO|REPLACE\s+INTO)\s+(?:ONLY\s+)?(?:public\.)?("
                   + "|".join(TABLES) + r")\b", re.I)


def is_test(rel):
    parts = rel.split("/")
    return "tests" in parts or parts[-1].startswith("test_") or parts[-1] == "conftest.py"


def files():
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith("donchian_screener") and not d.endswith("-wt-qf-guard")]
        for n in names:
            if n.endswith(EXT):
                full = os.path.join(base, n)
                rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
                if rel.startswith("docs/") or is_test(rel):
                    continue
                with open(full, encoding="utf-8", errors="replace") as fh:
                    yield rel, fh.read()


def test_the_scan_sees_the_known_files():
    seen = {rel for rel, _ in files()}
    assert set(ALLOWED_MENTIONS) <= seen, set(ALLOWED_MENTIONS) - seen
    assert "mechanism/data_updaters/fundamentals_updater.py" in seen


def test_only_reviewed_files_name_a_history_table():
    offenders = {rel for rel, text in files() if any(t in text for t in TABLES)} - set(ALLOWED_MENTIONS)
    assert not offenders, f"new non-test code names a forward sector history table: {sorted(offenders)} -- review it against the single-writer rule"


def test_only_the_recorder_writes_the_history_and_only_by_insert():
    writers = {}
    for rel, text in files():
        hits = [(m.group(1).upper().split()[0], m.group(2).lower()) for m in WRITE.finditer(text)]
        if hits:
            writers[rel] = hits
    assert set(writers) <= {"mechanism/data_updaters/sector_history_recorder.py"}, f"unexpected writers: {sorted(set(writers) - {'mechanism/data_updaters/sector_history_recorder.py'})}"
    hits = writers["mechanism/data_updaters/sector_history_recorder.py"]
    assert {v for v, _ in hits} == {"INSERT"}                                                      # never UPDATE / DELETE / TRUNCATE / COPY
    assert {t for _, t in hits} == {"sector_observation", "sector_poll"}                            # never the reconstruction store


def test_nothing_writes_the_reconstruction_store_and_nothing_copies_fundamentals_into_the_history():
    for rel, text in files():
        if rel in ("mechanism/add_sector_history_tables.sql",):
            continue
        assert not re.search(r"sector_reconstruction", text) or rel.startswith("deploy/db/research_roles") or rel in ("mechanism/validate_release_b.py", "deploy/db/rollback_24_31.sql"), rel
        if "daily_fundamentals" in text:
            assert not any(t in text for t in ("sector_observation", "sector_poll")), f"{rel} reads daily_fundamentals AND names the history"


def test_the_migration_has_no_object_that_copies_into_the_history():
    with open(os.path.join(ROOT, "mechanism", "add_sector_history_tables.sql"), encoding="utf-8") as fh:
        sql = re.sub(r"--[^\n]*", "", fh.read())
    assert "daily_fundamentals" not in sql                                                         # no INSERT .. SELECT FROM daily_fundamentals, no view
    assert not re.search(r"INSERT\s+INTO\s+(?:public\.)?sector_(observation|poll)\b", sql, re.I)   # the migration creates structure, never rows


def test_the_recorder_is_reachable_only_from_the_fundamentals_updater():
    users = {rel for rel, text in files() if re.search(r"\bsector_history_recorder\b", text)}
    assert users == {"mechanism/data_updaters/fundamentals_updater.py", "mechanism/data_updaters/sector_history_recorder.py"}, sorted(users)
    for rel, text in files():
        if rel in users:
            continue
        assert not re.search(r"\b(SectorRecorder|record_poll)\b", text), rel


def test_the_updater_calls_the_recorder_only_behind_the_flag_and_off_by_default():
    with open(os.path.join(ROOT, "mechanism", "data_updaters", "fundamentals_updater.py"), encoding="utf-8") as fh:
        text = fh.read()
    assert text.count("shr.enabled()") == 1 and text.count("SectorRecorder(") == 1 and text.count(".record(") == 1
    with open(os.path.join(ROOT, "mechanism", "data_updaters", "sector_history_recorder.py"), encoding="utf-8") as fh:
        rec = fh.read()
    assert 'os.getenv(FLAG_ENV, "") == "1"' in rec                                                 # default OFF


def test_the_flag_is_not_switched_on_anywhere_in_deployment():
    for rel in ("docker-compose.yml", "docker-compose.prod.yml"):
        full = os.path.join(ROOT, rel)
        if os.path.exists(full):
            with open(full, encoding="utf-8") as fh:
                assert "SECTOR_HISTORY_RECORDER_ENABLED" not in fh.read(), rel
    for base, _, names in os.walk(os.path.join(ROOT, "deploy")):
        for n in names:
            with open(os.path.join(base, n), encoding="utf-8", errors="replace") as fh:
                assert "SECTOR_HISTORY_RECORDER_ENABLED" not in fh.read(), os.path.join(base, n)
