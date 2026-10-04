"""Vendor trial harness: behaves correctly against deliberately good and deliberately dishonest fake providers. No vendor, no credentials, no network."""
import inspect
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

import mi_samples  # noqa: F401
from market_intelligence import vendor_trial as V

UTC = timezone.utc
T0 = datetime(2026, 10, 1, 12, tzinfo=UTC)
POLICY = V.TrialPolicy()
QUARTERS = [date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30), date(2024, 12, 31), date(2025, 3, 31), date(2025, 6, 30),
            date(2025, 9, 30), date(2025, 12, 31)]


def _print_time(sym, per):                        # SEC acceptance 21:30Z = after the close in both EDT and EST
    y, m = (per.year + 1, 1) if per.month == 12 else (per.year, per.month + 1)
    return datetime(y, m, 20, 21, 30, tzinfo=UTC)


def samples(n_symbols=25):
    out = []
    for i in range(n_symbols):
        for q in QUARTERS:
            tags = ()
            if q == QUARTERS[0]:
                tags = (V.TAGS[i % 4],) if i < 4 else ()
            pt = _print_time("x", q)
            out.append(V.Sample(f"S{i:02d}", q, as_of_dates=(pt - timedelta(days=30), pt - timedelta(days=7), pt - timedelta(days=1)), tags=tags))
    return out


def reference(ss, own=True):
    edgar = {(s.symbol, s.period): _print_time(s.symbol, s.period) for s in ss}
    xbrl = {(s.symbol, s.period, "eps_actual"): 1.25 for s in ss}
    first_seen = {}
    if own:
        for s in ss:
            for f in V.ESTIMATE_FIELDS[:1]:
                first_seen[(s.symbol, s.period, f)] = [(a - timedelta(hours=1), Honest.estimate(s, a)) for a in s.as_of_dates]
    return V.Reference(edgar, xbrl, first_seen)


class Honest:
    provider_id = "fake_good"
    later_than_edgar = False
    restate_by = 0.0
    constant_history = False
    asof_supported = True
    raises = False
    label_wrong = False

    @staticmethod
    def estimate(s, a):                             # a revision path: value depends on the as-of date
        return round(1.0 + (a - datetime(2024, 1, 1, tzinfo=UTC)).days % 17 / 100, 4)

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def report_time(self, symbol, period):
        at = _print_time(symbol, period) + (timedelta(hours=3) if self.later_than_edgar else timedelta(0))
        return V.ReportTime(at, "BMO" if self.label_wrong else "AMC")

    def current_value(self, symbol, period, field):
        if self.raises:
            raise TimeoutError("boom")
        if field == "eps_actual":
            return V.Value(1.25 + self.restate_by, "gaap")
        if field == "revenue_actual":
            return V.Value(100.0, "adjusted")
        if field in V.GUIDANCE_FIELDS:
            return None
        return V.Value(1.2 + self.restate_by, "unknown")

    def value_as_of(self, symbol, period, field, as_of):
        if not self.asof_supported:
            raise V.AsOfUnsupported()
        if self.constant_history:
            return 1.23
        return self.estimate(None, as_of) if field in V.ESTIMATE_FIELDS else None


def tick():
    c = {"t": 0.0}

    def clock():
        return T0

    def perf():
        c["t"] += 0.01
        return c["t"]
    return clock, perf


GOOD_OP = V.Operational(sustained_rps=8, universe_size=3000, outage_behaviour="error_codes", pagination_ok=True, auth_documented=True)
GOOD_LIC = V.Licence({q: "permitted" for q in V.LICENCE_QUESTIONS}, document_ref="terms-2026-10", recorded_by="owner")


def pull(adapter, ss, later_adapter=None, gap_days=8):
    clock, perf = tick()
    a = V.collect_snapshot(adapter, ss, clock, perf)
    b = None
    if later_adapter is not None:
        b = V.collect_snapshot(later_adapter, ss, lambda: T0 + timedelta(days=gap_days), perf)
    return a, b


def run(adapter, later_adapter="same", op=GOOD_OP, lic=GOOD_LIC, ss=None, ref=None, gap_days=8):
    ss = ss or samples()
    la = adapter if later_adapter == "same" else later_adapter
    a, b = pull(adapter, ss, la, gap_days)
    return V.evaluate(a, b, ss, ref or reference(ss), op, lic)


# ---------------------------------------------------------------- the honest provider earns A; everything else is earned down
def test_an_honest_as_of_stable_provider_is_graded_a_where_evidence_exists_and_x_where_it_does_not():
    rep = run(Honest())
    assert rep.check("report_time").status == V.PASS
    for f in V.ESTIMATE_FIELDS[:1]:
        assert rep.grade(f) == "A", rep.check(f"as_of:{f}")
    assert rep.grade("eps_actual") == "A"
    assert rep.grade("guidance_eps") == "X"                       # never delivered anything: unproven, not good
    assert rep.grade("revenue_consensus") == "X"                  # no reference first-seen data for it: cannot confirm as-of
    assert rep.adoptable_for_research and rep.adoptable_for_publication and rep.blockers == ()


def test_a_provider_that_reports_later_than_the_sec_fails_report_time_and_every_field_is_x():
    rep = run(Honest(later_than_edgar=True))
    assert rep.check("report_time").status == V.FAIL and all(g.grade == "X" for g in rep.grades) and not rep.adoptable_for_research


def test_a_wrong_bmo_amc_label_fails_report_time():
    assert run(Honest(label_wrong=True)).check("report_time").status == V.FAIL


def test_a_constant_history_is_restated_and_graded_c():
    rep = run(Honest(constant_history=True))
    c = rep.check("as_of:eps_consensus")
    assert c.status == V.FAIL and "restated" in c.detail["why"] and rep.grade("eps_consensus") == "C"


def test_a_provider_that_cannot_answer_as_of_fails_instead_of_being_assumed_fine():
    rep = run(Honest(asof_supported=False))
    c = rep.check("as_of:eps_consensus")
    assert c.status == V.FAIL and rep.grade("eps_consensus") == "C" and c.detail["unsupported_keys"] > 0


def test_a_silent_change_between_two_pulls_is_a_restatement_and_graded_c():
    rep = run(Honest(), later_adapter=Honest(restate_by=0.05))
    c = rep.check("restatement:eps_actual")
    assert c.status == V.FAIL and c.detail["silently_changed"] > 0 and rep.grade("eps_actual") == "C"


def test_without_a_second_pull_or_with_too_short_a_gap_restatement_is_unproven_not_passed():
    assert run(Honest(), later_adapter=None).check("restatement:eps_actual").status == V.INCONCLUSIVE
    rep = run(Honest(), gap_days=2)
    assert rep.check("restatement:eps_actual").status == V.INCONCLUSIVE and rep.grade("eps_actual") == "X" and not rep.adoptable_for_research


def test_too_little_own_history_makes_as_of_inconclusive_not_a_pass():
    ss = samples()
    rep = run(Honest(), ss=ss, ref=reference(ss, own=False))
    assert rep.check("as_of:eps_consensus").status == V.INCONCLUSIVE and rep.grade("eps_consensus") == "X"


def test_a_small_or_incomplete_sample_cannot_pass_and_is_listed_as_a_blocker():
    ss = samples(3)                                              # 24 symbol-quarters, one tag category missing
    rep = run(Honest(), ss=ss)
    assert any("symbol-quarters" in b for b in rep.blockers) and any("tagged" in b for b in rep.blockers)
    assert rep.check("report_time").status == V.INCONCLUSIVE and not rep.adoptable_for_research


# ---------------------------------------------------------------- actuals, coverage, operational, licence
def test_actuals_are_compared_per_stated_basis_and_adjusted_is_not_forced_against_gaap():
    rep = run(Honest())
    d = rep.check("actuals").detail
    assert rep.check("actuals").status == V.PASS and d["by_basis"]["gaap"]["match"] == d["by_basis"]["gaap"]["n"] and d["reference"] == "sec_xbrl_gaap"
    wrong = run(Honest(restate_by=0.5), later_adapter=None)
    assert wrong.check("actuals").status == V.FAIL


def test_coverage_is_reported_per_field_and_missing_is_not_counted_as_present():
    c = run(Honest()).check("coverage")
    assert c.status == V.FAIL and c.detail["share"]["guidance_eps"] == 0.0 and "guidance_eps" in c.detail["below_policy"]
    assert c.detail["share"]["eps_actual"] == 1.0


def test_a_silent_empty_outage_fails_operational_and_unknowns_are_unproven():
    assert run(Honest(), op=replace(GOOD_OP, outage_behaviour="silent_empty")).check("operational").status == V.FAIL
    rep = run(Honest(), op=V.Operational())
    assert rep.check("operational").status == V.INCONCLUSIVE and any("operational" in b for b in rep.blockers) and not rep.adoptable_for_research
    with pytest.raises(V.TrialError):
        run(Honest(), op=replace(GOOD_OP, outage_behaviour="whatever"))


def test_unanswered_licence_blocks_adoption_even_when_the_data_is_perfect():
    rep = run(Honest(), lic=V.Licence({"store_history": "permitted"}, "doc", "owner"))
    assert not rep.adoptable_for_research and sum(b.startswith("licence unanswered") for b in rep.blockers) == 4
    rep2 = run(Honest(), lic=V.Licence(dict(GOOD_LIC.answers), None, None))
    assert not rep2.adoptable_for_research and any("document reference" in b for b in rep2.blockers)


def test_research_permission_is_separate_from_publication_permission():
    answers = {**GOOD_LIC.answers, "publish_public_channel": "prohibited"}
    rep = run(Honest(), lic=V.Licence(answers, "doc", "owner"))
    assert rep.adoptable_for_research and not rep.adoptable_for_publication
    assert run(Honest(), lic=V.Licence({**GOOD_LIC.answers, "derive_signals": "needs_negotiation"}, "doc", "owner")).adoptable_for_research is False
    with pytest.raises(V.TrialError):
        V.check_licence(V.Licence({"store_history": "maybe"}))


# ---------------------------------------------------------------- collection is honest about errors
def test_provider_exceptions_are_counted_and_never_become_no_data():
    ss = samples()
    a, _ = pull(Honest(raises=True), ss)
    assert a.errors == {"TimeoutError": len(ss) * len(V.ALL_FIELDS)} and a.current == {} and a.max_latency_s > 0
    rep = V.evaluate(a, None, ss, reference(ss), GOOD_OP, GOOD_LIC)
    assert all(g.grade == "X" for g in rep.grades) and rep.check("operational").detail["errors_seen"]


def test_the_report_and_snapshot_hashes_are_deterministic_and_content_sensitive():
    ss = samples()
    a1, b1 = pull(Honest(), ss, Honest())
    a2, b2 = pull(Honest(), ss, Honest())
    assert a1.digest() == a2.digest() and b1.digest() == b2.digest() and a1.digest() != b1.digest()
    r1 = V.evaluate(a1, b1, ss, reference(ss), GOOD_OP, GOOD_LIC)
    assert r1.report_hash() == V.evaluate(a2, b2, ss, reference(ss), GOOD_OP, GOOD_LIC).report_hash()
    assert r1.report_hash() != V.evaluate(a1, None, ss, reference(ss), GOOD_OP, GOOD_LIC).report_hash()
    c = pull(Honest(constant_history=True), ss)[0]
    assert c.digest() != a1.digest()


@pytest.mark.parametrize("bad", [V.Sample("A", date(2025, 3, 31), tags=("ipo",)), V.Sample("", date(2025, 3, 31))])
def test_malformed_samples_raise(bad):
    with pytest.raises(V.TrialError):
        V.validate_samples([bad])


def test_duplicate_samples_and_naive_as_of_dates_raise():
    s = V.Sample("A", date(2025, 3, 31))
    with pytest.raises(V.TrialError):
        V.validate_samples([s, s])
    with pytest.raises(V.TrialError):
        V.validate_samples([V.Sample("A", date(2025, 3, 31), as_of_dates=(datetime(2025, 3, 1),))])


def test_the_adapter_label_is_validated_and_two_providers_are_never_compared_as_one():
    class Bad(Honest):
        provider_id = "Vendor Name With Spaces"
    with pytest.raises(V.TrialError):
        V.collect_snapshot(Bad(), samples(), *tick())
    ss = samples()
    a = V.collect_snapshot(Honest(), ss, *tick())
    b = V.collect_snapshot(Honest(provider_id="other"), ss, lambda: T0 + timedelta(days=9), tick()[1])
    with pytest.raises(V.TrialError):
        V.check_restatement(a, b, POLICY, "eps_actual")


# ---------------------------------------------------------------- provider neutrality
def test_the_harness_names_no_vendor_and_carries_no_credential_field():
    src = inspect.getsource(V).lower()
    for name in ("factset", "lseg", "refinitiv", "benzinga", "wallstreethorizon", "wall street horizon", "zacks", "polygon", "alpha vantage",
                 "finnhub", "fmp", "tiingo", "bloomberg", "visible alpha"):
        assert name not in src, name
    fields = {f for cls in (V.TrialPolicy, V.Sample, V.Reference, V.Operational, V.Licence, V.Snapshot, V.TrialReport) for f in cls.__dataclass_fields__}
    assert not [f for f in fields if any(w in f for w in ("key", "secret", "token", "password", "credential"))]
