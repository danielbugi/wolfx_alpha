"""The compose init-mount list is the only migration numbering that exists (there is no runner and no history table).
These checks keep it, the SQL files on disk and docs/architecture/MIGRATION_REGISTRY.md from drifting apart again."""
import os
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
MOUNT = re.compile(r"\./(mechanism/[\w./-]+\.sql):/docker-entrypoint-initdb\.d/(\d+)_([\w.-]+\.sql):ro")


def _mounts():
    with open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8") as f:
        return [(int(n), src, dst) for src, n, dst in MOUNT.findall(f.read())]


def test_init_numbers_are_unique_and_contiguous_from_one():
    nums = [n for n, _, _ in _mounts()]
    assert nums, "no init mounts parsed"
    assert nums == list(range(1, len(nums) + 1)), f"gap, duplicate or out-of-order init number: {nums}"


def test_each_mount_targets_its_own_source_file_and_the_file_exists():
    for n, src, dst in _mounts():
        assert dst == os.path.basename(src), f"{n:02d}: mounted as {dst} but the source is {src}"
        assert os.path.isfile(os.path.join(ROOT, src)), f"{n:02d}: {src} is missing"


def test_every_mechanism_sql_file_is_mounted_exactly_once():
    mounted = [src for _, src, _ in _mounts()]
    assert len(mounted) == len(set(mounted)), "a SQL file is mounted twice"
    on_disk = {f"mechanism/{f}" for f in os.listdir(os.path.join(ROOT, "mechanism")) if f.endswith(".sql")}
    assert on_disk == set(mounted), f"unmounted: {sorted(on_disk - set(mounted))}; dangling: {sorted(set(mounted) - on_disk)}"


def test_registry_doc_lists_every_migration_number_and_file():
    with open(os.path.join(ROOT, "docs", "architecture", "MIGRATION_REGISTRY.md"), encoding="utf-8") as f:
        doc = f.read()
    for n, src, _ in _mounts():
        assert os.path.basename(src) in doc, f"{src} is not in MIGRATION_REGISTRY.md"
        assert re.search(rf"\|\s*{n}\s*\|", doc), f"migration number {n} has no registry row"
