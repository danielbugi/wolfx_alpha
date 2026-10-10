"""Pure tests of manifest authoring and the portable manifest file (no database)."""
import copy
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import research.lab.dataset_authoring as AU
import research.lab.dataset_contract as C
from cli_world import spec_doc
from dataset_world import CAL, CUTOFF, HORIZONS, MAT, SHA, config, make_spec
from lab_samples import NOW
from research.lab import manifest as M
from research.lab.manifest import LabError


def problems(obj):
    with pytest.raises(LabError) as e:
        AU.parse_authoring(obj)
    return e.value.problems


def test_a_complete_spec_parses_into_exactly_what_was_written():
    a = AU.parse_authoring(spec_doc())
    assert a.dataset_name == "lab_ds" and a.label_horizons == (5, 20, 60) and a.knowledge_cutoff_at == CUTOFF
    assert a.label_maturity_session == MAT and a.calendar_file == "calendar.txt" and a.calendar_derive is None and a.universe_from_db
    assert a.windows.train[0].isoformat() == spec_doc()["windows"]["train"][0] and "universe_members" not in a.config


def test_every_problem_is_reported_at_once_and_nothing_is_defaulted():
    doc = spec_doc()
    for k in ("dataset_name", "windows", "knowledge_cutoff_at", "calendar"):
        del doc[k]
    got = problems(doc)
    for k in ("dataset_name", "windows", "knowledge_cutoff_at", "calendar"):
        assert f"missing key '{k}'" in got


@pytest.mark.parametrize("key", ["code_sha", "input_hashes", "created_at", "manifest_hash", "anything_else"])
def test_an_unknown_key_is_refused_so_identity_cannot_be_written_by_hand(key):
    assert any(f"unknown key '{key}'" in p for p in problems(dict(spec_doc(), **{key: "x"})))


@pytest.mark.parametrize("over,needle", [
    ({"schema": "lab_authoring_spec_v0"}, "schema must be"),
    ({"dataset_name": ""}, "dataset_name must be a non-empty string"),
    ({"label_horizons": []}, "label_horizons"),
    ({"label_horizons": [5, "20"]}, "label_horizons"),
    ({"label_horizons": [5, True]}, "label_horizons"),
    ({"feature_versions": {}}, "feature_versions"),
    ({"embargo_sessions": -1}, "embargo_sessions"),
    ({"purge_sessions": 1.5}, "purge_sessions"),
    ({"knowledge_cutoff_at": "2026-10-02T21:00:00"}, "UTC offset"),
    ({"knowledge_cutoff_at": 5}, "UTC offset"),
    ({"label_maturity_session": "02/10/2026"}, "ISO date"),
    ({"windows": {"train": ["2023-01-02", "2023-02-02"]}}, "windows must have exactly the keys"),
    ({"windows": {"train": ["2023-01-02"], "validation": ["2023-01-02", "2023-02-02"], "test": ["2023-01-02", "2023-02-02"]}}, "must be [start, end]"),
    ({"calendar": {"file": ""}}, "calendar must be"),
    ({"calendar": {"file": "c.txt", "derive": {}}}, "calendar must be"),
    ({"calendar": {"derive": {"start": "2023-01-02"}}}, "calendar.derive needs start and end"),
    ({"calendar": {"derive": {"start": "2023-01-02", "end": "2023-02-02", "x": 1}}}, "calendar.derive needs start and end"),
    ({"universe": {"from_db": False}}, "universe must be"),
    ({"universe": {"from_db": True, "from_config": True}}, "universe must be"),
    ({"config": []}, "config must be an object"),
])
def test_malformed_values_are_refused_with_a_specific_message(over, needle):
    assert any(needle in p for p in problems(spec_doc(**over))), over


def test_the_universe_source_and_config_members_must_agree():
    cfg = config()
    assert any("must be omitted" in p for p in problems(spec_doc(config=cfg)))                       # from_db + explicit members
    cfg_no = copy.deepcopy(cfg)
    del cfg_no["universe_members"]
    assert any("is required" in p for p in problems(spec_doc(universe={"from_config": True}, config=cfg_no)))
    ok = AU.parse_authoring(spec_doc(universe={"from_config": True}, config=cfg))
    assert not ok.universe_from_db and ok.config["universe_members"] == list(cfg["universe_members"])


def test_a_derived_calendar_is_parsed_with_its_default_benchmark():
    a = AU.parse_authoring(spec_doc(calendar={"derive": {"start": "2023-01-02", "end": "2023-12-29"}}))
    assert a.calendar_file is None and a.calendar_derive == (date(2023, 1, 2), date(2023, 12, 29), "^GSPC")
    b = AU.parse_authoring(spec_doc(calendar={"derive": {"start": "2023-01-02", "end": "2023-12-29", "benchmark_symbol": "SPY"}}))
    assert b.calendar_derive[2] == "SPY"


def test_a_non_object_spec_is_refused():
    for bad in (None, [], "x", 3):
        assert problems(bad) == ["the authoring spec must be a JSON object"]


def test_a_cutoff_with_another_offset_is_normalised_to_utc():
    a = AU.parse_authoring(spec_doc(knowledge_cutoff_at=CUTOFF.astimezone(timezone(timedelta(hours=3))).isoformat()))
    assert a.knowledge_cutoff_at == CUTOFF and a.knowledge_cutoff_at.utcoffset() == timedelta(0)


# ------------------------------------------------------------------ calendar text
def test_calendar_text_ignores_comments_and_blank_lines_but_reports_bad_lines_together():
    assert AU.parse_calendar_text("# c\n\n2023-01-02  # monday\n2023-01-03\n") == (date(2023, 1, 2), date(2023, 1, 3))
    with pytest.raises(LabError) as e:
        AU.parse_calendar_text("2023-01-02\nnope\n2023-13-01\n")
    assert len(e.value.problems) == 2 and "line 2" in e.value.problems[0] and "line 3" in e.value.problems[1]
    with pytest.raises(LabError):
        AU.parse_calendar_text("# nothing\n\n")


# ------------------------------------------------------------------ spec -> manifest
def authored(**over):
    a = AU.parse_authoring(spec_doc(**over))
    members = ["C", "A", "B", "A"]
    return a, M.build_manifest(AU.to_spec(a, code_sha=SHA, code_tree_clean=True, calendar=CAL, calendar_source="explicit_calendar_file",
                                         universe_members=members, input_hashes={"labels": "b" * 64}), now=NOW)


def test_the_universe_from_the_database_is_sorted_and_deduplicated_into_the_manifest():
    _, m = authored()
    assert m.document["config"]["universe_members"] == ["A", "B", "C"]
    assert m.document["universe_hash"] == M.universe_hash(["A", "B", "C"], "rule")


def test_an_empty_observed_universe_is_refused():
    a = AU.parse_authoring(spec_doc())
    with pytest.raises(LabError) as e:
        AU.to_spec(a, code_sha=SHA, code_tree_clean=True, calendar=CAL, calendar_source="s", universe_members=[], input_hashes={})
    assert "universe would be empty" in e.value.problems[0]


def test_a_spec_built_from_the_same_inputs_gives_the_same_manifest_hash():
    assert authored()[1].manifest_hash == authored()[1].manifest_hash
    assert authored(dataset_version="v2")[1].manifest_hash != authored()[1].manifest_hash


# ------------------------------------------------------------------ the manifest file
def test_a_manifest_file_round_trips_through_json_to_the_same_manifest():
    _, m = authored()
    text = AU.dump_json(AU.make_manifest_file(m, NOW))
    back, at = AU.parse_manifest_file(json.loads(text))
    assert back.manifest_hash == m.manifest_hash and back.calendar == m.calendar and at == NOW.astimezone(timezone.utc)
    assert AU.dump_json(AU.make_manifest_file(back, at)) == text


def test_a_manifest_file_is_refused_if_edited_truncated_or_incomplete():
    _, m = authored()
    base = AU.make_manifest_file(m, NOW)

    def refused(doc):
        with pytest.raises(LabError):
            AU.parse_manifest_file(doc)

    bad = copy.deepcopy(base)
    bad["manifest"]["code_sha"] = "e" * 40
    refused(bad)                                                                # document edited under its hash
    bad = copy.deepcopy(base)
    bad["manifest"]["windows"]["train"][1] = CAL[250].isoformat()
    refused(bad)
    bad = copy.deepcopy(base)
    bad["calendar"] = bad["calendar"][:-1]
    refused(bad)                                                                # calendar truncated: its hash no longer matches
    bad = copy.deepcopy(base)
    bad["manifest_hash"] = "0" * 64
    refused(bad)
    for key in ("schema", "manifest_hash", "authored_at", "calendar", "manifest"):
        bad = copy.deepcopy(base)
        del bad[key]
        refused(bad)
    refused(dict(base, extra=1))
    refused(dict(base, schema="lab_manifest_file_v0"))
    refused(dict(base, authored_at="2026-10-02T21:00:00"))
    refused(dict(base, calendar="2023-01-02"))
    refused([])


def test_make_manifest_file_needs_an_aware_time():
    _, m = authored()
    with pytest.raises(LabError):
        AU.make_manifest_file(m, datetime(2026, 1, 1))


# ------------------------------------------------------------------ canonical JSON
def test_dump_json_is_deterministic_sorted_ascii_and_newline_terminated():
    a = {"b": 1, "a": [date(2023, 1, 2), datetime(2023, 1, 2, tzinfo=timezone.utc)], "z": "é"}
    s = AU.dump_json(a)
    assert s == AU.dump_json({"z": "é", "a": a["a"], "b": 1}) and s.endswith("\n") and "\r" not in s
    assert s.isascii() and s.index('"a"') < s.index('"b"') < s.index('"z"')
    assert json.loads(s)["a"][0] == "2023-01-02"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), datetime(2023, 1, 1)])
def test_dump_json_refuses_non_finite_numbers_and_naive_timestamps(bad):
    with pytest.raises(LabError):
        AU.dump_json({"x": bad})
