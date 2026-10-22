"""deploy/vps/deploy.sh + rollback.sh: rollback-pointer semantics (2026-10-22 incident).

On 2026-10-22 a release (190349e8a3db) passed the health gate, regressed Screener latency to ~30 s and was rolled back.
The rollback was itself a successful deploy, so deploy.sh wrote the release it rolled back FROM into PREVIOUS_GOOD_SHA:
the next rollback would have redeployed the known-slow image. These tests run the REAL scripts (only BASE is pointed
at a temporary directory) against a fake `docker` that records which image is "running" and reports health from a
list of unhealthy tags. No container, network or host path is touched.
"""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
VPS = REPO / "deploy" / "vps"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None or shutil.which("tac") is None, reason="needs bash and coreutils")

A, B, C, D = "aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc", "dddddddddddd"

FAKE_DOCKER = r"""#!/usr/bin/env bash
S="$FAKE_STATE"
if [ "$1" = "compose" ]; then
  sub=""
  for a in "$@"; do case "$a" in pull|ps|up|logs|exec) sub="$a"; break;; esac; done
  case "$sub" in
    ps)  if printf '%s\n' "$@" | grep -qx postgres; then echo pg-cid; else cat "$S/backend_cid" 2>/dev/null || true; fi ;;
    up)  if printf '%s\n' "$@" | grep -qx backend; then echo "$IMAGE_TAG" > "$S/running"; echo "cid-$IMAGE_TAG" > "$S/backend_cid"; fi ;;
    *)   : ;;
  esac
  exit 0
fi
if [ "$1" = "inspect" ]; then
  tag="$(cat "$S/running" 2>/dev/null || true)"
  case "$3" in
    *Health.Status*) if grep -qx "$tag" "$S/unhealthy" 2>/dev/null; then echo unhealthy; else echo healthy; fi ;;
    *) echo "ghcr.io/danielbugi/wolfx_alpha-backend:$tag" ;;
  esac
  exit 0
fi
exit 0
"""


def _posix(p: Path) -> str:
    if os.name == "nt":
        return subprocess.run(["cygpath", "-u", str(p)], capture_output=True, text=True, check=True).stdout.strip()
    return str(p)


class Host:
    """A temporary /opt/donchian with the real scripts (BASE rewritten) and a fake docker on PATH."""

    def __init__(self, tmp: Path, current=None, previous=None, known_good=None, failed=None, releases=(A, B, C, D)):
        self.base, self.state, self.bin = tmp / "opt", tmp / "state", tmp / "bin"
        for d in (self.base / "compose", self.base / "env", self.base / "logs", self.base / "scripts", self.state, self.bin):
            d.mkdir(parents=True)
        (self.base / "env" / ".env").write_text("X=1\n")
        for r in releases:
            rel = self.base / "releases" / r
            (rel / "docker").mkdir(parents=True)
            for f in ("docker-compose.yml", "docker-compose.prod.yml"):
                (rel / f).write_text("services: {}\n")
            (rel / "docker" / "Caddyfile.prod").write_text(":80\n")
        base = _posix(self.base)
        for name in ("deploy.sh", "rollback.sh"):
            src = (VPS / name).read_text(encoding="utf-8").replace("BASE=/opt/donchian", f"BASE={base}")
            self._exe(self.base / "scripts" / name, src)        # rollback.sh exec's deploy.sh: executable, as installed (755)
        self._exe(self.bin / "docker", FAKE_DOCKER)
        if shutil.which("flock") is None:                       # Git Bash on Windows has no flock; CI (Ubuntu) uses the real one
            self._exe(self.bin / "flock", "#!/usr/bin/env bash\nexit 0\n")
        if current:
            self._set("CURRENT_SHA", current)
            (self.state / "running").write_text(current + "\n")
            (self.state / "backend_cid").write_text(f"cid-{current}\n")
        if previous is not None:
            self._set("PREVIOUS_GOOD_SHA", previous)
        if known_good:
            (self.base / "KNOWN_GOOD_SHAS").write_text("".join(s + "\n" for s in known_good), newline="\n")
        if failed:
            (self.base / "FAILED_SHAS").write_text("".join(s + "\n" for s in failed), newline="\n")

    def _exe(self, path: Path, body: str):
        path.write_text(body, encoding="utf-8", newline="\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def _set(self, name, value):
        (self.base / name).write_text(value + "\n", newline="\n")

    def unhealthy(self, *tags):
        (self.state / "unhealthy").write_text("".join(t + "\n" for t in tags), newline="\n")

    def run(self, script, *args, **env):
        e = dict(os.environ)
        path = e.get("PATH", "")
        if os.name == "nt":                                     # bash needs its own POSIX-form PATH
            path = subprocess.run([BASH, "-lc", "echo $PATH"], capture_output=True, text=True).stdout.strip()
        e.update({"PATH": _posix(self.bin) + ":" + path, "FAKE_STATE": _posix(self.state)})
        e.update(env)
        return subprocess.run([BASH, _posix(self.base / "scripts" / script), *args], capture_output=True, text=True, env=e, timeout=120)

    def deploy(self, tag, **env):
        return self.run("deploy.sh", "backend", tag, **env)

    def rollback(self):
        return self.run("rollback.sh", "backend")

    def read(self, name):
        p = self.base / name
        return p.read_text().strip() if p.exists() else None

    def lines(self, name):
        p = self.base / name
        return [s for s in p.read_text().split() if s] if p.exists() else []

    @property
    def running(self):
        return (self.state / "running").read_text().strip()


# ------------------------------------------------------------------------------------------------- normal deploys
def test_a_successful_deploy_records_the_replaced_release_as_known_good(tmp_path):
    h = Host(tmp_path, current=A)
    r = h.deploy(B)
    assert r.returncode == 0, r.stderr
    assert (h.read("CURRENT_SHA"), h.read("PREVIOUS_GOOD_SHA"), h.running) == (B, A, B)
    assert h.lines("KNOWN_GOOD_SHAS") == [A] and h.lines("FAILED_SHAS") == []


def test_a_chain_of_deploys_keeps_a_valid_recovery_path(tmp_path):
    h = Host(tmp_path, current=A)
    for t in (B, C):
        assert h.deploy(t).returncode == 0
    assert (h.read("CURRENT_SHA"), h.read("PREVIOUS_GOOD_SHA")) == (C, B)
    assert h.lines("KNOWN_GOOD_SHAS") == [A, B]


def test_compatible_with_a_host_that_has_only_the_two_pointer_files(tmp_path):
    """Production today: CURRENT_SHA + PREVIOUS_GOOD_SHA, no record files."""
    h = Host(tmp_path, current=C, previous=B)
    assert h.deploy(D).returncode == 0
    assert (h.read("CURRENT_SHA"), h.read("PREVIOUS_GOOD_SHA"), h.lines("KNOWN_GOOD_SHAS")) == (D, C, [C])


# ------------------------------------------------------------------------------------------------- failed deploys
def test_a_failed_deploy_restores_the_running_release_and_never_becomes_a_target(tmp_path):
    h = Host(tmp_path, current=B, previous=A, known_good=[A])
    h.unhealthy(C)
    r = h.deploy(C)
    assert r.returncode == 1
    assert (h.running, h.read("CURRENT_SHA"), h.read("PREVIOUS_GOOD_SHA")) == (B, B, A)
    assert h.lines("FAILED_SHAS") == [C]


def test_a_failed_release_is_refused_unless_retried_deliberately(tmp_path):
    h = Host(tmp_path, current=B, previous=A, known_good=[A], failed=[C])
    r = h.deploy(C)
    assert r.returncode == 2 and "FAILED_SHAS" in (r.stdout + r.stderr) and h.running == B
    r = h.deploy(C, ALLOW_FAILED_TAG="1")                       # deliberate operator retry, now healthy
    assert r.returncode == 0 and h.read("CURRENT_SHA") == C
    assert h.read("PREVIOUS_GOOD_SHA") == B


# ------------------------------------------------------------------------------------------------------ rollbacks
def test_rollback_never_records_the_release_it_left_as_the_rollback_target(tmp_path):
    """The 2026-10-22 incident: A -> B -> C deployed, C is bad (healthy gate, slow) and is rolled back."""
    h = Host(tmp_path, current=A)
    for t in (B, C):
        assert h.deploy(t).returncode == 0
    r = h.rollback()
    assert r.returncode == 0, r.stderr
    assert (h.running, h.read("CURRENT_SHA")) == (B, B)
    assert h.read("PREVIOUS_GOOD_SHA") == A                    # NOT C
    assert h.lines("FAILED_SHAS") == [C]


def test_repeated_rollbacks_walk_back_and_never_return_to_a_failed_release(tmp_path):
    h = Host(tmp_path, current=A)
    for t in (B, C):
        assert h.deploy(t).returncode == 0
    assert h.rollback().returncode == 0                         # C -> B
    assert h.rollback().returncode == 0                         # B -> A
    assert (h.running, h.read("CURRENT_SHA"), h.read("PREVIOUS_GOOD_SHA") or "") == (A, A, "")
    assert set(h.lines("FAILED_SHAS")) == {B, C}
    r = h.rollback()                                            # nothing older is known: refuse, keep A running
    assert r.returncode == 1 and "nothing to roll back to" in r.stderr and h.running == A


def test_a_rollback_whose_target_is_unhealthy_keeps_the_running_release_and_moves_the_pointer_further_back(tmp_path):
    h = Host(tmp_path, current=A)
    for t in (B, C):
        assert h.deploy(t).returncode == 0
    h.unhealthy(B)
    r = h.rollback()
    assert r.returncode == 1
    assert (h.running, h.read("CURRENT_SHA")) == (C, C)        # restored what was running
    assert B in h.lines("FAILED_SHAS") and h.read("PREVIOUS_GOOD_SHA") == A


def test_rollback_refuses_a_pointer_that_is_recorded_as_failed(tmp_path):
    h = Host(tmp_path, current=B, previous=C, failed=[C])
    r = h.rollback()
    assert r.returncode == 1 and "FAILED_SHAS" in r.stderr and h.running == B


def test_after_a_rollback_a_new_deploy_records_the_restored_release_as_known_good(tmp_path):
    h = Host(tmp_path, current=A)
    for t in (B, C):
        assert h.deploy(t).returncode == 0
    assert h.rollback().returncode == 0                         # running B, C failed
    assert h.deploy(D).returncode == 0
    assert (h.read("CURRENT_SHA"), h.read("PREVIOUS_GOOD_SHA")) == (D, B)
    assert C not in h.lines("KNOWN_GOOD_SHAS")


def test_the_incident_replay_with_seeded_history(tmp_path):
    """Seeded as the installation note proposes: KNOWN_GOOD=[57f2ab6..., 662cb38...], FAILED=[190349e...]."""
    good_old, good, slow, fixed = "57f2ab6b864d", "662cb38593ed", "190349e8a3db", "c6827ff0ff76"
    h = Host(tmp_path, current=fixed, previous=good, known_good=[good_old, good], failed=[slow],
             releases=(good_old, good, slow, fixed))
    assert h.rollback().returncode == 0                         # fixed -> good
    assert (h.running, h.read("PREVIOUS_GOOD_SHA")) == (good, good_old)
    assert h.deploy(slow).returncode == 2                       # the known-slow release cannot be redeployed by accident


def test_an_unknown_mode_is_refused(tmp_path):
    h = Host(tmp_path, current=A)
    r = h.deploy(B, DEPLOY_MODE="sideways")
    assert r.returncode == 2 and h.running == A


def test_ci_entry_still_cannot_select_rollback_mode():
    """The CI key's forced command accepts exactly `deploy backend <sha>` and passes nothing else to deploy.sh."""
    src = (VPS / "ci-entry.sh").read_text(encoding="utf-8")
    assert "DEPLOY_MODE" not in src and "ALLOW_FAILED_TAG" not in src
