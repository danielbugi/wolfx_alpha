"""Slice 10 (no database): the append-only sector history's pure core -- the recorder's classification of a vendor answer, the chain verifier, the
point-in-time selector and the dual-source cross-check.

The temporal semantics are pinned here on plain rows; `test_sector_history_temporal_db.py` proves the same through a real database, the real reader
and the real assembler."""
import dataclasses
from datetime import date, datetime, timedelta, timezone

import pytest

from data_updaters import sector_history_recorder as R
from research.lab import sector_history as SH
from research.lab import sector_provenance as SP

SRC = "yfinance_info"
CUTOFF = datetime(2099, 1, 1, tzinfo=timezone.utc)
GRACE = 1
D0 = date(2026, 3, 2)


def at(d, hour=10):
    return datetime(d.year, d.month, d.day, hour, tzinfo=timezone.utc)


def obs(seq, sector, when, *, prev=None, kind=None, symbol="AAA", reason=None, raw=None, session=None):
    """One observation row as the reader returns it, with a REAL (mirror) hash."""
    kind = kind or ("first" if seq == 1 else "changed")
    reason = reason if sector is None else None
    if sector is None and reason is None:
        reason = "vendor_null"
    row = {"row_kind": SH.OBSERVATION, "symbol": symbol, "source": SRC, "seq": seq, "sector": sector, "sector_raw": raw if raw is not None else sector,
           "no_sector_reason": reason, "change_kind": kind, "stamp": when, "effective_session": session or when.date(), "source_asof": None,
           "provenance": SH.FORWARD_PROVENANCE, "raw_payload_hash": R.payload_hash(sector), "prev_value_hash": prev, "run_id": f"r{seq}"}
    row["value_hash"] = SH.row_hash(row)
    return row


def chain(*steps, symbol="AAA"):
    """steps = (sector, datetime). Builds a valid chain (a sector equal to its predecessor is NOT a step: use confirm())."""
    out, prev = [], None
    for i, (sector, when) in enumerate(steps, 1):
        kind = "first" if i == 1 else ("became_none" if sector is None else ("became_set" if out[-1]["sector"] is None else "changed"))
        row = obs(i, sector, when, prev=prev, kind=kind, symbol=symbol)
        out.append(row)
        prev = row["value_hash"]
    return out


def confirm(head, when):
    return {"row_kind": SH.CONFIRMATION, "symbol": head["symbol"], "source": head["source"], "seq": head["seq"], "sector": head["sector"],
            "sector_raw": None, "no_sector_reason": None, "change_kind": None, "stamp": when, "effective_session": when.date(), "source_asof": None,
            "provenance": None, "raw_payload_hash": None, "prev_value_hash": None, "value_hash": head["value_hash"], "run_id": "conf"}


def sel(o, c=(), t0=D0, **kw):
    return SH.select(o, list(c), t0=t0, grace_days=GRACE, cutoff=CUTOFF, **kw)


# ================================================================== the recorder's reading of a vendor answer
def yf(sector, n=6):
    return {"symbol": "AAA", "sector": sector, "a": 1, "b": 2, "c": 3, "d": 4, "e": 5, "f": 6}


def meta(qt, n=6):
    return {"quote_type": qt, "n_keys": n, "sector_key_present": False}


@pytest.mark.parametrize("qt", ["ETF", "MUTUALFUND"])
@pytest.mark.parametrize("raw,state,reason", [(None, "no_sector", "vendor_null"), ("", "no_sector", "vendor_blank"), ("   ", "no_sector", "vendor_blank"),
                                              ("Unknown", "no_sector", "vendor_unknown_label"), (" unknown ", "no_sector", "vendor_unknown_label")])
def test_yfinance_can_say_explicit_no_sector_only_with_non_operating_quote_type_evidence(raw, state, reason, qt):
    o = R.classify("yfinance_info", yf(raw), meta=meta(qt))
    assert (o.response_state, o.no_sector_reason, o.sector, o.failure_reason, o.quote_type) == (state, reason, None, None, qt) and o.payload_hash


@pytest.mark.parametrize("m,why", [(None, "quote_type_evidence_missing"), (meta(None), "quote_type_missing"), (meta("EQUITY"), "operating_company_sector_absent"),
                                   (meta("CRYPTOCURRENCY"), "quote_type_unrecognised"), (meta("etf-ish"), "quote_type_unrecognised")])
@pytest.mark.parametrize("raw", [None, "", " ", "Unknown"])
def test_a_missing_sector_without_non_operating_evidence_is_responded_but_unusable_never_an_explicit_no_sector(raw, m, why):
    o = R.classify("yfinance_info", yf(raw), meta=m)
    assert (o.response_state, o.failure_reason, o.no_sector_reason, o.sector) == ("invalid_response", why, None, None)
    d = R.decide(None, o, "r1")
    assert d.chain_effect == "none" and d.seq is None                                  # it can never become an observation


def test_a_sparse_answer_is_responded_but_unusable_and_an_empty_one_is_a_request_failure():
    o = R.classify("yfinance_info", None, meta=meta(None, 1))                           # e.g. the 404 body (one key)
    assert (o.response_state, o.failure_reason) == ("invalid_response", "response_too_sparse")
    assert R.classify("yfinance_info", None, meta=meta(None, 0)).response_state == "request_failed"
    assert R.classify("yfinance_info", None).failure_reason == "no_company_info"


def test_yfinance_meta_and_company_info_are_the_single_reading_of_a_raw_answer():
    raw = {"quoteType": " etf ", "symbol": "SPY", "a": 1, "b": 2, "c": 3}
    assert R.yfinance_meta(raw) == {"quote_type": "ETF", "n_keys": 5, "sector_key_present": False}
    assert R.yfinance_meta({"sector": "Technology", "quoteType": "EQUITY"})["sector_key_present"] is True
    assert R.yfinance_meta(None) == {"quote_type": None, "n_keys": 0, "sector_key_present": False}
    assert R.yfinance_company_info(raw) == {"sector": None}                              # absent key -> None: exactly the flattening that erases the distinction
    assert R.yfinance_company_info({"a": 1}) is None and R.yfinance_company_info(None) is None
    assert R.MIN_YF_KEYS == 5


def test_the_payload_hash_carries_the_evidence_only_when_there_is_some():
    assert R.payload_hash("Tech") == R.payload_hash("Tech", None)
    assert R.payload_hash(None, "ETF") != R.payload_hash(None, "MUTUALFUND") != R.payload_hash(None)
    assert R.projection(None) == {"sector": None} and R.projection(None, "ETF") == {"sector": None, "quote_type": "ETF"}


def test_decide_is_the_one_chain_rule():
    sec = lambda s: R.classify("yfinance_info", yf(s))                                  # noqa: E731
    nosec = R.classify("yfinance_info", yf(None), meta=meta("ETF"))
    assert R.decide(None, sec("Tech"), "r1") == R.Decision("created_observation", "first", 1, None, None)
    head = (7, 3, "h3", "Tech", "r0")
    assert R.decide(head, sec("Tech"), "r1") == R.Decision("confirmed_head", observation_id=7)
    assert R.decide(head, sec("Tech"), "r0") == R.Decision("created_observation", observation_id=7)         # a retry of the run that created it
    assert R.decide(head, sec("Energy"), "r1") == R.Decision("created_observation", "changed", 4, "h3")
    assert R.decide(head, nosec, "r1") == R.Decision("created_observation", "became_none", 4, "h3")
    assert R.decide((7, 3, "h3", None, "r0"), sec("Tech"), "r1") == R.Decision("created_observation", "became_set", 4, "h3")
    assert R.decide((7, 3, "h3", None, "r0"), nosec, "r1") == R.Decision("confirmed_head", observation_id=7)
    for o in (R.classify("yfinance_info", None, TimeoutError()), R.classify("tiingo_meta", {"sector": None})):
        assert R.decide(head, o, "r1") == R.Decision("none")


def _tiingo(row):
    row = dict(row, source="tiingo_meta")
    row["value_hash"] = SH.row_hash(row)
    return row


def test_a_primary_outage_never_lets_the_diagnostic_vendors_sector_stand_in():
    """Policy A, answer 2: yfinance (authoritative) says Tech then fails for 40 days; Tiingo says Energy. The research reads ONLY the authoritative chain:
    its head is still Tech (stale after 30 days), never Energy, and the failed polls neither refresh nor erase it."""
    head = chain(("Tech", at(D0 - timedelta(days=60))))[0]
    rows = [head, _tiingo(obs(1, "Energy", at(D0 - timedelta(days=5))))]
    o, c = SH.group_history(rows, R.AUTHORITATIVE_SOURCE)
    s = SH.select(o["AAA"], c.get("AAA", []), t0=D0, grace_days=GRACE, cutoff=CUTOFF)
    assert (s.kind, s.sector, s.evidence.state) == (SH.K_OBSERVED, "Tech", SP.OBSERVED_STALE)
    od, cd = SH.group_history(rows, "tiingo_meta")                                       # the diagnostic chain is its own, intact, never merged
    assert SH.select(od["AAA"], cd.get("AAA", []), t0=D0, grace_days=GRACE, cutoff=CUTOFF).sector == "Energy"


def test_a_primary_that_returns_with_a_different_sector_is_an_ordinary_reclassification_not_a_reconciliation():
    """Answer 3: there is no fallback record in the authoritative chain, so nothing needs reconciling; Tech -> Energy is a normal `changed` row."""
    d = R.decide((7, 1, "h1", "Tech", "r0"), R.classify("yfinance_info", yf("Energy")), "r9")
    assert (d.chain_effect, d.change_kind, d.seq) == ("created_observation", "changed", 2)


def test_the_source_policy_is_one_authoritative_vendor_and_the_research_side_agrees():
    assert R.AUTHORITATIVE_SOURCE == SH.AUTHORITATIVE_SOURCE == "yfinance_info"
    assert tuple(R.DIAGNOSTIC_SOURCES) == tuple(SH.DIAGNOSTIC_SOURCES) == ("tiingo_meta",)
    assert set(R.DIAGNOSTIC_SOURCES).isdisjoint({R.AUTHORITATIVE_SOURCE})


@pytest.mark.parametrize("raw", [None, "", "Unknown"])
def test_a_tiingo_none_is_ambiguous_never_an_explicit_no_sector(raw):
    o = R.classify("tiingo_meta", {"symbol": "AAA", "sector": raw})
    assert (o.response_state, o.failure_reason, o.no_sector_reason, o.sector) == ("invalid_response", "ambiguous_source_none", None, None)


def test_a_real_sector_is_cleaned_and_keeps_the_verbatim_string():
    o = R.classify("yfinance_info", yf("  Technology "))
    assert (o.response_state, o.sector, o.sector_raw) == ("sector", "Technology", "  Technology ")


def test_failures_are_coded_and_never_a_sector_answer():
    assert R.classify("yfinance_info", None, TimeoutError("x")).failure_reason == "timeout"
    assert R.classify("yfinance_info", None, RuntimeError("x")).failure_reason == "request_error"
    assert R.classify("yfinance_info", None).failure_reason == "no_company_info"
    assert R.classify("yfinance_info", {"symbol": "AAA"}).failure_reason == "sector_field_missing"
    assert R.classify("yfinance_info", {"symbol": "AAA", "sector": 5}).failure_reason == "non_string_sector"
    assert R.classify("yfinance_info", yf("x" * 101)).failure_reason == "oversized_sector"
    for o in (R.classify("yfinance_info", None, TimeoutError()), R.classify("yfinance_info", None)):
        assert o.response_state == "request_failed" and o.sector is None and o.payload_hash is None


def test_the_recorders_cleaning_is_the_researchs_cleaning():
    for raw in (None, "", " ", "Unknown", "unknown", "  UNKNOWN", "Tech", " Tech ", "Health Care", "Real Estate"):
        o = R.classify("yfinance_info", yf(raw))
        assert (o.sector if o.response_state == "sector" else None) == SP.clean_sector(raw), raw


def test_the_payload_hash_is_canonical_and_sector_only():
    assert R.payload_hash("Tech") == R.payload_hash("Tech") != R.payload_hash("tech") != R.payload_hash(None)
    assert len(R.payload_hash("Tech")) == 64


def test_the_flag_is_off_unless_exactly_one(monkeypatch):
    monkeypatch.delenv(R.FLAG_ENV, raising=False)
    assert R.enabled() is False
    for v in ("", "0", "true", "TRUE", "yes", "on", " 1", "1 "):
        monkeypatch.setenv(R.FLAG_ENV, v)
        assert R.enabled() is False, v
    monkeypatch.setenv(R.FLAG_ENV, "1")
    assert R.enabled() is True


def test_a_disabled_recorder_does_nothing_and_never_connects():
    def boom():
        raise AssertionError("must not connect when disabled")
    rec = R.SectorRecorder(run_id="r", connect=boom, is_enabled=lambda: False)
    assert rec.record("AAA", SRC, yf("Tech")) is None and rec.counters == {"attempted": 0, "recorded": 0, "duplicate": 0, "failed": 0}


def test_a_connection_failure_is_counted_and_logged_never_raised(caplog):
    def boom():
        raise RuntimeError("db down")
    rec = R.SectorRecorder(run_id="r", connect=boom, is_enabled=lambda: True)
    with caplog.at_level("ERROR"):
        assert rec.record("AAA", SRC, yf("Tech")) is None
    assert rec.counters["failed"] == 1 and any("AAA" in m and "db down" in m for m in caplog.messages)


# ================================================================== chain verification
def test_a_valid_chain_verifies_and_a_single_row_anchors():
    c = chain(("Tech", at(D0)), ("Health Care", at(D0 + timedelta(days=5))), (None, at(D0 + timedelta(days=9))), ("Energy", at(D0 + timedelta(days=11))))
    assert SH.verify_chain(c) == [] and SH.verify_chain(c[:1]) == [] and SH.verify_chain([]) == []
    assert c[0]["prev_value_hash"] is None and c[1]["prev_value_hash"] == c[0]["value_hash"]


def test_every_kind_of_tampering_is_detected():
    c = chain(("Tech", at(D0)), ("Health Care", at(D0 + timedelta(days=5))), ("Energy", at(D0 + timedelta(days=9))))

    def tamper(i, **ch):
        out = [dict(r) for r in c]
        out[i].update(ch)
        return SH.verify_chain(out)
    assert "hash_mismatch" in tamper(1, sector="Utilities")                                     # value rewritten
    assert "hash_mismatch" in tamper(1, stamp=at(D0 + timedelta(days=1)))                      # capture time rewritten
    assert "hash_mismatch" in tamper(1, provenance="reconstructed")                            # provenance rewritten
    assert "provenance_not_observed_forward" in tamper(1, provenance="reconstructed")
    assert "broken_link" in tamper(2, prev_value_hash="0" * 64)                                # linkage cut
    assert "seq_gap_or_reorder" in SH.verify_chain([c[0], c[2]])                              # a missing row
    assert "anchor_mismatch" in tamper(0, prev_value_hash="1" * 64)                            # the first row must anchor to nothing
    assert "effective_session_mismatch" in tamper(1, effective_session=D0)
    assert "same_value_observation" in SH.verify_chain([c[0], {**obs(2, "Tech", at(D0 + timedelta(days=1)), prev=c[0]["value_hash"])}])
    assert "change_kind_mismatch" in tamper(1, change_kind="became_none")
    assert "time_not_monotone" in SH.verify_chain([c[0], obs(2, "Energy", at(D0 - timedelta(days=1)), prev=c[0]["value_hash"])])


def test_the_hash_depends_on_every_hashed_field():
    base = obs(1, "Tech", at(D0))
    for field, value in (("symbol", "BBB"), ("source", "other"), ("seq", 2), ("sector", "Energy"), ("sector_raw", "Tech "), ("stamp", at(D0, 11)),
                         ("provenance", "reconstructed"), ("raw_payload_hash", "a" * 64), ("prev_value_hash", "b" * 64)):
        assert SH.row_hash({**base, field: value}) != base["value_hash"], field
    assert SH.row_hash({**base, "no_sector_reason": "vendor_null"}) != base["value_hash"]


# ================================================================== the selector: what was known at t0
def test_no_history_is_absent_not_a_sector():
    s = sel([])
    assert s.kind == SH.K_NO_HISTORY and s.evidence.state == SP.UNAVAILABLE and s.evidence.reason == SP.HISTORY_ABSENT and s.sector is None


def test_an_observation_learned_after_the_decision_session_does_not_exist_for_it():
    """The grace rule alone (is_known) admits an observation captured the day AFTER t0; effective_session <= t0 closes that."""
    later = chain(("Tech", at(D0 + timedelta(days=1), 2)))
    assert SH.C.is_known(later[0]["stamp"], D0, GRACE, CUTOFF) is True               # the grace rule alone would let it in ...
    s = sel(later)
    assert s.kind == SH.K_NO_HISTORY and s.sector is None                              # ... the session test does not


def test_learning_later_never_changes_the_earlier_verdict():
    o = chain(("Tech", at(D0 - timedelta(days=3))), ("Health Care", at(D0 + timedelta(days=4))))
    before = sel(o[:1], t0=D0)
    after = sel(o, t0=D0)
    assert dataclasses.asdict(before) == dataclasses.asdict(after) and after.sector == "Tech" and after.head_seq == 1
    assert sel(o, t0=D0 + timedelta(days=4)).sector == "Health Care"                   # the later decision sees the later fact


def test_reclassification_is_seen_only_from_its_own_session_on():
    o = chain(("Tech", at(D0)), ("Health Care", at(D0 + timedelta(days=10))))
    assert [sel(o, t0=D0 + timedelta(days=d)).sector for d in (0, 5, 9, 10, 11, 20)] == ["Tech", "Tech", "Tech", "Health Care", "Health Care", "Health Care"]


@pytest.mark.parametrize("age,state", [(0, SP.OBSERVED_FRESH), (29, SP.OBSERVED_FRESH), (30, SP.OBSERVED_FRESH), (31, SP.OBSERVED_STALE), (90, SP.OBSERVED_STALE)])
def test_the_thirty_day_boundary_is_derived_at_read_time(age, state):
    o = chain(("Tech", at(D0)))
    s = sel(o, t0=D0 + timedelta(days=age))
    assert s.evidence.state == state and s.evidence.age_days == age and s.sector == "Tech"
    assert SP.SECTOR_MAX_AGE_DAYS == 30                                                  # operational, not empirically validated; stored nowhere


def test_the_age_limit_is_a_read_time_parameter_not_a_stored_fact():
    o = chain(("Tech", at(D0)))
    assert sel(o, t0=D0 + timedelta(days=10), max_age_days=7).evidence.state == SP.OBSERVED_STALE
    assert sel(o, t0=D0 + timedelta(days=10), max_age_days=60).evidence.state == SP.OBSERVED_FRESH
    assert "max_age" not in "".join(o[0])


def test_a_same_value_refresh_extends_freshness_to_the_latest_confirmation():
    """Defined: currency = max(head capture, latest eligible same-value confirmation). The identity stays the head's."""
    o = chain(("Tech", at(D0)))
    c = [confirm(o[0], at(D0 + timedelta(days=25)))]
    t = D0 + timedelta(days=40)
    assert sel(o, [], t0=t).evidence.state == SP.OBSERVED_STALE                           # 40 days since capture
    s = sel(o, c, t0=t)
    assert s.evidence.state == SP.OBSERVED_FRESH and s.evidence.age_days == 15 and s.sector == "Tech" and s.head_seq == 1 and s.confirmations_used == 1
    assert sel(o, c, t0=D0 + timedelta(days=24)).confirmations_used == 0                  # not yet confirmed at that decision


def test_a_confirmation_made_after_the_decision_does_not_extend_it():
    o = chain(("Tech", at(D0)))
    c = [confirm(o[0], at(D0 + timedelta(days=45)))]
    assert sel(o, c, t0=D0 + timedelta(days=40)).evidence.state == SP.OBSERVED_STALE


def test_a_confirmation_of_a_superseded_head_never_counts_for_the_new_head():
    o = chain(("Tech", at(D0)), ("Health Care", at(D0 + timedelta(days=5))))
    c = [confirm(o[0], at(D0 + timedelta(days=20)))]                                      # polled Tech AFTER the head was already Health Care? impossible
    s = sel(o, c, t0=D0 + timedelta(days=40))
    assert s.sector == "Health Care" and s.confirmations_used == 0 and s.evidence.state == SP.OBSERVED_STALE


def test_vendor_failure_cannot_extend_freshness_and_never_erases_the_last_sector():
    """A failed poll is a `sector_poll` row with chain_effect = none: the reader does not even select it, so the selector sees exactly the head."""
    o = chain(("Tech", at(D0)))
    ages = {d: sel(o, t0=D0 + timedelta(days=d)) for d in range(1, 36)}
    assert all(s.sector == "Tech" for s in ages.values())                                  # still believed every day
    assert all(s.evidence.state == SP.OBSERVED_FRESH for d, s in ages.items() if d <= 30)
    assert all(s.evidence.state == SP.OBSERVED_STALE for d, s in ages.items() if d > 30)
    assert [ages[d].evidence.age_days for d in (1, 2, 3, 35)] == [1, 2, 3, 35]            # and keeps aging


def test_explicit_no_sector_is_meaningful_and_differs_from_no_history():
    o = chain(("Tech", at(D0)), (None, at(D0 + timedelta(days=5))))
    s = sel(o, t0=D0 + timedelta(days=6))
    assert s.kind == SH.K_EXPLICIT_NO_SECTOR and s.sector is None and s.evidence.reason == SP.NO_SECTOR and s.head_seq == 2
    assert sel(o, t0=D0 + timedelta(days=4)).kind == SH.K_OBSERVED                         # before the vendor said none, Tech was still believed
    assert s.kind != sel([]).kind and s.evidence.reason != sel([]).evidence.reason


def test_an_explicit_no_sector_that_becomes_a_sector_again_is_a_new_chained_row():
    o = chain(("Tech", at(D0)), (None, at(D0 + timedelta(days=5))), ("Tech", at(D0 + timedelta(days=9))))
    assert [r["change_kind"] for r in o] == ["first", "became_none", "became_set"] and SH.verify_chain(o) == []
    assert sel(o, t0=D0 + timedelta(days=9)).sector == "Tech"


def test_a_broken_chain_fails_closed():
    o = chain(("Tech", at(D0)), ("Health Care", at(D0 + timedelta(days=5))))
    bad = [dict(o[0]), {**o[1], "sector": "Utilities"}]
    s = sel(bad, t0=D0 + timedelta(days=6))
    assert s.kind == SH.K_BROKEN and s.evidence.reason == SP.IDENTITY_CONFLICT and "hash_mismatch" in s.problems
    # the tampered row only matters from the moment it is eligible
    assert sel(bad, t0=D0 + timedelta(days=2)).kind == SH.K_OBSERVED


def test_a_reconstructed_row_in_the_chain_is_never_observed_evidence():
    o = chain(("Tech", at(D0)))
    o[0] = {**o[0], "provenance": "reconstructed"}
    s = sel(o)
    assert s.kind == SH.K_BROKEN and s.evidence.state == SP.UNAVAILABLE and "provenance_not_observed_forward" in s.problems


def test_a_confirmation_stamped_before_its_head_fails_closed():
    o = chain(("Tech", at(D0 + timedelta(days=5))))
    s = sel(o, [confirm(o[0], at(D0 + timedelta(days=2)))], t0=D0 + timedelta(days=10))
    assert s.kind == SH.K_BROKEN and "confirmation_before_head" in s.problems


def test_other_sources_never_mix_into_one_chain():
    a = chain(("Tech", at(D0)))
    b = [{**r, "source": "tiingo_meta"} for r in chain(("Energy", at(D0)))]
    o, c = SH.group_history(a + b, SRC)
    assert [r["sector"] for r in o["AAA"]] == ["Tech"] and c == {}


def test_orphan_confirmations_are_counted():
    o = chain(("Tech", at(D0)))
    assert SH.orphan_confirmations(o, [confirm(o[0], at(D0))]) == 0
    assert SH.orphan_confirmations([], [confirm(o[0], at(D0))]) == 1


# ================================================================== the dual-source cross-check
def ev(state, sector="Tech", reason="r"):
    return SP.SectorEvidence(state, reason, sector, 0)


def hist(kind=SH.K_OBSERVED, state=SP.OBSERVED_FRESH, sector="Tech", problems=()):
    return SH.HistorySelection(kind, ev(state, sector), sector, 1, None, None, 0, tuple(problems))


def test_agreement_proceeds_unchanged():
    cc = SH.crosscheck(ev(SP.OBSERVED_FRESH), hist())
    assert cc.relation == SH.R_AGREE and cc.effective.state == SP.OBSERVED_FRESH and cc.effective.sector == "Tech"


def test_a_sector_identity_disagreement_fails_closed():
    cc = SH.crosscheck(ev(SP.OBSERVED_FRESH, "Tech"), hist(sector="Health Care"))
    assert cc.relation == SH.R_IDENTITY_CONFLICT and cc.effective.state == SP.UNAVAILABLE and cc.effective.reason == SP.IDENTITY_CONFLICT


def test_an_explicit_no_sector_history_against_a_candidate_sector_is_a_conflict_not_a_winner():
    """Owner rule (Slice 11): history says explicit no_sector, candidate says Tech for the same decision point -> neither silently wins."""
    nos = SH.HistorySelection(SH.K_EXPLICIT_NO_SECTOR, ev(SP.UNAVAILABLE, None, "no_sector"), None, 2, None, None, 0, ())
    for cand in (SP.OBSERVED_FRESH, SP.OBSERVED_STALE):
        cc = SH.crosscheck(ev(cand, "Tech"), nos)
        assert cc.relation == SH.R_NO_SECTOR_CONFLICT and cc.effective.state == SP.UNAVAILABLE and cc.effective.reason == SP.IDENTITY_CONFLICT
        assert cc.effective.sector == "Tech"                                                        # the disputed name stays visible for diagnosis
    # a candidate that is itself "no sector" (an absence, not an assertion) is NOT a conflict
    for cand in (SP.UNAVAILABLE,):
        assert SH.crosscheck(ev(cand, None), nos).relation != SH.R_NO_SECTOR_CONFLICT
    # reconstructed / unknown candidates keep their own (never-loosened) classification
    for bad in (SP.RECONSTRUCTED, SP.UNKNOWN):
        assert SH.crosscheck(ev(bad), nos).relation != SH.R_NO_SECTOR_CONFLICT
    # an OBSERVED history sector is unaffected by this rule
    assert SH.crosscheck(ev(SP.OBSERVED_FRESH, "Tech"), hist()).relation == SH.R_AGREE


def test_history_can_only_tighten():
    for cand in (SP.OBSERVED_FRESH, SP.OBSERVED_STALE):
        for h in (SP.UNAVAILABLE, SP.RECONSTRUCTED, SP.UNKNOWN):
            cc = SH.crosscheck(ev(cand), hist(state=h))
            assert cc.effective.state == (h if SH._RANK[h] > SH._RANK[cand] else cand), (cand, h)
    assert SH.crosscheck(ev(SP.OBSERVED_FRESH), hist(SH.K_NO_HISTORY, SP.UNAVAILABLE)).effective.state == SP.UNAVAILABLE
    assert SH.crosscheck(ev(SP.OBSERVED_FRESH), hist(SH.K_NO_HISTORY, SP.UNAVAILABLE)).relation == SH.R_HISTORY_TIGHTENED


def test_a_stronger_history_never_repairs_a_weaker_candidate_cell():
    for cand in (SP.OBSERVED_STALE, SP.UNAVAILABLE, SP.RECONSTRUCTED, SP.UNKNOWN):
        cc = SH.crosscheck(ev(cand), hist(state=SP.OBSERVED_FRESH))
        if SH._RANK[cand] > 0:
            assert cc.relation == SH.R_HISTORY_AHEAD and cc.effective.state == cand, cand           # the candidate's own (safer) evidence stands


def test_reconstructed_or_unsafe_provenance_in_either_source_is_never_loosened():
    for bad in (SP.RECONSTRUCTED, SP.UNKNOWN):
        assert SH.crosscheck(ev(bad), hist()).effective.state == bad
        assert SH.crosscheck(ev(SP.OBSERVED_FRESH), hist(state=bad)).effective.state == bad


def test_a_broken_chain_fails_closed_whatever_the_candidate_says():
    for cand in (SP.OBSERVED_FRESH, SP.OBSERVED_STALE, SP.UNAVAILABLE):
        cc = SH.crosscheck(ev(cand), hist(SH.K_BROKEN, SP.UNAVAILABLE, problems=("broken_link",)))
        assert cc.relation == SH.R_CHAIN_BROKEN and cc.effective.reason == SP.IDENTITY_CONFLICT and cc.problems == ("broken_link",)


def test_summarise_is_deterministic_and_counts_every_relation():
    recs = [("k2", SH.crosscheck(ev(SP.OBSERVED_FRESH), hist())), ("k1", SH.crosscheck(ev(SP.OBSERVED_FRESH, "Tech"), hist(sector="Energy")))]
    a, b = SH.summarise(recs, source=SRC), SH.summarise(list(reversed(recs)), source=SRC)
    assert a == b and a["by_relation"] == {"agree": 1, "identity_conflict": 1} and a["examples"]["identity_conflict"][0]["observation_key"] == "k1"
