"""The owner-only failure alert (ops_failure_alert.py) and its host wrapper (deploy/vps/run_ops_alert.sh).

Pinned here: it only ever targets the owner's private chat, it sends only with --send AND OPS_ALERTS_ENABLED=1, it scrubs credentials from the
detail, the wrapper always leaves a log line, alerts once per job/day/status, reports a failed delivery as a failure, and never touches docker."""
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "mechanism"))
sys.path.insert(0, str(ROOT))

from alerts import ops_failure_alert as O  # noqa: E402

WRAPPER = ROOT / "deploy" / "vps" / "run_ops_alert.sh"
ALERT_UNIT = ROOT / "deploy" / "vps" / "donchian-forward-collection-alert.service"
NOW = datetime(2026, 11, 10, 4, 46, tzinfo=timezone.utc)
FC = "donchian-forward-collection"


class FakeClient:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_message(self, text, kind=None):
        if self.fail:
            raise RuntimeError("https://api.telegram.org/bot123456789:" + "A" * 35 + "/sendMessage failed")
        self.sent.append((text, kind))
        return [1]


# ------------------------------------------------------------------ the message
def test_each_documented_collector_exit_status_has_its_own_meaning():
    for status, word in ((2, "INCOMPLETE"), (4, "MISSED"), (5, "REFUSED")):
        t = O.build_text(FC + ".service", "exit-code", status, "", NOW)
        assert word in t and "donchian-forward-collection failed" in t and f"exit {status}" in t and "2026-11-10 04:46 UTC" in t


def test_an_unknown_status_or_unit_falls_back_to_the_systemd_result():
    assert "timed out" in O.build_text(FC, "timeout", None, "", NOW)
    assert "exited with a failure status (exit status 9)" in O.build_text("donchian-other", "exit-code", 9, "", NOW)


def test_the_detail_is_escaped_truncated_and_scrubbed_of_credentials():
    token = "123456789:" + "A" * 35
    raw = f"<b>x</b> {token} postgres://app:hunter2@db/x TELEGRAM_BOT_TOKEN=abc SMTP_PASSWORD: s3cret ghp_{'z' * 20} " + "y" * 600
    t = O.build_text(FC, "exit-code", 5, raw, NOW)
    for secret in (token, "hunter2", "=abc", "s3cret", "ghp_zzzz", "<b>x</b>"):
        assert secret not in t, secret
    assert "&lt;b&gt;x&lt;/b&gt;" in t and "[redacted]" in t
    assert len(O.scrub(raw)) <= O.MAX_DETAIL


# ------------------------------------------------------------------ the gates and the target
def test_nothing_is_sent_without_both_the_flag_and_the_environment_switch(capsys):
    made = []
    factory = lambda: made.append(1) or FakeClient()  # noqa: E731
    assert O.main(["--unit", FC, "--exit-status", "5"], client_factory=factory, env={"OPS_ALERTS_ENABLED": "1"}) == 0
    assert O.main(["--unit", FC, "--exit-status", "5", "--send"], client_factory=factory, env={}) == 0
    assert O.main(["--unit", FC, "--exit-status", "5", "--send"], client_factory=factory, env={"OPS_ALERTS_ENABLED": "true"}) == 0
    assert made == [] and capsys.readouterr().out.count("dry run") == 3


def test_with_both_gates_it_sends_one_owner_message():
    c = FakeClient()
    assert O.main(["--unit", FC, "--result", "exit-code", "--exit-status", "5", "--detail", "NOT ARMED", "--send"],
                  client_factory=lambda: c, env={"OPS_ALERTS_ENABLED": "1"}) == 0
    assert len(c.sent) == 1 and c.sent[0][1] == "ops_failure_alert" and "REFUSED" in c.sent[0][0] and "NOT ARMED" in c.sent[0][0]


def test_a_failed_send_exits_1_and_never_prints_the_exception_text(capsys):
    assert O.main(["--unit", FC, "--exit-status", "2", "--send"], client_factory=lambda: FakeClient(fail=True), env={"OPS_ALERTS_ENABLED": "1"}) == 1
    err = capsys.readouterr().err
    assert "RuntimeError" in err and "api.telegram.org" not in err and "AAAA" not in err


def test_the_target_is_fixed_to_the_owners_private_chat_and_there_is_no_channel_option():
    src = (ROOT / "mechanism" / "alerts" / "ops_failure_alert.py").read_text(encoding="utf-8")
    assert O.TARGET == "owner" and 'TelegramClient.from_env(TARGET, dry_run=False)' in src
    assert '"prod"' not in src and '"dev"' not in src and "TELEGRAM_CHAT_ID" not in src and "TELEGRAM_DEV_CHAT_ID" not in src
    with pytest.raises(SystemExit):                                  # there is no option to pick another target
        O.main(["--unit", FC, "--to", "prod", "--send"], client_factory=FakeClient, env={"OPS_ALERTS_ENABLED": "1"})


def test_the_alert_unit_runs_the_wrapper_and_cannot_be_enabled():
    lines = [ln.strip() for ln in ALERT_UNIT.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert lines[-1] == "ExecStart=/opt/donchian/scripts/run_ops_alert.sh donchian-forward-collection"
    assert "[Install]" not in ALERT_UNIT.read_text(encoding="utf-8") and "ExecStart=/bin/true" not in lines


# ------------------------------------------------------------------ the host wrapper, run for real against a scratch directory
def _bash():
    exe = shutil.which("bash")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-c", "echo ok"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return exe if r.returncode == 0 and r.stdout.strip() == "ok" else None


BASH = _bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="no working bash here")


@pytest.fixture
def world(tmp_path):
    base = tmp_path / "opt"
    (base / "bin").mkdir(parents=True)
    calls = tmp_path / "sender.calls"
    docker = base / "bin" / "docker"
    docker.write_text(f"#!/bin/sh\necho docker >> \"{(tmp_path / 'docker.calls').as_posix()}\"\nexit 0\n", encoding="utf-8")
    docker.chmod(0o755)

    def run(sender_rc=0, current="1fc1753cdae0", status="5", job=FC):
        if current is None:
            (base / "CURRENT_MECHANISM_SHA").unlink(missing_ok=True)
        else:
            (base / "CURRENT_MECHANISM_SHA").write_text(current + "\n")
        sender = tmp_path / "sender.sh"
        sender.write_text("#!/bin/sh\nfor a in \"$@\"; do printf '%s|' \"$a\"; done >> \"" + calls.as_posix() + "\"\necho >> \"" + calls.as_posix()
                          + f"\"\nexit {sender_rc}\n", encoding="utf-8")
        sender.chmod(0o755)
        env = dict(os.environ, DONCHIAN_BASE=base.as_posix(), OPS_ALERT_SENDER=sender.as_posix(), MONITOR_SERVICE_RESULT="exit-code",
                   MONITOR_EXIT_CODE="exited", MONITOR_EXIT_STATUS=status, PATH=(base / "bin").as_posix() + os.pathsep + os.environ.get("PATH", ""))
        return subprocess.run([BASH, WRAPPER.as_posix(), job], capture_output=True, text=True, env=env, timeout=60)

    def sent():
        return calls.read_text().splitlines() if calls.exists() else []

    def log():
        p = base / "logs" / "ops_alerts.log"
        return p.read_text() if p.exists() else ""

    return run, sent, log, tmp_path


@needs_bash
def test_the_wrapper_logs_every_failure_and_alerts_once_per_day_and_status(world):
    run, sent, log, tmp = world
    r = run()
    assert r.returncode == 0, r.stderr
    assert len(sent()) == 1
    args = sent()[0].split("|")
    assert args[:2] == ["mechanism/alerts/ops_failure_alert.py", "--unit"] and args[2] == FC and "--send" in args
    assert args[args.index("--detail") + 1] == "pinned mechanism image 1fc1753cdae0"
    assert args[args.index("--exit-status") + 1] == "5" and args[args.index("--result") + 1] == "exit-code"
    assert "ALERT job=donchian-forward-collection result=exit-code code=exited status=5 pinned mechanism image 1fc1753cdae0" in log()
    r2 = run()                                 # the second fire of the night: logged, not re-sent
    assert r2.returncode == 0 and len(sent()) == 1 and "already alerted today for status 5" in log()
    r3 = run(status="2")                                             # a different failure the same day is a new alert
    assert r3.returncode == 0 and len(sent()) == 2 and "|--exit-status|2|" in sent()[1]
    assert not (tmp / "docker.calls").exists()                       # the wrapper itself never touches docker


@needs_bash
def test_a_missing_pin_file_is_reported_as_unknown(world):
    run, sent, log, _ = world
    assert run(current=None).returncode == 0 and "pinned mechanism image unknown" in sent()[0]


@needs_bash
def test_a_failed_delivery_fails_the_alert_unit_and_is_retried_by_the_next_failure(world):
    run, sent, log, _ = world
    assert run(sender_rc=1).returncode == 1 and "DELIVERY FAILED (sender exit 1)" in log()
    assert run(sender_rc=0).returncode == 0 and len(sent()) == 2      # no stamp was written for the failed delivery


@needs_bash
def test_the_wrapper_refuses_an_unexpected_job_name(world):
    run, sent, log, _ = world
    r = run(job="x; rm -rf /")
    assert r.returncode == 2 and sent() == [] and log() == ""


def test_the_wrapper_contains_no_installer_or_unit_control_command():
    import re
    text = WRAPPER.read_text(encoding="utf-8")
    assert not re.search(r"systemctl|/etc/systemd|daemon-reload|systemd-run|enable --now|\.preset", text, re.I)
    assert "psql" not in text and "FORWARD_COLLECTION_ARMED" not in text      # only the collector's own wrapper may refer to its arming file
