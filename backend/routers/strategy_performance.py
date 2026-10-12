# File: backend/routers/strategy_performance.py
"""
Generic strategy performance API - internal, authenticated, read-only. Strategy and version are path parameters;
there is no endpoint for any one strategy. All metrics come from mechanism/strategy_analytics (the canonical layer);
this router never recomputes. Metrics are {value, n, state}; an unavailable metric or dimension is
state "not_available" with what it requires, never zero. `/api/strategies` (strategy_intelligence) is unchanged.

(`/api/performance` is the unrelated API-latency tracker.)
"""
from fastapi import APIRouter, Depends, HTTPException, Path

from auth.dependencies import require_authenticated_user
from services.strategy_performance_service import (
    ALL_DIMENSIONS, StrategyNotFound, StrategyPerformanceService, UnknownDimension,
)

strategy_performance_router = APIRouter(
    prefix="/api/strategies",
    tags=["strategy-performance"],
    dependencies=[Depends(require_authenticated_user)],
)

KEY = Path(..., pattern=r"^[a-z0-9_]{1,60}$")
VERSION = Path(..., pattern=r"^[A-Za-z0-9._-]{1,20}$")
DIMENSION = Path(..., pattern=r"^[a-z_]{1,40}$")


def get_strategy_performance_service() -> StrategyPerformanceService:
    from main import get_database_connection
    return StrategyPerformanceService(get_database_connection)


def _not_found(key: str, version: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": "strategy_not_found", "message": f"No strategy {key}/{version}."})


@strategy_performance_router.get("/performance/contract")
def contract(service: StrategyPerformanceService = Depends(get_strategy_performance_service)):
    return service.contract()


@strategy_performance_router.get("/{key}/{version}/performance")
def overview(key: str = KEY, version: str = VERSION,
             service: StrategyPerformanceService = Depends(get_strategy_performance_service)):
    try:
        return service.overview(key, version)
    except StrategyNotFound:
        raise _not_found(key, version)


@strategy_performance_router.get("/{key}/{version}/performance/outcomes")
def outcomes(key: str = KEY, version: str = VERSION,
             service: StrategyPerformanceService = Depends(get_strategy_performance_service)):
    try:
        return service.outcomes(key, version)
    except StrategyNotFound:
        raise _not_found(key, version)


@strategy_performance_router.get("/{key}/{version}/performance/breakdowns")
def breakdowns(key: str = KEY, version: str = VERSION,
               service: StrategyPerformanceService = Depends(get_strategy_performance_service)):
    try:
        return service.breakdowns(key, version)
    except StrategyNotFound:
        raise _not_found(key, version)


@strategy_performance_router.get("/{key}/{version}/performance/breakdowns/{dimension}")
def breakdown(key: str = KEY, version: str = VERSION, dimension: str = DIMENSION,
              service: StrategyPerformanceService = Depends(get_strategy_performance_service)):
    try:
        return service.breakdown(key, version, dimension)
    except StrategyNotFound:
        raise _not_found(key, version)
    except UnknownDimension as e:
        raise HTTPException(status_code=422, detail={"code": "unknown_dimension", "message": str(e),
                                                      "dimensions": list(ALL_DIMENSIONS)})
