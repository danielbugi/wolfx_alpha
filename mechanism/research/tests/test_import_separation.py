"""The application layer must not be able to reach the maintenance hatch or mutate the immutable tables.
Static (no database): the hatch is for a human with an approved ticket, never for application code."""
import os
import re

from conftest import ROOT

RESEARCH = os.path.join(ROOT, "mechanism", "research")
APP_MODULES = ["registry.py", "snapshot_builder.py", "repository.py", "observer.py"]
IMMUTABLE = ("candidate_observation", "feature_snapshot", "feature_set_registry")


def source(name):
    with open(os.path.join(RESEARCH, name), encoding="utf-8") as fh:
        return fh.read()


def test_no_application_module_touches_the_maintenance_objects():
    for name in APP_MODULES:
        text = source(name)
        for forbidden in ("research_maintenance_log", "research_maintenance_audit", "maintenance_ticket"):
            assert forbidden not in text.replace("\n", " ") or name == "x", f"{name} mentions {forbidden}"


def test_no_module_updates_deletes_or_truncates_an_immutable_table():
    pattern = re.compile(r"\b(UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+(" + "|".join(IMMUTABLE) + r")\b", re.I)
    for name in APP_MODULES:
        assert not pattern.search(source(name)), name


def test_the_only_update_in_the_application_layer_is_the_capture_run_counters():
    updates = [m for name in APP_MODULES for m in re.findall(r"\bUPDATE\s+(\w+)", source(name), re.I)]
    assert updates == ["candidate_capture_run"]


def test_the_screener_and_ledger_writer_do_not_reference_the_hatch():
    for rel in ("screeners/multi_timeframe_screener.py", "screeners/signal_ledger_writer.py"):
        with open(os.path.join(ROOT, "mechanism", rel), encoding="utf-8") as fh:
            text = fh.read()
        assert "research_maintenance" not in text and "maintenance_ticket" not in text, rel


# ---- repository-wide scan: no runtime Python can reach the hatch or the activation writer -------------------------
SCAN_ROOTS = ("mechanism", "backend", "ml_training", "deploy")
HATCH = re.compile(r"research_maintenance_(?:open|approve|begin|close|log|session|audit|log_guard)|maintenance_ticket"
                   r"|research_capture_set_state|research_audit_append_only|research_guard_immutable", re.I)
# Tooling that only READS the catalog to verify the migration/roles by name. Never imported by the screener,
# the observer, the backend or the bot.
CATALOG_TOOLS = {
    os.path.join("mechanism", "check_research_migration_preflight.py"),
    os.path.join("mechanism", "research", "schema_fingerprint.py"),
    os.path.join("mechanism", "validate_release_b.py"),     # read-only (3 layers); names functions via has_function_privilege
}


def runtime_python_files():
    for top in SCAN_ROOTS:
        for base, dirs, files in os.walk(os.path.join(ROOT, top)):
            dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__", "node_modules", ".venv", "venv", "migrations_tests")]
            for f in files:
                if f.endswith(".py") and not f.startswith("test_") and f != "conftest.py":
                    path = os.path.join(base, f)
                    yield os.path.relpath(path, ROOT), path


def read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def test_no_runtime_python_anywhere_references_the_hatch_or_the_activation_writer():
    offenders = []
    for rel, path in runtime_python_files():
        if rel in CATALOG_TOOLS:
            continue
        found = sorted(set(m.group(0).lower() for m in HATCH.finditer(read(path))))
        if found:
            offenders.append((rel, found))
    assert not offenders, f"runtime code reaches the maintenance hatch / activation writer: {offenders}"


def test_catalog_tools_only_name_the_hatch_never_call_it():
    call = re.compile(r"SELECT\s+research_(?:maintenance|capture_set_state)|CALL\s+research_|PERFORM\s+research_", re.I)
    for rel in CATALOG_TOOLS:
        assert not call.search(read(os.path.join(ROOT, rel))), rel


def test_activation_table_is_only_read_by_runtime_code():
    write = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+research_capture_activation\b", re.I)
    readers = []
    for rel, path in runtime_python_files():
        text = read(path)
        assert not write.search(text), f"{rel} writes research_capture_activation"
        if "research_capture_activation" in text:
            readers.append(rel.replace("\\", "/"))
    assert set(readers) <= {"mechanism/research/repository.py", "mechanism/strategy_analytics/research.py",
                            "mechanism/check_research_migration_preflight.py",
                            "mechanism/research/schema_fingerprint.py", "mechanism/validate_release_b.py",
                            "mechanism/research/lab/research_status_reader.py",
                            "mechanism/forward_collection/preflight.py"}


def test_no_runtime_python_writes_any_research_table_except_through_the_capture_repository():
    """The backend and strategy_analytics are read-only; only research/repository.py inserts observations/snapshots."""
    write = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+"
                       r"(candidate_observation|feature_snapshot|feature_set_registry|candidate_capture_run)\b", re.I)
    allowed = {"mechanism/research/repository.py", "mechanism/research/registry.py"}
    for rel, path in runtime_python_files():
        if rel.replace("\\", "/") in allowed:
            continue
        assert not write.search(read(path)), f"{rel} writes a research table"


def test_migration_and_role_sql_are_the_only_place_the_hatch_functions_are_defined():
    for top in ("mechanism", "deploy"):
        for base, _, files in os.walk(os.path.join(ROOT, top)):
            for f in files:
                if f.endswith(".sql") and "CREATE OR REPLACE FUNCTION research_maintenance" in read(os.path.join(base, f)):
                    assert f == "add_research_observation_tables.sql", f


# ---- fwd_v1 forward-return labels (migration 26) --------------------------------------------------------------------
LABEL_PKG = os.path.join("mechanism", "research", "labels")


def _label_modules():
    base = os.path.join(ROOT, LABEL_PKG)
    return {n: read(os.path.join(base, n)) for n in sorted(os.listdir(base)) if n.endswith(".py")}


def test_label_modules_only_ever_insert_into_the_label_table_and_never_touch_the_ledger():
    forbidden = re.compile(r"\b(UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+(forward_return_label|candidate_observation|"
                           r"feature_snapshot|signal_ledger|stock_prices|market_index_prices)\b", re.I)
    inserts = re.compile(r"\bINSERT\s+INTO\s+(\w+)", re.I)
    for name, text in _label_modules().items():
        assert not forbidden.search(text), name
        assert not re.search(r"(FROM|JOIN|INTO|UPDATE)\s+signal_ledger", text, re.I), f"{name} reads/writes the ledger"
        assert set(m.lower() for m in inserts.findall(text)) <= {"forward_return_label"}, name


OWNER_CLI = "mechanism/research/lab/dataset_cli.py"


def test_the_owner_dataset_cli_uses_only_the_pure_calendar_helpers_of_the_label_engine_and_nothing_imports_the_cli():
    """The one carve-out from the rule below: the owner-run dataset CLI derives a session calendar with the label engine's own (read-only)
    calendar code rather than re-implementing it. It may import only `sessions` and `fwd_v1` -- never the label repository or runner -- and no
    other runtime module may import the CLI, so it stays an operator tool the pipeline cannot reach."""
    cli = read(os.path.join(ROOT, OWNER_CLI))
    imported = set(re.findall(r"^\s*(?:from|import)\s+(research\.labels[\w.]*)", cli, re.M))
    assert imported == {"research.labels.fwd_v1", "research.labels.sessions"}, imported
    assert not re.search(r"research\.labels\.(repository|runner)|research import labels", cli)
    for rel, path in runtime_python_files():
        rel = rel.replace("\\", "/")
        if rel != OWNER_CLI:
            assert "dataset_cli" not in read(path), f"{rel} imports the owner dataset CLI"


def test_only_the_label_repository_writes_the_label_table_and_nothing_imports_the_runner_from_the_pipeline():
    write = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+forward_return_label\b", re.I)
    for rel, path in runtime_python_files():
        rel = rel.replace("\\", "/")
        text = read(path)
        if rel != "mechanism/research/labels/repository.py":
            assert not write.search(text), f"{rel} writes forward_return_label"
        if not rel.startswith(("mechanism/research/labels/", "mechanism/forward_collection/")) and rel != OWNER_CLI:
            assert "research.labels" not in text and "research import labels" not in text, \
                f"{rel} imports the label engine: it is activated by a separate, owner-authorised stage"


def test_label_modules_do_not_reach_the_hatch_or_the_clock():
    for name, text in _label_modules().items():
        assert not HATCH.search(text), name
        for bad in ("datetime.now", "date.today", "time.time(", "utcnow"):
            assert bad not in text, f"{name} reads the wall clock ({bad})"
