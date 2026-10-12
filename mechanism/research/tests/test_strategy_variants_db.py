"""Migration 35 + research.strategy_variants against a real throwaway schema: idempotent, versioned, append-only, and the live strategy identity / ledger are untouched."""
import threading

import psycopg2
import pytest
from psycopg2.extras import Json

from research import exit_policies as EP
from research import strategy_variants as V


@pytest.fixture
def cur(variant_env):
    _, connect = variant_env
    with connect() as c:
        yield c.cursor()
        c.rollback()


def _strategies(cur):
    cur.execute("SELECT id, strategy_key, strategy_version, description, created_at FROM strategies ORDER BY id")
    return cur.fetchall()


def _count(cur, table):
    cur.execute(f"SELECT count(*) FROM {table}")
    return cur.fetchone()[0]


def _expect(cur, match, fn):
    cur.execute("SAVEPOINT s")
    with pytest.raises(psycopg2.Error) as e:
        fn()
    assert match in str(e.value), str(e.value)
    cur.execute("ROLLBACK TO SAVEPOINT s")


def _raw_insert(cur, **o):
    pid = V.parent_id(cur, "donchian_breakout", "v1")
    d = V.exit_policy_definition("A_atr_stop_fixed_3R", "donchian_breakout", "v1")
    row = dict(parent_strategy_id=pid, variant_key="donchian_breakout__exit_a_atr_stop_fixed_3r", variant_version="v1", variant_kind="exit_policy",
               definition=Json(d), definition_hash=V.definition_hash(d), supersedes_variant_id=None, description=None)
    row.update(o)
    cols = ",".join(row)
    cur.execute(f"INSERT INTO strategy_variant ({cols}) VALUES ({','.join(['%s'] * len(row))})", list(row.values()))


def test_registering_twice_is_idempotent_and_leaves_the_live_identity_and_ledger_untouched(cur):
    before_strategies, before_ledger = _strategies(cur), _count(cur, "signal_ledger")
    first = V.register(cur, V.build_specs())
    second = V.register(cur, V.build_specs())
    assert [o.action for o in first] == ["created"] * 5 and [o.action for o in second] == ["unchanged"] * 5
    assert [o.variant_id for o in first] == [o.variant_id for o in second]
    assert _count(cur, "strategy_variant") == 5
    assert _strategies(cur) == before_strategies and _count(cur, "signal_ledger") == before_ledger
    assert [r[1:3] for r in _strategies(cur)] == [("donchian_breakout", "v1")]      # no variant row leaked into strategies
    assert V.verify(cur, specs=V.build_specs()) == []


def test_a_changed_definition_is_refused_unless_a_new_version_is_requested(cur, monkeypatch):
    V.register(cur, V.build_specs())
    monkeypatch.setattr(EP, "STOP_ATR", 2.5)
    changed = V.build_specs(policies=["A_atr_stop_fixed_3R"])
    cur.execute("SAVEPOINT s")
    with pytest.raises(V.DefinitionDrift):
        V.register(cur, changed)
    cur.execute("ROLLBACK TO SAVEPOINT s")
    assert _count(cur, "strategy_variant") == 5
    out = V.register(cur, changed, new_version_on_change=True)
    assert [(o.version, o.action) for o in out] == [("v2", "superseded")]
    again = V.register(cur, changed)
    assert [(o.version, o.action) for o in again] == [("v2", "unchanged")]
    cur.execute("SELECT variant_version, supersedes_variant_id, definition ->> 'engine' FROM strategy_variant WHERE variant_key = %s ORDER BY variant_version",
                (changed[0].key,))
    (v1, s1, _), (v2, s2, _) = cur.fetchall()
    assert (v1, s1, v2) == ("v1", None, "v2") and s2 is not None
    cur.execute("SELECT definition -> 'entry' ->> 'stop_atr_multiple_is_1R' FROM strategy_variant WHERE variant_key = %s ORDER BY variant_version", (changed[0].key,))
    assert [r[0] for r in cur.fetchall()] == ["2.0", "2.5"]          # v1 is exactly what it was
    assert V.verify(cur, specs=changed) == []


def test_reverting_to_an_older_definition_is_refused(cur, monkeypatch):
    original = V.build_specs(policies=["C_atr_trail_3x"])
    V.register(cur, original)
    monkeypatch.setattr(EP, "STOP_ATR", 3.0)
    V.register(cur, V.build_specs(policies=["C_atr_trail_3x"]), new_version_on_change=True)
    monkeypatch.undo()
    cur.execute("SAVEPOINT s")
    with pytest.raises(V.DefinitionRegressed):
        V.register(cur, original, new_version_on_change=True)
    cur.execute("ROLLBACK TO SAVEPOINT s")


def test_the_same_definition_under_a_second_name_is_refused(cur):
    spec = V.build_specs(policies=["B_donchian_trail_10"])[0]
    V.register(cur, [spec])
    clone = V.VariantSpec("donchian_breakout__exit_other_name", "v1", spec.kind, spec.definition, spec.definition_hash, "clone")
    cur.execute("SAVEPOINT s")
    with pytest.raises(V.DuplicateDefinition):
        V.register(cur, [clone])
    cur.execute("ROLLBACK TO SAVEPOINT s")
    assert _count(cur, "strategy_variant") == 1


def test_a_missing_parent_is_refused_and_nothing_is_written(cur):
    with pytest.raises(V.ParentMissing):
        V.register(cur, V.build_specs("no_such_strategy", "v1"), "no_such_strategy", "v1")
    assert _count(cur, "strategy_variant") == 0


def test_rows_are_immutable(cur):
    V.register(cur, V.build_specs())
    _expect(cur, "append-only", lambda: cur.execute("UPDATE strategy_variant SET description = 'x'"))
    _expect(cur, "append-only", lambda: cur.execute("DELETE FROM strategy_variant"))
    _expect(cur, "append-only", lambda: cur.execute("TRUNCATE strategy_variant"))
    assert _count(cur, "strategy_variant") == 5


def test_the_database_enforces_lineage_and_parent_consistency(cur):
    spec = V.build_specs(policies=["A_atr_stop_fixed_3R"])[0]
    out = V.register(cur, [spec])
    v1 = out[0].variant_id
    # a second version that supersedes nothing / skips a version / names another key
    _expect(cur, "strategy_variant_v1_chk", lambda: _raw_insert(cur, variant_version="v2", definition_hash="1" * 64))
    _expect(cur, "exactly one above", lambda: _raw_insert(cur, variant_version="v3", definition_hash="2" * 64, supersedes_variant_id=v1))
    _expect(cur, "same key under the same parent", lambda: _raw_insert(cur, variant_key="donchian_breakout__exit_zzz", variant_version="v2",
                                                                          definition_hash="3" * 64, supersedes_variant_id=v1))
    # a correct v2 is accepted once, and a row cannot be superseded twice
    _raw_insert(cur, variant_version="v2", definition_hash="4" * 64, supersedes_variant_id=v1)
    _expect(cur, "uq_strategy_variant_", lambda: _raw_insert(cur, variant_version="v2", definition_hash="5" * 64, supersedes_variant_id=v1))
    # definition must echo the real parent and its own kind
    bad_parent = V.exit_policy_definition("A_atr_stop_fixed_3R", "other_key", "v9")
    _expect(cur, "names parent", lambda: _raw_insert(cur, variant_key="donchian_breakout__exit_p", definition=Json(bad_parent), definition_hash="6" * 64))
    bad_kind = dict(V.exit_policy_definition("A_atr_stop_fixed_3R", "donchian_breakout", "v1"), kind="something")
    _expect(cur, "differs from variant_kind", lambda: _raw_insert(cur, variant_key="donchian_breakout__exit_k", definition=Json(bad_kind), definition_hash="7" * 64))
    _expect(cur, "strategy_variant_hash_chk", lambda: _raw_insert(cur, variant_key="donchian_breakout__exit_h", definition_hash="NOTAHASH"))
    _expect(cur, "variant_kind", lambda: _raw_insert(cur, variant_key="donchian_breakout__exit_q", variant_kind="entry_rule", definition_hash="8" * 64))


def test_created_at_is_stamped_by_the_database(cur):
    _raw_insert(cur, created_at="2001-01-01T00:00:00+00")          # the caller's claim is overwritten
    cur.execute("SELECT created_at, clock_timestamp() FROM strategy_variant")
    created, now = cur.fetchone()
    assert 0 <= (now - created).total_seconds() < 5


def test_verify_detects_a_tampered_row(cur):
    V.register(cur, V.build_specs(policies=["A_atr_stop_fixed_3R"]))
    cur.execute("ALTER TABLE strategy_variant DISABLE TRIGGER USER")     # only a superuser in a throwaway schema could do this; the point is that verify notices
    cur.execute("UPDATE strategy_variant SET definition = jsonb_set(definition, '{exit,params,target_r}', '9')")
    for trg in ("strategy_variant_immutable_row", "strategy_variant_immutable_truncate", "strategy_variant_stamp"):
        cur.execute(f"ALTER TABLE strategy_variant ENABLE ALWAYS TRIGGER {trg}")
    problems = V.verify(cur)
    assert any("stored hash does not match" in p for p in problems), problems


def test_concurrent_registrations_converge_on_one_row_per_variant(variant_env):
    _, connect = variant_env
    errors, results = [], []

    def worker():
        try:
            with connect() as c:
                cu = c.cursor()
                results.append([o.action for o in V.register(cu, V.build_specs())])
                c.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    with connect() as c:
        cu = c.cursor()
        assert _count(cu, "strategy_variant") == 5
        assert sorted(a for r in results for a in r).count("created") == 5
