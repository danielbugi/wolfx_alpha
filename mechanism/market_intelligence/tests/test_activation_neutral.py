"""Deploying the image that contains Market Intelligence is behaviour-neutral: nothing in the repository writes its tables, nothing scheduled
runs it, no migration is applied by running code, the backend wiring cannot stop the app from starting, and the live Telegram path does not
reach it. (The guards boundary half -- GUARDS_EFFECTIVE_FROM unset == legacy behaviour -- is proved in screeners/tests/test_guards_effective_from.py.)"""
import ast
import os
import re

from mi_samples import ROOT

SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "tests", "docs", "history", "backups", "SKILLS", ".next"}
RUNNER = "mechanism/market_intelligence/runner.py"          # the one, unscheduled, operator-run caller of the writers
EDGAR_STORE = "mechanism/market_intelligence/edgar_store.py"     # Phase 1A SEC ingest: also operator-run only (see the guards below)
SCRATCH_DRY_RUN = "ml_training/experiments/catalyst_phase1a_dryrun.py"
SCRATCH_SCRIPTS = (SCRATCH_DRY_RUN, "ml_training/experiments/catalyst_latency_probe.py")
WRITERS = re.compile(r"\b(write_session|write_universe_snapshot|append_event_revision)\b")


def _files(top, exts):
    for base, dirs, files in os.walk(os.path.join(ROOT, top)):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(exts):
                yield os.path.join(base, f)


def _read(p):
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def test_nothing_outside_the_package_and_its_tests_writes_the_market_intelligence_tables():
    """No collector, no pipeline stage, no job: only the package's own store defines the writers, and no runtime code calls them."""
    callers = []
    for top in ("mechanism", "backend", "ml_training", "deploy"):
        for p in _files(top, (".py", ".sh", ".service", ".timer", ".yml", ".yaml")):
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            if rel in ("mechanism/market_intelligence/store.py", RUNNER, EDGAR_STORE):
                continue
            if WRITERS.search(_read(p)):
                callers.append(rel)
    assert callers == []


def test_the_sec_ingest_is_referenced_only_by_its_own_package_its_tests_and_the_scratch_dry_run():
    """Phase 1A: nothing scheduled, imported by a pipeline stage, a router, a bot path, a workflow or a unit file may reach the SEC transport or the ingest."""
    hits = []
    pat = re.compile(r"edgar_store|sec_transport|SecTransport|ingest_facts|refresh_links|catalyst_phase1a_dryrun|add_catalyst_ingest_tables")
    for top in ("mechanism", "backend", "ml_training", "deploy", ".github"):
        for p in _files(top, (".py", ".sh", ".service", ".timer", ".yml", ".yaml")):
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            if rel.startswith("mechanism/market_intelligence/") or rel in SCRATCH_SCRIPTS:
                continue
            if pat.search(_read(p)):
                hits.append(rel)
    # docker-compose.yml only MOUNTS the migration file for a fresh database; it runs no code
    assert [h for h in hits if h != "docker-compose.yml"] == [], hits


def test_the_scratch_scripts_cannot_be_started_by_anything_but_a_person():
    for rel in SCRATCH_SCRIPTS:
        text = _read(os.path.join(ROOT, rel))
        assert 'if __name__ == "__main__":' in text and "SEC_USER_AGENT is required" in text
        assert not re.search(r"^(main|connect)\(", text, re.M)
    assert "PHASE1A_SCRATCH" in _read(os.path.join(ROOT, SCRATCH_DRY_RUN))                  # the dry run writes to a database: it demands an explicit scratch switch
    assert not re.search(r"psycopg2|INSERT|UPDATE|DELETE", _read(os.path.join(ROOT, "ml_training/experiments/catalyst_latency_probe.py")))   # the probe touches no database


# Slice 7 (dormant, lab): the one composition root that orchestrates the runner. Nothing else may reference it; forward_collection's own guard test
# pins that nothing outside that package imports the package.
FORWARD_COLLECTION_STEPS = "mechanism/forward_collection/steps.py"


def test_the_runner_is_imported_and_referenced_by_nothing_but_its_own_tests():
    """Not by a pipeline stage, an orchestrator, a backend router, a timer or a workflow: running it is an operator decision."""
    hits = []
    for top in ("mechanism", "backend", "ml_training", "deploy", ".github"):
        for p in _files(top, (".py", ".sh", ".service", ".timer", ".yml", ".yaml")):
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            if rel.startswith("mechanism/market_intelligence/"):
                continue
            if rel == FORWARD_COLLECTION_STEPS:
                continue
            if re.search(r"market_intelligence(\.|/)runner|market_intelligence import runner|mi_runner", _read(p)):
                hits.append(rel)
    assert hits == [], hits


def test_the_runner_defaults_to_a_dry_run_and_has_no_importable_side_effects():
    text = _read(os.path.join(ROOT, RUNNER))
    assert "apply: bool = False" in text and '"--apply", action="store_true"' in text
    assert 'if __name__ == "__main__":' in text
    assert not re.search(r"^(run|main|compute_session)\(", text, re.M)


def test_no_scheduled_unit_workflow_or_script_runs_market_intelligence():
    hits = []
    for p in _files("deploy", (".service", ".timer", ".sh", ".yml")):
        if "market_intelligence" in _read(p).replace("-", "_"):
            hits.append(os.path.relpath(p, ROOT).replace("\\", "/"))
    for p in _files(".github", (".yml",)):                                                  # CI may only TEST it, never run it
        for line in _read(p).splitlines():
            if "market_intelligence" in line and "/tests" not in line and "test_" not in line:
                hits.append(f"{os.path.relpath(p, ROOT)}: {line.strip()}")
    for name in ("automation_pipeline.sh", "docker-compose.yml", "docker-compose.prod.yml"):
        p = os.path.join(ROOT, name)
        if os.path.exists(p) and "market_intelligence" in _read(p):
            hits.append(name)
    assert hits == [], hits


def test_the_pipeline_orchestrators_do_not_know_about_it():
    for rel in ("mechanism/orchestrators", "mechanism/data_updaters", "mechanism/screeners", "mechanism/research", "mechanism/strategy_analytics"):
        for p in _files(rel, (".py",)):
            assert "market_intelligence" not in _read(p), p


def test_no_runtime_code_applies_the_migrations():
    """24/25 reach a database only by the operator (production, manually) or by the init mount of a fresh dev/CI database."""
    users = []
    for top in ("mechanism", "backend", "deploy", "ml_training"):
        for p in _files(top, (".py", ".sh", ".service")):
            if re.search(r"add_market_(snapshot|event)_tables", _read(p)):
                users.append(os.path.relpath(p, ROOT).replace("\\", "/"))
    assert users == []


def test_the_backend_router_include_is_guarded_so_a_failure_cannot_stop_the_app():
    tree = ast.parse(_read(os.path.join(ROOT, "backend", "main.py")))
    guarded = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(isinstance(n, ast.ImportFrom) and n.module == "routers.market_intelligence" for n in ast.walk(node)):
            handlers = {ast.unparse(h.type) if h.type else "bare" for h in node.handlers}
            guarded = {"ImportError", "Exception"} <= handlers
    assert guarded


def test_the_api_is_read_only_and_degrades_instead_of_failing():
    router = _read(os.path.join(ROOT, "backend", "routers", "market_intelligence.py"))
    assert not re.search(r"@\w+\.(post|put|patch|delete)\(", router)
    service = _read(os.path.join(ROOT, "backend", "services", "market_intelligence_service.py"))
    assert "UndefinedTable" in service and "InsufficientPrivilege" in service and "not_provisioned" in service


def test_the_live_post_market_package_cannot_reach_it():
    for rel in ("publish_post_market.py", "post_delivery.py", "send_daily_digest.py", "send_daily_alerts.py"):
        assert "market_intelligence" not in _read(os.path.join(ROOT, "mechanism", "alerts", rel)), rel
    from alerts import channel_content as cx
    from alerts import publish_post_market as ppm
    assert set(ppm.POST_MARKET_KINDS) == {"daily_digest", "momentum_board", "top_gainers", "market_health"}
    assert "market_environment" not in cx.ROTATION.get(0, ()) + cx.ROTATION.get(1, ()) + cx.ROTATION.get(2, ()) + cx.ROTATION.get(3, ()) + cx.ROTATION.get(4, ())


def test_the_backend_serves_the_feature_set_the_runner_writes():
    """The backend may not import the runner, so it names the feature sets it serves; the first must be the runner's."""
    m = re.search(r'SERVED_FEATURE_SETS = \("([^"]+)"', _read(os.path.join(ROOT, "backend/services/market_intelligence_service.py")))
    v = re.search(r'^FEATURE_SET_VERSION = "([^"]+)"', _read(os.path.join(ROOT, RUNNER)), re.M)
    assert m and v and m.group(1) == v.group(1)
