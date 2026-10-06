"""Deterministic, leakage-aware dataset assembly (pure).

`assemble(manifest, cfg, raw)` turns the rows a read-only database pass returned (`raw`, keyed by source name, each row a dict with exactly the
columns of `SOURCE_COLUMNS[source]`) into the dataset rows of `dataset_contract.COLUMNS`. No database, clock, environment, file or network:
the same manifest + the same raw rows always give the same dataset, whatever order the rows arrive in.

What it will never do
* use a value that was not known by the observation's decision deadline (`dataset_contract.is_known`): it is MASKED to NULL, the cell's state
  says why (`late`), and nothing is substituted;
* invent a value for something missing: absent / unavailable / reconstructed-and-excluded cells are NULL with an explicit state;
* keep a row whose label window crosses a split boundary: it is dropped and listed (`purged` / `embargo` / `outside`);
* build over an inconsistent label (wrong horizon session, matured after the maturity session, computed before its own horizon): the row is
  dropped with a reason and the independent auditor reports it as FATAL.

Every (candidate, horizon) pair of the raw candidates ends up in exactly one of `rows` / `dropped`, so counts reconcile.
"""
from __future__ import annotations

import json
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from research.lab import dataset_contract as C
from research.lab.manifest import LabError, Manifest, assign_split

# dropped-row reasons
NOT_IN_UNIVERSE = "not_in_universe"
T0_NOT_SESSION = "t0_not_a_session"
CANDIDATE_NOT_KNOWN = "candidate_not_known_by_deadline"
SPLIT_PREFIX = "split:"                                   # split:purged | split:embargo | split:outside
LABEL_INVALID_PREFIX = "label_invalid:"                   # label_invalid:<why>  (always fatal in the audit)
LABEL_HORIZON_MISMATCH = LABEL_INVALID_PREFIX + "horizon_session_not_calendar_derived"
LABEL_NOT_MATURE = LABEL_INVALID_PREFIX + "horizon_after_maturity_session"
LABEL_EARLY = LABEL_INVALID_PREFIX + "computed_as_of_before_horizon"
FATAL_DROP_REASONS = (T0_NOT_SESSION,)


@dataclass(frozen=True)
class Dropped:
    observation_key: str
    horizon_sessions: int
    reason: str
    t0_session: date


@dataclass(frozen=True)
class Assembly:
    rows: Tuple[Dict[str, Any], ...]
    dropped: Tuple[Dropped, ...]
    labels_after_cutoff: int
    candidates_seen: int

    def to_json_counts(self) -> Dict[str, Any]:
        by: Dict[str, int] = {}
        for d in self.dropped:
            by[d.reason] = by.get(d.reason, 0) + 1
        return {"rows": len(self.rows), "dropped_by_reason": dict(sorted(by.items())), "candidates_seen": self.candidates_seen,
                "labels_ignored_after_cutoff": self.labels_after_cutoff}


def observation_key(strategy_key: str, symbol: str, session: date, direction: int) -> str:
    return f"{strategy_key}|{symbol}|{session.isoformat()}|{direction}"


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, bool):
        raise LabError(["a bool is not a numeric column value"])
    return float(v) if isinstance(v, (Decimal, int, float)) else v


def _group(rows: Sequence[Mapping[str, Any]], key) -> Dict[Any, List[Mapping[str, Any]]]:
    out: Dict[Any, List[Mapping[str, Any]]] = {}
    for r in rows:
        out.setdefault(key(r), []).append(r)
    return out


def _check_columns(raw: Mapping[str, Sequence[Mapping[str, Any]]], cfg: C.DatasetConfig) -> None:
    p = []
    for src in C.enabled_db_sources(cfg):
        if src not in raw:
            p.append(f"raw source '{src}' is enabled by the manifest config but was not supplied")
            continue
        want = set(C.SOURCE_COLUMNS[src])
        for r in raw[src][:1]:
            if set(r) != want:
                p.append(f"raw source '{src}' columns differ from the contract: missing {sorted(want - set(r))}, extra {sorted(set(r) - want)}")
    if p:
        raise LabError(p)


def _pick(rows: Sequence[Mapping[str, Any]], known, policy: str, stamp: str) -> Tuple[Optional[Mapping[str, Any]], str]:
    """Choose the one usable row among rows that share a key apart from `provenance`. Observed rows are preferred; a reconstructed row is used
    only under policy `include_flagged`. Returns (row | None, state)."""
    obs = [r for r in rows if r["provenance"] == "observed"]
    rec = [r for r in rows if r["provenance"] == "reconstructed"]
    k_obs = [r for r in obs if known(r[stamp])]
    if len(k_obs) > 1:
        raise LabError(["more than one observed row for a unique key (the database contract is violated)"])
    if k_obs:
        return k_obs[0], C.OK
    k_rec = [r for r in rec if known(r[stamp])]
    if len(k_rec) > 1:
        raise LabError(["more than one reconstructed row for a unique key (the database contract is violated)"])
    if k_rec and policy == "include_flagged":
        return k_rec[0], C.OK
    if obs:
        return None, C.LATE
    if k_rec:
        return None, C.RECONSTRUCTED_EXCLUDED
    if rec:
        return None, C.LATE
    return None, C.ABSENT


def _empty_row() -> Dict[str, Any]:
    return {n: None for n in C.COLUMN_NAMES}


def _breadth(components: Any) -> Dict[str, Optional[float]]:
    if not isinstance(components, Mapping):
        raise LabError(["market_snapshot.regime_components has an unexpected shape (expected an object keyed by component)"])
    out: Dict[str, Optional[float]] = {}
    for col, key in (("breadth_sma50_pct", "c3_breadth_sma50"), ("breadth_sma200_pct", "c4_breadth_sma200"),
                     ("net_highs_lows_pct", "c5_net_highs_lows")):
        c = components.get(key)
        if isinstance(c, Mapping) and c.get("present") is True and c.get("value") is not None:
            out[col] = float(c["value"])
        else:
            out[col] = None
    return out


def _catalyst(events: Sequence[Mapping[str, Any]], cls_by_rev: Mapping[Tuple[str, int], Mapping[str, Any]], t0: date,
              cfg: C.DatasetConfig, cutoff: datetime) -> Dict[str, Any]:
    cat = cfg.catalyst
    assert cat is not None
    lo = date.fromordinal(t0.toordinal() - cat.lookback_days)
    known = lambda a: C.is_known(a, t0, cfg.availability_grace_days, cutoff)
    by_key = _group(events, lambda e: e["event_key"])
    used: List[Tuple[Mapping[str, Any], datetime]] = []
    unknown_availability = 0
    for key in sorted(by_key):
        revs = sorted(by_key[key], key=lambda e: e["revision"])
        ok = []
        for e in revs:
            a = C.event_available_at(e["pit_grade"], e["known_at"], e["ingested_at"])
            if a is not None and known(a) and known(e["ingested_at"]):
                ok.append((e, max(a, e["ingested_at"])))
        if ok:
            e, a = ok[-1]                                                  # the latest revision known by the deadline
            if lo <= e["event_time"] <= t0 and e["status"] != "cancelled":
                used.append((e, a))
        elif any(e["pit_grade"] not in ("A", "B", "C") and lo <= e["event_time"] <= t0 for e in revs):
            unknown_availability += 1
    labels: List[str] = []
    avail: List[datetime] = [a for _, a in used]
    for e, _ in used:
        c = cls_by_rev.get((e["event_key"], e["revision"]))
        if c is not None and known(c["classified_at"]):
            labels.append(c["label"])
            avail.append(c["classified_at"])
    if used:
        state = C.CATALYST_KNOWN if labels else C.CATALYST_UNCLASSIFIED
    elif unknown_availability:
        state = C.UNKNOWN_AVAILABILITY
    else:
        state = C.CATALYST_NONE_OBSERVED
    return {"catalyst__state": state, "catalyst_n_events": len(used), "catalyst_n_unknown_availability": unknown_availability,
            "catalyst_labels": "|".join(sorted(set(labels))) if labels else None,
            "catalyst_latest_event_time": max((e["event_time"] for e, _ in used), default=None),
            "catalyst_available_at": max(avail) if avail else None}


def _first_seen(by_subject: Mapping[Tuple[str, str, str], Sequence[Mapping[str, Any]]], symbol: str, t0: date, cfg: C.DatasetConfig,
                cutoff: datetime) -> Dict[str, Any]:
    known = lambda a: C.is_known(a, t0, cfg.availability_grace_days, cutoff)
    doc: Dict[str, Any] = {}
    avail: List[datetime] = []
    any_late = False
    for spec in cfg.first_seen:
        rows = by_subject.get((spec.source, spec.dataset, symbol), ())
        if not rows:
            continue
        ok = [r for r in rows if known(r["observed_at"])]
        if not ok:
            any_late = True
            continue
        period = max(r["period_key"] for r in ok)                          # latest period with a value known by the deadline
        pick = max((r for r in ok if r["period_key"] == period), key=lambda r: r["seq"])
        value = pick["value"] if isinstance(pick["value"], Mapping) else {}
        doc[f"{spec.source}/{spec.dataset}"] = {"period_key": period, "seq": pick["seq"],
                                                 "fields": {f: value.get(f) for f in spec.fields}}
        avail.append(pick["observed_at"])
    if doc:
        return {"first_seen__state": C.OK, "first_seen_json": json.dumps(C._fp_cell(doc), sort_keys=True, separators=(",", ":")),
                "first_seen_available_at": max(avail)}
    return {"first_seen__state": C.LATE if any_late else C.ABSENT, "first_seen_json": None, "first_seen_available_at": None}


def assemble(manifest: Manifest, cfg: C.DatasetConfig, raw: Mapping[str, Sequence[Mapping[str, Any]]]) -> Assembly:
    doc = manifest.document
    cal = manifest.calendar
    cutoff = datetime.fromisoformat(doc["knowledge_cutoff_at"]).astimezone(timezone.utc)
    maturity = date.fromisoformat(doc["label_maturity_session"])
    horizons = tuple(doc["label_horizons"])
    grace = cfg.availability_grace_days
    universe = set(cfg.universe_members)
    _check_columns(raw, cfg)

    cands = [r for r in raw["candidates"] if r["strategy_key"] == cfg.strategy_key and r["strategy_version"] == cfg.strategy_version]
    ckey = lambda r: (r["strategy_key"], r["strategy_version"], r["symbol"], r["session_date"], r["direction"])
    if len({ckey(r) for r in cands}) != len(cands):
        raise LabError(["duplicate candidate identity in the raw candidates (the database contract is violated)"])

    labels_all = [r for r in raw["labels"] if r["label_version"] == doc["label_version"]
                  and r["methodology_version"] == doc["label_methodology_version"]]
    after_cutoff = [r for r in labels_all if r["computed_at"] > cutoff]
    labels_by = {}
    for r in labels_all:
        if r["computed_at"] > cutoff:
            continue
        k = (r["strategy_key"], r["strategy_version"], r["symbol"], r["t0_session"], r["direction"], r["horizon_sessions"])
        if k in labels_by:
            raise LabError(["duplicate label for one (observation, horizon, label_version)"])
        labels_by[k] = r

    market_by = {}
    if cfg.market_feature_set_version:
        market_by = _group([r for r in raw["market"] if r["feature_set_version"] == cfg.market_feature_set_version], lambda r: r["session_date"])
    sector_by = {}
    if cfg.sector_feature_set_version:
        sector_by = _group([r for r in raw["sector"] if r["feature_set_version"] == cfg.sector_feature_set_version],
                           lambda r: (r["session_date"], r["sector"]))
    rs_by = {}
    if cfg.stock_rs:
        rs = cfg.stock_rs
        rs_by = _group([r for r in raw["stock_rs"] if r["model_version"] == rs.model_version and r["feature_set_version"] == rs.feature_set_version
                        and r["horizon_sessions"] == rs.horizon_sessions], lambda r: (r["session_date"], r["symbol"]))
    events_by = {}
    cls_by_rev: Dict[Tuple[str, int], Mapping[str, Any]] = {}
    if cfg.catalyst:
        events_by = _group([r for r in raw["events"] if r["symbol"] is not None], lambda r: r["symbol"])
        for r in raw["classifications"]:
            if r["classifier"] == cfg.catalyst.classifier and r["classifier_version"] == cfg.catalyst.classifier_version:
                k = (r["event_key"], r["revision"])
                if k in cls_by_rev:
                    raise LabError(["duplicate classification for (revision, classifier, version)"])
                cls_by_rev[k] = r
    fs_by = _group(raw["first_seen"], lambda r: (r["source"], r["dataset"], r["subject_id"])) if cfg.first_seen else {}

    rows: List[Dict[str, Any]] = []
    dropped: List[Dropped] = []
    for c in sorted(cands, key=lambda r: (r["session_date"], r["symbol"], r["direction"])):
        t0 = c["session_date"]
        okey = observation_key(c["strategy_key"], c["symbol"], t0, c["direction"])
        # ---- row-level eligibility, decided once per candidate
        reason: Optional[str] = None
        if c["symbol"] not in universe:
            reason = NOT_IN_UNIVERSE
        elif bisect_left(cal, t0) >= len(cal) or cal[bisect_left(cal, t0)] != t0:
            reason = T0_NOT_SESSION
        elif not C.is_known(c["captured_at"], t0, grace, cutoff):
            reason = CANDIDATE_NOT_KNOWN
        if reason is not None:
            dropped += [Dropped(okey, h, reason, t0) for h in horizons]
            continue
        i0 = bisect_left(cal, t0)
        known = lambda a, t0=t0: C.is_known(a, t0, grace, cutoff)

        # ---- cells that do not depend on the horizon
        base = _empty_row()
        base.update({"observation_key": okey, "strategy_key": c["strategy_key"], "strategy_version": c["strategy_version"],
                     "symbol": c["symbol"], "t0_session": t0, "direction": c["direction"], "signal_type": c["signal_type"],
                     "triggered": c["triggered"], "passed_guard": c["passed_guard"], "tracked_intent": c["tracked_intent"],
                     "atr_source": c["atr_source"], "breakout_dist_atr": _f(c["breakout_dist_atr"]),
                     "distance_to_channel_pct": _f(c["distance_to_channel_pct"]), "quality_grade": c["quality_grade"],
                     "alignment_score": _f(c["alignment_score"]), "candidate_available_at": c["captured_at"]})
        base.update({C.SOURCE_STATE_COLUMNS[s]: C.NOT_ENABLED for s in C.SOURCE_STATE_COLUMNS})
        base["rs_vs_sector__state"] = C.NOT_ENABLED
        sector_name: Optional[str] = None
        mrow = None
        if cfg.market_feature_set_version:
            mrow, mstate = _pick(market_by.get(t0, ()), known, cfg.reconstructed_policy, "captured_at")
            base["market__state"] = mstate
            base["breadth__state"] = mstate
            if mrow is not None:
                base["market__provenance"] = mrow["provenance"]
                base["market_available_at"] = mrow["captured_at"]
                if mrow["regime_state"] == "UNAVAILABLE":
                    base["market__state"] = C.UNAVAILABLE
                else:
                    base["regime_state"] = mrow["regime_state"]
                    base["regime_score"] = _f(mrow["regime_score"])
                    base["regime_strength"] = _f(mrow["regime_strength"])
                b = _breadth(mrow["regime_components"])
                base.update(b)
                base["breadth__state"] = C.OK if any(v is not None for v in b.values()) else C.UNAVAILABLE
        cand_ev = C.candidate_sector_evidence(c, t0, known)
        if cfg.sector_feature_set_version:
            name_state = C.sector_name_state(cand_ev)
            if name_state != C.OK:
                base["sector__state"] = name_state
            else:
                sector_name = cand_ev.sector
                base["sector"] = sector_name
                base["sector_available_at"] = None
                srow, sstate = _pick(sector_by.get((t0, sector_name), ()), known, cfg.reconstructed_policy, "captured_at")
                base["sector__state"] = sstate
                if srow is not None:
                    base["sector__provenance"] = srow["provenance"]
                    vals = {k: _f(srow[s]) for k, s in (("sector_ret_20", "sec_ret_20"), ("sector_vs_spx_20", "sec_vs_spx_20"),
                                                        ("sector_vs_univ_20", "sec_vs_univ_20"), ("sector_rank_20", "rank_20"))}
                    base.update(vals)
                    if vals["sector_rank_20"] is not None:
                        base["sector_rank_20"] = int(vals["sector_rank_20"])
                    base["sector_available_at"] = srow["captured_at"]
                    if all(v is None for v in vals.values()):
                        base["sector__state"] = C.UNAVAILABLE
        if cfg.stock_rs:
            rrow, rstate = _pick(rs_by.get((t0, c["symbol"]), ()), known, cfg.reconstructed_policy, "created_at")
            base["rs__state"] = rstate
            if rrow is not None:
                base["rs__provenance"] = rrow["provenance"]
                base["rs_available_at"] = rrow["created_at"]
                base["rs_sector_pit_safe"] = rrow["sector_pit_safe"]
                if rrow["state"] == "ok":
                    vs_sector = _f(rrow["vs_sector_pp"])
                    cell, keep, exposed = C.relative_sector_cell(provenance=rrow["provenance"], rs_sector=rrow["sector"], value=vs_sector,
                                                                 pit_safe=rrow["sector_pit_safe"], cand=cand_ev)
                    base.update({"rs_ret_pct": _f(rrow["ret_pct"]), "rs_vs_spx_pp": _f(rrow["vs_spx_pp"]),
                                 "rs_vs_sector_pp": vs_sector if keep else None, "rs_percentile": _f(rrow["rs_percentile"]),
                                 "rs_n_universe": rrow["n_universe_valid"], "rs_vs_sector__state": cell,
                                 "rs_sector": exposed if cell in C.SECTOR_EXPOSED_STATES else None})
                else:
                    base["rs__state"] = C.UNAVAILABLE
                    base["rs_vs_sector__state"] = C.UNAVAILABLE
            else:
                base["rs_vs_sector__state"] = rstate
        if cfg.catalyst:
            base.update(_catalyst(events_by.get(c["symbol"], ()), cls_by_rev, t0, cfg, cutoff))
        if cfg.first_seen:
            base.update(_first_seen(fs_by, c["symbol"], t0, cfg, cutoff))

        # ---- one dataset row per horizon
        for h in horizons:
            split = assign_split(manifest, t0, h)
            if split not in ("train", "validation", "test"):
                dropped.append(Dropped(okey, h, SPLIT_PREFIX + split, t0))
                continue
            row = dict(base)
            row["horizon_sessions"] = h
            row["split"] = split
            expect = cal[i0 + h]
            lab = labels_by.get((c["strategy_key"], c["strategy_version"], c["symbol"], t0, c["direction"], h))
            if lab is None:
                row["label_status"] = C.MISSING
                row["horizon_session"] = expect
            else:
                if lab["horizon_session"] != expect:
                    dropped.append(Dropped(okey, h, LABEL_HORIZON_MISMATCH, t0))
                    continue
                if lab["horizon_session"] > maturity:
                    dropped.append(Dropped(okey, h, LABEL_NOT_MATURE, t0))
                    continue
                if lab["computed_as_of_session"] < lab["horizon_session"]:
                    dropped.append(Dropped(okey, h, LABEL_EARLY, t0))
                    continue
                row.update({"horizon_session": lab["horizon_session"], "label_status": lab["label_status"], "void_reason": lab["void_reason"],
                            "raw_return": _f(lab["raw_return"]), "directional_return": _f(lab["directional_return"]),
                            "benchmark_state": lab["benchmark_state"], "directional_excess_return": _f(lab["directional_excess_return"]),
                            "path_state": lab["path_state"], "mfe": _f(lab["mfe"]), "mae": _f(lab["mae"]),
                            "data_quality": lab["data_quality"], "label_input_hash": lab["input_hash"],
                            "label_available_at": lab["computed_at"]})
            rows.append(row)
    rows.sort(key=C.row_sort_key)
    return Assembly(tuple(rows), tuple(sorted(dropped, key=lambda d: (d.t0_session, d.observation_key, d.horizon_sessions))),
                    len(after_cutoff), len(cands))
