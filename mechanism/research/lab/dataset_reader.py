"""Read-only database pass of the dataset builder (the only module of the harness that touches PostgreSQL besides `dataset_runner`).

SELECT only. Every read is bounded by the manifest: the knowledge cutoff (`<stamp> <= cutoff`, so a row appended after the manifest was
frozen can never enter a dataset or change a fingerprint), the manifest calendar's span, the universe, and the pinned strategy / label /
feature / classifier versions. Nothing is read as "latest": the manifest decides what is asked, the PIT rule in `dataset_contract` decides
per observation what may be used.

`read_only_session` makes the DATABASE enforce that (a write inside it fails), rather than relying on this file being well behaved.

Each source returns dict rows with exactly the columns of `dataset_contract.source_columns(source)`, in a deterministic order.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Mapping, Sequence, Tuple

from research.lab import dataset_contract as C
from research.lab.manifest import LabError, Manifest


@contextmanager
def read_only_session(conn) -> Iterator[None]:
    """Run the body in a read-only transaction mode (any write raises ReadOnlySqlTransaction) and restore the connection afterwards."""
    conn.rollback()
    previous = conn.readonly
    conn.set_session(readonly=True)
    try:
        yield
    finally:
        conn.rollback()
        conn.set_session(readonly=bool(previous))


def _fetch(cur, sql: str, params: Sequence[Any], columns: Sequence[str]) -> List[Dict[str, Any]]:
    cur.execute(sql, tuple(params))
    out = []
    for r in cur.fetchall():
        if len(r) != len(columns):
            raise LabError([f"reader returned {len(r)} columns, the contract has {len(columns)}"])
        out.append(dict(zip(columns, r)))
    return out


def _span(manifest: Manifest) -> Tuple[date, date]:
    return manifest.calendar[0], manifest.calendar[-1]


def _cutoff(manifest: Manifest) -> datetime:
    return datetime.fromisoformat(manifest.document["knowledge_cutoff_at"]).astimezone(timezone.utc)


def _read_candidates(cur, m: Manifest, cfg: C.DatasetConfig, *, after: bool = False) -> List[Dict[str, Any]]:
    lo, hi = _span(m)
    op = ">" if after else "<="
    sql = (
        "SELECT s.strategy_key, c.strategy_version, c.symbol, c.session_date, c.direction, c.signal_type, c.triggered, c.passed_guard, "
        "c.tracked_intent, c.atr_source, c.breakout_dist_atr, c.distance_to_channel_pct, c.quality_grade, c.alignment_score, c.captured_at, "
        "f.sector, f.sector_source, f.sector_asof, f.captured_at "
        "FROM candidate_observation c JOIN strategies s ON s.id = c.strategy_id JOIN feature_snapshot f ON f.id = c.snapshot_id "
        "WHERE s.strategy_key = %s AND c.strategy_version = %s AND c.symbol = ANY(%s) AND c.session_date BETWEEN %s AND %s "
        f"AND c.captured_at {op} %s ORDER BY c.session_date, c.symbol, c.direction")
    return _fetch(cur, sql, (cfg.strategy_key, cfg.strategy_version, list(cfg.universe_members), lo, hi, _cutoff(m)), C.SOURCE_COLUMNS["candidates"])


def _read_labels(cur, m: Manifest, cfg: C.DatasetConfig, *, after: bool = False) -> List[Dict[str, Any]]:
    lo, hi = _span(m)
    d = m.document
    op = ">" if after else "<="
    sql = (
        "SELECT s.strategy_key, c.strategy_version, l.symbol, l.t0_session, l.direction, l.horizon_sessions, l.label_version, "
        "l.methodology_version, l.label_status, l.void_reason, l.horizon_session, l.computed_as_of_session, l.calendar_source, "
        "l.raw_return, l.directional_return, l.benchmark_state, l.directional_excess_return, l.path_state, l.mfe, l.mae, "
        "l.data_quality, l.input_hash, l.computed_at "
        "FROM forward_return_label l JOIN candidate_observation c ON c.id = l.observation_id JOIN strategies s ON s.id = c.strategy_id "
        "WHERE s.strategy_key = %s AND c.strategy_version = %s AND l.symbol = ANY(%s) AND l.t0_session BETWEEN %s AND %s "
        "AND l.label_version = %s AND l.methodology_version = %s AND l.horizon_sessions = ANY(%s) "
        f"AND l.computed_at {op} %s ORDER BY l.t0_session, l.symbol, l.direction, l.horizon_sessions")
    return _fetch(cur, sql, (cfg.strategy_key, cfg.strategy_version, list(cfg.universe_members), lo, hi, d["label_version"],
                             d["label_methodology_version"], list(d["label_horizons"]), _cutoff(m)), C.SOURCE_COLUMNS["labels"])


def _read_market(cur, m: Manifest, cfg: C.DatasetConfig, *, after: bool = False) -> List[Dict[str, Any]]:
    lo, hi = _span(m)
    op = ">" if after else "<="
    sql = ("SELECT session_date, provenance, feature_set_version, regime_model_version, rs_model_version, regime_state, regime_score, "
           "regime_strength, regime_components, content_hash, captured_at FROM market_snapshot "
           f"WHERE feature_set_version = %s AND session_date BETWEEN %s AND %s AND captured_at {op} %s "
           "ORDER BY session_date, provenance, captured_at")
    return _fetch(cur, sql, (cfg.market_feature_set_version, lo, hi, _cutoff(m)), C.SOURCE_COLUMNS["market"])


def _read_sector(cur, m: Manifest, cfg: C.DatasetConfig, *, after: bool = False) -> List[Dict[str, Any]]:
    lo, hi = _span(m)
    op = ">" if after else "<="
    sql = ("SELECT session_date, provenance, feature_set_version, sector, n_valid_20, sec_ret_20, sec_vs_spx_20, sec_vs_univ_20, rank_20, "
           "captured_at FROM sector_snapshot "
           f"WHERE feature_set_version = %s AND session_date BETWEEN %s AND %s AND captured_at {op} %s "
           "ORDER BY session_date, sector, provenance, captured_at")
    return _fetch(cur, sql, (cfg.sector_feature_set_version, lo, hi, _cutoff(m)), C.SOURCE_COLUMNS["sector"])


def _read_stock_rs(cur, m: Manifest, cfg: C.DatasetConfig, *, after: bool = False) -> List[Dict[str, Any]]:
    lo, hi = _span(m)
    rs = cfg.stock_rs
    op = ">" if after else "<="
    sql = ("SELECT session_date, symbol, horizon_sessions, model_version, feature_set_version, provenance, sector, sector_pit_safe, state, "
           "ret_pct, vs_spx_pp, vs_sector_pp, rs_percentile, n_universe_valid, run_content_hash, created_at FROM stock_relative_strength "
           "WHERE model_version = %s AND feature_set_version = %s AND horizon_sessions = %s AND symbol = ANY(%s) "
           f"AND session_date BETWEEN %s AND %s AND created_at {op} %s ORDER BY session_date, symbol, provenance, created_at")
    return _fetch(cur, sql, (rs.model_version, rs.feature_set_version, rs.horizon_sessions, list(cfg.universe_members), lo, hi, _cutoff(m)),
                  C.SOURCE_COLUMNS["stock_rs"])


def _read_events(cur, m: Manifest, cfg: C.DatasetConfig, *, after: bool = False) -> List[Dict[str, Any]]:
    lo, hi = _span(m)
    lo = lo - timedelta(days=cfg.catalyst.lookback_days)
    op = ">" if after else "<="
    sql = ("SELECT r.event_key, e.symbol, e.event_type, r.revision, r.event_time, r.known_at, r.known_at_basis, r.pit_grade, r.ingested_at, "
           "r.status, r.provenance, r.payload_hash FROM market_event_revision r JOIN market_event e ON e.event_key = r.event_key "
           f"WHERE e.symbol = ANY(%s) AND r.event_time BETWEEN %s AND %s AND r.ingested_at {op} %s ORDER BY r.event_key, r.revision")
    return _fetch(cur, sql, (list(cfg.universe_members), lo, hi, _cutoff(m)), C.SOURCE_COLUMNS["events"])


def _read_classifications(cur, m: Manifest, cfg: C.DatasetConfig, *, after: bool = False) -> List[Dict[str, Any]]:
    lo, hi = _span(m)
    lo = lo - timedelta(days=cfg.catalyst.lookback_days)
    op = ">" if after else "<="
    sql = ("SELECT k.event_key, r.revision, k.classifier, k.classifier_version, k.method, k.label, k.confidence, k.fact_hash, k.classified_at "
           "FROM catalyst_classification k JOIN market_event_revision r ON r.id = k.revision_id JOIN market_event e ON e.event_key = r.event_key "
           "WHERE e.symbol = ANY(%s) AND k.classifier = %s AND k.classifier_version = %s AND r.event_time BETWEEN %s AND %s "
           f"AND k.classified_at {op} %s ORDER BY k.event_key, r.revision, k.classifier, k.classifier_version")
    return _fetch(cur, sql, (list(cfg.universe_members), cfg.catalyst.classifier, cfg.catalyst.classifier_version, lo, hi, _cutoff(m)),
                  C.SOURCE_COLUMNS["classifications"])


def _read_first_seen(cur, m: Manifest, cfg: C.DatasetConfig, *, after: bool = False) -> List[Dict[str, Any]]:
    op = ">" if after else "<="
    pairs = sorted({(f.source, f.dataset) for f in cfg.first_seen})
    out: List[Dict[str, Any]] = []
    for source, dataset in pairs:
        sql = ("SELECT series_key, source, dataset, subject_id, period_key, seq, value, value_hash, observed_at FROM source_observation "
               "WHERE subject_type = 'symbol' AND subject_id = ANY(%s) AND source = %s AND dataset = %s "
               f"AND observed_at {op} %s ORDER BY series_key, seq")
        out += _fetch(cur, sql, (list(cfg.universe_members), source, dataset, _cutoff(m)), C.SOURCE_COLUMNS["first_seen"])
    return out


def _read_sector_history(cur, m: Manifest, cfg: C.DatasetConfig, *, after: bool = False) -> List[Dict[str, Any]]:
    """The append-only sector history of the configured source: every observation (the whole chain prefix is needed to verify it, so there is no lower
    bound) and every same-value `confirmed_head` poll, each bounded by the knowledge cutoff and by the manifest span's last session. Failed and
    ambiguous polls are NOT read: they never change what the system believes, only its age, which the absence of a confirmation already expresses."""
    _, hi = _span(m)
    op = ">" if after else "<="
    src = cfg.sector_history.source
    syms = list(cfg.universe_members)
    sql = (
        "SELECT 'observation'::text, o.symbol, o.source, o.seq, o.sector, o.sector_raw, o.no_sector_reason, o.change_kind, o.captured_at, "
        "o.effective_session, o.source_asof, o.provenance, o.raw_payload_hash, o.prev_value_hash, o.value_hash, o.run_id "
        "FROM sector_observation o "
        f"WHERE o.symbol = ANY(%s) AND o.source = %s AND o.effective_session <= %s AND o.captured_at {op} %s "
        "UNION ALL "
        "SELECT 'confirmation'::text, p.symbol, p.source, o.seq, o.sector, NULL::varchar, NULL::varchar, NULL::varchar, p.attempted_at, "
        "(p.attempted_at AT TIME ZONE 'UTC')::date, NULL::timestamptz, NULL::varchar, NULL::char(64), NULL::char(64), o.value_hash, p.run_id "
        "FROM sector_poll p JOIN sector_observation o ON o.id = p.observation_id "
        f"WHERE p.chain_effect = 'confirmed_head' AND p.symbol = ANY(%s) AND p.source = %s "
        f"AND (p.attempted_at AT TIME ZONE 'UTC')::date <= %s AND p.attempted_at {op} %s "
        "ORDER BY 1, 2, 4, 9, 16")
    c = _cutoff(m)
    return _fetch(cur, sql, (syms, src, hi, c, syms, src, hi, c), C.HISTORY_COLUMNS)


_READERS = {"candidates": _read_candidates, "labels": _read_labels, "market": _read_market, "sector": _read_sector, "stock_rs": _read_stock_rs,
            "events": _read_events, "classifications": _read_classifications, "first_seen": _read_first_seen,
            C.HISTORY_SOURCE: _read_sector_history}


def sources_for(cfg: C.DatasetConfig) -> Tuple[str, ...]:
    return C.enabled_db_sources(cfg)


def read_raw(cur, manifest: Manifest, cfg: C.DatasetConfig) -> Dict[str, List[Dict[str, Any]]]:
    """Every source the config enables, cutoff-bounded. Sources the config disables are returned as empty lists (never read)."""
    wanted = set(sources_for(cfg))
    out = {s: (_READERS[s](cur, manifest, cfg) if s in wanted else []) for s in C.SOURCE_COLUMNS}
    if C.HISTORY_SOURCE in wanted:                                  # present only when the config opts in
        out[C.HISTORY_SOURCE] = _READERS[C.HISTORY_SOURCE](cur, manifest, cfg)
    return out


def post_cutoff_counts(cur, manifest: Manifest, cfg: C.DatasetConfig) -> Dict[str, int]:
    """How many in-scope rows per source arrived AFTER the knowledge cutoff. Run metadata only: it is deliberately outside the dataset, the
    report and every hash (a later append must not change them), but a run that sees appends can say so."""
    wanted = set(sources_for(cfg))
    names = sorted(C.SOURCE_COLUMNS) + ([C.HISTORY_SOURCE] if C.HISTORY_SOURCE in wanted else [])
    return {s: (len(_READERS[s](cur, manifest, cfg, after=True)) if s in wanted else 0) for s in names}


def read_candidate_symbols(cur, strategy_key: str, strategy_version: str, lo: date, hi: date, cutoff: datetime) -> List[str]:
    """Authoring helper: the distinct symbols the strategy has candidate observations for inside [lo, hi] captured by the cutoff, sorted.
    This is the OBSERVED candidate universe -- not the tradable universe -- and says nothing about symbols that were never observed."""
    cur.execute("SELECT DISTINCT c.symbol FROM candidate_observation c JOIN strategies s ON s.id = c.strategy_id "
                "WHERE s.strategy_key = %s AND c.strategy_version = %s AND c.session_date BETWEEN %s AND %s AND c.captured_at <= %s "
                "ORDER BY c.symbol", (strategy_key, strategy_version, lo, hi, cutoff.astimezone(timezone.utc)))
    return [r[0] for r in cur.fetchall()]
