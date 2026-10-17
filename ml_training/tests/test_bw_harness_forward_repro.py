"""Big Winner harness: the forward report (an incomplete horizon is never a negative), reproducibility (same inputs and configuration give the same dataset hash, results hash and manifest), the realised-return
columns, and the boundaries (read-only, no writes, not imported by anything else, no unit or timer)."""
import gzip
import hashlib
import json
import os
import re
import sys
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "ml_training", "experiments"))

from bw_harness import features as F  # noqa: E402
from bw_harness import forward as FW  # noqa: E402
from bw_harness import hypotheses as H  # noqa: E402
from bw_harness import io_read as IO  # noqa: E402
from bw_harness import run as R  # noqa: E402
from bw_harness import synthetic as syn  # noqa: E402
from bw_harness.config import DEFAULT  # noqa: E402

PKG = os.path.join(ROOT, "ml_training", "experiments", "bw_harness")
CFG = replace(DEFAULT, n_boot=80, n_perm=80)


# ---------------------------------------------------------------- forward report
def bars(n, start="2026-09-01", seed=0, trend=0.0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n)
    c = 50 * np.exp(np.cumsum(rng.normal(trend, 0.01, n)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"date": dates, "open": o, "high": np.maximum(o, c) * 1.004, "low": np.minimum(o, c) * 0.996, "close": c, "volume": 1_000_000})


def cands(px, positions, direction=1, atr=0.5):
    return pd.DataFrame({"symbol": "AAA", "session_date": px["date"].to_numpy()[positions], "direction": direction, "atr14": atr, "triggered": True})


def test_an_incomplete_horizon_is_not_yet_known_and_never_a_negative():
    px = bars(40)
    lab = FW.forward_labels(cands(px, [5, 25, 39, 38]), {"AAA": px}, CFG)
    st20 = list(lab["status_20"])
    assert st20[0] == "ok" and st20[1] == "immature" and st20[2] == "no_entry_yet" and st20[3] == "immature"        # position 38 has an entry bar but not 20 bars after it
    assert set(lab["status_60"]) <= {"immature", "no_entry_yet"} and set(lab["status_120"]) <= {"immature", "no_entry_yet"}
    pending = lab[lab["status_20"] != "ok"]
    for c in ("mfe_r_20", "ge3r_20", "ge5r_20", "ge10r_20", "terminal_r_20"):
        assert pending[c].isna().all(), c                                    # no zero, no "no": not known
    assert (pending["bars_needed_20"] > 0).all() and lab.loc[lab.status_20 == "ok", "bars_needed_20"].eq(0).all()


def test_rates_use_only_labelled_candidates_and_the_report_lists_the_rest_as_pending():
    px = bars(300, trend=0.004, seed=3)
    c = cands(px, [10, 50, 100, 250, 285, 299])
    rep = FW.summarise(FW.forward_labels(c, {"AAA": px}, CFG), CFG)
    h20, h120 = rep["horizons"]["h20"], rep["horizons"]["h120"]
    assert h20["labelled"] == 5 and h20["immature"] == 1 or h20["labelled"] + h20["immature"] + h20["no_entry_yet"] == 6
    assert h20["long"]["labelled"] == h20["labelled"]                        # the rate's denominator is the labelled count, never the candidate count
    assert h120["labelled"] < h20["labelled"] and h120["immature"] + h120["no_entry_yet"] == 6 - h120["labelled"]
    assert "approx_first_maturity_date" in h120 and h120["bars_still_needed"]["min"] >= 1
    assert "never counted as a negative" in rep["rule"]


def test_the_forward_label_is_the_same_function_as_the_historical_one():
    px = bars(300, trend=0.002, seed=4)
    i = 100
    f = FW.forward_labels(cands(px, [i], atr=0.7), {"AAA": px}, CFG).iloc[0]
    from bw_harness import labels as L
    atr = np.full(len(px), np.nan); atr[i] = 0.7
    h = L.signal_labels(px.open.to_numpy(), px.high.to_numpy(), px.low.to_numpy(), px.close.to_numpy(), atr, np.array([i]), np.array([1]), CFG)
    for k in ("entry_px", "unit_r", "mfe_r_20", "mfe_r_60", "mfe_r_120", "ge5r_120", "terminal_r_60"):
        assert (pd.isna(f[k]) and pd.isna(h[k][0])) or abs(f[k] - h[k][0]) < 1e-12
    assert abs(f["unit_r"] - 1.4) < 1e-12                                    # 1R = 2 x the RECORDED ATR14


def test_a_candidate_without_a_signal_bar_carries_no_label_values():
    px = bars(100)
    c = pd.DataFrame({"symbol": ["AAA", "AAA"], "session_date": [px.date[10], pd.Timestamp("2030-01-01")], "direction": [1, 1], "atr14": [0.5, 0.5], "triggered": [True, True]})
    lab = FW.forward_labels(c, {"AAA": px}, CFG)
    bad = lab.iloc[1]
    assert bad["entry_status"] == "signal_bar_missing" and bad["status_20"] == "no_entry_yet" and np.isnan(bad["mfe_r_20"]) and np.isnan(bad["entry_px"])


def test_a_symbol_with_no_price_history_is_reported_not_dropped():
    c = pd.DataFrame({"symbol": ["ZZZ"], "session_date": [pd.Timestamp("2026-10-09")], "direction": [1], "atr14": [1.0], "triggered": [True]})
    lab = FW.forward_labels(c, {}, CFG)
    assert lab.iloc[0]["entry_status"] == "no_price_history" and lab.iloc[0]["status_120"] == "no_entry_yet"


def test_evidence_coverage_counts_the_recorded_missing_features():
    feat = pd.DataFrame({"snapshot_status": ["complete", "partial", "partial"], "sector_source": ["daily_fundamentals", None, "daily_fundamentals"], "missing_features": [[], ["ret_60d", "sector"], ["ret_60d"]]})
    cov = FW.evidence_coverage(feat)
    assert cov["snapshot_status"] == {"complete": 1, "partial": 2} and cov["missing_feature_counts"] == {"ret_60d": 2, "sector": 1} and cov["sector_source"]["none"] == 1


# ---------------------------------------------------------------- reproducibility
def pipeline(tmp, cfg=CFG, seed=51):
    prices = syn.make_prices(14, seed=seed)
    keys = F.detect_signals(prices)
    snaps = syn.make_sector_snaps(list(prices))
    return R.run_pipeline(prices, keys, snaps, None, cfg, str(tmp), reconciliation={"agreement": 1.0}, fingerprints={"stock_prices": {"rows": 1}}, code_ref="test")


def test_the_same_inputs_and_configuration_give_identical_hashes(tmp_path):
    a = pipeline(tmp_path / "a")
    b = pipeline(tmp_path / "b")
    ma, mb = a["manifest"], b["manifest"]
    assert ma["dataset"]["sha256_uncompressed"] == mb["dataset"]["sha256_uncompressed"] and ma["results_sha256"] == mb["results_sha256"] and ma["config_hash"] == mb["config_hash"]
    assert (tmp_path / "a" / "dataset.csv.gz").read_bytes() == (tmp_path / "b" / "dataset.csv.gz").read_bytes()          # even the compressed bytes (mtime 0)
    raw = gzip.decompress((tmp_path / "a" / "dataset.csv.gz").read_bytes())
    assert hashlib.sha256(raw).hexdigest() == ma["dataset"]["sha256_uncompressed"]
    on_disk = json.loads((tmp_path / "a" / "manifest.json").read_text())
    assert on_disk["config_hash"] == DEFAULT.hash() or on_disk["config_hash"] == CFG.hash()
    assert on_disk["sample_counts"]["signals"] == ma["dataset"]["rows"] and set(on_disk["seeds"]) == {"seed", "n_boot", "n_perm"}


def test_a_changed_configuration_or_input_changes_the_hashes(tmp_path):
    base = pipeline(tmp_path / "a")["manifest"]
    other_cfg = pipeline(tmp_path / "b", cfg=replace(CFG, min_rr=1.3))["manifest"]
    assert other_cfg["config_hash"] != base["config_hash"] and other_cfg["results_sha256"] != base["results_sha256"] and other_cfg["dataset"]["sha256_uncompressed"] == base["dataset"]["sha256_uncompressed"]
    other_data = pipeline(tmp_path / "c", seed=52)["manifest"]
    assert other_data["dataset"]["sha256_uncompressed"] != base["dataset"]["sha256_uncompressed"]
    other_seed = pipeline(tmp_path / "d", cfg=replace(CFG, seed=CFG.seed + 1))["manifest"]
    assert other_seed["results_sha256"] != base["results_sha256"]


def test_the_outputs_carry_counts_effect_sizes_intervals_and_the_evidence_status(tmp_path):
    out = pipeline(tmp_path)
    res = json.loads((tmp_path / "results.json").read_text())
    def bad(token):
        raise AssertionError(f"non-finite number {token} in results.json")
    json.loads((tmp_path / "results.json").read_text(), parse_constant=bad)                  # strict JSON: no NaN or Infinity tokens
    assert {"config", "config_hash", "frozen_thresholds_train", "hypothesis_results", "survivorship_sensitivity", "data_quality"} <= set(res)
    assert set(res["hypothesis_results"]["verdicts"]) == {"H1", "H2", "H3", "H4", "H5"}
    assert "no untouched historical holdout" in out["manifest"]["boundary"] and "no model trained" in out["manifest"]["boundary"]
    prov = json.loads((tmp_path / "provenance.json").read_text())
    assert {v["class"] for v in prov.values()} <= {"OBSERVED_PIT", "RECONSTRUCTED", "REVISED_PRICE_DERIVED", "UNAVAILABLE", "LABEL_HINDSIGHT", "KEY"}


# ---------------------------------------------------------------- realised returns stay separate from MFE
def test_realised_columns_are_merged_by_key_and_reported_separately_from_the_excursion(tmp_path):
    prices = syn.make_prices(10, seed=61)
    keys = F.detect_signals(prices)
    df = F.build_dataset(prices, keys, None, DEFAULT)
    sub = df[df.eligible].sample(200, random_state=1)
    realised = sub[["symbol", "signal_date", "direction"]].copy()
    realised["realised_a_atr_stop_fixed_2r"] = np.random.default_rng(0).normal(-0.1, 1.0, len(realised))
    merged = IO.merge_realised(df, realised)
    assert merged["realised_a_atr_stop_fixed_2r"].notna().sum() == 200 and len(merged) == len(df)
    assert merged["realised_b_donchian_trail_10"].isna().all() if "realised_b_donchian_trail_10" in merged else True
    assert "mfe_r_120" in merged and "realised_a_atr_stop_fixed_2r" in merged                   # two different things in two different columns
    from bw_harness import provenance as P
    assert P.classify("realised_a_atr_stop_fixed_2r")[0] == P.LABEL_HINDSIGHT


# ---------------------------------------------------------------- boundaries
def _code(path):
    text = open(path, encoding="utf-8").read()
    text = re.sub(r'""".*?"""', "", text, flags=re.S)
    return "\n".join(l.split("  #")[0] for l in text.splitlines() if not l.strip().startswith("#"))


def test_the_harness_never_writes_to_a_database():
    for name in os.listdir(PKG):
        if name.endswith(".py"):
            code = _code(os.path.join(PKG, name))
            assert not re.search(r"""["'](\s*)(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\b""", code, re.I), name
            assert "commit()" not in code, name


def test_only_the_io_module_touches_a_driver_or_the_environment():
    for name in os.listdir(PKG):
        if name.endswith(".py") and name not in ("io_read.py", "run.py"):
            code = _code(os.path.join(PKG, name))
            assert "psycopg2" not in code and "os.environ" not in code and "getenv" not in code, name


def test_the_session_is_refused_unless_the_server_says_it_is_read_only(monkeypatch):
    import types
    class Cur:
        def __init__(self, v): self.v = v
        def execute(self, *a): pass
        def fetchone(self): return (self.v,)
    class Conn:
        def __init__(self, v): self.v = v
        def cursor(self): return Cur(self.v)
        def rollback(self): pass
        def close(self): pass
    fake = types.SimpleNamespace(connect=lambda **kw: Conn("off"))
    monkeypatch.setitem(sys.modules, "psycopg2", fake)
    for k in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"):
        monkeypatch.setenv(k, "x")
    with pytest.raises(SystemExit, match="not read-only"):
        IO.connect_readonly()
    monkeypatch.setitem(sys.modules, "psycopg2", types.SimpleNamespace(connect=lambda **kw: Conn("on")))
    assert IO.connect_readonly() is not None


def test_nothing_else_in_the_repository_imports_the_harness_and_no_unit_or_timer_runs_it():
    hits = []
    for top in ("mechanism", "backend", "ml_training", "deploy", ".github"):
        for base, dirs, files in os.walk(os.path.join(ROOT, top)):
            # market_participation is a sibling research package that reuses the harness's read-only IO and episode rule; it has its own isolation test (test_market_participation.py)
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules", "bw_harness", "tests", ".venv")]
            if os.path.relpath(base, ROOT).replace(os.sep, "/") == "ml_training/experiments":
                dirs[:] = [d for d in dirs if d != "market_participation"]               # only THIS path is exempt, not any directory of that name anywhere in mechanism / backend / deploy
            for f in files:
                if f.endswith((".py", ".sh", ".service", ".timer", ".yml", ".yaml")):
                    p = os.path.join(base, f)
                    if re.search(r"bw_harness|big_winner_harness", open(p, encoding="utf-8", errors="replace").read()):
                        hits.append(os.path.relpath(p, ROOT))
    assert hits == [], hits


def test_no_model_is_trained_by_the_harness():
    for name in os.listdir(PKG):
        if name.endswith(".py"):
            code = _code(os.path.join(PKG, name))
            assert not re.search(r"xgboost|sklearn|torch|\.fit\(|joblib|pickle", code), name
