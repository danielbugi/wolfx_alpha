"""Live HTTP transport for the SEC EDGAR collector (Phase 1A). The ONLY module of the package that opens a network connection.

It does one thing: fetch a document politely and report exactly what happened. It never touches a database and never reads the environment: the
User-Agent is handed in (validated by `edgar_collector.EdgarConfig`: an operator name and a real contact email; a placeholder is refused), and the
clock, the sleep and the jitter are injected so a test drives it without real time.

SEC fair-access rules this implements (https://www.sec.gov/os/webmaster-faq#developers):
  * a declared, descriptive User-Agent with contact details on every request;
  * at most 10 requests per second per client: the default is half of that (`DEFAULT_MAX_RPS`), and it is validated to (0, 10];
  * no retry storms: 429 and 5xx answers and transport errors are retried with exponential backoff and jitter, `Retry-After` is honoured (capped),
    and after `breaker` consecutive failed requests the circuit opens and NOTHING more is sent until the process ends (a run that is being
    refused must not keep knocking);
  * 403 means the client is being refused (bad agent or too fast): never retried, and it opens the circuit at once.

What it records (for the coverage / latency report): every request's outcome, attempts, retry and backoff totals, and per-request duration.
Every successfully fetched body is kept in `captured` (url, bytes, sha256, wall time) so the caller can preserve it as a source snapshot.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import random
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional
from urllib.parse import urlparse

from market_intelligence.edgar_collector import DEFAULT_MAX_RPS, EdgarConfig, EdgarError, RateLimiter

ALLOWED_HOSTS = frozenset({"data.sec.gov", "www.sec.gov"})
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_BODY_BYTES = 64 * 1024 * 1024


class SecFetchError(RuntimeError):
    """A request failed for good (not retryable, or retries exhausted)."""

    def __init__(self, url: str, message: str, status: Optional[int] = None):
        super().__init__(f"{url}: {message}")
        self.url, self.status = url, status


class SecNotFound(SecFetchError):
    """HTTP 404 -- an expected, per-document answer (not a transport failure; it does not count towards the circuit breaker)."""


class SecCircuitOpen(SecFetchError):
    """The run stopped sending requests because the source kept refusing or failing."""


@dataclass(frozen=True)
class Fetched:
    url: str
    status: int
    body: bytes                  # decompressed, exactly as the source sent it
    sha256: str
    fetched_at: datetime         # wall clock, UTC, when the response completed
    attempts: int
    duration_s: float            # the successful attempt only


@dataclass
class TransportStats:
    requests: int = 0            # logical fetches asked for
    attempts: int = 0            # HTTP attempts actually sent (requests + retries)
    retries: int = 0
    backoff_s: float = 0.0
    statuses: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)
    durations_s: List[float] = field(default_factory=list)
    bytes_received: int = 0
    circuit_open: bool = False

    def summary(self) -> Dict[str, Any]:
        d = sorted(self.durations_s)
        pick = lambda q: round(d[min(len(d) - 1, int(q * len(d)))], 3) if d else None
        return {"requests": self.requests, "attempts": self.attempts, "retries": self.retries, "backoff_s": round(self.backoff_s, 2),
                "statuses": dict(self.statuses), "errors": dict(self.errors), "bytes_received": self.bytes_received,
                "duration_s": {"p50": pick(0.5), "p95": pick(0.95), "max": round(d[-1], 3) if d else None}, "circuit_open": self.circuit_open}


Opener = Callable[[urllib.request.Request, float], Any]


def _default_opener(req: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(req, timeout=timeout)       # noqa: S310  (host allow-list enforced before this is reached)


class SecTransport:
    """`fetch(url)` -> Fetched; `get_json(url, headers)` satisfies `edgar_collector.Transport`."""

    def __init__(self, cfg: EdgarConfig, *, max_retries: int = 4, backoff_base_s: float = 1.0, backoff_cap_s: float = 30.0, retry_after_cap_s: float = 60.0,
                 timeout_s: float = 30.0, breaker: int = 5, opener: Opener = _default_opener, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 jitter: Callable[[], float] = random.random) -> None:
        if not isinstance(cfg, EdgarConfig):
            raise EdgarError("cfg must be an EdgarConfig (it validates the User-Agent and the rate)")
        if max_retries < 0 or breaker < 1 or backoff_base_s <= 0 or timeout_s <= 0:
            raise EdgarError("max_retries >= 0, breaker >= 1, backoff_base_s > 0 and timeout_s > 0 are required")
        self.cfg, self.max_retries, self.backoff_base_s, self.backoff_cap_s = cfg, max_retries, backoff_base_s, backoff_cap_s
        self.retry_after_cap_s, self.timeout_s, self.breaker = retry_after_cap_s, timeout_s, breaker
        self._open, self._clock, self._sleep, self._wall, self._jitter = opener, clock, sleep, wall, jitter
        self._limiter = RateLimiter(cfg.max_rps, clock, sleep)
        self._consecutive_failures = 0
        self.stats = TransportStats()
        self.captured: List[Fetched] = []

    # ------------------------------------------------------------------ one logical fetch
    def fetch(self, url: str, *, capture: bool = True) -> Fetched:
        host = urlparse(url).hostname or ""
        if urlparse(url).scheme != "https" or host not in ALLOWED_HOSTS:
            raise SecFetchError(url, f"only https://{sorted(ALLOWED_HOSTS)} may be fetched")
        if self.stats.circuit_open:
            raise SecCircuitOpen(url, "circuit open: the source kept refusing or failing; no further requests are sent")
        self.stats.requests += 1
        attempts = 0
        last_error = "no attempt made"
        while True:
            attempts += 1
            self._limiter.wait()
            self.stats.attempts += 1
            started = self._clock()
            status, body, retry_after, err = self._once(url)
            took = self._clock() - started
            if status is not None:
                self.stats.statuses[status] += 1
            if err is None and status == 200:
                self._consecutive_failures = 0
                self.stats.durations_s.append(took)
                self.stats.bytes_received += len(body)
                got = Fetched(url, status, body, hashlib.sha256(body).hexdigest(), self._wall(), attempts, took)
                if capture:
                    self.captured.append(got)
                return got
            if status == 404:
                self._consecutive_failures = 0                  # a missing document is an answer, not a failing source
                raise SecNotFound(url, "HTTP 404", 404)
            last_error = err or f"HTTP {status}"
            self.stats.errors[("HTTP %d" % status) if status else (err or "error").split(":")[0]] += 1
            if status == 403:                                    # refused: bad agent or too fast -- stop at once, never retry
                self._trip()
                raise SecFetchError(url, "HTTP 403 (the source refuses this client; check the User-Agent and the rate)", 403)
            retryable = status in RETRY_STATUSES or status is None
            if not retryable:
                self._fail()
                raise SecFetchError(url, last_error, status)
            if attempts > self.max_retries:
                self._fail()
                raise SecFetchError(url, f"gave up after {attempts} attempts: {last_error}", status)
            self.stats.retries += 1
            delay = self._delay(attempts, retry_after)
            self.stats.backoff_s += delay
            self._sleep(delay)

    def _fail(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.breaker:
            self._trip()

    def _trip(self) -> None:
        self.stats.circuit_open = True

    def _delay(self, attempts: int, retry_after: Optional[float]) -> float:
        expo = min(self.backoff_cap_s, self.backoff_base_s * (2 ** (attempts - 1)))
        jittered = expo * (0.5 + 0.5 * self._jitter())            # 50-100% of the exponential step: spreads clients, never shortens below half
        if retry_after is not None:
            return min(self.retry_after_cap_s, max(jittered, retry_after))
        return jittered

    def _once(self, url: str):
        """(status, body, retry_after_seconds, error_text). Exactly one of (status==200 with a body) / (an error) is meaningful."""
        req = urllib.request.Request(url, headers={"User-Agent": self.cfg.user_agent, "Accept": "application/json, */*;q=0.1", "Accept-Encoding": "gzip"})
        try:
            resp = self._open(req, self.timeout_s)
            try:
                status = getattr(resp, "status", None) or resp.getcode()
                raw = resp.read(MAX_BODY_BYTES + 1)
                enc = (resp.headers.get("Content-Encoding") or "").lower() if getattr(resp, "headers", None) is not None else ""
            finally:
                close = getattr(resp, "close", None)
                if close:
                    close()
            if len(raw) > MAX_BODY_BYTES:
                return status, b"", None, f"BodyTooLarge: more than {MAX_BODY_BYTES} bytes"
            if enc == "gzip" or raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            if not raw:
                return status, b"", None, "EmptyBody: HTTP 200 with no content"
            return status, raw, None, None
        except urllib.error.HTTPError as e:
            ra = None
            try:
                v = e.headers.get("Retry-After") if e.headers is not None else None
                ra = float(v) if v is not None and str(v).strip().isdigit() else None
            except Exception:                                       # noqa: BLE001  (an unparsable Retry-After is simply ignored)
                ra = None
            return e.code, b"", ra, f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, EOFError, gzip.BadGzipFile) as e:
            return None, b"", None, f"{type(e).__name__}: {e}"

    # ------------------------------------------------------------------ the edgar_collector.Transport protocol
    def get_json(self, url: str, headers: Mapping[str, str]) -> Mapping[str, Any]:
        got = self.fetch(url)
        try:
            doc = json.loads(got.body)
        except ValueError as e:
            raise SecFetchError(url, f"response is not JSON ({type(e).__name__}: {e}); body kept as a snapshot", got.status) from e
        if not isinstance(doc, dict):
            raise SecFetchError(url, "response JSON is not an object", got.status)
        return doc
