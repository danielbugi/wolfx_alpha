"""The application layer must not be able to reach the maintenance hatch or mutate the immutable tables.
Static (no database): the hatch is for a human with an approved ticket, never for application code."""
import os
import re

from conftest import ROOT

RESEARCH = os.path.join(ROOT, "mechanism", "research")
APP_MODULES = ["registry.py", "snapshot_builder.py", "repository.py", "observer.py"]
IMMUTABLE = ("candidate_observation", "feature_snapshot", "feature_set_registry")


def source(name):
    with open(os.path.join(RESEARCH, name), encoding="utf-8") as fh:
        return fh.read()


def test_no_application_module_touches_the_maintenance_objects():
    for name in APP_MODULES:
        text = source(name)
        for forbidden in ("research_maintenance_log", "research_maintenance_audit", "maintenance_ticket"):
            assert forbidden not in text.replace("\n", " ") or name == "x", f"{name} mentions {forbidden}"


def test_no_module_updates_deletes_or_truncates_an_immutable_table():
    pattern = re.compile(r"\b(UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+(" + "|".join(IMMUTABLE) + r")\b", re.I)
    for name in APP_MODULES:
        assert not pattern.search(source(name)), name


def test_the_only_update_in_the_application_layer_is_the_capture_run_counters():
    updates = [m for name in APP_MODULES for m in re.findall(r"\bUPDATE\s+(\w+)", source(name), re.I)]
    assert updates == ["candidate_capture_run"]


def test_the_screener_and_ledger_writer_do_not_reference_the_hatch():
    for rel in ("screeners/multi_timeframe_screener.py", "screeners/signal_ledger_writer.py"):
        with open(os.path.join(ROOT, "mechanism", rel), encoding="utf-8") as fh:
            text = fh.read()
        assert "research_maintenance" not in text and "maintenance_ticket" not in text, rel
