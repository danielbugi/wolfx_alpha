"""Pipeline step 8f/8g (Fundamental Updates publisher): the REAL block from automation_pipeline.sh, run under bash with run_optional_step
and log replaced by recorders. Off by default; record never passes --send; only an explicit gate session is ever used."""
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BASH = shutil.which("bash")


def block():
    src = open(os.path.join(ROOT, "automation_pipeline.sh"), encoding="utf-8").read()
    m = re.search(r'^FU_MODE=.*?^fi$', src, re.S | re.M)
    assert m, "step 8f block not found"
    return m.group(0)


def run(session, mode=None):
    env = {"PATH": os.environ.get("PATH", ""), "SESSION_DATE": session}
    if mode is not None:
        env["FU_PUBLISH_MODE"] = mode
    script = ('YELLOW=; NC=; log() { echo "LOG $*"; }\n'
              'run_optional_step() { echo "STEP $*"; }\n' + block())
    out = subprocess.run([BASH, "-c", script], env=env, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line.startswith("STEP")], out


pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


@pytest.mark.parametrize("mode", [None, "off"])
def test_off_by_default_runs_nothing(mode):
    steps, out = run("2026-11-10", mode)
    assert steps == [] and "Step 8f skipped" in out


def test_no_gate_session_runs_nothing_even_when_enabled():
    steps, out = run("", "send")
    assert steps == [] and "session=none" in out


def test_record_mode_records_then_reports_corrections_and_never_sends():
    steps, _ = run("2026-11-10", "record")
    assert len(steps) == 2
    assert steps[0].endswith("mechanism/inflection/update_publish.py --session 2026-11-10 --record") and "8f" in steps[0]
    assert steps[1].endswith("mechanism/inflection/update_publish.py --session 2026-11-10 --revise") and "8g" in steps[1]
    assert not any("--send" in s for s in steps)


def test_send_mode_adds_send_to_the_publisher_only():
    steps, _ = run("2026-11-10", "send")
    assert steps[0].endswith("--session 2026-11-10 --record --send") and "--send" not in steps[1]


def test_an_unknown_mode_is_skipped_with_a_warning():
    steps, out = run("2026-11-10", "yes")
    assert steps == [] and "OPTIONAL_STEP_WARNING step=8f reason=bad_mode" in out


def test_the_step_sits_after_the_sec_inflection_step_and_before_the_screener():
    src = open(os.path.join(ROOT, "automation_pipeline.sh"), encoding="utf-8").read()
    assert src.index('--source sec_pit "${ASOF_ARGS[@]}"') < src.index("FU_MODE=") < src.index('run_step 9 "multi-timeframe screener"')
