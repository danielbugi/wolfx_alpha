"""Activation-neutral guards: nothing outside this package imports it, and the committed scheduler units for it are the ONLY deploy files that name it
(dormant: the full proof is in test_dormant_units.py)."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PKG = REPO / "mechanism" / "forward_collection"
IMPORT = re.compile(r"^\s*(?:from|import)\s+(?:mechanism\.)?forward_collection\b", re.M)
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".next", ".venv", "venv"}
# The approved, DORMANT set (Slice 13): committed under deploy/vps, installed by an administrator only. Anything else naming the collector in a deploy path is a failure.
APPROVED_DEPLOY_FILES = {"deploy/vps/donchian-forward-collection.service", "deploy/vps/donchian-forward-collection.timer",
                         "deploy/vps/donchian-forward-collection-alert.service", "deploy/vps/run_forward_collection.sh", "deploy/vps/README.md"}


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


def test_only_the_approved_dormant_units_reference_the_collector_in_any_deploy_workflow_or_compose_file():
    hits = []
    for rel in ("deploy", ".github", "docker-compose.yml", "docker-compose.prod.yml"):
        base = REPO / rel
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()] if base.is_dir() else []
        for p in files:
            if p.name == "ci.yml":
                continue                                    # CI runs the package's own tests; that is the only permitted mention
            txt = p.read_text(encoding="utf-8", errors="ignore")
            if "forward_collection" in txt or "forward-collection" in txt:
                hits.append(p.relative_to(REPO).as_posix())
    assert set(hits) <= APPROVED_DEPLOY_FILES, sorted(set(hits) - APPROVED_DEPLOY_FILES)
    assert {h for h in hits if h != "deploy/vps/README.md"} == APPROVED_DEPLOY_FILES - {"deploy/vps/README.md"}      # and all four really are committed


def test_ci_mentions_the_package_only_to_run_its_tests():
    txt = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    lines = [l for l in txt.splitlines() if "forward_collection" in l]
    assert lines and all("pytest" in l or "forward_collection/tests" in l for l in lines)


def test_the_units_are_committed_under_deploy_vps_and_no_stale_drafts_remain():
    drafts = REPO / "docs" / "research" / "scheduler_drafts"
    assert not drafts.exists() or not list(drafts.glob("*"))
    committed = sorted(p.relative_to(REPO).as_posix() for p in (REPO / "deploy").rglob("*forward-collection*"))
    assert committed == sorted(f for f in APPROVED_DEPLOY_FILES if "forward-collection" in f)
    for f in APPROVED_DEPLOY_FILES - {"deploy/vps/README.md"}:
        assert "market_intelligence" not in (REPO / f).read_text(encoding="utf-8")


def test_the_package_never_names_a_capture_state_hatch_or_a_ledger_writer():
    banned = re.compile(r"research_capture_set_state|research_maintenance|write_session|write_universe_snapshot|append_event_revision")
    for p in PKG.glob("*.py"):
        assert not banned.search(p.read_text(encoding="utf-8")), p.name
