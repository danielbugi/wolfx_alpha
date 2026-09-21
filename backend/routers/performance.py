# File: backend/routers/performance.py
"""
Performance API Router - scored/tracked latency for backend endpoints, the
database, and frontend routes. See services/performance_service.py for why
this exists and what it measures.
"""

from datetime import datetime
from fastapi import APIRouter
from pydantic import BaseModel
import logging

from services.performance_service import tracker, measure_database

logger = logging.getLogger(__name__)

performance_router = APIRouter(
    prefix="/api/performance",
    tags=["performance"],
    responses={404: {"description": "Not found"}},
)


class ClientMetricPayload(BaseModel):
    route: str
    metric: str  # e.g. "initial_load_ms" or "route_change_ms"
    duration_ms: float


def _overall_status(statuses: list) -> str:
    if 'critical' in statuses:
        return 'critical'
    if 'warning' in statuses or 'unknown' in statuses:
        return 'warning'
    return 'healthy'


@performance_router.get("/")
async def full_report():
    from main import get_database_connection, db_pool

    endpoints = tracker.get_endpoint_report()
    routes = tracker.get_route_report()
    database = measure_database(get_database_connection, pooling_active=db_pool is not None)

    statuses = [e['status'] for e in endpoints] + [r['status'] for r in routes]
    statuses += [database.get('connection_status', 'unknown'), database.get('sample_query_status', 'unknown')]

    return {
        'generated_at': datetime.now().isoformat(),
        'overall': _overall_status(statuses),
        'backend_uptime_seconds': tracker.uptime_seconds(),
        'backend_endpoints': endpoints,
        'frontend_routes': routes,
        'database': database,
    }


@performance_router.get("/endpoints")
async def endpoints_report():
    return tracker.get_endpoint_report()


@performance_router.get("/routes")
async def routes_report():
    return tracker.get_route_report()


@performance_router.get("/database")
async def database_report():
    from main import get_database_connection, db_pool
    return measure_database(get_database_connection, pooling_active=db_pool is not None)


@performance_router.post("/client-metric")
async def record_client_metric(payload: ClientMetricPayload):
    """Frontend calls this after each route load to report real, in-browser
    timing -- see frontend/src/components/perf/RoutePerfCollector.tsx."""
    tracker.record_route_metric(payload.route, payload.metric, payload.duration_ms)
    return {"recorded": True}
