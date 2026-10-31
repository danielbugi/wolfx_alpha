"""A small, polite HTTP client for SEC EDGAR, for the fundamentals updater. Stdlib only.

Why this exists next to the research feed's own transport: the research package that fetches SEC filings is isolated from the pipeline on purpose (guard tests forbid
anything on the pipeline path from importing it), so the pipeline's updater carries its own copy of the rules instead of reaching into it.

SEC fair-access rules this implements (https://www.sec.gov/os/webmaster-faq#developers):
  * a declared, descriptive User-Agent with an operator name and a real contact email on every request; a missing or placeholder value is refused;
  * at most 10 requests per second: default 5, validated to (0, 10];
  * no retry storms: 429 and 5xx answers and transport errors are retried with exponential backoff and jitter, Retry-After is honoured (capped), and after
    `breaker` consecutive failed requests the circuit opens and NOTHING more is sent until the process ends;
  * 403 means the client is being refused: never retried, and it opens the circuit at once. 404 is an answer (the company or document does not exist).
Only https://data.sec.gov and https://www.sec.gov may be fetched.
"""
from __future__ import annotations

import gzip
import random
import re
import time
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Callable, Optional, Tuple
from urllib.parse import urlparse

ALLOWED_HOSTS = frozenset({"data.sec.gov", "www.sec.gov"})
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_BODY_BYTES = 64 * 1024 * 1024
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PLACEHOLDER = re.compile(r"example\.(com|org)|your[-_.\s]?(name|email|company)|changeme|placeholder|noreply|no-reply|test@|@test\b|<.*>", re.I)


class SecError(RuntimeError):
    def __init__(self, url: str, message: str, status: Optional[int] = None):
        super().__init__(f"{message} ({url})")
        self.url, self.status = url, status


class SecNotFound(SecError):
    pass


class SecCircuitOpen(SecError):
    pass


class UserAgentError(ValueError):
    pass


def validate_user_agent(ua: str) -> str:
    ua = (ua or "").strip()
    if not _EMAIL.search(ua) or _PLACEHOLDER.search(ua) or len(ua.split()) < 2:
        raise UserAgentError("the SEC User-Agent must name the operator and a real contact email (e.g. 'Operator Name ops@yourdomain.tld'); empty or placeholder values are refused")
    return ua


def _default_opener(req: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(req, timeout=timeout)


class SecClient:
    def __init__(self, user_agent: str, *, max_rps: float = 5.0, max_retries: int = 4, backoff_base_s: float = 1.0, backoff_cap_s: float = 30.0, retry_after_cap_s: float = 60.0,
                 timeout_s: float = 30.0, breaker: int = 5, opener: Callable = _default_opener, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, jitter: Callable[[], float] = random.random) -> None:
        self.user_agent = validate_user_agent(user_agent)
        if not 0 < max_rps <= 10:
            raise ValueError("max_rps must be in (0, 10]")
        if max_retries < 0 or breaker < 1 or backoff_base_s <= 0 or timeout_s <= 0:
            raise ValueError("max_retries >= 0, breaker >= 1, backoff_base_s > 0 and timeout_s > 0 are required")
        self.gap = 1.0 / max_rps
        self.max_retries, self.backoff_base_s, self.backoff_cap_s, self.retry_after_cap_s = max_retries, backoff_base_s, backoff_cap_s, retry_after_cap_s
        self.timeout_s, self.breaker = timeout_s, breaker
        self._open, self._clock, self._sleep, self._jitter = opener, clock, sleep, jitter
        self._next: Optional[float] = None
        self._consecutive_failures = 0
        self.circuit_open = False
        self.requests = 0
        self.attempts = 0

    def _wait(self) -> None:
        t = self._clock()
        if self._next is not None and t < self._next:
            self._sleep(self._next - t)
            t = self._next
        self._next = t + self.gap

    def _once(self, url: str) -> Tuple[Optional[int], bytes, Optional[float], Optional[str]]:
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip", "Accept": "application/json"})
        try:
            resp = self._open(req, self.timeout_s)
            try:
                raw = resp.read(MAX_BODY_BYTES + 1)
                status, enc = getattr(resp, "status", 200), (resp.headers.get("Content-Encoding") or "").lower()
            finally:
                close = getattr(resp, "close", None)
                if close:
                    close()
            if len(raw) > MAX_BODY_BYTES:
                return status, b"", None, "response larger than the size cap"
            return status, (gzip.decompress(raw) if enc == "gzip" else raw), None, None
        except urllib.error.HTTPError as e:
            ra = e.headers.get("Retry-After") if e.headers else None
            try:
                retry_after = float(ra) if ra is not None else None
            except ValueError:
                retry_after = None
            return e.code, b"", retry_after, f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001 - network errors, timeouts, bad gzip: all retried the same way
            return None, b"", None, f"{type(e).__name__}: {e}"[:160]

    def fetch(self, url: str, *, capture: bool = False) -> SimpleNamespace:
        host = urlparse(url).hostname or ""
        if urlparse(url).scheme != "https" or host not in ALLOWED_HOSTS:
            raise SecError(url, f"only https://{sorted(ALLOWED_HOSTS)} may be fetched")
        if self.circuit_open:
            raise SecCircuitOpen(url, "circuit open: the source kept refusing or failing; no further requests are sent")
        self.requests += 1
        attempts, last = 0, "no attempt made"
        while True:
            attempts += 1
            self._wait()
            self.attempts += 1
            status, body, retry_after, err = self._once(url)
            if err is None and status == 200:
                self._consecutive_failures = 0
                return SimpleNamespace(url=url, status=200, body=body, attempts=attempts)
            last = err or f"HTTP {status}"
            if status == 404:
                self._consecutive_failures = 0
                raise SecNotFound(url, "not found", 404)
            if status == 403:
                self.circuit_open = True
                raise SecError(url, "403: the SEC is refusing this client (check the User-Agent and the request rate); circuit opened", 403)
            if (status in RETRY_STATUSES or status is None) and attempts <= self.max_retries:
                delay = min(self.backoff_cap_s, self.backoff_base_s * (2 ** (attempts - 1)))
                delay = delay * (0.5 + self._jitter() * 0.5)
                if retry_after is not None:
                    delay = max(delay, min(retry_after, self.retry_after_cap_s))
                self._sleep(delay)
                continue
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.breaker:
                self.circuit_open = True
            raise SecError(url, f"failed after {attempts} attempt(s): {last}", status)
