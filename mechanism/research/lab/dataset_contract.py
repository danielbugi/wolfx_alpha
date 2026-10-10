"""The dataset contract (pure): what a manifest-driven research dataset IS, byte for byte.

Everything here is a pure function of its arguments (no psycopg2, clock, environment, file or network): the builder's database layer only
supplies rows, and this module defines how they are validated, canonicalised and hashed.

* `DatasetConfig` / `parse_config` -- the manifest's `config` document, parsed fail-closed. Every field is explicit (no defaults that change
  what is read); a missing or inconsistent one raises `LabError` listing every problem.
* `COLUMNS` -- the canonical column order and type of the dataset. One row = one (candidate observation, label horizon).
* `dataset_hash` -- sha256 of a canonical byte stream: a header (schema, manifest hash, label/methodology identity, feature versions,
  ordered columns, row count) then one JSON array per row in canonical row order. NULL is JSON `null` in every column (never "", 0 or NaN);
  floats are shortest-repr JSON numbers (NaN/inf refused); dates ISO; timestamps UTC ISO with microseconds.
* Input verification. Two kinds, never mixed:
    - CRYPTOGRAPHIC inputs are recomputed from content the caller holds (the explicit calendar, the universe list, the dataset-schema
      contract) and compared with the manifest.
    - DATABASE-DERIVED inputs are verified by a deterministic QUERY FINGERPRINT: the sha256 of the rows a fixed, versioned query returns
      for rows that already existed at the knowledge cutoff. Immutable append-only tables make that fingerprint stable under later appends
      and sensitive to any change of history. It proves "the database still says what it said"; it does not prove the vendor/source was right.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from research.lab import manifest as M
from research.lab import sector_provenance as SP
from research.lab.manifest import LabError, canonical_hash

DATASET_SCHEMA = "lab_dataset_v2"
LEGACY_DATASET_SCHEMAS = ("lab_dataset_v1",)
CONFIG_SCHEMA = "lab_dataset_config_v1"
RECONSTRUCTED_POLICIES = ("exclude", "include_flagged")

# ------------------------------------------------------------------ states (every optional source column carries one)
OK = "ok"                                  # a value from an eligible row was used
ABSENT = "absent"                          # no row exists for this key
LATE = "late"                              # a row exists but became available after the row's decision deadline: masked, never used
UNAVAILABLE = "unavailable"                # the source itself says the value is unavailable (stored NULL)
RECONSTRUCTED_EXCLUDED = "reconstructed_excluded"
NO_SECTOR = "no_sector"
SECTOR_NAME_LATE = "sector_name_late"      # the candidate's own sector attribute was stamped after the decision deadline: not used
SECTOR_ASOF_AFTER_T0 = "sector_asof_after_t0"
SECTOR_STALE = "sector_stale"              # the candidate's sector source row is older than SP.SECTOR_MAX_AGE_DAYS: not used (v2)
SECTOR_UNKNOWN_PROVENANCE = "sector_unknown_provenance"   # a sector with no source / no source date: fails closed, an audit finding (v2)
NOT_ENABLED = "not_enabled"                # the manifest does not configure this source
UNKNOWN_AVAILABILITY = "unknown_availability"
MISSING = "missing"                        # label state only: no label row at this horizon

# ------------------------------------------------------------------ columns
COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("observation_key", "str"), ("strategy_key", "str"), ("strategy_version", "str"), ("symbol", "str"), ("t0_session", "date"),
    ("direction", "int"), ("horizon_sessions", "int"), ("horizon_session", "date"), ("split", "str"),
    ("signal_type", "str"), ("triggered", "bool"), ("passed_guard", "bool"), ("tracked_intent", "bool"), ("atr_source", "str"),
    ("breakout_dist_atr", "float"), ("distance_to_channel_pct", "float"), ("quality_grade", "str"), ("alignment_score", "float"),
    ("candidate_available_at", "ts"),
    ("label_status", "str"), ("void_reason", "str"), ("raw_return", "float"), ("directional_return", "float"),
    ("benchmark_state", "str"), ("directional_excess_return", "float"), ("path_state", "str"), ("mfe", "float"), ("mae", "float"),
    ("data_quality", "str"), ("label_input_hash", "str"), ("label_available_at", "ts"),
    ("market__state", "str"), ("market__provenance", "str"), ("regime_state", "str"), ("regime_score", "float"),
    ("regime_strength", "float"), ("market_available_at", "ts"),
    ("breadth__state", "str"), ("breadth_sma50_pct", "float"), ("breadth_sma200_pct", "float"), ("net_highs_lows_pct", "float"),
    ("sector", "str"), ("sector__state", "str"), ("sector__provenance", "str"), ("sector_ret_20", "float"),
    ("sector_vs_spx_20", "float"), ("sector_vs_univ_20", "float"), ("sector_rank_20", "int"), ("sector_available_at", "ts"),
    ("rs__state", "str"), ("rs__provenance", "str"), ("rs_ret_pct", "float"), ("rs_vs_spx_pp", "float"), ("rs_vs_sector_pp", "float"),
    ("rs_percentile", "float"), ("rs_n_universe", "int"), ("rs_sector_pit_safe", "bool"), ("rs_available_at", "ts"),
    ("rs_sector", "str"), ("rs_vs_sector__state", "str"),
    ("catalyst__state", "str"), ("catalyst_n_events", "int"), ("catalyst_n_unknown_availability", "int"), ("catalyst_labels", "str"),
    ("catalyst_latest_event_time", "date"), ("catalyst_available_at", "ts"),
    ("first_seen__state", "str"), ("first_seen_json", "str"), ("first_seen_available_at", "ts"),
)
COLUMN_NAMES = tuple(n for n, _ in COLUMNS)
COLUMN_TYPES = dict(COLUMNS)
assert len(set(COLUMN_NAMES)) == len(COLUMN_NAMES)

# ------------------------------------------------------------------ frozen identity of the previous contract
# `lab_dataset_v1` had no sector-relative cell state (a no-sector symbol could only be told apart by sector_pit_safe = false). v2 adds exactly the
# two columns below. The v1 identity is kept so a v1 manifest / dataset hash is still *recognised* (and reproducible: project the rows onto the
# v1 columns). A test pins schema_hash('lab_dataset_v1'); it must never change.
V2_ADDED_COLUMNS = ("rs_sector", "rs_vs_sector__state")
COLUMNS_V1: Tuple[Tuple[str, str], ...] = tuple(c for c in COLUMNS if c[0] not in V2_ADDED_COLUMNS)
COLUMNS_BY_SCHEMA: Dict[str, Tuple[Tuple[str, str], ...]] = {"lab_dataset_v1": COLUMNS_V1, "lab_dataset_v2": COLUMNS}


def _columns_of(schema: str) -> Tuple[Tuple[str, str], ...]:
    if schema not in COLUMNS_BY_SCHEMA:
        raise LabError([f"unknown dataset schema '{schema}' (known: {sorted(COLUMNS_BY_SCHEMA)})"])
    return COLUMNS_BY_SCHEMA[schema]

# The fixed sources. `state_column` names the column that carries the source's state in the dataset.
SOURCE_STATE_COLUMNS = {"market": "market__state", "breadth": "breadth__state", "sector": "sector__state", "stock_rs": "rs__state",
                        "catalyst": "catalyst__state", "first_seen": "first_seen__state"}
# (source, availability column, provenance column or None)
AVAILABILITY_COLUMNS = (("candidate", "candidate_available_at", None), ("label", "label_available_at", None),
                        ("market", "market_available_at", "market__provenance"), ("sector", "sector_available_at", "sector__provenance"),
                        ("stock_rs", "rs_available_at", "rs__provenance"), ("catalyst", "catalyst_available_at", None),
                        ("first_seen", "first_seen_available_at", None))

# Columns each database read returns, in order. A change here is a change of the contract (it moves `contract.dataset_schema`).
SOURCE_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "candidates": ("strategy_key", "strategy_version", "symbol", "session_date", "direction", "signal_type", "triggered", "passed_guard",
                   "tracked_intent", "atr_source", "breakout_dist_atr", "distance_to_channel_pct", "quality_grade", "alignment_score",
                   "captured_at", "fs_sector", "fs_sector_source", "fs_sector_asof", "fs_captured_at"),
    "labels": ("strategy_key", "strategy_version", "symbol", "t0_session", "direction", "horizon_sessions", "label_version",
               "methodology_version", "label_status", "void_reason", "horizon_session", "computed_as_of_session", "calendar_source",
               "raw_return", "directional_return", "benchmark_state", "directional_excess_return", "path_state", "mfe", "mae",
               "data_quality", "input_hash", "computed_at"),
    "market": ("session_date", "provenance", "feature_set_version", "regime_model_version", "rs_model_version", "regime_state",
               "regime_score", "regime_strength", "regime_components", "content_hash", "captured_at"),
    "sector": ("session_date", "provenance", "feature_set_version", "sector", "n_valid_20", "sec_ret_20", "sec_vs_spx_20",
               "sec_vs_univ_20", "rank_20", "captured_at"),
    "stock_rs": ("session_date", "symbol", "horizon_sessions", "model_version", "feature_set_version", "provenance", "sector",
                 "sector_pit_safe", "state", "ret_pct", "vs_spx_pp", "vs_sector_pp", "rs_percentile", "n_universe_valid",
                 "run_content_hash", "created_at"),
    "events": ("event_key", "symbol", "event_type", "revision", "event_time", "known_at", "known_at_basis", "pit_grade", "ingested_at",
               "status", "provenance", "payload_hash"),
    "classifications": ("event_key", "revision", "classifier", "classifier_version", "method", "label", "confidence", "fact_hash",
                        "classified_at"),
    "first_seen": ("series_key", "source", "dataset", "subject_id", "period_key", "seq", "value", "value_hash", "observed_at"),
}
# The column of each source that says when that row became available to a reader (the row's own PIT stamp). For events the stamp is
# per-grade (see `event_available_at`); `ingested_at` is the stamp every revision has and the one the fingerprint is bounded by.
AVAILABILITY_FIELD: Dict[str, str] = {"candidates": "captured_at", "labels": "computed_at", "market": "captured_at", "sector": "captured_at",
                                      "stock_rs": "created_at", "events": "ingested_at", "classifications": "classified_at",
                                      "first_seen": "observed_at"}
# How much each stamp can be trusted. writer_default = a DEFAULT NOW() the inserting process may set explicitly; db_stamped = a trigger
# sets clock_timestamp() and the writer cannot override it.
AVAILABILITY_TRUST: Dict[str, str] = {"candidates": "writer_default", "labels": "writer_default", "market": "writer_default",
                                      "sector": "writer_default", "stock_rs": "db_stamped", "events": "db_stamped",
                                      "classifications": "db_stamped", "first_seen": "db_stamped"}
CATALYST_KNOWN, CATALYST_UNCLASSIFIED, CATALYST_NONE_OBSERVED = "catalyst_known", "event_unclassified", "none_observed"

# Bumping a query's version changes every fingerprint that depends on it (and `contract.dataset_schema`).
QUERY_VERSIONS: Dict[str, str] = {k: "q1" for k in SOURCE_COLUMNS}

# input_hashes vocabulary
CRYPTO_INPUTS = ("contract.dataset_schema", "calendar.sessions", "universe.members")
DB_INPUTS: Dict[str, str] = {"candidates": "db.candidate_observation", "labels": "db.forward_return_label", "market": "db.market_snapshot",
                             "sector": "db.sector_snapshot", "stock_rs": "db.stock_relative_strength", "events": "db.market_event",
                             "classifications": "db.catalyst_classification", "first_seen": "db.source_observation",
                             "sector_history": "db.sector_history"}

# ------------------------------------------------------------------ append-only sector history (Slice 10, opt-in, outside the v1/v2 contract)
# The history read is NOT part of SOURCE_COLUMNS / QUERY_VERSIONS: those two feed `schema_hash`, so adding the history there would silently change the
# identity of every existing lab_dataset_v1 / v2 manifest. It is an opt-in config section (`sector_history`) with its own registry, query version and
# input key (`db.sector_history`); a config without the section is read, assembled, audited and fingerprinted exactly as before.
HISTORY_SOURCE = "sector_history"
AUTHORITATIVE_HISTORY_SOURCE = "yfinance_info"   # the only source that may carry forward sector IDENTITY (policy A, Slice 11); the diagnostic chain never can
HISTORY_COLUMNS: Tuple[str, ...] = ("row_kind", "symbol", "source", "seq", "sector", "sector_raw", "no_sector_reason", "change_kind", "stamp",
                                    "effective_session", "source_asof", "provenance", "raw_payload_hash", "prev_value_hash", "value_hash", "run_id")
HISTORY_QUERY_VERSION = "h1"


def source_columns(source: str) -> Tuple[str, ...]:
    return HISTORY_COLUMNS if source == HISTORY_SOURCE else SOURCE_COLUMNS[source]


def source_stamp(source: str) -> str:
    return "stamp" if source == HISTORY_SOURCE else AVAILABILITY_FIELD[source]


def source_trust(source: str) -> str:
    return "db_stamped" if source == HISTORY_SOURCE else AVAILABILITY_TRUST[source]


def source_query_version(source: str) -> str:
    return HISTORY_QUERY_VERSION if source == HISTORY_SOURCE else QUERY_VERSIONS[source]


# ------------------------------------------------------------------ config
@dataclass(frozen=True)
class RsConfig:
    model_version: str
    feature_set_version: str
    horizon_sessions: int


@dataclass(frozen=True)
class CatalystConfig:
    lookback_days: int
    classifier: str
    classifier_version: str


@dataclass(frozen=True)
class FirstSeenSpec:
    source: str
    dataset: str
    fields: Tuple[str, ...]


@dataclass(frozen=True)
class SectorHistoryConfig:
    source: str                     # the vendor/source label of the chain to read (a chain is per (symbol, source))


@dataclass(frozen=True)
class DatasetConfig:
    strategy_key: str
    strategy_version: str
    universe_rule: str
    universe_members: Tuple[str, ...]
    availability_grace_days: int
    min_sample: int
    primary_horizon: int
    reconstructed_policy: str
    market_feature_set_version: Optional[str]
    sector_feature_set_version: Optional[str]
    stock_rs: Optional[RsConfig]
    catalyst: Optional[CatalystConfig]
    first_seen: Tuple[FirstSeenSpec, ...]
    sector_history: Optional[SectorHistoryConfig] = None

    def enabled(self) -> Dict[str, bool]:
        out = {"market": self.market_feature_set_version is not None, "breadth": self.market_feature_set_version is not None,
               "sector": self.sector_feature_set_version is not None, "stock_rs": self.stock_rs is not None,
               "catalyst": self.catalyst is not None, "first_seen": bool(self.first_seen)}
        if self.sector_history is not None:                                # present only when opted in: existing specs see the same dict as before
            out[HISTORY_SOURCE] = True
        return out


def _isint(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _nonempty(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def parse_config(config: Mapping[str, Any], label_horizons: Sequence[int]) -> DatasetConfig:
    """Parse the manifest's `config`. Fail closed: every problem is collected and raised together."""
    p: List[str] = []
    if not isinstance(config, Mapping):
        raise LabError(["manifest config must be a mapping"])
    if config.get("schema") != CONFIG_SCHEMA:
        p.append(f"config.schema must be '{CONFIG_SCHEMA}'")
    strat = config.get("strategy")
    if not (isinstance(strat, Mapping) and _nonempty(strat.get("key")) and _nonempty(strat.get("version"))):
        p.append("config.strategy needs a key and a version")
        strat = {"key": "", "version": ""}
    if not _nonempty(config.get("universe_rule")):
        p.append("config.universe_rule is required (the rule that produced the universe list)")
    um = config.get("universe_members")
    if not (isinstance(um, (list, tuple)) and um and all(_nonempty(x) for x in um) and len(set(um)) == len(um) and list(um) == sorted(um)):
        p.append("config.universe_members must be a non-empty, sorted, duplicate-free list of symbols (the manifest carries its universe)")
        um = []
    for name, lo in (("availability_grace_days", 0), ("min_sample", 2)):
        v = config.get(name)
        if not _isint(v) or v < lo:
            p.append(f"config.{name} must be an integer >= {lo} (explicit, never defaulted)")
    ph = config.get("primary_horizon")
    if ph not in tuple(label_horizons):
        p.append(f"config.primary_horizon must be one of the manifest's label horizons {tuple(label_horizons)}")
    policy = config.get("reconstructed_policy")
    if policy not in RECONSTRUCTED_POLICIES:
        p.append(f"config.reconstructed_policy must be one of {RECONSTRUCTED_POLICIES}")

    def fsv(section: str) -> Optional[str]:
        s = config.get(section)
        if s is None:
            return None
        if not (isinstance(s, Mapping) and _nonempty(s.get("feature_set_version"))):
            p.append(f"config.{section} needs a feature_set_version (or omit the section to disable the source)")
            return None
        return s["feature_set_version"]

    market, sector = fsv("market"), fsv("sector")
    if sector is not None and market is None:
        p.append("config.sector needs config.market (a sector comparison is read against the market snapshot of the same session)")
    rs = None
    s = config.get("stock_rs")
    if s is not None:
        if isinstance(s, Mapping) and _nonempty(s.get("model_version")) and _nonempty(s.get("feature_set_version")) \
                and _isint(s.get("horizon_sessions")) and s["horizon_sessions"] > 0:
            rs = RsConfig(s["model_version"], s["feature_set_version"], s["horizon_sessions"])
        else:
            p.append("config.stock_rs needs model_version, feature_set_version and a positive integer horizon_sessions")
    cat = None
    s = config.get("catalyst")
    if s is not None:
        if isinstance(s, Mapping) and _isint(s.get("lookback_days")) and s["lookback_days"] > 0 and _nonempty(s.get("classifier")) \
                and _nonempty(s.get("classifier_version")):
            cat = CatalystConfig(s["lookback_days"], s["classifier"], s["classifier_version"])
        else:
            p.append("config.catalyst needs a positive integer lookback_days, a classifier and a classifier_version")
    fs_specs: List[FirstSeenSpec] = []
    raw_fs = config.get("first_seen", [])
    if not isinstance(raw_fs, (list, tuple)):
        p.append("config.first_seen must be a list of {source, dataset, fields}")
        raw_fs = []
    for i, s in enumerate(raw_fs):
        if isinstance(s, Mapping) and _nonempty(s.get("source")) and _nonempty(s.get("dataset")) \
                and isinstance(s.get("fields"), (list, tuple)) and s["fields"] and all(_nonempty(f) for f in s["fields"]):
            fs_specs.append(FirstSeenSpec(s["source"], s["dataset"], tuple(sorted(set(s.get("fields", []))))))
        else:
            p.append(f"config.first_seen[{i}] needs a source, a dataset and a non-empty list of field names")
    keys = [(f.source, f.dataset) for f in fs_specs]
    if len(set(keys)) != len(keys):
        p.append("config.first_seen lists a (source, dataset) twice")
    hist = None
    s = config.get("sector_history")
    if s is not None:
        if isinstance(s, Mapping) and _nonempty(s.get("source")) and set(s) == {"source"}:
            hist = SectorHistoryConfig(s["source"].strip())
            if hist.source != AUTHORITATIVE_HISTORY_SOURCE:
                p.append(f"config.sector_history.source must be the authoritative source {AUTHORITATIVE_HISTORY_SOURCE!r}: a diagnostic or unknown source never carries sector identity")
        else:
            p.append("config.sector_history needs exactly a non-empty source (or omit the section to keep the candidate-bounded sector evidence only)")
        if sector is None and rs is None:
            p.append("config.sector_history cross-checks the sector evidence of config.sector / config.stock_rs: enable at least one of them")
    if p:
        raise LabError(p)
    return DatasetConfig(strat["key"], strat["version"], config["universe_rule"], tuple(um), config["availability_grace_days"], config["min_sample"],
                         ph, policy, market, sector, rs, cat, tuple(sorted(fs_specs, key=lambda f: (f.source, f.dataset))), hist)


def config_for(doc: Mapping[str, Any]) -> DatasetConfig:
    """Parse a manifest document's config AND check it against the rest of the manifest: every feature_set_version the config reads must be
    one the manifest declares in `feature_versions` (a dataset cannot read a feature set its manifest does not name)."""
    cfg = parse_config(doc.get("config"), doc.get("label_horizons", ()))
    declared = doc.get("feature_versions") or {}
    p = [f"config reads feature_set_version '{v}' which the manifest's feature_versions {sorted(declared)} does not declare"
         for v in sorted({cfg.market_feature_set_version, cfg.sector_feature_set_version, cfg.stock_rs.feature_set_version if cfg.stock_rs else None}
                         - {None}) if v not in declared]
    if p:
        raise LabError(p)
    return cfg


def required_input_keys(cfg: DatasetConfig) -> Tuple[str, ...]:
    keys = list(CRYPTO_INPUTS) + [DB_INPUTS["candidates"], DB_INPUTS["labels"]]
    if cfg.market_feature_set_version is not None:
        keys.append(DB_INPUTS["market"])
    if cfg.sector_feature_set_version is not None:
        keys.append(DB_INPUTS["sector"])
    if cfg.stock_rs is not None:
        keys.append(DB_INPUTS["stock_rs"])
    if cfg.catalyst is not None:
        keys += [DB_INPUTS["events"], DB_INPUTS["classifications"]]
    if cfg.first_seen:
        keys.append(DB_INPUTS["first_seen"])
    if cfg.sector_history is not None:
        keys.append(DB_INPUTS[HISTORY_SOURCE])
    return tuple(keys)


def enabled_db_sources(cfg: DatasetConfig) -> Tuple[str, ...]:
    out = ["candidates", "labels"]
    if cfg.market_feature_set_version is not None:
        out.append("market")
    if cfg.sector_feature_set_version is not None:
        out.append("sector")
    if cfg.stock_rs is not None:
        out.append("stock_rs")
    if cfg.catalyst is not None:
        out += ["events", "classifications"]
    if cfg.first_seen:
        out.append("first_seen")
    if cfg.sector_history is not None:
        out.append(HISTORY_SOURCE)
    return tuple(out)


# ------------------------------------------------------------------ time rules
def is_known(avail: Optional[datetime], t0: date, grace_days: int, cutoff: datetime) -> bool:
    """THE point-in-time rule: a value is known for an observation at session `t0` iff its availability stamp exists, is not after the manifest's
    knowledge cutoff, and is strictly before the decision deadline. Anything else is masked, never used."""
    if avail is None:
        return False
    return avail <= cutoff and avail < decision_deadline(t0, grace_days)


def event_available_at(pit_grade: str, known_at: Optional[datetime], ingested_at: Optional[datetime]) -> Optional[datetime]:
    """Availability of an event revision: A/B = its proven `known_at`; C = our ingestion time (the vendor's own time may be revised in place and
    is not trusted); X (or anything unrecognised) = unknown -> None."""
    if pit_grade in ("A", "B"):
        return known_at
    if pit_grade == "C":
        return ingested_at
    return None


def decision_deadline(t0: date, grace_days: int) -> datetime:
    """The instant after which nothing counts as known for an observation at session `t0`: the end of t0's UTC day plus `grace_days` calendar
    days. Explicit, declared in the manifest config, and applied identically in assembly and in the independent audit."""
    d = t0 + timedelta(days=1 + grace_days)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def effective_deadline(t0: date, grace_days: int, cutoff: datetime) -> datetime:
    return min(decision_deadline(t0, grace_days), cutoff.astimezone(timezone.utc))


# ------------------------------------------------------------------ sector decisions (v2)
# State of `rs_vs_sector__state` -- the cell the sector-relative RS value lives in. Only OK is an observed, point-in-time-safe sector-relative
# measurement. NO_SECTOR is the owner-decided Option B: the candidate stays, the sector-relative value is NULL (never 0, never market-relative).
SECTOR_VALUE_UNAVAILABLE = "sector_value_unavailable"   # a fresh PIT-safe sector exists but the model produced no value (too few members)
SECTOR_NOT_PIT_SAFE = "sector_not_pit_safe"             # the RS row says its sector map was not PIT-safe; value NULL
SECTOR_UNCONFIRMED = "sector_unconfirmed"               # the candidate snapshot has no usable sector at the decision point; value masked
SECTOR_IDENTITY_CONFLICT = "sector_identity_conflict"   # the candidate's fresh sector differs from the RS row's sector; value masked
SECTOR_RECONSTRUCTED = "sector_reconstructed"           # reconstructed sector information: never safe
UNSAFE_VALUE = "unsafe_value"                           # a NON-NULL sector-relative value that cannot be proved safe: kept so readiness blocks it
SECTOR_REL_STATES = (OK, NO_SECTOR, SECTOR_VALUE_UNAVAILABLE, SECTOR_NOT_PIT_SAFE, SECTOR_UNCONFIRMED, SECTOR_IDENTITY_CONFLICT, SECTOR_STALE,
                     SECTOR_UNKNOWN_PROVENANCE, SECTOR_RECONSTRUCTED, UNSAFE_VALUE)
# states in which the dataset exposes the RS row's own sector tag in `rs_sector`
SECTOR_EXPOSED_STATES = (OK, SECTOR_VALUE_UNAVAILABLE, UNSAFE_VALUE, SECTOR_RECONSTRUCTED)


def candidate_sector_evidence(c: Mapping[str, Any], t0: date, known) -> SP.SectorEvidence:
    """The evidence class of the sector tag stored on the candidate's own snapshot, at decision session `t0`."""
    return SP.classify_sector_evidence(sector=c["fs_sector"], source=c["fs_sector_source"], asof=c["fs_sector_asof"], t0=t0,
                                       provenance="observed", available=known(c["fs_captured_at"]))


def sector_name_state(ev: SP.SectorEvidence) -> str:
    """`sector__state` for a candidate-level evidence class: OK means the sector name may be used to look up the sector snapshot."""
    if ev.state == SP.OBSERVED_FRESH:
        return OK
    if ev.state == SP.OBSERVED_STALE:
        return SECTOR_STALE
    if ev.state == SP.UNKNOWN:
        return SECTOR_UNKNOWN_PROVENANCE
    if ev.state == SP.UNAVAILABLE:
        return {SP.NO_SECTOR: NO_SECTOR, SP.NAME_LATE: SECTOR_NAME_LATE, SP.ASOF_AFTER_T0: SECTOR_ASOF_AFTER_T0,
                SP.HISTORY_ABSENT: SECTOR_UNCONFIRMED, SP.IDENTITY_CONFLICT: SECTOR_IDENTITY_CONFLICT}[ev.reason]
    raise LabError([f"a candidate sector snapshot cannot be '{ev.state}'"])


def relative_sector_cell(*, provenance: Optional[str], rs_sector: Any, value: Optional[float], pit_safe: Any,
                         cand: SP.SectorEvidence) -> Tuple[str, bool, Optional[str]]:
    """Decide the sector-relative RS cell of ONE selected, state-ok RS row. Returns (state, keep_value, exposed rs_sector).

    A sector-relative value survives only when ALL of these hold: the RS row is observed, names a sector, flags its sector map PIT-safe, and the
    candidate's own snapshot independently shows the SAME sector as observed and fresh at t0. Everything else is NULL with a state saying why --
    except a NON-NULL value that is not provably safe, which is KEPT (state `unsafe_value` / `sector_reconstructed`) so readiness blocks the dataset
    rather than the problem being silently masked."""
    sec = SP.clean_sector(rs_sector)
    has = value is not None
    if provenance != "observed":
        return SECTOR_RECONSTRUCTED, True, sec
    if sec is None:
        return (UNSAFE_VALUE, True, None) if has else (NO_SECTOR, False, None)
    if pit_safe is not True:
        return (UNSAFE_VALUE, True, sec) if has else (SECTOR_NOT_PIT_SAFE, False, None)
    if cand.state == SP.UNKNOWN:
        return SECTOR_UNKNOWN_PROVENANCE, False, None
    if cand.state == SP.RECONSTRUCTED:
        return SECTOR_RECONSTRUCTED, False, None
    if cand.state == SP.UNAVAILABLE:
        return (SECTOR_IDENTITY_CONFLICT if cand.reason == SP.IDENTITY_CONFLICT else SECTOR_UNCONFIRMED), False, None
    if cand.state == SP.OBSERVED_STALE:
        return SECTOR_STALE, False, None
    if cand.sector != sec:
        return SECTOR_IDENTITY_CONFLICT, False, None
    return (OK if has else SECTOR_VALUE_UNAVAILABLE), True, sec


# ------------------------------------------------------------------ canonical values
def _num(v: Any, what: str) -> float:
    if isinstance(v, bool):
        raise LabError([f"{what}: a bool is not a number"])
    if isinstance(v, Decimal):
        v = float(v)
    if not isinstance(v, (int, float)):
        raise LabError([f"{what}: expected a number, got {type(v).__name__}"])
    v = float(v)
    if not math.isfinite(v):
        raise LabError([f"{what}: NaN/infinite is refused (missing is NULL, never NaN)"])
    return v


def iso_ts(v: datetime) -> str:
    if not isinstance(v, datetime) or v.tzinfo is None:
        raise LabError(["timestamps must be timezone-aware datetimes"])
    return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def encode_cell(value: Any, typ: str, name: str) -> Any:
    """The canonical JSON form of one dataset cell. NULL is always `None` -> `null`."""
    if value is None:
        return None
    if typ == "str":
        if not isinstance(value, str):
            raise LabError([f"{name}: expected str, got {type(value).__name__}"])
        return value
    if typ == "int":
        if not _isint(value):
            raise LabError([f"{name}: expected int, got {type(value).__name__}"])
        return int(value)
    if typ == "float":
        return _num(value, name)
    if typ == "bool":
        if not isinstance(value, bool):
            raise LabError([f"{name}: expected bool, got {type(value).__name__}"])
        return value
    if typ == "date":
        if isinstance(value, datetime) or not isinstance(value, date):
            raise LabError([f"{name}: expected a date"])
        return value.isoformat()
    if typ == "ts":
        return iso_ts(value)
    raise LabError([f"{name}: unknown column type {typ}"])


ROW_SORT = ("t0_session", "symbol", "direction", "strategy_key", "strategy_version", "horizon_sessions")


def row_sort_key(row: Mapping[str, Any]) -> Tuple[Any, ...]:
    return tuple(row[k] for k in ROW_SORT)


def encode_row(row: Mapping[str, Any], schema: str = DATASET_SCHEMA) -> List[Any]:
    """Encode one row under `schema`. Rows are always built with the CURRENT columns; under a frozen legacy schema the columns that schema never
    had (V2_ADDED_COLUMNS) are projected away. Any other missing / extra column is an error."""
    cols = _columns_of(schema)
    missing = [n for n in COLUMN_NAMES if n not in row]
    extra = [k for k in row if k not in COLUMN_TYPES]
    if missing or extra:
        raise LabError([f"row columns differ from the contract: missing {missing}, unexpected {extra}"])
    return [encode_cell(row[n], t, n) for n, t in cols]


def _dump(o: Any) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def schema_hash(schema: str = DATASET_SCHEMA) -> str:
    """Identity of the dataset contract itself: columns, source column lists and query versions. Recomputed from code, so a manifest built under
    another contract fails verification instead of silently describing a different dataset."""
    return canonical_hash({"schema": schema, "columns": [list(c) for c in _columns_of(schema)],
                           "source_columns": {k: list(v) for k, v in sorted(SOURCE_COLUMNS.items())},
                           "query_versions": dict(sorted(QUERY_VERSIONS.items()))})


def legacy_schema_of(declared_schema_hash: Optional[str]) -> Optional[str]:
    """The frozen legacy schema a declared `contract.dataset_schema` hash belongs to, or None (current schema, or not a known contract)."""
    for name in LEGACY_DATASET_SCHEMAS:
        if declared_schema_hash == schema_hash(name):
            return name
    return None


def dataset_header(manifest_hash: str, doc: Mapping[str, Any], row_count: int, schema: str = DATASET_SCHEMA) -> Dict[str, Any]:
    return {"schema": schema, "manifest_hash": manifest_hash, "label_version": doc["label_version"],
            "label_methodology_version": doc["label_methodology_version"], "feature_versions": dict(doc["feature_versions"]),
            "columns": [list(c) for c in _columns_of(schema)], "row_count": row_count}


def dataset_chunks(manifest: M.Manifest, rows: Sequence[Mapping[str, Any]], schema: str = DATASET_SCHEMA) -> Iterator[bytes]:
    """The canonical byte stream of the dataset: the header, then each row (canonical order) preceded by a newline. `dataset_hash` is the
    sha256 of exactly these bytes, so a file holding them hashes to the dataset_hash."""
    ordered = sorted(rows, key=row_sort_key)
    yield _dump(dataset_header(manifest.manifest_hash, manifest.document, len(ordered), schema)).encode()
    for r in ordered:
        yield b"\n"
        yield _dump(encode_row(r, schema)).encode()


def dataset_hash(manifest: M.Manifest, rows: Sequence[Mapping[str, Any]], schema: str = DATASET_SCHEMA) -> str:
    """Deterministic identity of the final dataset. Independent of the order `rows` arrives in (canonical row order is applied here)."""
    h = hashlib.sha256()
    for chunk in dataset_chunks(manifest, rows, schema):
        h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ query fingerprints
def _fp_cell(v: Any) -> Any:
    if v is None or isinstance(v, (bool, str, int)):
        return v
    if isinstance(v, Decimal):
        return _num(v, "fingerprint")
    if isinstance(v, float):
        return _num(v, "fingerprint")
    if isinstance(v, datetime):
        return iso_ts(v)
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Mapping):
        return {str(k): _fp_cell(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
    if isinstance(v, (list, tuple)):
        return [_fp_cell(x) for x in v]
    raise LabError([f"fingerprint: unsupported value type {type(v).__name__}"])


def fingerprint(source: str, rows: Sequence[Mapping[str, Any]], cutoff: datetime) -> str:
    """Fingerprint of one database read: its name, query version, the cutoff, the ordered columns and every row (canonically ordered)."""
    cols = source_columns(source)
    stamp = source_stamp(source)
    cut = cutoff.astimezone(timezone.utc)
    enc = sorted(_dump([_fp_cell(r[c]) for c in cols]) for r in rows if r[stamp] is not None and r[stamp] <= cut)
    h = hashlib.sha256()
    h.update(_dump({"input": DB_INPUTS.get(source, source), "query_version": source_query_version(source), "cutoff": iso_ts(cutoff),
                    "columns": list(cols), "row_count": len(enc)}).encode())
    for e in enc:
        h.update(b"\n")
        h.update(e.encode())
    return h.hexdigest()


@dataclass(frozen=True)
class InputCheck:
    key: str
    kind: str                       # cryptographic | database_fingerprint | unverifiable
    status: str                     # verified | MISMATCH | MISSING | unverifiable
    expected: Optional[str]
    actual: Optional[str]


@dataclass(frozen=True)
class InputVerification:
    checks: Tuple[InputCheck, ...]

    @property
    def ok(self) -> bool:
        return all(c.status in ("verified", "unverifiable") for c in self.checks)

    def failures(self) -> List[InputCheck]:
        return [c for c in self.checks if c.status in ("MISMATCH", "MISSING")]

    def by_kind(self, kind: str) -> List[InputCheck]:
        return [c for c in self.checks if c.kind == kind]

    def to_json(self) -> List[Dict[str, Any]]:
        return [{"key": c.key, "kind": c.kind, "status": c.status, "expected": c.expected, "actual": c.actual}
                for c in sorted(self.checks, key=lambda c: c.key)]


def compute_input_hashes(cfg: DatasetConfig, calendar: Sequence[date], universe_members: Sequence[str],
                         fingerprints: Mapping[str, str]) -> Dict[str, str]:
    """The `input_hashes` a manifest for this config must carry: three cryptographic hashes recomputed from content, plus the supplied database
    query fingerprints (one per enabled source). Used to AUTHOR a manifest; `verify_inputs` is the consuming side."""
    out = {"contract.dataset_schema": schema_hash(), "calendar.sessions": canonical_hash({"sessions": list(calendar)}),
           "universe.members": M.universe_hash(list(universe_members), cfg.universe_rule)}
    for src in enabled_db_sources(cfg):
        if src not in fingerprints:
            raise LabError([f"no fingerprint for enabled source '{src}'"])
        out[DB_INPUTS[src]] = fingerprints[src]
    return out


def verify_inputs(doc: Mapping[str, Any], cfg: DatasetConfig, calendar: Sequence[date], universe_members: Sequence[str],
                  fingerprints: Mapping[str, str]) -> InputVerification:
    """Compare every required manifest input hash with its recomputation. A missing required key, or any mismatch, fails closed (`ok` False).
    A key outside the vocabulary cannot be verified: it is listed as `unverifiable` (visible in the audit), never silently treated as checked."""
    declared = dict(doc["input_hashes"])
    actual: Dict[str, Optional[str]] = {"contract.dataset_schema": schema_hash(),
                                        "calendar.sessions": canonical_hash({"sessions": list(calendar)})}
    try:
        actual["universe.members"] = M.universe_hash(list(universe_members), cfg.universe_rule)
    except LabError:
        actual["universe.members"] = None
    checks: List[InputCheck] = []
    for key in required_input_keys(cfg):
        crypto = key in CRYPTO_INPUTS
        if crypto:
            act = actual[key]
        else:
            src = next(s for s, k in DB_INPUTS.items() if k == key)
            act = fingerprints.get(src)
        exp = declared.get(key)
        kind = "cryptographic" if crypto else "database_fingerprint"
        if exp is None:
            checks.append(InputCheck(key, kind, "MISSING", None, act))
        elif act is None or exp != act:
            checks.append(InputCheck(key, kind, "MISMATCH", exp, act))
        else:
            checks.append(InputCheck(key, kind, "verified", exp, act))
    # the manifest's own recorded identities must agree with the supplied content too
    if doc.get("calendar_hash") != actual["calendar.sessions"]:
        checks.append(InputCheck("manifest.calendar_hash", "cryptographic", "MISMATCH", doc.get("calendar_hash"), actual["calendar.sessions"]))
    if doc.get("universe_hash") != actual["universe.members"]:
        checks.append(InputCheck("manifest.universe_hash", "cryptographic", "MISMATCH", doc.get("universe_hash"), actual["universe.members"]))
    required = set(required_input_keys(cfg))
    for key in sorted(declared):
        if key not in required:
            checks.append(InputCheck(key, "unverifiable", "unverifiable", declared[key], None))
    return InputVerification(tuple(checks))


# ------------------------------------------------------------------ manifest <-> document
def spec_from_document(doc: Mapping[str, Any], calendar: Sequence[date]) -> M.DatasetSpec:
    """Rebuild the DatasetSpec a manifest document was made from (so the full manifest validation can be re-run against the stored document)."""
    w = doc["windows"]
    win = M.Windows(tuple(date.fromisoformat(x) for x in w["train"]), tuple(date.fromisoformat(x) for x in w["validation"]),
                    tuple(date.fromisoformat(x) for x in w["test"]))
    return M.DatasetSpec(
        dataset_name=doc["dataset_name"], dataset_version=doc["dataset_version"], code_sha=doc["code_sha"], code_tree_clean=True,
        label_version=doc["label_version"], label_methodology_version=doc["label_methodology_version"],
        label_horizons=tuple(doc["label_horizons"]), feature_versions=dict(doc["feature_versions"]), universe_id=doc["universe_id"],
        universe_hash=doc["universe_hash"], knowledge_cutoff_at=datetime.fromisoformat(doc["knowledge_cutoff_at"]),
        label_maturity_session=date.fromisoformat(doc["label_maturity_session"]), windows=win, embargo_sessions=doc["embargo_sessions"],
        purge_sessions=doc["purge_sessions"], calendar_source=doc["calendar_source"], calendar=tuple(calendar),
        input_hashes=dict(doc["input_hashes"]), config=dict(doc["config"]))


def revalidate_manifest(doc: Mapping[str, Any], stored_hash: str, calendar: Sequence[date], created_at: datetime) -> M.Manifest:
    """Fail closed unless the stored document (a) hashes to the hash it was fetched by, (b) passes the full manifest validation again as of its
    own creation time, and (c) re-derives the identical hash from the supplied calendar. Returns the validated Manifest."""
    p: List[str] = []
    if canonical_hash(dict(doc)) != stored_hash:
        p.append("the stored manifest document does not hash to its manifest_hash")
    if doc.get("schema") != M.MANIFEST_SCHEMA:
        p.append(f"manifest schema is not {M.MANIFEST_SCHEMA}")
    if p:
        raise LabError(p)
    m = M.build_manifest(spec_from_document(doc, calendar), now=created_at)
    if m.manifest_hash != stored_hash or m.document != dict(doc):
        raise LabError(["re-validating the stored document (with the supplied calendar) does not reproduce the manifest "
                        "(calendar mismatch or a document that was not produced by build_manifest)"])
    return m
