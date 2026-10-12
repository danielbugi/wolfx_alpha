"""Slice 12: the vendor request symbol is translated; the research identity is NOT. Through the real writer wiring (`FundamentalsUpdater.update_symbol` ->
real `_fetch_yfinance_raw` -> real `SectorRecorder` -> migration 31 on a throwaway Postgres schema). Only the network (`yfinance.Ticker`) and the fundamentals
upsert are stubbed.

Proven: `BRK.B` is asked of the vendor as `BRK-B`, yet every observation, poll, chain hash and history read is keyed `BRK.B`; nothing is ever stored under
the vendor spelling; two internal spellings of one vendor symbol keep two separate chains; a refused symbol makes no request and leaves a failed poll."""
import json
from datetime import date, datetime, timezone

import pytest

from conftest import sector_env  # noqa: F401  (explicit fixture: no top-level `conftest` name)
from data_updaters import fundamentals_updater as FU
from data_updaters import sector_history_recorder as R
from research.lab import dataset_reader as RD
from research.lab import sector_history as SH

YF = R.SRC_YFINANCE
HI, CUTOFF = date(2100, 1, 1), datetime(2100, 1, 1, tzinfo=timezone.utc)


class Fake:
    seen = []
    answers = {}

    def __init__(self, symbol):
        type(self).seen.append(symbol)
        self.info = type(self).answers.get(symbol, {"sector": "Financial Services", "quoteType": "EQUITY", "symbol": symbol,
                                                    "industry": "x", "a": 1, "b": 2, "c": 3})


@pytest.fixture
def world(monkeypatch, sector_env):
    import yfinance
    Fake.seen, Fake.answers = [], {}
    monkeypatch.setattr(yfinance, "Ticker", Fake)
    monkeypatch.setattr(FU.config, "data_provider", "yfinance", raising=False)
    monkeypatch.setenv(R.FLAG_ENV, "1")
    u = FU.FundamentalsUpdater()
    u.rate_limit_delay = 0
    u.upserts = []
    u.update_daily_fundamentals = lambda info, today: (u.upserts.append(info), True)[1]

    def run(run_id):
        u._sector_recorder = R.SectorRecorder(run_id=run_id, connect=sector_env[1], is_enabled=lambda: True)

    u.use_run = run
    u.connect = sector_env[1]
    return u


def q(world, sql, params=None):
    with world.connect() as c:
        cur = c.cursor()
        cur.execute(sql, params)
        out = cur.fetchall()
        c.rollback()
        return out


def history(world, symbols):
    with world.connect() as c:
        cur = c.cursor()
        rows = RD.history_rows(cur, symbols, YF, HI, CUTOFF)
        c.rollback()
        return rows


def test_the_vendor_is_asked_for_the_dashed_form_but_everything_is_stored_under_the_canonical_symbol(world):
    world.use_run("run-1")
    assert world.update_symbol("BRK.B") is True
    assert Fake.seen == ["BRK-B"]                                                           # the ONLY thing translated: the outbound request
    assert q(world, "SELECT DISTINCT symbol FROM sector_observation") == [("BRK.B",)]
    assert q(world, "SELECT DISTINCT symbol FROM sector_poll") == [("BRK.B",)]
    assert q(world, "SELECT count(*) FROM sector_observation WHERE symbol = 'BRK-B'") == [(0,)]
    assert q(world, "SELECT count(*) FROM sector_poll WHERE symbol = 'BRK-B'") == [(0,)]
    assert q(world, "SELECT seq, sector, change_kind, source FROM sector_observation") == [(1, "Financial Services", "first", YF)]
    assert len(world.upserts) == 1 and world.upserts[0]["symbol"] == "BRK.B"                 # the fundamentals row identity too


def test_the_hash_chain_is_computed_over_the_canonical_symbol_and_verifies(world):
    world.use_run("run-1")
    world.update_symbol("BRK.B")
    rows = history(world, ["BRK.B"])
    obs = [r for r in rows if r["row_kind"] == "observation"]
    assert len(obs) == 1 and obs[0]["symbol"] == "BRK.B" and SH.verify_chain(obs) == []
    # the stored hash is the hash of the CANONICAL identity: recomputing it with the vendor spelling cannot reproduce it
    spoofed = dict(obs[0], symbol="BRK-B")
    assert SH.row_hash(spoofed) != obs[0]["value_hash"] and SH.verify_chain([spoofed]) != []
    assert history(world, ["BRK-B"]) == []                                                    # the dataset read for the vendor spelling finds nothing


def test_the_stored_payload_projection_carries_no_symbol_at_all(world):
    world.use_run("run-1")
    world.update_symbol("BRK.B")
    (payload,) = q(world, "SELECT raw_payload FROM sector_observation")[0]
    assert set(payload) <= {"sector", "quote_type"} and "BRK" not in json.dumps(payload)


def test_a_later_refresh_confirms_the_head_of_the_canonical_chain(world):
    world.use_run("run-1")
    world.update_symbol("BRK.B")
    world.use_run("run-2")
    world.update_symbol("BRK.B")
    assert Fake.seen == ["BRK-B", "BRK-B"]
    assert q(world, "SELECT count(*) FROM sector_observation") == [(1,)]
    assert q(world, "SELECT symbol, run_id, chain_effect FROM sector_poll ORDER BY id") == [("BRK.B", "run-1", "created_observation"),
                                                                                         ("BRK.B", "run-2", "confirmed_head")]
    assert sorted(r["row_kind"] for r in history(world, ["BRK.B"])) == ["confirmation", "observation"]


def test_two_internal_spellings_of_one_vendor_symbol_keep_two_separate_chains(world):
    world.use_run("run-1")
    world.update_symbol("BRK.B")
    world.update_symbol("BRK/B")
    assert Fake.seen == ["BRK-B", "BRK-B"]
    assert q(world, "SELECT symbol, seq FROM sector_observation ORDER BY symbol") == [("BRK.B", 1), ("BRK/B", 1)]
    for sym in ("BRK.B", "BRK/B"):
        obs = [r for r in history(world, [sym]) if r["row_kind"] == "observation"]
        assert len(obs) == 1 and SH.verify_chain(obs) == []


@pytest.mark.parametrize("canonical,request_symbol", [("BF.B", "BF-B"), ("BRK/A", "BRK-A")])
def test_every_known_production_share_class_is_recorded_under_its_own_canonical_symbol(world, canonical, request_symbol):
    world.use_run("run-1")
    assert world.update_symbol(canonical) is True
    assert Fake.seen == [request_symbol]
    assert q(world, "SELECT DISTINCT symbol FROM sector_observation") == [(canonical,)]


def test_a_plain_symbol_is_requested_and_stored_exactly_as_before(world):
    world.use_run("run-1")
    assert world.update_symbol("AAPL") is True
    assert Fake.seen == ["AAPL"] and q(world, "SELECT DISTINCT symbol FROM sector_observation") == [("AAPL",)]


def test_an_unsupported_symbol_makes_no_request_and_leaves_a_failed_poll_under_its_own_identity(world):
    world.use_run("run-1")
    assert world.update_symbol("ABC.WS") is False                                          # ingestion fails exactly as any failed fetch does
    assert Fake.seen == []                                                                  # nothing was guessed and nothing was asked
    assert q(world, "SELECT count(*) FROM sector_observation") == [(0,)]
    assert q(world, "SELECT symbol, source, response_state, chain_effect, failure_reason FROM sector_poll") == \
        [("ABC.WS", YF, "request_failed", "none", "unsupported_symbol_format")]


def test_a_vendor_that_answers_the_dashed_form_with_no_sector_never_becomes_a_no_sector_state(world):
    """An EQUITY with no sector key is a degraded answer, not a no-sector instrument (production VPS, raw `BRK.B`: 15 keys, quoteType EQUITY, no sector)."""
    Fake.answers["BRK-B"] = {"quoteType": "EQUITY", "symbol": "BRK-B", "a": 1, "b": 2, "c": 3}
    world.use_run("run-1")
    world.update_symbol("BRK.B")
    assert q(world, "SELECT count(*) FROM sector_observation") == [(0,)]
    assert q(world, "SELECT symbol, response_state, failure_reason FROM sector_poll") == [("BRK.B", "invalid_response", "operating_company_sector_absent")]
