"""The ONLY place a paid model call is made (a test fails if another module imports `anthropic`).

    Gateway.structured(...)  ->  kill switch  ->  budget reservation (worst case)  ->  one Messages call with a JSON-schema output
                             ->  settlement at the actual cost (usage x the served model's price; the ceiling price if a fallback ran)

No call is made unless RESEARCH_PAID_CALLS_ENABLED=1, and none after a refused reservation. The SDK is imported lazily, so everything else
(and every test) runs without it.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, Optional, Tuple

from research_missions import budget as B
from research_missions import config as C


class PaidCallsDisabled(Exception):
    """RESEARCH_PAID_CALLS_ENABLED is not 1: no client is built and nothing is reserved."""


class ModelRefused(Exception):
    """The model (and any fallback) declined; the cost of the attempt is settled."""


class ModelOutputError(Exception):
    """The response was cut off or not the requested JSON; the cost of the attempt is settled."""


def paid_calls_enabled(env: Optional[Dict[str, str]] = None) -> bool:
    return (os.environ if env is None else env).get("RESEARCH_PAID_CALLS_ENABLED", "0") == "1"


def _default_client():
    import anthropic  # lazy: only a real paid call needs the SDK
    return anthropic.Anthropic()


def _billed_nothing(exc: Exception) -> bool:
    """A request the API rejected outright (4xx other than 429) is not billed; anything else (timeouts, 5xx, 429 mid-way, unknown) is
    settled at the reservation, the conservative side."""
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and 400 <= status < 500 and status != 429


class Gateway:
    def __init__(self, ledger, limits: Optional[B.Limits] = None, client_factory: Callable[[], Any] = _default_client, enabled: Optional[bool] = None) -> None:
        self.ledger = ledger
        self.limits = limits or B.limits_from_env()
        self._factory = client_factory
        self._client = None
        self.enabled = paid_calls_enabled() if enabled is None else enabled
        self.calls = 0
        self.cost_usd = 0.0

    def structured(self, *, mission_id: Optional[int], role: str, system: str, user: str, schema: Dict[str, Any],
                   mission_budget: Optional[float] = None) -> Tuple[Dict[str, Any], float]:
        """(parsed JSON, actual cost USD). Raises PaidCallsDisabled / budget.BudgetExceeded before any request; ModelRefused /
        ModelOutputError after a settled one."""
        if not self.enabled:
            raise PaidCallsDisabled("paid model calls are disabled (set RESEARCH_PAID_CALLS_ENABLED=1 to allow them)")
        model, max_tokens = C.MODELS[role], C.MAX_TOKENS[role]
        amount = B.worst_case_usd(model, len(system) + len(user) + len(json.dumps(schema)), max_tokens)
        row_id = self.ledger.reserve(mission_id, role, model, amount, self.limits, mission_budget)      # raises BudgetExceeded: nothing sent
        kwargs: Dict[str, Any] = dict(model=model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}],
                                      output_config={"effort": C.EFFORT[role], "format": {"type": "json_schema", "schema": schema}})
        if C.USE_FALLBACKS:
            kwargs.update(betas=[C.FALLBACK_BETA], fallbacks="default")
        try:
            if self._client is None:
                self._client = self._factory()
            resp = self._client.beta.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - every failure is settled before it propagates
            self.ledger.fail(row_id, 0.0 if _billed_nothing(exc) else amount, f"{type(exc).__name__}: {exc}"[:500])
            raise
        usage = resp.usage
        cost = B.usage_cost_usd(getattr(resp, "model", None), usage)
        tokens = {"input_tokens": int(getattr(usage, "input_tokens", 0) or 0), "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                  "cache_read_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0), "cache_write_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0)}
        self.ledger.settle(row_id, cost, tokens, getattr(resp, "model", None))
        self.calls += 1
        self.cost_usd += cost
        if resp.stop_reason == "refusal":
            raise ModelRefused(f"{role}: the model declined ({getattr(getattr(resp, 'stop_details', None), 'category', None)})")
        if resp.stop_reason == "max_tokens":
            raise ModelOutputError(f"{role}: output cut off at max_tokens={max_tokens}")
        text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), None)
        try:
            return json.loads(text), cost
        except (TypeError, ValueError) as exc:
            raise ModelOutputError(f"{role}: response is not the requested JSON") from exc
