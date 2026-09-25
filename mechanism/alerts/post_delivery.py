# mechanism/alerts/post_delivery.py
"""
Per-post delivery tracking for the post-market package (mechanism/add_telegram_post_delivery_table.sql).

Replaces the old coarse "one session_state.json key per script invocation" dedup for the four post-market
kinds (daily_digest, momentum_board, top_gainers, market_health) with a row per (market_session, post_kind,
target) so a partial failure -- e.g. digest and board sent, top gainers failed -- can be retried without
re-sending what already went out, and two racing processes cannot both send the same post.

market_session is the explicit US trading-session date (the same `session` value send_daily_digest.py /
send_channel_posts.py already resolve), never inferred from the Israel-local wall clock at send time --
this module is written to be called after midnight Israel time for a session that closed the evening
before, and that must not create a second, wrong-dated row.

claim() is the concurrency guard: a single atomic SQL statement, not a SELECT-then-INSERT. See the
CLAIM_SQL docstring below for exactly what it does and does not allow to proceed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

RECLAIM_AFTER_MINUTES = 10   # a 'reserved' row older than this is treated as an abandoned/crashed claim

# Atomically claim (market_session, post_kind, target):
#   - no row yet                          -> inserted as 'reserved', claim succeeds
#   - existing row status = 'sent'        -> WHERE excludes it, no update, claim fails (already delivered)
#   - existing row status = 'reserved'
#       and claimed_at is recent          -> WHERE excludes it, claim fails (another worker is sending now)
#       and claimed_at is stale           -> reclaimed (its claimant likely crashed), claim succeeds
#   - existing row status = 'failed'      -> reclaimed, claim succeeds
# RETURNING id is empty exactly when the claim failed -- callers must treat "no row back" as "skip this kind",
# never as an error worth retrying immediately.
CLAIM_SQL = """
    INSERT INTO telegram_post_delivery (market_session, post_kind, target, status, claimed_at, attempts)
    VALUES (%(session)s, %(kind)s, %(target)s, 'reserved', NOW(), 1)
    ON CONFLICT (market_session, post_kind, target) DO UPDATE
        SET claimed_at = NOW(), status = 'reserved', attempts = telegram_post_delivery.attempts + 1
        WHERE telegram_post_delivery.status = 'failed'
           OR (telegram_post_delivery.status = 'reserved'
               AND telegram_post_delivery.claimed_at < NOW() - INTERVAL '{reclaim_minutes} minutes')
    RETURNING id
""".format(reclaim_minutes=RECLAIM_AFTER_MINUTES)


@dataclass
class Claim:
    id: int
    session: date
    kind: str
    target: str


def claim(db, session: date, kind: str, target: str) -> Optional[Claim]:
    """Try to claim (session, kind, target). Returns a Claim on success, None when it should be skipped
    (already sent, or another process holds a fresh reservation). A single round trip, commits itself."""
    with db.get_sync_connection() as conn:
        cur = conn.cursor()
        cur.execute(CLAIM_SQL, {"session": session, "kind": kind, "target": target})
        row = cur.fetchone()
        conn.commit()
        return Claim(id=row[0], session=session, kind=kind, target=target) if row else None


def mark_sent(db, claim_: Claim, message_id: Optional[int]) -> None:
    db.execute_insert(
        "UPDATE telegram_post_delivery SET status = 'sent', message_id = %s, sent_at = NOW() WHERE id = %s",
        (message_id, claim_.id))


def mark_failed(db, claim_: Claim, error: str) -> None:
    """Never leaves the row as 'sent'. A future claim() (this run's retry, or the next scheduled one) can
    reclaim it immediately -- 'failed' is always reclaimable, no staleness wait needed."""
    db.execute_insert(
        "UPDATE telegram_post_delivery SET status = 'failed', last_error = %s WHERE id = %s",
        (str(error)[:200], claim_.id))


def already_sent(db, session: date, kind: str, target: str) -> bool:
    """Read-only check, for callers that want to know without attempting a claim (e.g. deciding whether
    to even build a post's data)."""
    rows = db.execute_dict_query(
        "SELECT 1 FROM telegram_post_delivery WHERE market_session = %s AND post_kind = %s AND target = %s AND status = 'sent'",
        (session, kind, target))
    return bool(rows)
