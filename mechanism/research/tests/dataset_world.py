"""A small, deterministic research world for the dataset-builder tests (explicit imports only; never a conftest name).

`dataset_env` gives a throwaway schema holding every table the builder reads (migrations 19-30 plus the market-data base tables). `seed_world`
fills it with a few symbols across the train / validation / test windows of `lab_samples`, deliberately including the awkward cases: late
market snapshots, absent and reconstructed context, unknown-availability events, unclassified events, void labels, and rows that sit in the
embargo gap or whose label window crosses a boundary.

Trigger-stamped tables (relative strength, events, classifications, first-seen observations) get their availability stamps from the database
clock in production. The tests need HISTORICAL stamps, so `seed_world` disables exactly those stamp triggers inside the throwaway schema for
the duration of the seeding and re-enables them (ENABLE ALWAYS) afterwards. That is test scaffolding for a disposable schema; nothing here
touches a production object.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from conftest import Seed, _schema_env
from lab_samples import CAL, MAT, NOW, SHA, TE, TR, VA, weekdays  # noqa: F401  (re-exported for the tests)
from research.lab import dataset_contract as C
from research.lab import dataset_reader as RD
from research.lab import dataset_runner as R
from research.lab import manifest as M

MIGRATIONS = [
    "create_trading_schema.sql", "add_market_data_tables.sql", "add_signal_ledger_tables.sql", "add_strategy_identity_release_a.sql",
    "add_signal_ledger_eval_flags.sql", "add_research_observation_tables.sql", "add_market_snapshot_tables.sql",
    "add_market_event_tables.sql", "add_forward_return_label_table.sql", "add_source_observation_tables.sql",
    "add_catalyst_classification_table.sql", "add_stock_relative_strength_table.sql", "add_dataset_experiment_registry_tables.sql",
]
STAMP_FUNCTIONS = ("research_rs_stamp", "research_observation_stamp", "research_market_event_stamp", "research_classification_consistency")

UNIVERSE = ("A", "B", "C")
SECTOR_OF = {"A": "Tech", "B": "Tech", "C": "Energy"}
CUTOFF = datetime(MAT.year, MAT.month, MAT.day, 12, tzinfo=timezone.utc)
HORIZONS = (5, 20, 60)
PRIMARY = 20
STRATEGY = ("donchian_breakout", "v1")

TRAIN_IDX = tuple(range(10, 171, 5))
VAL_IDX = tuple(range(270, 331, 3))
TEST_IDX = tuple(range(430, 491, 3))
EDGE_IDX = (185, 230, 600)             # crosses the train end / embargo gap / after maturity: dropped, never used
ALL_IDX = TRAIN_IDX + VAL_IDX + TEST_IDX + EDGE_IDX


def at(idx, hour=21, minute=0):
    d = CAL[idx]
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc)


def sha(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def config(**over):
    cfg = {"schema": C.CONFIG_SCHEMA, "strategy": {"key": STRATEGY[0], "version": STRATEGY[1]}, "universe_rule": "rule",
           "universe_members": list(UNIVERSE), "availability_grace_days": 1, "min_sample": 10, "primary_horizon": PRIMARY,
           "reconstructed_policy": "exclude", "market": {"feature_set_version": "mi_v2"}, "sector": {"feature_set_version": "mi_v2"},
           "stock_rs": {"model_version": "rs_v1", "feature_set_version": "mi_v2", "horizon_sessions": PRIMARY},
           "catalyst": {"lookback_days": 30, "classifier": "items_rule", "classifier_version": "v1"},
           "first_seen": [{"source": "vendor_x", "dataset": "earnings_date", "fields": ["date"]}]}
    cfg.update(over)
    return cfg


def make_spec(input_hashes, cfg=None, **over):
    cfg = cfg if cfg is not None else config()
    base = dict(dataset_name="lab_ds", dataset_version="v1", code_sha=SHA, code_tree_clean=True, label_version="fwd_v1",
                label_methodology_version="fwd_v1.m1", label_horizons=HORIZONS, feature_versions={"mi_v2": "1"},
                universe_id="u", universe_hash=M.universe_hash(list(cfg["universe_members"]), cfg["universe_rule"]),
                knowledge_cutoff_at=CUTOFF, label_maturity_session=MAT, windows=M.Windows(TR, VA, TE), embargo_sessions=60,
                purge_sessions=0, calendar_source="cal", calendar=CAL, input_hashes=input_hashes, config=cfg)
    base.update(over)
    return M.DatasetSpec(**base)


def author_manifest(conn, cfg=None, **over):
    """Build the manifest for the database as it stands: provisional bounds -> real input hashes -> the real manifest (not yet registered)."""
    cfg = cfg if cfg is not None else config()
    provisional = M.build_manifest(make_spec({"labels": "b" * 64}, cfg, **over), now=NOW)
    parsed = C.config_for(provisional.document)
    hashes = R.author_input_hashes(conn, provisional, parsed)
    return M.build_manifest(make_spec(hashes, cfg, **over), now=NOW)


def dataset_env():
    yield from _schema_env(MIGRATIONS)


# ------------------------------------------------------------------ seeding
def _stamp_triggers(cur):
    cur.execute("SELECT t.tgname, c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_proc p ON p.oid = t.tgfoid "
                "WHERE p.proname = ANY(%s) AND NOT t.tgisinternal AND c.relnamespace = current_schema()::regnamespace", (list(STAMP_FUNCTIONS),))
    return cur.fetchall()


class World:
    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()
        self.seed = Seed(conn)
        self.obs = {}              # (symbol, idx) -> observation id
        self.runs = {}

    def x(self, sql, params=None):
        self.cur.execute(sql, params)
        return self.cur

    def run(self, idx):
        if idx not in self.runs:
            self.runs[idx] = self.seed.run(CAL[idx])
        return self.runs[idx]

    # -- candidates + labels -------------------------------------------------------------------------------------
    def candidate(self, symbol, idx, *, captured_at=None, with_labels=True):
        k = UNIVERSE.index(symbol) if symbol in UNIVERSE else 7
        short = symbol == "C"
        triggered = (idx + k) % 2 == 0
        direction = -1 if short else 1
        signal = ("bearish_breakout" if triggered else "near_bearish") if short else ("bullish_breakout" if triggered else "near_bullish")
        stamp = captured_at or at(idx)
        snap = self.seed.snapshot(symbol, CAL[idx], sector=SECTOR_OF.get(symbol), sector_source="test_map" if symbol in SECTOR_OF else None,
                                  sector_asof=CAL[idx] if symbol in SECTOR_OF else None, captured_at=stamp)
        guard_ok = (idx + k) % 5 != 0
        oid = self.seed.observation(snap, self.run(idx), symbol, CAL[idx], direction, signal_type=signal, triggered=triggered,
                                    tracked_intent=triggered, passed_guard=guard_ok, guard_reasons=None if guard_ok else ["liquidity"],
                                    atr_source="measured", breakout_dist_atr=round(0.1 + ((idx + k) % 9) * 0.05, 2),
                                    quality_grade="ABCDF"[(idx + k) % 5], alignment_score=50 + (idx + k) % 40, captured_at=stamp)
        self.obs[(symbol, idx)] = oid
        if with_labels:
            for h in HORIZONS:
                self.label(symbol, idx, h, oid, direction)
        return oid

    def label(self, symbol, idx, h, oid=None, direction=None, *, computed_at=None, raw=None, void=False):
        oid = oid or self.obs[(symbol, idx)]
        k = UNIVERSE.index(symbol)
        direction = direction if direction is not None else (-1 if symbol == "C" else 1)
        hz = idx + h
        stamp = computed_at or at(hz, 22)
        common = dict(oid=oid, h=h, sym=symbol, dr=direction, t0=CAL[idx], hs=CAL[hz], asof=CAL[hz], bars=h, ih=sha("label", symbol, idx, h),
                      ca=stamp)
        if void or (idx % 29 == 0 and h == 5):
            self.x("INSERT INTO forward_return_label (observation_id, horizon_sessions, label_version, methodology_version, label_status, "
                   "void_reason, symbol, direction, t0_session, horizon_session, computed_as_of_session, benchmark_symbol, benchmark_state, "
                   "path_state, bars_expected, data_quality, price_source, price_basis, calendar_source, input_hash, code_ref, computed_at) "
                   "VALUES (%(oid)s, %(h)s, 'fwd_v1', 'fwd_v1.m1', 'void', 'missing_horizon_bar', %(sym)s, %(dr)s, %(t0)s, %(hs)s, %(asof)s, "
                   "'^GSPC', 'not_evaluated', 'not_evaluated', %(bars)s, 'not_evaluated', 'test', 'raw', 'cal', %(ih)s, 'test', %(ca)s)", common)
            return
        r = raw if raw is not None else ((idx * 7 + k * 3 + h) % 21 - 10) / 200.0
        bench = 0.01
        quality = "basis_adjusted" if (idx + h) % 31 == 0 else "ok"
        self.x("INSERT INTO forward_return_label (observation_id, horizon_sessions, label_version, methodology_version, label_status, "
               "void_reason, symbol, direction, t0_session, horizon_session, computed_as_of_session, reference_close, entry_close_captured, "
               "basis_ratio, horizon_close, raw_return, directional_return, benchmark_symbol, benchmark_state, benchmark_return, excess_return, "
               "directional_excess_return, path_state, mfe, mae, bars_expected, bars_observed, data_quality, price_source, price_basis, "
               "calendar_source, input_hash, code_ref, computed_at) VALUES (%(oid)s, %(h)s, 'fwd_v1', 'fwd_v1.m1', 'final', NULL, %(sym)s, "
               "%(dr)s, %(t0)s, %(hs)s, %(asof)s, 10.5, 10.5, 1.0, %(hc)s, %(r)s, %(dret)s, '^GSPC', 'ok', %(b)s, %(ex)s, %(dex)s, 'complete', "
               "0.04, -0.03, %(bars)s, %(bars)s, %(q)s, 'test', 'raw', 'cal', %(ih)s, 'test', %(ca)s)",
               dict(common, hc=round(10.5 * (1 + r), 4), r=r, dret=direction * r, b=bench, ex=r - bench, dex=direction * (r - bench), q=quality))

    # -- market context ------------------------------------------------------------------------------------------
    def market(self, idx, *, provenance="observed", captured_at=None, state=None):
        d = CAL[idx]
        rec = provenance == "reconstructed"
        basis = "reconstructed from the current sector map (test)" if rec else None
        self.x("INSERT INTO universe_snapshot (session_date, provenance, n_symbols, n_classified, sector_map, sector_source, sector_asof_rule, "
               "sector_pit_safe, content_hash, code_ref, reconstruction_basis, captured_at) VALUES (%s, %s, 3, 3, %s::jsonb, 'test_map', 'test', "
               "FALSE, %s, 'test', %s, %s) ON CONFLICT (session_date, provenance) DO NOTHING",
               (d, provenance, json.dumps(SECTOR_OF), sha("u", idx, provenance), basis, captured_at or at(idx)))
        uid = self.x("SELECT id FROM universe_snapshot WHERE session_date = %s AND provenance = %s", (d, provenance)).fetchone()[0]
        regime = state or (("RISK_ON", "NEUTRAL", "RISK_OFF")[(idx // 3 + idx) % 3] if idx % 19 else "UNAVAILABLE")
        unavailable = regime == "UNAVAILABLE"
        comps = {"c3_breadth_sma50": {"present": not unavailable, "value": 40.0 + idx % 30},
                 "c4_breadth_sma200": {"present": not unavailable, "value": 35.0 + idx % 25},
                 "c5_net_highs_lows": {"present": (idx % 7 != 0) and not unavailable, "value": -5.0 + idx % 11}}
        score = None if unavailable else round(((idx % 20) - 10) / 10, 4)
        mid = self.x("INSERT INTO market_snapshot (session_date, provenance, feature_set_version, regime_model_version, rs_model_version, "
                     "regime_state, regime_score, regime_strength, regime_present_weight, regime_components, coverage, source, "
                     "universe_snapshot_id, content_hash, code_ref, reconstruction_basis, captured_at) VALUES (%s, %s, 'mi_v2', 'risk_regime_v1', "
                     "'rs_v1', %s, %s, %s, %s, %s::jsonb, '{}'::jsonb, 'test', %s, %s, 'test', %s, %s) RETURNING id",
                     (d, provenance, regime, score, None if unavailable else round(abs(score or 0) * 0.8, 4), 0 if unavailable else 1,
                      json.dumps(comps), uid, sha("m", idx, provenance), basis, captured_at or at(idx))).fetchone()[0]
        for si, sector in enumerate(("Tech", "Energy")):
            self.x("INSERT INTO sector_snapshot (session_date, provenance, feature_set_version, sector, n_members, n_valid_5, n_valid_20, "
                   "n_valid_60, n_excluded_5, n_excluded_20, n_excluded_60, sec_ret_5, sec_ret_20, sec_ret_60, sec_vs_spx_5, sec_vs_spx_20, "
                   "sec_vs_spx_60, sec_vs_univ_5, sec_vs_univ_20, sec_vs_univ_60, rank_20, market_snapshot_id, reconstruction_basis, captured_at) "
                   "VALUES (%(d)s, %(p)s, 'mi_v2', %(sector)s, 10, 10, 10, 10, 0, 0, 0, 1.0, %(r20)s, 3.0, 0.5, %(s20)s, 1.0, 0.4, %(u20)s, "
                   "1.0, %(rk)s, %(mid)s, %(basis)s, %(ca)s)",
                   dict(d=d, p=provenance, sector=sector, r20=(idx + si) % 7 - 3.0, s20=(idx + si) % 5 - 2.0, u20=(idx + si) % 3 - 1.0,
                        rk=si + 1, mid=mid, basis=basis, ca=captured_at or at(idx)))

    def stock_rs(self, symbol, idx, *, provenance="observed", created_at=None, run_hash=None):
        k = UNIVERSE.index(symbol)
        rec = provenance == "reconstructed"
        sector = SECTOR_OF.get(symbol)
        self.x("INSERT INTO stock_relative_strength (session_date, symbol, horizon_sessions, model_version, feature_set_version, provenance, "
               "reconstruction_basis, sector, sector_pit_safe, state, ret_pct, vs_spx_pp, vs_sector_pp, rs_percentile, n_universe_valid, "
               "benchmark_symbol, run_content_hash, code_ref, created_at) VALUES (%s, %s, %s, 'rs_v1', 'mi_v2', %s, %s, %s, %s, 'ok', %s, %s, "
               "%s, %s, 3, '^GSPC', %s, 'test', %s)",
               (CAL[idx], symbol, PRIMARY, provenance, "reconstructed (test)" if rec else None, sector, (not rec) and sector is not None,
                (idx + k) % 13 - 6.0, (idx + k) % 9 - 4.0, None if sector is None else (idx + k) % 7 - 3.0, float((idx * 13 + k * 29) % 101),
                run_hash or sha("rs", idx),
                created_at or at(idx, 21, 30)))

    # -- events / classifications / first-seen ----------------------------------------------------------------------
    def event(self, symbol, idx, *, grade="A", ingested_at=None, classify=True, classified_at=None, label="earnings_beat"):
        key = f"{symbol}|earn|{CAL[idx].isoformat()}"
        self.x("INSERT INTO market_event (event_key, symbol, event_type) VALUES (%s, %s, 'earnings_reported') ON CONFLICT DO NOTHING", (key, symbol))
        ing = ingested_at or at(idx, 12)
        fact = sha("fact", key)
        basis = {"A": "vendor_published", "B": "ingested", "C": "vendor_published", "X": "unknown"}[grade]
        known = None if grade == "X" else ing
        published = known if basis == "vendor_published" else None
        rid = self.x("INSERT INTO market_event_revision (event_key, revision, event_time, known_at, published_at, known_at_basis, ingested_at, "
                     "source, source_ref, pit_grade, status, payload, payload_hash, provenance) VALUES (%s, 1, %s, %s, %s, %s, %s, 'test', %s, "
                     "%s, 'reported', %s::jsonb, %s, 'observed') RETURNING id",
                     (key, CAL[idx], known, published, basis, ing, key, grade, json.dumps({"fact_hash": fact}), sha("p", key))).fetchone()[0]
        if classify:
            self.x("INSERT INTO catalyst_classification (revision_id, event_key, fact_hash, classifier, classifier_version, method, "
                   "classifier_identity, label, evidence, classified_at, code_ref) VALUES (%s, %s, %s, 'items_rule', 'v1', 'rule', "
                   "'{\"rule_id\":\"r1\"}'::jsonb, %s, '{}'::jsonb, %s, 'test')",
                   (rid, key, fact, label, classified_at or at(idx, 18)))
        return rid

    def first_seen(self, symbol, observed_idx, values, *, dataset="earnings_date"):
        series = f"vendor_x|{dataset}|symbol:{symbol}|Q1"
        prev = None
        for seq, (idx, date_value) in enumerate(zip(observed_idx, values), start=1):
            value = {"date": date_value}
            vh = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            self.x("INSERT INTO source_observation (series_key, source, dataset, subject_type, subject_id, period_key, seq, prev_value_hash, "
                   "value, value_hash, observed_at, run_key, code_ref) VALUES (%s, 'vendor_x', %s, 'symbol', %s, 'Q1', %s, %s, %s::jsonb, %s, "
                   "%s, 'run1', 'test')", (series, dataset, symbol, seq, prev, json.dumps(value), vh, at(idx, 9)))
            prev = vh


def seed_world(conn):
    cur = conn.cursor()
    triggers = _stamp_triggers(cur)
    assert {t for t, _ in triggers} >= {"market_event_revision_stamp", "catalyst_classification_consistency"}, triggers
    for trg, rel in triggers:
        cur.execute(f'ALTER TABLE "{rel}" DISABLE TRIGGER "{trg}"')
    w = World(conn)
    try:
        for idx in ALL_IDX:
            for s in UNIVERSE:
                w.candidate(s, idx)
        for idx in TRAIN_IDX + VAL_IDX + TEST_IDX:
            if idx % 31 == 0:
                w.market(idx, provenance="reconstructed")                  # only a reconstructed snapshot exists
                continue
            if idx % 23 == 0:
                continue                                                    # market context absent for this session
            late = at(idx) + timedelta(days=5) if idx % 17 == 0 else None  # arrived after the decision deadline
            w.market(idx, captured_at=late)
            if idx % 11 == 0:
                w.market(idx, provenance="reconstructed")                  # alongside an observed one: never preferred
        for idx in TRAIN_IDX + VAL_IDX + TEST_IDX:
            for s in UNIVERSE:
                if (idx + UNIVERSE.index(s)) % 6 == 0:
                    continue                                                # relative strength absent
                w.stock_rs(s, idx, created_at=at(idx, 21, 30) + (timedelta(days=4) if idx % 13 == 0 else timedelta(0)))
        for s_i, s in enumerate(UNIVERSE):
            for e_i, idx in enumerate(range(8, 500, 37)):
                grade = "X" if (idx + s_i) % 5 == 0 else "A"
                late_class = (idx + s_i) % 7 == 0
                w.event(s, idx, grade=grade, classify=(e_i + s_i) % 3 != 0,
                        classified_at=at(idx, 18) + (timedelta(days=20) if late_class else timedelta(0)))
        w.first_seen("A", (5, 60, 300), ("2023-02-01", "2023-02-15", "2024-02-01"))
        w.first_seen("B", (40,), ("2023-03-01",))
    finally:
        for trg, rel in triggers:
            cur.execute(f'ALTER TABLE "{rel}" ENABLE ALWAYS TRIGGER "{trg}"')
    conn.commit()
    return w


# ------------------------------------------------------------------ fixtures (imported explicitly by the test modules that use them)
import pytest  # noqa: E402
from research.lab import registry_store as RS  # noqa: E402


class Env:
    def __init__(self, connect, conn, world, manifest, schema=None):
        self.connect, self.conn, self.world, self.manifest, self.schema = connect, conn, world, manifest, schema
        self.manifest_hash = manifest.manifest_hash
        self.code = R.CodeIdentity(SHA, True)

    def build(self, **kw):
        kw.setdefault("code", self.code)
        return R.build_dataset(self.conn, self.manifest_hash, CAL, **kw)

    def parts(self):
        """(manifest, config, raw rows) exactly as the builder reads them (read-only)."""
        with RD.read_only_session(self.conn):
            cur = self.conn.cursor()
            m = R.load_manifest(cur, self.manifest_hash, CAL)
            cfg = C.config_for(m.document)
            return m, cfg, RD.read_raw(cur, m, cfg)

    def register(self, manifest=None):
        manifest = manifest or self.manifest
        cur = self.conn.cursor()
        RS.insert_manifest(cur, manifest)
        self.conn.commit()
        return manifest.manifest_hash


def _make_env():
    gen = _schema_env(MIGRATIONS)
    schema, connect = next(gen)
    ctx = connect()
    conn = ctx.__enter__()
    try:
        world = seed_world(conn)
        manifest = author_manifest(conn)
        RS.insert_manifest(conn.cursor(), manifest)
        conn.commit()
        yield Env(connect, conn, world, manifest, schema)
    finally:
        try:
            conn.rollback()
        finally:
            ctx.__exit__(None, None, None)
            for _ in gen:
                pass


@pytest.fixture(scope="module")
def denv():
    """A seeded world + its registered manifest, shared by read-only tests in a module."""
    yield from _make_env()


@pytest.fixture
def fresh_denv():
    """The same, but private to one test (use when the test mutates the database)."""
    yield from _make_env()
