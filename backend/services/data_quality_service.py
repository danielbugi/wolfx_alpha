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

# The confirmatory spec `fundamental_inflection_pit v1` (mechanism/research/strategy_lab): events public 2023-01-01..2025-03-31 are its test period; public dates from 2025-04-01 are excluded
# because the yfinance-based studies already inspected them. The primary cell is the union of event types at 20 sessions.
TEST_START, TEST_END_EXCLUSIVE = "2023-01-01", "2025-04-01"
HORIZON_SESSIONS = 20
MIN_EVENTS, MIN_COMPANIES, MIN_PUBLIC_WEEKS = 150, 100, 40

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


def pit_stats(db: Any, method_version: str = "inflection_v2_sec_pit") -> Dict[str, Any]:
    """What the point-in-time family actually holds, for the confirmation gates. Read-only."""
    f = db.execute_dict_query(
        """SELECT COUNT(*) AS features,
                  COUNT(*) FILTER (WHERE quality_notes::text LIKE '%%pit_as_of:%%' AND quality_notes::text LIKE '%%prov:q=%%') AS with_provenance
           FROM fundamental_feature WHERE method_version = %s""", (method_version,))[0]
    # an event's window is complete when at least HORIZON+1 sessions of the company's prices follow its public date (entry open next session, exit close HORIZON sessions later)
    w = db.execute_dict_query(
        """SELECT COUNT(*) AS events_in_test_period,
                  COUNT(*) FILTER (WHERE n_after > %s) AS complete,
                  COUNT(DISTINCT symbol) FILTER (WHERE n_after > %s) AS companies,
                  COUNT(DISTINCT date_trunc('week', available_at)) FILTER (WHERE n_after > %s) AS weeks
           FROM (SELECT e.symbol, e.available_at,
                        (SELECT COUNT(*) FROM (SELECT 1 FROM stock_prices p WHERE p.symbol = e.symbol AND p.date > e.available_at LIMIT %s) x) AS n_after
                 FROM fundamental_inflection_event e
                 WHERE e.method_version = %s AND e.available_at >= %s::date AND e.available_at < %s::date) t""",
        (HORIZON_SESSIONS, HORIZON_SESSIONS, HORIZON_SESSIONS, HORIZON_SESSIONS + 2, method_version, TEST_START, TEST_END_EXCLUSIVE))[0]
    return {"features": int(f["features"] or 0), "features_with_provenance": int(f["with_provenance"] or 0),
            "events_in_test_period": int(w["events_in_test_period"] or 0), "complete_window_events": int(w["complete"] or 0),
            "companies": int(w["companies"] or 0), "public_weeks": int(w["weeks"] or 0)}


def confirmation_gates(coverage_ratio: float, s: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every gate a point-in-time family must pass before it may back a confirmatory test; coverage alone never qualifies. Order = the order the reasons are reported in."""
    feats, prov = s.get("features", 0), s.get("features_with_provenance", 0)
    return [
        {"gate": "coverage", "ok": coverage_ratio >= CONFIRMATION_MIN_COVERAGE,
         "detail": f"{coverage_ratio * 100:.1f}% of mapped filers ingested (needs {CONFIRMATION_MIN_COVERAGE * 100:.0f}%)"},
        {"gate": "point_in_time_features", "ok": feats > 0 and prov == feats,
         "detail": "no point-in-time features stored yet" if feats == 0 else f"{prov} of {feats} features carry filing-version provenance (all must)"},
        {"gate": "complete_forward_windows", "ok": s.get("complete_window_events", 0) >= MIN_EVENTS,
         "detail": f"{s.get('complete_window_events', 0)} events public {TEST_START} to {TEST_END_EXCLUSIVE} have a complete {HORIZON_SESSIONS}-session return window (needs {MIN_EVENTS}; "
                   f"{s.get('events_in_test_period', 0)} stored in that period)"},
        {"gate": "independent_companies", "ok": s.get("companies", 0) >= MIN_COMPANIES,
         "detail": f"{s.get('companies', 0)} distinct companies among them (needs {MIN_COMPANIES})"},
        {"gate": "independent_public_weeks", "ok": s.get("public_weeks", 0) >= MIN_PUBLIC_WEEKS,
         "detail": f"{s.get('public_weeks', 0)} distinct public weeks among them (needs {MIN_PUBLIC_WEEKS})"},
        {"gate": "untouched_holdout", "ok": True,
         "detail": f"only events public {TEST_START} to {TEST_END_EXCLUSIVE} are counted; dates from {TEST_END_EXCLUSIVE} are excluded (already inspected) and the six forward-only hypotheses are never reused"},
    ]


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
            stats = pit_stats(db, ns["method_version"])
            gates = confirmation_gates(coverage_ratio, stats)
            failing = [g for g in gates if not g["ok"]]
            row["gates"], row["stats"] = gates, stats
            row["role_now"] = "exploratory" if failing else "confirmation_eligible"
            row["role_reason"] = ("INSUFFICIENT_DATA: " + "; ".join(g["detail"] for g in failing)) if failing else "all confirmation gates pass"
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
