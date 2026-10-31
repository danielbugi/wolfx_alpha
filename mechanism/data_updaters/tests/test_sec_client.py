"""The SEC client's politeness rules with a fake network (no real request is ever sent): declared agent, host allow-list, rate spacing, retries with backoff,
Retry-After, 404 as an answer, 403 and repeated failure open the circuit, gzip."""
import gzip
import io
import os
import sys
import urllib.error

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from data_updaters import sec_client as C  # noqa: E402

UA = "First Light Research ops@firstlight-research.example.net"
URL = "https://data.sec.gov/submissions/CIK0000000001.json"


class Resp:
    def __init__(self, body=b"{}", status=200, headers=None):
        self._b, self.status, self.headers = body, status, headers or {}

    def read(self, n=-1):
        return self._b

    def close(self):
        pass


class Net:
    """A scripted opener: each call pops the next outcome (a Resp, an HTTPError, or an Exception)."""

    def __init__(self, *outcomes):
        self.outcomes, self.requests = list(outcomes), []

    def __call__(self, req, timeout):
        self.requests.append(req)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o


def http_error(code, retry_after=None):
    h = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError(URL, code, "x", h, io.BytesIO(b""))


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def client(net, clock=None, **kw):
    clock = clock or Clock()
    return C.SecClient(UA, opener=net, clock=clock.now, sleep=clock.sleep, jitter=lambda: 1.0, **kw), clock


@pytest.mark.parametrize("ua", ["", "Operator", "ops@firstlight-research.example.net", "Your Name you@example.com", "First Light noreply@firstlight.io", "First Light <ops@x.io>"])
def test_a_missing_or_placeholder_user_agent_is_refused(ua):
    with pytest.raises(C.UserAgentError):
        C.SecClient(ua)


def test_the_declared_agent_is_sent_on_every_request_and_only_sec_hosts_over_https_are_allowed():
    net = Net(Resp(b'{"a":1}'))
    c, _ = client(net)
    assert c.fetch(URL).body == b'{"a":1}' and net.requests[0].get_header("User-agent") == UA
    for bad in ("http://data.sec.gov/x", "https://example.org/x", "https://data.sec.gov.evil.net/x"):
        with pytest.raises(C.SecError):
            c.fetch(bad)
    assert len(net.requests) == 1


def test_requests_are_spaced_to_the_configured_rate():
    net = Net(*[Resp() for _ in range(4)])
    c, clock = client(net, max_rps=5.0)
    for _ in range(4):
        c.fetch(URL)
    assert clock.t == pytest.approx(3 * 0.2)          # 4 requests at 5/s: three 0.2 s gaps
    with pytest.raises(ValueError):
        C.SecClient(UA, max_rps=11)


def test_gzip_bodies_are_decoded():
    net = Net(Resp(gzip.compress(b'{"z":2}'), headers={"Content-Encoding": "gzip"}))
    assert client(net)[0].fetch(URL).body == b'{"z":2}'


def test_a_503_is_retried_with_backoff_and_then_succeeds():
    net = Net(http_error(503), http_error(503), Resp(b"ok"))
    c, clock = client(net)
    assert c.fetch(URL).body == b"ok" and len(net.requests) == 3
    assert [round(s, 2) for s in clock.slept if s >= 0.5][:2] == [1.0, 2.0]      # exponential, jitter pinned


def test_retry_after_is_honoured_but_capped():
    net = Net(http_error(429, "45"), Resp(b"ok"))
    c, clock = client(net)
    c.fetch(URL)
    assert max(clock.slept) == 45.0
    net2 = Net(http_error(429, "9999"), Resp(b"ok"))
    c2, clock2 = client(net2)
    c2.fetch(URL)
    assert max(clock2.slept) == 60.0


def test_a_404_is_an_answer_not_a_failure():
    net = Net(http_error(404))
    c, _ = client(net)
    with pytest.raises(C.SecNotFound):
        c.fetch(URL)
    assert len(net.requests) == 1 and not c.circuit_open


def test_a_403_is_never_retried_and_opens_the_circuit():
    net = Net(http_error(403), Resp(b"never"))
    c, _ = client(net)
    with pytest.raises(C.SecError) as e:
        c.fetch(URL)
    assert e.value.status == 403 and c.circuit_open and len(net.requests) == 1
    with pytest.raises(C.SecCircuitOpen):
        c.fetch(URL)
    assert len(net.requests) == 1                      # nothing more was sent


def test_repeated_failures_open_the_circuit_and_retries_are_bounded():
    net = Net(*[http_error(500) for _ in range(40)])
    c, _ = client(net, max_retries=1, breaker=3)
    for _ in range(3):
        with pytest.raises(C.SecError):
            c.fetch(URL)
    assert c.circuit_open and len(net.requests) == 6   # 3 requests x (1 try + 1 retry)
    with pytest.raises(C.SecCircuitOpen):
        c.fetch(URL)


def test_transport_errors_are_retried_like_server_errors():
    net = Net(TimeoutError("slow"), Resp(b"ok"))
    c, _ = client(net)
    assert c.fetch(URL).body == b"ok" and len(net.requests) == 2
