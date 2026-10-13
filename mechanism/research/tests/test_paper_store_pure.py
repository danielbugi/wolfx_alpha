"""The paper store's hashing (pure): a stored hash must still verify after the data was read back from JSONB, event and observation hashes depend on every field and on the chain, and the
identity helpers round-trip."""
import json

import pytest

from research import paper_store as ST

# value -> what a JSONB column hands back (measured on Postgres 16)
JSONB_ROUND_TRIPS = [(-0.0, 0.0), (1e16, 10000000000000000), (1e22, 10000000000000000000000), (1.2345678901234567e19, 12345678901234567000), (1.7976931348623157e308, int("17976931348623157" + "0" * 292)),
                     (1e-05, 1e-05), (0.1 + 0.2, 0.1 + 0.2), (100.0, 100.0), (5e-324, 5e-324), (2, 2)]


@pytest.mark.parametrize("sent, back", JSONB_ROUND_TRIPS)
def test_a_number_hashes_the_same_before_and_after_a_jsonb_round_trip(sent, back):
    assert ST.canonical({"x": sent, "y": [sent]}) == ST.canonical({"x": back, "y": [back]})


def test_the_normalisation_does_not_hide_a_real_change():
    assert ST.canonical({"x": 1.0}) != ST.canonical({"x": 1.0000000000000002})
    assert ST.canonical({"x": 1e16}) != ST.canonical({"x": 2e16})
    assert ST.canonical({"x": True}) != ST.canonical({"x": 1}) and ST.canonical({"x": None}) != ST.canonical({"x": 0})


def reg(**o):
    p = {"variant_key": "k", "definition_hash": "d" * 64, "symbol": "AAA", "direction": 1, "signal_session": "2026-01-05", "atr": 1.5, "hold_cap": 60, "registered_cap": True,
         "hist_h": [1.0, 2.0], "hist_l": [0.5, 1.5], "context": {"a": 1, "b": {"c": [1, 2.5]}}}
    p.update(o)
    return p


def test_the_observation_hash_depends_on_every_field():
    base = ST.observation_hash(reg())
    assert base == ST.observation_hash(reg()) and len(base) == 64
    for over in (dict(atr=1.6), dict(hold_cap=120), dict(registered_cap=False), dict(symbol="BBB"), dict(direction=-1), dict(signal_session="2026-01-06"), dict(hist_h=[1.0, 2.1]), dict(hist_l=[0.5]),
                 dict(context={"a": 2}), dict(variant_key="k2"), dict(definition_hash="e" * 64)):
        assert ST.observation_hash(reg(**over)) != base, over
    assert ST.observation_hash(reg(context={"b": {"c": [1, 2.5]}, "a": 1})) == base            # key order is irrelevant


def test_the_event_hash_chains_and_depends_on_every_field():
    prev = ST.observation_hash(reg())
    h1 = ST.event_hash("k|AAA|1|2026-01-05", 1, "entered", "2026-01-06", {"entry_price": 10.0}, prev)
    assert h1 == ST.event_hash("k|AAA|1|2026-01-05", 1, "entered", "2026-01-06", {"entry_price": 10.0}, prev)
    for args in (("k|BBB|1|2026-01-05", 1, "entered", "2026-01-06", {"entry_price": 10.0}, prev), ("k|AAA|1|2026-01-05", 2, "entered", "2026-01-06", {"entry_price": 10.0}, prev),
                 ("k|AAA|1|2026-01-05", 1, "closed", "2026-01-06", {"entry_price": 10.0}, prev), ("k|AAA|1|2026-01-05", 1, "entered", "2026-01-07", {"entry_price": 10.0}, prev),
                 ("k|AAA|1|2026-01-05", 1, "entered", "2026-01-06", {"entry_price": 10.5}, prev), ("k|AAA|1|2026-01-05", 1, "entered", "2026-01-06", {"entry_price": 10.0}, "f" * 64)):
        assert ST.event_hash(*args) != h1
    h2 = ST.event_hash("k|AAA|1|2026-01-05", 2, "closed", "2026-02-01", {"exit_price": 12.0}, h1)
    assert h2 != ST.event_hash("k|AAA|1|2026-01-05", 2, "closed", "2026-02-01", {"exit_price": 12.0}, prev)       # the previous hash is part of the content: the chain cannot be re-linked


def test_identities_round_trip_and_non_finite_numbers_are_refused():
    assert ST.parse_ident("donchian_breakout__exit_a|AAA|-1|2026-01-05") == ("donchian_breakout__exit_a", "AAA", -1, "2026-01-05")
    with pytest.raises(ValueError):
        ST.sha({"x": float("nan")})
    assert json.loads(ST.canonical({"b": 1, "a": [2, {"d": 1, "c": 2}]})) == {"a": [2, {"c": 2, "d": 1}], "b": 1}
