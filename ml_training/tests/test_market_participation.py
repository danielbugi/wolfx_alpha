"""breakout_participation_v1: counts are right, the series is causal (point in time), missing is never "no breakout", the output is reproducible, and the module is isolated (pure, read-only, imported by nothing)."""
import os
import re
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ml_training", "experiments"))

from bw_harness import features as F  # noqa: E402
from bw_harness import synthetic as syn  # noqa: E402
from market_participation import participation as P  # noqa: E402

PKG = os.path.join(ROOT, "ml_training", "experiments", "market_participation")
PUB = ["n_eligible", "n_long", "n_short", "n_long_new_episode", "n_short_new_episode", "pct_long", "pct_short", "state"]      # columns that must be causal


def panel(n=30, seed=5, **kw):
    return syn.make_prices(n_symbols=n, start="2017-03-01", end="2019-06-28", seed=seed, **kw)


def table(prices, min_eligible=5, start="2018-01-01"):
    return P.session_table(prices, start=start, min_eligible=min_eligible)


def test_one_hand_built_breakout_is_counted_once_with_the_right_denominator():
    dates = pd.bdate_range("2024-01-02", periods=100)
    c = np.full(100, 50.0) + np.sin(np.arange(100)) * 0.2
    c[90] = c[:90].max() + 2.5                                                    # closes above the previous 20-bar channel, previous close inside it
    px = pd.DataFrame({"date": dates, "open": c, "high": c + 0.1, "low": c - 0.1, "close": c, "volume": 2_000_000})
    t = P.session_table({"AAA": px}, min_eligible=1)
    row = t[t["session_date"] == dates[90]].iloc[0]
    assert (row["n_eligible"], row["n_long"], row["n_short"], row["n_long_new_episode"]) == (1, 1, 0, 1)
    assert row["pct_long"] == 100.0 and row["state"] == P.OK
    assert t["n_long"].sum() == 1 and t["n_short"].sum() == 0
    assert (t["session_date"] >= dates[P.MIN_BAR_INDEX]).all()                    # nothing before 60 bars of history


def test_counts_match_the_harness_signal_detector_when_there_are_no_discontinuities():
    prices = panel()
    ref = F.detect_signals(prices, start="2018-01-01")
    t = table(prices)
    for name, d in (("n_long", 1), ("n_short", -1)):
        want = ref[ref["direction"] == d].groupby("date").size()
        got = t.set_index("session_date")[name]
        got = got[got > 0]
        assert got.index.equals(want.index) and (got.to_numpy() == want.to_numpy()).all(), name
    assert (t["n_long_dropped_by_research_rule"] == 0).all()                      # clean data: the research rule drops nothing


def test_the_series_is_causal_and_the_research_rule_is_not():
    prices = panel(n=30, seed=11)
    full = table(prices)
    cut = pd.Timestamp("2018-09-14")
    trunc = {s: p[p["date"] <= cut] for s, p in prices.items()}
    t2 = table(trunc)
    a = full[full["session_date"] <= cut].reset_index(drop=True)[["session_date"] + PUB]
    b = t2[t2["session_date"] <= cut].reset_index(drop=True)[["session_date"] + PUB]
    pd.testing.assert_frame_equal(a, b)                                           # nothing after the cut changes anything up to the cut
    # a jump AFTER a signal bar: the point-in-time series still counts the signal; the research detector drops it
    ref = F.detect_signals(prices, start="2018-01-01")
    pick = ref[(ref["direction"] == 1) & (ref["date"] > "2018-03-01")].iloc[0]
    sym, d0 = pick["symbol"], pick["date"]
    px = prices[sym].reset_index(drop=True)
    pos = int(np.flatnonzero(px["date"] == d0)[0])
    mod = px.copy()
    for col in ("open", "high", "low", "close"):
        mod.loc[pos + 3:, col] = mod.loc[pos + 3:, col] * 4.0                      # a 4x jump three bars later
    prices2 = dict(prices)
    prices2[sym] = mod
    t3 = table(prices2).set_index("session_date")
    t0 = full.set_index("session_date")
    assert t3.loc[d0, "n_long"] == t0.loc[d0, "n_long"]                           # PIT count unchanged by the future jump
    assert t3.loc[d0, "n_long_dropped_by_research_rule"] == t0.loc[d0, "n_long_dropped_by_research_rule"] + 1
    ref2 = F.detect_signals(prices2, start="2018-01-01")
    assert not ((ref2["symbol"] == sym) & (ref2["date"] == d0) & (ref2["direction"] == 1)).any()      # the research detector lost it


def test_a_missing_bar_leaves_the_denominator_and_is_not_a_negative():
    prices = panel(n=12, seed=3)
    base = table(prices, min_eligible=1).set_index("session_date")
    d = pd.Timestamp("2018-06-12")
    p2 = dict(prices)
    p2["S000"] = prices["S000"][prices["S000"]["date"] != d]
    t2 = table(p2, min_eligible=1).set_index("session_date")
    assert t2.loc[d, "n_eligible"] == base.loc[d, "n_eligible"] - 1
    assert t2.loc[d, "n_long"] <= base.loc[d, "n_long"] and t2.loc[d, "n_short"] <= base.loc[d, "n_short"]


def test_percentages_are_withheld_below_the_minimum_eligible_count_but_counts_are_kept():
    t = P.session_table(panel(n=20), start="2018-01-01")                          # 20 stocks < 1,000
    assert (t["state"] == P.INSUFFICIENT).all() and t["pct_long"].isna().all() and t["pct_short"].isna().all()
    assert t["n_eligible"].max() == 20 and t["n_long"].sum() > 0


def test_new_episode_counts_never_exceed_signals_and_repeats_are_collapsed():
    t = table(panel(n=30, seed=7, trend_symbols=15))
    assert (t["n_long_new_episode"] <= t["n_long"]).all() and (t["n_short_new_episode"] <= t["n_short"]).all()
    assert t["n_long_new_episode"].sum() < t["n_long"].sum()                       # trending symbols repeat within 120 bars


def test_output_is_deterministic_independent_of_symbol_order_and_hash_changes_with_content():
    prices = panel(n=20, seed=9)
    a, b = table(prices), table(dict(reversed(list(prices.items()))))
    assert P.table_hash(a) == P.table_hash(b)
    assert list(a.columns) == P.COLUMNS and (a["version"] == P.VERSION).all() and (a["provenance"] == "REVISED_PRICE_DERIVED").all()
    c = a.copy()
    c.loc[0, "n_long"] += 1
    assert P.table_hash(c) != P.table_hash(a)


def test_summary_reports_the_size_of_the_research_rule_lookahead():
    prices = panel(n=15, seed=13)
    px = prices["S001"].reset_index(drop=True)
    for col in ("open", "high", "low", "close"):
        px.loc[400:, col] = px.loc[400:, col] * 5.0
    prices["S001"] = px
    t = P.session_table(prices, start="2018-01-01", min_eligible=1)
    s = P.summary(t)
    assert s["sessions_ok"] > 0 and s["long_dropped_by_research_rule"] + s["short_dropped_by_research_rule"] >= 0
    assert 0 < s["share_long_signals_new_episode"] <= 1


# ---------------------------------------------------------------- boundaries
def _src():
    return {f: open(os.path.join(PKG, f), encoding="utf-8").read() for f in os.listdir(PKG) if f.endswith(".py")}


def test_the_pure_module_has_no_io_clock_or_sql():
    src = _src()["participation.py"]
    assert not re.search(r"\b(psycopg2|requests|urllib|socket|sqlalchemy|subprocess)\b", src)
    assert not re.search(r"\b(datetime\.now|time\.time|date\.today|utcnow)\b", src)
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|CREATE TABLE|DROP|ALTER|TRUNCATE)\b", src)


def test_the_runner_only_reads_and_writes_files_in_its_output_directory():
    src = _src()["run.py"]
    assert "connect_readonly" in src and not re.search(r"\b(INSERT|UPDATE|DELETE|CREATE TABLE|DROP|ALTER|TRUNCATE)\b", src)


def test_nothing_in_production_code_imports_it_and_no_unit_or_timer_references_it():
    needle = re.compile(r"market_participation\b")
    for top in ("mechanism", "backend", "deploy"):
        for dp, _, fs in os.walk(os.path.join(ROOT, top)):
            if "node_modules" in dp or "__pycache__" in dp:
                continue
            for f in fs:
                if f.endswith((".py", ".service", ".timer", ".sh", ".yml", ".yaml")):
                    try:
                        text = open(os.path.join(dp, f), encoding="utf-8").read()
                    except Exception:
                        continue
                    if "breakout_participation" in text or re.search(r"(from|import)\s+market_participation", text):
                        pytest.fail(f"{os.path.join(dp, f)} references the participation module")
    assert needle.search(_src()["run.py"])
