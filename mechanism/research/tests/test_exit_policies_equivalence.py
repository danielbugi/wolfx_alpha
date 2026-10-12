"""The refactored exit engine (a loop over the stateful `advance()` that the forward paper tracker also runs) reproduces the ORIGINAL simulation exactly, on thousands of random paths:
every policy, both directions, several hold caps, gaps, flat bars, short histories, truncated data and unenterable inputs. Also the new actual-initial-risk fields and the stateful API."""
import math

import numpy as np
import pytest

from research import exit_policies as X
from reference_exit_simulation import reference_simulate

FIELDS = ("policy", "direction", "entry_bar", "exit_bar", "entry_price", "exit_price", "r_gross", "r_net", "duration", "reason", "mfe_r", "mae_r")
POLICY_NAMES = list(X.POLICIES)


def random_path(rng, n, gappy):
    o = np.empty(n)
    h = np.empty(n)
    l = np.empty(n)
    c = np.empty(n)
    price = 100.0 * math.exp(rng.normal(0, 0.1))
    for i in range(n):
        gap = rng.normal(0, 0.012 if gappy else 0.002)
        o[i] = price * (1 + gap)
        drift = rng.normal(0.0005, 0.015)
        c[i] = o[i] * (1 + drift)
        rng_hi, rng_lo = abs(rng.normal(0, 0.008)), abs(rng.normal(0, 0.008))
        h[i] = max(o[i], c[i]) * (1 + rng_hi)
        l[i] = min(o[i], c[i]) * (1 - rng_lo)
        price = c[i]
    return o, h, l, c


def assert_same(a, b, ctx):
    if a is None or b is None:
        assert a is None and b is None, ctx
        return
    for f in FIELDS:
        x, y = getattr(a, f), getattr(b, f)
        assert x == y or (isinstance(x, float) and math.isnan(x) and math.isnan(y)), f"{ctx}: {f} {x!r} != {y!r}"


@pytest.mark.parametrize("seed", range(6))
def test_the_refactored_engine_matches_the_original_on_random_paths(seed):
    rng = np.random.default_rng(seed)
    checked = 0
    for _ in range(120):
        n = int(rng.integers(5, 140))
        o, h, l, c = random_path(rng, n, gappy=bool(rng.integers(0, 2)))
        sig = int(rng.integers(0, n))
        atr = float(abs(rng.normal(1.6, 0.8))) if rng.random() > 0.03 else float(rng.choice([0.0, -1.0, float("nan")]))
        cap = int(rng.choice([1, 3, 10, 60, 250]))
        for pol in POLICY_NAMES:
            for d in (1, -1):
                ref = reference_simulate(pol, o, h, l, c, sig, d, atr, max_hold=cap, cost_bps=10.0)
                new = X.simulate(pol, o, h, l, c, sig, d, atr, max_hold=cap, cost_bps=10.0)
                assert_same(ref, new, f"seed {seed} {pol} d={d} cap={cap} sig={sig}/{n}")
                checked += 1
    assert checked == 120 * len(POLICY_NAMES) * 2


def test_non_finite_bars_inside_a_path_behave_like_the_original():
    rng = np.random.default_rng(99)
    for _ in range(60):
        o, h, l, c = random_path(rng, 80, True)
        k = int(rng.integers(3, 70))
        l[k] = float("nan")
        h[k + 1] = float("nan")
        for pol in POLICY_NAMES:
            for d in (1, -1):
                assert_same(reference_simulate(pol, o, h, l, c, 2, d, 1.5, max_hold=60), X.simulate(pol, o, h, l, c, 2, d, 1.5, max_hold=60), f"nan {pol} {d}")


def test_stepping_bar_by_bar_equals_simulate_when_the_data_is_complete():
    rng = np.random.default_rng(5)
    for _ in range(80):
        o, h, l, c = random_path(rng, 120, True)
        sig, d = int(rng.integers(12, 40)), int(rng.choice([1, -1]))
        for pol in POLICY_NAMES:
            batch = X.simulate(pol, o, h, l, c, sig, d, 1.4, max_hold=60)
            ch = X.channel_length(pol)
            e = sig + 1
            pos = X.open_position(pol, d, float(o[e]), 1.4, h[max(0, e - ch):e], l[max(0, e - ch):e], max_hold=60)
            ex, j = None, e
            while ex is None and j < len(c):
                ex = X.advance(pos, float(o[j]), float(h[j]), float(l[j]), float(c[j]))
                j += 1
            assert ex is not None and batch is not None
            t = X.make_trade(pos, ex, e, j - 1)
            assert (t.exit_bar, t.exit_price, t.reason, t.duration, t.r_gross) == (batch.exit_bar, batch.exit_price, batch.reason, batch.duration, batch.r_gross)


# ---------------------------------------------------------------------------------------------------------------------------------- actual initial risk
def bars(*rows):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


FLAT = (100.0, 100.4, 99.6, 100.0)


def sim(pol, rows, d=1, atr=1.0, **kw):
    o, h, l, c = bars(*rows)
    return X.simulate(pol, o, h, l, c, 0, d, atr, **kw)


def test_the_actual_initial_risk_differs_by_policy_and_is_not_the_standard_unit():
    rows = [FLAT] * 12 + [(100.0, 100.5, 99.5, 100.0)]                        # entry open 100, ATR 1 -> unit 2.0
    path = rows[:11] + [(100.0, 100.4, 96.0, 100.0)] + [FLAT, FLAT, (100.0, 100.0, 50.0, 60.0)]
    o, h, l, c = bars(*path)
    risk = {}
    for pol in POLICY_NAMES:
        t = X.simulate(pol, o, h, l, c, 11, 1, 1.0, max_hold=5)
        risk[pol] = (t.initial_stop, t.initial_risk, t.risk_atr_multiple)
    assert risk["A_atr_stop_fixed_2R"][:2] == (98.0, 2.0) and risk["A_atr_stop_fixed_3R"][:2] == (98.0, 2.0) and risk["D_hybrid_atr_stop_to_donchian_10"][:2] == (98.0, 2.0)
    assert risk["C_atr_trail_3x"][:2] == (97.0, 3.0)
    assert risk["B_donchian_trail_10"][:2] == (96.0, 4.0)                      # the channel low (96.0) of the 10 bars before the entry bar: wider than 1R
    assert risk["B_donchian_trail_10"][2] == pytest.approx(4.0) and risk["A_atr_stop_fixed_3R"][2] == pytest.approx(2.0)


def test_actual_r_and_standardised_r_are_related_by_the_risk_ratio():
    t = sim("C_atr_trail_3x", [FLAT, (100.0, 106.0, 99.8, 105.0), (105.0, 105.5, 100.0, 101.0)])
    assert t.initial_risk == pytest.approx(3.0) and t.unit == pytest.approx(2.0)
    assert t.r_actual_gross == pytest.approx(t.r_gross * t.unit / t.initial_risk)
    assert t.r_actual_net(10.0) == pytest.approx(t.r_actual_gross - 10e-4 * 100.0 / 3.0)
    assert t.mfe_actual_r == pytest.approx(t.mfe_r * 2.0 / 3.0)


def test_a_short_has_a_positive_risk_with_the_stop_above_entry():
    t = sim("A_atr_stop_fixed_3R", [FLAT, (100.0, 100.5, 99.5, 100.0)], d=-1)
    assert t.initial_stop == 102.0 and t.initial_risk == pytest.approx(2.0) and t.risk_valid


def test_an_entry_already_beyond_the_channel_has_no_valid_actual_risk_and_actual_r_is_nan():
    rows = [(100, 101, 100.8, 100.9)] * 12 + [(100.0, 100.2, 99.0, 99.5)]    # the 10 bars before the entry bar all have lows of 100.8: ABOVE the entry open 100.0
    o, h, l, c = bars(*rows)
    t = X.simulate("B_donchian_trail_10", o, h, l, c, 11, 1, 1.0)
    assert t.initial_risk < 0 and not t.risk_valid and math.isnan(t.r_actual_gross) and t.reason == "channel" and t.exit_price == 100.0   # gaps through the stop at the open
