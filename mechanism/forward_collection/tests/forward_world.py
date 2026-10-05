"""A disposable forward-collection world (explicit imports only; never a conftest name).

One throwaway Postgres SCHEMA holding every table the collector stack touches (the real base schema plus migrations 19-30), a deterministic
synthetic market, and a SIMULATED CLOCK. The collector stack is exercised through its real writers -- the candidate capture hook, the Market
Intelligence writer and the fwd_v1 label writer -- never by inserting ideal rows.

The simulated clock exists only inside the throwaway schema: `lab_sim_now()` returns the GUC `lab.sim_now` when set, else the real clock, and the
database-stamped availability columns (`captured_at`, `computed_at`, `run_started_at`, `set_at`, and the relative-strength stamp trigger) are pointed
at it. That is test scaffolding for a disposable schema -- the production defaults (`NOW()` / `clock_timestamp()`) are untouched. Every connection
the stack opens reads the world's CURRENT simulated instant, so a session's capture is stamped at its evening and its collection at the next
morning, exactly as the scheduler design says.
"""
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
for p in (os.path.join(ROOT, "mechanism"), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

MIGRATIONS = [
    "create_trading_schema.sql", "add_market_data_tables.sql", "add_ml_dataset_tables.sql", "add_signal_ledger_tables.sql",
    "add_strategy_identity_release_a.sql", "add_signal_ledger_eval_flags.sql", "add_research_observation_tables.sql",
    "add_market_snapshot_tables.sql", "add_market_event_tables.sql", "add_forward_return_label_table.sql", "add_source_observation_tables.sql",
    "add_catalyst_classification_table.sql", "add_stock_relative_strength_table.sql", "add_dataset_experiment_registry_tables.sql",
]
SIM_CLOCK_SQL = """
CREATE OR REPLACE FUNCTION lab_sim_now() RETURNS timestamptz LANGUAGE sql VOLATILE AS
$$ SELECT COALESCE(NULLIF(current_setting('lab.sim_now', true), '')::timestamptz, clock_timestamp()) $$;
CREATE OR REPLACE FUNCTION research_rs_stamp() RETURNS trigger LANGUAGE plpgsql AS
$$ BEGIN NEW.created_at := lab_sim_now(); RETURN NEW; END; $$;
ALTER TABLE universe_snapshot ALTER COLUMN captured_at SET DEFAULT lab_sim_now();
ALTER TABLE market_snapshot ALTER COLUMN captured_at SET DEFAULT lab_sim_now();
ALTER TABLE sector_snapshot ALTER COLUMN captured_at SET DEFAULT lab_sim_now();
ALTER TABLE feature_snapshot ALTER COLUMN captured_at SET DEFAULT lab_sim_now();
ALTER TABLE candidate_observation ALTER COLUMN captured_at SET DEFAULT lab_sim_now();
ALTER TABLE candidate_capture_run ALTER COLUMN run_started_at SET DEFAULT lab_sim_now();
ALTER TABLE research_capture_activation ALTER COLUMN set_at SET DEFAULT lab_sim_now();
ALTER TABLE forward_return_label ALTER COLUMN computed_at SET DEFAULT lab_sim_now();
"""

N_STOCKS = 1100
WARMUP = 260
SECTORS = ("Technology", "Energy", "Healthcare", "Financials")
INDEX_SYMBOLS = ("^GSPC", "^VIX", "^RUT")
UTC = timezone.utc
STRATEGY_KEY, STRATEGY_VERSION = "donchian_breakout", "v1"


SMALL_UNIVERSE = 80
SMALL_MIN = 60


def scale_universe_minimums(monkeypatch, minimum=SMALL_MIN):
    """TEST SCALE ONLY: the writers' "at least 1,000 stocks" floors (relative strength, breadth, risk regime) are lowered for a small synthetic
    universe so a ~250-session history can be simulated in seconds instead of the ~25 minutes a 1,100-stock market needs. The real floors stay in
    force everywhere else, and the real-size tests run the writers at 1,100 stocks."""
    from market_intelligence import breadth, relative_strength, risk_regime
    monkeypatch.setattr(relative_strength, "MIN_UNIVERSE", minimum)
    monkeypatch.setattr(breadth, "MIN_ELIGIBLE_MARKET", minimum)
    monkeypatch.setattr(risk_regime, "MIN_BREADTH_STOCKS", minimum)


def connect_args():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    return dict(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"],
                user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])


def sector_of(i, no_sector=()):
    return None if f"S{i:04d}" in no_sector else SECTORS[i % len(SECTORS)]


class ForwardWorld:
    """`connect()` yields a connection whose search_path is the throwaway schema and whose simulated instant is `self.now`."""

    def __init__(self, schema, args, admin, *, n_days, n_stocks=N_STOCKS, seed=11, no_sector=(), fundamentals_every=5):
        import psycopg2
        self.schema, self.args, self.admin = schema, args, admin
        self._psycopg2 = psycopg2
        self.no_sector = set(no_sector)
        self.fundamentals_every = fundamentals_every      # the fundamentals updater's cadence in sessions (None = it never runs again)
        self.now = None
        rng = np.random.default_rng(seed)
        total = WARMUP + n_days
        self.days = [d.date() for d in pd.bdate_range(end=pd.Timestamp(date(2026, 6, 30)), periods=total)]
        self.syms = [f"S{i:04d}" for i in range(n_stocks)]
        steps = rng.normal(0.0004, 0.002, n_stocks) + rng.normal(0.0, 0.01, (total, n_stocks))
        self.close = 100.0 * np.exp(np.cumsum(steps, axis=0))
        self.index = {"^GSPC": 4000 * np.exp(np.cumsum(rng.normal(0.0004, 0.006, total))), "^VIX": 15 + 3 * np.abs(rng.normal(0, 1, total)),
                      "^RUT": 2000 * np.exp(np.cumsum(rng.normal(0.0004, 0.008, total)))}
        self.sessions = self.days[WARMUP:]          # the simulated sessions (day 0 is the first forward session)
        self.loaded = 0

    # -- connections ---------------------------------------------------------------------------------------------
    @contextmanager
    def connect(self):
        opts = f"-c search_path={self.schema}"
        if self.now is not None:
            opts += f" -c lab.sim_now={self.now.isoformat()}"
        conn = self._psycopg2.connect(options=opts, **self.args)
        try:
            yield conn
        finally:
            conn.close()

    def at(self, session, hour, minute=0, *, days_after=0):
        d = session + timedelta(days=days_after)
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=UTC)

    def clock(self):
        return self.now

    # -- data ----------------------------------------------------------------------------------------------------
    def _bars(self, i):
        d = self.days[i]
        rows = []
        for j, s in enumerate(self.syms):
            c = round(float(self.close[i, j]), 4)
            rows.append((s, d, c, round(c * 1.01, 4), round(c * 0.99, 4), c, 1000))
        return rows

    def _index_rows(self, i):
        return [(k, self.days[i], round(float(v[i]), 4)) for k, v in self.index.items()]

    def seed_history(self):
        from psycopg2.extras import execute_values
        with self.connect() as conn:
            cur = conn.cursor()
            rows = [r for i in range(WARMUP) for r in self._bars(i)]
            execute_values(cur, "INSERT INTO stock_prices (symbol, date, open, high, low, close, volume) VALUES %s", rows, page_size=20000)
            execute_values(cur, "INSERT INTO market_index_prices (symbol, date, close) VALUES %s",
                           [r for i in range(WARMUP) for r in self._index_rows(i)])
            first = self.days[0] - timedelta(days=30)
            stamp = datetime(first.year, first.month, first.day, 3)
            execute_values(cur, "INSERT INTO daily_fundamentals (symbol, date, sector, created_at, updated_at) VALUES %s",
                           [(s, first, sector_of(i, self.no_sector), stamp, stamp) for i, s in enumerate(self.syms)])
            conn.commit()
        self.loaded = WARMUP

    def load_session(self, k):
        """Append the bars of simulated session k (0-based), exactly as the daily price updater would."""
        from psycopg2.extras import execute_values
        i = WARMUP + k
        assert i == self.loaded, "sessions must be loaded in order"
        with self.connect() as conn:
            cur = conn.cursor()
            execute_values(cur, "INSERT INTO stock_prices (symbol, date, open, high, low, close, volume) VALUES %s", self._bars(i), page_size=20000)
            execute_values(cur, "INSERT INTO market_index_prices (symbol, date, close) VALUES %s", self._index_rows(i))
            if self.fundamentals_every and k % self.fundamentals_every == 0:
                d = self.days[i]
                stamp = datetime(d.year, d.month, d.day, 19, 0)         # the updater's row is written on the session's own evening
                execute_values(cur, "INSERT INTO daily_fundamentals (symbol, date, sector, created_at, updated_at) VALUES %s",
                               [(sym, d, sector_of(j, self.no_sector), stamp, stamp) for j, sym in enumerate(self.syms)])
            conn.commit()
        self.loaded += 1

    def strategy_ref(self):
        from screeners.signal_ledger_writer import StrategyRef
        from research import repository
        with self.connect() as conn:
            sid, _ = repository.resolve_strategy(conn, STRATEGY_KEY, STRATEGY_VERSION)
        return StrategyRef(sid, STRATEGY_KEY, STRATEGY_VERSION)

    def activate_capture(self, strategy_id, effective_from):
        with self.connect() as conn:
            conn.cursor().execute("SELECT research_capture_set_state(%s, 'enabled', %s, 'forward-collection convergence test')",
                                  (strategy_id, effective_from))
            conn.commit()

    # -- candidates (through the real capture hook) ---------------------------------------------------------------
    def candidates_for(self, k, n):
        """`n` deterministic breakout candidates for simulated session k, built from that session's stored bars."""
        i = WARMUP + k
        d = self.days[i]
        out = []
        for m in range(n):
            j = (k * 7 + m * 53) % len(self.syms)
            close = float(round(float(self.close[i, j]), 4))
            out.append({"symbol": self.syms[j], "signal_type": "bullish_breakout", "screening_date": d, "current_price": close,
                        "prev_donchian_high": close * 0.97, "prev_donchian_low": close * 0.80, "donchian_high": close,
                        "donchian_low": close * 0.8, "distance_to_breakout": 1.5, "atr_14": 2.0, "urgency": "immediate",
                        "screener_defaults": [], "alignment_score": 70.0, "alignment_grade": "B",
                        "weekly_context": {"weekly_trend": "bullish", "week_ending_date": d}, "monthly_context": None})
        return out

    def capture(self, k, n, strategy):
        from research import observer
        cands = self.candidates_for(k, n)
        finals = [dict(c, combined_score=66.0, ml_prediction_available=True, ml_momentum_probability=0.61, ml_confidence="medium",
                       ml_model_version="m_test") for c in cands]
        res = observer.capture_session(candidates=cands, final_signals=finals, guard_decisions={c["symbol"]: [] for c in cands},
                                       guards_evaluated=True, session_date=self.days[WARMUP + k], strategy=strategy, connect=self.connect)
        return res

    def settle_capture_run(self, session, finished):
        """The capture hook stamps run_finished_at with the REAL clock; give the run the simulated finish time of its evening."""
        with self.connect() as conn:
            conn.cursor().execute("UPDATE candidate_capture_run SET run_finished_at = %s WHERE session_date = %s AND run_finished_at IS NOT NULL",
                                  (finished, session))
            conn.commit()

    def count(self, table, where="TRUE", params=()):
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT count(*) FROM {table} WHERE {where}", params)
            n = cur.fetchone()[0]
            conn.rollback()
        return n


def make_world(*, n_days, n_stocks=N_STOCKS, seed=11, no_sector=(), fundamentals_every=5):
    """Generator for a fixture: yields a seeded ForwardWorld, drops the schema afterwards. Skips only when Postgres is unreachable (CI treats a
    skip as a failure)."""
    import psycopg2
    try:
        args = connect_args()
        admin = psycopg2.connect(**args)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable: {type(e).__name__}")
    schema = "fc_w_" + uuid.uuid4().hex[:10]
    try:
        cur = admin.cursor()
        cur.execute(f'CREATE SCHEMA "{schema}"')
        cur.execute(f'SET search_path TO "{schema}"')
        for name in MIGRATIONS:
            with open(os.path.join(ROOT, "mechanism", name), encoding="utf-8") as fh:
                cur.execute(fh.read())
        cur.execute(SIM_CLOCK_SQL)
        admin.commit()
    except Exception:
        admin.rollback()
        admin.close()
        raise
    world = ForwardWorld(schema, args, admin, n_days=n_days, n_stocks=n_stocks, seed=seed, no_sector=no_sector, fundamentals_every=fundamentals_every)
    try:
        world.seed_history()
        yield world
    finally:
        try:
            admin.rollback()
            admin.cursor().execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            admin.commit()
        finally:
            admin.close()


# ------------------------------------------------------------------ the Slice 6 status, through the real owner CLI
def status_spec_doc(world, *, train, validation, test, maturity, cutoff, min_sample=10, grace_days=1, config_over=None, label_horizons=(5, 20, 60)):
    """A lab_authoring_spec_v1 document for the simulated sessions: windows are (first, last) SIMULATED-SESSION INDEXES."""
    from research.lab import dataset_contract as DC
    cfg = {"schema": DC.CONFIG_SCHEMA, "strategy": {"key": STRATEGY_KEY, "version": STRATEGY_VERSION}, "universe_rule": "rule",
           "availability_grace_days": grace_days, "min_sample": min_sample, "primary_horizon": 20, "reconstructed_policy": "exclude",
           "market": {"feature_set_version": "mi_v2"}, "sector": {"feature_set_version": "mi_v2"},
           "stock_rs": {"model_version": "rs_v1", "feature_set_version": "mi_v2", "horizon_sessions": 20}}
    cfg.update(config_over or {})
    s = world.sessions
    win = lambda w: [s[w[0]].isoformat(), s[w[1]].isoformat()]  # noqa: E731
    return {"schema": "lab_authoring_spec_v1", "dataset_name": "forward_ds", "dataset_version": "v1", "label_version": "fwd_v1",
            "label_methodology_version": "fwd_v1.m1", "label_horizons": list(label_horizons), "feature_versions": {"mi_v2": "1"}, "universe_id": "u",
            "knowledge_cutoff_at": cutoff.isoformat(), "label_maturity_session": s[maturity].isoformat(),
            "windows": {"train": win(train), "validation": win(validation), "test": win(test)}, "embargo_sessions": 60, "purge_sessions": 0,
            "calendar": {"file": "calendar.txt"}, "universe": {"from_db": True}, "config": cfg}


def write_status_spec(world, dirpath, doc, *, last_session_index):
    import json
    from pathlib import Path
    dirpath = Path(dirpath)
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "calendar.txt").write_text("# the simulated trading sessions\n" + "\n".join(d.isoformat() for d in world.sessions[:last_session_index + 1]) + "\n",
                                          encoding="utf-8")
    (dirpath / "spec.json").write_text(json.dumps(doc), encoding="utf-8")
    return dirpath / "spec.json"


def read_status(world, spec_path, cutoff, out_dir):
    """(exit code, parsed document | None, stderr) of `dataset_cli status --json` against the throwaway schema at an explicit cutoff."""
    import io
    import json
    import psycopg2
    from research.lab import dataset_cli as CLI
    from research.lab import dataset_runner as R

    def connect(a, *, readonly):
        conn = psycopg2.connect(options=f"-c search_path={world.schema}", **world.args)
        if readonly:
            conn.set_session(readonly=True)
        return conn

    os.environ["LAB_FC_TEST_PASSWORD"] = world.args["password"]
    out, err = io.StringIO(), io.StringIO()
    rc = CLI.main(["status", "--spec", str(spec_path), "--cutoff", cutoff.isoformat(), "--out-dir", str(out_dir), "--json",
                   "--host", world.args["host"], "--port", str(world.args["port"]), "--dbname", world.args["dbname"], "--user", world.args["user"],
                   "--password-env", "LAB_FC_TEST_PASSWORD"], connect=connect, code_provider=lambda _root: R.CodeIdentity("a" * 40, True),
                  out=out.write, err=err.write)
    text = out.getvalue()
    return rc, (json.loads(text) if text.strip().startswith("{") else None), err.getvalue()


def run_forward_sessions(world, steps, ks, *, n_candidates, strategy, code_ref="sim", capture=True, grace_days=1, on_report=None):
    """Simulate whole sessions, in order, through the real writers: the evening (bars load + candidate capture) then the next morning's collector
    run. Returns the list of SessionReports (one per session index)."""
    from forward_collection import contract as C
    from forward_collection import orchestrator as O
    reports = []
    for k in ks:
        world.load_session(k)
        if capture:
            world.now = world.at(world.sessions[k], 21, 30)
            world.capture(k, n_candidates, strategy)
            world.settle_capture_run(world.sessions[k], world.at(world.sessions[k], 21, 40))
        world.now = C.fire_instants_utc(world.sessions[k])[0] + timedelta(minutes=1)     # the scheduler design's first fire
        rep = O.run_session(world.connect, world.sessions[k], steps, apply=True, grace_days=grace_days, code_ref=code_ref, clock=world.clock)
        reports.append(rep)
        if on_report is not None:
            on_report(k, rep)
    return reports


def start_world(w, *, with_capture=True):
    """Activate candidate capture in the disposable world (the real set-state function, tests only) and return (strategy, steps)."""
    from forward_collection import steps as S
    strat = w.strategy_ref()
    w.now = w.at(w.sessions[0], 6)
    w.activate_capture(strat.id, w.sessions[0])
    return strat, S.make_steps(feature_set_version="mi_v2", with_capture=with_capture)


def evening(w, k, strat, n_candidates):
    """The screener's evening: bars of session k are loaded and its candidates captured by the real capture hook."""
    w.load_session(k)
    w.now = w.at(w.sessions[k], 21, 30)
    w.capture(k, n_candidates, strat)
    w.settle_capture_run(w.sessions[k], w.at(w.sessions[k], 21, 40))


def sessions_calendar(w):
    """What shared.market_calendar.get_sessions returns: {session date: close instant}."""
    from shared import market_calendar as mc
    return {d: mc._close_dt(d, "16:00") for d in w.sessions}
