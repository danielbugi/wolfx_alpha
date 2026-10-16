"""CIK -> symbol links (pure): parsing the SEC ticker file, the append-only update plan (new / ended / unchanged), refusal of truncated or stale files,
reconstructed links kept distinct, and the as-of resolution rules (late links and late closures are invisible to an earlier cutoff)."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import mi_samples  # noqa: F401  (path setup)
from market_intelligence import identifier_links as il

UTC = timezone.utc
D1, D2, D3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)


def tickers(pairs, extra=None):
    """A syntactically real SEC ticker file with `pairs` = [(cik, ticker)] plus ~1,200 filler pairs so the size sanity check passes."""
    doc = {str(i): {"cik_str": 1_000_000 + i, "ticker": f"F{i}", "title": "filler"} for i in range(1200)}
    for j, (c, t) in enumerate(pairs):
        doc[f"p{j}"] = {"cik_str": c, "ticker": t, "title": "x"}
    doc.update(extra or {})
    return json.dumps(doc).encode()


def open_links_from(plan_or_pairs, basis="observed", start=1):
    out = []
    for i, (p, b, vf) in enumerate(plan_or_pairs):
        out.append(il.OpenLink(start + i, p.cik, p.symbol, b, vf))
    return out


# ---------------------------------------------------------------- parsing
def test_a_real_shaped_file_parses_and_normalises_symbols():
    p = il.parse_company_tickers(tickers([(320193, "aapl"), (1067983, "BRK-B")]))
    assert il.Pair(320193, "AAPL") in p.pairs and il.Pair(1067983, "BRK-B") in p.pairs and not p.rejected


def test_malformed_entries_are_rejected_with_a_reason_and_never_repaired():
    p = il.parse_company_tickers(tickers([], extra={"a": {"cik_str": "abc", "ticker": "X"}, "b": {"cik_str": 5, "ticker": ""}, "c": {"cik_str": -1, "ticker": "Y"},
                                                    "d": "nope", "e": {"cik_str": True, "ticker": "Z"}, "f": {"cik_str": 7, "ticker": "bad ticker!"}}))
    assert len(p.rejected) == 6 and p.counters["rejected"] == 6 and len(p.pairs) == 1200


@pytest.mark.parametrize("body", [b"not json", b"[]", b"{}", b'"x"'])
def test_a_document_that_is_not_the_ticker_file_is_refused_as_a_whole(body):
    with pytest.raises(il.IdentifierError):
        il.parse_company_tickers(body)


def test_duplicate_pairs_collapse_and_symbols_shared_by_several_ciks_are_reported():
    p = il.parse_company_tickers(tickers([(11, "DUP"), (11, "DUP"), (22, "DUP")]))
    assert sum(1 for x in p.pairs if x.symbol == "DUP") == 2 and p.counters["duplicate_pair"] == 1 and p.symbols_on_several_ciks == {"DUP": [11, 22]}


# ---------------------------------------------------------------- planning
def test_the_first_file_creates_observed_links_valid_from_its_own_date_and_nothing_else():
    plan = il.plan_update(il.parse_company_tickers(tickers([(320193, "AAPL")])), D1, [])
    assert len(plan.new_links) == 1201 + 0 and plan.closures == [] and all(b == "observed" and vf == D1 for _, b, vf in plan.new_links)


def test_reconstruction_is_explicit_labelled_and_has_no_start_date():
    parsed = il.parse_company_tickers(tickers([(320193, "AAPL")]))
    assert not any(b == "reconstructed" for _, b, _ in il.plan_update(parsed, D1, []).new_links)
    plan = il.plan_update(parsed, D1, [], reconstruct=True)
    rec = [(p, vf) for p, b, vf in plan.new_links if b == "reconstructed"]
    assert len(rec) == 1201 and all(vf is None for _, vf in rec)


def test_applying_the_same_file_again_plans_nothing_new():
    parsed = il.parse_company_tickers(tickers([(320193, "AAPL")]))
    first = il.plan_update(parsed, D1, [])
    again = il.plan_update(parsed, D1, open_links_from(first.new_links))
    assert again.new_links == [] and again.closures == [] and again.unchanged == 1201


def test_a_ticker_change_ends_the_old_pair_and_opens_the_new_one_without_editing_anything():
    first = il.plan_update(il.parse_company_tickers(tickers([(555, "OLD")])), D1, [])
    opened = open_links_from(first.new_links)
    second = il.plan_update(il.parse_company_tickers(tickers([(555, "NEW")])), D2, opened, latest_applied=D1)
    assert [(l.cik, l.symbol) for l in second.closures] == [(555, "OLD")]
    assert [(p.cik, p.symbol, vf) for p, b, vf in second.new_links] == [(555, "NEW", D2)]


def test_a_symbol_reused_by_another_company_is_a_new_pair_not_a_continuation():
    opened = open_links_from(il.plan_update(il.parse_company_tickers(tickers([(1, "REUSE")])), D1, []).new_links)
    second = il.plan_update(il.parse_company_tickers(tickers([(2, "REUSE")])), D2, opened)
    assert [(l.cik, l.symbol) for l in second.closures] == [(1, "REUSE")] and [(p.cik, p.symbol) for p, _, _ in second.new_links] == [(2, "REUSE")]


def _file(n):
    return json.dumps({str(i): {"cik_str": 1_000_000 + i, "ticker": f"F{i}", "title": "f"} for i in range(n)}).encode()


def test_a_truncated_file_cannot_end_the_links_and_nothing_is_planned():
    opened = open_links_from(il.plan_update(il.parse_company_tickers(_file(2000)), D1, []).new_links)
    ok = il.plan_update(il.parse_company_tickers(_file(1800)), D2, opened)                       # 200 of 2000 gone = 10%: an unusually busy day, still allowed
    assert len(ok.closures) == 200
    with pytest.raises(il.IdentifierError, match="truncated"):
        il.plan_update(il.parse_company_tickers(_file(1500)), D2, opened)                          # 25% gone: refused, nothing planned


def test_a_file_too_small_to_be_the_sec_file_is_refused():
    tiny = il.parse_company_tickers(json.dumps({"0": {"cik_str": 1, "ticker": "A"}}).encode())
    with pytest.raises(il.IdentifierError, match="usable pairs"):
        il.plan_update(tiny, D1, [])


def test_a_snapshot_older_than_one_already_applied_is_refused():
    with pytest.raises(il.IdentifierError, match="older"):
        il.plan_update(il.parse_company_tickers(tickers([])), D1, [], latest_applied=D2)


# ---------------------------------------------------------------- reading: as-of resolution
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def link(i, cik, sym, basis="observed", vf=D1, rec=T0, end=None, end_rec=None):
    return il.StoredLink(i, cik, sym, basis, vf if basis == "observed" else None, rec, end, end_rec)


def test_a_link_recorded_after_the_cutoff_does_not_exist_for_that_decision():
    ls = [link(1, 7, "AAA", rec=T0 + timedelta(hours=3))]
    assert il.symbols_as_of(ls, 7, D2, T0 + timedelta(hours=1)) == ([], "unmapped")
    assert il.symbols_as_of(ls, 7, D2, T0 + timedelta(hours=4)) == (["AAA"], "observed")


def test_an_observed_link_does_not_cover_filings_dated_before_it_began():
    ls = [link(1, 7, "AAA", vf=D2)]
    assert il.symbols_as_of(ls, 7, D1, T0 + timedelta(days=5))[1] == "unmapped"


def test_a_closure_recorded_after_the_cutoff_is_unknown_to_that_decision_and_one_recorded_before_it_ends_the_mapping():
    ls = [link(1, 7, "OLD", end=D3, end_rec=T0 + timedelta(days=4))]
    assert il.symbols_as_of(ls, 7, D3, T0 + timedelta(days=3)) == (["OLD"], "observed")           # the closure is not recorded yet
    assert il.symbols_as_of(ls, 7, D3, T0 + timedelta(days=5)) == ([], "unmapped")                # recorded, and the filing is on/after absent_on
    assert il.symbols_as_of(ls, 7, D2, T0 + timedelta(days=5)) == (["OLD"], "observed")           # a filing dated before absent_on keeps the old symbol


def test_a_ticker_change_maps_each_filing_date_to_the_symbol_of_its_time():
    ls = [link(1, 7, "OLD", end=D3, end_rec=T0 + timedelta(days=4)), link(2, 7, "NEW", vf=D3, rec=T0 + timedelta(days=4))]
    cut = T0 + timedelta(days=6)
    assert il.symbols_as_of(ls, 7, D2, cut) == (["OLD"], "observed") and il.symbols_as_of(ls, 7, D3, cut) == (["NEW"], "observed")


def test_reconstructed_mapping_is_reported_as_such_and_never_upgraded_to_observed():
    ls = [link(1, 7, "AAA", basis="reconstructed")]
    assert il.symbols_as_of(ls, 7, D1, T0 + timedelta(days=1)) == (["AAA"], "reconstructed")
    ls.append(link(2, 7, "AAA", vf=D1))
    assert il.symbols_as_of(ls, 7, D2, T0 + timedelta(days=1)) == (["AAA"], "reconstructed")      # one reconstructed hit keeps the whole answer reconstructed


def test_a_filer_with_several_listed_classes_maps_to_all_of_them():
    """Found by the real-SEC dry run: GOOG / GOOGL, BRK-A / BRK-B. An issuer-level filing concerns every class; it is not 'ambiguous'."""
    ls = [link(1, 7, "GOOGL"), link(2, 7, "GOOG")]
    assert il.symbols_as_of(ls, 7, D2, T0 + timedelta(days=1)) == (["GOOG", "GOOGL"], "observed")


def test_a_symbol_claimed_by_two_filers_on_the_same_date_is_reported_so_nothing_is_attached_to_it():
    ls = [link(1, 7, "REUSE"), link(2, 8, "REUSE", vf=D2)]
    assert il.claimants(ls, "REUSE", D1, T0 + timedelta(days=1)) == [7]
    assert il.claimants(ls, "REUSE", D2, T0 + timedelta(days=1)) == [7, 8]                      # the old owner's closure is not recorded yet: both claim it
    closed = [link(1, 7, "REUSE", end=D2, end_rec=T0 + timedelta(hours=5)), link(2, 8, "REUSE", vf=D2, rec=T0 + timedelta(hours=5))]       # one ticker file ends one and opens the other
    assert il.claimants(closed, "REUSE", D2, T0 + timedelta(days=1)) == [8]                     # once the closure is recorded the new owner is alone
    assert il.claimants(closed, "REUSE", D2, T0 + timedelta(hours=1)) == [7]                    # ... and a decision before the closure was recorded sees only what it knew


def test_share_class_spellings_are_one_listing():
    assert il.canonical_symbol("brk.b") == il.canonical_symbol("BRK-B") == il.canonical_symbol("BRK/B") == "BRK-B"
    assert il.symbols_as_of([link(1, 7, "BRK-B"), link(2, 7, "BRK.B")], 7, D2, T0 + timedelta(days=1)) == (["BRK-B"], "observed")
    assert il.claimants([link(1, 7, "BRK-B"), link(2, 8, "BRK.B")], "brk/b", D2, T0 + timedelta(days=1)) == [7, 8]
