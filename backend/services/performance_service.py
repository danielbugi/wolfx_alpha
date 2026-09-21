# File: backend/services/performance_service.py
"""
Performance tracking - scores and tracks latency for every backend endpoint,
the database connection itself, and frontend route loads, so "is this app
fast" has a real number behind it instead of a guess.

Built 2026-09-19 after finding system_health_service.py alone opens 9+
separate psycopg2 connections per report (one per _query() call, see
get_pipeline_freshness's 6-query loop plus the rest) and backend/main.py's
get_database_connection() called raw psycopg2.connect() on every single
request across the whole API -- there was no connection pooling anywhere in
backend/, despite mechanism/shared/database.py already having a proper
ThreadedConnectionPool the backend never reused. The database section below
exists specifically to make that cost visible as a number (connection-acquire
time vs. query-execution time), not just as a code-reading observation.

Fixed same day: main.py now pools connections directly (see _PooledConnection
there). `measure_database()`'s `pooling_active` flag reflects whatever the
caller actually has wired up, so this stays honest if that ever regresses.

In-memory only (resets on backend restart) -- this is a single-process dev
uvicorn setup, so a process-local ring buffer is the right amount of
engineering for "track recent performance," not a new database table.
"""

import time
import threading
from collections import deque
from typing import Dict, Any, List, Optional, Tuple

# Samples retained per endpoint/route -- bounds memory while keeping enough
# history for stable p95/p99 estimates.
MAX_SAMPLES = 300

# Generic latency scoring bands. Deliberately the same for every endpoint
# rather than per-endpoint-tuned -- an endpoint that's "supposed to be slow"
# (e.g. ml-stats loading a model file) should still show critical here; that
# IS the signal that it needs caching, not a false alarm to threshold away.
def _status_from_ms(p95_ms: float, warn_ms: float, critical_ms: float) -> str:
    if p95_ms > critical_ms:
        return 'critical'
    if p95_ms > warn_ms:
        return 'warning'
    return 'healthy'


def _percentile(sorted_values: List[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


class _Series:
    """Recent (duration_ms, status_code) samples for one endpoint/route, plus
    all-time counters that don't get evicted when the ring buffer rolls over."""

    __slots__ = ('samples', 'total_count', 'total_errors', 'last_seen')

    def __init__(self):
        self.samples: deque = deque(maxlen=MAX_SAMPLES)
        self.total_count = 0
        self.total_errors = 0
        self.last_seen: Optional[float] = None

    def record(self, duration_ms: float, is_error: bool):
        self.samples.append((duration_ms, is_error))
        self.total_count += 1
        if is_error:
            self.total_errors += 1
        self.last_seen = time.time()

    def stats(self) -> Dict[str, Any]:
        durations = sorted(d for d, _ in self.samples)
        recent_errors = sum(1 for _, err in self.samples if err)
        return {
            'sample_count': len(self.samples),
            'total_count': self.total_count,
            'avg_ms': round(sum(durations) / len(durations), 1) if durations else None,
            'p50_ms': round(_percentile(durations, 0.50), 1) if durations else None,
            'p95_ms': round(_percentile(durations, 0.95), 1) if durations else None,
            'p99_ms': round(_percentile(durations, 0.99), 1) if durations else None,
            'max_ms': round(durations[-1], 1) if durations else None,
            'error_count': self.total_errors,
            'error_rate_pct': round(recent_errors / len(self.samples) * 100, 1) if self.samples else 0.0,
            'last_seen': self.last_seen,
        }


class PerformanceTracker:
    """Thread-safe recorder for backend endpoint latency and frontend route
    load timing. One process-wide instance (see `tracker` below)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._endpoints: Dict[Tuple[str, str], _Series] = {}
        self._routes: Dict[Tuple[str, str], _Series] = {}
        self._started_at = time.time()

    # -- backend endpoints ------------------------------------------------
    def record_request(self, method: str, path_template: str, status_code: int, duration_ms: float):
        key = (method, path_template)
        with self._lock:
            series = self._endpoints.setdefault(key, _Series())
            series.record(duration_ms, status_code >= 400)

    def get_endpoint_report(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._endpoints.items())
        report = []
        for (method, path), series in items:
            s = series.stats()
            s['method'] = method
            s['endpoint'] = path
            s['status'] = _status_from_ms(s['p95_ms'] or 0, warn_ms=800, critical_ms=3000)
            report.append(s)
        report.sort(key=lambda r: r['p95_ms'] or 0, reverse=True)
        return report

    # -- frontend routes ----------------------------------------------------
    def record_route_metric(self, route: str, metric: str, duration_ms: float):
        key = (route, metric)
        with self._lock:
            series = self._routes.setdefault(key, _Series())
            series.record(duration_ms, False)

    def get_route_report(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._routes.items())
        report = []
        for (route, metric), series in items:
            s = series.stats()
            s['route'] = route
            s['metric'] = metric
            s['status'] = _status_from_ms(s['p95_ms'] or 0, warn_ms=1000, critical_ms=3000)
            report.append(s)
        report.sort(key=lambda r: r['p95_ms'] or 0, reverse=True)
        return report

    def uptime_seconds(self) -> float:
        return round(time.time() - self._started_at, 1)


# Process-wide singleton -- imported by main.py's middleware and by
# routers/performance.py.
tracker = PerformanceTracker()


# ---------------------------------------------------------------------------
# Database timing benchmark -- isolates "opening a connection" from "running
# a query" so a pooling fix can be justified (or ruled out) with a number
# instead of a guess.
# ---------------------------------------------------------------------------
def measure_database(db_connection_func, pooling_active: bool = False) -> Dict[str, Any]:
    result: Dict[str, Any] = {}

    # 1. Connection acquire cost alone -- what EVERY request in this backend
    # currently pays, since get_database_connection() is a bare
    # psycopg2.connect() with no pool behind it.
    try:
        started = time.perf_counter()
        conn = db_connection_func()
        acquire_ms = (time.perf_counter() - started) * 1000
        if conn:
            conn.close()
        result['connection_acquire_ms'] = round(acquire_ms, 1)
        result['connection_status'] = _status_from_ms(acquire_ms, warn_ms=50, critical_ms=200)
    except Exception as e:
        result['connection_acquire_ms'] = None
        result['connection_status'] = 'unknown'
        result['connection_error'] = str(e)

    # 2. A representative real query, connection cost included -- so the
    # split between "connecting" and "querying" is visible side by side.
    try:
        started = time.perf_counter()
        conn = db_connection_func()
        if not conn:
            raise Exception("Database connection failed")
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM stock_prices WHERE date >= CURRENT_DATE - INTERVAL '1 day'")
        cursor.fetchone()
        cursor.close()
        conn.close()
        query_ms = (time.perf_counter() - started) * 1000
        result['sample_query_total_ms'] = round(query_ms, 1)
        result['sample_query_status'] = _status_from_ms(query_ms, warn_ms=200, critical_ms=1000)
        result['sample_query_label'] = "SELECT COUNT(*) FROM stock_prices WHERE date >= yesterday"
    except Exception as e:
        result['sample_query_total_ms'] = None
        result['sample_query_status'] = 'unknown'
        result['sample_query_error'] = str(e)

    if result.get('connection_acquire_ms') is not None and result.get('sample_query_total_ms') is not None:
        pure_query_ms = max(0.0, result['sample_query_total_ms'] - result['connection_acquire_ms'])
        result['pure_query_ms'] = round(pure_query_ms, 1)
        result['connection_overhead_pct'] = round(
            result['connection_acquire_ms'] / result['sample_query_total_ms'] * 100, 1
        ) if result['sample_query_total_ms'] > 0 else 0.0

    result['pooling_active'] = pooling_active
    return result
