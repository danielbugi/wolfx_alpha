"""
Forward paper tracker dashboard - the backend side. READ-ONLY: runs the pure aggregation in mechanism/research/paper_dashboard over the stored `paper_*` rows through the backend's connection pool.

* Every request opens its transaction with `SET TRANSACTION READ ONLY`; nothing here can write, and the service never touches the collector, the ledger, the bot or the tracker.
* If the tracker storage is absent or unreadable, or the database is unavailable, the answer is an explicit `available: false` payload with a reason - never an empty success, never a 500.
* The payload is small (aggregates only), but it is computed from all stored events, so a short in-process cache (default 120 s) keeps concurrent viewers from recomputing it.
* Every payload says `source: "forward"`. Historical replay results live in the static /lab reports and are never merged in here.
"""
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

_MECHANISM = str(Path(__file__).resolve().parents[2] / "mechanism")
if _MECHANISM not in sys.path:
    sys.path.append(_MECHANISM)

from research import paper_dashboard_io as _io  # noqa: E402

CACHE_SECONDS = 120.0


def _unavailable(reason: str) -> Dict[str, Any]:
    return {"schema": "forward_tracker_dashboard_v1", "source": "forward", "available": False, "reason": reason, "sessions_processed": 0}


class ForwardTrackerService:
    def __init__(self, get_connection: Callable, builder: Callable = _io.forward_payload, clock: Callable[[], float] = time.monotonic, cache_seconds: float = CACHE_SECONDS):
        self._get_connection = get_connection
        self._builder = builder
        self._clock = clock
        self._cache_seconds = cache_seconds
        self._cached: Optional[tuple] = None

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        conn = self._get_connection()
        if not conn:
            raise ConnectionError("database unavailable")
        try:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                yield cur
        finally:
            try:
                conn.rollback()
            finally:
                conn.close()

    def summary(self) -> Dict[str, Any]:
        now = self._clock()
        if self._cached is not None and now - self._cached[0] < self._cache_seconds:
            return self._cached[1]
        try:
            with self._cursor() as cur:
                payload = self._builder(cur)
        except ConnectionError:
            return _unavailable("the database is unavailable right now")
        except Exception:  # noqa: BLE001  (the reason is deliberately generic: no internals in the response)
            return _unavailable("the forward tracker summary could not be computed")
        self._cached = (now, payload)
        return payload
