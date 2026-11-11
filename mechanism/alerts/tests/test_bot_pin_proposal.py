"""The PROPOSED separation of the bot's image pin (docs/operations/BOT_DUAL_PIN.md). Nothing here is installed on the VPS; these tests pin the properties the proposal must have before anyone is asked to install it:

* a backend deploy, a mechanism pin change or a stray IMAGE_TAG can never change which image the bot runs;
* the bot's wrapper recreates only the bot and refuses to start without a running postgres;
* the blast-radius guard notices when anything other than backend / reverse-proxy changes during a deploy;
* the compose overlay proposal gives the bot a strict variable of its own, and the deploy.sh proposal only adds the library calls and the guard.

The behaviour tests run the real shell scripts against a stub `docker` on PATH (no Docker, no network); they are Linux-only (CI and the VPS), the static tests run anywhere."""
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
VPS = REPO / "deploy" / "vps"
OVERLAY = REPO / "docker-compose.prod.yml.proposed"
HEX = ("a" * 12, "b" * 12, "c" * 12, "d" * 12)         # backend, bot, mechanism, another
linux_only = pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None, reason="needs a POSIX bash (CI / VPS)")


def text(p: Path) -> str:
    return p.read_text(encoding="utf-8").replace("\r\n", "\n")


# ---------------------------------------------------------------------------------------------------------------- stubs
@pytest.fixture
def world(tmp_path):
    """A fake /opt/donchian with pin files and a compose dir, and a stub `docker` that records its calls and the image-tag environment it was started with."""
    base = tmp_path / "donchian"
    (base / "compose").mkdir(parents=True)
    (base / "env").mkdir()
    (base / "env" / ".env").write_text("X=1\n")
    for name, value in (("CURRENT_SHA", HEX[0]), ("CURRENT_BOT_SHA", HEX[1]), ("CURRENT_MECHANISM_SHA", HEX[2])):
        (base / name).write_text(value + "\n")
    (base / "compose" / "docker-compose.yml").write_text("services: {}\n")
    (base / "compose" / "docker-compose.prod.yml").write_text("services: {}\n")
    stub = tmp_path / "bin"
    stub.mkdir()
    log = tmp_path / "docker.log"
    containers = tmp_path / "containers"                       # one name per line; inspect data in files named inspect_<name>
    script = stub / "docker"
    script.write_text(f"""#!/usr/bin/env bash
echo "ARGS: $*" >> "{log}"
echo "ENV: BACKEND=${{BACKEND_IMAGE_TAG-}} BOT=${{BOT_IMAGE_TAG-}} MECH=${{MECHANISM_IMAGE_TAG-}} FRONT=${{FRONTEND_IMAGE_TAG-}} LEGACY=${{IMAGE_TAG-<unset>}}" >> "{log}"
case "$1" in
  compose)
    if [[ " $* " == *" ps -q --status running postgres "* ]]; then [ "${{STUB_POSTGRES_RUNNING:-1}}" = 1 ] && echo abc123; fi ;;
  ps) cat "{containers}" 2>/dev/null ;;
  inspect) n="${{@: -1}}"; cat "{tmp_path}/inspect_$n" 2>/dev/null ;;
esac
exit 0
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}", DONCHIAN_BASE=str(base))
    env.pop("IMAGE_TAG", None)
    return {"base": base, "env": env, "log": log, "tmp": tmp_path, "containers": containers}


def run(cmd, world, **extra):
    env = dict(world["env"], **{k: v for k, v in extra.items() if v is not None})
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(REPO))


def calls(world):
    return world["log"].read_text().splitlines() if world["log"].exists() else []


# ---------------------------------------------------------------------------------------------------------------- compose_env.sh
def exported(world, **extra):
    r = run(["bash", "-c", f". {VPS}/compose_env.sh; compose_env_export && env | grep -E '^(BACKEND|BOT|MECHANISM|FRONTEND)_IMAGE_TAG=|^IMAGE_TAG=' | sort"], world, **extra)
    return r, dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)


@linux_only
def test_each_variable_comes_from_its_own_pin_file(world):
    r, v = exported(world)
    assert r.returncode == 0, r.stderr
    assert v == {"BACKEND_IMAGE_TAG": HEX[0], "BOT_IMAGE_TAG": HEX[1], "MECHANISM_IMAGE_TAG": HEX[2], "FRONTEND_IMAGE_TAG": HEX[0]}


@linux_only
def test_changing_another_pin_never_changes_the_bot_tag(world):
    (world["base"] / "CURRENT_SHA").write_text(HEX[3] + "\n")                  # a backend deploy
    (world["base"] / "CURRENT_MECHANISM_SHA").write_text(HEX[3] + "\n")        # a mechanism pin change
    _, v = exported(world)
    assert v["BOT_IMAGE_TAG"] == HEX[1] and v["BACKEND_IMAGE_TAG"] == HEX[3] and v["MECHANISM_IMAGE_TAG"] == HEX[3]
    (world["base"] / "CURRENT_BOT_SHA").write_text(HEX[3] + "\n")
    _, v = exported(world)
    assert v["BOT_IMAGE_TAG"] == HEX[3]


@linux_only
def test_the_environment_cannot_override_the_bot_or_mechanism_tag_and_the_legacy_variable_is_dropped(world):
    _, v = exported(world, BOT_IMAGE_TAG="f" * 12, MECHANISM_IMAGE_TAG="e" * 12, IMAGE_TAG="9" * 12)
    assert v["BOT_IMAGE_TAG"] == HEX[1] and v["MECHANISM_IMAGE_TAG"] == HEX[2]
    assert "IMAGE_TAG" not in v                                               # the legacy shared variable never reaches a compose call


@linux_only
def test_a_deploy_may_preset_only_the_backend_tag_and_it_is_validated(world):
    _, v = exported(world, BACKEND_IMAGE_TAG="1" * 12)
    assert v["BACKEND_IMAGE_TAG"] == "1" * 12 and v["FRONTEND_IMAGE_TAG"] == "1" * 12 and v["BOT_IMAGE_TAG"] == HEX[1]
    r, _ = exported(world, BACKEND_IMAGE_TAG="latest")
    assert r.returncode != 0 and "not a 12-hex" in r.stderr


@linux_only
@pytest.mark.parametrize("bad", ["", "latest", "ABCDEF123456", "abc", "g" * 12, "a" * 13])
def test_a_malformed_pin_is_refused(world, bad):
    (world["base"] / "CURRENT_BOT_SHA").write_text(bad + "\n")
    r, _ = exported(world)
    assert r.returncode != 0 and "CURRENT_BOT_SHA" in r.stderr


@linux_only
def test_a_missing_bot_pin_is_refused_with_a_clear_message(world):
    (world["base"] / "CURRENT_BOT_SHA").unlink()
    r, _ = exported(world)
    assert r.returncode != 0 and "missing pin file" in r.stderr and "bot" in r.stderr


# ---------------------------------------------------------------------------------------------------------------- run_bot_service.sh
@linux_only
def test_up_recreates_only_the_bot_with_the_bots_own_tag(world):
    r = run(["bash", str(VPS / "run_bot_service.sh"), "up"], world)
    assert r.returncode == 0, r.stderr
    up = [c for c in calls(world) if c.startswith("ARGS:") and " up " in c]
    assert len(up) == 1 and up[0].endswith("--profile bot up -d --no-deps bot")
    env_line = calls(world)[calls(world).index(up[0]) + 1]
    assert f"BOT={HEX[1]}" in env_line and f"MECH={HEX[2]}" in env_line and "LEGACY=<unset>" in env_line


@linux_only
def test_up_refuses_without_a_running_postgres_and_never_calls_up(world):
    r = run(["bash", str(VPS / "run_bot_service.sh"), "up"], world, STUB_POSTGRES_RUNNING="0")
    assert r.returncode == 1 and "postgres is not running" in r.stderr
    assert not [c for c in calls(world) if " up " in c]


@linux_only
def test_stop_and_unknown_modes(world):
    assert run(["bash", str(VPS / "run_bot_service.sh"), "stop"], world).returncode == 0
    assert any(c.endswith("--profile bot stop bot") for c in calls(world))
    r = run(["bash", str(VPS / "run_bot_service.sh"), "restart"], world)
    assert r.returncode == 2


@linux_only
def test_the_bot_tag_is_unaffected_by_a_backend_deploy_between_two_starts(world):
    run(["bash", str(VPS / "run_bot_service.sh"), "up"], world)
    (world["base"] / "CURRENT_SHA").write_text(HEX[3] + "\n")
    (world["base"] / "CURRENT_MECHANISM_SHA").write_text(HEX[3] + "\n")
    world["log"].unlink()
    run(["bash", str(VPS / "run_bot_service.sh"), "up"], world)
    assert [l for l in calls(world) if l.startswith("ENV:")][-1].count(f"BOT={HEX[1]}") == 1


# ---------------------------------------------------------------------------------------------------------------- blast_radius_guard.sh
def guard(world, before_lines, after_lines):
    """Run snapshot -> (world changes) -> assert, using the stub docker's `ps` and `inspect`."""
    tmp = world["tmp"]

    def publish(lines):
        world["containers"].write_text("\n".join(n for n, _ in lines) + "\n")
        for n, data in lines:
            (tmp / f"inspect_{n}").write_text(data + "\n")
    snap = tmp / "snap"
    publish(before_lines)
    first = run(["bash", "-c", f". {VPS}/blast_radius_guard.sh; guard_snapshot {snap}"], world)
    assert first.returncode == 0, first.stderr
    publish(after_lines)
    return run(["bash", "-c", f". {VPS}/blast_radius_guard.sh; guard_assert_unchanged {snap}"], world)


BASE_SET = [("donchian-screener-backend-1", "/b sha256:old t1 t1"), ("donchian-screener-bot-1", "/bot sha256:bot t1 t1"),
            ("donchian-screener-postgres-1", "/postgres sha256:pg t1 t1"), ("donchian-screener-reverse-proxy-1", "/rp sha256:rp t1 t1")]


@linux_only
def test_the_guard_passes_when_only_the_backend_and_proxy_change(world):
    after = [(n, d.replace("old", "new").replace("sha256:rp t1 t1", "sha256:rp t2 t2")) if "backend" in n or "proxy" in n else (n, d) for n, d in BASE_SET]
    assert guard(world, BASE_SET, after).returncode == 0


@linux_only
@pytest.mark.parametrize("who, change", [("bot", ("sha256:bot", "sha256:NEWBOT")), ("bot", ("t1 t1", "t2 t2")), ("postgres", ("sha256:pg", "sha256:NEWPG"))])
def test_the_guard_trips_when_the_bot_or_postgres_changes(world, who, change):
    after = [(n, d.replace(*change)) if who in n else (n, d) for n, d in BASE_SET]
    r = guard(world, BASE_SET, after)
    assert r.returncode == 1 and "BLAST-RADIUS GUARD" in r.stderr and who in r.stderr


@linux_only
def test_the_guard_trips_when_a_container_appears_or_disappears(world):
    assert guard(world, BASE_SET, BASE_SET + [("donchian-screener-pipeline-run-1", "/p sha256:x t t")]).returncode == 1
    assert guard(world, BASE_SET, [x for x in BASE_SET if "bot" not in x[0]]).returncode == 1


# ---------------------------------------------------------------------------------------------------------------- static: overlay, deploy.sh, unit
class _ComposeLoader(yaml.SafeLoader):
    """Compose's own tags (!override, !reset) carry merge semantics only; for these static checks read through them."""


def _through_tag(loader, suffix, node):
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


_ComposeLoader.add_multi_constructor("!", _through_tag)


def services():
    return yaml.load(text(OVERLAY), Loader=_ComposeLoader)["services"]


def test_the_bot_has_a_strict_variable_of_its_own_with_no_legacy_fallback():
    image = services()["bot"]["image"]
    assert "${BOT_IMAGE_TAG:-bot-tag-not-set}" in image and "IMAGE_TAG:" not in image.replace("BOT_IMAGE_TAG", "")


def test_every_other_service_uses_its_own_variable_first_and_nothing_uses_the_bare_shared_one_alone():
    s = services()
    assert "${BACKEND_IMAGE_TAG:-${IMAGE_TAG:-" in s["backend"]["image"]
    assert "${FRONTEND_IMAGE_TAG:-${IMAGE_TAG:-" in s["frontend"]["image"]
    for name in ("pipeline", "channel-sender"):
        assert "${MECHANISM_IMAGE_TAG:-${IMAGE_TAG:-" in s[name]["image"]
    for name, svc in s.items():
        if "image" in svc:
            assert not re.search(r"\$\{IMAGE_TAG(?::\?|\})", svc["image"]), f"{name} still depends on the bare shared IMAGE_TAG"


def test_a_missing_variable_resolves_to_an_invalid_tag_not_a_valid_one():
    for svc in services().values():
        if "image" in svc:
            assert re.search(r"-tag-not-set\}", svc["image"])


def test_the_overlay_proposal_changes_nothing_but_the_five_image_lines_and_the_header():
    old = text(REPO / "docker-compose.prod.yml").splitlines()
    proposed = text(OVERLAY).splitlines()
    body = proposed[proposed.index(old[0]):]                                       # drop only the header this proposal prepends
    assert len(body) == len(old)
    changed = [(a, b) for a, b in zip(old, body) if a != b]
    assert len(changed) == 5 and all("image:" in a and "image:" in b for a, b in changed)


def test_the_deploy_proposal_adds_only_the_libraries_and_the_guard_and_still_deploys_only_the_backend():
    old, new = text(VPS / "deploy.sh"), text(VPS / "deploy.sh.proposed")
    assert "compose_env.sh" in new and "blast_radius_guard.sh" in new and "guard_snapshot" in new and "guard_assert_unchanged" in new
    assert 'if [ "$SERVICE" != "backend" ]' in new and "up -d --no-deps backend" in new
    code = "\n".join(l for l in new.splitlines() if not l.lstrip().startswith("#"))
    assert not re.search(r"(CURRENT_BOT_SHA|CURRENT_MECHANISM_SHA|FORWARD_COLLECTION_ARMED)", code), "a backend deploy must never touch the bot, mechanism or arming pins"
    assert 'IMAGE_TAG="$T" docker compose' not in new                              # the shared variable is gone from the compose call
    assert 'echo "$TAG" > "$BASE/CURRENT_SHA"' in new and 'echo "$TAG" > "$BASE/CURRENT_SHA"' in old


def test_the_guard_failure_path_does_not_roll_back_by_itself():
    new = text(VPS / "deploy.sh.proposed")
    block = new[new.index("guard_assert_unchanged"):]
    line = block.splitlines()[0]
    assert "exit 3" in line and "rollout" not in line and "NOT rolling back" in line


def test_the_unit_proposal_uses_the_wrapper_and_the_retired_env_file_is_gone():
    unit = text(VPS / "donchian-bot.service.proposed")
    code = "\n".join(l for l in unit.splitlines() if not l.startswith("#"))
    assert "ExecStart=/opt/donchian/scripts/run_bot_service.sh up" in code and "ExecStop=/opt/donchian/scripts/run_bot_service.sh stop" in code
    assert "EnvironmentFile" not in code and "mechanism_image_tag" not in code
    wrapper = "\n".join(l for l in text(VPS / "run_bot_service.sh").splitlines() if not l.lstrip().startswith("#"))
    assert "CURRENT_MECHANISM_SHA" not in wrapper and "CURRENT_BOT_SHA" not in wrapper                 # the wrapper only goes through compose_env.sh; the old, unsafe pin source is gone


def test_the_proposed_libraries_are_never_referenced_by_an_installed_unit_or_by_cd():
    for f in list(VPS.glob("*.service")) + [REPO / ".github" / "workflows" / "cd.yml"]:
        t = text(f)
        assert "compose_env.sh" not in t and "blast_radius_guard.sh" not in t, f.name
