# File: backend/services/strategy_performance_service.py
"""
Strategy performance - the backend side of the generic performance engine (mechanism/strategy_analytics).

Every number comes from strategy_analytics.analytics / .performance; this module only binds them to the
backend's connection pool and shapes nothing but the envelope. It is strategy-generic: the strategy is always
a (key, version) argument, never a code path. Read-only.
"""
from typing import Any, Dict

from services.strategy_intelligence_service import StrategyIntelligenceService, StrategyNotFound  # noqa: F401
from strategy_analytics import analytics, definitions, performance  # noqa: E402

DIMENSIONS = tuple(performance.DIMENSIONS)
UNAVAILABLE_DIMENSIONS = tuple(performance.UNAVAILABLE_DIMENSIONS)
ALL_DIMENSIONS = DIMENSIONS + UNAVAILABLE_DIMENSIONS


class UnknownDimension(ValueError):
    pass


class StrategyPerformanceService(StrategyIntelligenceService):
    """Reuses the parent's one-pooled-connection-per-request fetch and strategy lookup; adds only performance views."""

    def contract(self) -> Dict[str, Any]:
        """Static (no database): what may be requested and what is declared unavailable, and why."""
        return {**performance.contract(), "min_sample_size": definitions.MIN_SAMPLE_SIZE,
                "metric_shape": {"value": "number or null", "n": "resolved sample size",
                                 "state": list(("ok", "preliminary", "no_data", "not_available"))},
                "dimension_order": list(ALL_DIMENSIONS)}

    def overview(self, key: str, version: str) -> Dict[str, Any]:
        with self._fetch() as fetch:
            strategy = self._strategy(fetch, key, version)
            s = analytics.summary(fetch, strategy)
        return {
            "strategy": s["strategy"], "definitions_version": s["definitions_version"],
            "reference_session": s["reference_session"], "min_sample_size": definitions.MIN_SAMPLE_SIZE,
            "tracking": s["tracking"], "performance": s["performance"], "directions": s["directions"],
            "exit_rules": performance.exit_rules(strategy),
            "unavailable_metrics": performance.contract()["unavailable_metrics"],
            "unavailable_dimensions": performance.contract()["unavailable_dimensions"],
        }

    def outcomes(self, key: str, version: str) -> Dict[str, Any]:
        with self._fetch() as fetch:
            strategy = self._strategy(fetch, key, version)
            s = analytics.summary(fetch, strategy)
        return {"strategy": s["strategy"], "definitions_version": s["definitions_version"],
                "reference_session": s["reference_session"], "min_sample_size": definitions.MIN_SAMPLE_SIZE,
                "outcomes": s["outcomes"], "exit_rules": performance.exit_rules(strategy)}

    def breakdown(self, key: str, version: str, dimension: str) -> Dict[str, Any]:
        if dimension not in ALL_DIMENSIONS:
            raise UnknownDimension(f"unknown dimension {dimension!r}; one of {', '.join(ALL_DIMENSIONS)}")
        with self._fetch() as fetch:
            strategy = self._strategy(fetch, key, version)
            out = performance.breakdown(fetch, strategy, dimension)
        return {**out, "min_sample_size": definitions.MIN_SAMPLE_SIZE}

    def breakdowns(self, key: str, version: str) -> Dict[str, Any]:
        """Every dimension, available or not, over ONE connection: an unavailable one is listed with what it requires, never omitted."""
        with self._fetch() as fetch:
            strategy = self._strategy(fetch, key, version)
            out = {d: performance.breakdown(fetch, strategy, d) for d in ALL_DIMENSIONS}
        return {"strategy": strategy, "definitions_version": definitions.DEFINITIONS_VERSION,
                "min_sample_size": definitions.MIN_SAMPLE_SIZE, "dimensions": out}
