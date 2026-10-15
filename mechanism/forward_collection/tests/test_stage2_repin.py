"""deploy/vps/stage2_repin.sh against a fake /opt/donchian tree with stub docker / systemctl / wrapper scripts (Linux only: bash, flock, GNU date/stat/df).

Checks that every stop condition stops BEFORE anything is changed, that apply writes exactly the two files it should (pin and arming, atomically), that the tracker pin, tracker arming and the bot's
tag file are never touched, that a failing collector re-verification does NOT roll back by itself, that verify catches a changed row count / recreated container / changed tracker pin, and that rollback
restores the saved bytes."""
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "deploy" / "vps" / "stage2_repin.sh"
linux_only = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None or shutil.which("flock") is None, reason="needs POSIX bash + flock (CI / VPS)")
OLD, NEW = "5c511899505a", "a1b2c3d4e5f6"
DIG_NEW, DIG_OLD = "sha256:" + "a" * 64, "sha256:" + "b" * 64
COUNTS = "candidate_observation|4718\nsignal_ledger|2202\npaper_session|1\npaper_observation|3345\nstrategy_variant|5\n"


def put(path, text, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode:
        path.chmod(mode)


@pytest.fixture
def w(tmp_path):
    base, fake, scripts = tmp_path / "donchian", tmp_path / "fake", tmp_path / "donchian" / "scripts"
    for f, v in (("CURRENT_MECHANISM_SHA", OLD), ("FORWARD_COLLECTION_ARMED", OLD), ("CURRENT_PAPER_SHA", "45c2ac39249d"), ("PAPER_TRACKER_ARMED", "45c2ac39249d")):
        put(base / f, v + "\n")
    put(base / "env" / "mechanism_image_tag.env", "IMAGE_TAG=575fa5dc0599\n")
    fake.mkdir()
    put(fake / "digest_new", DIG_NEW)
    put(fake / "digest_old", DIG_OLD)
    put(fake / "counts", COUNTS)
    put(fake / "containers", "donchian-screener-backend-1 backend:x aaa111\ndonchian-screener-bot-1 mech:575fa5dc0599 bbb222\ndonchian-screener-postgres-1 postgres:16 ccc333\n")
    put(fake / "running", "")
    put(fake / "timers", "donchian-pipeline.timer enabled\ndonchian-paper-tracker.timer enabled\n")
    put(fake / "gate_rc", "3")
    put(fake / "collector_rc", "0")
    put(fake / "pipeline_rc", "0")
    in_hours = time.strftime("%a %Y-%m-%d %H:%M:%S UTC", time.gmtime(time.time() + 6 * 3600))
    put(fake / "next_elapse", in_hours)
    put(fake / "docker", f"""#!/usr/bin/env bash
F="{fake}"
case "$1" in
  image) img="$3"; case "$img" in *:{NEW}) [ -f "$F/absent_new" ] && exit 1; echo "ghcr.io/danielbugi/wolfx_alpha-mechanism@$(cat $F/digest_new)";; *:{OLD}) [ -f "$F/absent_old" ] && exit 1; echo "ghcr.io/danielbugi/wolfx_alpha-mechanism@$(cat $F/digest_old)";; *) exit 1;; esac;;
  ps) cat "$F/containers";;
  exec) cat > /dev/null; cat "$F/counts";;
  *) exit 0;;
esac
""", 0o755)
    put(fake / "systemctl", f"""#!/usr/bin/env bash
F="{fake}"
case "$1" in
  list-units) cat "$F/running";;
  list-unit-files) cat "$F/timers";;
  show) cat "$F/next_elapse";;
esac
""", 0o755)
    put(fake / "gate", f'#!/usr/bin/env bash\nexit "$(cat {fake}/gate_rc)"\n', 0o755)
    put(scripts / "run_forward_collection.sh", f'#!/usr/bin/env bash\necho "collector $(cat {base}/CURRENT_MECHANISM_SHA) $(cat {base}/FORWARD_COLLECTION_ARMED)" >> {fake}/collector.log\nexit "$(cat {fake}/collector_rc)"\n', 0o755)
    put(scripts / "run_pipeline.sh", f'#!/usr/bin/env bash\necho "pipeline" >> {fake}/pipeline.log\nexit "$(cat {fake}/pipeline_rc)"\n', 0o755)
    put(fake / "pre_backup", f"""#!/usr/bin/env bash
d="{base}/backups/pre-release-b/stage2-repin-$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$d"; echo dump > "$d/production.dump"; (cd "$d" && sha256sum production.dump > production.dump.sha256)
echo "BK=$d"; echo "OK label=stage2-repin"
""", 0o755)
    env = dict(os.environ, DONCHIAN_BASE=str(base), DONCHIAN_SCRIPTS=str(scripts), DOCKER=str(fake / "docker"), SYSTEMCTL=str(fake / "systemctl"), GATE_CMD=str(fake / "gate"),
               PRE_BACKUP=str(fake / "pre_backup"), MIN_FREE_GB="0")
    return {"base": base, "fake": fake, "env": env}


def run(w, *args, env=None):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=dict(w["env"], **(env or {})), timeout=60)


def snapshot(base, names):
    return {n: (base / n).read_bytes() if (base / n).exists() else None for n in names}


UNTOUCHED = ["CURRENT_PAPER_SHA", "PAPER_TRACKER_ARMED", "env/mechanism_image_tag.env"]
PINS = ["CURRENT_MECHANISM_SHA", "FORWARD_COLLECTION_ARMED"]


@linux_only
def test_preflight_passes_in_a_clean_world(w):
    r = run(w, "preflight", NEW, DIG_NEW)
    assert r.returncode == 0 and r.stdout.count("FAIL") == 0 and "ALL PASS" in r.stdout


@linux_only
@pytest.mark.parametrize("name,mutate,needle", [
    ("pin is not the expected rollback image", lambda w: put(w["base"] / "CURRENT_MECHANISM_SHA", "deadbeefcafe\n"), "CURRENT_MECHANISM_SHA is the expected rollback image"),
    ("collector is not armed for the pin", lambda w: put(w["base"] / "FORWARD_COLLECTION_ARMED", "deadbeefcafe\n"), "FORWARD_COLLECTION_ARMED equals the pin"),
    ("tracker pin missing", lambda w: (w["base"] / "PAPER_TRACKER_ARMED").unlink(), "tracker pin and arming present and equal"),
    ("new image digest differs", lambda w: put(w["fake"] / "digest_new", "sha256:" + "c" * 64), "digest equals the recorded one"),
    ("new image absent", lambda w: put(w["fake"] / "absent_new", ""), "new image is present locally"),
    ("rollback image absent", lambda w: put(w["fake"] / "absent_old", ""), "rollback image is present locally"),
    ("a service is running", lambda w: put(w["fake"] / "running", "donchian-pipeline.service loaded active running First Light\n"), "no donchian service is running"),
    ("a timer is due in 10 minutes", lambda w: put(w["fake"] / "next_elapse", time.strftime("%a %Y-%m-%d %H:%M:%S UTC", time.gmtime(time.time() + 600))), "no timer is due within"),
    ("a new session is pending (gate would run the pipeline)", lambda w: put(w["fake"] / "gate_rc", "0"), "gated no-op"),
])
def test_every_stop_condition_fails_the_preflight_and_apply_changes_nothing(w, name, mutate, needle):
    mutate(w)
    before = snapshot(w["base"], PINS + UNTOUCHED)
    r = run(w, "preflight", NEW, DIG_NEW)
    assert r.returncode == 1 and any(l.startswith("FAIL") and needle in l for l in r.stdout.splitlines()), (name, r.stdout)
    a = run(w, "apply", NEW, DIG_NEW)
    assert a.returncode == 1 and "STOP: preflight failed" in a.stdout
    assert snapshot(w["base"], PINS + UNTOUCHED) == before and not (w["fake"] / "collector.log").exists()


@linux_only
def test_a_held_lock_file_fails_the_preflight(w):
    lock = w["base"] / ".pipeline.lock"
    lock.write_text("")
    holder = subprocess.Popen(["bash", "-c", f"exec 9>{lock}; flock -n 9; sleep 20"])
    try:
        time.sleep(0.5)
        r = run(w, "preflight", NEW, DIG_NEW)
        assert r.returncode == 1 and "held: .pipeline.lock" in r.stdout
    finally:
        holder.kill()


@linux_only
def test_too_little_free_disk_fails_the_preflight(w):
    r = run(w, "preflight", NEW, DIG_NEW, env={"MIN_FREE_GB": "99999999"})
    assert r.returncode == 1 and "free disk" in r.stdout and "FAIL" in r.stdout


@linux_only
def test_apply_refuses_without_a_fresh_verified_backup_and_changes_nothing(w):
    before = snapshot(w["base"], PINS + UNTOUCHED)
    r = run(w, "apply", NEW, DIG_NEW)
    assert r.returncode == 1 and "no verified stage2-repin backup" in r.stdout and snapshot(w["base"], PINS + UNTOUCHED) == before


@linux_only
def test_apply_writes_exactly_the_pin_and_the_arming_file_and_nothing_else(w):
    assert run(w, "backup").returncode == 0
    before = snapshot(w["base"], UNTOUCHED)
    r = run(w, "apply", NEW, DIG_NEW)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (w["base"] / "CURRENT_MECHANISM_SHA").read_text() == NEW + "\n" and (w["base"] / "FORWARD_COLLECTION_ARMED").read_text() == NEW + "\n"
    assert snapshot(w["base"], UNTOUCHED) == before                                          # tracker pin, tracker arming, bot tag file: byte-identical
    assert (w["fake"] / "collector.log").read_text().strip() == f"collector {NEW} {NEW}"      # the collector wrapper ran once, already seeing the new pin AND the new arming
    states = list((w["base"] / "stage2-repin").iterdir())
    assert len(states) == 1 and (states[0] / "CURRENT_MECHANISM_SHA").read_text() == OLD + "\n" and (states[0] / "counts_before.txt").read_text() == COUNTS
    assert not list(w["base"].glob("*.new"))                                                 # writes were atomic renames


@linux_only
def test_a_failing_collector_reverification_stops_without_rolling_back_by_itself(w):
    run(w, "backup")
    put(w["fake"] / "collector_rc", "5")
    r = run(w, "apply", NEW, DIG_NEW)
    assert r.returncode == 1 and "STOP: collector re-verification failed (exit 5)" in r.stdout and "Nothing was rolled back automatically" in r.stdout and "rollback" in r.stdout
    assert (w["base"] / "CURRENT_MECHANISM_SHA").read_text() == NEW + "\n"                  # left as is for a human to decide


def _applied(w):
    run(w, "backup")
    assert run(w, "apply", NEW, DIG_NEW).returncode == 0
    return str(next((w["base"] / "stage2-repin").iterdir()))


@linux_only
def test_verify_passes_when_nothing_else_changed(w):
    st = _applied(w)
    r = run(w, "verify", st)
    assert r.returncode == 0 and "ALL PASS" in r.stdout and (w["fake"] / "pipeline.log").exists()


@linux_only
@pytest.mark.parametrize("mutate,needle", [
    (lambda w: put(w["fake"] / "counts", COUNTS.replace("2202", "2203")), "same row count as before"),
    (lambda w: put(w["fake"] / "containers", "donchian-screener-backend-1 backend:x ZZZ999\ndonchian-screener-bot-1 mech:575fa5dc0599 bbb222\ndonchian-screener-postgres-1 postgres:16 ccc333\n"), "no long-lived container was recreated"),
    (lambda w: put(w["base"] / "CURRENT_PAPER_SHA", "ffffffffffff\n"), "byte-identical to before"),
    (lambda w: put(w["base"] / "env" / "mechanism_image_tag.env", "IMAGE_TAG=changed\n"), "byte-identical to before"),
    (lambda w: put(w["base"] / "FORWARD_COLLECTION_ARMED", OLD + "\n"), "explicitly re-armed"),
])
def test_verify_catches_each_kind_of_unexpected_change(w, mutate, needle):
    st = _applied(w)
    mutate(w)
    r = run(w, "verify", st)
    assert r.returncode == 1 and any(l.startswith("FAIL") and needle in l for l in r.stdout.splitlines()), r.stdout


@linux_only
def test_verify_does_not_smoke_the_pipeline_when_a_new_session_is_pending(w):
    st = _applied(w)
    put(w["fake"] / "gate_rc", "0")
    r = run(w, "verify", st)
    assert not (w["fake"] / "pipeline.log").exists() and "a new session is pending" in r.stdout


@linux_only
def test_rollback_restores_the_saved_bytes_and_reverifies_the_collector(w):
    st = _applied(w)
    (w["fake"] / "collector.log").unlink()
    r = run(w, "rollback", st)
    assert r.returncode == 0, r.stdout
    assert (w["base"] / "CURRENT_MECHANISM_SHA").read_text() == OLD + "\n" and (w["base"] / "FORWARD_COLLECTION_ARMED").read_text() == OLD + "\n"
    assert (w["fake"] / "collector.log").read_text().strip() == f"collector {OLD} {OLD}"


def test_the_tool_is_dormant_and_never_writes_the_tracker_or_bot_files():
    import re
    text = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(l for l in text.replace(chr(13), "").splitlines() if not l.lstrip().startswith("#"))
    protected = r"(CURRENT_PAPER_SHA|PAPER_TRACKER_ARMED|env/mechanism_image_tag\.env)"
    write_to_protected = re.compile(r"(>>?|\bmv\b[^\n]*|\brm\b[^\n]*|\btouch\b[^\n]*)\s*\"?\$BASE/" + protected)
    assert write_to_protected.search(code) is None
    for forbidden in ("systemctl enable", "systemctl start", "systemctl restart", "daemon-reload", "docker restart", "docker stop"):
        assert forbidden not in code, forbidden
    for f in list((REPO / "deploy" / "vps").glob("*.service")) + list((REPO / "deploy" / "vps").glob("*.timer")) + [REPO / ".github" / "workflows" / "cd.yml", REPO / ".github" / "workflows" / "ci.yml"]:
        assert "stage2_repin" not in f.read_text(encoding="utf-8"), f.name
