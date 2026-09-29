"""TrackRecordService against a fake connection (no DB needed), matching
test_market_service_filters.py's style. Focus: the honesty rules -- a resolved sample below
MIN_SAMPLE_SIZE never gets a win rate, and open/resolved counts are always reported separately.

The summary is computed by mechanism/strategy_analytics (the canonical layer), so the fake cursor
answers that layer's two queries: the reference session and the grouped aggregate. Real SQL is
covered against Postgres in mechanism/strategy_analytics/tests and test_strategy_intelligence_api.py.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.track_record_service import TrackRecordService, MIN_SAMPLE_SIZE  # noqa: E402


def _agg_total(resolved=0, open_count=0, avg_r=None, first=None, latest=None, **statuses):
    counts = {s: statuses.get(s, 0) for s in ("stopped", "target1", "target2", "target3", "expired")}
    assert sum(counts.values()) == resolved, "fixture statuses must add up to resolved"
    return {
        "is_total": 1, "direction": None, "signals": resolved + open_count, "open_total": open_count,
        "normally_open": open_count, "held": 0, "resolved": resolved,
        "winners": counts["target1"] + counts["target2"] + counts["target3"], **counts, "ambiguous": 0,
        "avg_r": avg_r, "median_r": avg_r, "expired_avg_r": None, "avg_bars": None, "median_bars": None,
        "avg_mae_r": None, "first_session": first, "latest_session": latest, "this_week": 0, "this_month": 0,
    }


class _Cursor:
    def __init__(self, total_row):
        self._total = total_row
        self._sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, *_a, **_k):
        self._sql = sql

    def fetchall(self):
        if "GROUPING(direction)" in self._sql:
            return [self._total]  # no per-direction rows: the layer zero-fills them
        if "max(date)" in self._sql:
            return [{"d": None}]
        raise AssertionError(f"unexpected query: {self._sql[:80]}")


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, cursor_factory=None):
        return self._cursor

    def close(self):
        pass


def _service(total_row):
    cursor = _Cursor(total_row)
    return TrackRecordService(lambda: _Conn(cursor))


def test_below_min_sample_size_is_suppressed():
    row = _agg_total(resolved=MIN_SAMPLE_SIZE - 1, open_count=2, avg_r=0.5, first="2026-09-01",
                     latest="2026-09-27", target1=1, stopped=MIN_SAMPLE_SIZE - 2)
    out = _service(row).get_summary()
    assert out["suppressed"] is True
    assert "win_rate_pct" not in out
    assert "avg_outcome_r" not in out
    assert out["resolved_count"] == MIN_SAMPLE_SIZE - 1
    assert out["open_count"] == 2  # open count always reported, even while suppressed


def test_at_min_sample_size_reports_stats():
    row = _agg_total(resolved=MIN_SAMPLE_SIZE, open_count=0, avg_r=0.28, first="2026-09-01",
                     latest="2026-09-27", target1=3, stopped=2)
    out = _service(row).get_summary()
    assert out["suppressed"] is False
    assert out["win_rate_pct"] == 60.0
    assert out["avg_outcome_r"] == 0.28
    assert out["by_status"] == {"target1": 3, "stopped": 2}


def test_zero_resolved_signals_never_divides_by_zero():
    row = _agg_total(resolved=0, open_count=5, avg_r=None)
    out = _service(row).get_summary()
    assert out["suppressed"] is True
    assert out["resolved_count"] == 0
    assert out["open_count"] == 5


def test_a_positive_r_expiry_is_not_a_win():
    """Canonical definition (strategy_analytics.definitions): a win is a target reached before the stop.
    The pre-2026-09-29 track record counted any outcome_r > 0 -- so these five expiries were 100% wins."""
    row = _agg_total(resolved=MIN_SAMPLE_SIZE, open_count=0, avg_r=0.4, expired=MIN_SAMPLE_SIZE)
    out = _service(row).get_summary()
    assert out["suppressed"] is False
    assert out["win_rate_pct"] == 0.0
    assert out["avg_outcome_r"] == 0.4
    assert out["by_status"] == {"expired": MIN_SAMPLE_SIZE}
