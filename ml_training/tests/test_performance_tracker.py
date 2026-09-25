# ml_training/tests/test_performance_tracker.py
"""Regression coverage for the Phase 3/4A finding: performance_tracker.py used to carry its own
CREATE TABLE IF NOT EXISTS DDL for ml_predictions/ml_prediction_outcomes/ml_performance_metrics,
duplicating and silently diverging from mechanism/add_ml_prediction_tracking_tables.sql (the
tracked migration -- the only place these tables' schema should be defined). No real Postgres
needed: psycopg2 is mocked so this runs everywhere, including CI.
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evaluation"))

from performance_tracker import MLPerformanceTracker  # noqa: E402


def _tracker_with_mock_conn():
    tracker = MLPerformanceTracker()
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    tracker.get_db_connection = MagicMock(return_value=conn)
    return tracker, conn, cursor


def test_setup_performance_tables_never_issues_create_table():
    """The method must only ever SELECT (an existence check) -- never CREATE TABLE. This is the
    direct regression guard against the dead DDL silently coming back."""
    tracker, conn, cursor = _tracker_with_mock_conn()
    cursor.fetchone.return_value = (3,)

    tracker.setup_performance_tables()

    for call in cursor.execute.call_args_list:
        sql = call.args[0].upper()
        assert "CREATE TABLE" not in sql, f"setup_performance_tables() must not define schema: {sql}"
    conn.commit.assert_not_called()  # a read-only existence check never needs to commit


def test_setup_performance_tables_true_when_all_three_present():
    tracker, conn, cursor = _tracker_with_mock_conn()
    cursor.fetchone.return_value = (3,)
    assert tracker.setup_performance_tables() is True


def test_setup_performance_tables_false_when_migration_not_yet_applied():
    tracker, conn, cursor = _tracker_with_mock_conn()
    cursor.fetchone.return_value = (0,)
    assert tracker.setup_performance_tables() is False


def test_setup_performance_tables_false_when_connection_unavailable():
    tracker = MLPerformanceTracker()
    tracker.get_db_connection = MagicMock(return_value=None)
    assert tracker.setup_performance_tables() is False
