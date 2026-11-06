"""Domain-aware source routing: which sources a mission may use. Deterministic; the planner may only choose queries and companies inside
what the router allows. A source is available when the domain lists it, it is not paid (paid sources need an owner decision and are
not configured), it is not switched off, and its credentials (if any) are present."""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from research_missions import config as C


def route(kind: str, domain: str, env: Optional[Dict[str, str]] = None) -> List[str]:
    e = os.environ if env is None else env
    if domain not in C.DOMAINS:
        raise ValueError(f"unknown domain {domain!r}; configured: {sorted(C.DOMAINS)}")
    out = []
    for s in C.DOMAINS[domain]["sources"]:
        meta = C.SOURCES[s]
        if meta["paid"]:
            continue
        if e.get(f"RESEARCH_SOURCE_{s.upper()}", "1") == "0":
            continue
        if s == "tiingo_news" and not e.get("TIINGO_API_KEY"):
            continue
        out.append(s)
    if kind == "thematic" and not C.DOMAINS[domain]["industries"]:
        raise ValueError(f"domain {domain!r} has no candidate universe; thematic missions need one")
    return out
