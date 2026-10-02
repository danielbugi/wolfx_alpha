"""Event model and source interface: PIT rules, the ML gate, derived surprise, and the append-only store. A fake source stands in for a
vendor -- there is no collector and no vendor in this repository."""
from datetime import date, datetime, timedelta, timezone

import pytest

import mi_samples  # noqa: F401  (path setup)
from mi_fixtures import conn, connect, mi_env  # noqa: F401  (explicit fixtures: no top-level `conftest` name that could shadow research/tests)
from market_intelligence import events as E
from market_intelligence import store

UTC = timezone.utc
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def draft(**kw):
    base = dict(event_key="AAA|earnings|2026Q3", symbol="AAA", event_type="earnings_scheduled", event_time=date(2026, 10, 20),
                status="scheduled", source="fake", source_ref="r1", known_at_basis="ingested", pit_grade="B")
    base.update(kw)
    return E.EventDraft(**base)


class FakeSource:
    """A source that can only prove its own ingestion time."""
    name = "fake"
    known_at_basis = "ingested"

    def __init__(self, claim=None):
        self.claim = claim

    def fetch(self, since, until):
        return [E.RawEvent("fake", "r1", {"symbol": "AAA"})]

    def normalise(self, raw):
        if self.claim == "vendor":
            return draft(known_at_basis="vendor_published", pit_grade="A", published_at=T0)
        return draft()


def test_fake_source_satisfies_the_interface():
    assert isinstance(FakeSource(), E.CatalystSource)


def test_a_source_cannot_claim_a_better_known_at_than_it_declared():
    src = FakeSource(claim="vendor")
    with pytest.raises(E.EventValidationError, match="stronger"):
        E.normalise_checked(src, src.fetch(date(2026, 10, 1), date(2026, 10, 2))[0])
    ok = FakeSource()
    assert E.normalise_checked(ok, ok.fetch(date(2026, 10, 1), date(2026, 10, 2))[0]).known_at_basis == "ingested"


@pytest.mark.parametrize("kw,match", [
    (dict(event_type="rumour"), "event_type"),
    (dict(status="maybe"), "status"),
    (dict(session_timing="NOON"), "session_timing"),
    (dict(known_at_basis="guessed"), "known_at_basis"),
    (dict(known_at_basis="unknown", pit_grade="B"), "grade"),                               # unknown basis must be grade X
    (dict(known_at_basis="ingested", pit_grade="X"), "grade"),
    (dict(known_at_basis="vendor_published", pit_grade="A"), "published_at"),               # vendor basis needs a published_at
    (dict(known_at_basis="vendor_published", pit_grade="A", published_at=datetime(2026, 10, 1)), "timezone"),
    (dict(eps_actual=1.0), "actual"),                                                       # actual on a scheduled event
    (dict(provenance="reconstructed"), "reconstruction_basis"),
    (dict(provenance="observed", reconstruction_basis="x"), "reconstruction_basis"),
    (dict(source=""), "required"),
])
def test_validation_refuses_pit_violations(kw, match):
    with pytest.raises(E.EventValidationError, match=match):
        E.validate(draft(**kw))


def test_an_unprovable_known_at_is_represented_as_unknown_not_fabricated():
    d = E.validate(draft(known_at_basis="unknown", pit_grade="X"))
    assert d.known_at_basis == "unknown" and d.published_at is None


# ---- visibility / ML gate -----------------------------------------------------------------------------------------------
def rev(key="k", revision=1, known_at=T0, basis="ingested", grade="B", prov="observed", status="scheduled", eps_actual=None):
    return dict(event_key=key, revision=revision, known_at=known_at, known_at_basis=basis, pit_grade=grade, provenance=prov,
                status=status, eps_actual=eps_actual)


def test_visible_as_of_returns_the_latest_revision_known_by_the_cutoff():
    rows = [rev(revision=1, known_at=T0), rev(revision=2, known_at=T0 + timedelta(days=2), status="revised")]
    assert [r["revision"] for r in E.visible_as_of(rows, T0 + timedelta(days=1))] == [1]
    assert [r["revision"] for r in E.visible_as_of(rows, T0 + timedelta(days=3))] == [2]
    assert E.visible_as_of(rows, T0 - timedelta(seconds=1)) == []


def test_unknown_known_at_is_never_visible():
    rows = [rev(known_at=None, basis="unknown", grade="X")]
    assert E.visible_as_of(rows, T0 + timedelta(days=365)) == []


def test_cutoff_must_be_timezone_aware():
    with pytest.raises(ValueError):
        E.visible_as_of([], datetime(2026, 10, 1))
    with pytest.raises(ValueError):
        E.ml_view([], datetime(2026, 10, 1))


def test_ml_view_admits_only_observed_grade_a_or_b_and_explains_the_rest():
    rows = [rev("a", grade="A", basis="vendor_published"), rev("b"), rev("c", grade="C", basis="vendor_published"),
            rev("d", prov="reconstructed"), rev("x", known_at=None, basis="unknown", grade="X")]
    v = E.ml_view(rows, T0 + timedelta(days=1))
    assert sorted(r["event_key"] for r in v.rows) == ["a", "b"]
    assert v.excluded == {"reconstructed": 1, "grade_not_ml_eligible": 1}


def test_ml_view_has_no_override_parameter():
    import inspect
    assert list(inspect.signature(E.ml_view).parameters) == ["revisions", "cutoff"]


@pytest.mark.parametrize("row", [
    rev(known_at=None, basis="unknown", grade="X"),
    rev(grade="X"),
    rev(grade="C", basis="vendor_published"),
    rev(prov="reconstructed"),
])
def test_assert_ml_eligible_refuses_rows_without_pit_provenance(row):
    with pytest.raises(E.EventValidationError):
        E.assert_ml_eligible(row)


def test_assert_ml_eligible_accepts_an_observed_grade_b_row():
    E.assert_ml_eligible(rev())


def test_surprise_is_derived_and_never_invents_a_value():
    assert E.surprise(1.1, 1.0)["abs"] == pytest.approx(0.1) and E.surprise(1.1, 1.0)["pct"] == pytest.approx(10.0)
    assert E.surprise(-0.5, -1.0)["pct"] == pytest.approx(50.0)                  # relative to |estimate|
    assert E.surprise(1.0, 0.0) == {"abs": 1.0, "pct": None}
    assert E.surprise(None, 1.0) == {"abs": None, "pct": None}
    assert E.surprise(1.0, None) == {"abs": None, "pct": None}


def test_same_content_hashes_equal_and_a_changed_estimate_does_not():
    assert draft().payload_hash() == draft().payload_hash()
    assert draft().payload_hash() != draft(eps_estimate=1.0).payload_hash()


# ---- append-only store (real Postgres) ----------------------------------------------------------------------------------
def test_append_is_idempotent_and_revisions_increment(conn):
    cur = conn.cursor()
    a = store.append_event_revision(cur, draft(), declared_basis="ingested")
    again = store.append_event_revision(cur, draft(), declared_basis="ingested")
    assert a.created and not again.created and again.id == a.id
    b = store.append_event_revision(cur, draft(status="reported", eps_estimate=1.0, eps_actual=1.2))
    assert b.created
    rows = store.get_event_revisions(cur)
    assert [r["revision"] for r in rows] == [1, 2] and rows[1]["eps_actual"] == pytest.approx(1.2)
    assert rows[0]["ingested_at"] == rows[0]["known_at"]                                   # stamped by the database for basis 'ingested'
    assert isinstance(rows[0]["known_at"], datetime)


def test_the_store_never_supplies_known_at_for_the_ingested_basis_and_keeps_unknown_null(conn):
    cur = conn.cursor()
    store.append_event_revision(cur, draft(event_key="u1", known_at_basis="unknown", pit_grade="X"))
    row = store.get_event_revisions(cur, ["u1"])[0]
    assert row["known_at"] is None and row["pit_grade"] == "X"
    assert E.visible_as_of([row], datetime.now(UTC)) == []
    assert E.ml_view([row], datetime.now(UTC)).rows == []


def test_a_vendor_published_event_round_trips_with_its_own_time(conn):
    cur = conn.cursor()
    pub = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    store.append_event_revision(cur, draft(event_key="v1", known_at_basis="vendor_published", pit_grade="A", published_at=pub))
    row = store.get_event_revisions(cur, ["v1"])[0]
    assert row["known_at"] == pub and row["published_at"] == pub
    assert [r["event_key"] for r in E.ml_view([row], pub + timedelta(seconds=1)).rows] == ["v1"]
    assert E.ml_view([row], pub - timedelta(seconds=1)).rows == []


def test_event_identity_cannot_be_redefined(conn):
    cur = conn.cursor()
    store.append_event_revision(cur, draft())
    with pytest.raises(E.EventValidationError, match="different"):
        store.append_event_revision(cur, draft(symbol="BBB", status="confirmed"))


def test_market_wide_events_have_a_null_symbol_and_reconstructed_events_are_hidden_by_default(conn):
    cur = conn.cursor()
    store.append_event_revision(cur, draft(event_key="m1", symbol=None, event_type="regulatory"))
    store.append_event_revision(cur, draft(event_key="r1", provenance="reconstructed", reconstruction_basis="back-filled from filings"))
    assert [r["event_key"] for r in store.get_event_revisions(cur)] == ["m1"]
    assert store.get_event_revisions(cur)[0]["symbol"] is None
    assert {r["event_key"] for r in store.get_event_revisions(cur, include_reconstructed=True)} == {"m1", "r1"}
