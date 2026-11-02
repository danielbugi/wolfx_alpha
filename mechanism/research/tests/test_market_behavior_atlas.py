"""Market Behavior Atlas library: the ALFRED release-date search, discontinuity windows, event-window arithmetic, block-bootstrap guards, spells, and the breakout event
definitions on a hand-built panel. Synthetic data only (no network, no production)."""
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism", "research", "market_behavior_atlas"))

import atlas_lib as A  # noqa: E402
import fetch_macro as F  # noqa: E402


class FakeAlfred:
    """Observation month M is published on release[M]; any vintage before that lacks it."""

    def __init__(self, release):
        self.release, self.calls = release, 0

    def vintage(self, sid, d, cosd):
        self.calls += 1
        return {m.isoformat(): 1.0 for m, rd in self.release.items() if d >= rd}


def test_release_search_finds_the_first_publication_date_and_skips_cancelled_months():
    al = FakeAlfred({date(2020, 1, 1): date(2020, 2, 13), date(2020, 2, 1): date(2020, 3, 11)})
    assert F.find_release(al, "X", date(2020, 1, 1), date(2020, 2, 1), date(2020, 3, 31)) == date(2020, 2, 13)
    assert F.find_release(al, "X", date(2020, 2, 1), date(2020, 3, 1), date(2020, 4, 30)) == date(2020, 3, 11)
    assert F.find_release(al, "X", date(2020, 3, 1), date(2020, 4, 1), date(2020, 5, 31)) is None       # never published
    assert al.calls < 30


def _frame(vals, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(vals))
    return pd.DataFrame({"S": vals}, index=idx)


def test_window_clean_looks_exactly_at_the_requested_window():
    disc = _frame([False] * 5 + [True] + [False] * 5)
    fwd = A.window_clean(disc, 0, 3)["S"].values             # (t, t+3]
    assert list(np.flatnonzero(~fwd[:8])) == [2, 3, 4]
    back = A.window_clean(disc, 3, 0)["S"].values            # (t-3, t]
    assert list(np.flatnonzero(~back)) == [5, 6, 7]


def test_event_returns_measure_from_the_close_before_and_move_weekend_events():
    idx = pd.bdate_range("2021-01-04", periods=40)
    s = pd.Series(100 * 1.01 ** np.arange(40), index=idx)
    er = A.event_returns(s, pd.DatetimeIndex([idx[10], pd.Timestamp("2021-01-09")]), ks=(0, 5))
    assert abs(er.iloc[0]["k0"] - 0.01) < 1e-12 and abs(er.iloc[0]["k5"] - (1.01 ** 6 - 1)) < 1e-12
    assert bool(er.iloc[1]["moved"]) and er.iloc[1]["session"] == pd.Timestamp("2021-01-11")


def test_block_bootstrap_refuses_an_interval_with_fewer_than_two_blocks():
    m, lo, hi = A.block_bootstrap(pd.Series([0.1, 0.2, 0.3, 0.4, 0.5]), block=12)
    assert abs(m - 0.3) < 1e-12 and np.isnan(lo) and np.isnan(hi)
    m, lo, hi = A.block_bootstrap(pd.Series(np.linspace(0, 1, 60)), block=3)
    assert lo < m < hi


def test_spells_count_separate_runs():
    s = pd.Series(list("BBXXBXBB"), index=pd.bdate_range("2020-01-01", periods=8))
    assert A.spells(s, "X") == 2 and A.spells(s, "B") == 3


def _panel(close, high=None, low=None):
    idx = pd.bdate_range("2019-01-01", periods=len(close))
    C = pd.DataFrame({"S": close}, index=idx, dtype="float32")
    H = pd.DataFrame({"S": high if high is not None else close}, index=idx, dtype="float32")
    L = pd.DataFrame({"S": low if low is not None else close}, index=idx, dtype="float32")
    V = pd.DataFrame({"S": np.full(len(close), 1e6)}, index=idx, dtype="float32")
    disc = C.astype(bool) & False
    el = pd.DataFrame({"S": np.ones(len(close), bool)}, index=idx)
    return A.Panel(C, H, L, C, V, disc, el, {})


def test_breakout_is_the_first_close_above_the_prior_high_and_failure_is_a_close_back_below():
    c = [10.0] * 30 + [11.0, 11.2, 9.9] + [10.0] * 140
    p = _panel(c)
    e = A.breakout_events(p, 20, "up")
    assert list(e["date"]) == [p.C.index[30]]                 # bar 31 is above the level too, but not a fresh breakout
    assert bool(e.iloc[0]["failed10"])                         # closed back at 9.9 inside 10 sessions
    assert abs(e.iloc[0]["level"] - 10.0) < 1e-6


def test_a_stray_calendar_date_is_dropped_and_recorded(tmp_path):
    """Regression: one symbol priced on a market holiday (CBL on 2026-02-16 in production) must not put a NaN row into every other stock's rolling windows."""
    import csv, gzip, hashlib, io, json
    days = list(pd.bdate_range("2021-01-04", periods=6))
    holiday = pd.Timestamp("2021-01-09")                       # a Saturday: only one symbol has a row
    pp = tmp_path / "prices.csv.gz"
    with gzip.open(pp, "wt", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["symbol", "date", "open", "high", "low", "close", "volume"])
        for k in range(520):
            for d in days:
                w.writerow([f"S{k}", d.date().isoformat(), 10, 10.5, 9.5, 10, 1000000])
        w.writerow(["S0", holiday.date().isoformat(), 10, 10.5, 9.5, 10, 1000000])
    dp = tmp_path / "discontinuities.csv.gz"
    with gzip.open(dp, "wt") as f:
        f.write("symbol,date,kind,ratio\n")
    sha = lambda p_: hashlib.sha256(open(p_, "rb").read()).hexdigest()
    json.dump({"prices_sha256": sha(pp), "discontinuities_sha256": sha(dp)}, open(tmp_path / "extract_meta.json", "w"))
    p = A.load_panel(str(tmp_path))
    assert holiday not in p.C.index and len(p.C.index) == 6
    assert p.meta["stray_dates_dropped"] == {"2021-01-09": 1}


def test_signal_time_features_do_not_use_later_bars():
    rng = np.random.default_rng(3)
    c = 50 * np.exp(np.cumsum(rng.normal(0, 0.02, 400)))
    p = _panel(c, c * 1.01, c * 0.99)
    f1 = A.point_features(p)
    c2 = c.copy(); c2[300:] *= 3
    p2 = _panel(c2, c2 * 1.01, c2 * 0.99)
    f2 = A.point_features(p2)
    for k in f1:
        np.testing.assert_array_equal(f1[k].values[:300], f2[k].values[:300], err_msg=k)
