"""The owner dataset CLI, driven end to end through its real `main(argv)` and real database connection against a throwaway Postgres schema.

Covers: authoring a manifest from the database, validation, the build + artifacts + printed identities, the read-only default, the explicit
registry opt-in, every fail-closed path, and the four reproducibility acceptance tests (A: same DB twice, B: a second independent database,
C: rows after the cutoff, D: a pre-cutoff source mutation)."""
import copy
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import psycopg2
import pytest

from cli_world import CODE, PW_VAR, Cli, read_artifacts, spec_doc, write_spec
from conftest import _connect_args
from dataset_world import CAL, CUTOFF, SHA, _make_env, denv, fresh_denv  # noqa: F401
from research.lab import dataset_authoring as AU
from research.lab import dataset_cli as CLI
from research.lab import dataset_readiness as RY
from research.lab import dataset_report as RP
from research.lab import dataset_runner as R
from test_dataset_repro_db import MUTATIONS, mutate

REGISTRY = ("dataset_manifest", "experiment_registration", "experiment_result")
ROOT = Path(__file__).resolve().parents[3]


def counts(env):
    env.conn.commit()
    cur = env.conn.cursor()
    out = {}
    for t in REGISTRY:
        cur.execute(f'SELECT count(*) FROM "{t}"')
        out[t] = cur.fetchone()[0]
    env.conn.commit()
    return out


def hashes_of(out_dir):
    return json.loads((Path(out_dir) / "identities.json").read_text(encoding="utf-8"))


def authored(cli, tmp_path, name="manifest.json", **over):
    spec = write_spec(tmp_path / f"spec_{name}", **over)
    out = tmp_path / name
    rc, so, se = cli.run("author", "--spec", str(spec), "--out", str(out))
    assert rc == 0, (so, se)
    return out, spec


@pytest.fixture(scope="module")
def world(denv, tmp_path_factory):
    """The shared, read-only seeded world plus a manifest the CLI authored from it."""
    with pytest.MonkeyPatch.context() as mp:
        cli = Cli(mp, schema=denv.schema)
        tmp = tmp_path_factory.mktemp("cli")
        manifest, spec = authored(cli, tmp)
    return SimpleNamespace(env=denv, manifest=manifest, spec=spec, tmp=tmp)


@pytest.fixture
def cli(monkeypatch, world):
    return Cli(monkeypatch, schema=world.env.schema)


# ------------------------------------------------------------------ author
def test_author_writes_a_valid_manifest_file_and_records_nothing(world, cli, tmp_path):
    before = counts(world.env)
    out = tmp_path / "m.json"
    rc, so, se = cli.run("author", "--spec", str(world.spec), "--out", str(out))
    assert rc == 0 and se == ""
    manifest, authored_at = AU.parse_manifest_file(json.loads(out.read_text(encoding="utf-8")))
    assert f"manifest_hash  {manifest.manifest_hash}" in so and "not registered; nothing was written to the database" in so
    assert manifest.document["code_sha"] == SHA and manifest.document["config"]["universe_members"] == ["A", "B", "C"]
    assert manifest.document["calendar_source"] == "explicit_calendar_file" and list(manifest.calendar) == list(CAL)
    assert counts(world.env) == before


def test_author_takes_every_input_hash_from_the_shared_library_not_from_a_parallel_implementation(world):
    lib = world.env.manifest.document["input_hashes"]
    got = AU.parse_manifest_file(json.loads(world.manifest.read_text(encoding="utf-8")))[0].document["input_hashes"]
    assert got == lib, "the CLI must author exactly the input hashes the Slice 4 library authors for the same database"


def test_author_refuses_to_overwrite_an_existing_manifest(world, cli):
    before = world.manifest.read_bytes()
    rc, so, se = cli.run("author", "--spec", str(world.spec), "--out", str(world.manifest))
    assert rc == 2 and "already exists" in se and world.manifest.read_bytes() == before


def test_author_refuses_a_dirty_working_tree(world, cli, tmp_path):
    rc, so, se = cli.run("author", "--spec", str(world.spec), "--out", str(tmp_path / "m.json"), code=R.CodeIdentity(SHA, False))
    assert rc == 1 and "not clean" in se and not (tmp_path / "m.json").exists()


def test_author_fails_closed_when_the_code_identity_cannot_be_determined(world, cli, tmp_path, monkeypatch):
    def boom(_root):
        raise FileNotFoundError("git")
    out, err = [], []
    rc = CLI.main(["author", "--spec", str(world.spec), "--out", str(tmp_path / "m.json"), *cli.argv_db], code_provider=boom,
                  out=out.append, err=err.append)
    assert rc == 1 and "cannot determine the code identity" in "".join(err) and not (tmp_path / "m.json").exists()


@pytest.mark.parametrize("over,needle", [
    ({"surprise": 1}, "unknown key 'surprise'"),
    ({"code_sha": SHA}, "unknown key 'code_sha'"),
    ({"knowledge_cutoff_at": "2024-01-01T00:00:00"}, "UTC offset"),
])
def test_author_refuses_a_bad_spec_with_every_problem_listed(world, cli, tmp_path, over, needle):
    spec = write_spec(tmp_path / "s", **over)
    rc, so, se = cli.run("author", "--spec", str(spec), "--out", str(tmp_path / "m.json"))
    assert rc == 1 and "FAILED CLOSED" in se and needle in se


def test_author_refuses_a_cutoff_in_the_future(world, cli, tmp_path):
    spec = write_spec(tmp_path / "s", knowledge_cutoff_at="2999-01-01T00:00:00+00:00")
    rc, so, se = cli.run("author", "--spec", str(spec), "--out", str(tmp_path / "m.json"))
    assert rc == 1 and "FAILED CLOSED" in se and not (tmp_path / "m.json").exists()


def test_author_usage_errors_for_missing_files(world, cli, tmp_path):
    rc, _, se = cli.run("author", "--spec", str(tmp_path / "nope.json"), "--out", str(tmp_path / "m.json"))
    assert rc == 2 and "not found" in se
    spec = write_spec(tmp_path / "s")
    (tmp_path / "s" / "calendar.txt").unlink()
    rc, _, se = cli.run("author", "--spec", str(spec), "--out", str(tmp_path / "m.json"))
    assert rc == 2 and "calendar file not found" in se


def test_author_refuses_an_empty_observed_universe(world, cli, tmp_path):
    cfg = spec_doc()["config"]
    cfg["strategy"] = {"key": "no_such_strategy", "version": "v9"}
    rc, _, se = cli.run("author", "--spec", str(write_spec(tmp_path / "s", config=cfg)), "--out", str(tmp_path / "m.json"))
    assert rc == 1 and "universe would be empty" in se


def test_author_fails_closed_when_the_calendar_cannot_be_derived_from_price_bars(world, cli, tmp_path):
    spec = write_spec(tmp_path / "s", calendar={"derive": {"start": CAL[0].isoformat(), "end": CAL[50].isoformat()}})
    rc, _, se = cli.run("author", "--spec", str(spec), "--out", str(tmp_path / "m.json"))
    assert rc == 1 and "FAILED CLOSED" in se and "cannot derive the trading calendar" in se and not (tmp_path / "m.json").exists()      # no price bars: nothing is guessed


def test_a_manifest_file_is_refused_if_it_was_edited(world, cli, tmp_path):
    doc = json.loads(world.manifest.read_text(encoding="utf-8"))
    doc["manifest"]["code_sha"] = "e" * 40
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    for cmd in ("validate", "build"):
        extra = ["--out-dir", str(tmp_path / "o")] if cmd == "build" else []
        rc, so, se = cli.run(cmd, "--manifest", str(bad), *extra)
        assert rc == 1 and "FAILED CLOSED" in se, cmd
    bad.write_text("{not json", encoding="utf-8")
    assert cli.run("validate", "--manifest", str(bad))[0] == 1
    assert cli.run("validate", "--manifest", str(tmp_path / "missing.json"))[0] == 2


# ------------------------------------------------------------------ validate
def test_validate_checks_every_input_against_the_database(world, cli):
    rc, so, se = cli.run("validate", "--manifest", str(world.manifest))
    assert rc == 0 and "MANIFEST FILE VALID" in so and "VALIDATION OK" in so and "verified" in so and se == ""
    assert "db.forward_return_label" in so and "contract.dataset_schema" in so and "rows that arrived after the cutoff" in so


def test_validate_fails_closed_on_a_code_sha_mismatch_unless_drift_is_explicitly_allowed(world, cli):
    other = R.CodeIdentity("b" * 40, True)
    rc, so, se = cli.run("validate", "--manifest", str(world.manifest), code=other)
    assert rc == 1 and "FAILED CLOSED" in se and ("code" in se.lower())
    rc, so, se = cli.run("validate", "--manifest", str(world.manifest), "--allow-code-sha-drift", code=other)
    assert rc == 0 and "drift" in so.lower()


def test_validate_fails_closed_on_a_dirty_tree(world, cli):
    rc, so, se = cli.run("validate", "--manifest", str(world.manifest), code=R.CodeIdentity(SHA, False))
    assert rc == 1 and "FAILED CLOSED" in se


# ------------------------------------------------------------------ build: the happy path, artifacts, identities
def test_build_writes_the_artifacts_and_prints_every_identity(world, cli, tmp_path):
    out = tmp_path / "out"
    rc, so, se = cli.run("build", "--manifest", str(world.manifest), "--out-dir", str(out))
    assert rc == 0 and se == "", se
    art = read_artifacts(out)
    ident = hashes_of(out)
    for key in ("manifest_hash", "dataset_hash", "audit_hash", "report_hash", "readiness_hash", "inputs_hash"):
        assert len(ident[key]) == 64 and ident[key] in so, key
    assert ident["running_code_sha"] == SHA and ident["manifest_code_sha"] == SHA and f"running {SHA}" in so
    assert ident["predictive_edge_claim"] == "none" and ident["distinction"] == RY.DISTINCTION and RY.DISTINCTION in so
    assert hashlib.sha256(art["dataset.jsonl"]).hexdigest() == ident["dataset_hash"], "the dataset file hashes to the dataset_hash it is named by"
    report, readiness = json.loads(art["report.json"]), json.loads(art["readiness.json"])
    audit = json.loads(art["audit.json"])
    assert RP.verify_report_hash(report) and report["report_hash"] == ident["report_hash"]
    assert RY.verify_readiness_hash(readiness) and readiness["readiness_hash"] == ident["readiness_hash"]
    assert audit["verdict"] == "PASS"
    assert readiness["identity"]["dataset_hash"] == ident["dataset_hash"] and readiness["identity"]["audit_hash"] == ident["audit_hash"]
    assert art["report.txt"].decode() in so and "BUILD OK  (read-only; NOTHING was recorded in the database)" in so
    ctx = json.loads((out / CLI.CONTEXT_ARTIFACT).read_text(encoding="utf-8"))
    assert ctx["mode"] == "read_only" and ctx["registration"] is None and ctx["database"]["dbname"] == _connect_args()["dbname"]
    assert (out / "dataset.jsonl").stat().st_size > 0 and {p.name for p in out.iterdir()} == set(CLI.CANONICAL_ARTIFACTS) | {CLI.CONTEXT_ARTIFACT}


def test_quiet_omits_the_report_text_from_stdout_but_still_writes_it(world, cli, tmp_path):
    rc, so, se = cli.run("build", "--manifest", str(world.manifest), "--out-dir", str(tmp_path / "o"), "--quiet")
    assert rc == 0 and (tmp_path / "o" / "report.txt").read_text(encoding="utf-8") not in so and "DATASET READINESS" in so


def test_a_build_that_passes_the_audit_is_still_not_model_research_eligible(world, cli, tmp_path):
    out = tmp_path / "o"
    rc, so, se = cli.run("build", "--manifest", str(world.manifest), "--out-dir", str(out))
    r = json.loads((out / "readiness.json").read_text(encoding="utf-8"))
    assert rc == 0 and json.loads((out / "audit.json").read_text(encoding="utf-8"))["verdict"] == "PASS"
    assert r["eligibility"]["model_research_eligible"] is False and "MODEL RESEARCH ELIGIBLE: NO" in so
    failed = {c["id"] for c in r["eligibility"]["checks"] if not c["passed"]}
    assert {"observed_coverage_sufficient", "final_label_samples_sufficient"} <= failed       # the tiny seeded world is too small and too sparse
    assert r["eligibility"]["blocking_reasons"] and r["eligibility"]["predictive_edge_claim"] == "none"


def test_the_password_never_appears_in_any_output_or_artifact(world, cli, tmp_path):
    out = tmp_path / "o"
    rc, so, se = cli.run("build", "--manifest", str(world.manifest), "--out-dir", str(out))
    blob = so + se + "".join(p.read_text(encoding="utf-8", errors="replace") for p in out.iterdir())
    assert cli.password and cli.password not in blob


def test_require_eligible_exits_4_for_a_dataset_that_builds_but_is_not_eligible(world, cli, tmp_path):
    out = tmp_path / "o"
    rc, so, se = cli.run("build", "--manifest", str(world.manifest), "--out-dir", str(out), "--require-eligible")
    assert rc == 4 and "NOT ELIGIBLE and --require-eligible" in so and (out / "readiness.json").exists()


def test_build_never_overwrites_artifacts(world, cli, tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    (out / "keep.txt").write_text("x", encoding="utf-8")
    rc, so, se = cli.run("build", "--manifest", str(world.manifest), "--out-dir", str(out))
    assert rc == 2 and "not empty" in se and sorted(p.name for p in out.iterdir()) == ["keep.txt"]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.run("build", "--manifest", str(world.manifest), "--out-dir", str(empty))[0] == 0
    assert cli.run("build", "--manifest", str(world.manifest), "--out-dir", str(empty))[0] == 2


@pytest.mark.parametrize("flags,needle", [
    (["--register"], "--register needs --experiment-name"),
    (["--experiment-name", "x"], "only applies with --register"),
    (["--include-test"], "--include-test needs --register and --registration-hash"),
    (["--register", "--experiment-name", "x", "--include-test"], "--include-test needs --register and --registration-hash"),
    (["--include-test", "--registration-hash", "a" * 64], "--include-test needs --register"),
    (["--registration-hash", "a" * 64], "only applies with --include-test"),
    (["--register", "--experiment-name", "x", "--allow-code-sha-drift"], "cannot be combined with --allow-code-sha-drift"),
])
def test_inconsistent_flags_are_a_usage_error_and_touch_nothing(world, cli, tmp_path, flags, needle):
    before = counts(world.env)
    out = tmp_path / "o"
    rc, so, se = cli.run("build", "--manifest", str(world.manifest), "--out-dir", str(out), *flags)
    assert rc == 2 and needle in se and not out.exists() and counts(world.env) == before


def test_the_argument_parser_rejects_unknown_input_and_requires_a_database(world, cli, tmp_path):
    assert CLI.main(["build"], out=lambda s: None, err=lambda s: None) == 2
    assert CLI.main(["nonsense"], out=lambda s: None, err=lambda s: None) == 2
    assert CLI.main(["--help"], out=lambda s: None, err=lambda s: None) == 0
    out = tmp_path / "must_not_exist"
    for flag in ("--password", "--passw", "--regist", "--include"):
        rc, so, se = cli.run("build", "--manifest", str(world.manifest), "--out-dir", str(out), flag, "pw")
        assert rc == 2 and not out.exists(), f"{flag}: a password must not be accepted on argv, and flags are never abbreviated"


# ------------------------------------------------------------------ database failures
def test_a_missing_schema_is_an_exit_3_with_the_missing_tables_named(world, monkeypatch, tmp_path):
    admin = psycopg2.connect(**_connect_args())
    name = "empty_" + uuid.uuid4().hex[:8]
    try:
        admin.cursor().execute(f'CREATE SCHEMA "{name}"')
        admin.commit()
        bare = Cli(monkeypatch, schema=name)
        rc, so, se = bare.run("validate", "--manifest", str(world.manifest))
        assert rc == 3 and "database error" in se and "forward_return_label" in se
        rc, so, se = bare.run("build", "--manifest", str(world.manifest), "--out-dir", str(tmp_path / "o"))
        assert rc == 3 and not (tmp_path / "o" / "dataset.jsonl").exists()
    finally:
        admin.rollback()
        admin.cursor().execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        admin.commit()
        admin.close()


def test_an_unreachable_database_is_an_exit_3_through_the_real_module_entrypoint(world, tmp_path):
    env = {**__import__("os").environ, "PYTHONPATH": str(ROOT / "mechanism"), PW_VAR: "x"}
    p = subprocess.run([sys.executable, "-m", "research.lab.dataset_cli", "validate", "--manifest", str(world.manifest), "--host", "127.0.0.1",
                        "--port", "1", "--dbname", "d", "--user", "u", "--password-env", PW_VAR, "--connect-timeout", "2", "--repo-root", str(ROOT)],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert p.returncode in (1, 3), p.stderr            # 3 = cannot connect; 1 only if the code identity (a dirty checkout) is refused first
    assert "Traceback" not in p.stderr
    h = subprocess.run([sys.executable, "-m", "research.lab.dataset_cli", "--help"], capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert h.returncode == 0 and "author" in h.stdout and "build" in h.stdout


# ------------------------------------------------------------------ the read-only default
def test_the_default_connection_is_read_only_and_a_write_on_it_is_refused(world):
    a = SimpleNamespace(host=_connect_args()["host"], port=int(_connect_args()["port"]), dbname=_connect_args()["dbname"], user=_connect_args()["user"],
                        password_env=PW_VAR, connect_timeout=5)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(PW_VAR, _connect_args()["password"])
        mp.setenv("PGOPTIONS", f"-c search_path={world.env.schema}")
        conn = CLI.default_connect(a, readonly=True)
        try:
            cur = conn.cursor()
            cur.execute("SHOW application_name")
            assert cur.fetchone()[0] == "lab_dataset_cli"
            with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
                cur.execute("INSERT INTO dataset_manifest (manifest_hash) VALUES ('x')")
            conn.rollback()
            with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
                cur.execute("CREATE TABLE lab_cli_must_not_exist (x int)")
        finally:
            conn.close()


def test_validate_and_a_plain_build_open_only_read_only_connections_and_change_nothing(world, cli, tmp_path):
    modes = []

    def spy(a, *, readonly):
        modes.append(readonly)
        return CLI.default_connect(a, readonly=readonly)

    before = counts(world.env)
    assert cli.run("validate", "--manifest", str(world.manifest), connect=spy)[0] == 0
    assert cli.run("build", "--manifest", str(world.manifest), "--out-dir", str(tmp_path / "o"), connect=spy)[0] == 0
    assert modes and all(m is True for m in modes), modes
    assert counts(world.env) == before


def test_a_failed_build_leaves_the_registry_untouched_and_explains_itself(fresh_denv, monkeypatch, tmp_path):
    env = fresh_denv
    cli = Cli(monkeypatch, schema=env.schema)
    manifest, _ = authored(cli, tmp_path)
    mutate(env, *MUTATIONS[0][:2])
    before = counts(env)
    out = tmp_path / "o"
    rc, so, se = cli.run("build", "--manifest", str(manifest), "--out-dir", str(out), "--register", "--experiment-name", "should-not-record")
    assert rc == 1 and "input_hash_mismatch" in se and counts(env) == before
    failure = json.loads((out / "failure.json").read_text(encoding="utf-8"))
    assert failure["schema"] == "lab_dataset_failure_v1" and any("input_hash_mismatch" in p for p in failure["problems"])
    assert not (out / "dataset.jsonl").exists() and (out / "verification.json").exists() and "MISMATCH" in so


# ------------------------------------------------------------------ the registry is opt-in
def test_register_records_one_manifest_one_registration_and_one_count_only_result_and_is_idempotent(fresh_denv, monkeypatch, tmp_path):
    env = fresh_denv
    cli = Cli(monkeypatch, schema=env.schema)
    manifest, _ = authored(cli, tmp_path, dataset_version="v-cli")
    before = counts(env)
    rc, so, se = cli.run("build", "--manifest", str(manifest), "--out-dir", str(tmp_path / "a"), "--register", "--experiment-name", "owner-dry-run")
    assert rc == 0, (so, se)
    assert counts(env) == {"dataset_manifest": before["dataset_manifest"] + 1, "experiment_registration": before["experiment_registration"] + 1,
                           "experiment_result": before["experiment_result"] + 1}
    assert "REGISTRATION  validation" in so and "registered" in so
    ctx = json.loads((tmp_path / "a" / CLI.CONTEXT_ARTIFACT).read_text(encoding="utf-8"))
    assert ctx["mode"] == "registered" and ctx["registration"]["result_kind"] == "validation" and ctx["registration"]["manifest_row_created"] is True
    again = cli.run("build", "--manifest", str(manifest), "--out-dir", str(tmp_path / "b"), "--register", "--experiment-name", "owner-dry-run")
    assert again[0] == 0 and "validation_already_recorded" in again[1]
    assert counts(env)["experiment_result"] == before["experiment_result"] + 1 and counts(env)["dataset_manifest"] == before["dataset_manifest"] + 1
    assert read_artifacts(tmp_path / "a") == read_artifacts(tmp_path / "b"), "recording changes none of the canonical artifacts"
    cur = env.conn.cursor()
    cur.execute("SELECT result_kind, metrics FROM experiment_result ORDER BY id DESC LIMIT 1")
    kind, metrics = cur.fetchone()
    env.conn.commit()
    assert kind == "validation" and set(metrics) == set(R.DIAGNOSTIC_METRICS)


def test_register_with_require_eligible_records_nothing_when_the_dataset_is_not_eligible(fresh_denv, monkeypatch, tmp_path):
    env = fresh_denv
    cli = Cli(monkeypatch, schema=env.schema)
    manifest, _ = authored(cli, tmp_path, dataset_version="v-cli")
    before = counts(env)
    rc, so, se = cli.run("build", "--manifest", str(manifest), "--out-dir", str(tmp_path / "o"), "--register", "--experiment-name", "x", "--require-eligible")
    assert rc == 4 and "nothing was registered" in so and counts(env) == before
    ctx = json.loads((tmp_path / "o" / CLI.CONTEXT_ARTIFACT).read_text(encoding="utf-8"))
    assert ctx["mode"] == "read_only" and "refused" in ctx["registration"]


def test_the_test_split_is_revealed_only_through_a_registered_one_shot_experiment(fresh_denv, monkeypatch, tmp_path):
    env = fresh_denv
    cli = Cli(monkeypatch, schema=env.schema)
    manifest_path, _ = authored(cli, tmp_path, dataset_version="v-cli")
    manifest = AU.parse_manifest_file(json.loads(manifest_path.read_text(encoding="utf-8")))[0]
    good = R.diagnostic_registration(manifest, CODE, "final-look").registration_hash

    def reveal(tag, h):
        return cli.run("build", "--manifest", str(manifest_path), "--out-dir", str(tmp_path / tag), "--register", "--experiment-name", "final-look",
                       "--include-test", "--registration-hash", h)

    before = counts(env)
    never_validated = reveal("w0", good)                                     # no validation recorded yet: the test split stays sealed
    assert never_validated[0] == 1 and "needs a recorded validation result first" in never_validated[2] and counts(env) == before
    assert not (tmp_path / "w0" / "dataset.jsonl").exists()
    assert cli.run("build", "--manifest", str(manifest_path), "--out-dir", str(tmp_path / "v"), "--register", "--experiment-name", "final-look")[0] == 0
    mid = counts(env)
    wrong = reveal("w1", "f" * 64)
    assert wrong[0] == 1 and "FAILED CLOSED" in wrong[2] and counts(env) == mid and not (tmp_path / "w1" / "dataset.jsonl").exists()
    ok = reveal("t", good)
    assert ok[0] == 0, ok
    assert "REGISTRATION  test" in ok[1]
    readiness = json.loads((tmp_path / "t" / "readiness.json").read_text(encoding="utf-8"))
    checks = {c["id"]: c["passed"] for c in readiness["eligibility"]["checks"]}
    assert checks["test_split_unevaluated"] is False and readiness["eligibility"]["model_research_eligible"] is False
    assert counts(env)["experiment_result"] == mid["experiment_result"] + 1

    def tests_recorded():
        cur = env.conn.cursor()
        cur.execute("SELECT count(*) FROM experiment_result WHERE result_kind = 'test'")
        n = cur.fetchone()[0]
        env.conn.commit()
        return n

    assert tests_recorded() == 1
    second = reveal("t2", good)
    assert second[0] in (1, 3), second                       # the test split is one-shot: the registry refuses a second look
    assert not (tmp_path / "t2" / "dataset.jsonl").exists() and tests_recorded() == 1


def test_registering_a_different_manifest_under_an_existing_name_and_version_fails_closed(fresh_denv, monkeypatch, tmp_path):
    env = fresh_denv
    cli = Cli(monkeypatch, schema=env.schema)
    clash, _ = authored(cli, tmp_path, "clash.json")                     # the spec's name and version are the world's own registered ones
    m_clash = AU.parse_manifest_file(json.loads(clash.read_text(encoding="utf-8")))[0]
    assert (m_clash.document["dataset_name"], m_clash.document["dataset_version"]) == (env.manifest.document["dataset_name"],
                                                                                         env.manifest.document["dataset_version"])
    assert m_clash.manifest_hash != env.manifest.manifest_hash
    before = counts(env)
    rc, so, se = cli.run("build", "--manifest", str(clash), "--out-dir", str(tmp_path / "o"), "--register", "--experiment-name", "x")
    assert rc == 3 and "dataset_manifest_name_version" in se and counts(env) == before


def test_include_test_without_registering_can_never_reveal_the_test_split(world, cli, tmp_path):
    rc, so, se = cli.run("build", "--manifest", str(world.manifest), "--out-dir", str(tmp_path / "o"), "--include-test")
    assert rc == 2 and not (tmp_path / "o").exists()


# ------------------------------------------------------------------ reproducibility acceptance tests
def build_to(cli, manifest, out):
    rc, so, se = cli.run("build", "--manifest", str(manifest), "--out-dir", str(out), "--quiet")
    assert rc == 0, (so, se)
    return so


def test_A_the_same_build_twice_on_an_unchanged_database_is_byte_identical(world, cli, tmp_path):
    before = counts(world.env)
    so1 = build_to(cli, world.manifest, tmp_path / "one")
    so2 = build_to(cli, world.manifest, tmp_path / "two")
    one, two = read_artifacts(tmp_path / "one"), read_artifacts(tmp_path / "two")
    assert one == two and len(one) == len(CLI.CANONICAL_ARTIFACTS)
    i1, i2 = hashes_of(tmp_path / "one"), hashes_of(tmp_path / "two")
    for key in ("manifest_hash", "dataset_hash", "audit_hash", "report_hash", "readiness_hash"):
        assert i1[key] == i2[key] and len(i1[key]) == 64, key
    assert so1.replace(str(tmp_path / "one"), "OUT") == so2.replace(str(tmp_path / "two"), "OUT"), "the human-readable output is reproducible too"
    assert (tmp_path / "one" / CLI.CONTEXT_ARTIFACT).read_bytes() == (tmp_path / "two" / CLI.CONTEXT_ARTIFACT).read_bytes()
    assert counts(world.env) == before


@pytest.fixture
def second_world(monkeypatch):
    """A second, independent disposable database seeded with the same world. A separate DATABASE when the role may create one; otherwise a
    separate SCHEMA of the same cluster (the mode used is reported by the fixture and asserted by the test)."""
    args = _connect_args()
    origdb = args["dbname"]
    name, mode, admin = "lab_cli_b_" + uuid.uuid4().hex[:8], "database", None
    try:
        admin = psycopg2.connect(**args)
        admin.autocommit = True
        admin.cursor().execute(f'CREATE DATABASE "{name}"')
        monkeypatch.setenv("DB_NAME", name)
    except psycopg2.Error:
        mode, name = "schema", args["dbname"]
    gen = _make_env()
    try:
        env = next(gen)
        yield SimpleNamespace(env=env, dbname=name, mode=mode, origdb=origdb)
    finally:
        for _ in gen:
            pass
        if mode == "database":
            try:
                admin.cursor().execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            except psycopg2.Error:
                admin.cursor().execute(f'DROP DATABASE IF EXISTS "{name}"')
        if admin is not None:
            admin.close()


def test_B_an_independent_database_seeded_with_the_same_world_gives_the_same_identities_and_artifacts(world, second_world, monkeypatch, tmp_path):
    assert second_world.env.schema != world.env.schema
    cli_a = Cli(monkeypatch, schema=world.env.schema, dbname=second_world.origdb)
    cli_b = Cli(monkeypatch, schema=second_world.env.schema, dbname=second_world.dbname)
    assert (cli_a.dbname, cli_a._schema) != (cli_b.dbname, cli_b._schema)
    # authored independently on B: same inputs -> same manifest identity
    manifest_b, _ = authored(cli_b, tmp_path, "manifest_b.json")
    ma = AU.parse_manifest_file(json.loads(world.manifest.read_text(encoding="utf-8")))[0]
    mb = AU.parse_manifest_file(json.loads(manifest_b.read_text(encoding="utf-8")))[0]
    assert ma.manifest_hash == mb.manifest_hash and ma.document["input_hashes"] == mb.document["input_hashes"]
    # A's manifest validates against B, and the build is byte-identical to A's
    assert cli_b.run("validate", "--manifest", str(world.manifest))[0] == 0
    build_to(cli_a, world.manifest, tmp_path / "a")
    build_to(cli_b, world.manifest, tmp_path / "b")
    build_to(cli_b, manifest_b, tmp_path / "b2")
    assert read_artifacts(tmp_path / "a") == read_artifacts(tmp_path / "b") == read_artifacts(tmp_path / "b2")
    assert second_world.mode in ("database", "schema")
    ctx_a = json.loads((tmp_path / "a" / CLI.CONTEXT_ARTIFACT).read_text(encoding="utf-8"))
    ctx_b = json.loads((tmp_path / "b" / CLI.CONTEXT_ARTIFACT).read_text(encoding="utf-8"))
    assert ctx_a["database"]["dbname"] == second_world.origdb and ctx_b["database"]["dbname"] == second_world.dbname
    assert (second_world.dbname != second_world.origdb) == (second_world.mode == "database")


def test_C_rows_that_arrive_after_the_cutoff_do_not_change_the_result(fresh_denv, monkeypatch, tmp_path):
    env = fresh_denv
    cli = Cli(monkeypatch, schema=env.schema)
    manifest, _ = authored(cli, tmp_path)
    build_to(cli, manifest, tmp_path / "before")
    late = CUTOFF + timedelta(days=1)
    w = env.world
    oid = w.candidate("A", 301, captured_at=late, with_labels=False)
    for h in (5, 20, 60):
        w.label("A", 301, h, oid, 1, computed_at=late)
    w.market(301, captured_at=late)
    env.conn.commit()
    assert cli.run("validate", "--manifest", str(manifest))[0] == 0, "late rows do not break verification"
    build_to(cli, manifest, tmp_path / "after")
    assert read_artifacts(tmp_path / "before") == read_artifacts(tmp_path / "after")
    c0 = json.loads((tmp_path / "before" / CLI.CONTEXT_ARTIFACT).read_text(encoding="utf-8"))["post_cutoff_rows_ignored"]
    c1 = json.loads((tmp_path / "after" / CLI.CONTEXT_ARTIFACT).read_text(encoding="utf-8"))["post_cutoff_rows_ignored"]
    assert c1["candidates"] == c0["candidates"] + 1 and c1["labels"] == c0["labels"] + 3 and c1["market"] == c0["market"] + 1
    assert "post_cutoff" not in "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "after").iterdir() if p.name != CLI.CONTEXT_ARTIFACT)


@pytest.mark.parametrize("table,sql,key", MUTATIONS, ids=[f"{m[0]}-{i}" for i, m in enumerate(MUTATIONS)])
def test_D_a_pre_cutoff_source_mutation_fails_validation_and_build_closed(fresh_denv, monkeypatch, tmp_path, table, sql, key):
    env = fresh_denv
    cli = Cli(monkeypatch, schema=env.schema)
    manifest, _ = authored(cli, tmp_path)
    assert cli.run("validate", "--manifest", str(manifest))[0] == 0
    mutate(env, table, sql)
    rc, so, se = cli.run("validate", "--manifest", str(manifest))
    assert rc == 1 and "VERIFICATION FAILED" in so and "MISMATCH" in so and key in so
    rc, so, se = cli.run("build", "--manifest", str(manifest), "--out-dir", str(tmp_path / "o"))
    assert rc == 1 and "input_hash_mismatch" in se and not (tmp_path / "o" / "dataset.jsonl").exists()


def test_D_after_legitimate_re_authoring_the_changed_history_gives_a_different_dataset_identity(fresh_denv, monkeypatch, tmp_path):
    env = fresh_denv
    cli = Cli(monkeypatch, schema=env.schema)
    old, _ = authored(cli, tmp_path, "old.json")
    build_to(cli, old, tmp_path / "before")
    mutate(env, *MUTATIONS[0][:2])
    new, _ = authored(cli, tmp_path, "new.json", dataset_version="v-mutated")
    mo = AU.parse_manifest_file(json.loads(old.read_text(encoding="utf-8")))[0]
    mn = AU.parse_manifest_file(json.loads(new.read_text(encoding="utf-8")))[0]
    assert mn.manifest_hash != mo.manifest_hash
    assert mn.document["input_hashes"]["db.forward_return_label"] != mo.document["input_hashes"]["db.forward_return_label"]
    unchanged = {k for k in mo.document["input_hashes"] if mo.document["input_hashes"][k] == mn.document["input_hashes"][k]}
    assert "db.candidate_observation" in unchanged and "db.forward_return_label" not in unchanged
    assert cli.run("validate", "--manifest", str(new))[0] == 0
    build_to(cli, new, tmp_path / "after")
    b, a = hashes_of(tmp_path / "before"), hashes_of(tmp_path / "after")
    assert a["dataset_hash"] != b["dataset_hash"] and a["manifest_hash"] != b["manifest_hash"] and a["report_hash"] != b["report_hash"]
    assert (tmp_path / "before" / "dataset.jsonl").read_bytes() != (tmp_path / "after" / "dataset.jsonl").read_bytes()
    assert cli.run("validate", "--manifest", str(old))[0] == 1, "the old manifest still refuses to build on the rewritten history"


def test_the_first_manifest_is_unaffected_by_the_second_authoring_run_on_the_same_data(world, cli, tmp_path):
    again, _ = authored(cli, tmp_path, "again.json")
    a = AU.parse_manifest_file(json.loads(again.read_text(encoding="utf-8")))[0]
    b = AU.parse_manifest_file(json.loads(world.manifest.read_text(encoding="utf-8")))[0]
    assert a.manifest_hash == b.manifest_hash, "authoring is deterministic: the database clock is outside the manifest hash"


# ------------------------------------------------------------------ the real code-identity function
def test_current_code_identity_reads_the_commit_and_cleanliness_of_a_real_checkout(tmp_path):
    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@example.com", *a], check=True,
                              capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    (tmp_path / "f.txt").write_text("1", encoding="utf-8")
    git("add", "f.txt")
    git("commit", "-q", "-m", "c")
    c = R.current_code_identity(str(tmp_path))
    assert c.sha == git("rev-parse", "HEAD") and c.tree_clean is True
    (tmp_path / "f.txt").write_text("2", encoding="utf-8")
    assert R.current_code_identity(str(tmp_path)).tree_clean is False
    (tmp_path / "f.txt").write_text("1", encoding="utf-8")
    (tmp_path / "scratch.txt").write_text("untracked", encoding="utf-8")
    assert R.current_code_identity(str(tmp_path)).tree_clean is True, "untracked scratch must not block a run"
    with pytest.raises(subprocess.CalledProcessError):
        R.current_code_identity(str(tmp_path / "not_a_repo_dir_that_exists_nowhere"))
