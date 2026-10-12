"""Dataset manifest + experiment registration (pure). The reproducibility and leakage contract that must exist BEFORE any ML work.

A manifest is the exact identity of a dataset: code SHA, dataset version, label version + methodology + horizons, feature versions, universe
definition (+ hash), the point-in-time cutoff, train / validation / test windows, embargo and purge (in trading SESSIONS of an explicit
calendar), input hashes and the creation time. `manifest_hash` is the sha256 of the canonical document (all of it except `created_at`, which
the database stamps), so two datasets are the same dataset iff their hashes are equal.

Leakage controls enforced here (and, where the database can prove them, again by migration 30's CHECKs and triggers):
  * embargo >= max(60, longest label horizon) + purge sessions: a label at the end of a window reads up to 60 sessions of FUTURE prices, so
    the next window must start further away than that or its features overlap the previous window's label outcomes;
  * the embargo is measured in sessions of an explicit calendar, not calendar days (windows must start/end on sessions);
  * the label-maturity session lies >= longest horizon + VOID_GRACE sessions after the test window ends: labels have been finalised;
  * the knowledge cutoff is not before the maturity session and not in the future (the caller injects "now"; this module reads no clock);
  * `assign_split` puts a row in a window only if its WHOLE label window [t0, t0 + horizon] lies inside it, otherwise `purged` / `embargo`;
  * a dirty working tree cannot produce a manifest or a registration (a SHA that does not describe the code is not provenance).

An experiment is registered against one manifest BEFORE results exist: model + configuration, search budget, seed, evaluation plan (metrics,
primary metric, decision rule) and hypothesis. The test window can be evaluated once. Nothing here optimises, fits or scores a model.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# Mirrors of the fwd_v1 constants. Deliberately not imported: nothing outside research/labels may import the label engine
# (test_import_separation); test_lab_manifest pins these to the fwd_v1 module's own constants so they cannot drift.
HORIZONS = (1, 3, 5, 10, 20, 60)
LABEL_VERSION = "fwd_v1"
METHODOLOGY_VERSION = "fwd_v1.m1"
VOID_GRACE_SESSIONS = 3

MANIFEST_SCHEMA = "lab_manifest_v1"
REGISTRATION_SCHEMA = "lab_registration_v1"
KNOWN_LABELS = {LABEL_VERSION: METHODOLOGY_VERSION}
LONGEST_FWD_V1_HORIZON = max(HORIZONS)
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
RESULT_KINDS = ("validation", "test", "failed", "abandoned")
SPLITS = ("train", "validation", "test")


class LabError(ValueError):
    """Carries every violation found, not just the first."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


# ------------------------------------------------------------------ canonical hashing
def _canon(v: Any, path: str = "") -> Any:
    if v is None or isinstance(v, (bool, str, int)):
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            raise LabError([f"{path or 'value'} is NaN/infinite"])
        return v
    if isinstance(v, datetime):
        if v.tzinfo is None:
            raise LabError([f"{path or 'value'} is a naive datetime"])
        return v.astimezone(timezone.utc).isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Mapping):
        return {str(k): _canon(x, f"{path}.{k}") for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    if isinstance(v, (list, tuple)):
        return [_canon(x, f"{path}[]") for x in v]
    raise LabError([f"{path or 'value'}: unsupported type {type(v).__name__}"])


def canonical_hash(doc: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(_canon(doc), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def universe_hash(members: Sequence[str], rule: str) -> str:
    """Hash of the sorted unique member list AND the rule that produced it (the same members under another rule are another universe)."""
    if not rule or not members:
        raise LabError(["a universe needs members and the rule that produced them"])
    return canonical_hash({"members": sorted(set(members)), "rule": rule})


# ------------------------------------------------------------------ dataset manifest
@dataclass(frozen=True)
class Windows:
    train: Tuple[date, date]
    validation: Tuple[date, date]
    test: Tuple[date, date]


@dataclass(frozen=True)
class DatasetSpec:
    dataset_name: str
    dataset_version: str
    code_sha: str
    code_tree_clean: bool
    label_version: str
    label_methodology_version: str
    label_horizons: Tuple[int, ...]
    feature_versions: Mapping[str, str]
    universe_id: str
    universe_hash: str
    knowledge_cutoff_at: datetime
    label_maturity_session: date
    windows: Windows
    embargo_sessions: int
    purge_sessions: int
    calendar_source: str
    calendar: Tuple[date, ...]                 # the explicit trading calendar the session counts use (stored as a hash, not inlined)
    input_hashes: Mapping[str, str]
    config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Manifest:
    document: Dict[str, Any]
    manifest_hash: str
    calendar: Tuple[date, ...]

    def row(self) -> Dict[str, Any]:
        d = self.document
        w = d["windows"]
        return {
            "dataset_name": d["dataset_name"], "dataset_version": d["dataset_version"], "manifest_hash": self.manifest_hash,
            "code_sha": d["code_sha"], "label_version": d["label_version"], "label_methodology_version": d["label_methodology_version"],
            "label_horizons": list(d["label_horizons"]), "feature_versions": dict(d["feature_versions"]), "universe_id": d["universe_id"],
            "universe_hash": d["universe_hash"], "knowledge_cutoff_at": datetime.fromisoformat(d["knowledge_cutoff_at"]),
            "label_maturity_session": date.fromisoformat(d["label_maturity_session"]),
            "train_start": date.fromisoformat(w["train"][0]), "train_end": date.fromisoformat(w["train"][1]),
            "validation_start": date.fromisoformat(w["validation"][0]), "validation_end": date.fromisoformat(w["validation"][1]),
            "test_start": date.fromisoformat(w["test"][0]), "test_end": date.fromisoformat(w["test"][1]),
            "embargo_sessions": d["embargo_sessions"], "purge_sessions": d["purge_sessions"], "calendar_source": d["calendar_source"],
            "input_hashes": dict(d["input_hashes"]), "config": dict(d["config"]), "manifest": self.document,
        }

    def required_gap(self) -> int:
        return required_gap(self.document["label_horizons"], self.document["purge_sessions"])


def required_gap(horizons: Sequence[int], purge_sessions: int) -> int:
    return max(LONGEST_FWD_V1_HORIZON, max(horizons)) + purge_sessions


def _idx(cal: Sequence[date], d: date) -> Optional[int]:
    i = bisect_left(cal, d)
    return i if i < len(cal) and cal[i] == d else None


def build_manifest(spec: DatasetSpec, *, now: datetime) -> Manifest:
    """Validate everything and return the canonical, hashed manifest, or raise LabError listing every violation."""
    p: List[str] = []
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise LabError(["now must be a timezone-aware datetime (inject the database or system time)"])
    for name in ("dataset_name", "dataset_version", "universe_id", "calendar_source"):
        if not getattr(spec, name):
            p.append(f"{name} is empty")
    if not SHA40.match(spec.code_sha or ""):
        p.append("code_sha must be a full 40-hex lowercase git commit")
    if spec.code_tree_clean is not True:
        p.append("the working tree is not clean: a SHA that does not describe the code is not provenance")
    if KNOWN_LABELS.get(spec.label_version) != spec.label_methodology_version:
        p.append(f"label {spec.label_version}/{spec.label_methodology_version} is not a known label methodology pair")
    hs = tuple(spec.label_horizons)
    if not hs or len(set(hs)) != len(hs) or any(h not in HORIZONS for h in hs):
        p.append(f"label_horizons must be a non-empty, duplicate-free subset of {HORIZONS}")
    if not spec.feature_versions or any(not k or not isinstance(v, str) or not v for k, v in spec.feature_versions.items()):
        p.append("feature_versions must name at least one feature set with a version")
    if not SHA256.match(spec.universe_hash or ""):
        p.append("universe_hash must be a sha256 (use universe_hash())")
    if not spec.input_hashes or any(not SHA256.match(str(h)) for h in spec.input_hashes.values()):
        p.append("input_hashes must be a non-empty map of sha256 digests")
    if not isinstance(spec.knowledge_cutoff_at, datetime) or spec.knowledge_cutoff_at.tzinfo is None:
        p.append("knowledge_cutoff_at must be timezone-aware")
        cutoff = None
    else:
        cutoff = spec.knowledge_cutoff_at.astimezone(timezone.utc)
    cal = tuple(spec.calendar)
    if not cal or any(b <= a for a, b in zip(cal, cal[1:])):
        p.append("calendar must be a non-empty, strictly increasing tuple of sessions")
        raise LabError(p)
    w = spec.windows
    named = (("train", w.train), ("validation", w.validation), ("test", w.test))
    for n, (a, b) in named:
        if not (isinstance(a, date) and isinstance(b, date)) or a > b:
            p.append(f"{n} window must be (start <= end) dates")
    if p:
        raise LabError(p)
    for n, (a, b) in named:
        for lbl, d in (("start", a), ("end", b)):
            if _idx(cal, d) is None:
                p.append(f"{n} {lbl} {d} is not a session of calendar '{spec.calendar_source}'")
    if spec.purge_sessions < 0 or spec.embargo_sessions < 0:
        p.append("embargo and purge must be >= 0")
    elif hs:
        need = required_gap(hs, spec.purge_sessions)
        if spec.embargo_sessions < need:
            p.append(f"embargo_sessions {spec.embargo_sessions} < required {need} (longest label horizon incl. the 60-session forward window, + purge)")
    if not p:
        for (an, (_, ae)), (bn, (bs, _)) in zip(named, named[1:]):
            between = _idx(cal, bs) - _idx(cal, ae) - 1
            if _idx(cal, ae) >= _idx(cal, bs) or between < spec.embargo_sessions:
                p.append(f"only {max(between, 0)} sessions between {an} end and {bn} start; embargo is {spec.embargo_sessions}")
        mat = spec.label_maturity_session
        mi = _idx(cal, mat)
        te = _idx(cal, w.test[1])
        if mi is None:
            p.append(f"label_maturity_session {mat} is not a session of the calendar")
        elif hs and mi < te + max(hs) + VOID_GRACE_SESSIONS:
            p.append(f"label_maturity_session is only {mi - te} sessions after the test window; labels need {max(hs)} + {VOID_GRACE_SESSIONS}")
        if cutoff is not None:
            if cutoff < datetime(mat.year, mat.month, mat.day, tzinfo=timezone.utc):
                p.append("knowledge_cutoff_at precedes the label-maturity session")
            if cutoff > now.astimezone(timezone.utc):
                p.append("knowledge_cutoff_at is in the future: a dataset cannot read information that did not exist yet")
    if p:
        raise LabError(p)
    doc = {
        "schema": MANIFEST_SCHEMA, "dataset_name": spec.dataset_name, "dataset_version": spec.dataset_version, "code_sha": spec.code_sha,
        "label_version": spec.label_version, "label_methodology_version": spec.label_methodology_version, "label_horizons": sorted(hs),
        "feature_versions": dict(spec.feature_versions), "universe_id": spec.universe_id, "universe_hash": spec.universe_hash,
        "knowledge_cutoff_at": cutoff.isoformat(), "label_maturity_session": spec.label_maturity_session.isoformat(),
        "windows": {n: [a.isoformat(), b.isoformat()] for n, (a, b) in named},
        "embargo_sessions": spec.embargo_sessions, "purge_sessions": spec.purge_sessions, "calendar_source": spec.calendar_source,
        "calendar_hash": canonical_hash({"sessions": list(cal)}), "calendar_span": [cal[0].isoformat(), cal[-1].isoformat()],
        "input_hashes": dict(spec.input_hashes), "config": dict(spec.config),
    }
    _canon(doc)                                             # refuse NaN / unsupported types before hashing
    return Manifest(doc, canonical_hash(doc), cal)


def assign_split(m: Manifest, t0: date, horizon: int) -> str:
    """Which window a labelled row may be used in: 'train' | 'validation' | 'test', or 'purged' (its label window crosses a window edge),
    'embargo' (it falls between windows) or 'outside'. A row is usable only if t0 AND t0 + horizon sessions lie inside the SAME window."""
    cal = m.calendar
    if horizon not in m.document["label_horizons"]:
        raise LabError([f"horizon {horizon} is not a horizon of this dataset"])
    i = _idx(cal, t0)
    if i is None:
        raise LabError([f"{t0} is not a session of the manifest calendar"])
    end = i + horizon
    if end >= len(cal):
        return "purged"                                      # the label would read past the known calendar: not finalised
    for name in SPLITS:
        a, b = (date.fromisoformat(x) for x in m.document["windows"][name])
        if a <= t0 <= b:
            return name if cal[end] <= b else "purged"
    ws = [date.fromisoformat(x) for n in SPLITS for x in m.document["windows"][n]]
    return "outside" if t0 < ws[0] or t0 > ws[-1] else "embargo"


# ------------------------------------------------------------------ experiment registration
@dataclass(frozen=True)
class ExperimentSpec:
    experiment_name: str
    manifest: Manifest
    code_sha: str
    code_tree_clean: bool
    feature_versions: Mapping[str, str]
    model_spec: Mapping[str, Any]
    search_budget: int
    seed: int
    evaluation_plan: Mapping[str, Any]
    hypothesis: str


@dataclass(frozen=True)
class Registration:
    document: Dict[str, Any]
    registration_hash: str

    def row(self) -> Dict[str, Any]:
        d = self.document
        return {"experiment_name": d["experiment_name"], "registration_hash": self.registration_hash, "manifest_hash": d["manifest_hash"],
                "code_sha": d["code_sha"], "label_version": d["label_version"], "feature_versions": dict(d["feature_versions"]),
                "model_spec": dict(d["model_spec"]), "search_budget": d["search_budget"], "seed": d["seed"],
                "evaluation_plan": dict(d["evaluation_plan"]), "hypothesis": d["hypothesis"]}


def build_registration(spec: ExperimentSpec) -> Registration:
    p: List[str] = []
    m = spec.manifest.document
    if not spec.experiment_name:
        p.append("experiment_name is empty")
    if not SHA40.match(spec.code_sha or ""):
        p.append("code_sha must be a full 40-hex lowercase git commit")
    if spec.code_tree_clean is not True:
        p.append("the working tree is not clean")
    if not spec.feature_versions or any(m["feature_versions"].get(k) != v for k, v in spec.feature_versions.items()):
        p.append("feature_versions must be a non-empty subset of the manifest's, with identical versions")
    if not spec.model_spec or not isinstance(spec.model_spec, Mapping):
        p.append("model_spec (family + fixed configuration) is required")
    if not isinstance(spec.search_budget, int) or isinstance(spec.search_budget, bool) or spec.search_budget < 1:
        p.append("search_budget must be an integer >= 1 (the number of configurations the experiment may try)")
    if not isinstance(spec.seed, int) or isinstance(spec.seed, bool):
        p.append("seed must be an integer")
    plan = spec.evaluation_plan
    metrics = plan.get("metrics") if isinstance(plan, Mapping) else None
    if not metrics or not isinstance(metrics, (list, tuple)) or not all(isinstance(x, str) and x for x in metrics):
        p.append("evaluation_plan.metrics must be a non-empty list of metric names")
    elif plan.get("primary_metric") not in metrics:
        p.append("evaluation_plan.primary_metric must be one of the metrics")
    if not (isinstance(plan, Mapping) and plan.get("decision_rule")):
        p.append("evaluation_plan.decision_rule must be written down before results exist")
    if not (spec.hypothesis or "").strip():
        p.append("hypothesis is empty")
    if p:
        raise LabError(p)
    doc = {"schema": REGISTRATION_SCHEMA, "experiment_name": spec.experiment_name, "manifest_hash": spec.manifest.manifest_hash,
           "code_sha": spec.code_sha, "label_version": m["label_version"], "feature_versions": dict(spec.feature_versions),
           "model_spec": dict(spec.model_spec), "search_budget": spec.search_budget, "seed": spec.seed,
           "evaluation_plan": dict(plan), "hypothesis": spec.hypothesis}
    return Registration(doc, canonical_hash(doc))


@dataclass(frozen=True)
class ResultSpec:
    registration_hash: str
    result_kind: str
    metrics: Mapping[str, float]
    n_configs_tried: int
    artifact_hashes: Mapping[str, str]
    code_sha: str
    note: Optional[str] = None


def validate_result(spec: ResultSpec, reg: Registration, prior_kinds: Sequence[str]) -> Dict[str, Any]:
    """The same append rules the database enforces, checkable without one. Returns the insert row."""
    p: List[str] = []
    if spec.registration_hash != reg.registration_hash:
        p.append("result names a different registration")
    if spec.result_kind not in RESULT_KINDS:
        p.append(f"result_kind must be one of {RESULT_KINDS}")
    scored = spec.result_kind in ("validation", "test")
    if scored != bool(spec.metrics):
        p.append("validation/test results need metrics; failed/abandoned must carry none")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in spec.metrics.values()):
        p.append("every metric must be a finite number (an unmeasured metric is absent, never 0 or NaN)")
    elif scored and not set(reg.document["evaluation_plan"]["metrics"]) <= set(spec.metrics):
        p.append("a scored result must report every registered metric")
    if not isinstance(spec.n_configs_tried, int) or spec.n_configs_tried < 0 or spec.n_configs_tried > reg.document["search_budget"]:
        p.append(f"n_configs_tried must be within the registered search_budget {reg.document['search_budget']}")
    if any(not SHA256.match(str(h)) for h in spec.artifact_hashes.values()):
        p.append("artifact_hashes must be sha256 digests")
    if not SHA40.match(spec.code_sha or ""):
        p.append("code_sha must be a full 40-hex lowercase git commit")
    if spec.result_kind == "test":
        if "test" in prior_kinds:
            p.append("the test window may be evaluated once per experiment; a re-test is a new experiment")
        if "validation" not in prior_kinds:
            p.append("a test result needs a prior validation result")
    if scored and any(k in prior_kinds for k in ("failed", "abandoned")):
        p.append("the experiment was already closed as failed/abandoned")
    if p:
        raise LabError(p)
    return {"registration_hash": spec.registration_hash, "result_kind": spec.result_kind, "metrics": dict(spec.metrics),
            "n_configs_tried": spec.n_configs_tried, "artifact_hashes": dict(spec.artifact_hashes), "code_sha": spec.code_sha, "note": spec.note}
