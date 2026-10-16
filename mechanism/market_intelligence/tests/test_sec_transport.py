"""The live SEC transport, driven with a fake opener, fake clock, fake sleep and a deterministic jitter (no network, no real time): User-Agent on every
request, https + host allow-list, rate spacing, retries with exponential backoff and Retry-After, 404 as an answer, 403 and repeated failure opening the
circuit, gzip bodies, snapshots of every successful body."""
import gzip
import hashlib
import io
import json
import urllib.error
from datetime import datetime, timezone
from email.message import Message

import pytest

import mi_samples  # noqa: F401  (path setup)
from edgar_samples import UA
from market_intelligence import edgar_collector as E
from market_intelligence import sec_transport as T

URL = "https://data.sec.gov/submissions/CIK0000320193.json"
UTC = timezone.utc


class Resp:
    def __init__(self, body=b"{}", status=200, headers=None):
        self._b, self.status, self.headers = body, status, headers or Message()
        for k, v in (headers or {}).items():
            pass

    def read(self, n=-1):
        return self._b if n < 0 else self._b[:n]

    def getcode(self):
        return self.status

    def close(self):
        pass


def http_error(code, retry_after=None):
    h = Message()
    if retry_after is not None:
        h["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError(URL, code, f"E{code}", h, io.BytesIO(b""))


class World:
    """A script of outcomes, one per attempt: bytes/Resp = success, an Exception instance = raised."""

    def __init__(self, *script):
        self.script, self.requests, self.t, self.slept = list(script), [], 0.0, []

    def opener(self, req, timeout):
        self.requests.append((req.full_url, dict(req.header_items()), timeout))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step if isinstance(step, Resp) else Resp(step)

    def clock(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def make(world, **kw):
    kw.setdefault("jitter", lambda: 1.0)
    return T.SecTransport(E.EdgarConfig(UA), opener=world.opener, clock=world.clock, sleep=world.sleep,
                          wall=lambda: datetime(2026, 10, 16, 9, 0, tzinfo=UTC), **kw)


def test_every_request_carries_the_declared_user_agent_and_asks_for_gzip():
    w = World(b'{"cik": 1}')
    got = make(w).fetch(URL)
    ua = {k.lower(): v for k, v in w.requests[0][1].items()}
    assert ua["user-agent"] == UA and ua["accept-encoding"] == "gzip" and got.status == 200 and w.requests[0][2] == 30.0


def test_a_placeholder_or_missing_contact_is_refused_before_any_request():
    for bad in ("", "First Light", "Your Name you@example.com"):
        with pytest.raises(E.EdgarError):
            E.EdgarConfig(bad)
    with pytest.raises(E.EdgarError):
        E.EdgarConfig(UA, max_rps=11)                                   # above the SEC's published limit
    with pytest.raises(E.EdgarError):
        T.SecTransport("not a config")


@pytest.mark.parametrize("url", ["http://data.sec.gov/x.json", "https://evil.example/x.json", "https://data.sec.gov.evil.io/x.json", "ftp://www.sec.gov/x"])
def test_only_https_sec_hosts_are_fetched_and_nothing_is_sent_otherwise(url):
    w = World()
    with pytest.raises(T.SecFetchError):
        make(w).fetch(url)
    assert w.requests == []


def test_requests_are_spaced_by_the_rate_limit():
    w = World(b"{}", b"{}", b"{}", b"{}")
    t = make(w)                                                         # default 5 per second
    for _ in range(4):
        t.fetch(URL)
    assert len(w.slept) == 3 and all(abs(s - 0.2) < 1e-9 for s in w.slept)
    assert t.cfg.max_rps == E.DEFAULT_MAX_RPS == 5.0


def test_gzip_bodies_are_decompressed_and_hashed_as_received_text():
    plain = json.dumps({"cik": 320193}).encode()
    w = World(Resp(gzip.compress(plain)))
    got = make(w).fetch(URL)
    assert got.body == plain and got.sha256 == hashlib.sha256(plain).hexdigest()


def test_a_503_then_success_retries_with_exponential_backoff_and_counts_it():
    w = World(http_error(503), http_error(503), b'{"ok": 1}')
    t = make(w, backoff_base_s=1.0)
    got = t.fetch(URL)
    assert got.attempts == 3 and t.stats.retries == 2 and t.stats.statuses[503] == 2 and t.stats.statuses[200] == 1
    backoffs = [s for s in w.slept if s >= 1.0]                         # (rate-limit gaps are 0.2 s; backoffs are the longer sleeps)
    assert backoffs == [1.0, 2.0] and abs(t.stats.backoff_s - 3.0) < 1e-9


def test_a_429_honours_retry_after_but_never_beyond_the_cap():
    w = World(http_error(429, retry_after=7), http_error(429, retry_after=9999), b"{}")
    t = make(w, retry_after_cap_s=60.0)
    t.fetch(URL)
    longest = sorted(s for s in w.slept if s > 1.0)
    assert longest == [7.0, 60.0]


def test_jitter_never_shortens_a_backoff_below_half_the_exponential_step():
    w = World(http_error(500), b"{}")
    make(w, backoff_base_s=4.0, jitter=lambda: 0.0).fetch(URL)
    assert 2.0 in w.slept


def test_retries_are_bounded_and_exhaustion_is_an_error_not_a_hang():
    w = World(*[http_error(503)] * 5)
    t = make(w, max_retries=4)
    with pytest.raises(T.SecFetchError) as e:
        t.fetch(URL)
    assert "gave up after 5 attempts" in str(e.value) and len(w.requests) == 5 and t.stats.retries == 4 and not w.script


def test_transport_errors_and_timeouts_are_retried():
    w = World(urllib.error.URLError("connection reset"), TimeoutError("timed out"), b"{}")
    t = make(w)
    assert t.fetch(URL).attempts == 3 and t.stats.errors


def test_a_404_is_an_answer_not_a_failure_and_is_never_retried():
    w = World(http_error(404), http_error(404), http_error(404), http_error(404), http_error(404), http_error(404), b"{}")
    t = make(w, breaker=3)
    for _ in range(6):
        with pytest.raises(T.SecNotFound):
            t.fetch(URL)
    assert len(w.requests) == 6 and not t.stats.circuit_open            # six 404s did not trip a breaker of 3
    assert t.fetch(URL).status == 200


def test_a_403_is_never_retried_and_opens_the_circuit_immediately():
    w = World(http_error(403), b"{}")
    t = make(w)
    with pytest.raises(T.SecFetchError) as e:
        t.fetch(URL)
    assert e.value.status == 403 and len(w.requests) == 1 and t.stats.circuit_open
    with pytest.raises(T.SecCircuitOpen):
        t.fetch(URL)
    assert len(w.requests) == 1                                         # nothing more was sent


def test_consecutive_failures_open_the_circuit_and_a_success_resets_the_count():
    w = World(http_error(400), http_error(400), b"{}", http_error(400), http_error(400), http_error(400), b"{}")
    t = make(w, breaker=3)
    for expect_ok in (False, False, True, False, False, False):
        if expect_ok:
            t.fetch(URL)
        else:
            with pytest.raises(T.SecFetchError):
                t.fetch(URL)
    assert t.stats.circuit_open
    with pytest.raises(T.SecCircuitOpen):
        t.fetch(URL)
    assert len(w.requests) == 6 and w.script == [b"{}"]


def test_an_empty_200_and_a_bad_gzip_are_failures_not_documents():
    w = World(b"")                                                     # an empty 200 is refused at once (retrying cannot help)
    with pytest.raises(T.SecFetchError):
        make(w).fetch(URL)
    w = World(*[Resp(b"\x1f\x8bnot-gzip")] * 5)                         # a corrupt body is a transport fault: retried, then given up
    with pytest.raises(T.SecFetchError):
        make(w).fetch(URL)
    assert len(w.requests) == 5


def test_get_json_rejects_non_json_and_non_objects_but_keeps_the_body_as_a_snapshot():
    w = World(b"<html>maintenance</html>", b"[1,2,3]", b'{"cik": "0000320193"}')
    t = make(w)
    with pytest.raises(T.SecFetchError):
        t.get_json(URL, {})
    with pytest.raises(T.SecFetchError):
        t.get_json(URL, {})
    assert t.get_json(URL, {}) == {"cik": "0000320193"}
    assert [c.body for c in t.captured] == [b"<html>maintenance</html>", b"[1,2,3]", b'{"cik": "0000320193"}']


def test_the_summary_reports_attempts_statuses_and_durations():
    w = World(http_error(503), b'{"a": 1}')
    t = make(w)
    t.fetch(URL)
    s = t.stats.summary()
    assert s["requests"] == 1 and s["attempts"] == 2 and s["retries"] == 1 and s["statuses"] == {503: 1, 200: 1} and s["bytes_received"] == 8
    assert s["duration_s"]["max"] is not None and s["circuit_open"] is False


def test_the_collector_runs_over_the_live_transport_and_records_one_failing_cik_without_stopping():
    from edgar_samples import CIK_A, CIK_B, NOW, doc, r
    good = json.dumps(doc(CIK_B, [r("0000789019-26-000001")])).encode()
    w = World(http_error(404), good)
    t = make(w)
    rep = E.collect(E.EdgarConfig(UA), t, [CIK_A, CIK_B], now=NOW, clock=w.clock, sleep=w.sleep)
    assert not rep.complete and CIK_A in rep.fetch_errors and rep.ciks_complete == [CIK_B] and len(rep.facts) == 1
    assert len(t.captured) == 1 and t.captured[0].url.endswith("CIK0000789019.json")
