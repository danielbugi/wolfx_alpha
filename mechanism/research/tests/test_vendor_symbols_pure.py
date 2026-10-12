"""Slice 12: the vendor-boundary symbol translation, with no database.

The translation is evidence-bound and fails closed. Evidence: Slice 11's live probe (dev machine) and Slice 12's probe from the production VPS network
(2026-10-06): `BRK.B` -> 15 keys and no sector key, `BF.B` -> 1 key, `BRK/A` and `BRK/B` -> a request error, while `BRK-B`, `BF-B` and `BRK-A` return a full
classification. The production universe's only non-plain symbols are exactly BF.B, BRK.B, BRK/A (active) and BRK/B (inactive duplicate of BRK.B)."""
import re

import pytest

from conftest import ROOT
from data_updaters import fundamentals_updater as FU
from data_updaters import sector_history_recorder as R
from data_updaters import vendor_symbols as V

PRODUCTION_FORMS = {"BF.B": "BF-B", "BRK.B": "BRK-B", "BRK/A": "BRK-A", "BRK/B": "BRK-B"}      # the complete set of non-plain production symbols (2026-10-06)


# ------------------------------------------------------------------ the rule
@pytest.mark.parametrize("canonical,request_symbol", sorted(PRODUCTION_FORMS.items()))
def test_every_known_production_share_class_translates_to_its_evidenced_vendor_form(canonical, request_symbol):
    v = V.to_yfinance(canonical)
    assert (v.canonical, v.request, v.status, v.rule) == (canonical, request_symbol, V.TRANSLATED, V.RULE_SEPARATOR_TO_DASH)


@pytest.mark.parametrize("symbol", ["AAPL", "A", "GOOGL", "MSTR", "BRK1", "ZZZZXQ", "1ABC", "AFGB"])
def test_a_plain_symbol_is_sent_unchanged(symbol):
    v = V.to_yfinance(symbol)
    assert v.request == symbol == v.canonical and v.status == V.UNCHANGED and v.rule == V.RULE_PLAIN


@pytest.mark.parametrize("symbol", ["BRK-B", "BF-B", "BRK-A", "LEN-B"])
def test_an_already_dashed_share_class_is_never_translated_again(symbol):
    v = V.to_yfinance(symbol)
    assert v.request == symbol and v.status == V.UNCHANGED and v.rule == V.RULE_ALREADY_DASHED


@pytest.mark.parametrize("symbol,reason", [
    ("XYZ.U", V.R_AMBIGUOUS_CLASS), ("XYZ.W", V.R_AMBIGUOUS_CLASS), ("XYZ/R", V.R_AMBIGUOUS_CLASS),     # units / warrants / rights: vendors disagree on the spelling
    ("ABC.WS", V.R_LAYOUT), ("ABC.WT", V.R_LAYOUT), ("ABC.RT", V.R_LAYOUT), ("BRK.BB", V.R_LAYOUT),    # a multi-letter suffix is not a share class
    ("A.B.C", V.R_LAYOUT), ("BRK..B", V.R_LAYOUT), ("BRK./B", V.R_LAYOUT), ("AB1.C", V.R_LAYOUT),     # a second separator / a digit in a dotted root
    ("BTC-USD", V.R_LAYOUT), ("ABC-WT", V.R_LAYOUT), (".B", V.R_LAYOUT), ("BRK.", V.R_LAYOUT), ("-B", V.R_LAYOUT),
    ("brk.b", V.R_CHARACTERS), ("Brk.B", V.R_CHARACTERS), (" BRK.B", V.R_CHARACTERS), ("BRK.B ", V.R_CHARACTERS), ("BRK B", V.R_CHARACTERS),
    ("^GSPC", V.R_CHARACTERS), ("EURUSD=X", V.R_CHARACTERS), ("AT&T", V.R_CHARACTERS), ("BRK.B\n", V.R_CHARACTERS), ("BRK․B", V.R_CHARACTERS),
    ("", V.R_EMPTY), (None, V.R_EMPTY),
])
def test_anything_the_evidence_does_not_cover_is_refused_never_guessed(symbol, reason):
    with pytest.raises(V.UnsupportedSymbolFormat) as e:
        V.to_yfinance(symbol)
    assert e.value.reason == reason


def test_a_non_string_is_refused():
    for bad in (123, 4.5, b"BRK.B", ["BRK.B"]):
        with pytest.raises(V.UnsupportedSymbolFormat):
            V.to_yfinance(bad)


def test_a_naive_replace_would_have_been_wrong_for_these_and_the_translation_is_not_a_naive_replace():
    """Tiingo's normalizer maps every '.' and '/' to '-'. That is exactly the generic punctuation replacement this module refuses to be."""
    for sym in ("XYZ.U", "ABC.WS", "A.B.C", "AB1.C"):
        assert sym.replace(".", "-").replace("/", "-") != sym
        with pytest.raises(V.UnsupportedSymbolFormat):
            V.to_yfinance(sym)


def test_the_translation_is_idempotent_and_its_output_is_always_a_supported_unchanged_form():
    for sym in list(PRODUCTION_FORMS) + ["AAPL", "BRK-B"]:
        once = V.to_yfinance(sym)
        again = V.to_yfinance(once.request)
        assert again.request == once.request and again.status == V.UNCHANGED


def test_two_internal_symbols_may_share_one_vendor_symbol_but_never_one_identity():
    a, b = V.to_yfinance("BRK.B"), V.to_yfinance("BRK/B")
    assert a.request == b.request == "BRK-B" and a.canonical != b.canonical


def test_the_canonical_symbol_is_carried_through_unchanged():
    for sym in list(PRODUCTION_FORMS) + ["AAPL", "BRK-B"]:
        assert V.to_yfinance(sym).canonical == sym and V.to_yfinance(sym).as_dict()["canonical"] == sym


def test_the_module_is_standard_library_only_so_the_dry_run_needs_no_database():
    text = open(V.__file__, encoding="utf-8").read()
    imports = set(re.findall(r"^(?:from|import)\s+([\w.]+)", text, re.M))
    assert imports <= {"__future__", "re", "dataclasses", "typing"}, imports


# ------------------------------------------------------------------ the recorder and the dry run
def test_a_refused_symbol_is_a_failed_poll_with_a_coded_reason_not_a_vendor_answer():
    o = R.classify(R.SRC_YFINANCE, None, V.UnsupportedSymbolFormat("ABC.WS", V.R_LAYOUT), None)
    assert (o.response_state, o.failure_reason, o.payload_hash) == ("request_failed", "unsupported_symbol_format", None)
    assert re.fullmatch(r"[a-z0-9_]{1,40}", o.failure_reason)                        # the poll table's own format constraint


def test_the_dry_run_reports_the_translation_and_keeps_the_canonical_symbol_as_the_identity():
    raw = {"sector": "Financial Services", "quoteType": "EQUITY", "a": 1, "b": 2, "c": 3}
    r = R.dry_run("BRK.B", R.SRC_YFINANCE, raw=raw)
    assert r["symbol"] == "BRK.B" and r["vendor_symbol"] == {"canonical": "BRK.B", "request": "BRK-B", "status": "translated",
                                                            "rule": "share_class_separator_to_dash"}
    assert r["outcome"]["sector"] == "Financial Services" and r["admissible_for_forward_history"] and r["writes"] is False


def test_the_dry_run_of_a_refused_symbol_is_the_refusal_whatever_response_was_supplied():
    raw = {"sector": "Technology", "quoteType": "EQUITY", "a": 1, "b": 2, "c": 3}
    r = R.dry_run("ABC.WS", R.SRC_YFINANCE, raw=raw)
    assert r["vendor_symbol"]["status"] == "refused" and r["vendor_symbol"]["request"] is None and r["vendor_symbol"]["refusal_reason"] == V.R_LAYOUT
    assert r["outcome"]["response_state"] == "request_failed" and r["outcome"]["failure_reason"] == "unsupported_symbol_format"
    assert not r["admissible_for_forward_history"] and r["proposed_observation"] is None and r["raw_payload_hash"] is None


def test_a_tiingo_interaction_has_no_yfinance_translation():
    r = R.dry_run("BRK.B", R.SRC_TIINGO, raw={"sector": "Financial Services", "a": 1})
    assert r["vendor_symbol"] is None and r["source_identity"] == "diagnostic" and not r["admissible_for_forward_history"]


# ------------------------------------------------------------------ the no-sector wording: inferred, never asserted by the vendor
def test_a_no_sector_state_is_labelled_as_inferred_from_quote_type_in_every_report():
    etf = {"quoteType": "ETF", "a": 1, "b": 2, "c": 3, "d": 4}
    r = R.dry_run("SPY", R.SRC_YFINANCE, raw=etf)
    assert r["outcome"]["response_state"] == "no_sector" and r["no_sector_basis"] == "inferred_from_quote_type" == R.NO_SECTOR_BASIS
    assert r["outcome"]["quote_type"] == "ETF"
    assert R.dry_run("AAPL", R.SRC_YFINANCE, raw={"sector": "Technology", "a": 1, "b": 2, "c": 3, "d": 4})["no_sector_basis"] is None


def test_an_operating_company_without_a_sector_is_still_invalid_not_no_sector():
    raw = {"quoteType": "EQUITY", "a": 1, "b": 2, "c": 3, "d": 4}           # what the production VPS returned for the raw 'BRK.B': 15 keys, no sector key
    r = R.dry_run("BRK.B", R.SRC_YFINANCE, raw=raw)
    assert r["outcome"]["response_state"] == "invalid_response" and r["outcome"]["failure_reason"] == "operating_company_sector_absent"
    assert r["no_sector_basis"] is None and not r["admissible_for_forward_history"]


def test_the_wording_never_claims_the_vendor_asserted_no_sector():
    """Code comments and docstrings of the recorder must not say the vendor 'explicitly answered/asserted' a no-sector (it supplies none)."""
    text = open(R.__file__, encoding="utf-8").read()
    assert not re.search(r"vendor\s+explicitly\s+(answered|asserts?|said)", text, re.I)
    assert "INFERRED" in text and R.NO_SECTOR_BASIS in text


def test_source_policy_a_is_unchanged():
    assert R.AUTHORITATIVE_SOURCE == R.SRC_YFINANCE == "yfinance_info" and R.DIAGNOSTIC_SOURCES == (R.SRC_TIINGO,)
    assert R.source_identity(R.SRC_YFINANCE) == "authoritative" and R.source_identity(R.SRC_TIINGO) == "diagnostic"


# ------------------------------------------------------------------ ONE translation, used by the writer path AND the dry-run path
class _FakeTicker:
    seen = []

    def __init__(self, symbol):
        type(self).seen.append(symbol)
        self.info = {"sector": "Financial Services", "quoteType": "EQUITY", "symbol": symbol, "a": 1, "b": 2, "c": 3}


@pytest.fixture
def fake_yf(monkeypatch):
    import yfinance
    _FakeTicker.seen = []
    monkeypatch.setattr(yfinance, "Ticker", _FakeTicker)
    return _FakeTicker


@pytest.mark.parametrize("canonical", ["AAPL", "BRK.B", "BF.B", "BRK/A", "BRK/B", "BRK-B"])
def test_the_writer_path_and_the_dry_run_probe_ask_the_vendor_for_the_same_symbol(fake_yf, canonical):
    u = FU.FundamentalsUpdater()
    u._fetch_yfinance_raw(canonical)                                  # the WRITER's vendor call
    R.fetch_yfinance_raw(canonical)                                   # the dry-run CLI's vendor call
    assert fake_yf.seen == [V.to_yfinance(canonical).request] * 2
    assert fake_yf.seen[0] == R.dry_run(canonical, R.SRC_YFINANCE, raw={"a": 1})["vendor_symbol"]["request"]


@pytest.mark.parametrize("canonical", ["ABC.WS", "XYZ.U", "brk.b", "^GSPC", ""])
def test_both_paths_refuse_an_unsupported_symbol_before_any_request(fake_yf, canonical):
    u = FU.FundamentalsUpdater()
    with pytest.raises(V.UnsupportedSymbolFormat):
        u._fetch_yfinance_raw(canonical)
    with pytest.raises(V.UnsupportedSymbolFormat):
        R.fetch_yfinance_raw(canonical)
    assert fake_yf.seen == []


def test_the_flattened_company_info_keeps_the_canonical_symbol(fake_yf, monkeypatch):
    """`fetch_company_info` is what the fundamentals upsert stores: the row's symbol is the canonical one even though the vendor was asked for BRK-B."""
    from data_updaters import fundamentals_updater as mod
    monkeypatch.setattr(mod.config, "data_provider", "yfinance", raising=False)
    info = FU.FundamentalsUpdater().fetch_company_info("BRK.B")
    assert fake_yf.seen == ["BRK-B"] and info["symbol"] == "BRK.B" and info["sector"] == "Financial Services"


def test_there_is_exactly_one_vendor_symbol_translation_in_the_yfinance_paths():
    """No second implementation: every yfinance request in the writer and in the dry-run probe takes the symbol from `vendor_symbols.to_yfinance`."""
    for rel in ("mechanism/data_updaters/fundamentals_updater.py", "mechanism/data_updaters/sector_history_recorder.py"):
        text = open(f"{ROOT}/{rel}", encoding="utf-8").read()
        calls = re.findall(r"yf\.Ticker\((\w+)\)", text)
        assert calls == ["request_symbol"], (rel, calls)
        assert re.search(r"request_symbol\s*=\s*vendor_symbols\.to_yfinance\(\w+\)\.request", text), rel
        assert not re.search(r"\.replace\(\s*['\"][./]['\"]\s*,\s*['\"]-['\"]\s*\)", text), rel     # no inline dot->dash anywhere


# ------------------------------------------------------------------ the production evidence agrees with the code
def test_the_production_vps_dry_run_evidence_matches_the_translation_in_the_code():
    """`check_c_dry_run.json` is what the dry run reported from the production VPS network on 2026-10-06 (provenance: LIVE_VENDOR). Every translation it
    recorded must be the one the code produces today, and the four production share classes must all resolve to a full sector."""
    import json
    path = f"{ROOT}/docs/research/evidence/production_readiness_2026-10-06/check_c_dry_run.json"
    cases = {c["symbol"]: c for c in json.load(open(path, encoding="utf-8"))}
    for sym, c in cases.items():
        vs = c["vendor_symbol"]
        if vs["status"] == "refused":
            with pytest.raises(V.UnsupportedSymbolFormat):
                V.to_yfinance(sym)
        else:
            assert V.to_yfinance(sym).as_dict() == {k: vs[k] for k in ("canonical", "request", "status", "rule")}
        assert c["writes"] is False
    for sym in PRODUCTION_FORMS:
        assert cases[sym]["outcome"]["response_state"] == "sector" and cases[sym]["vendor_symbol"]["request"] == PRODUCTION_FORMS[sym]
    assert cases["ABC.WS"]["vendor_symbol"]["status"] == "refused"
