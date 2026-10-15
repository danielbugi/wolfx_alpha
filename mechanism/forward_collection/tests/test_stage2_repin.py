"""deploy/vps/stage2_repin.sh against a fake /opt/donchian tree with stub docker / unit-introspection / collector-check scripts (Linux only: bash, flock, GNU date/stat/df).

Checks that every stop condition stops BEFORE anything is changed; that `apply` writes ONLY the mechanism pin (never the collector's arming file, never the tracker's pin/arming, never the bot's tag file) and
prints the exact arming command for the operator; that `collector-check` refuses to run until the operator armed and does not roll back by itself when the collector check fails; that `verify` catches a
changed row count / recreated container / changed tracker pin; that `rollback` restores the saved pin bytes and again leaves the arming to the operator; and that the file itself contains none of the words
the repository's dormancy guards forbid (no unit-management command, no collector name, no arming-file name)."""
import os
import re
import shutil
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
    arming = base / "ARMING"
    for f, v in (("CURRENT_MECHANISM_SHA", OLD), ("ARMING", OLD), ("CURRENT_PAPER_SHA", "45c2ac39249d"), ("PAPER_TRACKER_ARMED", "45c2ac39249d")):
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
    put(fake / "next_elapse", time.strftime("%a %Y-%m-%d %H:%M:%S UTC", time.gmtime(time.time() + 6 * 3600)))
    put(fake / "docker", f"""#!/usr/bin/env bash
F="{fake}"
case "$1" in
  image) img="$3"; case "$img" in *:{NEW}) [ -f "$F/absent_new" ] && exit 1; echo "ghcr.io/danielbugi/wolfx_alpha-mechanism@$(cat $F/digest_new)";; *:{OLD}) [ -f "$F/absent_old" ] && exit 1; echo "ghcr.io/danielbugi/wolfx_alpha-mechanism@$(cat $F/digest_old)";; *) exit 1;; esac;;
  ps) cat "$F/containers";;
  exec) cat > /dev/null; cat "$F/counts";;
  *) exit 0;;
esac
""", 0o755)
    put(fake / "unitctl", f"""#!/usr/bin/env bash
F="{fake}"
case "$1" in
  list-units) cat "$F/running";;
  list-unit-files) cat "$F/timers";;
  show) cat "$F/next_elapse";;
esac
""", 0o755)
    put(fake / "gate", f'#!/usr/bin/env bash\nexit "$(cat {fake}/gate_rc)"\n', 0o755)
    put(fake / "collector_check", f'#!/usr/bin/env bash\necho "collector-check pin=$(cat {base}/CURRENT_MECHANISM_SHA) arm=$(cat {arming})" >> {fake}/collector.log\nexit "$(cat {fake}/collector_rc)"\n', 0o755)
    put(scripts / "run_pipeline.sh", f'#!/usr/bin/env bash\necho "pipeline" >> {fake}/pipeline.log\nexit "$(cat {fake}/pipeline_rc)"\n', 0o755)
    put(fake / "pre_backup", f"""#!/usr/bin/env bash
d="{base}/backups/pre-release-b/stage2-repin-$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$d"; echo dump > "$d/production.dump"; (cd "$d" && sha256sum production.dump > production.dump.sha256)
echo "BK=$d"; echo "OK label=stage2-repin"
""", 0o755)
    env = dict(os.environ, DONCHIAN_BASE=str(base), DONCHIAN_SCRIPTS=str(scripts), DOCKER=str(fake / "docker"), UNITCTL=str(fake / "unitctl"), GATE_CMD=str(fake / "gate"),
               ARMING_FILE=str(arming), COLLECTOR_CHECK=str(fake / "collector_check"), PRE_BACKUP=str(fake / "pre_backup"), MIN_FREE_GB="0")
    return {"base": base, "fake": fake, "env": env, "arming": arming}


def run(w, *args, env=None):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=dict(w["env"], **(env or {})), timeout=60)


def snap(w, names):
    out = {}
    for n in names:
        p = w["arming"] if n == "ARMING" else w["base"] / n
        out[n] = p.read_bytes() if p.exists() else None
    return out


UNTOUCHED = ["CURRENT_PAPER_SHA", "PAPER_TRACKER_ARMED", "env/mechanism_image_tag.env"]
PIN_AND_ARM = ["CURRENT_MECHANISM_SHA", "ARMING"]


@linux_only
def test_preflight_passes_in_a_clean_world(w):
    r = run(w, "preflight", NEW, DIG_NEW)
    assert r.returncode == 0 and r.stdout.count("FAIL") == 0 and "ALL PASS" in r.stdout


@linux_only
def test_the_operator_supplied_variables_are_required(w):
    for var in ("ARMING_FILE", "COLLECTOR_CHECK", "UNITCTL"):
        r = run(w, "preflight", NEW, DIG_NEW, env={var: ""})
        assert r.returncode == 1 and "STOP: set the operator-supplied variables first" in r.stdout and var in r.stdout


@linux_only
@pytest.mark.parametrize("name,mutate,needle", [
    ("pin is not the expected rollback image", lambda w: put(w["base"] / "CURRENT_MECHANISM_SHA", "deadbeefcafe\n"), "mechanism pin is the expected rollback image"),
    ("collector is not armed for the pin", lambda w: put(w["arming"], "deadbeefcafe\n"), "arming file equals the pin"),
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
    before = snap(w, PIN_AND_ARM + UNTOUCHED)
    r = run(w, "preflight", NEW, DIG_NEW)
    assert r.returncode == 1 and any(l.startswith("FAIL") and needle in l for l in r.stdout.splitlines()), (name, r.stdout)
    a = run(w, "apply", NEW, DIG_NEW)
    assert a.returncode == 1 and "STOP: preflight failed" in a.stdout
    assert snap(w, PIN_AND_ARM + UNTOUCHED) == before and not (w["fake"] / "collector.log").exists() and not (w["base"] / "stage2-repin").exists()


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
    before = snap(w, PIN_AND_ARM + UNTOUCHED)
    r = run(w, "apply", NEW, DIG_NEW)
    assert r.returncode == 1 and "no verified stage2-repin backup" in r.stdout and snap(w, PIN_AND_ARM + UNTOUCHED) == before


@linux_only
def test_apply_writes_only_the_pin_never_the_arming_file_and_prints_the_exact_arming_command(w):
    assert run(w, "backup").returncode == 0
    before = snap(w, UNTOUCHED + ["ARMING"])
    r = run(w, "apply", NEW, DIG_NEW)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (w["base"] / "CURRENT_MECHANISM_SHA").read_text() == NEW + "\n"
    assert snap(w, UNTOUCHED + ["ARMING"]) == before                                          # arming file, tracker pin/arming, bot tag file: byte-identical
    assert f"printf '%s\\n' {NEW} > {w['arming']}" in r.stdout and "YOU arm it" in r.stdout
    assert not (w["fake"] / "collector.log").exists()                                         # the collector is not run by apply
    st = next((w["base"] / "stage2-repin").iterdir())
    assert (st / "pin_before").read_text() == OLD + "\n" and (st / "arming_before").read_text() == OLD + "\n" and (st / "counts_before.txt").read_text() == COUNTS
    assert not list(w["base"].glob("*.new"))                                                 # the write was an atomic rename


def _applied(w):
    run(w, "backup")
    assert run(w, "apply", NEW, DIG_NEW).returncode == 0
    return str(next((w["base"] / "stage2-repin").iterdir()))


@linux_only
def test_collector_check_refuses_until_the_operator_has_armed_then_runs_the_check_once(w):
    st = _applied(w)
    r = run(w, "collector-check", st)
    assert r.returncode == 1 and "not armed for" in r.stdout and not (w["fake"] / "collector.log").exists()
    put(w["arming"], NEW + "\n")                                                              # the deliberate human act
    r = run(w, "collector-check", st)
    assert r.returncode == 0 and (w["fake"] / "collector.log").read_text().strip() == f"collector-check pin={NEW} arm={NEW}"


@linux_only
def test_a_failing_collector_check_stops_without_rolling_back_by_itself(w):
    st = _applied(w)
    put(w["arming"], NEW + "\n")
    put(w["fake"] / "collector_rc", "5")
    r = run(w, "collector-check", st)
    assert r.returncode == 1 and "collector re-verification exited 0" in r.stdout and "Nothing was rolled back automatically" in r.stdout
    assert (w["base"] / "CURRENT_MECHANISM_SHA").read_text() == NEW + "\n" and w["arming"].read_text() == NEW + "\n"


@linux_only
def test_verify_passes_when_nothing_else_changed(w):
    st = _applied(w)
    put(w["arming"], NEW + "\n")
    r = run(w, "verify", st)
    assert r.returncode == 0 and "ALL PASS" in r.stdout and (w["fake"] / "pipeline.log").exists()


@linux_only
@pytest.mark.parametrize("mutate,needle", [
    (lambda w: put(w["fake"] / "counts", COUNTS.replace("2202", "2203")), "same row count as before"),
    (lambda w: put(w["fake"] / "containers", "donchian-screener-backend-1 backend:x ZZZ999\ndonchian-screener-bot-1 mech:575fa5dc0599 bbb222\ndonchian-screener-postgres-1 postgres:16 ccc333\n"), "no long-lived container was recreated"),
    (lambda w: put(w["base"] / "CURRENT_PAPER_SHA", "ffffffffffff\n"), "byte-identical to before"),
    (lambda w: put(w["base"] / "env" / "mechanism_image_tag.env", "IMAGE_TAG=changed\n"), "byte-identical to before"),
    (lambda w: put(w["arming"], OLD + "\n"), "explicitly re-armed"),
])
def test_verify_catches_each_kind_of_unexpected_change(w, mutate, needle):
    st = _applied(w)
    put(w["arming"], NEW + "\n")
    mutate(w)
    r = run(w, "verify", st)
    assert r.returncode == 1 and any(l.startswith("FAIL") and needle in l for l in r.stdout.splitlines()), r.stdout


@linux_only
def test_verify_does_not_smoke_the_pipeline_when_a_new_session_is_pending(w):
    st = _applied(w)
    put(w["arming"], NEW + "\n")
    put(w["fake"] / "gate_rc", "0")
    r = run(w, "verify", st)
    assert not (w["fake"] / "pipeline.log").exists() and "a new session is pending" in r.stdout


@linux_only
def test_rollback_restores_the_saved_pin_bytes_and_leaves_the_arming_to_the_operator(w):
    st = _applied(w)
    put(w["arming"], NEW + "\n")
    r = run(w, "rollback", st)
    assert r.returncode == 0, r.stdout
    assert (w["base"] / "CURRENT_MECHANISM_SHA").read_text() == OLD + "\n"
    assert w["arming"].read_text() == NEW + "\n"                                              # the tool does not touch the arming file, not even to roll back
    assert f"printf '%s\\n' {OLD} > {w['arming']}" in r.stdout and f"collector-check {st} {OLD}" in r.stdout
    put(w["arming"], OLD + "\n")                                                              # the operator re-arms the old image by hand
    c = run(w, "collector-check", st, OLD)
    assert c.returncode == 0 and (w["fake"] / "collector.log").read_text().strip() == f"collector-check pin={OLD} arm={OLD}"


# ---------------------------------------------------------------------------------------------------------------------------------- what the repository's dormancy guards require of this file
def _code():
    text = SCRIPT.read_text(encoding="utf-8")
    return "\n".join(l for l in text.replace(chr(13), "").splitlines() if not l.lstrip().startswith("#")), text.replace(chr(13), "")


def test_the_file_contains_none_of_the_words_the_dormancy_guards_forbid_in_deploy_files():
    _, whole = _code()
    installers = re.compile(r"systemctl|/etc/systemd|daemon-reload|systemd-run|enable --now|\.preset", re.I)          # tests/test_dormant_units.py
    assert installers.search(whole) is None
    assert "forward_collection" not in whole and "forward-collection" not in whole                                       # tests/test_guards.py
    assert "FORWARD_COLLECTION_ARMED" not in whole                                                                       # tests/test_dormant_units.py: no repository file creates the arming file


def test_the_tool_never_writes_the_arming_file_the_tracker_files_or_the_bot_tag_file_and_is_referenced_by_nothing():
    import shlex
    code, _ = _code()
    protected = ("$ARMING_FILE", "$BASE/CURRENT_PAPER_SHA", "$BASE/PAPER_TRACKER_ARMED", "$BASE/env/mechanism_image_tag.env")
    hits_protected = lambda tok: any(tok.strip("\"'{}").startswith(p) for p in protected)
    offenders = []
    for line in code.splitlines():
        if line.lstrip().startswith("echo"):
            continue                                                                       # printed operator instructions, not actions
        for m in re.finditer(r">>?\s*(\"?\$[A-Za-z_/{}.]+)", line):                     # any redirection into a protected file
            if hits_protected(m.group(1)):
                offenders.append(line.strip())
        try:
            toks = shlex.split(line, comments=True)
        except ValueError:
            continue
        for k, t in enumerate(toks):
            if t in ("cp", "mv") and k + 1 < len(toks):
                args = [a for a in toks[k + 1:] if not a.startswith("-") and a not in ("&&", "||", ";", "|")]
                if args and hits_protected(args[-1]):                                      # the DESTINATION of a copy/move
                    offenders.append(line.strip())
            if t in ("rm", "touch", "tee") and any(hits_protected(a) for a in toks[k + 1:]):
                offenders.append(line.strip())
    assert offenders == [], offenders
    for forbidden in ("docker restart", "docker stop", "docker rm", "docker compose up"):
        assert forbidden not in code, forbidden
    for f in list((REPO / "deploy" / "vps").glob("*.service")) + list((REPO / "deploy" / "vps").glob("*.timer")) + [REPO / ".github" / "workflows" / "cd.yml", REPO / ".github" / "workflows" / "ci.yml"]:
        assert "stage2_repin" not in f.read_text(encoding="utf-8"), f.name
