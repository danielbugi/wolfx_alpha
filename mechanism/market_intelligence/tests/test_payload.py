"""The normalised payload: reshapes stored rows, recomputes nothing, never turns a missing value into a number or a classification."""
import json

import mi_samples as S
from market_intelligence import payload as P
from market_intelligence import store


def _write(conn, regime=None, provenance="observed"):
    basis = "recomputed from stored prices" if provenance == "reconstructed" else None
    cur = conn.cursor()
    store.write_session(cur, regime or S.regime(), S.relative_strength(provenance), provenance, "unit-test", "test@abc", reconstruction_basis=basis)
    conn.commit()


def _build(conn, provenance=None, include=False):
    cur = conn.cursor()
    m = store.get_market_snapshot(cur, S.T, provenance, include)
    sec = store.get_sector_snapshots(cur, S.T, provenance, include)
    return P.build(m, sec)


def test_absent_snapshot_is_explicitly_unavailable_not_empty_zero(conn):
    p = _build(conn)
    assert p["available"] is False and p["reason"] == "no_snapshot" and "regime" not in p
    assert P.build(None) == P.unavailable("no_snapshot", "No market snapshot has been captured for this session.")


def test_observed_payload_carries_every_component_and_the_audit_trail(conn):
    _write(conn)
    p = _build(conn)
    json.dumps(p)                                                    # JSON-safe
    assert p["available"] and p["observed"] and p["provenance"] == "observed" and p["provenance_note"] is None
    r = p["regime"]
    assert r["state"] == "RISK_ON" and r["is_heuristic"] and "not fitted" in r["disclaimer"]
    assert [c["key"] for c in r["components"]] == list(P.RR.WEIGHTS)
    assert all(c["present"] and c["score"] == 0.5 for c in r["components"])
    assert r["present_weight"] == 1.0 and r["thresholds"] == {"risk_on": 0.3, "risk_off": -0.3}
    assert set(p["returns"]["spx"]) == {"5", "20", "60"} and p["returns"]["universe_median"]["20"]["n_valid"] == 1200
    assert p["sector_map"]["pit_safe"] is True and p["meta"]["feature_set_version"] == "mi_v1" and len(p["meta"]["content_hash"]) == 64
    assert [s["rank_20"] for s in p["sectors"]] == [1, 2, 3]


def test_unavailable_regime_keeps_null_score_and_no_classification(conn):
    _write(conn, S.regime({}))
    r = _build(conn)["regime"]
    assert r["state"] == "UNAVAILABLE" and r["available"] is False
    assert r["score"] is None and r["strength"] is None and r["strength_label"] is None and r["agreement"] is None
    assert not any(c["present"] for c in r["components"]) and all(c["score"] is None for c in r["components"])


def test_a_partially_missing_component_is_reported_missing_with_its_reason(conn):
    scores = {k: 0.5 for k in P.RR.WEIGHTS}
    scores["c3_breadth_sma50"] = None                                # 0.20 of the weight missing -> still >= 0.70 present
    _write(conn, S.regime(scores))
    r = _build(conn)["regime"]
    c3 = next(c for c in r["components"] if c["key"] == "c3_breadth_sma50")
    assert c3["present"] is False and c3["score"] is None and c3["missing_reason"]
    assert r["present_weight"] == 0.8 and r["state"] != "UNAVAILABLE"


def test_reconstructed_payload_is_labelled_and_never_pit_safe(conn):
    _write(conn, provenance="reconstructed")
    p = _build(conn, "reconstructed", True)
    assert p["observed"] is False and p["provenance"] == "reconstructed"
    assert "not a record of what was known" in p["provenance_note"]
    assert p["sector_map"]["pit_safe"] is False


def test_sectors_of_another_provenance_never_leak_into_the_payload(conn):
    _write(conn)
    _write(conn, provenance="reconstructed")
    cur = conn.cursor()
    m = store.get_market_snapshot(cur, S.T)
    mixed = store.get_sector_snapshots(cur, S.T, include_reconstructed=True)
    assert {s["provenance"] for s in mixed} == {"observed", "reconstructed"}
    assert len(P.build(m, mixed)["sectors"]) == 3                    # only the observed three


def test_sector_with_too_few_members_stays_null_not_zero():
    market = {"session_date": "2026-09-30", "provenance": "observed", "regime_state": "UNAVAILABLE", "regime_model_version": "risk_regime_v1",
              "regime_components": {}, "regime_present_weight": 0.0, "feature_set_version": "mi_v1", "coverage": {}}
    sec = [{"provenance": "observed", "session_date": "2026-09-30", "sector": "Tiny", "n_members": 4, "rank_20": None,
            **{f"n_valid_{h}": 4 for h in (5, 20, 60)}, **{f"n_excluded_{h}": 0 for h in (5, 20, 60)}}]
    p = P.build(market, sec)
    assert p["sectors"][0]["horizons"]["20"]["ret"] is None and p["sectors"][0]["rank_20"] is None
    assert [c["missing_reason"] for c in p["regime"]["components"]] == ["not stored"] * 7


def test_definitions_describe_the_model_as_a_heuristic():
    d = P.definitions()
    json.dumps(d)
    assert d["risk_regime"]["is_heuristic"] and d["risk_regime"]["min_present_weight"] == 0.7
    assert abs(sum(c["weight"] for c in d["risk_regime"]["components"]) - 1.0) < 1e-9
    assert d["relative_strength"]["min_sector_members"] == 5 and d["provenance"]["default_reads"].startswith("observed only")
