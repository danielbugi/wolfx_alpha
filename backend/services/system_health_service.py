# File: backend/services/system_health_service.py
"""
System Health Service - production-readiness checks across four areas:
pipeline freshness, data quality, ML model health, and universe coverage.

Built 2026-09-18 alongside the Tiingo/Russell-3000 migration, specifically
so "is everything fitted and working well" (the user's framing) is answered
by looking at all four together, not any one in isolation -- each catches a
different failure mode a single metric would miss (see the health-dashboard
conversation in project history): the pipeline silently stopping (yfinance
outage, 2025-11 to 2026-09), data flowing but wrong (NULL indicators, price
anomalies), a model going stale unnoticed (live model unretrained since
2025-07-29), or the universe expansion not actually finishing.
"""

import os
import glob
import re
import time
from concurrent.futures import ThreadPoolExecutor
from psycopg2.extras import RealDictCursor
from typing import Dict, Any, List
from datetime import datetime, date
import logging

logger = logging.getLogger(__name__)

# Relative to backend/ (where uvicorn runs from) -- see CLAUDE.md directory map
ML_MODELS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'ml_training', 'models')
MODEL_FILE_PATTERN = re.compile(r'momentum_predictor_v(\d{8})_(\d{4})\.joblib$')

# This is a production-readiness dashboard, not a live trading feed -- a
# couple of minutes of staleness is fine and turns "~15 queries across 4
# sections, some of them full-table scans, on every single request/refresh"
# into "once every FULL_REPORT_CACHE_TTL_SECONDS". Module-level so it survives
# across the per-request SystemHealthService instances (see
# routers/system_health.py's Depends).
FULL_REPORT_CACHE_TTL_SECONDS = 120
_full_report_cache: Dict[str, Any] = {"data": None, "ts": 0.0}

# stock_prices' active-symbol count is computed by both get_data_quality()
# and get_universe_coverage() -- cache it once (short TTL, this table only
# changes once/day) so a full report doesn't pay for the same full-table
# COUNT(DISTINCT symbol) twice.
ACTIVE_SYMBOLS_CACHE_TTL_SECONDS = 60
_active_symbols_cache: Dict[str, Any] = {"value": None, "ts": 0.0}


def _status_from_age(days_stale: float, warn_at: float, critical_at: float) -> str:
    if days_stale is None:
        return 'unknown'
    if days_stale > critical_at:
        return 'critical'
    if days_stale > warn_at:
        return 'warning'
    return 'healthy'


def _days_stale(latest, today: date):
    """Days since `latest`. Clamped at 0 -- weekly/monthly tables key rows
    by period-END date, so the current in-progress period's row is dated in
    the future relative to today; that's current, not stale, not a
    negative number of days."""
    if latest is None:
        return None
    if isinstance(latest, datetime):
        latest = latest.date()
    return max(0, (today - latest).days)


class SystemHealthService:
    """Read-only system health / production-readiness checks."""

    def __init__(self, db_connection_func):
        self.get_db_connection = db_connection_func

    def _query(self, sql: str, params=None) -> List[Dict[str, Any]]:
        conn = self.get_db_connection()
        if not conn:
            raise Exception("Database connection failed")
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def _get_active_symbols_count(self) -> int:
        now = time.time()
        if _active_symbols_cache["value"] is not None and (now - _active_symbols_cache["ts"]) < ACTIVE_SYMBOLS_CACHE_TTL_SECONDS:
            return _active_symbols_cache["value"]
        r = self._query("""
            SELECT COUNT(DISTINCT symbol) AS n FROM stock_prices
            WHERE symbol NOT IN (SELECT symbol FROM inactive_symbols)
        """)
        n = r[0]['n']
        _active_symbols_cache["value"] = n
        _active_symbols_cache["ts"] = now
        return n

    # ------------------------------------------------------------------
    # 1. Pipeline freshness
    # ------------------------------------------------------------------
    def get_pipeline_freshness(self) -> Dict[str, Any]:
        today = date.today()
        checks = []

        freshness_queries = [
            ('Daily prices', 'stock_prices', 'MAX(date)', 1, 5),
            ('Daily technical indicators', 'technical_indicators', 'MAX(date)', 1, 5),
            ('Weekly technical indicators', 'weekly_technical_indicators', 'MAX(week_ending_date)', 8, 15),
            ('Monthly technical indicators', 'monthly_technical_indicators', 'MAX(month_ending_date)', 35, 65),
            ('Daily fundamentals', 'daily_fundamentals', 'MAX(date)', 3, 10),
            ('Quarterly fundamentals', 'quarterly_fundamentals', 'MAX(quarter)', 100, 200),
        ]

        for label, table, expr, warn_days, critical_days in freshness_queries:
            try:
                r = self._query(f"SELECT {expr} AS latest FROM {table}")
                latest = r[0]['latest'] if r else None
                stale = _days_stale(latest, today)
                checks.append({
                    'label': label,
                    'latest_date': latest.isoformat() if latest else None,
                    'days_stale': stale,
                    'status': _status_from_age(stale, warn_days, critical_days),
                })
            except Exception as e:
                checks.append({'label': label, 'status': 'unknown', 'error': str(e)})

        overall = 'healthy'
        if any(c['status'] == 'critical' for c in checks):
            overall = 'critical'
        elif any(c['status'] in ('warning', 'unknown') for c in checks):
            overall = 'warning'

        return {'overall': overall, 'checks': checks}

    # ------------------------------------------------------------------
    # 2. Data quality
    # ------------------------------------------------------------------
    def get_data_quality(self) -> Dict[str, Any]:
        checks = []

        # NULL rate on core indicators over the last 30 days -- should be
        # near-zero for any symbol with enough history for the 20-day
        # Donchian / 14-day RSI warmup to have completed.
        try:
            r = self._query("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE donchian_high_20 IS NULL) AS null_donchian,
                    COUNT(*) FILTER (WHERE rsi_14 IS NULL) AS null_rsi,
                    COUNT(*) FILTER (WHERE atr_14 IS NULL) AS null_atr
                FROM technical_indicators
                WHERE date >= CURRENT_DATE - INTERVAL '30 days'
            """)
            row = r[0]
            total = row['total'] or 1
            null_pct = max(row['null_donchian'], row['null_rsi'], row['null_atr']) / total * 100
            checks.append({
                'label': 'Missing indicator values (last 30 days)',
                'detail': f"{null_pct:.2f}% NULL across donchian/rsi/atr ({row['total']} rows checked)",
                'status': 'critical' if null_pct > 5 else ('warning' if null_pct > 1 else 'healthy'),
            })
        except Exception as e:
            checks.append({'label': 'Missing indicator values', 'status': 'unknown', 'error': str(e)})

        # Price sanity: impossible OHLC relationships or non-positive prices
        try:
            r = self._query("""
                SELECT COUNT(*) AS bad_rows FROM stock_prices
                WHERE date >= CURRENT_DATE - INTERVAL '30 days'
                  AND (high < low OR close <= 0 OR open <= 0 OR volume < 0)
            """)
            bad = r[0]['bad_rows']
            checks.append({
                'label': 'Price anomalies (last 30 days)',
                'detail': f"{bad} rows with impossible OHLCV values",
                'status': 'critical' if bad > 0 else 'healthy',
            })
        except Exception as e:
            checks.append({'label': 'Price anomalies', 'status': 'unknown', 'error': str(e)})

        # Fundamentals coverage: active symbols with a recent daily_fundamentals row
        try:
            active_symbols = self._get_active_symbols_count()
            r = self._query("""
                SELECT COUNT(DISTINCT symbol) AS covered_symbols FROM daily_fundamentals
                WHERE date >= CURRENT_DATE - INTERVAL '35 days'
            """)
            covered = r[0]['covered_symbols']
            active = active_symbols or 1
            pct = covered / active * 100
            checks.append({
                'label': 'Fundamentals coverage',
                'detail': f"{covered:,}/{active_symbols:,} active symbols ({pct:.1f}%)",
                'status': 'critical' if pct < 50 else ('warning' if pct < 90 else 'healthy'),
            })
        except Exception as e:
            checks.append({'label': 'Fundamentals coverage', 'status': 'unknown', 'error': str(e)})

        overall = 'healthy'
        if any(c['status'] == 'critical' for c in checks):
            overall = 'critical'
        elif any(c['status'] in ('warning', 'unknown') for c in checks):
            overall = 'warning'

        return {'overall': overall, 'checks': checks}

    # ------------------------------------------------------------------
    # 3. ML model health
    # ------------------------------------------------------------------
    def get_ml_health(self) -> Dict[str, Any]:
        checks = []
        today = date.today()

        # Latest deployed model file, by filename timestamp -- mirrors
        # ml_signal_enhancer.py's own _find_model_files() auto-load logic,
        # so this reports on the model actually in use, not just any model
        # that happens to exist on disk.
        try:
            candidates = glob.glob(os.path.join(ML_MODELS_DIR, 'momentum_predictor_v*.joblib'))
            # Only models with the _meta.json contract are loaded by ml_signal_enhancer.py; legacy files
            # still on disk are ignored there, so they must not count as a healthy "live model" here.
            candidates = [f for f in candidates
                          if not f.endswith('_scaler.joblib') and os.path.exists(f.replace('.joblib', '_meta.json'))]
            latest_model = None
            latest_ts = None
            for path in candidates:
                m = MODEL_FILE_PATTERN.search(os.path.basename(path))
                if m:
                    ts = datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M')
                    if latest_ts is None or ts > latest_ts:
                        latest_ts = ts
                        latest_model = os.path.basename(path)

            if latest_model:
                stale_days = (today - latest_ts.date()).days
                checks.append({
                    'label': 'Live model freshness',
                    'detail': f"{latest_model} ({stale_days} days since training)",
                    'status': 'critical' if stale_days > 90 else ('warning' if stale_days > 30 else 'healthy'),
                })
            else:
                checks.append({'label': 'Live model freshness', 'status': 'critical',
                               'detail': 'No validated model is being served -- ML scores are unavailable '
                                         '(no trained model has passed the promotion gate; see /ml-stats)'})
        except Exception as e:
            checks.append({'label': 'Live model freshness', 'status': 'unknown', 'error': str(e)})

        # Prediction activity
        try:
            r = self._query("SELECT COUNT(*) AS n, MAX(prediction_date) AS latest FROM ml_predictions")
            row = r[0]
            stale = _days_stale(row['latest'], today)
            checks.append({
                'label': 'ML predictions being recorded',
                'detail': f"{row['n']:,} total, latest {row['latest'].isoformat() if row['latest'] else 'never'}",
                'status': _status_from_age(stale, 3, 10) if row['n'] else 'critical',
            })
        except Exception as e:
            checks.append({'label': 'ML predictions being recorded', 'status': 'unknown', 'error': str(e)})

        # Outcome evaluation -- whether predictions are ever checked against
        # what actually happened (performance_tracker.py's job; per
        # CLAUDE.md this has historically never run in production)
        try:
            r = self._query("SELECT COUNT(*) AS n FROM ml_prediction_outcomes")
            n = r[0]['n']
            checks.append({
                'label': 'Prediction outcomes evaluated',
                'detail': f"{n:,} outcomes recorded" if n else "0 -- predictions are never evaluated against actual results",
                'status': 'healthy' if n > 0 else 'warning',
            })
        except Exception as e:
            checks.append({'label': 'Prediction outcomes evaluated', 'status': 'unknown', 'error': str(e)})

        # Model registry usage
        try:
            r = self._query("SELECT COUNT(*) AS n FROM ml_models")
            n = r[0]['n']
            checks.append({
                'label': 'Model registry (ml_models table)',
                'detail': f"{n} registered model(s)" if n else "0 -- model_registry.py exists but isn't used to gate production",
                'status': 'healthy' if n > 0 else 'warning',
            })
        except Exception as e:
            checks.append({'label': 'Model registry', 'status': 'unknown', 'error': str(e)})

        overall = 'healthy'
        if any(c['status'] == 'critical' for c in checks):
            overall = 'critical'
        elif any(c['status'] in ('warning', 'unknown') for c in checks):
            overall = 'warning'

        return {'overall': overall, 'checks': checks}

    # ------------------------------------------------------------------
    # 4. Universe coverage
    # ------------------------------------------------------------------
    def get_universe_coverage(self) -> Dict[str, Any]:
        try:
            active_symbols = self._get_active_symbols_count()

            r = self._query("""
                SELECT
                    (SELECT COUNT(DISTINCT symbol) FROM stock_prices) AS total_symbols,
                    (SELECT COUNT(*) FROM inactive_symbols) AS inactive_symbols,
                    (SELECT COUNT(DISTINCT symbol) FROM quarterly_fundamentals) AS symbols_with_quarterly,
                    (SELECT COUNT(DISTINCT symbol) FROM stock_prices
                     WHERE date <= CURRENT_DATE - INTERVAL '9 years') AS symbols_with_10y_history
            """)
            row = r[0]
            total = row['total_symbols'] or 1

            sectors = self._query("""
                SELECT sector, COUNT(DISTINCT symbol) AS n
                FROM daily_fundamentals
                WHERE date >= CURRENT_DATE - INTERVAL '35 days' AND sector IS NOT NULL
                GROUP BY sector ORDER BY n DESC
            """)

            return {
                'overall': 'healthy',
                'total_symbols': row['total_symbols'],
                'active_symbols': active_symbols,
                'inactive_symbols': row['inactive_symbols'],
                'quarterly_fundamentals_coverage_pct': round(row['symbols_with_quarterly'] / total * 100, 1),
                'ten_year_history_coverage_pct': round(row['symbols_with_10y_history'] / total * 100, 1),
                'sector_breakdown': sectors,
            }
        except Exception as e:
            return {'overall': 'unknown', 'error': str(e)}

    # ------------------------------------------------------------------
    def get_full_report(self) -> Dict[str, Any]:
        now = time.time()
        cached = _full_report_cache["data"]
        if cached is not None and (now - _full_report_cache["ts"]) < FULL_REPORT_CACHE_TTL_SECONDS:
            return cached

        # The 4 sections are independent read-only queries against different
        # tables -- run them concurrently instead of one after another so a
        # cache-miss pays for the slowest section, not the sum of all four.
        with ThreadPoolExecutor(max_workers=4) as executor:
            f_freshness = executor.submit(self.get_pipeline_freshness)
            f_quality = executor.submit(self.get_data_quality)
            f_ml = executor.submit(self.get_ml_health)
            f_coverage = executor.submit(self.get_universe_coverage)

            freshness = f_freshness.result()
            quality = f_quality.result()
            ml = f_ml.result()
            coverage = f_coverage.result()

        statuses = [freshness['overall'], quality['overall'], ml['overall'], coverage.get('overall', 'unknown')]
        if 'critical' in statuses:
            overall = 'critical'
        elif 'warning' in statuses or 'unknown' in statuses:
            overall = 'warning'
        else:
            overall = 'healthy'

        report = {
            'overall': overall,
            'generated_at': datetime.now().isoformat(),
            'pipeline_freshness': freshness,
            'data_quality': quality,
            'ml_health': ml,
            'universe_coverage': coverage,
        }
        _full_report_cache["data"] = report
        _full_report_cache["ts"] = now
        return report
