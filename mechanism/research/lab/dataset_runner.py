"""The dataset build: manifest_hash in, (dataset, audit, baselines, report) out -- read-only against the database.

Order of operations (each step fails closed, nothing later runs on a failed earlier step):
  1. fetch the manifest by `manifest_hash` and RE-VALIDATE it (hash of the stored document, full manifest validation, calendar identity);
  2. parse its `config` (strict; every problem reported);
  3. check the running code against the manifest's `code_sha` (explicit, recorded drift allowance only);
  4. read every needed source in a DATABASE-enforced read-only transaction, bounded by the cutoff / span / universe / versions;
  5. fingerprint each database read and VERIFY every manifest input hash (cryptographic + query fingerprints);
  6. assemble, then audit independently; any fatal finding raises `BuildFailed` (carrying the audit + verification for the human);
  7. `dataset_hash`, baselines, deterministic report.
Recording a run goes through the registry (`record_validation` / `record_test`): count-only registered metrics plus the artifact hashes. No
migration, no new table, and the registry's one-shot test rule is enforced before the test split is ever computed.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from research.lab import dataset_assemble as A
from research.lab import dataset_audit as AU
from research.lab import dataset_baselines as B
from research.lab import dataset_contract as C
from research.lab import dataset_reader as RD
from research.lab import dataset_report as RP
from research.lab import manifest as M
from research.lab import registry_store as RS
from research.lab.manifest import LabError, canonical_hash

DIAGNOSTIC_METRICS = ("n_rows", "n_final")


@dataclass(frozen=True)
class CodeIdentity:
    sha: str
    tree_clean: bool


def current_code_identity(repo_root: str) -> CodeIdentity:
    """The checked-out commit and whether the tree is clean (tracked files only; the lab's own untracked scratch must not block a run)."""
    sha = subprocess.run(["git", "-C", repo_root, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", repo_root, "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, check=True).stdout.strip()
    return CodeIdentity(sha, not dirty)


class BuildFailed(LabError):
    """A fatal finding. The audit / verification travel with the exception so the failure is explainable, not just a message."""

    def __init__(self, problems: Sequence[str], *, audit: Optional[AU.Audit] = None, verification: Optional[C.InputVerification] = None):
        super().__init__(list(problems))
        self.audit = audit
        self.verification = verification


@dataclass(frozen=True)
class Build:
    manifest: M.Manifest
    config: C.DatasetConfig
    code_check: Dict[str, Any]
    verification: C.InputVerification
    fingerprints: Mapping[str, str]
    assembly: A.Assembly
    audit: AU.Audit
    dataset_hash: str
    baselines: Mapping[str, Any]
    report: Mapping[str, Any]
    include_test: bool
    post_cutoff_counts: Mapping[str, int] = field(default_factory=dict)       # run metadata: never part of any hash

    @property
    def rows(self):
        return self.assembly.rows

    @property
    def inputs_hash(self) -> str:
        return canonical_hash({"inputs": self.verification.to_json()})

    @property
    def report_text(self) -> str:
        return RP.render_text(self.report)

    @property
    def artifact_hashes(self) -> Dict[str, str]:
        return {"dataset_hash": self.dataset_hash, "report_hash": self.report["report_hash"], "audit_hash": self.audit.audit_hash,
                "inputs_hash": self.inputs_hash}


def _check_code(doc: Mapping[str, Any], code: Optional[CodeIdentity], allow_drift: bool) -> Dict[str, Any]:
    if code is None:
        raise BuildFailed(["code identity was not supplied: a build cannot prove which code made the dataset"])
    status = {"manifest_code_sha": doc["code_sha"], "running_code_sha": code.sha, "tree_clean": code.tree_clean,
              "allow_code_sha_drift": allow_drift, "status": "match"}
    p: List[str] = []
    if code.sha != doc["code_sha"]:
        if allow_drift:
            status["status"] = "drift_allowed"
        else:
            p.append(f"running code {code.sha[:12]} is not the manifest's code_sha {doc['code_sha'][:12]} (pass allow_code_sha_drift to build anyway; "
                     "the drift is then recorded in the report's identity)")
    if not code.tree_clean:
        p.append("the working tree is not clean: a dataset must be reproducible from a commit")
    if p:
        raise BuildFailed(p)
    return status


def load_manifest(cur, manifest_hash: str, calendar: Sequence) -> M.Manifest:
    got = RS.get_manifest(cur, manifest_hash)
    if got is None:
        raise LabError([f"no dataset manifest with hash {manifest_hash}"])
    return C.revalidate_manifest(got["manifest"], manifest_hash, list(calendar), got["created_at"])


def _check_test_split(cur, include_test: bool, registration_hash: Optional[str]) -> None:
    if not include_test:
        return
    if not registration_hash:
        raise LabError(["the test split is evaluated through a registered experiment: pass registration_hash"])
    kinds = RS.result_kinds(cur, registration_hash)
    if "validation" not in kinds:
        raise LabError(["the test split needs a recorded validation result first"])
    if "test" in kinds:
        raise LabError(["the test window was already evaluated for this experiment; a re-test is a new experiment"])
    if "failed" in kinds or "abandoned" in kinds:
        raise LabError(["the experiment is closed (failed/abandoned): its test window stays unevaluated"])


@dataclass(frozen=True)
class Verified:
    """What `verify_manifest` proves without assembling anything: the parsed config, the code check, every database fingerprint and the
    verification of each manifest input hash. `verification.ok` False means a build would fail closed."""
    config: C.DatasetConfig
    code_check: Dict[str, Any]
    fingerprints: Mapping[str, str]
    verification: C.InputVerification
    post_cutoff_counts: Mapping[str, int]


def _verify(manifest: M.Manifest, cfg: C.DatasetConfig, raw: Mapping[str, Any]):
    doc = manifest.document
    fingerprints = {s: C.fingerprint(s, raw[s], _cutoff(doc)) for s in C.enabled_db_sources(cfg)}
    return fingerprints, C.verify_inputs(doc, cfg, manifest.calendar, cfg.universe_members, fingerprints)


def verify_manifest(conn, manifest: M.Manifest, *, code: Optional[CodeIdentity], allow_code_sha_drift: bool = False) -> Verified:
    """Everything a build checks BEFORE assembling: config parse, code identity, then the database fingerprints against the manifest's input
    hashes (read-only). Raises on a config / code problem; a fingerprint mismatch is returned (`verification.ok` False) so it can be shown."""
    with RD.read_only_session(conn):
        cur = conn.cursor()
        cfg = C.config_for(manifest.document)
        code_check = _check_code(manifest.document, code, allow_code_sha_drift)
        raw = RD.read_raw(cur, manifest, cfg)
        post = RD.post_cutoff_counts(cur, manifest, cfg)
    fingerprints, verification = _verify(manifest, cfg, raw)
    return Verified(cfg, code_check, fingerprints, verification, post)


def _build(conn, fetch, *, code: Optional[CodeIdentity], allow_code_sha_drift: bool, include_test: bool,
           registration_hash: Optional[str]) -> Build:
    with RD.read_only_session(conn):
        cur = conn.cursor()
        manifest = fetch(cur)
        doc = manifest.document
        cfg = C.config_for(doc)
        code_check = _check_code(doc, code, allow_code_sha_drift)
        _check_test_split(cur, include_test, registration_hash)
        raw = RD.read_raw(cur, manifest, cfg)
        post = RD.post_cutoff_counts(cur, manifest, cfg)
    fingerprints, verification = _verify(manifest, cfg, raw)
    assembly = A.assemble(manifest, cfg, raw)
    audit = AU.audit(manifest, cfg, raw, assembly, verification)
    if not audit.ok:
        raise BuildFailed([f"{f['code']} x{f['count']}" for f in audit.document["fatal"]], audit=audit, verification=verification)
    dhash = C.dataset_hash(manifest, assembly.rows)
    baselines = B.run_baselines(assembly.rows, cfg, include_test=include_test)
    report = RP.build_report(manifest, cfg, assembly.rows, dhash, audit, baselines)
    report = _with_code_check(report, code_check)
    return Build(manifest, cfg, code_check, verification, fingerprints, assembly, audit, dhash, baselines, report, include_test, post)


def build_dataset(conn, manifest_hash: str, calendar: Sequence, *, code: Optional[CodeIdentity], allow_code_sha_drift: bool = False,
                  include_test: bool = False, registration_hash: Optional[str] = None) -> Build:
    """Build the dataset the (registered) manifest names. `calendar` is the explicit trading-session list (its hash is in the manifest)."""
    return _build(conn, lambda cur: load_manifest(cur, manifest_hash, calendar), code=code, allow_code_sha_drift=allow_code_sha_drift,
                  include_test=include_test, registration_hash=registration_hash)


def build_from_manifest(conn, manifest: M.Manifest, *, code: Optional[CodeIdentity], allow_code_sha_drift: bool = False,
                        include_test: bool = False, registration_hash: Optional[str] = None) -> Build:
    """The same build for a manifest the caller already holds and has validated (a manifest FILE), so nothing needs to be registered -- or
    written -- first. The identical steps run; the dataset it yields equals `build_dataset`'s for the same manifest."""
    return _build(conn, lambda cur: manifest, code=code, allow_code_sha_drift=allow_code_sha_drift, include_test=include_test,
                  registration_hash=registration_hash)


def author_input_hashes(conn, provisional: M.Manifest, cfg: C.DatasetConfig) -> Dict[str, str]:
    """The `input_hashes` a manifest for this config must carry, computed from the database as it stands (read-only). `provisional` is only a
    bound (calendar span, cutoff, label identity): it is never registered, and the real manifest is built from the hashes returned here."""
    with RD.read_only_session(conn):
        raw = RD.read_raw(conn.cursor(), provisional, cfg)
    fps = {s: C.fingerprint(s, raw[s], _cutoff(provisional.document)) for s in C.enabled_db_sources(cfg)}
    return C.compute_input_hashes(cfg, provisional.calendar, cfg.universe_members, fps)


def _cutoff(doc: Mapping[str, Any]) -> datetime:
    return datetime.fromisoformat(doc["knowledge_cutoff_at"])


def _with_code_check(report: Mapping[str, Any], code_check: Mapping[str, Any]) -> Dict[str, Any]:
    body = {k: v for k, v in report.items() if k != "report_hash"}
    body["identity"] = dict(body["identity"], code_check=dict(code_check))
    body["report_hash"] = canonical_hash(body)
    return body


# ------------------------------------------------------------------ recording through the registry
def diagnostic_registration(manifest: M.Manifest, code: CodeIdentity, name: str) -> M.Registration:
    """The registration a diagnostic baseline run uses: no model, no search (budget 1), count-only metrics, and a decision rule that decides nothing."""
    spec = M.ExperimentSpec(
        experiment_name=name, manifest=manifest, code_sha=code.sha, code_tree_clean=code.tree_clean,
        feature_versions=dict(manifest.document["feature_versions"]),
        model_spec={"family": "diagnostic_baselines", "version": B.BASELINE_VERSION, "fitted": False},
        search_budget=1, seed=0,
        evaluation_plan={"metrics": list(DIAGNOSTIC_METRICS), "primary_metric": "n_final",
                         "decision_rule": "Diagnostic only. The registered metrics are row counts; no performance claim and no model selection follows."},
        hypothesis="Describe the labelled history under fixed, pre-declared strata; claim nothing predictive.")
    return M.build_registration(spec)


def _metrics(build: Build, split: str) -> Dict[str, float]:
    sel = [r for r in build.rows if r["split"] == split and r["horizon_sessions"] == build.config.primary_horizon]
    return {"n_rows": float(len(sel)), "n_final": float(sum(1 for r in sel if r["label_status"] == "final"))}


def record_validation(cur, build: Build, reg: M.Registration, code: CodeIdentity) -> int:
    """Append one `validation` result (counts of the validation split at the primary horizon) carrying the artifact hashes."""
    return RS.append_result(cur, M.ResultSpec(reg.registration_hash, "validation", _metrics(build, "validation"), 1, build.artifact_hashes,
                                              code.sha, "diagnostic baselines; counts only"), reg)


def record_test(cur, build: Build, reg: M.Registration, code: CodeIdentity) -> int:
    if not build.include_test:
        raise LabError(["this build withheld the test split: it cannot be recorded as the test evaluation"])
    return RS.append_result(cur, M.ResultSpec(reg.registration_hash, "test", _metrics(build, "test"), 1, build.artifact_hashes, code.sha,
                                              "diagnostic baselines; counts only; one-shot"), reg)
