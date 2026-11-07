# File: backend/services/public_signals_service.py
"""
Public Fundamental Updates (/api/public/signals) - the data side. UNAUTHENTICATED callers reach this, so it is deliberately narrow:

  * It reads ONLY the publication record (migration 41: fundamental_update_publication and its fundamental_update_current view), never the event,
    feature or any other table. An event that was never published does not exist here.
  * It returns ONLY allowlisted fields. public_json was written as the allowlisted dict (mechanism/inflection/update_model.PUBLIC_FIELDS); the
    response models below are built field by field with extra="forbid", so an unexpected key fails the request (500) instead of leaking.
  * Internal provenance stored next to it (event_id, method_version) is never selected.
  * Keyset pagination: an opaque cursor over (first publication time, update key), newest first; constant cost at any depth.
"""
import base64
import binascii
import json
import logging
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple

from psycopg2.extras import RealDictCursor
from pydantic import BaseModel, ConfigDict

_MECHANISM = str(Path(__file__).resolve().parents[2] / "mechanism")
if _MECHANISM not in sys.path:
    sys.path.append(_MECHANISM)

from inflection.update_model import PUBLIC_FIELDS, PUBLIC_TYPES, SLUG_TO_TYPE, SYMBOL_RE  # noqa: E402

log = logging.getLogger(__name__)

MAX_LIMIT, DEFAULT_LIMIT = 50, 20
SECTORS = ("Basic Materials", "Communication Services", "Consumer Cyclical", "Consumer Defensive", "Energy", "Financial Services", "Healthcare", "Industrials",
           "Real Estate", "Technology", "Utilities")
QUARTER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SLUGS = tuple(slug for slug, _ in PUBLIC_TYPES.values())


# ---------------------------------------------------------------------------------------------------------------- response models (the allowlist)
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Figure(_Strict):
    label: str
    value: str
    compare: Optional[str] = None


class NewsItem(_Strict):
    published_at: str
    source: str
    headline: str
    url: str


class MarketCap(_Strict):
    value: str
    as_of: str


class Links(_Strict):
    finviz: str
    detail: str
    sec: Optional[str] = None
    sec_kind: Optional[Literal["filing", "company_filings"]] = None


class PublicUpdate(_Strict):
    key: str
    symbol: str
    company_name: Optional[str]
    event_type: str
    label: str
    quarter_end: str
    fiscal_label: str
    public_date: str
    date_source: str
    sector: Optional[str]
    industry: Optional[str]
    market_cap: Optional[MarketCap]
    figures: List[Figure]
    why: str
    links: Links
    news: List[NewsItem]
    content_hash: str
    render_version: str


class FeedItem(_Strict):
    update: PublicUpdate
    status: Literal["published", "corrected", "withdrawn"]
    first_published_at: str
    updated_at: str


class FeedResponse(_Strict):
    items: List[FeedItem]
    next_cursor: Optional[str]


class Correction(_Strict):
    version: int
    kind: Literal["restated", "redated", "withdrawn"]
    changed: List[str]
    at: str


class DetailResponse(_Strict):
    update: PublicUpdate
    status: Literal["published", "corrected", "withdrawn"]
    version: int
    first_published_at: str
    updated_at: str
    corrections: List[Correction]


assert tuple(PublicUpdate.model_fields) == PUBLIC_FIELDS, "the public response model must be exactly the allowlist"


# ---------------------------------------------------------------------------------------------------------------- validation + cursor
class BadRequest(ValueError):
    """A client input that is not acceptable. The message never echoes the input."""


def validate_key(symbol: str, quarter_end: str, slug: str) -> str:
    if not SYMBOL_RE.match(symbol or ""):
        raise BadRequest("invalid symbol")
    if not QUARTER_RE.match(quarter_end or ""):
        raise BadRequest("invalid quarter")
    try:
        q = date.fromisoformat(quarter_end)
    except ValueError:
        raise BadRequest("invalid quarter") from None
    if not date(2000, 1, 1) <= q <= datetime.now(timezone.utc).date():
        raise BadRequest("invalid quarter")
    if slug not in SLUG_TO_TYPE:
        raise BadRequest("invalid type")
    return f"{symbol}/{quarter_end}/{slug}"


def encode_cursor(first_published_at: datetime, update_key: str) -> str:
    raw = json.dumps([first_published_at.astimezone(timezone.utc).isoformat(), update_key], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> Tuple[datetime, str]:
    try:
        if len(cursor) > 200:
            raise ValueError
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        ts, key = json.loads(raw)
        when = datetime.fromisoformat(ts)
        if when.tzinfo is None or not isinstance(key, str) or len(key) > 80:
            raise ValueError
        return when, key
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError):
        raise BadRequest("invalid cursor") from None


def _iso(v: Any) -> str:
    return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(v, datetime) else str(v)


def _status(kind: str, version: int) -> str:
    return "withdrawn" if kind == "withdrawn" else "corrected" if version > 1 else "published"


def _public(pj: Any) -> PublicUpdate:
    d = pj if isinstance(pj, dict) else json.loads(pj)
    return PublicUpdate(**{k: d.get(k) for k in PUBLIC_FIELDS})      # missing keys fail; extra keys inside nested objects fail (extra="forbid")


# ---------------------------------------------------------------------------------------------------------------- service
class PublicSignalsService:
    def __init__(self, get_connection: Callable) -> None:
        self._get_connection = get_connection

    def _query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        if conn is None:
            raise RuntimeError("database unavailable")
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
            return rows
        finally:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def feed(self, limit: int = DEFAULT_LIMIT, cursor: Optional[str] = None, types: Sequence[str] = (), sector: Optional[str] = None) -> FeedResponse:
        if not 1 <= limit <= MAX_LIMIT:
            raise BadRequest("invalid limit")
        bad = [t for t in types if t not in SLUG_TO_TYPE]
        if bad:
            raise BadRequest("invalid type")
        if sector is not None and sector not in SECTORS:
            raise BadRequest("invalid sector")
        where, params = ["TRUE"], []
        if types:
            where.append("event_type = ANY(%s)")
            params.append([SLUG_TO_TYPE[t] for t in types])
        if sector:
            where.append("sector = %s")
            params.append(sector)
        if cursor:
            ts, key = decode_cursor(cursor)
            where.append("(first_published_at, update_key) < (%s, %s)")
            params += [ts, key]
        rows = self._query(f"""SELECT update_key, version, kind, public_json, first_published_at, updated_at FROM fundamental_update_current
                                WHERE {' AND '.join(where)} ORDER BY first_published_at DESC, update_key DESC LIMIT %s""", params + [limit + 1])
        more = len(rows) > limit
        rows = rows[:limit]
        items = [FeedItem(update=_public(r["public_json"]), status=_status(r["kind"], r["version"]), first_published_at=_iso(r["first_published_at"]),
                          updated_at=_iso(r["updated_at"])) for r in rows]
        nxt = encode_cursor(rows[-1]["first_published_at"], rows[-1]["update_key"]) if more and rows else None
        return FeedResponse(items=items, next_cursor=nxt)

    def detail(self, symbol: str, quarter_end: str, slug: str) -> Optional[DetailResponse]:
        key = validate_key(symbol, quarter_end, slug)
        rows = self._query("""SELECT version, kind, changed, public_json, published_at FROM fundamental_update_publication WHERE update_key = %s ORDER BY version""", (key,))
        if not rows:
            return None
        last = rows[-1]
        return DetailResponse(update=_public(last["public_json"]), status=_status(last["kind"], last["version"]), version=last["version"],
                              first_published_at=_iso(rows[0]["published_at"]), updated_at=_iso(last["published_at"]),
                              corrections=[Correction(version=r["version"], kind=r["kind"], changed=[str(c) for c in (r["changed"] or [])], at=_iso(r["published_at"]))
                                           for r in rows[1:]])
