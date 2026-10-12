"""Owner-facing dataset CLI: author a manifest -> validate -> build -> audit -> report -> (optionally) register.

A thin orchestration layer. It owns no research semantics: manifest validation / hashing (`manifest`, `dataset_contract`), PIT semantics and
assembly (`dataset_assemble`), the leakage audit (`dataset_audit`), baselines, the report and the registry recording all live in the Slice 3/4
modules and are only *called* here. What this module adds is I/O: argv, a database connection, files, and printed output.

    PYTHONPATH=mechanism python -m research.lab.dataset_cli author   --spec spec.json --out manifest.json   <db>
    PYTHONPATH=mechanism python -m research.lab.dataset_cli validate --manifest manifest.json                <db>
    PYTHONPATH=mechanism python -m research.lab.dataset_cli build    --manifest manifest.json --out-dir out  <db>
        [--register --experiment-name NAME]  [--include-test --registration-hash H]  [--require-eligible]
    PYTHONPATH=mechanism python -m research.lab.dataset_cli status   --spec spec.json [--cutoff ISO|db-now] [--out-dir out] [--json]  <db>
        [--require-no-readiness-failures]
    where <db> is  --host H --dbname D --user U [--port P] [--password-env VAR]   (the password is read from the environment, never argv)

Read-only by default: the connection is `set_session(readonly=True)` and every read additionally runs inside the harness's read-only
transaction, so a write raises instead of happening. Only `--register` opens a SECOND, writable connection, and only to record the manifest,
the diagnostic registration and one count-only result through the existing registry.

`status` (Slice 6) is read-only end to end and writes nothing to the database, not even the registry: it reports how much genuinely observed,
point-in-time-safe history exists and what still keeps the Slice 5 contract from passing. It never states eligibility and claims no edge.

Exit codes: 0 ok | 1 validation / build / fingerprint / code / PIT / audit failure | 2 usage | 3 database connection or schema failure |
4 `--require-eligible` and the dataset is not model-research-eligible, or `status --require-no-readiness-failures` and a failure is listed.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import psycopg2

from research.lab import dataset_authoring as AUTH
from research.lab import dataset_contract as C
from research.lab import dataset_reader as RD
from research.lab import dataset_readiness as READY
from research.lab import dataset_runner as R
from research.lab import manifest as M
from research.lab import research_status as ST
from research.lab import research_status_reader as SR
from research.lab import registry_store as RS
from research.lab.manifest import LabError
from research.labels.fwd_v1 import CalendarError
from research.labels.sessions import derive_sessions

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_DB, EXIT_NOT_ELIGIBLE = 0, 1, 2, 3, 4
IDENTITIES_SCHEMA = "lab_dataset_run_identities_v1"
CONTEXT_SCHEMA = "lab_dataset_run_context_v1"
REPO_ROOT = Path(__file__).resolve().parents[3]

# every file `build` writes whose bytes are a pure function of (manifest, database content at the cutoff, code): the reproducibility set
CANONICAL_ARTIFACTS = ("dataset.jsonl", "audit.json", "audit.txt", "verification.json", "report.json", "report.txt", "readiness.json",
                       "readiness.txt", "identities.json")
STATUS_ARTIFACTS = ("status.json", "status.txt")        # canonical: a pure function of (spec, cutoff, database content at the cutoff)
STATUS_CONTEXT_ARTIFACT = "status_context.json"          # volatile: database target, database time, running code, rows that arrived after the cutoff
STATUS_CONTEXT_SCHEMA = "lab_research_status_context_v1"
CONTEXT_ARTIFACT = "run_context.json"      # run metadata (rows that arrived after the cutoff, registry ids): deliberately outside the set above

_SOURCE_TABLES = {"candidates": ("candidate_observation", "feature_snapshot", "strategies"), "labels": ("forward_return_label",),
                  "market": ("market_snapshot",), "sector": ("sector_snapshot",), "stock_rs": ("stock_relative_strength",),
                  "events": ("market_event", "market_event_revision"), "classifications": ("catalyst_classification",),
                  "first_seen": ("source_observation",), "sector_history": ("sector_observation", "sector_poll")}
_REGISTRY_TABLES = ("dataset_manifest", "experiment_registration", "experiment_result")
_CALENDAR_TABLES = ("stock_prices", "market_index_prices")

CodeProvider = Callable[[str], R.CodeIdentity]
Connect = Callable[..., Any]


class UsageError(Exception):
    pass


# ------------------------------------------------------------------ database
def _db_target(a: argparse.Namespace) -> Dict[str, Any]:
    return {"host": a.host, "port": a.port, "dbname": a.dbname, "user": a.user}


def default_connect(a: argparse.Namespace, *, readonly: bool):
    """A fresh connection to exactly the database named on the command line. The password comes from the environment only."""
    conn = psycopg2.connect(host=a.host, port=a.port, dbname=a.dbname, user=a.user, password=os.environ.get(a.password_env) or None,
                            connect_timeout=a.connect_timeout, application_name="lab_dataset_cli")
    if readonly:
        conn.set_session(readonly=True)
    return conn


def _preflight(conn, tables: Sequence[str]) -> None:
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = ANY (current_schemas(false)) AND table_name = ANY (%s)",
                (sorted(set(tables)),))
    have = {r[0] for r in cur.fetchall()}
    conn.rollback()
    missing = sorted(set(tables) - have)
    if missing:
        raise psycopg2.ProgrammingError("the database lacks the tables this dataset needs (migrations 26-30 applied?): " + ", ".join(missing))


def _tables_for(cfg: C.DatasetConfig, *, registry: bool = False) -> List[str]:
    out: List[str] = []
    for s in C.enabled_db_sources(cfg):
        out += _SOURCE_TABLES[s]
    return out + (list(_REGISTRY_TABLES) if registry else [])


# ------------------------------------------------------------------ small I/O helpers
def _read_json(path: str, what: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UsageError(f"{what} file not found: {path}")
    except ValueError as e:
        raise LabError([f"{what} {path} is not valid JSON: {e}"])


def _write_new(path: Path, data: str | bytes) -> None:
    mode = "xb" if isinstance(data, bytes) else "x"
    with open(path, mode, **({} if isinstance(data, bytes) else {"encoding": "utf-8", "newline": "\n"})) as f:
        f.write(data)


def _emit(out: Callable[[str], None], text: str) -> None:
    out(text if text.endswith("\n") else text + "\n")


def _code(code_provider: CodeProvider, repo_root: str) -> R.CodeIdentity:
    try:
        return code_provider(repo_root)
    except Exception as e:  # noqa: BLE001 -- git missing / not a repository / CalledProcessError: fail closed, never proceed without an identity
        raise LabError([f"cannot determine the code identity ({type(e).__name__}): a dataset must be reproducible from a commit"])


def _file_calendar(authoring: AUTH.Authoring, spec_path: Path) -> Optional[List[Any]]:
    if not authoring.calendar_file:
        return None
    cal_path = Path(authoring.calendar_file)
    cal_path = cal_path if cal_path.is_absolute() else spec_path.resolve().parent / cal_path
    try:
        return AUTH.parse_calendar_text(cal_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise UsageError(f"calendar file not found: {cal_path}")


def _resolve_calendar(authoring: AUTH.Authoring, file_cal, conn):
    """(sessions, source): the explicit calendar file, else derived from the database. Call inside a read-only session."""
    if file_cal is not None:
        return file_cal, "explicit_calendar_file"
    lo, hi, bench = authoring.calendar_derive
    try:
        return derive_sessions(conn, lo, hi, bench)
    except CalendarError as e:
        raise LabError([f"cannot derive the trading calendar from the database: {e}"])


# ------------------------------------------------------------------ author
def cmd_author(a: argparse.Namespace, connect: Connect, code_provider: CodeProvider, out: Callable[[str], None]) -> int:
    spec_path = Path(a.spec)
    authoring = AUTH.parse_authoring(_read_json(a.spec, "authoring spec"))
    out_path = Path(a.out)
    if out_path.exists():
        raise UsageError(f"{out_path} already exists: author never overwrites a manifest")
    code = _code(code_provider, a.repo_root)
    if not code.tree_clean:
        raise LabError(["the working tree is not clean: a manifest records the code it was authored with, and that must be a commit"])
    file_cal = _file_calendar(authoring, spec_path)

    conn = connect(a, readonly=True)
    try:
        _preflight(conn, list(_SOURCE_TABLES["candidates"]) + (list(_CALENDAR_TABLES) if authoring.calendar_derive else []))
        with RD.read_only_session(conn):
            cur = conn.cursor()
            now = RS.db_now(cur)
            calendar, cal_source = _resolve_calendar(authoring, file_cal, conn)
            members = None
            if authoring.universe_from_db:
                strat = authoring.config.get("strategy")
                if not (isinstance(strat, dict) and strat.get("key") and strat.get("version")):
                    raise LabError(["config.strategy {key, version} is needed to read the observed candidate universe"])
                members = RD.read_candidate_symbols(conn.cursor(), strat["key"], strat["version"], calendar[0], calendar[-1],
                                                    authoring.knowledge_cutoff_at)
        kw = dict(code_sha=code.sha, code_tree_clean=code.tree_clean, calendar=calendar, calendar_source=cal_source, universe_members=members)
        provisional = M.build_manifest(AUTH.to_spec(authoring, input_hashes=AUTH.PROVISIONAL_INPUT_HASHES, **kw), now=now)
        _preflight(conn, _tables_for(C.config_for(provisional.document)))
        hashes = R.author_input_hashes(conn, provisional, C.config_for(provisional.document))
        manifest = M.build_manifest(AUTH.to_spec(authoring, input_hashes=hashes, **kw), now=now)
    finally:
        conn.close()

    text = AUTH.dump_json(AUTH.make_manifest_file(manifest, now))
    reread, _ = AUTH.parse_manifest_file(json.loads(text))        # the file must validate before it is offered
    if reread.manifest_hash != manifest.manifest_hash:
        raise LabError(["internal: the authored manifest file does not round-trip to the same manifest hash"])
    _write_new(out_path, text)
    doc = manifest.document
    _emit(out, "MANIFEST AUTHORED (not registered; nothing was written to the database)")
    _emit(out, f"  file           {out_path}")
    _emit(out, f"  manifest_hash  {manifest.manifest_hash}")
    _emit(out, f"  dataset        {doc['dataset_name']} {doc['dataset_version']}   cutoff {doc['knowledge_cutoff_at']}")
    _emit(out, f"  code_sha       {code.sha}  (clean tree)")
    _emit(out, f"  calendar       {len(calendar)} sessions {doc['calendar_span'][0]}..{doc['calendar_span'][1]}  source {cal_source}  hash {doc['calendar_hash']}")
    _emit(out, f"  universe       {len(doc['config']['universe_members'])} symbols  hash {doc['universe_hash']}"
               + ("  (from the database's observed candidates)" if authoring.universe_from_db else "  (from the spec)"))
    for k, v in sorted(doc["input_hashes"].items()):
        _emit(out, f"  input {k:<34} {v}")
    return EXIT_OK


# ------------------------------------------------------------------ validate
def _load_manifest_file(path: str) -> M.Manifest:
    return AUTH.parse_manifest_file(_read_json(path, "manifest"))[0]


def _verification_text(v: C.InputVerification) -> str:
    lines = ["INPUT VERIFICATION (each manifest input hash recomputed from the database / the held content)"]
    for c in sorted(v.checks, key=lambda c: c.key):
        lines.append(f"  [{c.status:<12}] {c.key:<34} {c.kind}" + ("" if c.status in ("verified", "unverifiable") else f"   expected {c.expected}  actual {c.actual}"))
    return "\n".join(lines) + "\n"


def cmd_validate(a: argparse.Namespace, connect: Connect, code_provider: CodeProvider, out: Callable[[str], None]) -> int:
    manifest = _load_manifest_file(a.manifest)
    code = _code(code_provider, a.repo_root)
    cfg = C.config_for(manifest.document)
    conn = connect(a, readonly=True)
    try:
        _preflight(conn, _tables_for(cfg))
        v = R.verify_manifest(conn, manifest, code=code, allow_code_sha_drift=a.allow_code_sha_drift)
    finally:
        conn.close()
    _emit(out, f"MANIFEST FILE VALID  manifest_hash {manifest.manifest_hash}  (re-derived from the document and its calendar)")
    cc = v.code_check
    _emit(out, f"  code     running {cc['running_code_sha']}  manifest {cc['manifest_code_sha']}  -> {cc['status']}")
    _emit(out, _verification_text(v.verification))
    late = {k: n for k, n in sorted(v.post_cutoff_counts.items()) if n}
    _emit(out, "  rows that arrived after the cutoff (ignored by the dataset): " + (", ".join(f"{k} {n}" for k, n in late.items()) or "none"))
    if not v.verification.ok:
        _emit(out, "VERIFICATION FAILED: the database no longer says what it said when this manifest was authored. A build would refuse. "
                   "Re-author the manifest only if the change is understood and intended.")
        return EXIT_FAILED
    _emit(out, "VALIDATION OK: every manifest input verifies; a build may proceed.")
    return EXIT_OK


# ------------------------------------------------------------------ build
def _identities(build: R.Build, ready: Dict[str, Any], code: R.CodeIdentity) -> Dict[str, Any]:
    return {"schema": IDENTITIES_SCHEMA, "manifest_hash": build.manifest.manifest_hash, "dataset_hash": build.dataset_hash,
            "audit_hash": build.audit.audit_hash, "report_hash": build.report["report_hash"], "readiness_hash": ready["readiness_hash"],
            "inputs_hash": build.inputs_hash, "manifest_code_sha": build.manifest.document["code_sha"], "running_code_sha": code.sha,
            "code_check": dict(build.code_check), "model_research_eligible": ready["eligibility"]["model_research_eligible"],
            "predictive_edge_claim": "none", "distinction": READY.DISTINCTION}


def _identities_text(i: Dict[str, Any]) -> str:
    return "\n".join(["IDENTITIES", f"  manifest_hash  {i['manifest_hash']}", f"  dataset_hash   {i['dataset_hash']}",
                      f"  audit_hash     {i['audit_hash']}", f"  report_hash    {i['report_hash']}", f"  readiness_hash {i['readiness_hash']}",
                      f"  inputs_hash    {i['inputs_hash']}",
                      f"  code_sha       running {i['running_code_sha']}  manifest {i['manifest_code_sha']}  ({i['code_check']['status']})",
                      f"  model_research_eligible: {'YES' if i['model_research_eligible'] else 'NO'}   (predictive edge claimed: none)",
                      f"  {i['distinction']}"]) + "\n"


def _write_canonical(d: Path, build: R.Build, ready: Dict[str, Any], ident: Dict[str, Any]) -> None:
    _write_new(d / "dataset.jsonl", b"".join(C.dataset_chunks(build.manifest, build.rows)))
    _write_new(d / "audit.json", AUTH.dump_json(build.audit.document))
    _write_new(d / "audit.txt", build.audit.text)
    _write_new(d / "verification.json", AUTH.dump_json(build.verification.to_json()))
    _write_new(d / "report.json", AUTH.dump_json(build.report))
    _write_new(d / "report.txt", build.report_text)
    _write_new(d / "readiness.json", AUTH.dump_json(ready))
    _write_new(d / "readiness.txt", READY.render_text(ready))
    _write_new(d / "identities.json", AUTH.dump_json(ident))


def _register(a: argparse.Namespace, connect: Connect, build: R.Build, code: R.CodeIdentity) -> Dict[str, Any]:
    """The ONLY write path: manifest + diagnostic registration + one count-only result, on a separate writable connection, in one transaction."""
    reg = R.diagnostic_registration(build.manifest, code, a.experiment_name)
    if a.include_test and reg.registration_hash != a.registration_hash:
        raise LabError([f"--registration-hash {a.registration_hash} is not the registration of experiment '{a.experiment_name}' for this manifest "
                        f"and code ({reg.registration_hash})"])
    w = connect(a, readonly=False)
    try:
        _preflight(w, list(_REGISTRY_TABLES))
        cur = w.cursor()
        m = RS.insert_manifest(cur, build.manifest)
        r = RS.insert_registration(cur, reg)
        kinds = RS.result_kinds(cur, reg.registration_hash)
        if a.include_test:
            result_id, kind = R.record_test(cur, build, reg, code), "test"
        elif "validation" in kinds:
            result_id, kind = None, "validation_already_recorded"
        else:
            result_id, kind = R.record_validation(cur, build, reg, code), "validation"
        w.commit()
    except BaseException:
        w.rollback()
        raise
    finally:
        w.close()
    return {"experiment_name": a.experiment_name, "registration_hash": reg.registration_hash, "manifest_row_created": m["created"],
            "registration_row_created": r["created"], "result_kind": kind, "result_id": result_id}


def _check_build_flags(a: argparse.Namespace) -> None:
    if a.register and not a.experiment_name:
        raise UsageError("--register needs --experiment-name")
    if a.experiment_name and not a.register:
        raise UsageError("--experiment-name only applies with --register (a build records nothing unless --register is given)")
    if a.include_test and not (a.register and a.registration_hash):
        raise UsageError("--include-test needs --register and --registration-hash: the test split is revealed only through a registered, "
                         "one-shot experiment, so it can never be looked at without being recorded")
    if a.registration_hash and not a.include_test:
        raise UsageError("--registration-hash only applies with --include-test")
    if a.register and a.allow_code_sha_drift:
        raise UsageError("--register cannot be combined with --allow-code-sha-drift: a recorded result must name the exact code that made it")


def _failure_artifacts(d: Path, e: LabError) -> None:
    doc: Dict[str, Any] = {"schema": "lab_dataset_failure_v1", "problems": list(e.problems)}
    audit, ver = getattr(e, "audit", None), getattr(e, "verification", None)
    if audit is not None:
        _write_new(d / "audit.json", AUTH.dump_json(audit.document))
        _write_new(d / "audit.txt", audit.text)
        doc["audit_hash"] = audit.audit_hash
    if ver is not None:
        _write_new(d / "verification.json", AUTH.dump_json(ver.to_json()))
    _write_new(d / "failure.json", AUTH.dump_json(doc))


def cmd_build(a: argparse.Namespace, connect: Connect, code_provider: CodeProvider, out: Callable[[str], None]) -> int:
    _check_build_flags(a)
    out_dir = Path(a.out_dir)
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise UsageError(f"{out_dir} exists and is not empty: a build never overwrites artifacts")
    manifest = _load_manifest_file(a.manifest)
    code = _code(code_provider, a.repo_root)
    cfg = C.config_for(manifest.document)
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(a, readonly=True)
    try:
        _preflight(conn, _tables_for(cfg, registry=a.include_test))
        try:
            build = R.build_from_manifest(conn, manifest, code=code, allow_code_sha_drift=a.allow_code_sha_drift, include_test=a.include_test,
                                          registration_hash=a.registration_hash)
        except LabError as e:
            _failure_artifacts(out_dir, e)
            if getattr(e, "audit", None) is not None:
                _emit(out, e.audit.text)
            raise
    finally:
        conn.close()

    ready = READY.assess(manifest=manifest, cfg=build.config, rows=build.rows, audit_document=build.audit.document, audit_hash=build.audit.audit_hash,
                         verification=build.verification, code_check=build.code_check, dataset_hash=build.dataset_hash,
                         report_hash=build.report["report_hash"], inputs_hash=build.inputs_hash, include_test=a.include_test)
    ident = _identities(build, ready, code)
    eligible = ready["eligibility"]["model_research_eligible"]
    registration = None
    if a.register and a.require_eligible and not eligible:
        _write_canonical(out_dir, build, ready, ident)
        _write_new(out_dir / CONTEXT_ARTIFACT, AUTH.dump_json({"schema": CONTEXT_SCHEMA, "database": _db_target(a), "mode": "read_only",
                                                               "post_cutoff_rows_ignored": dict(build.post_cutoff_counts),
                                                               "registration": "refused: --require-eligible and not eligible"}))
        _emit(out, _identities_text(ident))
        _emit(out, READY.render_text(ready))
        _emit(out, "NOT ELIGIBLE and --require-eligible was given: nothing was registered.")
        return EXIT_NOT_ELIGIBLE
    if a.register and a.include_test:
        registration = _register(a, connect, build, code)           # the test split is spent before it is shown or written anywhere
    _write_canonical(out_dir, build, ready, ident)
    if a.register and not a.include_test:
        registration = _register(a, connect, build, code)
    _write_new(out_dir / CONTEXT_ARTIFACT, AUTH.dump_json({
        "schema": CONTEXT_SCHEMA, "database": _db_target(a), "mode": "registered" if registration else "read_only",
        "post_cutoff_rows_ignored": dict(build.post_cutoff_counts), "registration": registration}))

    _emit(out, "BUILD OK  (read-only" + ("; registered" if registration else "; NOTHING was recorded in the database") + ")")
    _emit(out, _identities_text(ident))
    if registration:
        _emit(out, f"REGISTRATION  {registration['result_kind']}  registration_hash {registration['registration_hash']}  "
                   f"(manifest row created: {registration['manifest_row_created']}, registration row created: {registration['registration_row_created']})\n")
    late = {k: n for k, n in sorted(build.post_cutoff_counts.items()) if n}
    _emit(out, "rows that arrived after the cutoff and were ignored: " + (", ".join(f"{k} {n}" for k, n in late.items()) or "none") + "\n")
    _emit(out, READY.render_text(ready))
    if not a.quiet:
        _emit(out, build.report_text)
    _emit(out, f"artifacts in {out_dir}: " + ", ".join(CANONICAL_ARTIFACTS + (CONTEXT_ARTIFACT,)))
    if a.require_eligible and not eligible:
        _emit(out, "NOT ELIGIBLE and --require-eligible was given.")
        return EXIT_NOT_ELIGIBLE
    return EXIT_OK


# ------------------------------------------------------------------ status
def _parse_cutoff(text: str):
    if text == "db-now":
        return None
    try:
        d = datetime.fromisoformat(text)
    except ValueError:
        d = None
    if d is None or d.tzinfo is None:
        raise UsageError("--cutoff must be an ISO timestamp WITH a UTC offset (e.g. 2026-10-05T00:00:00+00:00) or the word db-now")
    return d.astimezone(timezone.utc)


def _status_code(code_provider: CodeProvider, repo_root: str) -> Dict[str, Any]:
    """Volatile context only: the status has no code identity of its own and must not fail because git is unavailable or the tree is dirty."""
    try:
        c = code_provider(repo_root)
        return {"running_code_sha": c.sha, "tree_clean": c.tree_clean}
    except Exception as e:  # noqa: BLE001
        return {"running_code_sha": None, "unavailable": type(e).__name__}


def _status_summary(doc: Dict[str, Any]) -> str:
    blockers = sum(1 for f in doc["integrity"] if f["severity"] == "blocker" and f["count"])
    return (f"STATUS  hash {doc['status_hash']}  readiness failures {len(doc['readiness_failures'])}  integrity blockers {blockers}  "
            f"contract data checks {'ALL PASS' if doc['contract'].get('data_checks_all_pass') else 'NOT all passing'}  predictive edge claimed: none")


def cmd_status(a: argparse.Namespace, connect: Connect, code_provider: CodeProvider, out: Callable[[str], None]) -> int:
    spec_path = Path(a.spec)
    authoring = AUTH.parse_authoring(_read_json(a.spec, "status spec"))
    override = _parse_cutoff(a.cutoff) if a.cutoff else authoring.knowledge_cutoff_at
    out_dir = Path(a.out_dir) if a.out_dir else None
    if out_dir is not None and out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise UsageError(f"{out_dir} exists and is not empty: status never overwrites artifacts")
    file_cal = _file_calendar(authoring, spec_path)
    strat = authoring.config.get("strategy")
    if not (isinstance(strat, dict) and strat.get("key") and strat.get("version")):
        raise LabError(["config.strategy {key, version} is required"])
    base_cfg = SR.parse_cfg(authoring, [])
    conn = connect(a, readonly=True)
    try:
        _preflight(conn, _tables_for(base_cfg) + list(SR.EXTRA_TABLES) + (list(_CALENDAR_TABLES) if file_cal is None else []))
        with RD.read_only_session(conn):
            now = RS.db_now(conn.cursor())
            authoring = dataclasses.replace(authoring, knowledge_cutoff_at=override if override is not None else now.replace(microsecond=0))
            calendar, cal_source = _resolve_calendar(authoring, file_cal, conn)
        inp, context = SR.collect(conn, authoring, calendar, cal_source, now)
    finally:
        conn.close()
    doc = ST.build_status(inp)
    if not ST.verify_status_hash(doc):
        raise LabError(["internal: the status document does not re-derive its own hash"])
    text = ST.render_text(doc)
    ctx = {"schema": STATUS_CONTEXT_SCHEMA, "database": _db_target(a), "mode": "read_only", "database_time": context["database_time"],
           "post_cutoff_rows_ignored": context["post_cutoff_rows"], "code": _status_code(code_provider, a.repo_root), "status_hash": doc["status_hash"]}
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_new(out_dir / "status.json", AUTH.dump_json(doc))
        _write_new(out_dir / "status.txt", text)
        _write_new(out_dir / STATUS_CONTEXT_ARTIFACT, AUTH.dump_json(ctx))
    failed = bool(a.require_no_readiness_failures and doc["readiness_failures"])
    if a.json:
        _emit(out, AUTH.dump_json(doc))
    else:
        _emit(out, _status_summary(doc))
        if not a.quiet:
            _emit(out, "")
            _emit(out, text)
        if out_dir is not None:
            _emit(out, f"artifacts in {out_dir}: " + ", ".join(STATUS_ARTIFACTS + (STATUS_CONTEXT_ARTIFACT,)))
        if failed:
            _emit(out, f"{len(doc['readiness_failures'])} readiness failure(s) listed and --require-no-readiness-failures was given.")
    return EXIT_NOT_ELIGIBLE if failed else EXIT_OK


# ------------------------------------------------------------------ argv
def _parser() -> argparse.ArgumentParser:
    db = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    db.add_argument("--host", required=True)
    db.add_argument("--dbname", required=True)
    db.add_argument("--user", required=True)
    db.add_argument("--port", type=int, default=5432)
    db.add_argument("--password-env", default="PGPASSWORD", help="name of the environment variable holding the password (never a value)")
    db.add_argument("--connect-timeout", type=int, default=10)
    db.add_argument("--repo-root", default=str(REPO_ROOT), help="the git checkout whose HEAD is the code identity")
    p = argparse.ArgumentParser(prog="dataset_cli", description=__doc__.split("\n\n")[0], allow_abbrev=False)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("author", parents=[db], allow_abbrev=False, help="author a manifest file from a spec and the database (read-only)")
    s.add_argument("--spec", required=True)
    s.add_argument("--out", required=True)
    s = sub.add_parser("validate", parents=[db], allow_abbrev=False, help="re-validate a manifest file and verify every input hash against the database")
    s.add_argument("--manifest", required=True)
    s.add_argument("--allow-code-sha-drift", action="store_true")
    s = sub.add_parser("build", parents=[db], allow_abbrev=False, help="build, audit and report a dataset; records nothing unless --register")
    s.add_argument("--manifest", required=True)
    s.add_argument("--out-dir", required=True)
    s.add_argument("--allow-code-sha-drift", action="store_true")
    s.add_argument("--register", action="store_true", help="record the manifest + a diagnostic registration + one count-only result (the only write)")
    s.add_argument("--experiment-name")
    s.add_argument("--include-test", action="store_true", help="reveal the test split (one-shot; needs --register and --registration-hash)")
    s.add_argument("--registration-hash")
    s.add_argument("--require-eligible", action="store_true", help="exit 4 (and register nothing) unless model_research_eligible")
    s.add_argument("--quiet", action="store_true", help="omit the full report text from stdout (it is still written to report.txt)")
    s = sub.add_parser("status", parents=[db], allow_abbrev=False,
                       help="read-only research observation status: what is accumulating, what is missing, what blocks the Slice 5 contract")
    s.add_argument("--spec", required=True, help="an authoring spec (the same file `author` takes); only its config/calendar/windows are used")
    s.add_argument("--cutoff", help="ISO timestamp WITH a UTC offset, or db-now, replacing the spec's knowledge_cutoff_at (the status is a pure function of the cutoff)")
    s.add_argument("--out-dir", help="also write status.json, status.txt and the volatile status_context.json here (must be new or empty)")
    s.add_argument("--json", action="store_true", help="print the canonical status.json on stdout instead of the text")
    s.add_argument("--quiet", action="store_true", help="print only the one-line summary (the full text is still written to status.txt)")
    s.add_argument("--require-no-readiness-failures", action="store_true", help="exit 4 if any readiness failure is listed")
    return p


def main(argv: Optional[Sequence[str]] = None, *, connect: Optional[Connect] = None, code_provider: Optional[CodeProvider] = None,
         out: Optional[Callable[[str], None]] = None, err: Optional[Callable[[str], None]] = None) -> int:
    out = out or sys.stdout.write
    err = err or sys.stderr.write
    connect = connect or default_connect
    code_provider = code_provider or R.current_code_identity
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return EXIT_USAGE if e.code else EXIT_OK
    handler = {"author": cmd_author, "validate": cmd_validate, "build": cmd_build, "status": cmd_status}[args.command]
    try:
        return handler(args, connect, code_provider, out)
    except UsageError as e:
        _emit(err, f"usage error: {e}")
        return EXIT_USAGE
    except LabError as e:
        _emit(err, "FAILED CLOSED:")
        for prob in e.problems:
            _emit(err, f"  - {prob}")
        return EXIT_FAILED
    except psycopg2.Error as e:
        _emit(err, f"database error ({type(e).__name__}): {str(e).strip().splitlines()[0] if str(e).strip() else ''}")
        return EXIT_DB


if __name__ == "__main__":
    sys.exit(main())
