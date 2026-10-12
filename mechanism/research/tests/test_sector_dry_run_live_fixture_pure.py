"""The non-mutating sector dry run, replayed over REAL vendor responses (provenance: LIVE yfinance Ticker.info, 40 symbols, 2026-10-06).

The fixture is evidence, not a spec: it records what the vendor actually answered that day, trimmed to the identifying keys (the original key count is
kept so the sparse-response rule sees the real size). The dry run reuses the production classifier; this test pins that the live answers land where the
policy says and that the dry run never writes."""
import json
import os
from collections import Counter

import pytest

from data_updaters import sector_history_recorder as R
from conftest import ROOT

FIXTURE = os.path.join(ROOT, "docs", "research", "evidence", "yfinance_live_probe_2026-10-06.json")


def cases():
    with open(FIXTURE, encoding="utf-8") as fh:
        doc = json.load(fh)
    out = {}
    for c in doc["cases"]:
        raw = dict(c["raw"])
        raw.update({f"_pad{i}": None for i in range(max(0, c["n_keys"] - len(raw)))})
        out[c["symbol"]] = raw
    return out


CASES = cases()


def run(symbol, **kw):
    return R.dry_run(symbol, R.SRC_YFINANCE, raw=CASES[symbol], **kw)


def test_the_fixture_is_the_live_sample_with_its_provenance():
    with open(FIXTURE, encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["provenance"].startswith("LIVE VENDOR") and doc["probed_at_utc"].startswith("2026-10-06") and len(doc["cases"]) == 40


def test_the_live_answers_land_in_the_expected_buckets():
    tally = Counter()
    for sym in CASES:
        o = run(sym)["outcome"]
        tally[(o["response_state"], o["quote_type"])] += 1
    assert tally == {("sector", None): 30, ("no_sector", "ETF"): 6, ("no_sector", "MUTUALFUND"): 2, ("invalid_response", None): 2}


@pytest.mark.parametrize("symbol,sector", [("AAPL", "Technology"), ("JPM", "Financial Services"), ("BRK-B", "Financial Services"), ("O", "Real Estate"),
                                           ("T", "Communication Services"), ("TSM", "Technology"), ("GME", "Consumer Cyclical")])
def test_an_operating_company_records_its_vendor_sector_and_is_admissible(symbol, sector):
    r = run(symbol)
    assert r["outcome"]["sector"] == sector and r["admissible_for_forward_history"] and r["rejection_reason"] is None
    assert r["proposed_poll"] == {"response_state": "sector", "chain_effect": "created_observation"} and r["proposed_observation"]["change_kind"] == "first"


@pytest.mark.parametrize("symbol", ["SPY", "QQQ", "GLD", "IWM", "XLK", "TLT", "VTSAX", "VFIAX"])
def test_a_fund_is_an_inferred_no_sector_only_because_the_vendor_says_what_it_is(symbol):
    r = run(symbol)
    assert r["outcome"]["response_state"] == "no_sector" and r["outcome"]["sector"] is None
    assert r["outcome"]["quote_type"] in ("ETF", "MUTUALFUND") and r["admissible_for_forward_history"]


@pytest.mark.parametrize("symbol", ["ZZZZXQ", "QQQQQQ9"])
def test_an_unknown_symbol_is_a_sparse_invalid_response_never_a_no_sector(symbol):
    """The live 404 body is a ONE-key dict: the vendor answered, with too little to use. That is `invalid_response`, not an outage and not a no-sector."""
    r = run(symbol)
    assert r["outcome"]["response_state"] == "invalid_response" and r["outcome"]["failure_reason"] == "response_too_sparse"
    assert not r["admissible_for_forward_history"]
    assert r["proposed_poll"]["chain_effect"] == "none" and r["proposed_observation"] is None


def head(sector, seq=3):
    return {"seq": seq, "value_hash": "h" * 32, "sector": sector, "run_id": "earlier"}


def test_the_same_sector_on_an_existing_chain_is_a_confirmation_not_a_new_observation():
    r = run("AAPL", head=head("Technology"))
    assert r["proposed_poll"] == {"response_state": "sector", "chain_effect": "confirmed_head"} and r["proposed_observation"] is None


def test_a_different_sector_from_the_authoritative_source_is_an_ordinary_reclassification():
    r = run("AAPL", head=head("Industrials"))
    assert r["proposed_observation"]["change_kind"] == "changed" and r["proposed_observation"]["seq"] == 4


def test_a_fund_arriving_on_a_chain_that_had_a_sector_becomes_none_and_the_reverse_becomes_set():
    assert run("SPY", head=head("Technology"))["proposed_observation"]["change_kind"] == "became_none"
    assert run("AAPL", head=head(None))["proposed_observation"]["change_kind"] == "became_set"


def test_a_failed_poll_never_touches_the_head():
    r = R.dry_run("AAPL", R.SRC_YFINANCE, error="timeout", head=head("Technology"))
    assert r["proposed_poll"]["chain_effect"] == "none" and r["proposed_observation"] is None


def test_an_operating_company_whose_sector_vanished_is_invalid_not_no_sector():
    raw = dict(CASES["AAPL"])
    raw.pop("sector")
    r = R.dry_run("AAPL", R.SRC_YFINANCE, raw=raw)
    assert r["outcome"]["response_state"] == "invalid_response" and r["outcome"]["failure_reason"] == "operating_company_sector_absent"
    assert not r["admissible_for_forward_history"]


def test_a_blank_sector_is_never_a_sector():
    raw = dict(CASES["AAPL"], sector="   ")
    r = R.dry_run("AAPL", R.SRC_YFINANCE, raw=raw)
    assert r["outcome"]["response_state"] != "sector" and not r["admissible_for_forward_history"]


@pytest.mark.parametrize("error", ["timeout", "request_error"])
def test_a_provider_failure_is_request_failed(error):
    r = R.dry_run("AAPL", R.SRC_YFINANCE, error=error)
    assert r["outcome"]["response_state"] == "request_failed" and not r["admissible_for_forward_history"] and r["proposed_poll"]["chain_effect"] == "none"


def test_a_tiingo_answer_is_diagnostic_and_never_admissible_even_when_it_has_a_sector():
    r = R.dry_run("AAPL", R.SRC_TIINGO, raw={"sector": "Technology", "name": "Apple Inc"})
    assert r["source_identity"] == "diagnostic" and not r["admissible_for_forward_history"] and r["rejection_reason"] == "diagnostic_source_is_not_identity"


def test_the_dry_run_has_no_database_handle_and_declares_it_writes_nothing():
    for sym in CASES:
        r = run(sym)
        assert r["writes"] is False and r["source_asof"] is None
        assert bool(r["raw_payload_hash"]) == (r["outcome"]["response_state"] in ("sector", "no_sector"))


def test_the_dry_run_answer_is_the_production_classifier_answer():
    for sym, raw in CASES.items():
        o = R.classify(R.SRC_YFINANCE, R.yfinance_company_info(raw), None, R.yfinance_meta(raw))
        d = R.dry_run(sym, R.SRC_YFINANCE, raw=raw)["outcome"]
        assert (d["response_state"], d["sector"], d["no_sector_reason"], d["failure_reason"]) == (o.response_state, o.sector, o.no_sector_reason, o.failure_reason)
