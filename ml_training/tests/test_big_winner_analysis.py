"""Big-winner analysis on synthetic data: the purge keeps label windows out of the test period, tails and tiers are computed from the right observations, capture is measured on big winners only,
lift and AUC are fitted on train and judged on test, and a model that sees only noise is not reported as skilled."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ml_training", "experiments"))

import big_winner_analysis as B  # noqa: E402


def test_the_split_purges_train_signals_whose_label_window_reaches_into_the_test_period():
    dates = pd.to_datetime(["2021-01-04", "2021-06-30", "2021-07-05", "2021-12-31", "2022-01-03", "2022-02-14", "2022-02-15", "2023-05-01"])
    s = B.purge_split(dates, "2022-01-01", "2022-02-15", label_purge_days=180)
    assert list(s) == ["train", "train", "train", "purged", "embargo", "embargo", "test", "test"]      # test start - 180 days = 2021-08-19: only the 2021-12-31 label window reaches into the test period
    assert list(B.purge_split(dates, "2022-01-01", "2022-02-15", label_purge_days=0))[2:4] == ["train", "train"]


def test_tail_summary_of_a_skewed_payoff():
    rng = np.random.default_rng(0)
    v = np.r_[rng.normal(-0.3, 0.3, 950), rng.normal(6, 2, 50)]
    t = B.tail_summary(v)
    assert t["n"] == 1000 and t["top_5pct_share_of_positive"] > 0.6 and t["skewness"] > 2 and t["p99"] > t["p90"] > t["median"]
    assert B.tail_summary([]) == {"n": 0} and B.tail_summary([float("nan"), None])["n"] == 0


def test_opportunity_distribution_by_direction_with_unknown_windows_counted_not_filled():
    rng = np.random.default_rng(1)
    n = 400
    direction = np.where(np.arange(n) % 2 == 0, 1, -1)
    fav = {60: np.abs(rng.normal(2, 2, n)), 120: np.abs(rng.normal(3, 3, n))}
    fav[120][:30] = np.nan
    out = B.opportunity_distribution(fav, direction)
    assert out["all"]["n_signals"] == 400 and out["bullish"]["n_signals"] == 200 and out["all"]["by_horizon"]["120"]["n_unknown"] == 30
    t = out["all"]["tiers_120"]
    assert t["ge_3r"] >= t["ge_5r"] >= t["ge_10r"] and t["ge_3r"] == pytest.approx((fav[120][30:] >= 3).mean(), abs=1e-4)


def test_capture_is_measured_on_the_big_winners_a_variant_took():
    n = 10
    big = np.array([True] * 4 + [False] * 6)
    realized = np.array([[3.0, 1.0]] * 4 + [[-1.0, -1.0]] * 6)
    mfe = np.array([[8.0, 8.0]] * 4 + [[0.5, 0.5]] * 6)
    cap = np.array([[0.4, 0.15]] * 4 + [[-0.2, -0.2]] * 6)
    miss = np.array([[1.0, 6.0]] * 10)
    prem = np.array([[0.0, 1.0]] * 10)
    taken = np.ones((n, 2), bool)
    taken[0, 1] = False                                                  # variant 2 skipped the first big winner (one-open-position rule)
    out = B.capture_by_variant(["a", "b"], realized, mfe, cap, miss, prem, big, taken)
    assert out["a"]["big_winner_positions"] == 4 and out["b"]["big_winner_positions"] == 3
    assert out["a"]["big_winner_capture_ratio"]["median"] == pytest.approx(0.4) and out["b"]["big_winner_capture_ratio"]["median"] == pytest.approx(0.15)
    assert out["a"]["big_winner_premature_exit_share"] == 0.0 and out["b"]["big_winner_premature_exit_share"] == 1.0
    assert out["a"]["big_winner_mean_realized_r"] == 3.0 and out["a"]["big_winner_mean_mfe_r"] == 8.0


def synthetic_rows(n=6000, seed=3, signal=True):
    rng = np.random.default_rng(seed)
    dates = pd.to_datetime("2019-01-01") + pd.to_timedelta(np.sort(rng.integers(0, 365 * 6, n)), unit="D")
    relvol = rng.lognormal(0.3, 0.6, n)
    atr_pct = rng.lognormal(1.0, 0.4, n)
    regime = rng.integers(0, 2, n)
    p = 1 / (1 + np.exp(-(-3.4 + (1.2 * (relvol > 2.0) if signal else 0))))
    y = (rng.random(n) < p).astype(int)
    X = pd.DataFrame({"pit_relvol": relvol, "pit_atr_pct": atr_pct, "pit_regime_up": regime})
    return dates, X, y


def test_lift_is_fitted_on_train_and_judged_on_test_per_bucket():
    dates, X, y = synthetic_rows()
    split = B.purge_split(dates, "2022-01-01", "2022-02-15")
    bucket = pd.cut(X["pit_relvol"], [0, 1.0, 2.0, 100], labels=["<1", "1-2", ">2"]).astype(str)
    months = dates.strftime("%Y-%m").to_numpy()
    t = B.lift_table(bucket, y, split, months)
    assert t["train"]["buckets"][">2"]["lift"] > 1.3 and t["test"]["buckets"][">2"]["lift"] > 1.3 and t["test"]["buckets"][">2"]["rate_ci95"][0] < t["test"]["buckets"][">2"]["rate"]
    assert t["train"]["n"] + t["test"]["n"] < len(y)                       # the embargo and purged rows are in neither


def test_auc_and_precision_at_top():
    y = np.array([0, 0, 1, 1, 0, 1])
    assert B.auc(y, np.array([0.1, 0.2, 0.9, 0.8, 0.3, 0.7])) == 1.0 and B.auc(y, np.array([0.9, 0.8, 0.1, 0.2, 0.7, 0.3])) == 0.0 and B.auc(y, np.ones(6)) == 0.5
    assert B.auc(np.zeros(5), np.arange(5.0)) is None
    pt = B.precision_at_top(y, np.array([0.1, 0.2, 0.9, 0.8, 0.3, 0.7]), 0.5)
    assert pt["precision"] == 1.0 and pt["lift"] == 2.0 and pt["n_top"] == 3


def test_a_model_finds_a_real_signal_and_is_not_fooled_by_noise():
    dates, X, y = synthetic_rows(signal=True)
    split = B.purge_split(dates, "2022-01-01", "2022-02-15")
    real = B.model_baselines(X, y, split, ["pit_atr_pct", "pit_relvol"])
    assert real["model"]["auc"] > 0.55 and real["baselines"]["pit_relvol"]["auc"] > 0.55 and real["baselines"]["pit_atr_pct"]["auc"] < 0.56
    dates, X, y = synthetic_rows(signal=False, seed=9)
    noise = B.model_baselines(X, y, B.purge_split(dates, "2022-01-01", "2022-02-15"), ["pit_atr_pct"])
    assert abs(noise["model"]["auc"] - 0.5) < 0.05                          # nothing to find: the evaluation says so
    tiny = B.model_baselines(X.iloc[:300], y[:300], split[:300], ["pit_atr_pct"])
    assert "too few positives" in tiny["note"]
