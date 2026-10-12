"""Manifest authoring and the portable manifest FILE (pure: no database, clock, file or environment access).

Two documents:

* the **authoring spec** (`lab_authoring_spec_v1`) -- what a person writes: dataset identity, label identity, windows, cutoff, maturity,
  embargo / purge, where the trading calendar comes from, and the strict dataset `config` (`lab_dataset_config_v1`). It is parsed strictly:
  an unknown key, a missing key or a malformed value is reported (all problems at once), never defaulted. It deliberately has no
  `code_sha`, no input hashes and no creation time: those are taken from the running code and the database by the tool, not by hand.
* the **manifest file** (`lab_manifest_file_v1`) -- the authored manifest plus what is needed to re-validate it anywhere: the explicit
  calendar and the database time it was authored at. `parse_manifest_file` re-runs the full manifest validation (the same
  `revalidate_manifest` the registry path uses), so a hand-edited or truncated file fails closed instead of building something else.

Nothing here decides what a manifest means: `dataset_contract` / `manifest` own that.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from research.lab import dataset_contract as C
from research.lab import manifest as M
from research.lab.manifest import LabError

AUTHORING_SCHEMA = "lab_authoring_spec_v1"
MANIFEST_FILE_SCHEMA = "lab_manifest_file_v1"
PROVISIONAL_INPUT_HASHES = {"labels": "0" * 64}

_REQUIRED = ("schema", "dataset_name", "dataset_version", "label_version", "label_methodology_version", "label_horizons", "feature_versions",
             "universe_id", "knowledge_cutoff_at", "label_maturity_session", "windows", "embargo_sessions", "purge_sessions", "calendar",
             "universe", "config")


@dataclass(frozen=True)
class Authoring:
    dataset_name: str
    dataset_version: str
    label_version: str
    label_methodology_version: str
    label_horizons: Tuple[int, ...]
    feature_versions: Dict[str, str]
    universe_id: str
    knowledge_cutoff_at: datetime
    label_maturity_session: date
    windows: M.Windows
    embargo_sessions: int
    purge_sessions: int
    calendar_file: Optional[str]                    # explicit session list
    calendar_derive: Optional[Tuple[date, date, str]]  # (start, end, benchmark symbol) -> derived from stock_prices
    universe_from_db: bool
    config: Dict[str, Any]


def _date(v: Any, what: str, p: List[str]) -> Optional[date]:
    try:
        if isinstance(v, str):
            return date.fromisoformat(v)
    except ValueError:
        pass
    p.append(f"{what} must be an ISO date (YYYY-MM-DD)")
    return None


def _utc(v: Any, what: str, p: List[str]) -> Optional[datetime]:
    try:
        if isinstance(v, str):
            d = datetime.fromisoformat(v)
            if d.tzinfo is not None:
                return d.astimezone(timezone.utc)
    except ValueError:
        pass
    p.append(f"{what} must be an ISO timestamp WITH a UTC offset (e.g. 2026-10-02T21:00:00+00:00)")
    return None


def _window(v: Any, what: str, p: List[str]) -> Optional[Tuple[date, date]]:
    if not (isinstance(v, (list, tuple)) and len(v) == 2):
        p.append(f"{what} must be [start, end]")
        return None
    a, b = _date(v[0], f"{what}[0]", p), _date(v[1], f"{what}[1]", p)
    return (a, b) if a and b else None


def _isint(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def parse_authoring(obj: Any) -> Authoring:
    """Strict parse of an authoring spec. Every problem is collected and raised together."""
    if not isinstance(obj, Mapping):
        raise LabError(["the authoring spec must be a JSON object"])
    p: List[str] = []
    if obj.get("schema") != AUTHORING_SCHEMA:
        p.append(f"schema must be '{AUTHORING_SCHEMA}'")
    for k in _REQUIRED:
        if k not in obj:
            p.append(f"missing key '{k}'")
    for k in sorted(set(obj) - set(_REQUIRED)):
        p.append(f"unknown key '{k}' (an authoring spec has no code_sha, input hashes or creation time: the tool derives those)")
    s = lambda k: obj.get(k)  # noqa: E731
    for k in ("dataset_name", "dataset_version", "label_version", "label_methodology_version", "universe_id"):
        if k in obj and not (isinstance(s(k), str) and s(k).strip()):
            p.append(f"{k} must be a non-empty string")
    hz = s("label_horizons")
    if "label_horizons" in obj and not (isinstance(hz, list) and hz and all(_isint(h) and h > 0 for h in hz)):
        p.append("label_horizons must be a non-empty list of positive integers")
    fv = s("feature_versions")
    if "feature_versions" in obj and not (isinstance(fv, Mapping) and fv and all(isinstance(k, str) and isinstance(v, str) for k, v in fv.items())):
        p.append("feature_versions must be a non-empty {name: version} mapping of strings")
    for k in ("embargo_sessions", "purge_sessions"):
        if k in obj and not (_isint(s(k)) and s(k) >= 0):
            p.append(f"{k} must be a non-negative integer")
    cutoff = _utc(s("knowledge_cutoff_at"), "knowledge_cutoff_at", p) if "knowledge_cutoff_at" in obj else None
    maturity = _date(s("label_maturity_session"), "label_maturity_session", p) if "label_maturity_session" in obj else None
    w = s("windows")
    windows = None
    if "windows" in obj:
        if not (isinstance(w, Mapping) and set(w) == set(M.SPLITS)):
            p.append(f"windows must have exactly the keys {list(M.SPLITS)}")
        else:
            got = [_window(w[k], f"windows.{k}", p) for k in M.SPLITS]
            windows = M.Windows(*got) if all(got) else None
    cal = s("calendar")
    cal_file, cal_derive = None, None
    if "calendar" in obj:
        if isinstance(cal, Mapping) and set(cal) == {"file"} and isinstance(cal["file"], str) and cal["file"].strip():
            cal_file = cal["file"]
        elif isinstance(cal, Mapping) and set(cal) <= {"derive"} and isinstance(cal.get("derive"), Mapping):
            d = cal["derive"]
            extra = sorted(set(d) - {"start", "end", "benchmark_symbol"})
            if extra or "start" not in d or "end" not in d:
                p.append("calendar.derive needs start and end (and optionally benchmark_symbol) and nothing else")
            else:
                a, b = _date(d["start"], "calendar.derive.start", p), _date(d["end"], "calendar.derive.end", p)
                bench = d.get("benchmark_symbol", "^GSPC")
                if not (isinstance(bench, str) and bench):
                    p.append("calendar.derive.benchmark_symbol must be a non-empty string")
                elif a and b:
                    cal_derive = (a, b, bench)
        else:
            p.append("calendar must be {\"file\": <path of one ISO session date per line>} or {\"derive\": {\"start\", \"end\"[, \"benchmark_symbol\"]}}")
    uni = s("universe")
    from_db = False
    if "universe" in obj:
        if uni == {"from_db": True}:
            from_db = True
        elif uni == {"from_config": True}:
            from_db = False
        else:
            p.append("universe must be {\"from_db\": true} (the distinct symbols observed for the strategy) or {\"from_config\": true} (config.universe_members)")
    cfg = s("config")
    if "config" in obj:
        if not isinstance(cfg, Mapping):
            p.append("config must be an object")
        elif from_db and "universe_members" in cfg:
            p.append("config.universe_members must be omitted when universe is {\"from_db\": true}")
        elif not from_db and "universe_members" not in cfg and "universe" in obj and uni == {"from_config": True}:
            p.append("config.universe_members is required when universe is {\"from_config\": true}")
    if p:
        raise LabError(p)
    return Authoring(obj["dataset_name"], obj["dataset_version"], obj["label_version"], obj["label_methodology_version"], tuple(hz), dict(fv),
                     obj["universe_id"], cutoff, maturity, windows, obj["embargo_sessions"], obj["purge_sessions"], cal_file, cal_derive,
                     from_db, dict(cfg))


def parse_calendar_text(text: str) -> Tuple[date, ...]:
    """One ISO date per line; blank lines and `#` comments are ignored. Order / uniqueness are validated by the manifest, not repaired here."""
    out: List[date] = []
    p: List[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        t = line.split("#", 1)[0].strip()
        if not t:
            continue
        try:
            out.append(date.fromisoformat(t))
        except ValueError:
            p.append(f"calendar line {n}: '{t}' is not an ISO date")
    if p:
        raise LabError(p)
    if not out:
        raise LabError(["the calendar file lists no sessions"])
    return tuple(out)


def to_spec(a: Authoring, *, code_sha: str, code_tree_clean: bool, calendar: Sequence[date], calendar_source: str,
            universe_members: Optional[Sequence[str]], input_hashes: Mapping[str, str]) -> M.DatasetSpec:
    cfg = dict(a.config)
    if a.universe_from_db:
        if not universe_members:
            raise LabError(["the database holds no candidate observations for this strategy in the span: the universe would be empty"])
        cfg["universe_members"] = sorted(set(universe_members))
    members = cfg.get("universe_members")
    if not isinstance(members, list):
        raise LabError(["config.universe_members must be a list of symbols"])
    return M.DatasetSpec(
        dataset_name=a.dataset_name, dataset_version=a.dataset_version, code_sha=code_sha, code_tree_clean=code_tree_clean,
        label_version=a.label_version, label_methodology_version=a.label_methodology_version, label_horizons=a.label_horizons,
        feature_versions=dict(a.feature_versions), universe_id=a.universe_id, universe_hash=M.universe_hash(list(members), str(cfg.get("universe_rule"))),
        knowledge_cutoff_at=a.knowledge_cutoff_at, label_maturity_session=a.label_maturity_session, windows=a.windows,
        embargo_sessions=a.embargo_sessions, purge_sessions=a.purge_sessions, calendar_source=calendar_source, calendar=tuple(calendar),
        input_hashes=dict(input_hashes), config=cfg)


# ------------------------------------------------------------------ the manifest file
def make_manifest_file(manifest: M.Manifest, authored_at: datetime) -> Dict[str, Any]:
    if authored_at.tzinfo is None:
        raise LabError(["authored_at must be timezone-aware"])
    return {"schema": MANIFEST_FILE_SCHEMA, "manifest_hash": manifest.manifest_hash, "authored_at": authored_at.astimezone(timezone.utc).isoformat(),
            "calendar": [d.isoformat() for d in manifest.calendar], "manifest": manifest.document}


def parse_manifest_file(obj: Any) -> Tuple[M.Manifest, datetime]:
    """Fail closed unless the file is a complete `lab_manifest_file_v1` whose document re-validates in full and re-derives its own hash from
    the calendar it carries. Returns (manifest, authored_at)."""
    if not isinstance(obj, Mapping):
        raise LabError(["the manifest file must be a JSON object"])
    p: List[str] = []
    if obj.get("schema") != MANIFEST_FILE_SCHEMA:
        p.append(f"schema must be '{MANIFEST_FILE_SCHEMA}'")
    keys = {"schema", "manifest_hash", "authored_at", "calendar", "manifest"}
    p += [f"missing key '{k}'" for k in sorted(keys - set(obj))] + [f"unknown key '{k}'" for k in sorted(set(obj) - keys)]
    if p:
        raise LabError(p)
    authored = _utc(obj["authored_at"], "authored_at", p)
    cal = obj["calendar"]
    calendar: List[date] = []
    if not isinstance(cal, list):
        p.append("calendar must be a list of ISO dates")
    else:
        calendar = [d for d in (_date(x, "calendar entry", p) for x in cal) if d]
    if not (isinstance(obj["manifest_hash"], str) and M.SHA256.match(obj["manifest_hash"])):
        p.append("manifest_hash must be a sha256 hex digest")
    if not isinstance(obj["manifest"], Mapping):
        p.append("manifest must be an object")
    if p:
        raise LabError(p)
    m = C.revalidate_manifest(obj["manifest"], obj["manifest_hash"], calendar, authored)
    return m, authored


def dump_json(doc: Any) -> str:
    """The one JSON form every machine-readable artifact is written in: sorted keys, ASCII, dates/timestamps as ISO (the same `_canon` the
    hashes use, so NaN / naive datetimes are refused rather than written), trailing newline. Byte-identical for equal documents."""
    return json.dumps(M._canon(doc), sort_keys=True, indent=2, ensure_ascii=True) + chr(10)
