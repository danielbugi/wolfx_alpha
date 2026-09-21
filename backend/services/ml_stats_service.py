# File: backend/services/ml_stats_service.py
"""
ML Stats Service - model accuracy, feature importance, training dataset
composition, live prediction track record, and retraining cadence.

Built 2026-09-18 alongside system_health_service.py, split out specifically
because "is the data pipeline healthy" and "is the model actually good" are
different questions a manager needs answered separately -- this file is the
second one. Depends on ml_models actually being populated, which it wasn't
until the same session wired momentum_predictor.py's register_model() (see
that file) -- rows from before that fix won't exist; only the training run
that follows it (and every one after) will show up here with real
accuracy/precision/recall/F1/AUC instead of just a filename and a date.
"""

import os
import re
import glob
import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date
from typing import Dict, Any, List, Optional

from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

ML_MODELS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'ml_training', 'models')
MODEL_FILE_PATTERN = re.compile(r'momentum_predictor_v(\d{8})_(\d{4})\.joblib$')

# joblib.load() of the XGBoost model is a real, non-trivial disk read +
# deserialize -- cached by model version (not a TTL) since a given model
# file's feature importances never change until it's retrained, at which
# point _find_model_files() picks up a new version and this cache just
# naturally misses once for it.
_feature_importance_cache: Dict[str, Any] = {}

# Full report bundles 5 DB round trips + the feature-importance load above;
# this is a slow-changing monitoring page, not a live feed, so a short TTL
# turns "every request/refresh" into "once every FULL_REPORT_CACHE_TTL_SECONDS".
FULL_REPORT_CACHE_TTL_SECONDS = 120
_full_report_cache: Dict[str, Any] = {"data": None, "ts": 0.0}


def _find_model_files() -> List[Dict[str, Any]]:
    """
    All momentum_predictor_v*.joblib files on disk, newest first -- mirrors
    ml_signal_enhancer.py's own _find_model_files() auto-load logic (newest
    by filename timestamp), so "current model" here always means the same
    model the live screener is actually using.
    """
    candidates = glob.glob(os.path.join(ML_MODELS_DIR, 'momentum_predictor_v*.joblib'))
    candidates = [f for f in candidates if not f.endswith('_scaler.joblib')]

    models = []
    for path in candidates:
        m = MODEL_FILE_PATTERN.search(os.path.basename(path))
        if not m:
            continue
        ts = datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M')
        # "served" == carries the _meta.json contract written by the honest-evaluation trainer. Older
        # models are still on disk but ml_signal_enhancer.py refuses to load them (unvalidated data,
        # train/serve feature skew), so they must not be presented as "the current model".
        served = os.path.exists(path.replace('.joblib', '_meta.json'))
        models.append({'path': path, 'version': os.path.basename(path).replace('.joblib', ''), 'trained_at': ts, 'served': served})

    models.sort(key=lambda x: x['trained_at'], reverse=True)
    return models


class MLStatsService:
    """Read-only ML model / training data / prediction statistics."""

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

    # ------------------------------------------------------------------
    def get_current_model(self) -> Dict[str, Any]:
        all_models = _find_model_files()
        models = [m for m in all_models if m['served']]
        if not models:
            return {
                'found': False,
                'reason': 'No validated model is being served, so ML scores are unavailable in the app. '
                          'No trained model has passed the promotion gate yet -- see "Latest honest evaluation".',
                'legacy_models_ignored': len(all_models),
            }

        latest = models[0]
        days_since_trained = (datetime.now() - latest['trained_at']).days
        meta = {}
        try:
            with open(latest['path'].replace('.joblib', '_meta.json')) as f:
                meta = json.load(f)
        except Exception as e:
            logger.warning(f"model meta read failed: {e}")
        holdout = meta.get('holdout') or {}

        features_path = latest['path'].replace('.joblib', '_features.json')
        feature_count = None
        if os.path.exists(features_path):
            try:
                with open(features_path) as f:
                    feature_count = len(json.load(f))
            except Exception:
                pass

        # Match against ml_models -- present only for training runs since
        # register_model() was added (2026-09-18). Older/pre-existing model
        # files on disk won't have a row; that's expected, not an error.
        registry_row = None
        try:
            rows = self._query(
                "SELECT * FROM ml_models WHERE version = %s ORDER BY created_at DESC LIMIT 1",
                (latest['version'],)
            )
            if rows:
                registry_row = rows[0]
        except Exception as e:
            logger.warning(f"ml_models lookup failed: {e}")

        return {
            'found': True,
            'version': latest['version'],
            'trained_at': latest['trained_at'].isoformat(),
            'days_since_trained': days_since_trained,
            'feature_count': feature_count,
            'registered': registry_row is not None,
            'target': meta.get('target'),
            'target_definition': meta.get('target_definition'),
            'holdout_auc': holdout.get('auc'),
            'holdout_auc_ci95': holdout.get('auc_ci95'),
            'holdout_base_rate': holdout.get('base_rate'),
            'accuracy': float(registry_row['accuracy']) if registry_row and registry_row.get('accuracy') is not None else None,
            'precision': float(registry_row['precision_score']) if registry_row and registry_row.get('precision_score') is not None else None,
            'recall': float(registry_row['recall_score']) if registry_row and registry_row.get('recall_score') is not None else None,
            'f1': float(registry_row['f1_score']) if registry_row and registry_row.get('f1_score') is not None else None,
            'auc': float(registry_row['auc_score']) if registry_row and registry_row.get('auc_score') is not None else None,
            'training_samples': registry_row.get('training_samples') if registry_row else None,
            'training_start_date': registry_row['training_start_date'].isoformat() if registry_row and registry_row.get('training_start_date') else None,
            'training_end_date': registry_row['training_end_date'].isoformat() if registry_row and registry_row.get('training_end_date') else None,
        }

    # ------------------------------------------------------------------
    def get_feature_importance(self, top_n: int = 20) -> Dict[str, Any]:
        models = [m for m in _find_model_files() if m['served']]
        if not models:
            return {'available': False, 'reason': 'No validated model is being served'}

        latest = models[0]
        cache_key = f"{latest['version']}:{top_n}"
        cached = _feature_importance_cache.get(cache_key)
        if cached is not None:
            return cached

        features_path = latest['path'].replace('.joblib', '_features.json')
        if not os.path.exists(features_path):
            return {'available': False, 'reason': 'No features.json sidecar for this model'}

        try:
            import joblib
            model = joblib.load(latest['path'])
            with open(features_path) as f:
                feature_names = json.load(f)

            importances = model.feature_importances_
            paired = list(zip(feature_names, [float(v) for v in importances]))
            paired.sort(key=lambda x: x[1], reverse=True)

            result = {
                'available': True,
                'model_version': latest['version'],
                'features': [{'feature': name, 'importance': imp} for name, imp in paired[:top_n]],
            }
            # Bound growth -- at most a handful of (version, top_n) combos ever
            # get requested in practice, but don't let it grow unbounded.
            if len(_feature_importance_cache) > 20:
                _feature_importance_cache.clear()
            _feature_importance_cache[cache_key] = result
            return result
        except Exception as e:
            logger.warning(f"Feature importance extraction failed: {e}")
            return {'available': False, 'reason': str(e)}

    # ------------------------------------------------------------------
    def get_training_dataset_stats(self) -> Dict[str, Any]:
        """ml_breakout_dataset_v2: built from stock_prices only (see ml_training/data_preparation/build_dataset.py).
        Class shares are over rows whose label window has matured."""
        try:
            r = self._query("""
                SELECT
                    COUNT(*) AS total_rows,
                    MIN(date) AS earliest_date,
                    MAX(date) AS latest_date,
                    COUNT(DISTINCT symbol) AS unique_symbols,
                    COUNT(*) FILTER (WHERE target_binary IS NOT NULL) AS labelled,
                    COUNT(*) FILTER (WHERE target_binary = 1) AS positive_class,
                    COUNT(*) FILTER (WHERE target_binary = 0) AS negative_class,
                    COUNT(*) FILTER (WHERE plan_r > 0) AS plan_profitable,
                    COUNT(*) FILTER (WHERE plan_r IS NOT NULL) AS plan_labelled,
                    COUNT(*) FILTER (WHERE direction = 1) AS bullish_count,
                    COUNT(*) FILTER (WHERE direction = -1) AS bearish_count
                FROM ml_breakout_dataset_v2
            """)
            row = r[0]
            total = row['total_rows'] or 1
            labelled = row['labelled'] or 1
            plan_labelled = row['plan_labelled'] or 1
            disc = self._query("SELECT COUNT(*) AS n, COUNT(DISTINCT symbol) AS syms FROM price_discontinuities")[0]
            return {
                'total_rows': row['total_rows'],
                'unique_symbols': row['unique_symbols'],
                'earliest_date': row['earliest_date'].isoformat() if row['earliest_date'] else None,
                'latest_date': row['latest_date'].isoformat() if row['latest_date'] else None,
                'positive_class_pct': round((row['positive_class'] or 0) / labelled * 100, 1),
                'negative_class_pct': round((row['negative_class'] or 0) / labelled * 100, 1),
                'plan_profitable_pct': round((row['plan_profitable'] or 0) / plan_labelled * 100, 1),
                'bullish_pct': round((row['bullish_count'] or 0) / total * 100, 1),
                'bearish_pct': round((row['bearish_count'] or 0) / total * 100, 1),
                'price_discontinuities': disc['n'],
                'symbols_with_discontinuities': disc['syms'],
            }
        except Exception as e:
            return {'error': str(e)}

    # ------------------------------------------------------------------
    def get_evaluations(self) -> List[Dict[str, Any]]:
        """Newest honest-evaluation report per target (written by momentum_predictor.py, promoted or not)."""
        out = []
        latest_by_target: Dict[str, str] = {}
        for path in sorted(glob.glob(os.path.join(ML_MODELS_DIR, 'candidates', 'report_*.json'))):
            m = re.search(r'report_([a-z_]+?)_\d{8}_\d{4}\.json$', os.path.basename(path))
            if m:
                latest_by_target[m.group(1)] = path   # sorted ascending -> last wins
        for target, path in latest_by_target.items():
            try:
                with open(path) as f:
                    r = json.load(f)
                h = r.get('holdout', {})
                out.append({
                    'target': r.get('target'), 'target_definition': r.get('target_definition'),
                    'generated_at': r.get('generated_at'), 'promoted': r.get('promoted', False),
                    'excluded_features': r.get('excluded_features') or [],
                    'holdout_range': r.get('holdout_range'), 'n_holdout': h.get('n'), 'base_rate': h.get('base_rate'),
                    'auc': h.get('auc'), 'auc_ci95': h.get('auc_ci95'),
                    'direction_baseline_auc': h.get('direction_baseline_auc'),
                    'auc_vs_plan_profit_bullish': h.get('auc_vs_plan_profit_bullish'),
                    'auc_vs_plan_profit_bearish': h.get('auc_vs_plan_profit_bearish'),
                    'top_decile_lift': h.get('top_decile_lift'),
                    'walk_forward_auc': [w.get('auc') for w in r.get('walk_forward', [])],
                    'gate': [{'check': k, **v} for k, v in (r.get('gate') or {}).items()],
                })
            except Exception as e:
                logger.warning(f"evaluation report {path} unreadable: {e}")
        return out

    # ------------------------------------------------------------------
    def get_prediction_track_record(self) -> Dict[str, Any]:
        try:
            r = self._query("""
                SELECT
                    COUNT(*) AS total,
                    MAX(prediction_date) AS latest_date,
                    COUNT(*) FILTER (WHERE ml_confidence = 'very_high') AS very_high,
                    COUNT(*) FILTER (WHERE ml_confidence = 'high') AS high,
                    COUNT(*) FILTER (WHERE ml_confidence = 'medium') AS medium,
                    COUNT(*) FILTER (WHERE ml_confidence = 'low') AS low,
                    COUNT(*) FILTER (WHERE ml_confidence = 'very_low') AS very_low
                FROM ml_predictions
            """)
            pred = r[0]

            r2 = self._query("""
                SELECT
                    COUNT(*) AS total_evaluated,
                    COUNT(*) FILTER (WHERE momentum_achieved = TRUE) AS wins,
                    AVG(actual_return_pct) AS avg_return_pct
                FROM ml_prediction_outcomes
            """)
            outcomes = r2[0]
            total_eval = outcomes['total_evaluated'] or 0

            return {
                'total_predictions': pred['total'],
                'latest_prediction_date': pred['latest_date'].isoformat() if pred['latest_date'] else None,
                'confidence_distribution': [
                    {'confidence': 'very_high', 'count': pred['very_high']},
                    {'confidence': 'high', 'count': pred['high']},
                    {'confidence': 'medium', 'count': pred['medium']},
                    {'confidence': 'low', 'count': pred['low']},
                    {'confidence': 'very_low', 'count': pred['very_low']},
                ],
                'outcomes_evaluated': total_eval,
                'win_rate_pct': round(outcomes['wins'] / total_eval * 100, 1) if total_eval else None,
                'avg_return_pct': round(float(outcomes['avg_return_pct']), 2) if outcomes['avg_return_pct'] is not None else None,
            }
        except Exception as e:
            return {'error': str(e)}

    # ------------------------------------------------------------------
    def get_model_history(self) -> List[Dict[str, Any]]:
        """Every model file on disk, newest first -- shows actual retraining
        cadence regardless of whether ml_models has a matching row."""
        models = _find_model_files()
        registry_by_version = {}
        try:
            rows = self._query("SELECT version, accuracy, auc_score FROM ml_models")
            registry_by_version = {r['version']: r for r in rows}
        except Exception:
            pass

        history = []
        for m in models:
            reg = registry_by_version.get(m['version'])
            history.append({
                'version': m['version'],
                'trained_at': m['trained_at'].isoformat(),
                'served': m['served'],
                'accuracy': float(reg['accuracy']) if reg and reg.get('accuracy') is not None else None,
                'auc': float(reg['auc_score']) if reg and reg.get('auc_score') is not None else None,
            })
        return history

    # ------------------------------------------------------------------
    def get_full_report(self) -> Dict[str, Any]:
        now = time.time()
        cached = _full_report_cache["data"]
        if cached is not None and (now - _full_report_cache["ts"]) < FULL_REPORT_CACHE_TTL_SECONDS:
            return cached

        with ThreadPoolExecutor(max_workers=6) as executor:
            f_model = executor.submit(self.get_current_model)
            f_importance = executor.submit(self.get_feature_importance)
            f_dataset = executor.submit(self.get_training_dataset_stats)
            f_predictions = executor.submit(self.get_prediction_track_record)
            f_history = executor.submit(self.get_model_history)
            f_evals = executor.submit(self.get_evaluations)

            report = {
                'generated_at': datetime.now().isoformat(),
                'current_model': f_model.result(),
                'feature_importance': f_importance.result(),
                'training_dataset': f_dataset.result(),
                'prediction_track_record': f_predictions.result(),
                'model_history': f_history.result(),
                'evaluations': f_evals.result(),
            }

        _full_report_cache["data"] = report
        _full_report_cache["ts"] = now
        return report
