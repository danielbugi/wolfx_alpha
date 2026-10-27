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
if [ "$1" = "pull" ]; then
  tag="${2##*:}"
  if grep -qx "$tag" "$S/missing" 2>/dev/null; then echo "manifest unknown" >&2; exit 1; fi
  echo pulled >> "$S/pulls"; exit 0
fi
if [ "$1" = "image" ] && [ "$2" = "inspect" ]; then
  ref="${@: -1}"
  case "$ref" in
    img-*) tag="${ref#img-}"; src="$S/digests_running"; [ -f "$src" ] || src="$S/digests" ;;   # the image a container was created from
    *)     tag="${ref##*:}"; src="$S/digests" ;;                                              # whatever the tag resolves to now
  esac
  d="$(grep "^$tag " "$src" 2>/dev/null | cut -d' ' -f2)"
  [ -n "$d" ] && echo "ghcr.io/danielbugi/wolfx_alpha-backend@$d"
  exit 0
fi
if [ "$1" = "inspect" ]; then
  tag="$(cat "$S/running" 2>/dev/null || true)"
  case "$3" in
    *Health.Status*) if grep -qx "$tag" "$S/unhealthy" 2>/dev/null; then echo unhealthy; else echo healthy; fi ;;
    '{{.Image}}') echo "img-$tag" ;;
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

    def digests(self, **by_tag):
        """What the registry/local image for each tag resolves to (sha256:<64 hex>); a tag without an entry has none."""
        (self.state / "digests").write_text("".join(f"{t} {d}" + chr(10) for t, d in by_tag.items()), newline=chr(10))

    def digests_running(self, **by_tag):
        """The digest of the image a container was actually created from, when it differs from what the tag resolves to."""
        (self.state / "digests_running").write_text("".join(f"{t} {d}" + chr(10) for t, d in by_tag.items()), newline=chr(10))

    def missing(self, *tags):
        (self.state / "missing").write_text("".join(t + chr(10) for t in tags), newline=chr(10))

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


# --------------------------------------------------------------------- history that is empty, corrupt or inconsistent
def test_known_good_only_ever_holds_health_verified_releases(tmp_path):
    """KNOWN_GOOD gets a release only when a normal deploy REPLACES it -- i.e. it had passed the health gate and ran."""
    h = Host(tmp_path, current=A)
    h.unhealthy(B)
    assert h.deploy(B).returncode == 1                          # failed health gate
    assert h.lines("KNOWN_GOOD_SHAS") == [] and h.lines("FAILED_SHAS") == [B]
    assert h.deploy(C).returncode == 0
    assert h.lines("KNOWN_GOOD_SHAS") == [A]                    # A ran healthy and was replaced; B never qualifies


def test_corrupt_history_lines_are_skipped_never_deployed(tmp_path):
    pruned = "eeeeeeeeeeee"                                     # well-formed but its release bundle is gone
    h = Host(tmp_path, current=C, previous=B, known_good=[A, "garbage", "abc123", pruned, B])
    assert h.rollback().returncode == 0                         # C -> B
    assert h.read("PREVIOUS_GOOD_SHA") == A                    # walked past the bundle-less and malformed lines


def test_an_unusable_pointer_is_refused_with_a_manual_recovery_hint(tmp_path):
    h = Host(tmp_path, current=B, previous="not-a-sha")
    r = h.rollback()
    assert r.returncode == 1 and "Manual recovery" in r.stderr and h.running == B


def test_an_inconsistent_pointer_equal_to_current_is_healed_on_the_next_deploy(tmp_path):
    h = Host(tmp_path, current=B, previous=B, known_good=[A])
    assert h.deploy(C).returncode == 0
    assert (h.read("CURRENT_SHA"), h.read("PREVIOUS_GOOD_SHA")) == (C, B)


def test_manual_recovery_by_explicit_deploy_works_with_no_history_at_all(tmp_path):
    h = Host(tmp_path, current=C)                               # no PREVIOUS_GOOD_SHA, no record files
    assert h.rollback().returncode == 1                         # nothing recorded to roll back to
    assert h.deploy(A).returncode == 0 and h.running == A       # the operator names an older release explicitly
    assert h.read("PREVIOUS_GOOD_SHA") == C and h.lines("KNOWN_GOOD_SHAS") == [C]


# ------------------------------------------------------------------------------- EXPECTED_DIGEST (promotion safety)
SOAKED = "sha256:" + "1" * 64
OTHER = "sha256:" + "2" * 64


def test_expected_digest_deploys_the_exact_soaked_image_and_says_so(tmp_path):
    h = Host(tmp_path, current=A)
    h.digests(**{B: SOAKED})
    r = h.deploy(B, EXPECTED_DIGEST=SOAKED)
    assert r.returncode == 0, r.stderr
    assert (h.read("CURRENT_SHA"), h.read("PREVIOUS_GOOD_SHA"), h.running) == (B, A, B)
    assert f"digest {SOAKED} verified" in (h.base / "logs" / "deploy.log").read_text()


def _untouched(h, current, previous=None, known_good=(), failed=()):
    """Production exactly as before: same release running, same pointers, no compose file activated, no failed record."""
    assert (h.read("CURRENT_SHA"), h.running) == (current, current)
    assert h.read("PREVIOUS_GOOD_SHA") == previous
    assert h.lines("KNOWN_GOOD_SHAS") == list(known_good) and h.lines("FAILED_SHAS") == list(failed)
    assert not (h.base / "compose" / "docker-compose.yml").exists()


def test_a_digest_mismatch_before_the_switch_aborts_without_modifying_production(tmp_path):
    h = Host(tmp_path, current=A, previous=C, known_good=[C])
    h.digests(**{B: OTHER})                                   # the tag was re-pushed after the soak
    r = h.deploy(B, EXPECTED_DIGEST=SOAKED)
    assert r.returncode == 1 and "nothing was changed" in r.stderr
    _untouched(h, A, previous=C, known_good=[C])              # not even recorded as failed: nothing was attempted
    assert "ABORTED before any change" in (h.base / "logs" / "deploy.log").read_text()


def test_a_missing_image_aborts_without_modifying_production(tmp_path):
    h = Host(tmp_path, current=A, previous=C, known_good=[C])
    h.missing(B)
    r = h.deploy(B, EXPECTED_DIGEST=SOAKED)
    assert r.returncode == 1 and "could not be pulled" in r.stderr
    _untouched(h, A, previous=C, known_good=[C])


def test_an_image_without_any_digest_aborts_without_modifying_production(tmp_path):
    h = Host(tmp_path, current=A, previous=C, known_good=[C])
    r = h.deploy(B, EXPECTED_DIGEST=SOAKED)                   # nothing resolves for the tag
    assert r.returncode == 1 and "digest is ''" in r.stderr
    _untouched(h, A, previous=C, known_good=[C])


def test_a_digest_mismatch_after_the_switch_triggers_the_health_gated_rollback(tmp_path):
    h = Host(tmp_path, current=A, previous=C, known_good=[C])
    h.digests(**{B: SOAKED})                                  # the pre-switch check passes ...
    h.digests_running(**{B: OTHER})                           # ... but the tag moved before the container was created
    r = h.deploy(B, EXPECTED_DIGEST=SOAKED)
    assert r.returncode == 1 and "running backend digest is" in r.stderr
    # the previous release is restored WITHOUT the digest check (A has no recorded digest here) and stays current
    assert (h.read("CURRENT_SHA"), h.running) == (A, A)
    assert h.lines("FAILED_SHAS") == [B] and h.lines("KNOWN_GOOD_SHAS") == [C] and h.read("PREVIOUS_GOOD_SHA") == C
    log = (h.base / "logs" / "deploy.log").read_text()
    assert f"restored previous good backend {A}" in log and "ALSO failed" not in log


def test_a_correct_digest_with_a_failed_health_check_is_rolled_back_and_recorded(tmp_path):
    h = Host(tmp_path, current=A, previous=C, known_good=[C])
    h.digests(**{B: SOAKED})
    h.unhealthy(B)
    r = h.deploy(B, EXPECTED_DIGEST=SOAKED)
    assert r.returncode == 1
    assert (h.read("CURRENT_SHA"), h.running) == (A, A)
    assert h.lines("FAILED_SHAS") == [B] and h.read("PREVIOUS_GOOD_SHA") == C and h.lines("KNOWN_GOOD_SHAS") == [C]
    assert f"restored previous good backend {A}" in (h.base / "logs" / "deploy.log").read_text()


def test_pointers_stay_correct_through_a_digest_verified_deploy_and_a_later_rollback(tmp_path):
    h = Host(tmp_path, current=A, previous=C, known_good=[C])
    h.digests(**{B: SOAKED})
    assert h.deploy(B, EXPECTED_DIGEST=SOAKED).returncode == 0
    assert (h.read("CURRENT_SHA"), h.read("PREVIOUS_GOOD_SHA"), h.running) == (B, A, B)
    assert h.lines("KNOWN_GOOD_SHAS") == [C, A] and h.lines("FAILED_SHAS") == []     # the replaced release stays recoverable
    assert h.rollback().returncode == 0
    assert (h.read("CURRENT_SHA"), h.running) == (A, A) and h.lines("FAILED_SHAS") == [B]
    assert h.read("PREVIOUS_GOOD_SHA") == C                                          # never the release rolled back from


def test_a_malformed_expected_digest_is_refused_before_anything_changes(tmp_path):
    h = Host(tmp_path, current=A)
    r = h.deploy(B, EXPECTED_DIGEST="sha256:abc")
    assert r.returncode == 2 and "EXPECTED_DIGEST must be" in r.stderr
    assert (h.read("CURRENT_SHA"), h.running) == (A, A) and h.lines("FAILED_SHAS") == []


def test_without_expected_digest_nothing_changes_and_ci_entry_cannot_set_it():
    src = (VPS / "ci-entry.sh").read_text(encoding="utf-8")
    assert "EXPECTED_DIGEST" not in src and src.rstrip().endswith('exec /opt/donchian/scripts/deploy.sh backend "$TAG"')
