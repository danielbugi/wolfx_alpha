"""The authoritative writer as it is actually wired: `FundamentalsUpdater.update_symbol` -> `SectorRecorder` -> migration 31 (real Postgres, throwaway
schema). Only the fundamentals UPSERT (`update_daily_fundamentals`) and the vendor call are stubbed: the recorder, its SAVEPOINT and the triggers are real.

Proven: flag OFF is a no-op; flag ON records; a failing history write never fails ingestion and is logged + counted; the history is recorded
independently of the upsert's outcome; duplicate / retry is idempotent; a vendor failure is a poll that never touches the chain; inferred no-sector
(yfinance only) vs the ambiguous Tiingo None; same value = poll only; changed value = chained row."""
from contextlib import contextmanager

import psycopg2
import pytest

from conftest import sconn, sector_env  # noqa: F401  (explicit fixtures: no top-level `conftest` name)
from data_updaters import fundamentals_updater as FU
from data_updaters import sector_history_recorder as R

INFO = lambda sector: {"sector": sector, "industry": "x", "a": 1, "b": 2, "c": 3, "market_cap": 5}   # noqa: E731  (>= 5 keys: a real yfinance info)
YF, TG = R.SRC_YFINANCE, R.SRC_TIINGO
ETF_META = {"quote_type": "ETF", "n_keys": 6, "sector_key_present": False}
RAW_EQUITY = lambda sector: {"sector": sector, "quoteType": "EQUITY", "industry": "x", "a": 1, "b": 2, "c": 3}   # noqa: E731


class Harness:
    def __init__(self, monkeypatch, connect):
        monkeypatch.setenv(R.FLAG_ENV, "1")
        self.u = FU.FundamentalsUpdater()
        self.u.rate_limit_delay = 0
        self.connect = connect
        self.upserts = []
        self.upsert_result = True
        self.fetch = lambda symbol: INFO("Technology")
        self.vendor = YF
        self.meta = None                                                                            # the raw-answer evidence `fetch_company_info` would capture
        self.yf_probe = lambda symbol: RAW_EQUITY("Technology")                                     # the authoritative probe made when Tiingo served the symbol
        self.u._fetch_yfinance_raw = lambda symbol: self.yf_probe(symbol)
        self.u.update_daily_fundamentals = self._upsert
        self.u.fetch_company_info = self._fetch

    def _upsert(self, info, today):
        self.upserts.append(info)
        if isinstance(self.upsert_result, BaseException):
            raise self.upsert_result
        return self.upsert_result

    def _fetch(self, symbol):
        self.u._sector_vendor = self.vendor
        self.u._sector_meta = self.meta
        return self.fetch(symbol)

    def recorder(self, run_id="run-1", connect=None):
        self.u._sector_recorder = R.SectorRecorder(run_id=run_id, connect=connect or self.connect, is_enabled=lambda: True)
        return self.u._sector_recorder


@pytest.fixture
def h(monkeypatch, sector_env):
    return Harness(monkeypatch, sector_env[1])


def rows(sector_env, sql, params=None):
    with sector_env[1]() as c:
        cur = c.cursor()
        cur.execute(sql, params)
        out = cur.fetchall()
        c.rollback()
        return out


def obs(sector_env, symbol="AAA"):
    return rows(sector_env, "SELECT seq, sector, change_kind, source, run_id, writer FROM sector_observation WHERE symbol = %s ORDER BY seq", (symbol,))


def polls(sector_env, symbol="AAA"):
    return rows(sector_env, "SELECT run_id, response_state, chain_effect, failure_reason FROM sector_poll WHERE symbol = %s ORDER BY id", (symbol,))


# ------------------------------------------------------------------ flag
def test_the_flag_defaults_to_off_and_only_exactly_one_turns_it_on(monkeypatch):
    for v in (None, "", "0", "true", "yes", "on", "True", " 1", "1 ", "2"):
        if v is None:
            monkeypatch.delenv(R.FLAG_ENV, raising=False)
        else:
            monkeypatch.setenv(R.FLAG_ENV, v)
        assert R.enabled() is False, v
    monkeypatch.setenv(R.FLAG_ENV, "1")
    assert R.enabled() is True


def test_flag_off_is_a_no_op_for_ingestion_and_for_the_history(monkeypatch, sector_env):
    monkeypatch.delenv(R.FLAG_ENV, raising=False)
    h = Harness(monkeypatch, sector_env[1])
    monkeypatch.delenv(R.FLAG_ENV, raising=False)
    assert h.u.update_symbol("AAA") is True and len(h.upserts) == 1
    assert h.u._sector_recorder is None and obs(sector_env) == [] and polls(sector_env) == []
    h.u.get_symbols_to_update = lambda limit=None: ["AAA"]
    result = h.u.run_fundamentals_update(symbols=["AAA"])
    assert result["symbols_successful"] == 1 and "sector_history" not in result


# ------------------------------------------------------------------ history write succeeds / ingestion unchanged
def test_a_history_write_succeeds_and_ingestion_is_unchanged(h, sector_env):
    h.recorder()
    assert h.u.update_symbol("AAA") is True
    assert len(h.upserts) == 1 and h.upserts[0]["sector"] == "Technology"                          # the upsert saw exactly the vendor's info
    assert obs(sector_env) == [(1, "Technology", "first", YF, "run-1", R.WRITER)]
    assert polls(sector_env) == [("run-1", "sector", "created_observation", None)]
    assert h.u._sector_recorder.counters == {"attempted": 1, "recorded": 1, "duplicate": 0, "failed": 0}


def test_a_history_write_failure_never_fails_ingestion_and_is_logged_and_counted(h, sector_env, caplog):
    @contextmanager
    def broken():
        raise ConnectionError("pool exhausted")
        yield
    rec = h.recorder(connect=broken)
    with caplog.at_level("ERROR"):
        assert h.u.update_symbol("AAA") is True                                                     # ingestion carried on and succeeded
    assert len(h.upserts) == 1
    assert rec.counters == {"attempted": 1, "recorded": 0, "duplicate": 0, "failed": 1}
    assert any("sector history NOT recorded for AAA" in m and "pool exhausted" in m for m in caplog.messages)   # observable, not swallowed
    assert obs(sector_env) == [] and polls(sector_env) == []


def test_a_transaction_level_failure_is_isolated_too(h, sector_env, caplog):
    """The history connection arrives in an ABORTED transaction (InFailedSqlTransaction on the very first statement): the unit fails as a whole,
    ingestion is untouched, nothing half-written is left behind, and the next symbol records normally."""
    state = {"n": 0}

    @contextmanager
    def aborted_then_fine():
        with h.connect() as c:
            state["n"] += 1
            if state["n"] == 1:
                cur = c.cursor()
                with pytest.raises(psycopg2.Error):
                    cur.execute("SELECT 1/0")
            yield c
            c.rollback()
    rec = h.recorder(connect=aborted_then_fine)
    with caplog.at_level("ERROR"):
        assert h.u.update_symbol("AAA") is True
        assert h.u.update_symbol("BBB") is True
    assert len(h.upserts) == 2
    assert rec.counters == {"attempted": 2, "recorded": 1, "duplicate": 0, "failed": 1}
    assert any("sector history NOT recorded for AAA" in m for m in caplog.messages)
    assert obs(sector_env, "AAA") == [] and polls(sector_env, "AAA") == []
    assert [o[1] for o in obs(sector_env, "BBB")] == ["Technology"] and len(polls(sector_env, "BBB")) == 1


def test_a_recorder_that_cannot_even_be_built_does_not_fail_ingestion(h, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("cannot build")
    monkeypatch.setattr(R, "SectorRecorder", boom)
    h.u._sector_recorder = None
    with caplog.at_level("ERROR"):
        assert h.u.update_symbol("AAA") is True
    assert len(h.upserts) == 1 and any("sector history recorder unavailable for AAA" in m for m in caplog.messages)


def test_the_history_is_recorded_independently_of_the_fundamentals_upsert(h, sector_env):
    h.recorder()
    h.upsert_result = False                                                                         # the upsert reports failure ...
    assert h.u.update_symbol("AAA") is False
    h.upsert_result = RuntimeError("db down")                                                        # ... or raises
    h.recorder("run-2")
    assert h.u.update_symbol("AAA") is False
    assert [o[1] for o in obs(sector_env)] == ["Technology"]                                         # the vendor answered: the history is kept
    assert [p[2] for p in polls(sector_env)] == ["created_observation", "confirmed_head"]


def test_the_recorder_never_shares_the_upserts_connection(h, monkeypatch):
    seen = []
    real = R.record_poll

    def spy(conn, outcome, **kw):
        seen.append(conn)
        return real(conn, outcome, **kw)
    monkeypatch.setattr(R, "record_poll", spy)
    h.recorder()
    assert h.u.update_symbol("AAA") is True
    assert len(seen) == 1 and not hasattr(h.u, "conn")                                               # a separate connection object; the updater holds none


# ------------------------------------------------------------------ duplicate / retry
def test_a_duplicate_attempt_and_a_retry_after_a_transient_error_are_idempotent(h, sector_env):
    @contextmanager
    def flaky_once():
        flaky_once.calls += 1
        if flaky_once.calls == 1:
            raise ConnectionError("transient")
        with h.connect() as c:
            yield c
            c.rollback()
    flaky_once.calls = 0
    rec = h.recorder("run-9", connect=flaky_once)
    assert h.u.update_symbol("AAA") is True                                                         # transient failure: counted, ingestion fine
    assert rec.counters["failed"] == 1 and obs(sector_env) == []
    assert h.u.update_symbol("AAA") is True                                                         # the retry of the same run records it
    assert h.u.update_symbol("AAA") is True                                                         # a duplicate of the same (run, symbol, source)
    assert rec.counters == {"attempted": 3, "recorded": 1, "duplicate": 1, "failed": 1}
    assert len(obs(sector_env)) == 1 and polls(sector_env) == [("run-9", "sector", "created_observation", None)]


# ------------------------------------------------------------------ vendor failure / no sector / same / changed
def test_a_vendor_failure_is_a_poll_that_never_touches_the_chain(h, sector_env):
    h.recorder("run-1")
    assert h.u.update_symbol("AAA") is True
    h.recorder("run-2")
    h.fetch = lambda s: (_ for _ in ()).throw(TimeoutError("slow"))
    assert h.u.update_symbol("AAA") is False                                                        # ingestion behaves exactly as before: failure
    h.recorder("run-3")
    h.fetch = lambda s: None
    assert h.u.update_symbol("AAA") is False
    assert obs(sector_env) == [(1, "Technology", "first", YF, "run-1", R.WRITER)]                    # the last known classification is untouched
    assert polls(sector_env) == [("run-1", "sector", "created_observation", None), ("run-2", "request_failed", "none", "timeout"),
                                 ("run-3", "request_failed", "none", "no_company_info")]
    assert len(h.upserts) == 1                                                                      # no upsert for a failed fetch


def test_inferred_no_sector_is_recorded_for_yfinance_with_evidence_but_never_for_the_ambiguous_tiingo_none(h, sector_env):
    h.recorder("run-1")
    h.fetch, h.meta = (lambda s: INFO(None)), ETF_META
    assert h.u.update_symbol("YF") is True
    h.vendor, h.meta = TG, None
    h.recorder("run-2")
    h.fetch = lambda s: {"sector": None, "industry": None}
    h.yf_probe = lambda s: None                                                                     # the authoritative probe also finds nothing usable
    assert h.u.update_symbol("TG") is True                                                          # ingestion proceeds as before
    assert obs(sector_env, "YF") == [(1, None, "first", YF, "run-1", R.WRITER)]
    assert polls(sector_env, "YF") == [("run-1", "no_sector", "created_observation", None)]
    assert obs(sector_env, "TG") == []                                                              # Tiingo's None is ambiguous: no observation ...
    assert [p for p in polls(sector_env, "TG") if p[1:] == ("invalid_response", "none", "ambiguous_source_none")] == [("run-2", "invalid_response", "none", "ambiguous_source_none")]


def test_an_operating_company_with_no_sector_is_not_an_inferred_no_sector(h, sector_env):
    h.recorder("run-1")
    h.fetch, h.meta = (lambda s: INFO(None)), {"quote_type": "EQUITY", "n_keys": 6, "sector_key_present": False}
    assert h.u.update_symbol("EQ") is True
    assert obs(sector_env, "EQ") == []
    assert polls(sector_env, "EQ") == [("run-1", "invalid_response", "none", "operating_company_sector_absent")]


def test_a_same_value_refresh_is_a_poll_and_no_second_observation(h, sector_env):
    h.recorder("run-1")
    h.u.update_symbol("AAA")
    h.recorder("run-2")
    h.u.update_symbol("AAA")
    assert [o[:3] for o in obs(sector_env)] == [(1, "Technology", "first")]
    assert [p[:3] for p in polls(sector_env)] == [("run-1", "sector", "created_observation"), ("run-2", "sector", "confirmed_head")]


def test_a_changed_value_is_a_chained_observation_and_the_old_one_stays(h, sector_env):
    h.recorder("run-1")
    h.u.update_symbol("AAA")
    h.recorder("run-2")
    h.fetch = lambda s: INFO("Health Care")
    h.u.update_symbol("AAA")
    assert [o[:3] for o in obs(sector_env)] == [(1, "Technology", "first"), (2, "Health Care", "changed")]
    assert [p[1:3] for p in polls(sector_env)] == [("sector", "created_observation")] * 2


def test_the_vendor_tag_follows_the_real_fetch_path(monkeypatch, sector_env):
    """The real `fetch_company_info` (Tiingo configured): a Tiingo answer is tagged tiingo_meta, so its None is the ambiguous kind."""
    import shared.tiingo_client as T
    h = Harness(monkeypatch, sector_env[1])
    h.u.fetch_company_info = FU.FundamentalsUpdater.fetch_company_info.__get__(h.u)
    monkeypatch.setattr(FU.config, "data_provider", "tiingo", raising=False)
    monkeypatch.setattr(T, "get_fundamentals", lambda symbol: {"sector": "Technology", "industry": "x"})
    h.recorder()
    assert h.u.update_symbol("AAA") is True
    assert obs(sector_env)[0][3] == TG and h.u._sector_vendor == TG


def test_the_real_yfinance_fetch_path_captures_the_evidence_a_flattened_info_loses(monkeypatch, sector_env):
    """The real `fetch_company_info` (yfinance configured): an ETF answer has NO `sector` key; the structural evidence (quoteType) is captured from the
    RAW answer before it is flattened, so the inferred no-sector is recorded with that evidence; an operating company with the same gap is not."""
    raw = {"symbol": "SPY", "quoteType": "ETF", "longName": "x", "exchange": "PCX", "currency": "USD", "market": "us_market"}
    h = Harness(monkeypatch, sector_env[1])
    h.u.fetch_company_info = FU.FundamentalsUpdater.fetch_company_info.__get__(h.u)
    del h.u._fetch_yfinance_raw
    monkeypatch.setattr(FU.config, "data_provider", "yfinance", raising=False)
    answers = {"SPY": raw, "EQ": dict(raw, symbol="EQ", quoteType="EQUITY")}
    monkeypatch.setattr(FU.FundamentalsUpdater, "_fetch_yfinance_raw", lambda self, symbol: answers[symbol])
    h.recorder()
    assert h.u.update_symbol("SPY") is True and h.u.update_symbol("EQ") is True
    assert obs(sector_env, "SPY") == [(1, None, "first", YF, "run-1", R.WRITER)]
    assert rows(sector_env, "SELECT no_sector_reason, raw_payload FROM sector_observation WHERE symbol = 'SPY'") == [("vendor_null", {"sector": None, "quote_type": "ETF"})]
    assert obs(sector_env, "EQ") == [] and polls(sector_env, "EQ") == [("run-1", "invalid_response", "none", "operating_company_sector_absent")]


def test_when_tiingo_serves_the_symbol_the_authoritative_chain_is_probed_separately_and_the_two_never_mix(monkeypatch, sector_env):
    import shared.tiingo_client as T
    h = Harness(monkeypatch, sector_env[1])
    h.u.fetch_company_info = FU.FundamentalsUpdater.fetch_company_info.__get__(h.u)
    monkeypatch.setattr(FU.config, "data_provider", "tiingo", raising=False)
    monkeypatch.setattr(T, "get_fundamentals", lambda symbol: {"sector": "Technology", "industry": "x"})
    h.yf_probe = lambda s: RAW_EQUITY("Information Technology")                                    # a different taxonomy from Tiingo's
    h.recorder()
    assert h.u.update_symbol("AAA") is True
    assert {(o[3], o[1]) for o in obs(sector_env)} == {(TG, "Technology"), (YF, "Information Technology")}   # two chains, neither contaminates the other
    assert h.upserts[0]["sector"] == "Technology"                                                   # the ingestion value is unchanged by the probe
    h.recorder("run-2")
    h.yf_probe = lambda s: (_ for _ in ()).throw(TimeoutError("probe"))                            # the authoritative probe fails the next day
    assert h.u.update_symbol("AAA") is True
    got = rows(sector_env, "SELECT run_id, source, response_state, chain_effect, failure_reason FROM sector_poll WHERE run_id = 'run-2' ORDER BY source")
    assert got == [("run-2", TG, "sector", "confirmed_head", None), ("run-2", YF, "request_failed", "none", "timeout")]
    assert [o[1] for o in obs(sector_env) if o[3] == YF] == ["Information Technology"]            # the failure neither erased the head nor borrowed Tiingo's sector


def test_the_run_result_exposes_the_recorder_counters(monkeypatch, sector_env):
    h = Harness(monkeypatch, sector_env[1])
    h.u.get_symbols_to_update = lambda limit=None: ["AAA", "BBB"]
    monkeypatch.setattr(R, "SectorRecorder", lambda: _Real(h.connect))
    result = h.u.run_fundamentals_update(symbols=["AAA", "BBB"])
    assert result["symbols_successful"] == 2
    assert {k: result["sector_history"][k] for k in ("attempted", "recorded", "duplicate", "failed")} == {"attempted": 2, "recorded": 2, "duplicate": 0, "failed": 0}
    assert result["sector_history"]["run_id"].startswith("fund-")


class _Real(R.SectorRecorder):
    def __init__(self, connect):
        super().__init__(connect=connect, is_enabled=lambda: True)
