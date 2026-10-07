"""The activation runbook cannot drift from the files it tells an operator to apply.

The runbook (docs/operations/FORWARD_RESEARCH_ACTIVATION_RUNBOOK.md) carries a manifest of sha256 digests (content as stored in git, line endings normalised) of every
migration, role script, rollback script, backup script, unit, wrapper and the preflight spec it refers to. These tests fail when a file changes without the runbook being
regenerated, when the migration order in the runbook stops matching the compose init order, when a step loses its rollback/reversibility statement, or when an owner gate is
not mentioned. They also exercise the backup script's argument refusal and pin that the committed preflight spec passes the architecture layer."""
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import forward_world  # noqa: F401  (puts mechanism/ and the repo root on sys.path, like every test in this directory)
from forward_collection import activation as A
from research.lab import dataset_authoring as AUTH
from research.lab import research_status_reader as SR

REPO = Path(__file__).resolve().parents[3]
RUNBOOK = REPO / "docs" / "operations" / "FORWARD_RESEARCH_ACTIVATION_RUNBOOK.md"
TEXT = RUNBOOK.read_text(encoding="utf-8")
SPEC = REPO / "docs" / "operations" / "forward_research_preflight_spec.json"
BACKUP = REPO / "deploy" / "db" / "pre_activation_backup.sh"

EXPECTED_MANIFEST = {
    *(f"mechanism/{n}.sql" for n in ("add_market_snapshot_tables", "add_market_event_tables", "add_forward_return_label_table", "add_source_observation_tables",
                                     "add_catalyst_classification_table", "add_stock_relative_strength_table", "add_dataset_experiment_registry_tables",
                                     "add_sector_history_tables")),
    "deploy/db/research_roles.sql", "deploy/db/research_roles_verify.sql", "deploy/db/research_roles_rollback.sql", "deploy/db/rollback_24_31.sql",
    "deploy/db/pre_activation_backup.sh", "deploy/vps/run_forward_collection.sh", "deploy/vps/donchian-forward-collection.service",
    "deploy/vps/donchian-forward-collection.timer", "deploy/vps/donchian-forward-collection-alert.service", "docs/operations/forward_research_preflight_spec.json",
}


def manifest():
    block = re.search(r"^```manifest\n(.*?)\n```$", TEXT, re.S | re.M).group(1)
    out = {}
    for line in block.splitlines():
        digest, path = line.split("  ", 1)
        out[path] = digest
    return out


def lf_sha256(rel):
    return hashlib.sha256((REPO / rel).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_the_manifest_lists_exactly_the_files_the_runbook_applies():
    assert set(manifest()) == EXPECTED_MANIFEST


@pytest.mark.parametrize("rel", sorted(EXPECTED_MANIFEST))
def test_every_manifest_digest_matches_the_file(rel):
    assert manifest()[rel] == lf_sha256(rel), f"{rel} changed: regenerate the runbook manifest"


def test_the_migration_order_in_the_runbook_is_the_compose_init_order():
    loop = re.search(r"for f in (.*?); do", TEXT, re.S).group(1)
    in_runbook = [n.strip() for n in loop.replace("\\", " ").split()]
    cat = A.migration_catalog()
    from_compose = [Path(cat[n]["file"]).stem for n in (24, 25, 26, 27, 28, 29, 30, 31)]
    assert in_runbook == from_compose


def test_there_are_twenty_numbered_steps_each_with_a_rollback_and_a_reversibility_statement():
    heads = list(re.finditer(r"^### (\d+)\. ", TEXT, re.M))
    assert [int(m.group(1)) for m in heads] == list(range(1, 21))
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else TEXT.index("\n## 3. Rollback")
        block = TEXT[m.start():end]
        assert "Rollback" in block and "Reversible" in block, f"step {m.group(1)} lacks a rollback or reversibility statement"


def test_the_first_append_only_write_is_marked_once_and_it_is_the_recorder_step():
    assert TEXT.count("[FIRST APPEND-ONLY WRITE]") == 2                    # the table of changes and the step heading
    heading = re.search(r"^### 12\. .*$", TEXT, re.M).group(0)
    assert "[FIRST APPEND-ONLY WRITE]" in heading and "recorder" in heading.lower()


def test_every_owner_gate_is_named_and_none_is_supplied_by_the_runbook():
    for gate in A.GATES:
        assert gate in TEXT, gate
    assert not re.search(r"--gate\s+(?!NAME=)\w+=yes", TEXT)                        # the runbook never states a gate on the owner's behalf


def test_the_runbook_contains_no_secret_shaped_text():
    assert not re.search(r"(password|secret|token|api[_-]?key)\s*[=:]\s*\S{6,}", TEXT, re.I)


def test_the_runbook_leaves_the_bot_backend_and_existing_pins_alone():
    assert "mechanism_image_tag.env" in TEXT and "deliberately **not** changed" in TEXT
    assert "research_roles_rollback.sql" in TEXT and "Do NOT run `research_roles_rollback.sql`" in TEXT


# ------------------------------------------------------------------ the scripts and the spec
def bash_works():
    exe = shutil.which("bash")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-c", "echo ok"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return exe if r.returncode == 0 and r.stdout.strip() == "ok" else None


BASH = bash_works()


@pytest.mark.skipif(BASH is None, reason="no working bash here")
def test_the_backup_script_is_syntactically_valid_and_refuses_a_missing_or_bad_label():
    assert subprocess.run([BASH, "-n", BACKUP.as_posix()], capture_output=True, timeout=30).returncode == 0
    for args in ([], ["Bad Label"], ["../x"], ["UPPER"]):
        r = subprocess.run([BASH, BACKUP.as_posix(), *args], capture_output=True, text=True, timeout=30)
        assert r.returncode == 2 and "usage" in r.stderr, args


def test_the_backup_script_writes_only_under_the_backup_directory_and_never_prints_a_secret():
    text = BACKUP.read_text(encoding="utf-8")
    assert 'BK="$BASE/backups/pre-release-b/$LABEL-$TS"' in text and "umask 077" in text and 'chmod 700 "$BK"' in text
    assert not re.search(r"PGPASSWORD|--password|-W\b", text)
    assert "default_transaction_read_only=on" in text                      # the row counts are read-only SQL


def test_the_backup_and_wrapper_scripts_are_executable_in_git():
    for rel in ("deploy/db/pre_activation_backup.sh", "deploy/vps/run_forward_collection.sh"):
        out = subprocess.run(["git", "ls-files", "--stage", "--", rel], cwd=str(REPO), capture_output=True, text=True).stdout.split()
        if not out:
            pytest.skip("not tracked yet (checked again once committed)")
        assert out[0] == "100755", rel


def test_the_committed_preflight_spec_passes_the_architecture_layer_with_the_sector_history_enabled():
    doc = json.loads(SPEC.read_text(encoding="utf-8"))
    enabled = SR.parse_cfg(AUTH.parse_authoring(doc), []).enabled()
    assert {k for k, v in enabled.items() if v} == {"market", "breadth", "sector", "stock_rs", "sector_history"}
    checks = A.architecture_checks(doc, env={})
    assert all(c["ok"] for c in checks if c["blocking"]), [c["id"] for c in checks if not c["ok"]]
    assert doc["config"]["sector_history"] == {"source": "yfinance_info"}
