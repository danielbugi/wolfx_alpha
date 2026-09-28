"""TrackRecordService against a fake connection (no DB needed), matching
test_market_service_filters.py's style. Focus: the honesty rules -- a resolved sample below
MIN_SAMPLE_SIZE never gets a win rate, and open/resolved counts are always reported separately.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.track_record_service import TrackRecordService, MIN_SAMPLE_SIZE  # noqa: E402


class _Cursor:
    def __init__(self, fetchall_rows, fetchone_rows):
        self._all = list(fetchall_rows)
        self._one = list(fetchone_rows)

    def execute(self, *_a, **_k):
        pass

    def fetchall(self):
        return self._all.pop(0)

    def fetchone(self):
        return self._one.pop(0)

    def close(self):
        pass


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, cursor_factory=None):
        return self._cursor

    def close(self):
        pass


def _service(summary_row, by_status_rows):
    cursor = _Cursor([by_status_rows], [summary_row])
    return TrackRecordService(lambda: _Conn(cursor))


def test_below_min_sample_size_is_suppressed():
    row = {
        "resolved_count": MIN_SAMPLE_SIZE - 1, "open_count": 2, "resolved_positive": 1,
        "avg_outcome_r": 0.5, "earliest_signal_date": "2026-09-01", "latest_signal_date": "2026-09-27",
    }
    out = _service(row, [{"status": "target1", "n": MIN_SAMPLE_SIZE - 1}]).get_summary()
    assert out["suppressed"] is True
    assert "win_rate_pct" not in out
    assert "avg_outcome_r" not in out
    assert out["resolved_count"] == MIN_SAMPLE_SIZE - 1
    assert out["open_count"] == 2  # open count always reported, even while suppressed


def test_at_min_sample_size_reports_stats():
    row = {
        "resolved_count": MIN_SAMPLE_SIZE, "open_count": 0, "resolved_positive": 3,
        "avg_outcome_r": 0.28, "earliest_signal_date": "2026-09-01", "latest_signal_date": "2026-09-27",
    }
    out = _service(row, [{"status": "target1", "n": 3}, {"status": "stopped", "n": 2}]).get_summary()
    assert out["suppressed"] is False
    assert out["win_rate_pct"] == 60.0
    assert out["avg_outcome_r"] == 0.28
    assert out["by_status"] == {"target1": 3, "stopped": 2}


def test_zero_resolved_signals_never_divides_by_zero():
    row = {
        "resolved_count": 0, "open_count": 5, "resolved_positive": 0,
        "avg_outcome_r": None, "earliest_signal_date": None, "latest_signal_date": None,
    }
    out = _service(row, []).get_summary()
    assert out["suppressed"] is True
    assert out["resolved_count"] == 0
    assert out["open_count"] == 5
