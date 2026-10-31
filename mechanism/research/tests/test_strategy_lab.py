"""Strategy lab: event detection, point-in-time safety, the backtest on a planted-edge panel versus a no-edge panel, determinism, the report and the ledger.
Synthetic data only (no network, no production). The ledger tests need a PostgreSQL with migration 39 and are skipped without one."""
import copy
import json
import os
import sys
from collections import Counter
from datetime import date, timedelta

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from research.strategy_lab import engine as E  # noqa: E402
from research.strategy_lab import report as R  # noqa: E402
from research.strategy_lab import specs as SP  # noqa: E402
from research.strategy_lab.data import Bars, Panel, Report  # noqa: E402

SPEC = SP.get_spec("earnings_drift", "v1")


def bdays(start: date, n: int):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def bars_from_close(dates, close, volume=300_000.0):
    c = np.asarray(close, dtype=float)
    o = np.r_[c[0], c[:-1]]                       # open = previous close: no gaps except through the closes themselves
    h = np.maximum(o, c) * 1.004
    l = np.minimum(o, c) * 0.996
    return Bars(list(dates), o, h, l, c, np.full(len(c), volume))


def flat_then(dates, base=50.0, moves=None):
    """A flat-ish price path (tiny alternating noise so ATR > 0) with explicit day-by-day returns from `moves` {index: return}."""
    c = [base]
    for i in range(1, len(dates)):
        r = moves.get(i, 0.002 if i % 2 else -0.002) if moves else (0.002 if i % 2 else -0.002)
        c.append(c[-1] * (1 + r))
    return np.array(c)


DATES = bdays(date(2021, 1, 4), 300)


def test_detect_keeps_only_events_that_meet_every_condition():
    D = 100                                         # report session index; signal bar = D + 1
    rep = DATES[D]
    moves = {D: 0.02, D + 1: 0.02}                  # +4.04% reaction over the two sessions
    bars = bars_from_close(DATES, flat_then(DATES, moves=moves))
    good = Report(rep, 1.0, 1.2, 20.0)
    cases = {
        "qualifies": (good, bars, 1),
        "surprise_too_small": (Report(rep, 1.0, 1.05, 5.0), bars, 0),
        "no_eps": (Report(rep, 1.0, None, None), bars, 0),
        "reaction_disagrees": (Report(rep, 1.0, 0.8, -20.0), bars, 0),
        "reaction_too_small": (good, bars_from_close(DATES, flat_then(DATES, moves={D: 0.005, D + 1: 0.005})), 0),
        "reaction_is_a_split_or_deal": (good, bars_from_close(DATES, flat_then(DATES, moves={D: 0.3, D + 1: 0.1})), 0),
        "illiquid": (good, bars_from_close(DATES, flat_then(DATES, moves=moves), volume=1_000.0), 0),
        "no_session_near_report": (Report(date(2025, 1, 1), 1.0, 1.2, 20.0), bars, 0),
    }
    for name, (r, b, expect) in cases.items():
        counts = Counter()
        sig = [s for s in E.detect(SPEC, "T", b, [r], counts) if s.kind == E.SIGNAL]
        assert len(sig) == expect, (name, dict(counts))
    s = [x for x in E.detect(SPEC, "T", bars, [good], Counter()) if x.kind == E.SIGNAL][0]
    assert s.direction == 1 and s.signal_idx == D + 1 and s.report_date == rep and s.reaction_pct == pytest.approx(4.04, abs=0.05)


def test_a_short_needs_a_miss_and_a_fall():
    D = 100
    bars = bars_from_close(DATES, flat_then(DATES, moves={D: -0.02, D + 1: -0.02}))
    sig = [s for s in E.detect(SPEC, "T", bars, [Report(DATES[D], 1.0, 0.7, -30.0)], Counter()) if s.kind == E.SIGNAL]
    assert len(sig) == 1 and sig[0].direction == -1


def test_a_weekend_report_date_uses_the_next_session():
    D = next(i for i in range(60, 200) if DATES[i].weekday() == 0)       # a Monday
    sat = DATES[D] - timedelta(days=2)
    moves = {D: 0.02, D + 1: 0.02}
    sig = [s for s in E.detect(SPEC, "T", bars_from_close(DATES, flat_then(DATES, moves=moves)), [Report(sat, 1.0, 1.2, 20.0)], Counter()) if s.kind == E.SIGNAL]
    assert len(sig) == 1 and sig[0].signal_idx == D + 1


def test_signals_use_nothing_after_the_signal_bar():
    """Point in time: rewriting every bar after the signal bar must not change which signals exist or their fields."""
    D = 100
    moves = {D: 0.02, D + 1: 0.02}
    rng = np.random.default_rng(3)
    base_close = flat_then(DATES, moves=moves)
    a = bars_from_close(DATES, base_close)
    close2 = base_close.copy()
    close2[D + 2:] = close2[D + 1] * np.exp(np.cumsum(rng.normal(0, 0.2, len(close2) - D - 2)))      # wild, even absurd, future
    b = bars_from_close(DATES, close2)
    rep = [Report(DATES[D], 1.0, 1.2, 20.0)]
    sa = [(s.kind, s.signal_idx, s.direction, round(s.reaction_pct, 9)) for s in E.detect(SPEC, "T", a, rep, Counter())]
    sb = [(s.kind, s.signal_idx, s.direction, round(s.reaction_pct, 9)) for s in E.detect(SPEC, "T", b, rep, Counter())]
    assert sa == sb and sa


def test_atr_uses_only_bars_up_to_each_index():
    c = np.linspace(50, 60, 80)
    h, l = c * 1.01, c * 0.99
    a = E.atr14(h, l, c)
    c2, h2, l2 = c.copy(), h.copy(), l.copy()
    c2[50:], h2[50:], l2[50:] = 5.0, 6.0, 4.0
    b = E.atr14(h2, l2, c2)
    assert np.allclose(a[:50], b[:50], equal_nan=True) and np.isnan(a[:14]).all()


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ a panel with a planted edge
def make_panel(drift_per_day: float, n_symbols: int = 160, seed: int = 11) -> Panel:
    rng = np.random.default_rng(seed)
    dates = bdays(date(2021, 1, 4), 1300)
    bars, reports = {}, {}
    for k in range(n_symbols):
        sym = f"S{k:03d}"
        r = rng.normal(0.0, 0.012, len(dates))
        reps = []
        for D in range(70 + (k % 13), len(dates) - 70, 63):
            sign = 1 if rng.random() < 0.5 else -1
            surprise = sign * float(rng.uniform(12, 40))
            r[D] = sign * 0.025
            r[D + 1] = sign * 0.02
            r[D + 2: D + 22] += sign * drift_per_day                       # the planted post-report drift (after the signal bar)
            reps.append(Report(dates[D], 1.0, 1.0 + surprise / 100, surprise))
        close = 50.0 * np.exp(np.cumsum(r))
        bars[sym] = bars_from_close(dates, close)
        reports[sym] = reps
    return Panel(bars, reports, "d" * 64, {"prices_rows": 0, "earnings_rows": sum(len(v) for v in reports.values())})


@pytest.fixture(scope="module")
def planted():
    return E.run_backtest(SPEC, make_panel(0.006), seed=5, n_boot=200)


@pytest.fixture(scope="module")
def null():
    return E.run_backtest(SPEC, make_panel(0.0), seed=5, n_boot=200)


def test_a_planted_drift_is_found_out_of_sample_and_passes_every_check(planted):
    assert planted["verdict"] == "PASS_FOR_FORWARD_SHADOW", planted["decision_checks"]
    t = planted["cells"]["signal|test|hold20|both|20bps"]
    assert t["n"] >= 150 and t["ci"]["ci95"][0] > 0 and t["expectancy_r"] > 0.1


def test_with_no_drift_the_same_strategy_is_not_declared_an_edge(null):
    assert null["verdict"] != "PASS_FOR_FORWARD_SHADOW", null["decision_checks"]
    t = null["cells"]["signal|test|hold20|both|20bps"]
    assert t["ci"]["ci95"][0] <= 0 <= t["ci"]["ci95"][1] or t["expectancy_r"] <= 0


def test_the_random_date_control_shows_no_edge_even_when_the_signal_has_one(planted):
    rnd = planted["cells"]["random_date|test|hold20|both|20bps"]
    assert abs(rnd["expectancy_r"]) < 0.15
    d = planted["controls_diff"]["test|hold20|signal_minus_random"]
    assert d["ci95"][0] > 0


def test_costs_lower_the_result_monotonically(planted):
    c = [planted["cells"][f"signal|test|hold20|both|{b}bps"]["expectancy_r"] for b in (10, 20, 40)]
    assert c[0] > c[1] > c[2]


def test_split_has_no_overlap_and_train_comes_before_test(planted):
    n = planted["n_positions_by_kind_split"]
    assert n["signal|train"] > 0 and n["signal|test"] > 0
    assert planted["by_year"]["2021"]["n"] > 0 and planted["by_year"]["2025"]["n"] > 0


def test_the_run_is_reproducible_and_the_seed_only_moves_the_controls():
    p = make_panel(0.006, n_symbols=60)
    a = E.run_backtest(SPEC, p, seed=5, n_boot=100)
    b = E.run_backtest(SPEC, p, seed=5, n_boot=100)
    assert json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    c = E.run_backtest(SPEC, p, seed=6, n_boot=100)
    assert a["cells"]["signal|test|hold20|both|20bps"]["expectancy_r"] == c["cells"]["signal|test|hold20|both|20bps"]["expectancy_r"]      # signal results do not depend on the seed
    assert a["cells"]["random_date|test|hold20|both|20bps"] != c["cells"]["random_date|test|hold20|both|20bps"]


def test_too_few_out_of_sample_positions_is_insufficient_data_not_a_verdict():
    r = E.run_backtest(SPEC, make_panel(0.006, n_symbols=3), seed=5, n_boot=50)
    assert r["verdict"] == "INSUFFICIENT_DATA"


def test_one_open_position_per_symbol_and_direction():
    D = 100
    dates = bdays(date(2021, 1, 4), 400)
    close = flat_then(dates, moves={D: 0.02, D + 1: 0.02, D + 7: 0.02, D + 8: 0.02})
    two = [Report(dates[D], 1.0, 1.2, 20.0), Report(dates[D + 6], 1.0, 1.2, 20.0)]          # a second signal while the first position may still be open
    panel = Panel({"T": bars_from_close(dates, close)}, {"T": two}, "d" * 64)
    spec = copy.deepcopy(SPEC)
    spec["split"]["train_end_exclusive"] = "2030-01-01"
    spec["split"]["test_start_inclusive"] = "2030-02-01"
    r = E.run_backtest(spec, panel, seed=1, n_boot=20)
    assert r["n_positions_by_kind_split"]["signal|train"] <= 1


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ report + hashes
def test_the_report_is_deterministic_and_states_the_identity_and_the_limits(planted):
    kw = dict(spec_hash=SP.spec_hash(SPEC), data_hash="d" * 64, code_ref="abc123", run_hash=E.run_hash(SPEC, "d" * 64, "abc123", 5), seed=5, data_meta={"prices_rows": 1})
    a, b = R.render(SPEC, planted, **kw), R.render(SPEC, planted, **kw)
    assert a == b and R.report_sha256(a) == R.report_sha256(b)
    for needle in ("Verdict:", SP.spec_hash(SPEC), "Decision rule and checks", "Limits that apply", "not point-in-time", "It is not a claim that the strategy makes money"):
        assert needle in a


def test_a_failed_check_never_renders_as_a_pass(null):
    kw = dict(spec_hash="s" * 64, data_hash="d" * 64, code_ref="abc", run_hash="r" * 64, seed=5, data_meta={})
    text = R.render(SPEC, null, **kw)
    if null["verdict"] != "PASS_FOR_FORWARD_SHADOW":
        assert "PASS FOR FORWARD SHADOW" not in text


def test_spec_identity_is_a_hash_of_the_definition_and_changes_with_it():
    h = SP.spec_hash(SPEC)
    changed = copy.deepcopy(SPEC)
    changed["event"]["min_abs_surprise_pct"] = 15.0
    assert len(h) == 64 and SP.spec_hash(changed) != h and SP.spec_hash(copy.deepcopy(SPEC)) == h
    assert E.run_hash(SPEC, "d" * 64, "x", 1) != E.run_hash(SPEC, "e" * 64, "x", 1) != E.run_hash(SPEC, "d" * 64, "y", 1)


def test_current_candidates_lists_only_fresh_signals():
    D = 130
    dates = bdays(date(2021, 1, 4), D + 2)           # the data ends right after the reaction window (signal bar = last bar)
    close = flat_then(dates, moves={D: 0.02, D + 1: 0.02})
    p = Panel({"T": bars_from_close(dates, close)}, {"T": [Report(dates[D], 1.0, 1.2, 20.0)]}, "d" * 64)
    got = E.current_candidates(SPEC, p, as_of_sessions=2)
    assert [(g["symbol"], g["direction"]) for g in got] == [("T", "long")] and got[0]["signal_date"] == dates[D + 1].isoformat()


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ ledger (PostgreSQL)
def _cur():
    try:
        import psycopg2
        from shared import db  # noqa: F401
        from shared.config import config
        conn = psycopg2.connect(host=config.db_host, port=config.db_port, dbname=config.db_name, user=config.db_user, password=config.db_password)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM research_strategy_run LIMIT 1")
        conn.rollback()
        return conn
    except Exception as e:  # pragma: no cover
        pytest.skip(f"database with migration 39 not reachable: {e}")


def test_ledger_registers_idempotently_refuses_edits_and_is_append_only(planted):
    import psycopg2
    from research.strategy_lab import ledger
    spec = copy.deepcopy(SPEC)
    spec["version"] = "vtest"                                  # never collides with a real registration
    conn = _cur()
    try:
        cur = conn.cursor()
        a = ledger.register_spec(cur, spec)
        assert a["created"] is True and ledger.register_spec(cur, spec)["created"] is False
        edited = copy.deepcopy(spec)
        edited["event"]["min_abs_surprise_pct"] = 99.0
        with pytest.raises(ledger.LedgerError):
            ledger.register_spec(cur, edited)
        rh = E.run_hash(spec, "d" * 64, "a" * 40, 5)
        r1 = ledger.record_run(cur, spec, data_hash="d" * 64, code_ref="a" * 40, run_hash=rh, seed=5, result=planted, report_sha256="f" * 64)
        r2 = ledger.record_run(cur, spec, data_hash="d" * 64, code_ref="a" * 40, run_hash=rh, seed=5, result=planted, report_sha256="f" * 64)
        assert r1["created"] is True and r2["created"] is False and r1["id"] == r2["id"]
        for sql in ("UPDATE research_strategy_run SET verdict = 'NO_EDGE_DEMONSTRATED'", "DELETE FROM research_strategy_run", "TRUNCATE research_strategy_run",
                    "UPDATE research_strategy_spec SET version = 'x'", "DELETE FROM research_strategy_spec"):
            cur.execute("SAVEPOINT s")
            with pytest.raises(psycopg2.Error):
                cur.execute(sql)
            cur.execute("ROLLBACK TO SAVEPOINT s")
    finally:
        conn.rollback()
        conn.close()
