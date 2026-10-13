"""Big-winner intelligence on hand-built paths: hindsight excursions, capture ratios, premature exits, tiers, tail concentration, and the line between hindsight and decision-time information."""
import math

import numpy as np
import pytest

from research import opportunity as OP


def bars(*rows):
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def rising(n, step=1.0, start=100.0):
    rows = []
    p = start
    for _ in range(n):
        rows.append((p, p + step + 0.2, p - 0.1, p + step))
        p += step
    return bars(*rows)


def test_favourable_excursion_of_a_long_in_pct_and_r_with_the_peak_bar():
    o, h, l, c = rising(30, step=1.0)
    f = OP.favourable_excursion(1, 100.0, 1.0, o, h, l, c, 0, horizons=(10, 20))
    assert f["decision_time_information"] is False
    assert f["h10"]["max_favourable_pct"] == pytest.approx(10.2) and f["h10"]["max_favourable_r"] == pytest.approx(5.1)      # highest high 110.2, unit 2.0
    assert f["h10"]["bars_to_peak"] == 10 and f["h20"]["bars_to_peak"] == 20 and f["h10"]["max_adverse_before_peak_r"] == pytest.approx(0.05)
    assert f["h10"]["close_return_pct"] == pytest.approx(10.0)


def test_a_short_is_the_mirror_of_a_long():
    o, h, l, c = rising(30, step=1.0)
    long = OP.favourable_excursion(1, 100.0, 1.0, o, h, l, c, 0, horizons=(20,))
    m = 400.0
    short = OP.favourable_excursion(-1, m - 100.0, 1.0, m - o, m - l, m - h, m - c, 0, horizons=(20,))
    for k in ("max_favourable_r", "bars_to_peak", "max_adverse_before_peak_r"):                      # R and timing are mirror-symmetric (a percentage is not: the reflected entry price differs)
        assert short["h20"][k] == pytest.approx(long["h20"][k])


def test_a_horizon_that_is_not_fully_inside_the_data_is_unknown_not_truncated():
    o, h, l, c = rising(30)
    f = OP.favourable_excursion(1, 100.0, 1.0, o, h, l, c, 15)
    assert f["h20"] is None and f["h60"] is None and f["h120"] is None and f["h250"] is None
    assert OP.favourable_excursion(1, 100.0, 1.0, o, h, l, c, 10, horizons=(20,))["h20"] is not None
    assert OP.favourable_excursion(1, 100.0, 0.0, o, h, l, c, 0, horizons=(10,))["h10"] is None            # unusable ATR
    h2 = h.copy()
    h2[5] = float("nan")
    assert OP.favourable_excursion(1, 100.0, 1.0, o, h2, l, c, 0, horizons=(10,))["h10"] is None            # a non-finite bar inside the window


def test_adverse_excursion_is_measured_only_up_to_the_peak():
    rows = [(100, 101, 98, 100), (100, 103, 99, 102), (102, 110, 101, 109), (109, 109, 90, 95)]            # a deep drop AFTER the peak must not count as adverse-before-peak
    o, h, l, c = bars(*rows)
    f = OP.favourable_excursion(1, 100.0, 1.0, o, h, l, c, 0, horizons=(4,))["h4"]
    assert f["bars_to_peak"] == 3 and f["max_adverse_before_peak_r"] == pytest.approx(1.0)                  # low 98 -> 2 price units = 1R


def test_tiers_use_the_120_bar_favourable_excursion():
    assert OP.tier_of(None) is None and OP.tier_of(2.9) == "none" and OP.tier_of(3.0) == "ge_3r" and OP.tier_of(7.0) == "ge_5r" and OP.tier_of(25.0) == "ge_10r"


def test_capture_ratio_is_realised_over_the_favourable_move_of_a_window_covering_the_hold():
    o, h, l, c = rising(70, step=1.0)
    fav = OP.favourable_excursion(1, 100.0, 1.0, o, h, l, c, 0)
    cap = OP.capture(1, 100.0, 125.0, fav, holding_bars=40)             # exited at +25% after 40 bars; the best the position saw within 60 bars was ~ +60.2%
    assert cap["horizon"] == 60 and cap["capture_ratio"] == pytest.approx(25.0 / fav["h60"]["max_favourable_pct"], abs=1e-3) and 0 < cap["capture_ratio"] < 1
    assert OP.capture(1, 100.0, 90.0, fav, 10)["capture_ratio"] < 0                                              # a loss captures a negative share
    assert OP.capture(1, 100.0, 120.0, None, 10)["capture_ratio"] is None
    flat = OP.favourable_excursion(1, 100.0, 1.0, *bars(*[(100, 100, 99, 99.5)] * 30), 0, horizons=(20,))
    assert OP.capture(1, 100.0, 99.0, {"h20": {**flat["h20"], "max_favourable_pct": 0.0}}, 5)["capture_ratio"] is None      # nothing favourable to capture


def test_a_premature_exit_is_one_followed_by_a_large_further_move():
    o, h, l, c = rising(90, step=1.0)
    m = OP.missed_continuation(1, 120.0, 1.0, h, l, 20)                  # exit at 120 on bar 20; price keeps rising for the next 60 bars
    assert m["premature"] is True and m["missed_continuation_r"] > 20 and m["bars_to_continuation_peak"] == 60 and m["decision_time_information"] is False
    flat = bars(*[(100, 100.4, 99.6, 100)] * 90)
    assert OP.missed_continuation(1, 100.0, 1.0, flat[1], flat[2], 20)["premature"] is False
    assert OP.missed_continuation(1, 120.0, 1.0, h, l, 60)["premature"] is None                          # fewer than 60 bars after the exit: unknown


def test_the_complete_hindsight_payload_is_flagged_and_contains_every_part():
    o, h, l, c = rising(300, step=0.5)
    p = OP.hindsight_payload(1, 100.0, 115.0, 1.0, 30, o, h, l, c, 0, 29)
    assert p["decision_time_information"] is False and set(p) >= {"excursions", "capture", "big_winner_tier_120", "missed_continuation"}
    assert p["big_winner_tier_120"] == "ge_10r" and p["excursions"]["h250"] is not None and p["capture"]["capture_ratio"] is not None
    assert "decision_time_information" not in p["excursions"]


def test_hindsight_metrics_only_read_bars_from_the_entry_onwards():
    o, h, l, c = rising(60)
    base = OP.favourable_excursion(1, 100.0, 1.0, o, h, l, c, 20, horizons=(20,))
    h2, l2 = h.copy(), l.copy()
    h2[:20] *= 10.0                                                      # rewriting the PAST (before the entry) changes nothing
    l2[:20] *= 0.1
    assert OP.favourable_excursion(1, 100.0, 1.0, o, h2, l2, c, 20, horizons=(20,)) == base


def test_tail_concentration_of_a_skewed_payoff():
    rng = np.random.default_rng(0)
    v = np.r_[rng.normal(-0.3, 0.4, 950), rng.normal(6.0, 2.0, 50)]
    t = OP.tail_concentration(v)
    assert t["n"] == 1000 and 0 < t["share_positive"] < 0.3 and t["top_5pct_share_of_positive"] > 0.6 and t["skewness"] > 2
    assert OP.tail_concentration([])["n"] == 0
    assert OP.tail_concentration([float("nan"), None, 1.0])["n"] == 1
