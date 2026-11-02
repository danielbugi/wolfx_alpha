"""Right-tail breakout study: point-in-time safety, the exit conventions (identical to the registered C_atr_trail_3x policy when started from the same stop), gaps, censoring,
the one-open-position rule, the purge, the decision rule, and a byte-identical end-to-end run on a synthetic extract. Synthetic data only (no network, no production)."""
import csv
import gzip
import hashlib
import io
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

from research import exit_policies as X  # noqa: E402
from research.right_tail import spec as SP  # noqa: E402
from research.right_tail import study as S  # noqa: E402
from research.strategy_lab.data import Bars  # noqa: E402

SPEC = SP.get_spec()


def bdays(start: date, n: int):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def random_bars(seed: int, n: int = 700, start=date(2017, 1, 2), drift=0.0005, vol=0.02, price=40.0, volume=500_000.0):
    rng = np.random.default_rng(seed)
    c = price * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    o = np.r_[c[0], c[:-1]] * np.exp(rng.normal(0, vol / 3, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    v = volume * np.exp(rng.normal(0, 0.4, n))
    return Bars(bdays(start, n), o, h, l, c, v)


def test_spec_hash_is_stable_and_declares_the_primary_cell():
    assert SP.spec_hash() == SP.spec_hash(SP.get_spec())
    assert "BASE+RS+VOL" in SPEC["decision_rule"]["primary_cell"] and "X4_trend_sma50" in SPEC["decision_rule"]["primary_cell"]
    assert set(SPEC["exits"]) - {"common"} == set(S.EXITS)


def test_signals_and_features_never_use_a_later_bar():
    b = random_bars(1)
    x = S.prepare_symbol("AAA", b, [], SPEC)
    for cut in (300, 420, 555):
        rng = np.random.default_rng(cut)
        f = lambda a: np.r_[a[:cut + 1], a[cut + 1:] * rng.uniform(0.3, 3.0, len(a) - cut - 1)]
        b2 = Bars(b.dates, f(b.o), f(b.h), f(b.l), f(b.c), f(b.v))
        y = S.prepare_symbol("AAA", b2, [], SPEC)
        for name in ("d55", "base", "liquid", "vol_ok"):
            assert np.array_equal(getattr(x, name)[:cut + 1], getattr(y, name)[:cut + 1]), name
        for name in ("stop_d55", "stop_base", "atr", "ret126", "sma50", "wk3"):
            np.testing.assert_array_equal(getattr(x, name)[:cut + 1], getattr(y, name)[:cut + 1], err_msg=name)
        for k in x.feats:
            np.testing.assert_array_equal(x.feats[k][:cut + 1], y.feats[k][:cut + 1], err_msg=k)


def test_weekly_level_uses_completed_weeks_only():
    ds = bdays(date(2024, 1, 1), 30)                      # Mon 2024-01-01 .. 6 weeks
    low = np.arange(30, dtype=float) + 10.0
    lvl = S.weekly_trail_level(ds, low, weeks=3)
    assert np.isnan(lvl[:15]).all()                       # weeks 0-2 have fewer than three completed weeks before them
    assert lvl[15] == 10.0 and lvl[19] == 10.0            # week 3 sees weeks 0-2: lowest low = first bar
    assert lvl[20] == 15.0                                # week 4 sees weeks 1-3


def _one_symbol(o, h, l, c, sma50=None):
    n = len(c)
    b = Bars(bdays(date(2020, 1, 6), n), np.asarray(o, float), np.asarray(h, float), np.asarray(l, float), np.asarray(c, float), np.full(n, 1e6))
    x = S.prepare_symbol("T", Bars(b.dates * 1, b.o, b.h, b.l, b.c, b.v), [], {**SPEC, "universe": {**SPEC["universe"], "min_history_bars": 0}})
    if sma50 is not None:
        x.sma50 = np.asarray(sma50, float)
    return x


def test_chandelier_exit_matches_the_registered_atr_trail_policy():
    for seed in range(40):
        b = random_bars(100 + seed, n=400, vol=0.03)
        x = S.prepare_symbol("R", b, [], {**SPEC, "universe": {**SPEC["universe"], "min_history_bars": 0}})
        s = 120
        atr = float(x.atr[s])
        e = s + 1
        stop0 = b.o[e] - 3.0 * atr
        ours = S.simulate_exit("X2_atr_trail", x, e, stop0, atr, cap=250)
        ref = X.simulate("C_atr_trail_3x", b.o, b.h, b.l, b.c, s, 1, atr, max_hold=250)
        assert ours.exit_bar == ref.exit_bar and ours.exit_price == pytest.approx(ref.exit_price), seed


def test_stop_gap_fills_at_the_open_and_counts_slippage():
    n = 30
    c = np.full(n, 100.0); o = c.copy(); h = c + 1; l = c - 1
    o[10], h[10], l[10], c[10] = 80.0, 82.0, 79.0, 81.0           # gaps through a 90 stop
    x = _one_symbol(o, h, l, c)
    out = S.simulate_exit("X1_stop_only", x, 5, 90.0, 2.0)
    assert (out.exit_bar, out.exit_price, out.reason) == (10, 80.0, "stop")
    assert out.slippage_r == pytest.approx((90 - 80) / (100 - 90))


def test_trend_exit_fills_at_the_next_open_and_time_exit_at_the_cap_close():
    n = 300
    c = np.full(n, 100.0); o = c.copy(); h = c + 0.5; l = c - 0.5
    sma = np.full(n, 99.0); sma[20] = 101.0                       # close 100 < 101 on bar 20 -> exit at the open of bar 21
    o[21] = 98.5
    x = _one_symbol(o, h, l, c, sma50=sma)
    out = S.simulate_exit("X4_trend_sma50", x, 5, 90.0, 2.0)
    assert (out.exit_bar, out.exit_price, out.reason) == (21, 98.5, "trend")
    out = S.simulate_exit("X5_time_60", x, 5, 90.0, 2.0)
    assert (out.exit_bar, out.reason, out.censored) == (64, "time", False)


def test_history_ending_before_the_exit_is_censored():
    n = 100
    c = np.linspace(100, 130, n); o = c.copy(); h = c + 0.5; l = c - 0.5
    x = _one_symbol(o, h, l, c)
    out = S.simulate_exit("X1_stop_only", x, 10, 90.0, 2.0)
    assert out.censored and out.exit_bar == n - 1


def test_segments_and_purge():
    assert S.segment_of(SPEC, date(2017, 9, 1), date(2017, 10, 1)) == "warmup"
    assert S.segment_of(SPEC, date(2020, 1, 2), date(2020, 6, 1)) == "discovery"
    assert S.segment_of(SPEC, date(2022, 11, 1), date(2023, 2, 1)) == "purged"
    assert S.segment_of(SPEC, date(2023, 1, 3), date(2023, 2, 1)) == "validation"


def _cell(n, exp, ci, s5):
    return {"n": n, "expectancy_r": exp, "ci95": ci, "share_ge": {"5R": s5}}


def test_decision_rule():
    good = {"validation": _cell(200, 0.3, [0.1, 0.5], 0.1), "discovery": _cell(300, 0.2, [0.0, 0.4], 0.09),
            "random_validation": _cell(200, 0.0, [-0.1, 0.1], 0.03), "random_discovery": _cell(300, 0.0, [-0.1, 0.1], 0.03)}
    assert S.decide(good, {"ci95": [0.05, 0.4]})[0] == "CONTINUE"
    assert S.decide(good, {"ci95": [-0.05, 0.4]})[0] == "REVISE"
    assert S.decide({**good, "validation": _cell(200, -0.1, [-0.3, 0.1], 0.1)}, {"ci95": [-0.2, 0.1]})[0] == "REJECT"
    assert S.decide({**good, "validation": _cell(100, 0.3, [0.1, 0.5], 0.1)}, {"ci95": [0.05, 0.4]})[0] == "INSUFFICIENT_DATA"


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ end to end
def write_extract(d, n_sym=70, n=700):
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "prices.csv.gz")
    with gzip.GzipFile(p, "wb", mtime=0) as gz, io.TextIOWrapper(gz, encoding="utf-8", newline="") as tw:
        w = csv.writer(tw, lineterminator="\n")
        w.writerow(["symbol", "date", "open", "high", "low", "close", "volume"])
        for k in range(n_sym):
            b = random_bars(k, n=n, drift=0.001 if k % 3 == 0 else 0.0002, vol=0.025)
            for i in range(n):
                w.writerow([f"S{k:03d}", b.dates[i].isoformat(), f"{b.o[i]:.6f}", f"{b.h[i]:.6f}", f"{b.l[i]:.6f}", f"{b.c[i]:.6f}", str(int(b.v[i]))])
    q = os.path.join(d, "discontinuities.csv.gz")
    with gzip.GzipFile(q, "wb", mtime=0) as gz:
        gz.write(b"symbol,date,kind,ratio\nS001,2018-06-01,jump_up,3.5\n")
    sha = lambda f: hashlib.sha256(open(f, "rb").read()).hexdigest()
    json.dump({"prices_sha256": sha(p), "discontinuities_sha256": sha(q)}, open(os.path.join(d, "extract_meta.json"), "w"))


def test_end_to_end_runs_and_is_byte_identical(tmp_path):
    from research.right_tail import __main__ as M
    data = tmp_path / "x"
    write_extract(str(data))
    r1 = M.run(str(data), str(tmp_path / "o1"), "test", n_boot=50)
    M.run(str(data), str(tmp_path / "o2"), "test", n_boot=50)
    a = open(tmp_path / "o1" / "result.json", "rb").read()
    assert a == open(tmp_path / "o2" / "result.json", "rb").read()
    assert r1["primary"]["verdict"] in ("CONTINUE", "REVISE", "REJECT", "INSUFFICIENT_DATA")
    assert any(v["n"] > 0 for k, v in r1["cells"].items() if k.startswith("D55|"))


def test_extract_refuses_altered_files(tmp_path):
    d = tmp_path / "x"
    write_extract(str(d), n_sym=2, n=50)
    with open(d / "discontinuities.csv.gz", "ab") as f:
        f.write(b"x")
    with pytest.raises(S.DataError):
        S.load_extract(str(d))


def test_one_open_position_per_symbol():
    b = random_bars(7, n=700, drift=0.002, vol=0.02)
    x = S.prepare_symbol("UP", b, [], SPEC)
    sd = {"UP": x}
    # a minimal market by hand: one symbol cannot be ranked (ranking needs 50 names), and the D55 set does not use RS
    x.gidx = np.arange(len(b.c))
    mk = S.Market(b.dates, {"UP": np.full(len(b.c), 0.9)}, np.ones(len(b.c), bool), np.full(len(b.c), 0.5), np.ones(len(b.c)))
    sim = S.Simulator(SPEC, sd, mk)
    ps = S.account(sim, "D55", "X1_stop_only", Counter())
    for a, c in zip(ps, ps[1:]):
        assert c.s > a.out.exit_bar
