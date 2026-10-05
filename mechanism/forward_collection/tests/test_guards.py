"""Activation-neutral guards: nothing outside this package imports it, and no scheduler unit for it exists in a deploy path."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PKG = REPO / "mechanism" / "forward_collection"
IMPORT = re.compile(r"^\s*(?:from|import)\s+(?:mechanism\.)?forward_collection\b", re.M)
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".next", ".venv", "venv"}


def _walk(suffixes):
    for p in REPO.rglob("*"):
        if p.is_file() and p.suffix in suffixes and not (set(p.parts) & SKIP_DIRS):
            yield p


def test_nothing_outside_the_package_imports_forward_collection():
    offenders = []
    for p in _walk({".py"}):
        if PKG in p.parents:
            continue
        try:
            if IMPORT.search(p.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(p.relative_to(REPO)))
        except OSError:
            pass
    assert offenders == []


def test_no_deploy_workflow_or_compose_file_references_the_collector():
    hits = []
    for rel in ("deploy", ".github", "docker-compose.yml", "docker-compose.prod.yml"):
        base = REPO / rel
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()] if base.is_dir() else []
        for p in files:
            if p.name == "ci.yml":
                continue                                    # CI runs the package's own tests; that is the only permitted mention
            txt = p.read_text(encoding="utf-8", errors="ignore")
            if "forward_collection" in txt or "forward-collection" in txt:
                hits.append(str(p.relative_to(REPO)))
    assert hits == []


def test_ci_mentions_the_package_only_to_run_its_tests():
    txt = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    lines = [l for l in txt.splitlines() if "forward_collection" in l]
    assert lines and all("pytest" in l or "forward_collection/tests" in l for l in lines)


def test_unit_drafts_live_under_docs_not_deploy_and_carry_the_proposed_suffix():
    drafts = list((REPO / "docs" / "research" / "scheduler_drafts").glob("*"))
    assert drafts and all(p.suffix == ".proposed" for p in drafts)
    assert not list((REPO / "deploy").rglob("*forward-collection*"))
    for p in drafts:
        assert "market_intelligence" not in p.read_text(encoding="utf-8")


def test_the_package_never_names_a_capture_state_hatch_or_a_ledger_writer():
    banned = re.compile(r"research_capture_set_state|research_maintenance|write_session|write_universe_snapshot|append_event_revision")
    for p in PKG.glob("*.py"):
        assert not banned.search(p.read_text(encoding="utf-8")), p.name
