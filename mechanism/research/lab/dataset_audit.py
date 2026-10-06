"""Independent leakage audit (pure).

The assembler decides what goes into a dataset; this module does not trust it. `audit()` re-derives, from the manifest, the explicit calendar
and the RAW rows, whether the finished dataset breaks a rule -- using its own calendar arithmetic for splits/embargo (not `assign_split`) and its
own lookups into the raw rows for the point-in-time selection. Every FATAL finding fails the run; limitations are never fatal but are always
listed, with counts, and never turn into zero or a default.

Fatal codes
  input_hash_mismatch / input_hash_missing   a manifest input hash does not match its recomputation (or is absent)
  row_accounting                             rows + dropped != candidates x horizons, or a duplicate dataset key
  t0_not_a_session / label_invalid           a candidate off the calendar, or a label inconsistent with the calendar / maturity
  pit_violation                              an availability stamp not strictly before the row's decision deadline
  future_observation                         a stamp after the knowledge cutoff, or a dated fact after t0
  label_maturity_violation                   a label in the dataset that is not mature / not calendar-consistent / not shaped by its status
  split_boundary_violation                   a row outside its window, or whose label window crosses the window end
  embargo_violation                          an embargo/purge shorter than the 60-session horizon + purge, or later-split rows closer than the embargo
  masked_value_leak                          a cell whose state says "not used" still carries a value or an availability stamp
  reconstructed_policy_violation             a reconstructed input present under the `exclude` policy
  selection_mismatch                         the assembled cell is not what the raw rows say was known at the deadline
  sector_provenance_unknown                  a sector (candidate snapshot or sector-relative cell) whose provenance cannot be established
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from research.lab import dataset_assemble as A
from research.lab import dataset_contract as C
from research.lab import manifest as M
from research.lab import sector_provenance as SP
from research.lab.manifest import canonical_hash

AUDIT_SCHEMA = "lab_dataset_audit_v2"
EXAMPLES = 5

NOT_USED_STATES = (C.ABSENT, C.LATE, C.RECONSTRUCTED_EXCLUDED, C.NOT_ENABLED, C.NO_SECTOR, C.SECTOR_ASOF_AFTER_T0,
                   C.SECTOR_NAME_LATE, C.SECTOR_STALE, C.SECTOR_UNKNOWN_PROVENANCE)
# per source: the value columns that must be NULL whenever the source's state is not "used"
VALUE_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "market": ("regime_state", "regime_score", "regime_strength", "market__provenance", "market_available_at"),
    "breadth": ("breadth_sma50_pct", "breadth_sma200_pct", "net_highs_lows_pct"),
    "sector": ("sector__provenance", "sector_ret_20", "sector_vs_spx_20", "sector_vs_univ_20", "sector_rank_20", "sector_available_at"),
    "stock_rs": ("rs__provenance", "rs_ret_pct", "rs_vs_spx_pp", "rs_vs_sector_pp", "rs_percentile", "rs_n_universe", "rs_sector_pit_safe", "rs_sector",
                 "rs_available_at"),
    "catalyst": (),
    "first_seen": ("first_seen_json", "first_seen_available_at"),
}
LABEL_VALUE_COLUMNS = ("void_reason", "raw_return", "directional_return", "benchmark_state", "directional_excess_return", "path_state",
                       "mfe", "mae", "data_quality", "label_input_hash", "label_available_at")


@dataclass(frozen=True)
class Audit:
    document: Dict[str, Any]
    text: str
    audit_hash: str

    @property
    def ok(self) -> bool:
        return not self.document["fatal"]


class _Findings:
    def __init__(self) -> None:
        self.items: Dict[str, List[str]] = {}

    def add(self, code: str, detail: str) -> None:
        self.items.setdefault(code, []).append(detail)

    def fatal(self) -> List[Dict[str, Any]]:
        return [{"code": c, "count": len(d), "examples": sorted(d)[:EXAMPLES]} for c, d in sorted(self.items.items())]


def _idx(cal: Sequence[date], d: Optional[date]) -> Optional[int]:
    if d is None:
        return None
    i = bisect_left(cal, d)
    return i if i < len(cal) and cal[i] == d else None


def _num(v: Any) -> Any:
    return float(v) if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) else v


MARKET_VALUES = {"regime_state": "regime_state", "regime_score": "regime_score", "regime_strength": "regime_strength"}
SECTOR_VALUES = {"sector_ret_20": "sec_ret_20", "sector_vs_spx_20": "sec_vs_spx_20", "sector_vs_univ_20": "sec_vs_univ_20",
                 "sector_rank_20": "rank_20"}
RS_VALUES = {"rs_ret_pct": "ret_pct", "rs_vs_spx_pp": "vs_spx_pp", "rs_percentile": "rs_percentile",
             "rs_n_universe": "n_universe_valid", "rs_sector_pit_safe": "sector_pit_safe"}
BREADTH_KEYS = {"breadth_sma50_pct": "c3_breadth_sma50", "breadth_sma200_pct": "c4_breadth_sma200", "net_highs_lows_pct": "c5_net_highs_lows"}


def _value_check(f: _Findings, label: str, key: str, row: Mapping[str, Any], raw_row: Mapping[str, Any], colmap: Mapping[str, str]) -> None:
    """Every value the dataset carries for an `ok` source must be the value of the raw row that was known."""
    for col, rcol in colmap.items():
        if _num(row[col]) != _num(raw_row[rcol]):
            f.add("selection_mismatch", f"{label} {key}: column {col} is {row[col]!r} but the known raw row holds {raw_row[rcol]!r}")


def _breadth_check(f: _Findings, key: str, row: Mapping[str, Any], raw_row: Mapping[str, Any]) -> None:
    comp = raw_row["regime_components"]
    for col, ckey in BREADTH_KEYS.items():
        c = comp.get(ckey) if isinstance(comp, Mapping) else None
        want = float(c["value"]) if isinstance(c, Mapping) and c.get("present") is True and c.get("value") is not None else None
        if row[col] != want:
            f.add("selection_mismatch", f"breadth {key}: column {col} is {row[col]!r} but the known market snapshot says {want!r}")


def _cross_check(f: _Findings, label: str, key: str, raw_rows: Sequence[Mapping[str, Any]], stamp: str, state: str, prov: Optional[str],
                 avail: Optional[datetime], known, policy: str, row: Optional[Mapping[str, Any]] = None,
                 colmap: Optional[Mapping[str, str]] = None, extra=None) -> None:
    """Does the assembled cell (state/provenance/availability) agree with the raw rows for its key?"""
    obs = [r for r in raw_rows if r["provenance"] == "observed"]
    rec = [r for r in raw_rows if r["provenance"] == "reconstructed"]
    k_obs = [r for r in obs if known(r[stamp])]
    k_rec = [r for r in rec if known(r[stamp])]
    if state in (C.OK, C.UNAVAILABLE):
        pool = k_obs if k_obs else (k_rec if policy == "include_flagged" else [])
        match = [r for r in pool if r["provenance"] == prov and r[stamp] == avail]
        if len(match) != 1:
            f.add("selection_mismatch", f"{label} {key}: state {state} is not backed by exactly one known raw row (provenance {prov})")
        elif state == C.OK and row is not None and colmap:
            _value_check(f, label, key, row, match[0], colmap)
            if extra:
                extra(match[0])
    elif state == C.ABSENT:
        if raw_rows:
            f.add("selection_mismatch", f"{label} {key}: marked absent but {len(raw_rows)} raw row(s) exist")
    elif state == C.LATE:
        if k_obs or (k_rec and policy == "include_flagged") or not raw_rows:
            f.add("selection_mismatch", f"{label} {key}: marked late but a known row exists (or no row exists at all)")
    elif state == C.RECONSTRUCTED_EXCLUDED:
        if k_obs or not k_rec or policy != "exclude":
            f.add("selection_mismatch", f"{label} {key}: marked reconstructed_excluded but the raw rows do not say that")


NAME_UNUSED = (C.NO_SECTOR, C.SECTOR_ASOF_AFTER_T0, C.SECTOR_NAME_LATE, C.SECTOR_STALE, C.SECTOR_UNKNOWN_PROVENANCE)
_UNAVAILABLE_REASON_STATE = {SP.NO_SECTOR: C.NO_SECTOR, SP.NAME_LATE: C.SECTOR_NAME_LATE, SP.ASOF_AFTER_T0: C.SECTOR_ASOF_AFTER_T0}


def _candidate_evidence(c: Mapping[str, Any], t0: date, known) -> SP.SectorEvidence:
    return SP.classify_sector_evidence(sector=c["fs_sector"], source=c["fs_sector_source"], asof=c["fs_sector_asof"], t0=t0,
                                       provenance="observed", available=known(c["fs_captured_at"]))


def _sector_name_check(f, key, r, c, cfg, known, t0) -> None:
    """The sector NAME is the candidate's own attribute (its feature snapshot), independent of whether a sector snapshot exists for it. It is
    usable only as an observed, fresh sector (SP.OBSERVED_FRESH); every other evidence class has its own state and no name."""
    if not cfg.sector_feature_set_version:
        return
    if c is None:
        f.add("selection_mismatch", f"{key}: no raw candidate row to check the sector name against")
        return
    ev = _candidate_evidence(c, t0, known)
    if ev.state == SP.OBSERVED_FRESH:
        expected = None
    elif ev.state == SP.OBSERVED_STALE:
        expected = C.SECTOR_STALE
    elif ev.state == SP.UNKNOWN:
        expected = C.SECTOR_UNKNOWN_PROVENANCE
        f.add("sector_provenance_unknown", f"{key}: candidate sector {ev.sector!r} has {ev.reason}")
    else:
        expected = _UNAVAILABLE_REASON_STATE[ev.reason]
    state = r["sector__state"]
    if expected is not None:
        if state != expected or r["sector"] is not None:
            f.add("masked_value_leak" if r["sector"] is not None else "selection_mismatch",
                  f"{key}: sector state {state!r} / name {r['sector']!r}, the raw candidate says {expected!r}")
    elif state in NAME_UNUSED or r["sector"] != ev.sector:
        f.add("selection_mismatch", f"{key}: sector name {r['sector']!r} / state {state!r} do not match the candidate's sector {ev.sector!r}")


def _expected_relative_cell(raw: Mapping[str, Any], ev: SP.SectorEvidence) -> Tuple[str, Any, Optional[str]]:
    """Re-derive (state, value the dataset may carry, exposed rs_sector) for the SELECTED raw RS row and the candidate's sector evidence."""
    sec, val = SP.clean_sector(raw["sector"]), raw["vs_sector_pp"]
    if raw["provenance"] != "observed":
        return C.SECTOR_RECONSTRUCTED, val, sec
    if sec is None:
        return (C.UNSAFE_VALUE, val, None) if val is not None else (C.NO_SECTOR, None, None)
    if raw["sector_pit_safe"] is not True:
        return (C.UNSAFE_VALUE, val, sec) if val is not None else (C.SECTOR_NOT_PIT_SAFE, None, None)
    blocked = {SP.UNKNOWN: C.SECTOR_UNKNOWN_PROVENANCE, SP.RECONSTRUCTED: C.SECTOR_RECONSTRUCTED, SP.UNAVAILABLE: C.SECTOR_UNCONFIRMED,
               SP.OBSERVED_STALE: C.SECTOR_STALE}
    if ev.state in blocked:
        return blocked[ev.state], None, None
    if ev.sector != sec:
        return C.SECTOR_IDENTITY_CONFLICT, None, None
    return (C.OK if val is not None else C.SECTOR_VALUE_UNAVAILABLE), val, sec


def _rs_sector_check(f, key, r, raw_rs_rows, c, cfg, known, t0) -> None:
    """The sector-relative RS cell: its state, its value and the sector tag it exposes must be exactly what the selected raw RS row and the
    candidate's own sector evidence imply. A sector-relative value is never carried without a fresh, observed, PIT-safe, agreeing sector."""
    state = r["rs_vs_sector__state"]
    if r["rs__state"] != C.OK:
        if state != r["rs__state"] or r["rs_vs_sector_pp"] is not None or r["rs_sector"] is not None:
            f.add("selection_mismatch", f"{key}: RS cell is {r['rs__state']!r} but the sector-relative cell is {state!r} / values set")
        return
    match = [x for x in raw_rs_rows if x["provenance"] == r["rs__provenance"] and x["created_at"] == r["rs_available_at"] and x["state"] == "ok"]
    if len(match) != 1 or c is None:
        f.add("selection_mismatch", f"{key}: the sector-relative cell is not backed by exactly one known raw RS row and one candidate")
        return
    want_state, want_val, want_sec = _expected_relative_cell(match[0], _candidate_evidence(c, t0, known))
    want = (want_state, _num(want_val), want_sec if want_state in C.SECTOR_EXPOSED_STATES else None)
    got = (state, _num(r["rs_vs_sector_pp"]), r["rs_sector"])
    if got != want:
        f.add("selection_mismatch", f"{key}: sector-relative cell {got!r} but the raw rows imply {want!r}")
    if want_state == C.SECTOR_UNKNOWN_PROVENANCE:
        f.add("sector_provenance_unknown", f"{key}: the sector behind a sector-relative RS cell has unknown provenance")


def _catalyst_expected(raw_events: Sequence[Mapping[str, Any]], cls: Mapping[Tuple[str, int], Mapping[str, Any]], t0: date,
                       cfg: C.DatasetConfig, known) -> Tuple[str, int, Optional[str]]:
    """Re-derive (state, n_events, labels) for one symbol/t0 from the raw events with its own loop (not the assembler's helper)."""
    cat = cfg.catalyst
    lo = date.fromordinal(t0.toordinal() - cat.lookback_days)
    latest: Dict[str, Mapping[str, Any]] = {}
    for e in raw_events:
        a = C.event_available_at(e["pit_grade"], e["known_at"], e["ingested_at"])
        if a is None or not known(a) or not known(e["ingested_at"]):
            continue
        cur = latest.get(e["event_key"])
        if cur is None or e["revision"] > cur["revision"]:
            latest[e["event_key"]] = e
    used = [e for e in latest.values() if lo <= e["event_time"] <= t0 and e["status"] != "cancelled"]
    labels = sorted({cls[(e["event_key"], e["revision"])]["label"] for e in used
                     if (e["event_key"], e["revision"]) in cls and known(cls[(e["event_key"], e["revision"])]["classified_at"])})
    if used:
        return (C.CATALYST_KNOWN if labels else C.CATALYST_UNCLASSIFIED), len(used), ("|".join(labels) if labels else None)
    unknown = {e["event_key"] for e in raw_events if e["pit_grade"] not in ("A", "B", "C") and lo <= e["event_time"] <= t0
               and e["event_key"] not in latest}
    return (C.UNKNOWN_AVAILABILITY if unknown else C.CATALYST_NONE_OBSERVED), 0, None


def _catalyst_check(f, key, r, events, cls, cfg, known, t0) -> None:
    state, n, labels = _catalyst_expected(events, cls, t0, cfg, known)
    got = (r["catalyst__state"], r["catalyst_n_events"], r["catalyst_labels"])
    if got != (state, n, labels):
        f.add("selection_mismatch", f"{key}: catalyst {got!r} but the raw events/classifications say {(state, n, labels)!r}")


def audit(manifest: M.Manifest, cfg: C.DatasetConfig, raw: Mapping[str, Sequence[Mapping[str, Any]]], assembly: A.Assembly,
          verification: C.InputVerification) -> Audit:
    doc = manifest.document
    cal = manifest.calendar
    cutoff = datetime.fromisoformat(doc["knowledge_cutoff_at"]).astimezone(timezone.utc)
    maturity = date.fromisoformat(doc["label_maturity_session"])
    horizons = tuple(doc["label_horizons"])
    grace = cfg.availability_grace_days
    rows = assembly.rows
    f = _Findings()

    # ---- input hashes
    for c in verification.failures():
        legacy = C.legacy_schema_of(c.expected) if c.key == "contract.dataset_schema" else None
        hint = (f" (built under the frozen {legacy} contract: rebuild under {C.DATASET_SCHEMA}, or reproduce the old dataset with the code "
                "revision pinned in its manifest)") if legacy else ""
        f.add("input_hash_missing" if c.status == "MISSING" else "input_hash_mismatch", f"{c.key} [{c.kind}]{hint}")

    # ---- accounting
    cands = [r for r in raw["candidates"] if r["strategy_key"] == cfg.strategy_key and r["strategy_version"] == cfg.strategy_version]
    expected = len(cands) * len(horizons)
    if len(rows) + len(assembly.dropped) != expected:
        f.add("row_accounting", f"rows {len(rows)} + dropped {len(assembly.dropped)} != candidates {len(cands)} x horizons {len(horizons)}")
    keys = [(r["observation_key"], r["horizon_sessions"]) for r in rows]
    if len(set(keys)) != len(keys):
        f.add("row_accounting", "duplicate (observation, horizon) in the dataset")
    for d in assembly.dropped:
        if d.reason == A.T0_NOT_SESSION:
            f.add("t0_not_a_session", f"{d.observation_key}")
        elif d.reason.startswith(A.LABEL_INVALID_PREFIX):
            f.add("label_invalid", f"{d.observation_key} h{d.horizon_sessions}: {d.reason}")

    # ---- manifest-level embargo
    need = M.required_gap(horizons, doc["purge_sessions"])
    if doc["embargo_sessions"] < need:
        f.add("embargo_violation", f"embargo {doc['embargo_sessions']} < 60-session horizon + purge = {need}")
    wins: Dict[str, Tuple[int, int]] = {}
    for n in M.SPLITS:
        a, b = (_idx(cal, date.fromisoformat(x)) for x in doc["windows"][n])
        if a is None or b is None:
            f.add("split_boundary_violation", f"window {n} edge is not a session of the calendar")
        else:
            wins[n] = (a, b)
    order = list(M.SPLITS)
    for x, y in zip(order, order[1:]):
        if x in wins and y in wins and wins[y][0] - wins[x][1] - 1 < doc["embargo_sessions"]:
            f.add("embargo_violation", f"only {wins[y][0] - wins[x][1] - 1} sessions between {x} and {y}")

    # ---- per-row checks
    lows: Dict[str, int] = {}
    highs: Dict[str, int] = {}
    idx_by_source = {
        "market": {}, "sector": {}, "stock_rs": {}}
    if cfg.market_feature_set_version:
        for r in raw["market"]:
            if r["feature_set_version"] == cfg.market_feature_set_version:
                idx_by_source["market"].setdefault(r["session_date"], []).append(r)
    if cfg.sector_feature_set_version:
        for r in raw["sector"]:
            if r["feature_set_version"] == cfg.sector_feature_set_version:
                idx_by_source["sector"].setdefault((r["session_date"], r["sector"]), []).append(r)
    if cfg.stock_rs:
        rc = cfg.stock_rs
        for r in raw["stock_rs"]:
            if r["model_version"] == rc.model_version and r["feature_set_version"] == rc.feature_set_version \
                    and r["horizon_sessions"] == rc.horizon_sessions:
                idx_by_source["stock_rs"].setdefault((r["session_date"], r["symbol"]), []).append(r)
    cand_sector = {(r["symbol"], r["session_date"], r["direction"]): r for r in cands}
    events_by_symbol: Dict[str, List[Mapping[str, Any]]] = {}
    cls_by_rev: Dict[Tuple[str, int], Mapping[str, Any]] = {}
    if cfg.catalyst:
        for e in raw["events"]:
            if e["symbol"] is not None:
                events_by_symbol.setdefault(e["symbol"], []).append(e)
        for c in raw["classifications"]:
            if c["classifier"] == cfg.catalyst.classifier and c["classifier_version"] == cfg.catalyst.classifier_version:
                cls_by_rev[(c["event_key"], c["revision"])] = c

    for r in rows:
        key = f"{r['observation_key']} h{r['horizon_sessions']}"
        t0, h = r["t0_session"], r["horizon_sessions"]
        deadline = C.decision_deadline(t0, grace)
        known = lambda a, t0=t0: C.is_known(a, t0, grace, cutoff)
        # PIT / future observation
        for source, col, _ in C.AVAILABILITY_COLUMNS:
            a = r[col]
            if a is None:
                continue
            if a > cutoff:
                f.add("future_observation", f"{key}: {col} {a.isoformat()} is after the knowledge cutoff")
            elif source != "label" and a >= deadline:
                f.add("pit_violation", f"{key}: {col} {a.isoformat()} is not before the decision deadline {deadline.isoformat()}")
        if r["catalyst_latest_event_time"] is not None and r["catalyst_latest_event_time"] > t0:
            f.add("future_observation", f"{key}: a catalyst dated {r['catalyst_latest_event_time']} is after t0")
        # states vs values
        for source, scol in C.SOURCE_STATE_COLUMNS.items():
            st = r[scol]
            if st in NOT_USED_STATES and source != "catalyst":
                leaked = [c for c in VALUE_COLUMNS[source] if r[c] is not None]
                if leaked:
                    f.add("masked_value_leak", f"{key}: {source} is {st} but {leaked} are set")
        if cfg.catalyst:
            _catalyst_check(f, key, r, events_by_symbol.get(r["symbol"], ()), cls_by_rev, cfg, known, t0)
        _sector_name_check(f, key, r, cand_sector.get((r["symbol"], t0, r["direction"])), cfg, known, t0)
        if cfg.stock_rs:
            _rs_sector_check(f, key, r, idx_by_source["stock_rs"].get((t0, r["symbol"]), ()), cand_sector.get((r["symbol"], t0, r["direction"])),
                             cfg, known, t0)
        if cfg.reconstructed_policy == "exclude":
            for col in ("market__provenance", "sector__provenance", "rs__provenance"):
                if r[col] == "reconstructed":
                    f.add("reconstructed_policy_violation", f"{key}: {col}")
        # label maturity and shape
        i0 = _idx(cal, t0)
        if i0 is None:
            f.add("t0_not_a_session", key)
            continue
        hs = r["horizon_session"]
        if i0 + h >= len(cal) or hs != cal[i0 + h]:
            f.add("label_maturity_violation", f"{key}: horizon_session {hs} is not the calendar's session {h} after t0")
        if hs is not None and hs > maturity:
            f.add("label_maturity_violation", f"{key}: horizon_session {hs} is after the maturity session {maturity}")
        st = r["label_status"]
        if st == C.MISSING:
            if any(r[c] is not None for c in LABEL_VALUE_COLUMNS):
                f.add("masked_value_leak", f"{key}: label missing but label values are set")
        elif st == "final":
            if r["directional_return"] is None or r["raw_return"] is None or r["label_available_at"] is None:
                f.add("label_maturity_violation", f"{key}: a final label without its outcome")
            elif r["label_available_at"] > cutoff:
                f.add("label_maturity_violation", f"{key}: label computed after the knowledge cutoff")
        elif st == "void":
            if r["directional_return"] is not None or r["raw_return"] is not None:
                f.add("label_maturity_violation", f"{key}: a void label carrying a return")
        else:
            f.add("label_maturity_violation", f"{key}: unknown label status {st!r}")
        # split / embargo, from calendar arithmetic
        end = i0 + h
        inside = [n for n, (a, b) in wins.items() if a <= i0 <= b]
        if not inside:
            f.add("split_boundary_violation", f"{key}: t0 is in no window")
        else:
            n = inside[0]
            if r["split"] != n:
                f.add("split_boundary_violation", f"{key}: labelled {r['split']} but t0 lies in {n}")
            if end > wins[n][1]:
                f.add("split_boundary_violation", f"{key}: the {h}-session label window ends after the {n} window")
            lows[n] = min(lows.get(n, i0), i0)
            highs[n] = max(highs.get(n, end), end)
        # selection cross-checks against the raw rows
        if cfg.market_feature_set_version:
            _cross_check(f, "market", key, idx_by_source["market"].get(t0, ()), "captured_at", r["market__state"], r["market__provenance"],
                         r["market_available_at"], known, cfg.reconstructed_policy,
                         row=r, colmap=MARKET_VALUES, extra=lambda m, k=key, r=r: _breadth_check(f, k, r, m))
        if cfg.sector_feature_set_version and r["sector"] is not None and r["sector__state"] not in (C.NO_SECTOR, C.SECTOR_ASOF_AFTER_T0, C.NOT_ENABLED):
            _cross_check(f, "sector", key, idx_by_source["sector"].get((t0, r["sector"]), ()), "captured_at", r["sector__state"],
                         r["sector__provenance"], r["sector_available_at"], known, cfg.reconstructed_policy,
                         row=r, colmap=SECTOR_VALUES)
        if cfg.stock_rs:
            _cross_check(f, "stock_rs", key, idx_by_source["stock_rs"].get((t0, r["symbol"]), ()), "created_at", r["rs__state"],
                         r["rs__provenance"], r["rs_available_at"], known, cfg.reconstructed_policy,
                         row=r, colmap=RS_VALUES)
    for x, y in zip(order, order[1:]):
        if x in highs and y in lows and lows[y] - highs[x] < doc["embargo_sessions"] + 1:
            f.add("embargo_violation", f"{y} rows begin {lows[y] - highs[x]} sessions after the last {x} label ends (embargo {doc['embargo_sessions']})")

    return _document(manifest, cfg, raw, assembly, verification, f, cands)


# ------------------------------------------------------------------ report assembly
def _hist(rows: Sequence[Mapping[str, Any]], col: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        out[str(r[col])] = out.get(str(r[col]), 0) + 1
    return dict(sorted(out.items()))


def _document(manifest: M.Manifest, cfg: C.DatasetConfig, raw: Mapping[str, Sequence[Mapping[str, Any]]], assembly: A.Assembly,
              verification: C.InputVerification, f: _Findings, cands: Sequence[Mapping[str, Any]]) -> Audit:
    rows = assembly.rows
    doc = manifest.document
    drop: Dict[str, int] = {}
    for d in assembly.dropped:
        drop[d.reason] = drop.get(d.reason, 0) + 1
    enabled = cfg.enabled()
    states = {s: _hist(rows, col) for s, col in C.SOURCE_STATE_COLUMNS.items() if enabled[s]}
    lim: List[Dict[str, Any]] = []

    def limit(code: str, count: Optional[int], note: str) -> None:
        lim.append({"code": code, "count": count, "note": note})

    for src, col in (("market", "market__provenance"), ("sector", "sector__provenance"), ("stock_rs", "rs__provenance")):
        n = sum(1 for r in rows if r[col] == "reconstructed")
        if n:
            limit("reconstructed_input_used", n, f"{n} rows read a RECONSTRUCTED {src} value (policy include_flagged); never point-in-time safe")
    if cfg.stock_rs:
        rel = _hist(rows, "rs_vs_sector__state")
        n = sum(rel.get(k, 0) for k in (C.NO_SECTOR, C.SECTOR_VALUE_UNAVAILABLE, C.SECTOR_NOT_PIT_SAFE))
        if n:
            limit("sector_relative_unavailable", n, f"{n} rows have no sector-relative RS value (no_sector {rel.get(C.NO_SECTOR, 0)}, "
                  f"sector_value_unavailable {rel.get(C.SECTOR_VALUE_UNAVAILABLE, 0)}, sector_not_pit_safe {rel.get(C.SECTOR_NOT_PIT_SAFE, 0)}): "
                  "the candidate stays in the dataset, the value is NULL (never 0), and there is no market-relative substitute")
        n = sum(rel.get(k, 0) for k in (C.SECTOR_STALE, C.SECTOR_UNCONFIRMED, C.SECTOR_IDENTITY_CONFLICT, C.SECTOR_UNKNOWN_PROVENANCE))
        if n:
            limit("sector_relative_value_masked", n, f"{n} rows had a stored sector-relative value that the dataset MASKED to NULL because the "
                  "candidate's own sector evidence was stale / unconfirmed / conflicting / of unknown provenance at the decision point")
        n = rel.get(C.UNSAFE_VALUE, 0)
        if n:
            limit("sector_relative_unsafe_value", n, f"{n} rows carry a NON-NULL sector-relative value that cannot be proved point-in-time safe "
                  "(kept visible so readiness blocks it; never masked quietly)")
    for s, h in states.items():
        for st in (C.RECONSTRUCTED_EXCLUDED,):
            if h.get(st):
                limit("reconstructed_input_excluded", h[st], f"{h[st]} {s} cells were reconstructed-only and excluded (NULL, state {st})")
        if h.get(C.SECTOR_NAME_LATE):
            limit("late_value_masked", h[C.SECTOR_NAME_LATE], f"{h[C.SECTOR_NAME_LATE]} candidate sector attributes were stamped after the decision deadline: masked to NULL")
        if h.get(C.LATE):
            limit("late_value_masked", h[C.LATE], f"{h[C.LATE]} {s} cells existed only after the decision deadline: masked to NULL")
        if h.get(C.UNKNOWN_AVAILABILITY):
            limit("unknown_availability", h[C.UNKNOWN_AVAILABILITY], f"{h[C.UNKNOWN_AVAILABILITY]} {s} cells had only events with unknown availability (grade X)")
    nu = sum(r["catalyst_n_unknown_availability"] or 0 for r in rows)
    if nu:
        limit("unknown_availability_events", nu, f"{nu} events in look-back windows have unprovable availability (grade X) and were not used")
    unavailable = {s: {k: v for k, v in h.items() if k in (C.UNAVAILABLE, C.ABSENT, C.NO_SECTOR, C.SECTOR_ASOF_AFTER_T0, C.SECTOR_NAME_LATE,
                                                           C.SECTOR_STALE, C.SECTOR_UNKNOWN_PROVENANCE)} for s, h in states.items()}
    unavailable = {s: h for s, h in unavailable.items() if h}
    if unavailable:
        limit("unavailable_features", sum(sum(h.values()) for h in unavailable.values()), "cells with no usable value (the cell is NULL, not zero)")
    if cfg.catalyst:
        limit("catalyst_absence_is_not_evidence", None,
              "'none_observed' means no usable classified event was found; it is not proof that no catalyst existed")
    if cfg.sector_feature_set_version:
        limit("sector_history_reconstructed", None,
              "sector snapshots outside a live capture window are reconstructed from today's sector map and are never point-in-time safe")
    if assembly.labels_after_cutoff:
        limit("labels_computed_after_cutoff", assembly.labels_after_cutoff, "labels computed after the knowledge cutoff were ignored (rows read them as missing)")
    miss = sum(1 for r in rows if r["label_status"] == C.MISSING)
    if miss:
        limit("labels_missing", miss, f"{miss} dataset rows have no label at that horizon (counted, never zero-filled)")
    cs = sorted({r["calendar_source"] for r in raw["labels"]})
    if cs and cs != [doc["calendar_source"]]:
        limit("label_calendar_source_differs", None, f"labels name calendar sources {cs}; the manifest's explicit calendar is '{doc['calendar_source']}' "
              "(horizon sessions were re-verified against the manifest calendar)")
    for c in verification.by_kind("unverifiable"):
        limit("unverifiable_input", None, f"manifest input '{c.key}' is outside the verifiable vocabulary and was not checked")
    trust = {s: C.AVAILABILITY_TRUST[s] for s in C.enabled_db_sources(cfg)}
    wd = sorted(s for s, t in trust.items() if t == "writer_default")
    limit("availability_stamp_trust", None, f"availability stamps of {wd} are writer-settable defaults (not database-enforced); the others are trigger-stamped")

    by_split = {s: sum(1 for r in rows if r["split"] == s) for s in M.SPLITS}
    by_label = {s: _hist([r for r in rows if r["split"] == s], "label_status") for s in M.SPLITS}
    counts = {
        "candidates_seen": assembly.candidates_seen, "candidate_horizon_pairs": assembly.candidates_seen * len(doc["label_horizons"]),
        "rows_final": len(rows), "rows_by_split": by_split, "label_status_by_split": by_label,
        "rows_purged": drop.get(A.SPLIT_PREFIX + "purged", 0), "rows_embargoed": drop.get(A.SPLIT_PREFIX + "embargo", 0),
        "rows_outside_windows": drop.get(A.SPLIT_PREFIX + "outside", 0),
        "rows_excluded": {k: v for k, v in sorted(drop.items()) if not k.startswith(A.SPLIT_PREFIX)},
        "source_states": states,
        "sector_relative_states": _hist(rows, "rs_vs_sector__state") if cfg.stock_rs else {},
    }
    fatal = f.fatal()
    document = {"schema": AUDIT_SCHEMA, "manifest_hash": manifest.manifest_hash, "verdict": "FAIL" if fatal else "PASS", "fatal": fatal,
                "limitations": sorted(lim, key=lambda x: x["code"]), "counts": counts, "availability_trust": dict(sorted(trust.items())),
                "input_verification": verification.to_json(), "policy": {"reconstructed_policy": cfg.reconstructed_policy,
                                                                          "availability_grace_days": cfg.availability_grace_days}}
    return Audit(document, render_text(document), canonical_hash(document))


def render_text(d: Mapping[str, Any]) -> str:
    out = [f"LEAKAGE AUDIT  manifest {d['manifest_hash'][:16]}  verdict {d['verdict']}", ""]
    c = d["counts"]
    out += [f"candidates seen {c['candidates_seen']}   (candidate x horizon pairs {c['candidate_horizon_pairs']})",
            f"final rows {c['rows_final']}   train {c['rows_by_split']['train']}  validation {c['rows_by_split']['validation']}  "
            f"test {c['rows_by_split']['test']}",
            f"purged {c['rows_purged']}   embargoed {c['rows_embargoed']}   outside windows {c['rows_outside_windows']}   "
            f"excluded {c['rows_excluded'] or 'none'}", ""]
    out.append("FATAL VIOLATIONS" + ("  (none)" if not d["fatal"] else ""))
    for x in d["fatal"]:
        out.append(f"  - {x['code']}: {x['count']}")
        out += [f"      e.g. {e}" for e in x["examples"]]
    out += ["", "SOURCE STATES (rows)"]
    for s, h in c["source_states"].items():
        out.append(f"  {s}: " + ", ".join(f"{k}={v}" for k, v in h.items()))
    out += ["", "INPUT VERIFICATION"]
    for v in d["input_verification"]:
        out.append(f"  {v['status']:<12} {v['key']}  [{v['kind']}]")
    out += ["", "LIMITATIONS (visible, non-fatal; never zero-filled)"]
    for x in d["limitations"]:
        out.append(f"  - {x['code']}" + (f" ({x['count']})" if x["count"] is not None else "") + f": {x['note']}")
    return "\n".join(out) + "\n"
