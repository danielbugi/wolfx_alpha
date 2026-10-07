"""The complete activation preflight, the parts that need no database: the architecture layer, the owner gates and the derived migration catalog."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import forward_world as FW
from research.lab import dataset_authoring as AUTH
from research.lab import research_status_reader as SR
from forward_collection import activation as A
from forward_collection import contract as C
from forward_collection import steps as S
from full_env import ROOT, weekdays


def spec(**over):
    w = SimpleNamespace(sessions=weekdays(8))
    return FW.status_spec_doc(w, train=(0, 1), validation=(2, 3), test=(4, 5), maturity=5,
                              cutoff=datetime(2026, 3, 20, 6, tzinfo=timezone.utc), **over)


def by_id(checks):
    return {c["id"]: c for c in checks}


def test_the_real_architecture_is_ready_and_the_sector_history_has_a_real_collector_dependency():
    checks = A.architecture_checks(spec(), env={})
    assert all(c["ok"] for c in checks if c["blocking"]), [c for c in checks if not c["ok"]]
    c = by_id(checks)
    assert c["sector_history_has_a_collector_dependency"]["detail"]["step"] == C.STEP_SECTOR_HISTORY
    assert c["scheduler_command_verifies_sector_history"]["ok"] and c["sector_writer_flag_reported"]["blocking"] is False


def test_a_dataset_that_enables_the_sector_history_source_is_no_longer_a_structural_blocker():
    sector = {"sector_history": {"source": "yfinance_info"}}
    en = SR.parse_cfg(AUTH.parse_authoring(spec(config_over=sector)), []).enabled()
    assert en["sector_history"] is True and C.spec_source_blockers(en) == []


@pytest.mark.parametrize("source", ["tiingo_meta", "unknown_vendor"])
def test_a_spec_cannot_make_a_non_authoritative_source_carry_sector_identity(source):
    from research.lab.manifest import LabError
    with pytest.raises(LabError) as e:
        SR.parse_cfg(AUTH.parse_authoring(spec(config_over={"sector_history": {"source": source}})), [])
    assert "authoritative" in str(e.value)


def test_removing_the_step_would_block_it_again(monkeypatch):
    monkeypatch.setitem(C.SOURCE_CONTRACT, "sector_history", {**C.SOURCE_CONTRACT["sector_history"], "step": None})
    sector = {"sector_history": {"source": "yfinance_info"}}
    checks = by_id(A.architecture_checks(spec(config_over=sector), env={}))
    assert not checks["dataset_sources_have_collectors"]["ok"] and not checks["sector_history_has_a_collector_dependency"]["ok"]


def test_a_step_missing_from_the_implemented_set_is_a_blocker(monkeypatch):
    real = S.make_steps
    monkeypatch.setattr(S, "make_steps", lambda **kw: {k: v for k, v in real(**kw).items() if k != C.STEP_SECTOR_HISTORY})
    c = by_id(A.architecture_checks(spec(), env={}))
    assert not c["sector_history_has_a_collector_dependency"]["ok"]


def test_a_scheduler_command_without_the_verification_flag_is_a_blocker(monkeypatch):
    monkeypatch.setitem(C.SCHEDULER_DESIGN, "command", C.SCHEDULER_DESIGN["command"].replace("--with-sector-history-check", ""))
    assert not by_id(A.architecture_checks(spec(), env={}))["scheduler_command_verifies_sector_history"]["ok"]


def test_the_catalog_is_derived_from_the_compose_list_and_the_sql_files_not_hand_kept():
    cat = A.migration_catalog()
    assert sorted(cat) == list(A.ACTIVATION_MIGRATIONS)
    assert {"sector_observation", "sector_poll", "sector_reconstruction"} == set(cat[31]["tables"])
    assert "research_sector_guard" in cat[31]["functions"]
    assert {"candidate_observation", "candidate_capture_run", "research_capture_activation"} <= set(cat[22]["tables"])
    for n in range(24, 32):
        assert cat[n]["tables"], n


def test_an_unreadable_repo_gives_no_catalog_and_a_no_not_a_crash(tmp_path):
    assert A.migration_catalog(tmp_path) == {}


def test_every_gate_defaults_to_no_and_only_an_explicit_yes_opens_it(tmp_path):
    none = A.gate_checks(None, tmp_path)
    assert len(none) == len(A.GATES) + 1 and not any(c["ok"] for c in none)
    some = by_id(A.gate_checks({"s11_passed": "yes", "backup_verified": "true", "model_version_fix_applied": " YES "}, tmp_path))
    assert some["gate:s11_passed"]["ok"] and some["gate:model_version_fix_applied"]["ok"]
    assert not some["gate:backup_verified"]["ok"] and not some["gate:owner_activation_approved"]["ok"]


def test_the_scheduler_units_gate_needs_both_a_committed_service_and_timer(tmp_path):
    vps = tmp_path / "deploy" / "vps"
    vps.mkdir(parents=True)
    (vps / "donchian-forward-collection.service").write_text("[Service]\n")
    assert not by_id(A.gate_checks({}, tmp_path))["gate:scheduler_units_committed"]["ok"]
    (vps / "donchian-forward-collection.timer").write_text("[Timer]\n")
    assert by_id(A.gate_checks({}, tmp_path))["gate:scheduler_units_committed"]["ok"]


def test_the_real_repo_has_the_committed_dormant_units_but_every_owner_gate_still_defaults_to_no():
    c = by_id(A.gate_checks({}, A.REPO_ROOT))
    assert c["gate:scheduler_units_committed"]["ok"] and not any(c[f"gate:{k}"]["ok"] for k in A.GATES)
    c = by_id(A.gate_checks({k: "yes" for k in A.GATES}, A.REPO_ROOT))
    assert c["gate:scheduler_units_committed"]["ok"] and all(c[f"gate:{k}"]["ok"] for k in A.GATES)


def test_the_gate_list_names_the_s11_and_model_version_blockers():
    assert {"s11_passed", "model_version_fix_applied", "owner_activation_approved", "backup_verified"} <= set(A.GATES)


def test_the_module_never_writes_and_never_names_a_history_table():
    import re
    text = open(A.__file__, encoding="utf-8").read()
    code = re.sub(r'"""[\s\S]*?"""', "", text)
    assert not re.search(r"\b(INSERT\s+INTO|DELETE\s+FROM|UPDATE\s+\w+\s+SET|DROP\s+\w+|ALTER\s+\w+|GRANT\s+\w+|CREATE\s+(TABLE|ROLE|OR|INDEX))\b", code), \
        "the preflight must only read"
    assert ROOT and not re.search(r"sector_(observation|poll|reconstruction)", text)


@pytest.mark.parametrize("name", sorted(A.GATES))
def test_every_gate_has_a_human_meaning(name):
    assert len(A.GATES[name]) > 30


def test_the_real_request_sites_use_the_one_vendor_symbol_translation():
    c = by_id(A.architecture_checks(spec(), env={}))["sector_vendor_requests_use_the_translation"]
    assert c["ok"] and c["blocking"] and c["detail"]["problems"] == [] and c["detail"]["evidenced"]["BRK.B"] == "BRK-B"


def test_a_code_root_without_the_translated_request_sites_is_a_blocker(tmp_path):
    ok, detail = A.vendor_symbol_check(tmp_path)
    assert not ok and detail["sites"] == [] and detail["problems"]


def test_a_naive_dot_to_dash_replacement_is_a_blocker(monkeypatch):
    from data_updaters import vendor_symbols as V
    real = V.to_yfinance
    monkeypatch.setattr(V, "to_yfinance", lambda s: V.VendorSymbol(s, s.replace(".", "-").replace("/", "-"), V.TRANSLATED, "naive"))
    ok, detail = A.vendor_symbol_check()
    assert not ok and any("was not refused" in p for p in detail["problems"])
    monkeypatch.setattr(V, "to_yfinance", real)
    assert A.vendor_symbol_check()[0]


def test_a_translation_that_does_not_reproduce_the_evidenced_forms_is_a_blocker(monkeypatch):
    from data_updaters import vendor_symbols as V
    monkeypatch.setattr(V, "to_yfinance", lambda s: V.VendorSymbol(s, s, V.UNCHANGED, "identity"))
    ok, detail = A.vendor_symbol_check()
    assert not ok and any("BRK.B" in p for p in detail["problems"])


def test_a_request_site_that_bypasses_the_translation_is_a_blocker(tmp_path):
    d = tmp_path / "data_updaters"
    d.mkdir()
    (d / "fundamentals_updater.py").write_text("request_symbol = vendor_symbols.to_yfinance(s).request\nyf.Ticker(request_symbol)\n")
    (d / "other_probe.py").write_text("x = vendor_symbols.to_yfinance(s)\nyf.Ticker(symbol)\n")
    ok, detail = A.vendor_symbol_check(tmp_path)
    assert not ok and detail["sites"] == ["fundamentals_updater.py", "other_probe.py"]
    assert any("other_probe.py" in p for p in detail["problems"])
