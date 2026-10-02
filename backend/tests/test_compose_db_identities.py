"""P2 -- postgres bootstrap identity vs application runtime identities in docker-compose.yml.

Renders the real compose files with `docker compose config --format json` in a throwaway project directory (so the
developer's real .env is never read) and asserts the credential wiring. Skipped when docker compose is unavailable.
All credentials below are dummy values.
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APPS = {"backend": "BACKEND", "bot": "BOT", "pipeline": "PIPELINE", "channel-sender": "SENDER"}
SCRUB = ("DB_", "APP_DB_", "POSTGRES_BOOTSTRAP_", "BACKEND_DB_", "BOT_DB_", "PIPELINE_DB_", "SENDER_DB_", "IMAGE_TAG")


def _have_compose():
    try:
        return subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(not _have_compose(), reason="docker compose not available")


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    d = tmp_path_factory.mktemp("compose_proj")
    for f in ("docker-compose.yml", "docker-compose.prod.yml"):
        shutil.copy(os.path.join(ROOT, f), d / f)
    (d / "docker").mkdir()
    shutil.copy(os.path.join(ROOT, "docker", ".env.example"), d / "docker" / ".env.example")
    (d / ".env").write_text("")      # satisfies the prod overlay's env_file existence check; never populated
    return d


def render(project, tmp_path, values, prod):
    envf = tmp_path / "vars.env"
    envf.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    env = {k: v for k, v in os.environ.items() if not k.startswith(SCRUB)}
    env["IMAGE_TAG"] = "test-tag"
    files = ["-f", "docker-compose.yml"] + (["-f", "docker-compose.prod.yml"] if prod else [])
    r = subprocess.run(["docker", "compose", "--project-directory", str(project), "--profile", "*", *files, "--env-file", str(envf),
                        "config", "--format", "json"], capture_output=True, text=True, env=env, cwd=project, timeout=120)
    assert r.returncode == 0, r.stderr[-600:]
    return json.loads(r.stdout)["services"]


def ident(svcs):
    out = {n: (svcs[n]["environment"]["DB_USER"], svcs[n]["environment"]["DB_PASSWORD"]) for n in APPS}
    pg = svcs["postgres"]
    out["postgres"] = (pg["environment"]["POSTGRES_USER"], pg["environment"]["POSTGRES_PASSWORD"])
    out["pg_isready"] = pg["healthcheck"]["test"][1].split("-U ")[1].split()[0]
    return out


LEGACY = {"DB_NAME": "n", "DB_USER": "legacy_admin", "DB_PASSWORD": "legacy_pw"}


@pytest.mark.parametrize("prod", [False, True], ids=["base", "base+prod"])
class TestIdentities:
    def test_no_new_variables_renders_the_legacy_pair_everywhere(self, project, tmp_path, prod):
        got = ident(render(project, tmp_path, LEGACY, prod))
        for n in (*APPS, "postgres"):
            assert got[n] == ("legacy_admin", "legacy_pw"), n
        assert got["pg_isready"] == "legacy_admin"

    def test_legacy_pair_still_follows_when_new_variables_unset(self, project, tmp_path, prod):
        got = ident(render(project, tmp_path, {**LEGACY, "DB_USER": "other", "DB_PASSWORD": "pw2"}, prod))
        assert all(got[n] == ("other", "pw2") for n in (*APPS, "postgres"))

    def test_app_identity_switch_never_touches_postgres(self, project, tmp_path, prod):
        vals = {**LEGACY, "APP_DB_USER": "donchian_app", "APP_DB_PASSWORD": "app_pw"}
        got = ident(render(project, tmp_path, vals, prod))
        for n in APPS:
            assert got[n] == ("donchian_app", "app_pw"), n
        assert got["postgres"] == ("legacy_admin", "legacy_pw")
        assert got["pg_isready"] == "legacy_admin"

    def test_per_service_variables_beat_the_shared_app_pair(self, project, tmp_path, prod):
        vals = {**LEGACY, "APP_DB_USER": "donchian_app", "APP_DB_PASSWORD": "app_pw",
                "BACKEND_DB_USER": "be_ro", "BACKEND_DB_PASSWORD": "be_pw", "PIPELINE_DB_USER": "pl", "PIPELINE_DB_PASSWORD": "pl_pw"}
        got = ident(render(project, tmp_path, vals, prod))
        assert got["backend"] == ("be_ro", "be_pw") and got["pipeline"] == ("pl", "pl_pw")
        assert got["bot"] == got["channel-sender"] == ("donchian_app", "app_pw")
        assert got["postgres"] == ("legacy_admin", "legacy_pw")

    def test_single_service_can_move_alone(self, project, tmp_path, prod):
        got = ident(render(project, tmp_path, {**LEGACY, "SENDER_DB_USER": "s", "SENDER_DB_PASSWORD": "s_pw"}, prod))
        assert got["channel-sender"] == ("s", "s_pw")
        assert all(got[n] == ("legacy_admin", "legacy_pw") for n in ("backend", "bot", "pipeline", "postgres"))

    def test_explicit_bootstrap_variables_move_only_postgres(self, project, tmp_path, prod):
        vals = {**LEGACY, "POSTGRES_BOOTSTRAP_USER": "pg_boot", "POSTGRES_BOOTSTRAP_PASSWORD": "boot_pw"}
        got = ident(render(project, tmp_path, vals, prod))
        assert got["postgres"] == ("pg_boot", "boot_pw") and got["pg_isready"] == "pg_boot"
        assert all(got[n] == ("legacy_admin", "legacy_pw") for n in APPS)


def test_compose_carries_no_real_credentials():
    """The only literal password anywhere in the compose files is the documented throwaway local-dev default."""
    text = open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8").read()
    text += open(os.path.join(ROOT, "docker-compose.prod.yml"), encoding="utf-8").read()
    import re
    values = re.findall(r"PASSWORD:\s*(\S+)", text)
    assert values and all(v.startswith("${") for v in values), values     # never a bare literal
    literals = set(re.findall(r":-([A-Za-z0-9_]+)\}", " ".join(values)))
    assert literals == {"docker_local_dev_only_change_me"}, literals
