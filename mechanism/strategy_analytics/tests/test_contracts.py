"""Strategy-rule contracts c1/c2 (strategy_analytics/contracts.py): immutability, drift against the live code, evidence
integrity, and historical assignment with certainty. Pure: no database."""
import hashlib
import os
import re
import sys
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))

from strategy_analytics import contracts as C  # noqa: E402
from strategy_analytics import performance as P  # noqa: E402

# An in-place edit of a contract must fail here. A real rule change is a NEW contract_version with a later
# effective_from_session (and its own pinned hash) -- never an edit of c1 or c2.
PINNED = {
    "c1": "6b5d9b56bef112d8b070cca74d1a131c2454018ec7fd3b9f999e0ceccd208885",
    "c2": "aff17c826c0cc26cd33aa0874a063e7eca2557072c6a16ec4c3af7a6c9a2b1a7",
}


def _c(v):
    return next(c for c in C.CONTRACTS if c["contract_version"] == v)


def test_contracts_are_immutable_by_hash():
    assert {c["contract_version"]: C.contract_hash(c) for c in C.CONTRACTS} == PINNED


def test_hash_is_canonical_and_sensitive_to_any_change():
    c2 = _c("c2")
    shuffled = dict(reversed(list(c2.items())))
    assert C.contract_hash(shuffled) == C.contract_hash(c2)
    changed = {**c2, "expiry": {**c2["expiry"], "bars": 21}}
    assert C.contract_hash(changed) != C.contract_hash(c2)


def test_versions_form_one_ordered_supersedes_chain():
    cs = C.contracts_for("donchian_breakout", "v1")
    assert [c["contract_version"] for c in cs] == ["c1", "c2"]
    assert [c["supersedes"] for c in cs] == [None, "c1"]
    eff = [date.fromisoformat(c["effective_from_session"]) for c in cs]
    assert eff == sorted(eff) and len(set(eff)) == len(eff)


# ---------------------------------------------------------------------------------------- drift vs the running code
def test_the_latest_contract_matches_the_trade_plan_and_the_declared_exit_rules():
    from shared import trade_plan as TP
    c2 = _c("c2")
    assert c2["risk"]["initial_stop"]["atr_mult"] == TP.STOP_ATR_MULT
    assert tuple(c2["targets"]["atr_mults"]) == tuple(TP.TARGET_ATR_MULTS)
    assert c2["expiry"]["bars"] == TP.PLAN_HORIZON_BARS
    r = P.EXIT_RULES[("donchian_breakout", "v1")]
    assert (r["stop_atr_mult"], tuple(r["target_atr_mults"]), r["expiry_bars"]) == (2.0, (2.0, 4.0, 6.0), 20)


def test_the_guards_boundary_is_the_recorded_setting():
    from screeners import guards_boundary as GB
    c2 = _c("c2")
    name, value = c2["universe"]["boundary_setting"].split("=")
    assert name == GB.ENV_VAR and value == c2["effective_from_session"]
    evidence = open(os.path.join(ROOT, C.EVIDENCE_DIR, "config_evidence.txt"), encoding="utf-8").read()
    assert f"| {c2['universe']['boundary_setting']}" in evidence


def test_the_evaluator_still_has_the_contracts_resolution_semantics():
    src = open(os.path.join(ROOT, "mechanism", "screeners", "evaluate_signal_ledger.py"), encoding="utf-8").read()
    assert 'SAME_BAR_STOP_AND_TARGET = "same_bar_stop_and_target"' in src
    assert "from shared.trade_plan import PLAN_HORIZON_BARS" in src
    assert 'Resolution("stopped", -1.0' in src                      # stop first, at -1R (no gap model)


def test_evidence_files_match_their_recorded_sha256():
    d = os.path.join(ROOT, C.EVIDENCE_DIR)
    readme = open(os.path.join(d, "README.md"), encoding="utf-8").read()
    lines = re.findall(r"^([0-9a-f]{64}) \*?(\S+)$", readme, re.M)
    assert len(lines) == 3
    for digest, name in lines:
        with open(os.path.join(d, name), "rb") as fh:
            data = fh.read().replace(b"\r\n", b"\n")              # hashes are over the committed LF content
        assert hashlib.sha256(data).hexdigest() == digest, name


# ------------------------------------------------------------------------------------------- historical assignment
def test_assignment_by_session_with_certainty():
    cases = {date(2026, 9, 27): None, date(2026, 9, 28): ("c1", "high"), date(2026, 10, 1): ("c1", "high"),
             date(2026, 10, 2): ("c2", "high"), date(2026, 10, 6): ("c2", "high"),
             date(2026, 10, 7): ("c2", "medium_high"), date(2026, 10, 8): ("c2", "medium_high"),
             date(2026, 10, 9): ("c2", "high"), date(2026, 12, 31): ("c2", "high")}
    for d, want in cases.items():
        a = C.assignment("donchian_breakout", "v1", d)
        if want is None:
            assert a["state"] == "not_available"
        else:
            assert (a["contract_version"], a["certainty"]) == want, d
            assert a["contract_hash"] == PINNED[want[0]] and a["basis"] and a["evidence"] == C.EVIDENCE_DIR


def test_cohort_ranges_are_contiguous_and_inside_their_contract():
    for c in C.CONTRACTS:
        ranges = c["assignment"]
        assert date.fromisoformat(ranges[0]["from"]) == date.fromisoformat(c["effective_from_session"])
        for a, b in zip(ranges, ranges[1:]):
            assert (date.fromisoformat(b["from"]) - date.fromisoformat(a["to"])).days >= 1


def test_unknown_strategy_has_no_contract_and_nothing_is_guessed():
    assert C.public_view("other", "v1")["state"] == "not_available"
    assert C.assignment("donchian_breakout", "v2", date(2026, 10, 10))["state"] == "not_available"


def test_the_registry_never_touches_a_database():
    src = open(os.path.join(ROOT, "mechanism", "strategy_analytics", "contracts.py"), encoding="utf-8").read()
    assert not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|psycopg2|execute)\b", src)
