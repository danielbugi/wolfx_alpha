# File: backend/routers/ml_stats.py
"""
ML Stats API Router - model accuracy, feature importance, training data
composition, prediction track record, retraining cadence. See
services/ml_stats_service.py for why this is split from system_health.
"""

from fastapi import APIRouter, Depends
import logging

from services.ml_stats_service import MLStatsService

logger = logging.getLogger(__name__)

ml_stats_router = APIRouter(
    prefix="/api/ml-stats",
    tags=["ml-stats"],
    responses={404: {"description": "Not found"}},
)


def get_ml_stats_service():
    from main import get_database_connection
    return MLStatsService(get_database_connection)


@ml_stats_router.get("/")
def full_report(service: MLStatsService = Depends(get_ml_stats_service)):
    return service.get_full_report()


@ml_stats_router.get("/model")
def current_model(service: MLStatsService = Depends(get_ml_stats_service)):
    return service.get_current_model()


@ml_stats_router.get("/feature-importance")
def feature_importance(service: MLStatsService = Depends(get_ml_stats_service)):
    return service.get_feature_importance()


@ml_stats_router.get("/training-dataset")
def training_dataset(service: MLStatsService = Depends(get_ml_stats_service)):
    return service.get_training_dataset_stats()


@ml_stats_router.get("/predictions")
def predictions(service: MLStatsService = Depends(get_ml_stats_service)):
    return service.get_prediction_track_record()


@ml_stats_router.get("/history")
def model_history(service: MLStatsService = Depends(get_ml_stats_service)):
    return service.get_model_history()
