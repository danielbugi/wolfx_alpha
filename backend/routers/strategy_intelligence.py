# File: backend/routers/strategy_intelligence.py
"""
Strategy Intelligence API - internal, authenticated. Per-strategy summary (tracking, performance,
outcome distribution, bullish vs bearish), data health, and the signal explorer, all computed by
mechanism/strategy_analytics so the dashboard and the Telegram assistant give the same answers.

`/api/strategies` (plural) is this; `/api/strategy` (routers/strategy.py) is the unrelated
entry/stop ranking of today's screener output. Data health and research metadata are internal: the
track record (routers/track_record.py) exposes only its own summary subset.
"""
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from auth.dependencies import require_authenticated_user
from services.strategy_intelligence_service import (
    ResearchUnavailable, StrategyIntelligenceService, StrategyNotFound,
)

strategy_intelligence_router = APIRouter(
    prefix="/api/strategies",
    tags=["strategy-intelligence"],
    dependencies=[Depends(require_authenticated_user)],
)

KEY = Path(..., pattern=r"^[a-z0-9_]{1,60}$")
VERSION = Path(..., pattern=r"^[A-Za-z0-9._-]{1,20}$")


def get_strategy_intelligence_service() -> StrategyIntelligenceService:
    from main import get_database_connection
    return StrategyIntelligenceService(get_database_connection)


def _not_found(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": code, "message": message})


@strategy_intelligence_router.get("")
def list_strategies(service: StrategyIntelligenceService = Depends(get_strategy_intelligence_service)):
    return service.list_strategies()


@strategy_intelligence_router.get("/definitions")
def definitions(service: StrategyIntelligenceService = Depends(get_strategy_intelligence_service)):
    return service.definitions()


@strategy_intelligence_router.get("/{key}/{version}/summary")
def summary(key: str = KEY, version: str = VERSION,
            service: StrategyIntelligenceService = Depends(get_strategy_intelligence_service)):
    try:
        return service.summary(key, version)
    except StrategyNotFound:
        raise _not_found("strategy_not_found", f"No strategy {key}/{version}.")


@strategy_intelligence_router.get("/{key}/{version}/data-health")
def data_health(key: str = KEY, version: str = VERSION,
                service: StrategyIntelligenceService = Depends(get_strategy_intelligence_service)):
    try:
        return service.data_health(key, version)
    except StrategyNotFound:
        raise _not_found("strategy_not_found", f"No strategy {key}/{version}.")


@strategy_intelligence_router.get("/{key}/{version}/signals")
def signals(
        key: str = KEY,
        version: str = VERSION,
        symbol: Optional[str] = Query(None, pattern=r"^[A-Za-z0-9.\-]{1,10}$"),
        direction: Optional[Literal["bullish", "bearish"]] = None,
        status: Optional[Literal["open", "stopped", "target1", "target2", "target3", "expired"]] = None,
        lifecycle: Optional[Literal["open", "held", "resolved"]] = None,
        evaluation_flag: Optional[Literal["split_suspect"]] = None,
        resolution_flag: Optional[Literal["same_bar_stop_and_target"]] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        model_version: Optional[str] = Query(None, max_length=80),
        sector: Optional[str] = Query(None, max_length=60),
        quality_grade: Optional[str] = Query(None, pattern=r"^[A-Fa-f][+-]?$"),
        sort: Literal["newest", "oldest", "symbol", "r_desc", "r_asc", "holding_desc", "holding_asc",
                      "grade"] = "newest",
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        service: StrategyIntelligenceService = Depends(get_strategy_intelligence_service),
):
    try:
        return service.signals(key, version, symbol=symbol, direction=direction, status=status,
                               lifecycle_state=lifecycle, evaluation_flag=evaluation_flag,
                               resolution_flag=resolution_flag, date_from=date_from, date_to=date_to,
                               model_version=model_version, sector=sector, quality_grade=quality_grade,
                               sort=sort, limit=limit, offset=offset)
    except StrategyNotFound:
        raise _not_found("strategy_not_found", f"No strategy {key}/{version}.")
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": "invalid_query", "message": str(e)})


# --- Release B research layer. Lists/summary/capture-runs answer 200 with an explicit `availability` (a missing
# research schema is a state to render, not an error); the detail routes 404 with `research_not_available`. ---
def _unavailable(e: ResearchUnavailable) -> HTTPException:
    return _not_found("research_not_available", e.availability["reason"])


@strategy_intelligence_router.get("/{key}/{version}/research/summary")
def research_summary(key: str = KEY, version: str = VERSION,
                     service: StrategyIntelligenceService = Depends(get_strategy_intelligence_service)):
    try:
        return service.research_summary(key, version)
    except StrategyNotFound:
        raise _not_found("strategy_not_found", f"No strategy {key}/{version}.")


@strategy_intelligence_router.get("/{key}/{version}/research/capture-runs")
def research_capture_runs(key: str = KEY, version: str = VERSION, limit: int = Query(30, ge=1, le=120),
                          service: StrategyIntelligenceService = Depends(get_strategy_intelligence_service)):
    try:
        return service.research_capture_runs(key, version, limit)
    except StrategyNotFound:
        raise _not_found("strategy_not_found", f"No strategy {key}/{version}.")


@strategy_intelligence_router.get("/{key}/{version}/research/candidates")
def research_candidates(
        key: str = KEY,
        version: str = VERSION,
        session_date: Optional[date] = None,
        direction: Optional[Literal["bullish", "bearish"]] = None,
        guard: Optional[Literal["passed", "rejected", "not_evaluated"]] = None,
        selected: Optional[bool] = None,
        symbol: Optional[str] = Query(None, pattern=r"^[A-Za-z0-9.\-]{1,10}$"),
        candidate_class: Optional[str] = Query(None, pattern=r"^[a-z_]{1,40}$"),
        grade: Optional[str] = Query(None, pattern=r"^[A-Fa-f][+-]?$"),
        sort: Literal["rank", "combined_desc", "alignment_desc", "breakout_desc", "symbol", "grade"] = "rank",
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        service: StrategyIntelligenceService = Depends(get_strategy_intelligence_service),
):
    try:
        return service.research_candidates(key, version, session_date=session_date, direction=direction,
                                           guard=guard, selected=selected, symbol=symbol,
                                           candidate_class=candidate_class, grade=grade, sort=sort,
                                           limit=limit, offset=offset)
    except StrategyNotFound:
        raise _not_found("strategy_not_found", f"No strategy {key}/{version}.")
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": "invalid_query", "message": str(e)})


@strategy_intelligence_router.get("/{key}/{version}/research/candidates/{observation_id}")
def research_candidate(observation_id: int = Path(..., ge=1), key: str = KEY, version: str = VERSION,
                       service: StrategyIntelligenceService = Depends(get_strategy_intelligence_service)):
    try:
        detail = service.research_candidate(key, version, observation_id)
    except StrategyNotFound:
        raise _not_found("strategy_not_found", f"No strategy {key}/{version}.")
    except ResearchUnavailable as e:
        raise _unavailable(e)
    if detail is None:
        raise _not_found("candidate_not_found", f"No candidate {observation_id} for {key}/{version}.")
    return detail


@strategy_intelligence_router.get("/{key}/{version}/research/snapshots/{snapshot_id}")
def research_snapshot(snapshot_id: int = Path(..., ge=1), key: str = KEY, version: str = VERSION,
                      service: StrategyIntelligenceService = Depends(get_strategy_intelligence_service)):
    try:
        detail = service.research_snapshot(key, version, snapshot_id)
    except StrategyNotFound:
        raise _not_found("strategy_not_found", f"No strategy {key}/{version}.")
    except ResearchUnavailable as e:
        raise _unavailable(e)
    if detail is None:
        raise _not_found("snapshot_not_found", f"No snapshot {snapshot_id} for {key}/{version}.")
    return detail


@strategy_intelligence_router.get("/{key}/{version}/signals/{signal_id}")
def signal_detail(signal_id: int = Path(..., ge=1), key: str = KEY, version: str = VERSION,
                  service: StrategyIntelligenceService = Depends(get_strategy_intelligence_service)):
    try:
        detail = service.signal(key, version, signal_id)
    except StrategyNotFound:
        raise _not_found("strategy_not_found", f"No strategy {key}/{version}.")
    if detail is None:
        raise _not_found("signal_not_found", f"No signal {signal_id} for {key}/{version}.")
    return detail
