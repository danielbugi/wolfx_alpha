# File: backend/routers/system_health.py
"""
System Health API Router - production-readiness checks.

Distinct from /api/health (main.py) which is a basic liveness check --
this answers "is everything fitted and working well" across pipeline
freshness, data quality, ML model health, and universe coverage. See
services/system_health_service.py for why all four matter together.
"""

from fastapi import APIRouter, Depends

from auth.dependencies import require_authenticated_user
import logging

from services.system_health_service import SystemHealthService

logger = logging.getLogger(__name__)

system_health_router = APIRouter(
    prefix="/api/system-health",
    tags=["system-health"],
    responses={404: {"description": "Not found"}},
    dependencies=[Depends(require_authenticated_user)],
)


def get_system_health_service():
    from main import get_database_connection
    return SystemHealthService(get_database_connection)


@system_health_router.get("/")
def full_report(service: SystemHealthService = Depends(get_system_health_service)):
    """Combined report: pipeline freshness, data quality, ML health, universe coverage."""
    return service.get_full_report()


@system_health_router.get("/freshness")
def freshness(service: SystemHealthService = Depends(get_system_health_service)):
    return service.get_pipeline_freshness()


@system_health_router.get("/quality")
def quality(service: SystemHealthService = Depends(get_system_health_service)):
    return service.get_data_quality()


@system_health_router.get("/ml")
def ml_health(service: SystemHealthService = Depends(get_system_health_service)):
    return service.get_ml_health()


@system_health_router.get("/coverage")
def coverage(service: SystemHealthService = Depends(get_system_health_service)):
    return service.get_universe_coverage()
