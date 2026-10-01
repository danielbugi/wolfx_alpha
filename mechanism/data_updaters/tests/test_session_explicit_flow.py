"""Session-explicit pipeline (2026-10-01): the updater decides FETCH/SKIP from the explicit target session,
never from `now() - latest_bar <= 1 day`. No network, no database.

The production bug these reproduce: at the 20:45 / 22:00 UTC pipeline runs the newest DB bar was
YESTERDAY's, so the old wall-clock gap was 1 day, every symbol was skipped as "current", the new session was
never fetched, the freshness gate failed, and screener/ML/ledger never ran (only after UTC midnight, gap 2,
did a fetch happen -- via the post-market retry, which does not run the screener).

Run:  python -m pytest mechanism/data_updaters/tests -q      (from the repo root)
"""
import os
import sys
import types
from datetime import date, datetime, timezone

import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from data_updaters import daily_data_updater as dd  # noqa: E402
from data_updaters import check_price_freshness as cpf  # noqa: E402
from screeners import signal_ledger_writer as slw  # noqa: E402
from shared import market_calendar as mc  # noqa: E402
from shared.session_integrity import partition_by_session  # noqa: E402

NY = mc.NY
MON, TUE, WED, FRI = date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 9, 25)


def utc(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def cal(*days):
    return {d: mc._close_dt(d, "16:00") for d in days}


# =================================================================== plan_symbol_fetch (pure)
def test_normal_weekday_latest_monday_target_tuesday_fetches():
    plan = dd.plan_symbol_fetch(MON, TUE)
    assert plan.action == dd.FETCH and plan.gap_days == 1 and plan.period == "5d"


def test_already_current_skips():
    assert dd.plan_symbol_fetch(TUE, TUE).action == dd.CURRENT


def test_monday_target_friday_latest_fetches():
    plan = dd.plan_symbol_fetch(FRI, MON)
    assert plan.action == dd.FETCH and plan.gap_days == 3


def test_monday_retry_monday_bar_present_skips():
    assert dd.plan_symbol_fetch(MON, MON).action == dd.CURRENT


def test_latest_after_target_is_reported_not_treated_as_current():
    plan = dd.plan_symbol_fetch(WED, TUE)
    assert plan.action == dd.AHEAD and plan.gap_days == 1


def test_new_symbol_gets_a_year():
    plan = dd.plan_symbol_fetch(None, TUE)
    assert plan.action == dd.NEW_SYMBOL and plan.period == "1y"


@pytest.mark.parametrize("gap,period", [(1, "5d"), (5, "5d"), (6, "1mo"), (30, "1mo"), (31, "3mo"), (91, "1y")])
def test_lookback_buckets(gap, period):
    target = date(2026, 9, 29)
    latest = date.fromordinal(target.toordinal() - gap)
    assert dd.plan_symbol_fetch(latest, target).period == period


def test_unknown_target_never_skips_a_symbol_as_current():
    today = datetime.now().date()
    assert dd.plan_symbol_fetch(today, None).action == dd.FETCH


# =================================================================== get_efficient_stock_data (updater, fake DB + vendor)
class FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 29, 20, 45, tzinfo=timezone.utc)


def vendor_frame(*days):
    return pd.DataFrame({"date": list(days), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
                         "adj_close": 1.5, "volume": 1000})


@pytest.fixture
def updater_factory(monkeypatch):
    """-> make(latest_by_symbol, vendor_days_by_symbol, target) returning (updater, vendor_calls)."""
    vendor_calls = []

    def make(latest, vendor_days, target):
        def fake_get_daily_bars(symbol, period=None):
            vendor_calls.append((symbol, period))
            return vendor_frame(*vendor_days.get(symbol, []))

        mod = types.ModuleType("shared.tiingo_client")
        mod.get_daily_bars = fake_get_daily_bars
        monkeypatch.setitem(sys.modules, "shared.tiingo_client", mod)
        monkeypatch.setattr(dd.config, "data_provider", "tiingo", raising=False)
        u = dd.EnhancedDailyDataUpdater(target_session=target)
        monkeypatch.setattr(u, "get_latest_date_for_symbol", lambda s: latest.get(s))
        return u, vendor_calls

    return make


def test_bug_repro_wall_clock_tuesday_2045_utc_latest_monday_target_tuesday_fetches(updater_factory, monkeypatch):
    monkeypatch.setattr(dd, "datetime", FrozenClock)   # wall clock: Tue 2026-09-29 20:45 UTC, the pipeline's first run
    u, calls = updater_factory({"AAA": MON}, {"AAA": [MON, TUE]}, TUE)
    out = u.get_efficient_stock_data("AAA", TUE)
    assert calls, "the old `now().date() - latest <= 1` shortcut skipped this symbol without calling the vendor"
    assert list(out["date"]) == [TUE]


def test_already_current_does_not_call_the_vendor(updater_factory):
    u, calls = updater_factory({"AAA": TUE}, {"AAA": [TUE]}, TUE)
    assert u.get_efficient_stock_data("AAA", TUE) is None
    assert calls == [] and u.stats["already_current"] == 1


def test_vendor_without_the_target_bar_returns_nothing_new(updater_factory):
    u, _ = updater_factory({"AAA": MON}, {"AAA": [MON]}, TUE)       # vendor is late: still only Monday
    assert u.get_efficient_stock_data("AAA", TUE) is None
    assert u.stats["vendor_no_new_data"] == 1


def test_bars_after_the_target_session_are_never_ingested(updater_factory):
    u, _ = updater_factory({"AAA": MON}, {"AAA": [MON, TUE, WED]}, TUE)
    assert list(u.get_efficient_stock_data("AAA", TUE)["date"]) == [TUE]


def test_ahead_of_target_is_counted_and_untouched(updater_factory):
    u, calls = updater_factory({"AAA": WED}, {"AAA": [WED]}, TUE)
    assert u.get_efficient_stock_data("AAA", TUE) is None
    assert calls == [] and u.stats["ahead_of_target"] == 1


def test_monday_target_after_a_weekend_fetches(updater_factory):
    u, calls = updater_factory({"AAA": FRI}, {"AAA": [FRI, MON]}, MON)
    assert list(u.get_efficient_stock_data("AAA", MON)["date"]) == [MON]
    assert calls == [("AAA", "5d")]


# =================================================================== the whole retry story (real components, in-memory DB)
class MemDB:
    """stock_prices + signal_ledger in memory; the ledger emulates the real ON CONFLICT (symbol, strategy_id,
    direction, signal_date) upsert (the DB-backed test_signal_ledger_writer.py covers the real constraint)."""

    def __init__(self):
        self.prices = {}                 # symbol -> set(dates)
        self.ledger = {}                 # (symbol, strategy_id, direction, signal_date) -> params
        self.ledger_writes = 0

    def execute_query(self, query, params=None):          # check_price_freshness.check
        session = params[0]
        have = sum(1 for ds in self.prices.values() if session in ds)
        prev = max((d for ds in self.prices.values() for d in ds if d < session), default=None)
        prev_count = sum(1 for ds in self.prices.values() if prev in ds)
        return [(have, prev_count, prev)]

    def execute_insert(self, query, params=None):         # the ledger upsert
        self.ledger_writes += 1
        self.ledger[(params["symbol"], params["strategy_id"], params["direction"], params["signal_date"])] = params
        return True


class Pipeline:
    """The order of automation_pipeline.sh, with the real gate, updater, freshness check, session-integrity
    filter, ledger writer and session mark. (The shell's `set -e` glue itself is not executed here.)"""

    SYMBOLS = [f"S{i:02d}" for i in range(20)]

    def __init__(self, tmp_path, monkeypatch, sessions):
        self.db = MemDB()
        self.sessions = sessions
        self.vendor_days = {}            # symbol -> days the vendor can currently serve
        monkeypatch.setattr(mc, "STATE_PATH", tmp_path / "state.json")
        monkeypatch.setattr(mc, "CACHE_PATH", tmp_path / "cache.json")
        monkeypatch.setenv("MARKET_SETTLE_MINUTES", "30")
        monkeypatch.setattr(cpf, "db", self.db)
        monkeypatch.setattr(slw, "_resolve_default_strategy", lambda db: (1, "v1"))
        monkeypatch.setattr(slw, "_has_open_position", lambda *a, **k: False)
        mod = types.ModuleType("shared.tiingo_client")
        mod.get_daily_bars = lambda symbol, period=None: vendor_frame(*self.vendor_days.get(symbol, []))
        monkeypatch.setitem(sys.modules, "shared.tiingo_client", mod)
        monkeypatch.setattr(dd.config, "data_provider", "tiingo", raising=False)
        self.monkeypatch = monkeypatch

    def seed(self, day, symbols=None):
        for s in symbols or self.SYMBOLS:
            self.db.prices.setdefault(s, set()).add(day)

    def attempt(self, now):
        decision = mc.check_new_session("pipeline", now=now, sessions=self.sessions)
        if not decision.run:
            return "gate-skipped"
        session, _ = mc.resolve_session(decision.session, now=now, sessions=self.sessions)

        u = dd.EnhancedDailyDataUpdater(target_session=session)
        self.monkeypatch.setattr(u, "get_latest_date_for_symbol",
                                 lambda s: max(self.db.prices.get(s, {None}), default=None) if self.db.prices.get(s) else None)
        for sym in self.SYMBOLS:
            frame = u.get_efficient_stock_data(sym, session)
            if frame is not None:
                self.db.prices.setdefault(sym, set()).update(frame["date"])

        ok, _ = cpf.check(session, 0.90)
        if not ok:
            return "freshness-failed"        # shell: handle_error -> exit 1 -> session NOT marked

        signals = []
        for sym in self.SYMBOLS:             # the screener's rn=1 (newest bar <= target) candidate per symbol
            newest = max(d for d in self.db.prices[sym] if d <= session)
            signals.append({"symbol": sym, "signal_type": "bullish_breakout", "screening_date": newest,
                            "current_price": 100.0, "atr_14": 2.0})
        accepted, _rejected = partition_by_session(signals, session, "screening_date")
        slw.write_todays_signals(self.db, accepted, session)
        mc.mark_processed("pipeline", session)
        return "completed"


def test_retry_idempotency_attempt1_fails_attempt2_completes_attempt3_is_a_noop(tmp_path, monkeypatch):
    sessions = cal(MON, TUE)
    p = Pipeline(tmp_path, monkeypatch, sessions)
    p.seed(MON)
    mc.mark_processed("pipeline", MON)                      # Monday fully processed earlier

    # attempt 1 -- 20:45 UTC Tuesday: the vendor has not published Tuesday yet (it only serves Monday)
    p.vendor_days = {s: [MON] for s in p.SYMBOLS}
    assert p.attempt(utc(2026, 9, 29, 20, 45)) == "freshness-failed"
    assert mc.last_processed("pipeline") == MON            # Tuesday NOT marked
    assert p.db.ledger == {} and p.db.ledger_writes == 0   # nothing written for Tuesday

    # attempt 2 -- 22:00 UTC: Tuesday is available. (The old code skipped every symbol here: wall-clock gap 1.)
    p.vendor_days = {s: [MON, TUE] for s in p.SYMBOLS}
    assert p.attempt(utc(2026, 9, 29, 22, 0)) == "completed"
    assert mc.last_processed("pipeline") == TUE
    assert len(p.db.ledger) == len(p.SYMBOLS)
    assert {k[3] for k in p.db.ledger} == {TUE}
    writes_after_success = p.db.ledger_writes

    # attempt 3 -- the next trigger: Tuesday is already complete -> NO-OP, no duplicate ledger events
    assert p.attempt(utc(2026, 9, 30, 0, 0)) == "gate-skipped"
    assert p.db.ledger_writes == writes_after_success and len(p.db.ledger) == len(p.SYMBOLS)


def test_first_attempt_succeeds_at_2045_utc_when_the_vendor_has_the_bar(tmp_path, monkeypatch):
    p = Pipeline(tmp_path, monkeypatch, cal(MON, TUE))
    p.seed(MON)
    mc.mark_processed("pipeline", MON)
    p.vendor_days = {s: [MON, TUE] for s in p.SYMBOLS}
    assert p.attempt(utc(2026, 9, 29, 20, 45)) == "completed"
    assert mc.last_processed("pipeline") == TUE


def test_stale_individual_symbol_never_becomes_a_tuesday_signal(tmp_path, monkeypatch):
    p = Pipeline(tmp_path, monkeypatch, cal(MON, TUE))
    p.seed(MON)
    mc.mark_processed("pipeline", MON)
    stale = p.SYMBOLS[0]
    p.vendor_days = {s: [MON, TUE] for s in p.SYMBOLS}
    p.vendor_days[stale] = [MON]                            # this one symbol has no Tuesday bar
    assert p.attempt(utc(2026, 9, 29, 22, 0)) == "completed"   # 19/20 = 95% coverage passes the gate
    assert all(k[0] != stale for k in p.db.ledger), "a Monday bar was relabelled as a Tuesday signal"
    assert len(p.db.ledger) == len(p.SYMBOLS) - 1
    assert {k[3] for k in p.db.ledger} == {TUE}


def test_never_creates_a_next_day_target_with_a_previous_day_bar(tmp_path, monkeypatch):
    """The 2026-10-01 shape: target Oct 1, newest bar Sep 30 -> signal_date Oct 1 must be impossible."""
    oct1, sep30 = date(2026, 10, 1), date(2026, 9, 30)
    signals = [{"symbol": "AAA", "signal_type": "bullish_breakout", "screening_date": sep30,
                "current_price": 100.0, "atr_14": 2.0}]
    db = MemDB()
    monkeypatch.setattr(slw, "_resolve_default_strategy", lambda db: (1, "v1"))
    monkeypatch.setattr(slw, "_has_open_position", lambda *a, **k: False)
    assert slw.write_todays_signals(db, signals, oct1) == 0
    assert db.ledger == {}


def test_weekend_and_holiday_invent_no_session(tmp_path, monkeypatch):
    p = Pipeline(tmp_path, monkeypatch, cal(FRI, TUE))      # Mon 09-28 treated as a holiday here
    mc.mark_processed("pipeline", FRI)
    for now in (utc(2026, 9, 26, 15, 0), utc(2026, 9, 27, 15, 0), utc(2026, 9, 28, 22, 0)):  # Sat, Sun, holiday Monday
        assert p.attempt(now) == "gate-skipped"
    assert p.db.ledger_writes == 0


# =================================================================== wiring (the shell glue is not executed by the tests above)
def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_pipeline_script_passes_the_gate_session_to_updater_and_screener():
    sh = _read("automation_pipeline.sh")
    assert 'SESSION_ARGS=(--session "$SESSION_DATE")' in sh
    assert 'daily_data_updater.py "${SESSION_ARGS[@]}"' in sh
    assert 'multi_timeframe_screener.py "${SESSION_ARGS[@]}"' in sh


def test_postmarket_retry_tells_the_updater_its_session_and_never_runs_screener_ml_ledger():
    src = _read("mechanism/alerts/publish_post_market.py")
    assert '"--session", target.isoformat()' in src
    for forbidden in ("multi_timeframe_screener", "signal_ledger", "ml_signal_enhancer"):
        assert forbidden not in src
