"""Read-only database pass of the research status (SELECT only; no write, DDL, lock, function call with side effects, or registry access).

Two layers, both bounded by the knowledge cutoff (`<stamp> <= cutoff`, so rows appended later never change the status):

  A. per-session AGGREGATES of every observation source, computed in SQL (counts by session and provenance, in-time / late / back-dated against the
     decision deadline, capture-run outcomes). It needs no dataset universe, so it works on an empty or brand-new history.
  B. the Slice 5 data-derived checks, obtained by assembling a PROVISIONAL dataset with the existing reader and assembler and applying
     `dataset_readiness.assess_data` -- the very function a real build uses. Nothing here re-implements point-in-time semantics.

The SQL deadline expression `((session_date + (1 + grace))::timestamp AT TIME ZONE 'UTC')` is the SQL spelling of `dataset_contract.decision_deadline`
(00:00 UTC of t0 + 1 + grace days); a test pins the two together at the boundary.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from research.lab import dataset_assemble as A
from research.lab import dataset_authoring as AUTH
from research.lab import dataset_contract as C
from research.lab import dataset_reader as RD
from research.lab import dataset_readiness as READY
from research.lab import manifest as M
from research.lab.manifest import LabError

PROVISIONAL_CODE_SHA = "0" * 40         # a provisional manifest is never emitted, registered or compared; the status has no code identity of its own
NO_SYMBOL_PLACEHOLDER = "_NO_OBSERVED_SYMBOLS_"

# tables each layer needs (checked by the CLI's preflight so a missing migration is named, not a stack trace)
EXTRA_TABLES = ("candidate_capture_run", "research_capture_activation")     # the status reads these beyond the Slice 5 source tables


def _dl(col: str, g1: str = "%(g1)s") -> str:
    return f"(({col} + {g1})::timestamp AT TIME ZONE 'UTC')"


def _day0(col: str) -> str:
    return f"({col}::timestamp AT TIME ZONE 'UTC')"


def _rows(cur, sql: str, params: Mapping[str, Any], cols: Sequence[str]) -> List[Dict[str, Any]]:
    cur.execute(sql, params)
    out = []
    for r in cur.fetchall():
        if len(r) != len(cols):
            raise LabError([f"status reader returned {len(r)} columns, expected {len(cols)}"])
        out.append(dict(zip(cols, r)))
    return out


def _ints(rows: List[Dict[str, Any]], *names: str) -> List[Dict[str, Any]]:
    for r in rows:
        for n in names:
            r[n] = int(r[n] or 0)
    return rows


# ------------------------------------------------------------------ layer A
def _candidates(cur, p) -> Dict[str, Any]:
    dl, d0 = _dl("c.session_date"), _day0("c.session_date")
    rows = _ints(_rows(cur,
        f"SELECT c.session_date, count(*), count(*) FILTER (WHERE c.captured_at < {dl}), count(*) FILTER (WHERE c.captured_at >= {dl}), "
        f"count(*) FILTER (WHERE c.captured_at < {d0}), count(DISTINCT c.symbol), "
        "count(*) FILTER (WHERE c.session_date < COALESCE((SELECT min(a.effective_from_session) FROM research_capture_activation a "
        "WHERE a.strategy_id = c.strategy_id AND a.state = 'enabled' AND a.set_at <= %(cutoff)s), DATE '9999-12-31')) "
        "FROM candidate_observation c JOIN strategies s ON s.id = c.strategy_id "
        "WHERE s.strategy_key = %(sk)s AND c.strategy_version = %(sv)s AND c.session_date BETWEEN %(lo)s AND %(hi)s AND c.captured_at <= %(cutoff)s "
        "GROUP BY c.session_date ORDER BY c.session_date", p,
        ("session", "n", "n_in_time", "n_late", "n_backdated", "n_symbols", "n_before_activation")),
        "n", "n_in_time", "n_late", "n_backdated", "n_symbols", "n_before_activation")
    # a run that finished after the cutoff was still 'running' AT the cutoff; the stored status only describes now
    st = "CASE WHEN r.run_finished_at IS NULL OR r.run_finished_at > %(cutoff)s THEN 'running' ELSE r.status END"
    dlr = _dl("r.session_date")
    runs = _ints(_rows(cur,
        f"SELECT r.session_date, {st} AS st, count(*), "
        f"count(*) FILTER (WHERE r.run_finished_at IS NOT NULL AND r.run_finished_at <= %(cutoff)s AND r.run_finished_at < {dlr}), "
        "COALESCE(sum(r.hash_drift), 0), COALESCE(sum(r.snapshot_drift), 0) "
        "FROM candidate_capture_run r JOIN strategies s ON s.id = r.strategy_id "
        "WHERE s.strategy_key = %(sk)s AND r.strategy_version = %(sv)s AND r.session_date BETWEEN %(lo)s AND %(hi)s AND r.run_started_at <= %(cutoff)s "
        f"GROUP BY r.session_date, {st} ORDER BY r.session_date, 2", p,
        ("session", "status", "n", "n_in_time", "hash_drift", "snapshot_drift")), "n", "n_in_time", "hash_drift", "snapshot_drift")
    act = _rows(cur,
        "SELECT a.state, a.effective_from_session FROM research_capture_activation a JOIN strategies s ON s.id = a.strategy_id "
        "WHERE s.strategy_key = %(sk)s AND a.set_at <= %(cutoff)s ORDER BY a.effective_from_session, a.id", p, ("state", "effective_from_session"))
    return {"rows": rows, "runs": runs, "activation": act}


def _provenance(cur, table: str, stamp: str, where: str, extra_cols: str, extra_names: Sequence[str], p) -> List[Dict[str, Any]]:
    dl, d0 = _dl("session_date"), _day0("session_date")
    sql = (f"SELECT session_date, provenance, count(*), count(*) FILTER (WHERE {stamp} < {dl}), count(*) FILTER (WHERE {stamp} >= {dl}), "
           f"count(*) FILTER (WHERE {stamp} < {d0}){extra_cols} FROM {table} WHERE {where} AND session_date BETWEEN %(lo)s AND %(hi)s AND {stamp} <= %(cutoff)s "
           "GROUP BY session_date, provenance ORDER BY session_date, provenance")
    return _ints(_rows(cur, sql, p, ("session", "provenance", "n", "n_in_time", "n_late", "n_backdated", *extra_names)),
                 "n", "n_in_time", "n_late", "n_backdated", *extra_names)


def _labels(cur, p) -> Dict[str, Any]:
    dl = _dl("c.session_date")
    rows = _ints(_rows(cur,
        "SELECT c.session_date, count(*), count(*) FILTER (WHERE l.label_status = 'final'), count(*) FILTER (WHERE l.label_status = 'void'), "
        "count(*) FILTER (WHERE l.observation_id IS NOT NULL AND l.label_status NOT IN ('final', 'void')), count(*) FILTER (WHERE l.observation_id IS NULL) "
        "FROM candidate_observation c JOIN strategies s ON s.id = c.strategy_id "
        "LEFT JOIN forward_return_label l ON l.observation_id = c.id AND l.horizon_sessions = %(ph)s AND l.label_version = %(lv)s "
        "AND l.methodology_version = %(lm)s AND l.computed_at <= %(cutoff)s "
        f"WHERE s.strategy_key = %(sk)s AND c.strategy_version = %(sv)s AND c.session_date BETWEEN %(lo)s AND %(hi)s AND c.captured_at < {dl} "
        "AND c.captured_at <= %(cutoff)s GROUP BY c.session_date ORDER BY c.session_date", p,
        ("session", "n_candidates", "n_final", "n_void", "n_other", "n_unlabelled")), "n_candidates", "n_final", "n_void", "n_other", "n_unlabelled")
    cur.execute(
        "SELECT count(*), (array_agg(DISTINCT l.t0_session ORDER BY l.t0_session))[1:5] FROM forward_return_label l "
        "JOIN candidate_observation c ON c.id = l.observation_id JOIN strategies s ON s.id = c.strategy_id "
        "WHERE s.strategy_key = %(sk)s AND c.strategy_version = %(sv)s AND l.label_version = %(lv)s AND l.methodology_version = %(lm)s "
        "AND l.t0_session BETWEEN %(lo)s AND %(hi)s AND l.computed_at <= %(cutoff)s AND l.horizon_session IS NOT NULL "
        "AND l.computed_at < (l.horizon_session::timestamp AT TIME ZONE 'UTC')", p)
    n, ex = cur.fetchone()
    return {"rows": rows, "n_computed_before_horizon": int(n or 0), "computed_before_horizon_examples": list(ex or [])}


def _stamp_session(ts: Optional[datetime]) -> Optional[date]:
    return ts.astimezone(timezone.utc).date() if ts is not None else None


def _events(cur, cfg: C.DatasetConfig, p) -> Dict[str, Any]:
    if cfg.catalyst is None:
        return {"n": 0, "enabled_in_config": False}
    cur.execute("SELECT count(*), count(DISTINCT r.event_key), min(r.ingested_at), max(r.ingested_at), count(*) FILTER (WHERE r.known_at IS NULL) "
                "FROM market_event_revision r WHERE r.ingested_at <= %(cutoff)s", p)
    n, ne, lo, hi, nk = cur.fetchone()
    by = {}
    for col, key in (("provenance", "by_provenance"), ("pit_grade", "by_pit_grade")):
        cur.execute(f"SELECT {col}, count(*) FROM market_event_revision r WHERE r.ingested_at <= %(cutoff)s GROUP BY 1 ORDER BY 1", p)
        by[key] = {str(k): int(v) for k, v in cur.fetchall()}
    cur.execute("SELECT count(*) FROM catalyst_classification k WHERE k.classifier = %(cl)s AND k.classifier_version = %(cv)s AND k.classified_at <= %(cutoff)s",
                {**p, "cl": cfg.catalyst.classifier, "cv": cfg.catalyst.classifier_version})
    return {"n": int(n), "enabled_in_config": True, "n_events": int(ne), "first_stamp_session": _stamp_session(lo), "latest_stamp_session": _stamp_session(hi),
            "revisions_with_unknown_known_at": int(nk), "classifications_for_pinned_classifier": int(cur.fetchone()[0]), **by}


def _first_seen(cur, cfg: C.DatasetConfig, p) -> Dict[str, Any]:
    if not cfg.first_seen:
        return {"n": 0, "enabled_in_config": False}
    pairs = sorted({(f.source, f.dataset) for f in cfg.first_seen})
    n, lo, hi, per = 0, None, None, {}
    for src, ds in pairs:
        cur.execute("SELECT count(*), min(observed_at), max(observed_at) FROM source_observation WHERE source = %s AND dataset = %s AND observed_at <= %s",
                    (src, ds, p["cutoff"]))
        c, a, b = cur.fetchone()
        per[f"{src}/{ds}"] = int(c)
        n += int(c)
        lo = a if lo is None or (a is not None and a < lo) else lo
        hi = b if hi is None or (b is not None and b > hi) else hi
    return {"n": n, "enabled_in_config": True, "first_stamp_session": _stamp_session(lo), "latest_stamp_session": _stamp_session(hi), "by_source_dataset": per}


# ------------------------------------------------------------------ layer B
def _contract(cur, authoring: AUTH.Authoring, calendar: Sequence[date], cal_source: str, members: Optional[Sequence[str]],
              now: datetime, cfg: C.DatasetConfig) -> Dict[str, Any]:
    try:
        spec = AUTH.to_spec(authoring, code_sha=PROVISIONAL_CODE_SHA, code_tree_clean=True, calendar=calendar, calendar_source=cal_source,
                            universe_members=members, input_hashes=AUTH.PROVISIONAL_INPUT_HASHES)
        manifest = M.build_manifest(spec, now=now)
        pcfg = C.config_for(manifest.document)
        raw = RD.read_raw(cur, manifest, pcfg)
        rows = A.assemble(manifest, pcfg, raw).rows
    except LabError as e:
        return {"evaluated": False, "reason": "a provisional dataset could not be assembled: " + "; ".join(e.problems)}
    prim = [r for r in rows if r["horizon_sessions"] == pcfg.primary_horizon]
    if not prim:
        return {"evaluated": False, "reason": "no primary-horizon dataset row exists yet (no observed candidate with a known context); nothing to evaluate"}
    doc = manifest.document
    d = READY.assess_data(cfg=pcfg, windows_train_end=doc["windows"]["train"][1], maturity=doc["label_maturity_session"], prim=prim)
    return {"evaluated": True, "provisional_rows": len(prim), "data_checks": d["checks"], "earliest_trustworthy_pit_date": d["earliest"]["all_sources"],
            "coverage": d["coverage"], "label_maturity": d["labels"], "sample": d["sample"], "sector_unsafe_cells": d["sector_unsafe_cells"]}


# ------------------------------------------------------------------ entry point
def parse_cfg(authoring: AUTH.Authoring, members: Sequence[str]) -> C.DatasetConfig:
    """The spec's config with the observed universe filled in. An empty observed universe uses a placeholder member ONLY so the config parses:
    the placeholder never reaches a query that matters (nothing is observed for it) and never a manifest."""
    cfg_doc = dict(authoring.config)
    cfg_doc["universe_members"] = sorted(set(members)) or [NO_SYMBOL_PLACEHOLDER]
    return C.parse_config(cfg_doc, authoring.label_horizons)


def collect(conn, authoring: AUTH.Authoring, calendar: Sequence[date], cal_source: str, now: datetime) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Everything the pure status builder needs, read inside a read-only session. Returns (inputs, volatile_context)."""
    cutoff = authoring.knowledge_cutoff_at.astimezone(timezone.utc)
    if cutoff > now:
        raise LabError([f"the spec's knowledge_cutoff_at {cutoff.isoformat()} is in the future of the database clock {now.isoformat()}"])
    cal = sorted(calendar)
    strat = authoring.config.get("strategy")
    if not (isinstance(strat, dict) and strat.get("key") and strat.get("version")):
        raise LabError(["config.strategy {key, version} is required"])
    with RD.read_only_session(conn):
        cur = conn.cursor()
        if authoring.universe_from_db:
            members = RD.read_candidate_symbols(cur, strat["key"], strat["version"], cal[0], cal[-1], cutoff)
        else:
            members = list(authoring.config.get("universe_members") or [])
        cfg = parse_cfg(authoring, members)
        p = {"sk": cfg.strategy_key, "sv": cfg.strategy_version, "lo": cal[0], "hi": cal[-1], "cutoff": cutoff, "g1": 1 + cfg.availability_grace_days,
             "ph": cfg.primary_horizon, "lv": authoring.label_version, "lm": authoring.label_methodology_version}
        inp: Dict[str, Any] = {"cutoff": cutoff, "calendar": cal, "calendar_source": cal_source, "config": cfg, "maturity_session": authoring.label_maturity_session,
                               "plan": {"embargo_sessions": authoring.embargo_sessions, "purge_sessions": authoring.purge_sessions,
                                        "windows": {k: [v[0], v[1]] for k, v in (("train", authoring.windows.train), ("validation", authoring.windows.validation),
                                                                                 ("test", authoring.windows.test))}}}
        inp["universe"] = {"rule": str(cfg.universe_rule), "n_symbols": len(members), "hash": M.universe_hash(members, cfg.universe_rule) if members else None,
                           "from_database": bool(authoring.universe_from_db)}
        inp["candidates"] = _candidates(cur, p)
        inp["market"] = {"rows": _provenance(cur, "market_snapshot", "captured_at", "feature_set_version = %(mf)s", "", (), {**p, "mf": cfg.market_feature_set_version})
                         if cfg.market_feature_set_version else []}
        inp["sector"] = {"rows": _provenance(cur, "sector_snapshot", "captured_at", "feature_set_version = %(sf)s", ", count(DISTINCT sector)", ("n_sectors",),
                                             {**p, "sf": cfg.sector_feature_set_version}) if cfg.sector_feature_set_version else []}
        rs = cfg.stock_rs
        inp["stock_rs"] = {"rows": _provenance(
            cur, "stock_relative_strength", "created_at", "model_version = %(rm)s AND feature_set_version = %(rf)s AND horizon_sessions = %(rh)s",
            ", count(DISTINCT run_content_hash), count(*) FILTER (WHERE state = 'ok' AND sector IS NULL AND sector_pit_safe IS FALSE), "
            "count(*) FILTER (WHERE state = 'ok' AND sector IS NOT NULL AND sector_pit_safe IS FALSE)",
            ("n_run_hashes", "n_ok_no_sector", "n_ok_sector_unsafe"), {**p, "rm": rs.model_version, "rf": rs.feature_set_version, "rh": rs.horizon_sessions})
            if rs else []}
        inp["labels"] = _labels(cur, p)
        inp["catalyst"] = _events(cur, cfg, p)
        inp["first_seen"] = _first_seen(cur, cfg, p)
        inp["contract"] = _contract(cur, authoring, cal, cal_source, members if members else None, now, cfg)
        context = {"database_time": now, "post_cutoff_rows": _post_cutoff(cur, cfg, p, cutoff)}
    conn.rollback()
    return inp, context


def _post_cutoff(cur, cfg: C.DatasetConfig, p, cutoff: datetime) -> Dict[str, int]:
    """Volatile metadata only: how many in-scope rows arrived after the cutoff (never part of the canonical status)."""
    q = {"candidates": ("candidate_observation c JOIN strategies s ON s.id = c.strategy_id", "s.strategy_key = %(sk)s AND c.strategy_version = %(sv)s", "c.captured_at"),
         "market": ("market_snapshot", "feature_set_version = %(mf)s", "captured_at"),
         "sector": ("sector_snapshot", "feature_set_version = %(sf)s", "captured_at"),
         "stock_rs": ("stock_relative_strength", "model_version = %(rm)s AND feature_set_version = %(rf)s AND horizon_sessions = %(rh)s", "created_at")}
    extra = {"mf": cfg.market_feature_set_version, "sf": cfg.sector_feature_set_version,
             "rm": cfg.stock_rs.model_version if cfg.stock_rs else None, "rf": cfg.stock_rs.feature_set_version if cfg.stock_rs else None,
             "rh": cfg.stock_rs.horizon_sessions if cfg.stock_rs else None}
    out: Dict[str, int] = {}
    for name, (frm, where, stamp) in q.items():
        cur.execute(f"SELECT count(*) FROM {frm} WHERE {where} AND {stamp} > %(cutoff)s", {**p, **extra})
        out[name] = int(cur.fetchone()[0])
    return out
