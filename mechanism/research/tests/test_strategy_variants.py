"""Strategy variant identities: pure definition/hash/key behaviour, pinned so a silent change of an exit rule or constant cannot keep its old name. No database."""
import os
import re

import pytest

from conftest import ROOT
from research import exit_policies as EP
from research import strategy_variants as V

PINNED = {
    "donchian_breakout__exit_a_atr_stop_fixed_2r": "15a7230d0671626fba0854f4f9f2f2be4f9ee89690fd56745a865b1427d587d8",
    "donchian_breakout__exit_a_atr_stop_fixed_3r": "c5f4c0edf79947fd0e02a56aff657c9d45b1e0db227f7dcd36fc6982c2f369da",
    "donchian_breakout__exit_b_donchian_trail_10": "cf6f60238e13d46a453dedced0b77cf861e7dcc28d12231151d4bc4bc84a39d9",
    "donchian_breakout__exit_c_atr_trail_3x": "eafa7e67ca563cda7ebbbd9fe0e66390168269a5e705464ee97d4fc975cc0134",
    "donchian_breakout__exit_d_hybrid_atr_stop_to_donchian_10": "0d14824dd24c000c903c9623c9a66d589af08c1d400f244d92a06f8f584bc16b",
}


def test_the_five_identities_are_pinned():
    got = {s.key: s.definition_hash for s in V.build_specs()}
    assert got == PINNED


def test_keys_fit_the_schema_and_are_unique():
    keys = [s.key for s in V.build_specs()]
    assert len(set(keys)) == len(keys) == len(EP.POLICIES)
    assert all(re.fullmatch(r"[a-z0-9_]{3,60}", k) for k in keys)
    assert all(s.version == "v1" and s.kind == "exit_policy" for s in V.build_specs())


def test_the_hash_ignores_key_order_and_follows_every_value():
    d = V.exit_policy_definition("A_atr_stop_fixed_3R", "donchian_breakout", "v1")
    shuffled = dict(reversed(list(d.items())))
    assert V.definition_hash(d) == V.definition_hash(shuffled)
    changed = V.exit_policy_definition("A_atr_stop_fixed_3R", "donchian_breakout", "v1")
    changed["exit"]["params"]["target_r"] = 3.5
    assert V.definition_hash(changed) != V.definition_hash(d)


def test_a_changed_engine_constant_changes_every_identity(monkeypatch):
    before = {s.key: s.definition_hash for s in V.build_specs()}
    monkeypatch.setattr(EP, "STOP_ATR", 2.5)
    assert all(s.definition_hash != before[s.key] for s in V.build_specs())
    monkeypatch.undo()
    monkeypatch.setattr(EP, "DEFAULT_MAX_HOLD", 40)
    assert all(s.definition_hash != before[s.key] for s in V.build_specs())


def test_the_parent_is_part_of_the_identity():
    a = {s.key: s.definition_hash for s in V.build_specs("donchian_breakout", "v1")}
    b = {s.key.replace("donchian_breakout", "donchian_breakout"): s.definition_hash for s in V.build_specs("donchian_breakout", "v2")}
    assert all(a[k] != b[k] for k in a)


def test_a_bad_key_is_refused():
    with pytest.raises(V.VariantError):
        V.variant_key("x" * 80, "A_atr_stop_fixed_2R")


def test_the_module_only_reads_strategies_and_only_inserts_into_the_variant_table():
    with open(os.path.join(ROOT, "mechanism", "research", "strategy_variants.py"), encoding="utf-8") as fh:
        text = fh.read()
    code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))
    assert sorted(set(re.findall(r"INSERT\s+INTO\s+(\w+)", code, re.I))) == ["strategy_variant"]
    assert not re.search(r"\b(UPDATE\s+\w+\s+SET|DELETE\s+FROM|TRUNCATE\s+(?:TABLE\s+)?\w+)", code, re.I)
    assert "signal_ledger" not in code.replace('"""', "").split("def parent_id")[1], "the cursor functions must not touch the ledger"
    assert re.findall(r"FROM\s+strategies\b", code) and not re.search(r"(INSERT\s+INTO|UPDATE)\s+strategies\b", code, re.I)
