# File: backend/routers/public_signals.py
"""
Public Fundamental Updates API - UNAUTHENTICATED and READ ONLY (owner decision 2026-11-07, option A).

    GET /api/public/signals?limit=20&cursor=...&type=margin-expansion&type=...&sector=Technology
    GET /api/public/signals/{symbol}/{quarter_end}/{slug}

Safety, in order of the request:
  * RATE LIMIT per client IP (token bucket, in memory, per worker): 60/min on the feed, 120/min on detail pages; 429 with Retry-After. The client IP
    is the socket peer unless the peer is a trusted proxy (PUBLIC_API_TRUSTED_PROXIES, default loopback + the docker app network), in which case the
    LAST X-Forwarded-For entry (the one Caddy appends) is used, so a client cannot choose its own bucket by sending the header itself.
  * VALIDATION before any database work: closed lists for type/sector, strict symbol/date/slug patterns, a bounded limit, an opaque cursor. Errors
    are generic and never echo the input.
  * ALLOWLIST: only the publication record is read and only allowlisted fields are returned (services/public_signals_service.py).
  * CACHING: Cache-Control public, max-age=300; detail pages carry an ETag (version + content hash) and answer If-None-Match with 304.
This router imports nothing from auth and is mounted without require_authenticated_user; nothing here writes.
"""
import ipaddress
import logging
import math
import os
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from services.public_signals_service import DEFAULT_LIMIT, MAX_LIMIT, BadRequest, DetailResponse, FeedResponse, PublicSignalsService

logger = logging.getLogger(__name__)

public_signals_router = APIRouter(prefix="/api/public/signals", tags=["public-signals"])

CACHE_CONTROL = "public, max-age=300"
FEED_PER_MIN, DETAIL_PER_MIN = 60, 120


class RateLimiter:
    """Token bucket per key: `rate` tokens per minute, burst = rate. Thread-safe; old buckets are pruned so memory stays bounded."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic, max_keys: int = 50_000) -> None:
        self.capacity, self.refill = float(per_minute), per_minute / 60.0
        self._clock, self._max_keys = clock, max_keys
        self._buckets: Dict[str, Tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> Tuple[bool, int]:
        """(allowed, seconds to wait when refused)."""
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                allowed, wait = True, 0
            else:
                self._buckets[key] = (tokens, now)
                allowed, wait = False, max(1, math.ceil((1 - tokens) / self.refill))
            if len(self._buckets) > self._max_keys:
                full = [k for k, (t, ts) in self._buckets.items() if t + (now - ts) * self.refill >= self.capacity]
                for k in full:
                    del self._buckets[k]
            return allowed, wait


def _trusted_networks() -> List[ipaddress._BaseNetwork]:
    raw = os.getenv("PUBLIC_API_TRUSTED_PROXIES", "127.0.0.1/32,::1/128,172.16.0.0/12")
    nets = []
    for part in raw.split(","):
        try:
            nets.append(ipaddress.ip_network(part.strip(), strict=False))
        except ValueError:
            logger.warning("PUBLIC_API_TRUSTED_PROXIES: ignoring an invalid entry")
    return nets


_TRUSTED = _trusted_networks()


def client_ip(request: Request, trusted=None) -> str:
    peer = request.client.host if request.client else "unknown"
    nets = _TRUSTED if trusted is None else trusted
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if any(peer_ip in n for n in nets):
        fwd = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if fwd:
            try:
                return str(ipaddress.ip_address(fwd[-1]))
            except ValueError:
                return peer
    return peer


_feed_limiter, _detail_limiter = RateLimiter(FEED_PER_MIN), RateLimiter(DETAIL_PER_MIN)


def _limit(limiter: RateLimiter, request: Request) -> None:
    ok, wait = limiter.take(client_ip(request))
    if not ok:
        raise HTTPException(status_code=429, detail="Too many requests", headers={"Retry-After": str(wait)})


def feed_rate_limit(request: Request) -> None:
    _limit(_feed_limiter, request)


def detail_rate_limit(request: Request) -> None:
    _limit(_detail_limiter, request)


def get_public_signals_service() -> PublicSignalsService:
    from main import get_database_connection
    return PublicSignalsService(get_database_connection)


def _unavailable(e: Exception) -> HTTPException:
    if "does not exist" in str(e) and "fundamental_update" in str(e):
        return HTTPException(status_code=503, detail="Fundamental Updates are not available yet")
    logger.error("public signals query failed: %s", type(e).__name__)
    return HTTPException(status_code=503, detail="Temporarily unavailable")


@public_signals_router.get("", response_model=FeedResponse, dependencies=[Depends(feed_rate_limit)])
def feed(response: Response,
         limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
         cursor: Optional[str] = Query(None, max_length=200),
         type: List[str] = Query([]),  # noqa: A002 - the public query parameter name (each value is checked against the closed list)
         sector: Optional[str] = Query(None, max_length=40),
         service: PublicSignalsService = Depends(get_public_signals_service)):
    if len(type) > 5:
        raise HTTPException(status_code=422, detail="Invalid request")
    try:
        out = service.feed(limit=limit, cursor=cursor, types=type, sector=sector)
    except BadRequest as e:
        raise HTTPException(status_code=422, detail=f"Invalid request: {e}") from None
    except Exception as e:  # noqa: BLE001
        raise _unavailable(e) from None
    response.headers["Cache-Control"] = CACHE_CONTROL
    return out


@public_signals_router.get("/{symbol}/{quarter_end}/{slug}", response_model=DetailResponse, dependencies=[Depends(detail_rate_limit)])
def detail(symbol: str, quarter_end: str, slug: str, request: Request, response: Response,
           service: PublicSignalsService = Depends(get_public_signals_service)):
    try:
        out = service.detail(symbol, quarter_end, slug)
    except BadRequest as e:
        raise HTTPException(status_code=422, detail=f"Invalid request: {e}") from None
    except Exception as e:  # noqa: BLE001
        raise _unavailable(e) from None
    if out is None:
        raise HTTPException(status_code=404, detail="Not found")
    etag = f'"{out.version}-{out.update.content_hash[:16]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": CACHE_CONTROL})
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = CACHE_CONTROL
    return out
