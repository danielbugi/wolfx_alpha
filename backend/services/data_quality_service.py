"""
Quant Lab data quality - the backend side. READ-ONLY: SEC ingestion coverage, SEC-vs-yfinance event reconciliation, and which event namespaces are valid for what kind of study.

* Every request opens its transaction with `SET TRANSACTION READ ONLY`; nothing here writes, and nothing influences screening, scoring, alerts or the research ledger.
* The pure logic lives in mechanism (data_updaters.sec_coverage, inflection.reconcile / reconcile_db); this service only gives it a read-only cursor and a short cache (default 10 min),
  because coverage and reconciliation scan the SEC and inflection tables.
* If the database or the tables are unavailable the answer is an explicit `available: false` payload with a generic reason - never an empty success, never a 500.
* `namespaces` states, for every stored event family, whether it is point-in-time and therefore what it may be used for. Exploratory analysis is allowed on any of them; only a
  filing-version-aware (point-in-time) family with enough coverage may back a confirmatory test, and only on data the hypothesis was not looked at on.
"""
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

_MECHANISM = str(Path(__file__).resolve().parents[2] / "mechanism")
if _MECHANISM not in sys.path:
    sys.path.append(_MECHANISM)

CACHE_SECONDS = 600.0
CONFIRMATION_MIN_COVERAGE = 0.80          # share of mapped US-domestic filers whose SEC history must be ingested before a point-in-time family may back a confirmatory test

NAMESPACES: List[Dict[str, Any]] = [
    {"method_version": "inflection_v1", "label": "yfinance statements", "source": "quarterly_fundamentals", "point_in_time": False,
     "availability": "earnings-report date, else an assumed 75-day lag", "figures": "restated (latest values)", "role": "exploratory",
     "note": "Only 5-6 quarters per company. The 2026-10-31 study ran on it and its test period has been inspected: nothing here can confirm anything."},
    {"method_version": "inflection_v1_sec", "label": "SEC, latest values", "source": "sec_edgar", "point_in_time": False,
     "availability": "SEC acceptance date (US Eastern) of the first filing", "figures": "latest values (a restatement overwrites)", "role": "exploratory",
     "note": "Long history, but each figure is the latest the SEC holds, so a restatement filed years later leaks into the earlier quarter. Exploration only; not evidence of tradable alpha."},
    {"method_version": "inflection_v2_sec_pit", "label": "SEC, filing-version-aware", "source": "sec_edgar_pit", "point_in_time": True,
     "availability": "SEC acceptance date (US Eastern) of the first filing", "figures": "as published: only filings public at that date", "role": "confirmation_eligible",
     "note": "Every figure is the one public when the quarter was first published; a restatement is reported but never rewrites the stored feature. Eligible for confirmatory tests once coverage is sufficient, on data not previously inspected."},
]


def _unavailable(reason: str) -> Dict[str, Any]:
    return {"schema": "data_quality_v1", "available": False, "reason": reason}


class _ReadOnlyDb:
    """Gives mechanism's pure helpers the one method they use, on a read-only backend connection."""

    def __init__(self, cur: Any):
        self._cur = cur

    def execute_dict_query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        self._cur.execute(sql, params)
        return [dict(r) for r in self._cur.fetchall()]


def build_payload(db: Any) -> Dict[str, Any]:
    from data_updaters import sec_coverage
    from inflection import reconcile_db

    cov = sec_coverage.coverage(db)
    counts = {r["method_version"]: r for r in db.execute_dict_query(
        """SELECT method_version, COUNT(*) AS features, COUNT(DISTINCT symbol) AS symbols, MAX(available_at)::text AS newest_available,
                  (SELECT COUNT(*) FROM fundamental_inflection_event e WHERE e.method_version = f.method_version) AS events
           FROM fundamental_feature f GROUP BY method_version""")}
    mapped = max(1, int(cov["universe"].get("with_cik") or 0))
    ingested = int(cov["universe"].get("with_revenue_quarters") or 0)
    coverage_ratio = ingested / mapped
    namespaces = []
    for ns in NAMESPACES:
        c = counts.get(ns["method_version"], {})
        row = {**ns, "features": int(c.get("features") or 0), "events": int(c.get("events") or 0), "symbols": int(c.get("symbols") or 0), "newest_available": c.get("newest_available")}
        if ns["role"] == "confirmation_eligible":
            ok = coverage_ratio >= CONFIRMATION_MIN_COVERAGE and row["events"] > 0
            row["role_now"] = "confirmation_eligible" if ok else "exploratory"
            row["role_reason"] = (f"{coverage_ratio * 100:.0f}% of mapped filers ingested (needs {CONFIRMATION_MIN_COVERAGE * 100:.0f}%)" if coverage_ratio < CONFIRMATION_MIN_COVERAGE
                                  else "no events stored yet" if row["events"] == 0 else "coverage sufficient")
        else:
            row["role_now"] = ns["role"]
        namespaces.append(row)

    def recon(a: str, b: str) -> Optional[Dict[str, Any]]:
        if not counts.get(a) or not counts.get(b):
            return None
        return reconcile_db.run(a, b, 3, db)

    return {"schema": "data_quality_v1", "available": True, "coverage": cov, "coverage_ratio": round(coverage_ratio, 4), "confirmation_min_coverage": CONFIRMATION_MIN_COVERAGE,
            "namespaces": namespaces,
            "reconciliation": {"sec_vs_yfinance": recon("inflection_v1_sec", "inflection_v1"), "pit_vs_latest_values": recon("inflection_v1_sec", "inflection_v2_sec_pit")},
            "rules": ["Exploratory analysis may use any event family; it is labelled exploratory and is never presented as out-of-sample evidence.",
                      "A confirmatory test needs a point-in-time family, sufficient coverage, and data the hypothesis was not found on (forward-only or an untouched holdout).",
                      "The six registered forward-only hypotheses are evaluated only on events public after 2026-10-31 with a complete window; nothing on this page changes that."]}


class DataQualityService:
    def __init__(self, get_connection: Callable, builder: Callable = build_payload, clock: Callable[[], float] = time.monotonic, cache_seconds: float = CACHE_SECONDS):
        self._get_connection = get_connection
        self._builder = builder
        self._clock = clock
        self._cache_seconds = cache_seconds
        self._cached: Optional[tuple] = None

    @contextmanager
    def _db(self) -> Iterator[_ReadOnlyDb]:
        conn = self._get_connection()
        if not conn:
            raise ConnectionError("database unavailable")
        try:
            from psycopg2.extras import RealDictCursor
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                yield _ReadOnlyDb(cur)
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
            with self._db() as db:
                payload = self._builder(db)
        except ConnectionError:
            return _unavailable("the database is unavailable right now")
        except Exception:  # noqa: BLE001  (generic reason on purpose: no internals in the response)
            return _unavailable("the data quality summary could not be computed")
        self._cached = (now, payload)
        return payload
