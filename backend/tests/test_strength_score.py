"""Strength Score (shared Screener engine): one definition, weights that sum to 1, a data-health gate that withholds the score
with a reason, components exposed for the breakdown, sortable and filterable, and a "Strongest" preset."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import discovery_service as D  # noqa: E402


def test_weights_sum_to_one_and_match_the_stated_definition():
    assert abs(sum(D.STRENGTH_WEIGHTS.values()) - 1) < 1e-12
    assert D.STRENGTH_WEIGHTS == {"rs": 0.35, "trend": 0.25, "momentum": 0.20, "volume": 0.10, "liquidity": 0.10}
    text = D.METRICS["strength_score"].definition
    for part in ("relative strength 35%", "trend 25%", "momentum 20%", "volume 10%", "liquidity 10%", "under $1M a day", "last 90 days"):
        assert part in text


def test_the_gate_withholds_the_score_in_a_fixed_order_with_a_reason():
    sql = D.page_sql("TRUE", D.order_clause("strength_score", "desc"))
    reasons = ["no relative-strength snapshot for the session", "indicators missing for the session", "fewer than 20 sessions of trading",
               "illiquid (under $1M a day)", "price discontinuity recorded in the last 90 days"]
    positions = [sql.index(r) for r in reasons]
    assert positions == sorted(positions)
    assert "CASE WHEN strength_reason IS NULL THEN ROUND(" in sql            # no reason -> a score; a reason -> NULL, never 0
    assert "FROM price_discontinuities" in sql and "%(disc_days)s" in sql
    assert "strength_reason" in D.ROW_COLUMNS


def test_components_are_exposed_sortable_and_filterable():
    for k in ("strength_score", "strength_rs", "strength_trend", "strength_momentum", "strength_volume", "strength_liquidity"):
        assert k in D.SORT_KEYS and D.METRICS[k].numeric and D.METRICS[k].family == D.DERIVED
    q = D.normalise(D.parse_params([("min_strength_score", "70"), ("sort", "strength_score")]))
    where, params = D.build_where(q)
    assert "strength_score >= %s" in where and params == [70.0]


def test_strongest_preset_sorts_by_the_score():
    q = D.normalise(D.parse_params([("preset", "strongest")]))
    assert D.PRESETS["strongest"]["sort"] == ("strength_score", "desc")
    assert q.preset == "strongest"


def test_definition_wording_stays_descriptive():
    text = " ".join(D.METRICS[k].definition for k in D.METRICS if k.startswith("strength")).lower()
    for banned in ("buy", "sell", "predict", "forecast", "opportunit", "signal"):
        assert banned not in text
