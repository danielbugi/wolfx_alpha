"""Exit-policy engine: fill mechanics, no lookahead, costs, independence and aggregation, on hand-built bars (pure; no database)."""
import numpy as np
import pytest

from research import exit_policies as X

# Bars are (open, high, low, close). Index 0 is the signal bar; the position enters at the OPEN of index 1. ATR = 1.0 => 1R = 2.0 price units, entry 100 => long stop 98.
FLAT = (100.0, 100.4, 99.6, 100.0)


def arrays(bars):
    a = np.array(bars, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def run(policy, bars, direction=1, atr=1.0, signal_bar=0, **kw):
    o, h, l, c = arrays(bars)
    return X.simulate(policy, o, h, l, c, signal_bar, direction, atr, **kw)


# ---------------------------------------------------------------------------------------------------------------------------- policy A
def test_a_long_target_fills_at_the_target_level_not_the_bar_high():
    t = run("A_atr_stop_fixed_3R", [FLAT, FLAT, (100.5, 106.5, 100.0, 106.2)])
    assert t.reason == "target" and t.exit_price == 106.0 and t.r_gross == pytest.approx(3.0) and t.duration == 2 and t.exit_bar == 2


def test_a_long_stop_fills_at_the_stop_level():
    t = run("A_atr_stop_fixed_3R", [FLAT, (100.0, 100.5, 97.9, 98.5)])
    assert t.reason == "stop" and t.exit_price == 98.0 and t.r_gross == pytest.approx(-1.0) and t.duration == 1


def test_a_gap_through_the_stop_fills_at_the_open_and_loses_more_than_one_r():
    t = run("A_atr_stop_fixed_3R", [FLAT, FLAT, (96.0, 97.0, 95.5, 96.5)])
    assert t.reason == "stop" and t.exit_price == 96.0 and t.r_gross == pytest.approx(-2.0)


def test_a_gap_beyond_the_target_fills_at_the_open_and_wins_more_than_the_target():
    t = run("A_atr_stop_fixed_2R", [FLAT, FLAT, (110.0, 111.0, 109.0, 110.5)])
    assert t.reason == "target" and t.exit_price == 110.0 and t.r_gross == pytest.approx(5.0)


def test_when_stop_and_target_are_both_inside_one_bar_the_stop_is_taken_first():
    t = run("A_atr_stop_fixed_2R", [FLAT, (100.0, 105.0, 97.0, 100.0)])
    assert t.reason == "stop" and t.r_gross == pytest.approx(-1.0)


def test_a_short_is_the_exact_mirror_of_a_long():
    tgt = run("A_atr_stop_fixed_3R", [FLAT, (100.0, 100.0, 93.0, 94.0)], direction=-1)
    assert tgt.reason == "target" and tgt.exit_price == 94.0 and tgt.r_gross == pytest.approx(3.0)
    stp = run("A_atr_stop_fixed_3R", [FLAT, (100.0, 102.5, 99.0, 101.0)], direction=-1)
    assert stp.reason == "stop" and stp.exit_price == 102.0 and stp.r_gross == pytest.approx(-1.0)


def test_the_time_cap_exits_at_that_bars_close():
    t = run("A_atr_stop_fixed_3R", [FLAT] + [(100.0, 100.6, 99.5, 100.2)] * 6, max_hold=3)
    assert t.reason == "time" and t.duration == 3 and t.exit_price == 100.2 and t.exit_bar == 3


# ---------------------------------------------------------------------------------------------------------------------------- policy B
@pytest.fixture
def channel3(monkeypatch):
    monkeypatch.setitem(X.POLICIES, "B3", X.PolicySpec("donchian_trail", {"channel": 3}))
    monkeypatch.setitem(X.POLICIES, "D3", X.PolicySpec("hybrid_atr_to_donchian", {"switch_r": 1.0, "channel": 3}))


def test_b_exits_when_price_breaks_the_lowest_low_of_the_previous_channel_bars(channel3):
    # channel(3) for bar j = lowest low of bars j-3..j-1 (bars before the entry count). Bars 2..4 never break it.
    bars = [(100, 101, 99.0, 100.5), (100.0, 101.5, 99.5, 101.0), (101.0, 103.0, 100.4, 102.5), (102.5, 105.0, 101.8, 104.5), (104.5, 106.0, 101.0, 102.0)]
    t = run("B3", bars)
    assert t.reason == "time"                                                  # never broken: closes at the last bar
    bars2 = bars + [(102.0, 102.5, 99.0, 99.4)]
    t2 = run("B3", bars2)
    assert t2.reason == "channel" and t2.exit_bar == 5 and t2.exit_price == pytest.approx(100.4)     # lowest low of bars 2..4 = min(100.4, 101.8, 101.0)


def test_b_channel_uses_only_earlier_bars_no_lookahead(channel3):
    bars = [(100, 101, 99.0, 100.5), (100.0, 101.5, 99.5, 101.0), (101.0, 103.0, 100.4, 102.5), (102.5, 105.0, 101.8, 104.5), (104.5, 106.0, 103.0, 105.0)]
    full = run("B3", bars + [(105.0, 105.5, 90.0, 91.0)])
    cut = run("B3", bars)
    assert full.exit_bar == 5 and cut.reason == "time"
    # editing a FUTURE bar's low cannot change what happened before it
    bars_edited = bars + [(105.0, 105.5, 95.0, 96.0)]
    again = run("B3", bars_edited)
    assert again.exit_bar == 5 and again.entry_bar == full.entry_bar


# ---------------------------------------------------------------------------------------------------------------------------- policy C
def test_c_trail_ratchets_up_only_and_exits_at_the_trailing_level():
    # atr 1, mult 3 => initial stop 97. bar1 high 105 => from bar2 the stop is 102. bar2 pulls back to 101.5 -> exit at 102 (+1R).
    t = run("C_atr_trail_3x", [FLAT, (100.0, 105.0, 99.5, 104.0), (103.0, 103.5, 101.5, 102.0)])
    assert t.reason == "trail" and t.exit_price == pytest.approx(102.0) and t.r_gross == pytest.approx(1.0)


def test_c_trail_never_loosens_after_a_lower_high():
    bars = [FLAT, (100.0, 106.0, 99.8, 105.0), (105.0, 105.5, 103.2, 104.0), (104.0, 104.2, 102.9, 103.0)]   # stop becomes 103 after bar1; bar2 low 103.2 holds; bar3 low 102.9 < 103 -> exit
    t = run("C_atr_trail_3x", bars)
    assert t.reason == "trail" and t.exit_bar == 3 and t.exit_price == pytest.approx(103.0)


def test_c_has_no_fixed_target_a_big_run_is_not_capped():
    bars = [FLAT, (100.0, 120.0, 99.9, 119.0), (119.0, 130.0, 118.0, 129.0)]
    t = run("C_atr_trail_3x", bars, max_hold=3)
    assert t.reason == "time" and t.r_gross > 3.0 and t.mfe_r >= 14.0


# ---------------------------------------------------------------------------------------------------------------------------- policy D
def test_d_behaves_like_the_initial_stop_until_plus_one_r_is_reached(channel3):
    t = run("D3", [FLAT, (100.0, 101.0, 97.9, 98.5)])
    assert t.reason == "stop" and t.exit_price == 98.0


def test_d_switches_to_the_channel_after_plus_one_r_but_never_loosens_below_the_initial_stop(channel3):
    bars = [(100, 101, 96.0, 100.0), (100.0, 102.5, 99.8, 102.0),      # bar1 reaches +1R (high >= 102): switch takes effect from bar 2
            (102.0, 103.0, 100.5, 102.8),                             # channel = min(lows 0..1) = 96.0 -> clipped UP to the initial stop 98.0, not hit
            (102.8, 103.0, 97.0, 98.0)]                               # low 97.0 < 98.0 => exit at 98.0 (the initial stop floor), reason channel
    t = run("D3", bars)
    assert t.reason == "channel" and t.exit_bar == 3 and t.exit_price == pytest.approx(98.0)


# ---------------------------------------------------------------------------------------------------------------------------- costs, excursions, guards
def test_round_trip_cost_is_charged_in_r():
    g = run("A_atr_stop_fixed_3R", [FLAT, FLAT, (100.5, 106.5, 100.0, 106.2)])
    n = run("A_atr_stop_fixed_3R", [FLAT, FLAT, (100.5, 106.5, 100.0, 106.2)], cost_bps=10.0)
    assert g.r_gross == n.r_gross and g.r_net == g.r_gross and n.r_net == pytest.approx(n.r_gross - 0.001 * 100.0 / 2.0)


def test_mfe_and_mae_are_measured_in_r_up_to_and_including_the_exit_bar():
    t = run("A_atr_stop_fixed_3R", [FLAT, (100.0, 103.0, 99.0, 102.0), (102.0, 102.5, 97.0, 97.5)])
    assert t.reason == "stop" and t.mfe_r == pytest.approx(1.5) and t.mae_r == pytest.approx(1.5)


@pytest.mark.parametrize("bars,atr,sig", [([FLAT], 1.0, 0), ([FLAT, FLAT], 0.0, 0), ([FLAT, FLAT], float("nan"), 0), ([FLAT, FLAT], 1.0, 1)])
def test_a_position_that_cannot_be_entered_returns_none(bars, atr, sig):
    assert run("A_atr_stop_fixed_3R", bars, atr=atr, signal_bar=sig) is None


def test_two_policies_on_the_same_signal_account_independently():
    bars = [FLAT, (100.0, 106.5, 99.9, 106.0), (106.0, 106.2, 102.0, 102.5)]
    a = run("A_atr_stop_fixed_3R", bars)
    c = run("C_atr_trail_3x", bars)
    assert a.policy != c.policy and a.entry_bar == c.entry_bar and (a.exit_price, a.reason) != (c.exit_price, c.reason)


def test_policy_identities_are_versioned_and_distinct():
    ids = [s.identity for s in X.POLICIES.values()]
    assert len(set(ids)) == len(ids) and all("@v1:" in i for i in ids)


# ---------------------------------------------------------------------------------------------------------------------------- aggregation
def _t(r, dur=5, mfe=1.0, mae=0.5, reason="stop"):
    return X.Trade("p", 1, 1, 1 + dur, 100.0, 100.0, r, r, dur, reason, mfe, mae)


def test_aggregate_known_numbers():
    trades = [_t(2.0), _t(-1.0), _t(-1.0), _t(3.0), _t(-1.0)]
    a = X.aggregate(trades, exit_dates=["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"])
    assert a["n"] == 5 and a["expectancy_r"] == pytest.approx(0.4) and a["win_rate"] == 0.4
    assert a["profit_factor"] == pytest.approx(5.0 / 3.0, abs=1e-3) and a["total_r"] == 2.0
    assert a["max_drawdown_r_cumulative"] == 2.0                                   # cumulative: 2, 1, 0, 3, 2 -> peak 2 to trough 0
    assert a["top1pct_share_of_gross_profit"] == pytest.approx(0.6)                # k = max(1, 0) = 1 trade: 3 of 5 gross profit


def test_aggregate_orders_the_drawdown_by_exit_date_not_input_order():
    trades = [_t(-1.0), _t(2.0), _t(-1.0)]
    assert X.aggregate(trades, exit_dates=["2026-01-03", "2026-01-01", "2026-01-02"])["max_drawdown_r_cumulative"] == 2.0     # by exit date: +2, -1, -1 -> peak 2, trough 0 (input order would give 1.0)
    assert X.aggregate([], None) == {"n": 0}
