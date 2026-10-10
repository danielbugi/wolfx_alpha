"""Option B (owner decision, Slice 8) and the sector provenance/freshness contract, proven against a real throwaway Postgres.

Two kinds of evidence, both from running code:
  * a REAL build of a world in which one symbol ("C") legitimately has no sector at all: it stays in the dataset, its sector-relative RS is NULL
    with the explicit state `no_sector`, its market-relative RS is its own, the independent audit passes and the readiness sector check passes;
  * raw-row scenarios (the exact rows the reader returns, edited) pushed through the assembler, the INDEPENDENT audit and the readiness function:
    stale / unknown-provenance / reconstructed / unsafe / changed / late-learned sectors, and the immutability of everything decided before a
    sector became known. The scenarios assert on the dataset VALUES, not on a state label.
"""
import dataclasses
import json
from datetime import date, timedelta

import pytest

import dataset_world as DW
from dataset_world import CAL, CUTOFF, MAT, denv, fresh_denv  # noqa: F401
from research.lab import dataset_assemble as A
from research.lab import dataset_audit as AU
from research.lab import dataset_contract as C
from research.lab import dataset_readiness as RY

T = CAL[270]                    # the first validation session: the point at which a sector "becomes known" in the scenarios
OTHER = ("A", "B")


# ------------------------------------------------------------------ helpers
def ds(rows, **match):
    return [r for r in rows if all(r[k] == v for k, v in match.items())]


def edit(raw, source, pred, **changes):
    """A copy of `raw` in which every row of `source` satisfying pred has the given columns replaced (the originals are never touched)."""
    out = {k: list(v) for k, v in raw.items()}
    out[source] = [({**dict(r), **changes} if pred(r) else dict(r)) for r in raw[source]]
    return out


def assemble(parts, raw=None, cfg=None):
    manifest, cfg0, raw0 = parts[:3]
    return A.assemble(manifest, cfg or cfg0, raw if raw is not None else raw0)


def audit_of(parts, assembly, raw=None, cfg=None):
    manifest, cfg0, raw0, _asm, ver = parts
    return AU.audit(manifest, cfg or cfg0, raw if raw is not None else raw0, assembly, ver)


def fatal(audit):
    return {f["code"] for f in audit.document["fatal"]}


def limits(audit):
    return {x["code"] for x in audit.document["limitations"]}


def rs_ok(rows, **match):
    return [r for r in ds(rows, **match) if r["rs__state"] == C.OK or r["rs_vs_sector__state"] not in (C.ABSENT, C.UNAVAILABLE)]


def sector_check(parts, assembly, cfg=None):
    cfg = cfg or parts[1]
    prim = [r for r in assembly.rows if r["horizon_sessions"] == cfg.primary_horizon]
    return next(c for c in RY.assess_data(cfg=cfg, windows_train_end=parts[0].document["windows"]["train"][1],
                                          maturity=parts[0].document["label_maturity_session"], prim=prim)["checks"]
                if c["id"] == "relative_strength_sector_pit_safe")


def carrying(rows):
    """The rows that CARRY a sector-relative value (the thing that must be provably safe)."""
    return [r for r in rows if r["rs_vs_sector_pp"] is not None]


@pytest.fixture(scope="module")
def parts(denv):
    manifest, cfg, raw = denv.parts()
    ver = C.verify_inputs(manifest.document, cfg, manifest.calendar, cfg.universe_members,
                          {s: C.fingerprint(s, raw[s], CUTOFF) for s in C.enabled_db_sources(cfg)})
    return manifest, cfg, raw, A.assemble(manifest, cfg, raw), ver


# ================================================================== the baseline world (every symbol has a sector)
def test_the_baseline_world_carries_observed_sector_relative_values_and_passes_everything(parts):
    asm = parts[3]
    assert audit_of(parts, asm).ok
    assert sector_check(parts, asm)["passed"] is True
    ok = carrying(asm.rows)
    assert ok and {r["rs_vs_sector__state"] for r in ok} == {C.OK}
    assert {r["rs_sector"] for r in ok} <= {"Tech", "Energy"} and all(r["rs_sector_pit_safe"] is True for r in ok)


# ================================================================== QUESTION 1: a legitimate no-sector stock stays and does not fail readiness
@pytest.fixture(scope="module")
def ns_parts():
    """A real database in which "C" has NO sector anywhere: no candidate sector, no sector in its RS rows, no sector-relative value."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(DW, "SECTOR_OF", {"A": "Tech", "B": "Tech"})
        gen = DW._make_env()
        env = next(gen)
        try:
            manifest, cfg, raw = env.parts()
            ver = C.verify_inputs(manifest.document, cfg, manifest.calendar, cfg.universe_members,
                                  {s: C.fingerprint(s, raw[s], CUTOFF) for s in C.enabled_db_sources(cfg)})
            build = env.build()
            yield env, (manifest, cfg, raw, A.assemble(manifest, cfg, raw), ver), build
        finally:
            try:
                next(gen)
            except StopIteration:
                pass


def test_the_no_sector_symbol_is_in_the_database_without_any_sector(ns_parts):
    _env, p, _b = ns_parts
    raw = p[2]
    assert all(r["fs_sector"] is None for r in raw["candidates"] if r["symbol"] == "C")
    rs_c = [r for r in raw["stock_rs"] if r["symbol"] == "C"]
    assert rs_c and all(r["sector"] is None and r["vs_sector_pp"] is None and r["sector_pit_safe"] is False and r["state"] == "ok" for r in rs_c)


def test_the_no_sector_symbol_is_not_excluded_from_the_dataset(ns_parts):
    _env, p, _b = ns_parts
    rows = p[3].rows
    base = {s: len(ds(rows, symbol=s)) for s in ("A", "B", "C")}
    assert base["C"] > 0 and base["C"] == base["A"] == base["B"]                    # as many rows as the symbols WITH a sector
    assert not any("sector" in d.reason.lower() for d in p[3].dropped)               # no row was dropped for want of a sector


def test_its_sector_relative_value_is_unavailable_never_zero_and_never_market_relative(ns_parts):
    _env, p, _b = ns_parts
    c_rows = [r for r in p[3].rows if r["symbol"] == "C" and r["rs__state"] == C.OK]
    assert c_rows
    for r in c_rows:
        assert r["rs_vs_sector_pp"] is None and r["rs_vs_sector__state"] == C.NO_SECTOR and r["rs_sector"] is None and r["sector"] is None
        assert r["rs_ret_pct"] is not None and r["rs_vs_spx_pp"] is not None            # its OWN market-relative RS, a different field
        assert r["rs_sector_pit_safe"] is False                                          # the RS row says so honestly; it is not relabelled
    # and the market-relative number was not copied into the sector-relative field
    assert all(r["rs_vs_sector_pp"] != r["rs_vs_spx_pp"] for r in c_rows if r["rs_vs_sector_pp"] is not None)
    # symbols WITH a sector are untouched
    assert carrying(ds(p[3].rows, symbol="A")) and all(r["rs_vs_sector__state"] == C.OK for r in carrying(ds(p[3].rows, symbol="A")))


def test_the_independent_audit_passes_and_reports_the_unavailability_as_a_limitation(ns_parts):
    _env, p, _b = ns_parts
    a = audit_of(p, p[3])
    assert a.ok and fatal(a) == set()
    assert "sector_relative_unavailable" in limits(a)
    assert a.document["counts"]["sector_relative_states"].get(C.NO_SECTOR, 0) > 0


def test_the_readiness_sector_check_passes_for_a_legitimate_no_sector_universe(ns_parts):
    _env, p, _b = ns_parts
    chk = sector_check(p, p[3])
    assert chk["passed"] is True, chk
    prim = [r for r in p[3].rows if r["horizon_sessions"] == p[1].primary_horizon]
    assert RY.sector_relative_unsafe  # the predicate under test
    assert not any(RY.sector_relative_unsafe(r) for r in prim)


def test_a_real_full_build_succeeds_and_is_deterministic(ns_parts):
    env, _p, build = ns_parts
    again = env.build()
    assert build.dataset_hash == again.dataset_hash and len(build.dataset_hash) == 64
    assert build.audit.ok and ds(build.rows, symbol="C")
    assert [r for r in ds(build.rows, symbol="C") if r["rs_vs_sector_pp"] is not None] == []


def test_the_baseline_and_no_sector_datasets_differ_only_where_the_sector_matters(ns_parts, parts):
    """Same fixture, one symbol's sector removed: only C's sector columns (and the Tech/Energy sector-context cells that depend on a sector) change;
    A and B rows' sector-relative cells are identical, so the no-sector symbol does not perturb the others."""
    base = {(r["symbol"], r["t0_session"], r["horizon_sessions"]): r for r in parts[3].rows}
    for r in ns_parts[1][3].rows:
        if r["symbol"] in OTHER:
            b = base[(r["symbol"], r["t0_session"], r["horizon_sessions"])]
            for col in ("rs_vs_sector_pp", "rs_vs_sector__state", "rs_sector", "rs_ret_pct", "rs_vs_spx_pp", "sector", "sector__state"):
                assert r[col] == b[col], (r["symbol"], r["t0_session"], col)


# ================================================================== QUESTION 2: no unsafe / reconstructed / stale sector can yield a passing value
def test_a_stale_sector_never_yields_a_sector_relative_value(parts):
    raw = dict(parts[2])
    raw["candidates"] = [({**r, "fs_sector_asof": r["session_date"] - timedelta(days=45)} if r["symbol"] == "A" and r["session_date"] >= T else r)
                         for r in parts[2]["candidates"]]
    asm = assemble(parts, raw)
    stale = [r for r in ds(asm.rows, symbol="A") if r["t0_session"] >= T and r["rs__state"] == C.OK]
    assert stale
    for r in stale:
        assert r["rs_vs_sector_pp"] is None and r["rs_vs_sector__state"] == C.SECTOR_STALE and r["rs_sector"] is None
        assert r["sector"] is None and r["sector__state"] == C.SECTOR_STALE            # a stale name is not served as if it were fresh
        assert r["rs_ret_pct"] is not None                                             # market-relative RS is still its own
    assert sector_check(parts, asm)["passed"] is True                                  # masked to NULL => nothing unsafe is carried
    assert audit_of(parts, asm, raw).ok


def test_a_stale_sector_is_not_hidden_from_the_audit_when_the_value_is_kept(parts):
    """Tamper: the assembly keeps the value although the candidate's sector evidence is stale. The independent audit must refuse it."""
    raw = dict(parts[2])
    raw["candidates"] = [({**r, "fs_sector_asof": r["session_date"] - timedelta(days=45)} if r["symbol"] == "A" and r["session_date"] >= T else r)
                         for r in parts[2]["candidates"]]
    honest = assemble(parts, raw)
    rows = [dict(r) for r in honest.rows]
    i = next(i for i, r in enumerate(rows) if r["symbol"] == "A" and r["t0_session"] >= T and r["rs__state"] == C.OK)
    orig = next(r for r in parts[3].rows if (r["symbol"], r["t0_session"], r["horizon_sessions"]) == (rows[i]["symbol"], rows[i]["t0_session"], rows[i]["horizon_sessions"]))
    rows[i]["rs_vs_sector_pp"], rows[i]["rs_vs_sector__state"], rows[i]["rs_sector"] = orig["rs_vs_sector_pp"], C.OK, orig["rs_sector"]
    a = audit_of(parts, dataclasses.replace(honest, rows=tuple(rows)), raw)
    assert not a.ok and {"selection_mismatch", "masked_value_leak"} & fatal(a)


@pytest.mark.parametrize("source", [None, "   ", ""])
def test_a_sector_of_unknown_provenance_never_yields_a_value_and_is_a_fatal_audit_finding(parts, source):
    raw = edit(parts[2], "candidates", lambda r: r["symbol"] == "A" and r["session_date"] >= T, fs_sector_source=source)
    asm = assemble(parts, raw)
    rows = [r for r in ds(asm.rows, symbol="A") if r["t0_session"] >= T and r["rs__state"] == C.OK]
    assert rows
    for r in rows:
        assert r["rs_vs_sector_pp"] is None and r["rs_vs_sector__state"] == C.SECTOR_UNKNOWN_PROVENANCE and r["sector"] is None
    assert "sector_provenance_unknown" in fatal(audit_of(parts, asm, raw))             # never quietly NULL: unknown provenance is an integrity finding


def test_a_missing_sector_asof_is_unknown_provenance_not_fresh(parts):
    raw = edit(parts[2], "candidates", lambda r: r["symbol"] == "A" and r["session_date"] >= T, fs_sector_asof=None)
    asm = assemble(parts, raw)
    rows = [r for r in ds(asm.rows, symbol="A") if r["t0_session"] >= T and r["rs__state"] == C.OK]
    assert rows and all(r["rs_vs_sector_pp"] is None and r["rs_vs_sector__state"] == C.SECTOR_UNKNOWN_PROVENANCE for r in rows)
    assert "sector_provenance_unknown" in fatal(audit_of(parts, asm, raw))


def test_a_reconstructed_relative_strength_row_never_yields_a_safe_value(parts):
    """Under the research policy ('exclude') it is not used at all; under 'include_flagged' it is carried flagged and the readiness sector check FAILS."""
    rec = edit(parts[2], "stock_rs", lambda r: r["symbol"] == "A" and r["session_date"] >= T, provenance="reconstructed", sector_pit_safe=False)
    excl = assemble(parts, rec)
    assert all(r["rs_vs_sector_pp"] is None for r in ds(excl.rows, symbol="A") if r["t0_session"] >= T)
    assert sector_check(parts, excl)["passed"] is True
    flagged_cfg = dataclasses.replace(parts[1], reconstructed_policy="include_flagged")
    flagged = assemble(parts, rec, flagged_cfg)
    kept = [r for r in ds(flagged.rows, symbol="A") if r["t0_session"] >= T and r["rs_vs_sector_pp"] is not None]
    assert kept and all(r["rs_vs_sector__state"] == C.SECTOR_RECONSTRUCTED for r in kept)
    assert all(RY.sector_relative_unsafe(r) for r in kept)
    assert sector_check(parts, flagged, flagged_cfg)["passed"] is False


def test_an_observed_value_with_a_not_pit_safe_sector_map_fails_readiness(parts):
    raw = edit(parts[2], "stock_rs", lambda r: r["symbol"] == "A" and r["session_date"] >= T, sector_pit_safe=False)
    asm = assemble(parts, raw)
    bad = [r for r in ds(asm.rows, symbol="A") if r["t0_session"] >= T and r["rs_vs_sector_pp"] is not None]
    assert bad and all(r["rs_vs_sector__state"] == C.UNSAFE_VALUE for r in bad)
    assert all(RY.sector_relative_unsafe(r) for r in bad)
    assert sector_check(parts, asm)["passed"] is False
    assert "sector_relative_unsafe_value" in limits(audit_of(parts, asm, raw))


def test_a_sector_relative_value_without_any_sector_is_unsafe_not_no_sector(parts):
    """The one case that must NOT be mistaken for a legitimate no-sector symbol: a NON-NULL value while the sector is NULL."""
    raw = edit(parts[2], "stock_rs", lambda r: r["symbol"] == "A" and r["session_date"] >= T, sector=None, sector_pit_safe=False)
    asm = assemble(parts, raw)
    bad = [r for r in ds(asm.rows, symbol="A") if r["t0_session"] >= T and r["rs_vs_sector_pp"] is not None]
    assert bad and all(r["rs_vs_sector__state"] == C.UNSAFE_VALUE and RY.sector_relative_unsafe(r) for r in bad)
    assert sector_check(parts, asm)["passed"] is False


def test_a_sector_relative_value_that_the_candidate_cannot_confirm_is_masked(parts):
    """The RS row names a sector but the candidate has none (unconfirmed) or a different one (identity conflict): the value is dropped."""
    none_raw = edit(parts[2], "candidates", lambda r: r["symbol"] == "A" and r["session_date"] >= T,
                    fs_sector=None, fs_sector_source=None, fs_sector_asof=None)
    asm = assemble(parts, none_raw)
    rows = [r for r in ds(asm.rows, symbol="A") if r["t0_session"] >= T and r["rs__state"] == C.OK]
    assert rows and all(r["rs_vs_sector_pp"] is None and r["rs_vs_sector__state"] == C.SECTOR_UNCONFIRMED for r in rows)
    assert audit_of(parts, asm, none_raw).ok
    other = edit(parts[2], "candidates", lambda r: r["symbol"] == "A" and r["session_date"] >= T, fs_sector="Health Care")
    asm = assemble(parts, other)
    rows = [r for r in ds(asm.rows, symbol="A") if r["t0_session"] >= T and r["rs__state"] == C.OK]
    assert rows and all(r["rs_vs_sector_pp"] is None and r["rs_vs_sector__state"] == C.SECTOR_IDENTITY_CONFLICT for r in rows)
    assert sector_check(parts, asm)["passed"] is True and audit_of(parts, asm, other).ok


def test_exhaustive_candidate_and_rs_combinations_never_pass_a_value_that_is_not_fresh_observed_safe_and_agreeing(parts):
    """Every combination of (candidate evidence) x (RS row shape) over real raw rows: a row carrying a sector-relative value is safe iff the
    candidate evidence is observed+fresh, the RS row is observed, PIT-safe, sector-bearing and agrees. Decided on the assembled VALUES."""
    cand_shapes = {"fresh": {}, "stale": {"age": 45}, "unknown_src": {"fs_sector_source": None}, "no_asof": {"fs_sector_asof": None},
                   "none": {"fs_sector": None, "fs_sector_source": None, "fs_sector_asof": None}, "other": {"fs_sector": "Utilities"},
                   "future_asof": {"age": -3}}
    rs_shapes = {"safe": {}, "unsafe_flag": {"sector_pit_safe": False}, "no_sector": {"sector": None, "sector_pit_safe": False},
                 "rec": {"provenance": "reconstructed", "sector_pit_safe": False}, "no_value": {"vs_sector_pp": None}}
    cfg_flagged = dataclasses.replace(parts[1], reconstructed_policy="include_flagged")
    n_safe = 0
    for cn, cs in cand_shapes.items():
        for rn, rs in rs_shapes.items():
            raw = {k: list(v) for k, v in parts[2].items()}
            raw["candidates"] = []
            for r in parts[2]["candidates"]:
                r = dict(r)
                if r["symbol"] == "A" and r["session_date"] >= T:
                    ch = dict(cs)
                    age = ch.pop("age", None)
                    r.update(ch)
                    if age is not None:
                        r["fs_sector_asof"] = r["session_date"] - timedelta(days=age)
                raw["candidates"].append(r)
            raw["stock_rs"] = [({**r, **rs} if r["symbol"] == "A" and r["session_date"] >= T else r) for r in parts[2]["stock_rs"]]
            for cfg in (parts[1], cfg_flagged):
                asm = assemble(parts, raw, cfg)
                for r in [x for x in ds(asm.rows, symbol="A") if x["t0_session"] >= T and x["rs_vs_sector_pp"] is not None]:
                    safe_world = cn == "fresh" and rn == "safe"
                    assert RY.sector_relative_unsafe(r) is (not safe_world), (cn, rn, cfg.reconstructed_policy, r["rs_vs_sector__state"])
                    if safe_world:
                        n_safe += 1
                        assert r["rs_vs_sector__state"] == C.OK and r["rs_sector"] is not None
    assert n_safe > 0


# ================================================================== forward-convergence scenarios (rows are decided once, per decision point)
def _with_sector_from(parts, symbol, start, *, name="Energy"):
    """No sector for `symbol` before `start` (candidate AND RS row), the real fixture rows from `start` on."""
    raw = {k: list(v) for k, v in parts[2].items()}
    raw["candidates"] = [({**r, "fs_sector": None, "fs_sector_source": None, "fs_sector_asof": None}
                          if r["symbol"] == symbol and r["session_date"] < start else dict(r)) for r in parts[2]["candidates"]]
    raw["stock_rs"] = [({**r, "sector": None, "sector_pit_safe": False, "vs_sector_pp": None}
                        if r["symbol"] == symbol and r["session_date"] < start else dict(r)) for r in parts[2]["stock_rs"]]
    return raw


def test_a_sector_that_becomes_available_later_gives_no_sector_before_and_a_value_after(parts):
    raw = _with_sector_from(parts, "C", T)
    asm = assemble(parts, raw)
    before = [r for r in ds(asm.rows, symbol="C") if r["t0_session"] < T and r["rs__state"] == C.OK]
    after = [r for r in ds(asm.rows, symbol="C") if r["t0_session"] >= T and r["rs_vs_sector_pp"] is not None]
    assert before and after
    assert all(r["rs_vs_sector_pp"] is None and r["rs_vs_sector__state"] == C.NO_SECTOR and r["sector"] is None for r in before)
    assert all(r["rs_vs_sector__state"] == C.OK and r["rs_sector"] == "Energy" for r in after)
    assert sector_check(parts, asm)["passed"] is True and audit_of(parts, asm, raw).ok


def test_sector_staleness_then_resumption_is_decided_per_decision_point(parts):
    """Fresh -> stale (the refresh stopped) -> fresh again: each row follows its OWN evidence; the resumption does not repair the stale rows."""
    s, e = CAL[330], CAL[490]
    raw = dict(parts[2])
    raw["candidates"] = [({**r, "fs_sector_asof": r["session_date"] - timedelta(days=45)} if r["symbol"] == "A" and s <= r["session_date"] <= e else r)
                         for r in parts[2]["candidates"]]
    asm = assemble(parts, raw)
    a = [r for r in ds(asm.rows, symbol="A") if r["rs__state"] == C.OK]
    for r in a:
        if s <= r["t0_session"] <= e:
            assert r["rs_vs_sector__state"] == C.SECTOR_STALE and r["rs_vs_sector_pp"] is None
        else:
            assert r["rs_vs_sector__state"] == C.OK and r["rs_vs_sector_pp"] is not None
    assert audit_of(parts, asm, raw).ok


def test_a_sector_change_is_represented_historically_and_a_conflicting_row_is_masked(parts):
    """A changes sector at T. Rows before T keep the OLD sector, rows from T carry the NEW one, and nothing is rewritten backwards. The RS rows
    from T that still name the old sector disagree with the candidate's observed sector and are masked, never trusted."""
    raw = dict(parts[2])
    raw["candidates"] = [({**r, "fs_sector": "Health Care"} if r["symbol"] == "A" and r["session_date"] >= T else r) for r in parts[2]["candidates"]]
    raw["stock_rs"] = [({**r, "sector": "Health Care"} if r["symbol"] == "A" and r["session_date"] >= T and r["session_date"] < CAL[430] else r)
                       for r in parts[2]["stock_rs"]]
    asm = assemble(parts, raw)
    a = [r for r in ds(asm.rows, symbol="A") if r["rs__state"] == C.OK]
    assert {r["rs_sector"] for r in a if r["t0_session"] < T} == {"Tech"}
    new = [r for r in a if T <= r["t0_session"] < CAL[430]]
    assert new and all(r["rs_sector"] == "Health Care" and r["rs_vs_sector__state"] == C.OK for r in new)
    stale_name = [r for r in a if r["t0_session"] >= CAL[430]]                        # RS rows still say Tech, the candidate says Health Care
    assert stale_name and all(r["rs_vs_sector_pp"] is None and r["rs_vs_sector__state"] == C.SECTOR_IDENTITY_CONFLICT for r in stale_name)
    assert audit_of(parts, asm, raw).ok and sector_check(parts, asm)["passed"] is True


# ================================================================== QUESTION 3: a sector learned at T cannot change what was knowable before T
def test_learning_a_sector_at_T_leaves_every_row_decided_before_T_byte_identical(parts):
    never = _with_sector_from(parts, "C", date(2100, 1, 1))                           # C never has a sector
    later = _with_sector_from(parts, "C", T)                                           # C is learned to be Energy from T
    a, b = assemble(parts, never), assemble(parts, later)
    key = lambda r: (r["symbol"], r["t0_session"], r["horizon_sessions"])
    ka, kb = {key(r): r for r in a.rows}, {key(r): r for r in b.rows}
    assert ka.keys() == kb.keys()
    early = [k for k in ka if k[1] < T]
    assert early and all(json.dumps(ka[k], sort_keys=True, default=str) == json.dumps(kb[k], sort_keys=True, default=str) for k in early)
    assert any(ka[k] != kb[k] for k in kb if k[0] == "C" and k[1] >= T)               # the learning DID change the rows it applies to (not vacuous)
    # the dataset hash of the early rows alone is therefore identical too
    early_hash = lambda rows: C.dataset_hash(parts[0], [r for r in rows if r["t0_session"] < T])
    assert early_hash(a.rows) == early_hash(b.rows)
    assert C.dataset_hash(parts[0], a.rows) != C.dataset_hash(parts[0], b.rows)
    for k in early:
        if k[0] == "C":
            assert kb[k]["sector"] is None and kb[k]["rs_sector"] is None and kb[k]["rs_vs_sector_pp"] is None
            assert kb[k]["rs_vs_sector__state"] == (C.NO_SECTOR if kb[k]["rs__state"] == C.OK else kb[k]["rs__state"])


def test_a_sector_stamped_after_the_decision_is_not_knowable_at_the_decision(parts):
    """The sector's own as-of is AFTER t0 (it was learned later): the row at t0 must not know it, and a sector-relative value is withheld."""
    raw = dict(parts[2])
    raw["candidates"] = [({**r, "fs_sector": "Tech", "fs_sector_asof": r["session_date"] + timedelta(days=3)}
                          if r["symbol"] == "A" and r["session_date"] >= T else r) for r in parts[2]["candidates"]]
    asm = assemble(parts, raw)
    rows = [r for r in ds(asm.rows, symbol="A") if r["t0_session"] >= T and r["rs__state"] == C.OK]
    assert rows
    for r in rows:
        assert r["sector"] is None and r["sector__state"] == C.SECTOR_ASOF_AFTER_T0
        assert r["rs_vs_sector_pp"] is None and r["rs_vs_sector__state"] == C.SECTOR_UNCONFIRMED and r["rs_sector"] is None
    assert audit_of(parts, asm, raw).ok


def test_a_sector_snapshot_captured_after_the_decision_deadline_is_not_known_at_the_decision(parts):
    raw = dict(parts[2])
    raw["candidates"] = [({**r, "fs_captured_at": r["captured_at"] + timedelta(days=9)} if r["symbol"] == "A" and r["session_date"] >= T else r)
                         for r in parts[2]["candidates"]]
    asm = assemble(parts, raw)
    rows = [r for r in ds(asm.rows, symbol="A") if r["t0_session"] >= T and r["rs__state"] == C.OK]
    assert rows and all(r["sector"] is None and r["rs_vs_sector_pp"] is None and r["rs_vs_sector__state"] != C.OK for r in rows)


def test_an_rs_row_written_after_the_deadline_is_not_used_so_a_late_sector_cannot_reach_back(parts):
    late = {k: list(v) for k, v in parts[2].items()}
    late["stock_rs"] = [({**r, "created_at": r["created_at"] + timedelta(days=30)} if r["symbol"] == "A" and r["session_date"] >= T else dict(r))
                        for r in parts[2]["stock_rs"]]
    asm = assemble(parts, late)
    for r in [x for x in ds(asm.rows, symbol="A") if x["t0_session"] >= T]:
        assert r["rs_vs_sector_pp"] is None and r["rs_vs_sector__state"] != C.OK


# -- the database-level facts that make the above hold in production-shaped storage --------------------------------------------------
def test_stored_sector_observations_are_append_only_in_the_database(fresh_denv):
    """A sector learned today cannot be written over what a past snapshot recorded: the stored candidate snapshot rejects UPDATE and DELETE."""
    import psycopg2

    cur = fresh_denv.conn.cursor()
    cur.execute("SELECT id, sector FROM feature_snapshot WHERE symbol = 'C' ORDER BY id LIMIT 1")
    rid, before = cur.fetchone()
    for sql in ("UPDATE feature_snapshot SET sector = 'Rewritten' WHERE id = %s", "DELETE FROM feature_snapshot WHERE id = %s"):
        with pytest.raises(psycopg2.Error):
            cur.execute(sql, (rid,))
        fresh_denv.conn.rollback()
        cur = fresh_denv.conn.cursor()
    cur.execute("SELECT sector FROM feature_snapshot WHERE id = %s", (rid,))
    assert cur.fetchone()[0] == before


def test_the_dataset_never_reads_daily_fundamentals_so_an_in_place_rewrite_cannot_change_it(fresh_denv):
    """daily_fundamentals.sector is a MUTABLE, current-classification column (ON CONFLICT ... DO UPDATE). The dataset path must not depend on it:
    rewriting it in place -- including backdating its updated_at -- leaves the dataset hash exactly unchanged."""
    from research.lab import dataset_reader as RD

    assert "daily_fundamentals" not in open(RD.__file__, encoding="utf-8").read()
    env = fresh_denv
    cur = env.conn.cursor()
    cur.execute("INSERT INTO daily_fundamentals (symbol, date, sector) VALUES ('C', %s, 'Energy'), ('A', %s, 'Technology')", (CAL[100], CAL[100]))
    env.conn.commit()
    h1 = env.build().dataset_hash
    cur.execute("UPDATE daily_fundamentals SET sector = 'Utilities', updated_at = %s WHERE symbol = 'C'", (CAL[50],))
    cur.execute("INSERT INTO daily_fundamentals (symbol, date, sector) VALUES ('C', %s, 'Utilities')", (CAL[40],))
    env.conn.commit()
    h2 = env.build().dataset_hash
    assert h1 == h2


# ================================================================== the contract's identity: old datasets stay reproducible under the old contract
def test_the_v1_contract_projects_a_v2_dataset_by_dropping_exactly_the_added_columns(parts):
    asm = parts[3]
    v1 = [C.encode_row(r, schema=C.LEGACY_DATASET_SCHEMAS[0]) for r in asm.rows[:50]]
    v2 = [C.encode_row(r, schema=C.DATASET_SCHEMA) for r in asm.rows[:50]]
    assert len(C.V2_ADDED_COLUMNS) == 2 and all(len(a) == len(b) - 2 for a, b in zip(v1, v2))
    assert C.legacy_schema_of(C.schema_hash("lab_dataset_v1")) == "lab_dataset_v1" and C.legacy_schema_of(C.schema_hash(C.DATASET_SCHEMA)) is None
    assert C.schema_hash(C.LEGACY_DATASET_SCHEMAS[0]) == "84dd207c76bd951f61b6e8a0edf7f315a9ce9e4d3694a87fb7f9e614c0d527e1"
    assert C.schema_hash(C.DATASET_SCHEMA) == "0e919310ac520442c6efbb484450ffdc8f860b5c128e4d417f0b928ece945ed1"
