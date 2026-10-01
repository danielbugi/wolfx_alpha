"""Registry: manifest identity, hash sensitivity, register-once, refuse-on-drift."""
import pytest

from conftest import ROOT  # noqa: F401
from research import registry


def test_manifest_hash_is_stable_across_builds():
    assert registry.manifest_hash(registry.build_manifest()) == registry.manifest_hash(registry.build_manifest())
    assert registry.current_manifest_hash() == registry.manifest_hash(registry.build_manifest())


def test_renaming_a_feature_changes_the_hash(monkeypatch):
    base = registry.manifest_hash(registry.build_manifest())
    feats = list(registry._FEATURES)
    feats[2] = ("rsi_15",) + feats[2][1:]
    monkeypatch.setattr(registry, "_FEATURES", feats)
    assert registry.manifest_hash(registry.build_manifest()) != base


def test_reordering_features_changes_the_hash(monkeypatch):
    base = registry.manifest_hash(registry.build_manifest())
    monkeypatch.setattr(registry, "_FEATURES", list(reversed(registry._FEATURES)))
    assert registry.manifest_hash(registry.build_manifest()) != base


def test_changing_an_integrity_constant_changes_the_hash(monkeypatch):
    from ml_training.features import price_features as pf
    base = registry.manifest_hash(registry.build_manifest())
    monkeypatch.setattr(pf, "LOOKBACK_BARS", 200)
    assert registry.manifest_hash(registry.build_manifest()) != base


def test_manifest_has_no_earnings_fundamentals_regime_or_label_fields():
    names = {f["name"] for f in registry.build_manifest()["features"]}
    names |= {f["name"] for f in registry.build_manifest()["raw_columns"]}
    forbidden = {"earn", "earnings", "pe", "fundamental", "fundamentals", "regime", "rs", "relative", "label",
                 "outcome", "plan", "ml"}
    assert not [n for n in names if forbidden & set(n.split("_"))]
    assert "is_bullish" not in names and "breakout_dist_atr" not in names  # direction lives on the observation


def test_registers_once_and_reregistration_is_a_noop(conn):
    h1 = registry.ensure_registered(conn)
    h2 = registry.ensure_registered(conn)
    assert h1 == h2 == registry.current_manifest_hash()
    cur = conn.cursor()
    cur.execute("SELECT count(*), min(impl_ref), min(manifest_hash) FROM feature_set_registry")
    count, impl_ref, stored = cur.fetchone()
    assert count == 1 and impl_ref.startswith("t0_v1:impl=") and stored.strip() == h1


def test_registered_manifest_is_the_code_manifest(conn):
    registry.ensure_registered(conn)
    cur = conn.cursor()
    cur.execute("SELECT manifest FROM feature_set_registry WHERE feature_set_version = 't0_v1'")
    assert cur.fetchone()[0] == registry.build_manifest()


def test_refuses_when_the_code_manifest_drifts_from_the_registered_one(conn, monkeypatch):
    registry.ensure_registered(conn)
    feats = list(registry._FEATURES)
    feats[0] = ("atr_15",) + feats[0][1:]
    monkeypatch.setattr(registry, "_FEATURES", feats)
    with pytest.raises(registry.ManifestMismatch, match="new feature_set_version"):
        registry.ensure_registered(conn)


def test_unknown_version_is_refused(conn):
    with pytest.raises(ValueError):
        registry.ensure_registered(conn, "t0_v2")
