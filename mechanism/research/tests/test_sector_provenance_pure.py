"""Slice 8 -- the sector provenance / freshness contract and the Option B sector-relative cell, tested without a database.

The point of this module is EVIDENCE, not description: the exhaustive sweep below drives every combination of RS-row provenance, sector tag,
value presence, PIT flag and candidate-sector evidence through the contract and through Slice 5's own unsafe-value predicate, and asserts that a
non-NULL sector-relative value survives *and passes readiness* in exactly one situation -- an observed, fresh, PIT-safe, agreeing sector."""
import itertools
import json
import hashlib
from datetime import date, datetime, timedelta, timezone

import pytest

import research.lab.dataset_contract as C
import research.lab.dataset_readiness as RY
import research.lab.sector_provenance as SP
import research.repository as REPO

T0 = date(2026, 3, 10)
SRC = "daily_fundamentals"


def ev(sector="Technology", source=SRC, asof=T0, provenance="observed", available=True, t0=T0, **kw):
    return SP.classify_sector_evidence(sector=sector, source=source, asof=asof, t0=t0, provenance=provenance, available=available, **kw)


# ------------------------------------------------------------------ the freshness window
def test_the_window_is_the_one_the_capture_itself_applies():
    assert SP.SECTOR_MAX_AGE_DAYS == REPO.SECTOR_WINDOW_DAYS == 30


@pytest.mark.parametrize("age,state", [(0, SP.OBSERVED_FRESH), (1, SP.OBSERVED_FRESH), (29, SP.OBSERVED_FRESH), (30, SP.OBSERVED_FRESH),
                                       (31, SP.OBSERVED_STALE), (45, SP.OBSERVED_STALE), (400, SP.OBSERVED_STALE)])
def test_age_boundary_is_inclusive_at_thirty_days(age, state):
    e = ev(asof=T0 - timedelta(days=age))
    assert e.state == state and e.age_days == age and e.sector == "Technology"
    assert e.fresh is (state == SP.OBSERVED_FRESH)


def test_a_stale_sector_keeps_its_name_but_is_never_fresh():
    e = ev(asof=T0 - timedelta(days=31))
    assert e.state == SP.OBSERVED_STALE and e.reason == SP.STALE and e.sector == "Technology" and not e.fresh


def test_a_custom_window_is_honoured_and_the_default_is_not_widened():
    assert ev(asof=T0 - timedelta(days=10), max_age_days=5).state == SP.OBSERVED_STALE
    assert ev(asof=T0 - timedelta(days=31)).state == SP.OBSERVED_STALE


# ------------------------------------------------------------------ the other four evidence classes
def test_a_source_row_dated_after_the_decision_is_unavailable_not_fresh():
    e = ev(asof=T0 + timedelta(days=1))
    assert (e.state, e.reason, e.age_days) == (SP.UNAVAILABLE, SP.ASOF_AFTER_T0, -1) and not e.fresh


def test_a_sector_stamped_after_the_decision_deadline_is_unavailable():
    e = ev(available=False)
    assert (e.state, e.reason) == (SP.UNAVAILABLE, SP.NAME_LATE) and not e.fresh


@pytest.mark.parametrize("raw", [None, "", "   ", "Unknown", "unknown", " UNKNOWN "])
def test_no_sector_blank_and_the_legacy_placeholder_are_all_unavailable(raw):
    e = ev(sector=raw)
    assert (e.state, e.reason, e.sector) == (SP.UNAVAILABLE, SP.NO_SECTOR, None)


def test_a_reconstructed_sector_is_never_fresh_however_recent():
    e = ev(provenance="reconstructed")
    assert e.state == SP.RECONSTRUCTED and not e.fresh and e.sector == "Technology"


@pytest.mark.parametrize("src", [None, "", "  "])
def test_an_observed_sector_without_a_source_is_unknown_provenance(src):
    e = ev(source=src)
    assert (e.state, e.reason) == (SP.UNKNOWN, SP.SOURCE_MISSING) and not e.fresh


def test_an_observed_sector_without_a_source_date_is_unknown_provenance():
    e = ev(asof=None)
    assert (e.state, e.reason) == (SP.UNKNOWN, SP.ASOF_MISSING) and not e.fresh


@pytest.mark.parametrize("prov", [None, "", "guess", "OBSERVED", "projected"])
def test_an_unrecognised_provenance_fails_closed_even_with_no_sector(prov):
    assert ev(provenance=prov).state == SP.UNKNOWN
    assert ev(provenance=prov, sector=None).state == SP.UNKNOWN          # never a quiet NULL


def test_every_evidence_state_is_reachable_and_only_one_is_fresh():
    seen = {ev().state, ev(asof=T0 - timedelta(days=31)).state, ev(provenance="reconstructed").state, ev(sector=None).state, ev(source=None).state}
    assert seen == set(SP.EVIDENCE_STATES)
    assert [s for s in SP.EVIDENCE_STATES if SP.SectorEvidence(s, "x", "A", 0).fresh] == [SP.OBSERVED_FRESH]


def test_a_sector_cannot_become_fresh_by_a_later_row():
    # the classification is a pure function of (sector, source, asof, t0): what is known at an earlier t0 does not depend on a later row
    earlier = ev(asof=T0 - timedelta(days=40), t0=T0 - timedelta(days=1))
    later = ev(asof=T0, t0=T0)
    assert earlier.state == SP.OBSERVED_STALE and later.state == SP.OBSERVED_FRESH
    assert ev(asof=T0, t0=T0 - timedelta(days=1)).state == SP.UNAVAILABLE      # a sector first sourced at T is not known at T-1


# ------------------------------------------------------------------ candidate-level name state
@pytest.mark.parametrize("kw,state", [
    ({}, C.OK),
    ({"asof": T0 - timedelta(days=31)}, C.SECTOR_STALE),
    ({"sector": None}, C.NO_SECTOR),
    ({"sector": "Unknown"}, C.NO_SECTOR),
    ({"available": False}, C.SECTOR_NAME_LATE),
    ({"asof": T0 + timedelta(days=2)}, C.SECTOR_ASOF_AFTER_T0),
    ({"source": None}, C.SECTOR_UNKNOWN_PROVENANCE),
    ({"asof": None}, C.SECTOR_UNKNOWN_PROVENANCE),
])
def test_candidate_sector_name_state(kw, state):
    assert C.sector_name_state(ev(**kw)) == state


def test_a_reconstructed_evidence_has_no_candidate_name_state():
    with pytest.raises(C.LabError):
        C.sector_name_state(ev(provenance="reconstructed"))


def test_candidate_sector_evidence_reads_the_snapshot_columns_and_the_availability_test():
    cutoff = datetime(2026, 12, 1, tzinfo=timezone.utc)
    cap = datetime(2026, 3, 10, 21, 0, tzinfo=timezone.utc)
    c = {"fs_sector": "Energy", "fs_sector_source": SRC, "fs_sector_asof": T0 - timedelta(days=3), "fs_captured_at": cap}
    got = C.candidate_sector_evidence(c, T0, lambda a: C.is_known(a, T0, 1, cutoff))
    assert got.state == SP.OBSERVED_FRESH and got.sector == "Energy" and got.age_days == 3
    late = dict(c, fs_captured_at=datetime(2026, 3, 12, 0, 0, tzinfo=timezone.utc))
    assert C.candidate_sector_evidence(late, T0, lambda a: C.is_known(a, T0, 1, cutoff)).reason == SP.NAME_LATE


# ------------------------------------------------------------------ the five states stay distinct (Option B)
FRESH_TECH = ev()


def cell(**over):
    kw = dict(provenance="observed", rs_sector="Technology", value=1.5, pit_safe=True, cand=FRESH_TECH)
    kw.update(over)
    return C.relative_sector_cell(**kw)


def test_state_1_a_pit_safe_sector_with_a_value_is_the_only_observed_measurement():
    assert cell() == (C.OK, True, "Technology")


def test_state_2_no_sector_is_null_with_an_explicit_state_and_is_not_a_zero():
    assert cell(rs_sector=None, value=None, pit_safe=False, cand=ev(sector=None)) == (C.NO_SECTOR, False, None)
    assert cell(rs_sector="Unknown", value=None, pit_safe=False, cand=ev(sector=None))[0] == C.NO_SECTOR
    state, keep, _ = cell(rs_sector=None, value=None, pit_safe=False, cand=ev(sector=None))
    assert state != C.OK and keep is False                                # the value is dropped: it can never be read back as 0


def test_state_2_is_distinct_from_a_real_zero():
    assert cell(value=0.0) == (C.OK, True, "Technology")                  # a genuine 0.0 pp is an observed value
    assert cell(rs_sector=None, value=None, pit_safe=False, cand=ev(sector=None))[0] != cell(value=0.0)[0]


def test_state_3_reconstructed_information_never_becomes_ok():
    assert cell(provenance="reconstructed")[0] == C.SECTOR_RECONSTRUCTED and cell(provenance="reconstructed")[1] is True    # kept: readiness blocks
    assert cell(cand=ev(provenance="reconstructed")) == (C.SECTOR_RECONSTRUCTED, False, None)


def test_state_4_unknown_provenance_is_its_own_state():
    assert cell(cand=ev(source=None)) == (C.SECTOR_UNKNOWN_PROVENANCE, False, None)
    assert cell(cand=ev(asof=None))[0] == C.SECTOR_UNKNOWN_PROVENANCE


def test_state_5_a_missing_source_observation_is_not_a_sector_state():
    # a missing RS row never reaches this function: the RS cell itself is 'absent' and the sector-relative cell mirrors it
    for bad in (C.ABSENT, C.LATE, C.UNAVAILABLE, C.RECONSTRUCTED_EXCLUDED):
        assert bad not in C.SECTOR_REL_STATES


def test_a_stale_sector_masks_the_value():
    assert cell(cand=ev(asof=T0 - timedelta(days=31))) == (C.SECTOR_STALE, False, None)


def test_a_candidate_with_no_usable_sector_masks_a_stored_value_even_when_the_rs_row_calls_itself_pit_safe():
    assert cell(cand=ev(sector=None)) == (C.SECTOR_UNCONFIRMED, False, None)
    assert cell(cand=ev(available=False)) == (C.SECTOR_UNCONFIRMED, False, None)
    assert cell(cand=ev(asof=T0 + timedelta(days=1))) == (C.SECTOR_UNCONFIRMED, False, None)


def test_a_sector_identity_conflict_masks_the_value():
    assert cell(cand=ev(sector="Energy")) == (C.SECTOR_IDENTITY_CONFLICT, False, None)


def test_a_value_the_rs_row_itself_says_is_not_pit_safe_is_kept_so_readiness_blocks_it():
    assert cell(pit_safe=False) == (C.UNSAFE_VALUE, True, "Technology")
    assert cell(pit_safe=None)[0] == C.UNSAFE_VALUE
    assert cell(pit_safe=False, value=None) == (C.SECTOR_NOT_PIT_SAFE, False, None)


def test_a_value_with_no_sector_at_all_is_kept_so_readiness_blocks_it():
    assert cell(rs_sector=None, pit_safe=False, cand=ev(sector=None)) == (C.UNSAFE_VALUE, True, None)


def test_a_fresh_sector_whose_model_produced_no_value_is_distinct_from_no_sector():
    assert cell(value=None) == (C.SECTOR_VALUE_UNAVAILABLE, True, "Technology")


# ------------------------------------------------------------------ the exhaustive sweep (evidence for "can unsafe information pass?")
def readiness_row(state, keep, exposed, prov, value, pit_safe):
    return {"rs__state": C.OK, "rs__provenance": prov, "rs_vs_sector__state": state, "rs_vs_sector_pp": value if keep else None,
            "rs_sector": exposed, "rs_sector_pit_safe": pit_safe}


def all_candidate_evidence():
    out = []
    for sector, source, asof, prov, avail in itertools.product(
            ("Technology", "Energy", None, "Unknown"), (SRC, None), (T0, T0 - timedelta(days=30), T0 - timedelta(days=31), T0 + timedelta(days=1), None),
            ("observed", "reconstructed", None), (True, False)):
        out.append(ev(sector=sector, source=source, asof=asof, provenance=prov, available=avail))
    return out


def test_exhaustive_sweep_a_non_null_sector_relative_value_passes_readiness_only_when_fully_proven():
    evidences = all_candidate_evidence()
    assert len({(e.state, e.reason) for e in evidences}) >= 9          # the sweep really covers every evidence class
    n = passed = 0
    for prov, rs_sector, value, pit_safe, cand in itertools.product(("observed", "reconstructed", None, "guess"), (None, "", "Unknown", "Technology", "Energy"),
                                                                     (None, 1.5, 0.0), (True, False, None), evidences):
        state, keep, exposed = C.relative_sector_cell(provenance=prov, rs_sector=rs_sector, value=value, pit_safe=pit_safe, cand=cand)
        out = value if keep else None
        unsafe = RY.sector_relative_unsafe(readiness_row(state, keep, exposed, prov, value, pit_safe))
        n += 1
        fully_proven = (prov == "observed" and SP.clean_sector(rs_sector) is not None and pit_safe is True and cand.state == SP.OBSERVED_FRESH
                        and cand.sector == SP.clean_sector(rs_sector))
        if out is not None and not unsafe:
            passed += 1
            assert fully_proven and state == C.OK, (prov, rs_sector, value, pit_safe, cand, state)
        if value is not None and fully_proven:
            assert state == C.OK and out == value and not unsafe
        if out is None:
            assert state != C.OK                                        # a NULL value is never labelled as an observed measurement
            assert not unsafe                                          # and a NULL value can never fail readiness
        if out is not None and not fully_proven:
            assert unsafe and state in (C.UNSAFE_VALUE, C.SECTOR_RECONSTRUCTED)      # kept, labelled, and blocked by readiness
    assert n > 10000 and passed > 0


def test_exhaustive_sweep_nothing_unsafe_is_ever_silently_masked_to_null_without_a_distinct_state():
    for prov, rs_sector, value, pit_safe, cand in itertools.product(("observed", "reconstructed"), (None, "Technology"), (1.5,), (True, False, None),
                                                                     all_candidate_evidence()):
        state, keep, _ = C.relative_sector_cell(provenance=prov, rs_sector=rs_sector, value=value, pit_safe=pit_safe, cand=cand)
        if not keep:
            assert state in (C.SECTOR_UNKNOWN_PROVENANCE, C.SECTOR_RECONSTRUCTED, C.SECTOR_UNCONFIRMED, C.SECTOR_STALE, C.SECTOR_IDENTITY_CONFLICT)


# ------------------------------------------------------------------ dataset identity
V1_SCHEMA_HASH = "84dd207c76bd951f61b6e8a0edf7f315a9ce9e4d3694a87fb7f9e614c0d527e1"
V2_SCHEMA_HASH = "0e919310ac520442c6efbb484450ffdc8f860b5c128e4d417f0b928ece945ed1"


def test_the_frozen_v1_contract_identity_is_pinned_and_reproducible():
    assert C.LEGACY_DATASET_SCHEMAS == ("lab_dataset_v1",)
    assert C.schema_hash("lab_dataset_v1") == V1_SCHEMA_HASH                # the value committed in the Slice 4-7 history
    assert C.legacy_schema_of(V1_SCHEMA_HASH) == "lab_dataset_v1"


def test_the_v2_contract_identity_is_pinned_and_different():
    assert C.DATASET_SCHEMA == "lab_dataset_v2" and C.schema_hash() == V2_SCHEMA_HASH != V1_SCHEMA_HASH
    assert C.legacy_schema_of(V2_SCHEMA_HASH) is None and C.legacy_schema_of(None) is None and C.legacy_schema_of("0" * 64) is None


def test_v2_adds_exactly_two_columns_and_removes_none():
    v1 = [n for n, _ in C._columns_of("lab_dataset_v1")]
    v2 = list(C.COLUMN_NAMES)
    assert set(v1) < set(v2) and [n for n in v2 if n not in v1] == ["rs_sector", "rs_vs_sector__state"] == list(C.V2_ADDED_COLUMNS)
    assert [n for n in v2 if n in v1] == v1                                   # the shared columns keep their order


def test_a_v1_projection_drops_only_the_v2_columns():
    sample = {"str": "x", "int": 1, "float": 1.5, "bool": True, "date": date(2026, 1, 2), "ts": datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)}
    row = {n: sample[t] for n, t in C.COLUMNS}
    v2, v1 = C.encode_row(row), C.encode_row(row, "lab_dataset_v1")
    keep = [i for i, n in enumerate(C.COLUMN_NAMES) if n not in C.V2_ADDED_COLUMNS]
    assert len(v2) - len(v1) == 2 and [v2[i] for i in keep] == v1
    with pytest.raises(C.LabError):
        C.encode_row({k: v for k, v in row.items() if k != "rs_sector"})        # a row must always be built with the current columns


def test_v1_and_v2_headers_differ_so_the_two_datasets_can_never_be_confused():
    doc = {"label_version": "l", "label_methodology_version": "m", "feature_versions": {}}
    h1, h2 = C.dataset_header("a" * 64, doc, 0, "lab_dataset_v1"), C.dataset_header("a" * 64, doc, 0)
    assert h1["schema"] != h2["schema"] and len(h2["columns"]) - len(h1["columns"]) == 2
    assert hashlib.sha256(json.dumps(h1, sort_keys=True).encode()).hexdigest() != hashlib.sha256(json.dumps(h2, sort_keys=True).encode()).hexdigest()


def test_the_db_query_identities_are_unchanged_by_the_contract_bump():
    # Slice 8 changes how stored rows are INTERPRETED, not what is read: the query versions (hence every DB fingerprint) of Slices 4-7 stay valid.
    assert C.QUERY_VERSIONS == {"candidates": "q1", "labels": "q1", "market": "q1", "sector": "q1", "stock_rs": "q1", "events": "q1",
                                "classifications": "q1", "first_seen": "q1"}


def test_the_sector_module_is_pure_and_never_touches_the_market_intelligence_package():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(SP))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not {m for m in mods if m and ("market_intelligence" in m or "psycopg" in m or "sqlalchemy" in m)}
    text = inspect.getsource(SP).lower()
    for banned in ("datetime.now", "date.today", "os.environ", "open(", "requests"):
        assert banned not in text
