# mechanism/alerts/message_ledger.py
"""
The ledger of what First Light posted to a CHANNEL (dev / prod), for the Telegram Control Center (TELEGRAM_CONTROL_MILESTONES.md).

A bot cannot read a channel's history, so this table is the only way to list, edit or delete what was posted. `TelegramClient` (the one place
every send goes through) calls a `LedgerRecorder` after each successful send / edit / delete / pin; the control page reads it through `PgLedger`.

Rules
  * Channels only: `TelegramClient.from_env` attaches a recorder for the 'dev' and 'prod' targets, never for 'owner' (the private chat) and never
    in dry-run. Nothing of the assistant is recorded here.
  * Recording can never break or repeat a send: the recorder swallows every error (it logs the error TYPE only - no text, no token), because a
    raise after a message was delivered would make a script abort and be re-run, posting the same message twice.
  * `MemoryLedger` has the same interface as `PgLedger` (the same contract tests run against both) and exists so the rules in channel_control.py
    can be tested without a database.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

TARGETS = ("dev", "prod")
STATUSES = ("sent", "deleted", "edited")          # 'edited' = still up and edited at least once (derived, not stored)
MAX_PAGE = 200


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clamp_page(limit: int, offset: int) -> Tuple[int, int]:
    return max(1, min(int(limit), MAX_PAGE)), max(0, int(offset))


# ================================================================== Postgres
class PgLedger:
    """Postgres implementation (tables: mechanism/add_telegram_control_tables.sql). `db` = an object with execute_dict_query / execute_insert
    (shared.database.db, or the backend's pooled adapter)."""

    def __init__(self, db):
        self.db = db

    # ---- writes
    def record_sent(self, *, target: str, chat_id: str, message_id: int, kind: str, content_type: str, text: str,
                    reply_markup: Optional[dict], silent: bool, disable_preview: bool, sent_at: Optional[datetime] = None) -> None:
        self.db.execute_insert(
            "INSERT INTO telegram_messages (target, chat_id, message_id, kind, content_type, text, original_text, reply_markup, silent, "
            "disable_preview, sent_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s) ON CONFLICT (chat_id, message_id) DO NOTHING",
            (target, str(chat_id), int(message_id), kind, content_type, text, text, json.dumps(reply_markup) if reply_markup else None,
             bool(silent), bool(disable_preview), sent_at or _utcnow()))

    def record_edited(self, *, chat_id: str, message_id: int, text: str) -> None:
        self.db.execute_insert(
            "UPDATE telegram_messages SET text = %s, edit_count = edit_count + 1, edited_at = %s WHERE chat_id = %s AND message_id = %s",
            (text, _utcnow(), str(chat_id), int(message_id)))

    def record_deleted(self, *, chat_id: str, message_id: int) -> None:
        self.db.execute_insert(
            "UPDATE telegram_messages SET status = 'deleted', deleted_at = COALESCE(deleted_at, %s), pinned = FALSE "
            "WHERE chat_id = %s AND message_id = %s", (_utcnow(), str(chat_id), int(message_id)))

    def record_pinned(self, *, chat_id: str, message_id: int) -> None:
        self.db.execute_insert("UPDATE telegram_messages SET pinned = TRUE WHERE chat_id = %s AND message_id = %s AND status = 'sent'",
                               (str(chat_id), int(message_id)))

    def record_unpinned(self, *, chat_id: str, message_id: int) -> None:
        self.db.execute_insert("UPDATE telegram_messages SET pinned = FALSE WHERE chat_id = %s AND message_id = %s",
                               (str(chat_id), int(message_id)))

    def audit_add(self, *, action: str, target: str, chat_id: str, message_id: int, outcome: str, detail: Optional[str] = None) -> None:
        self.db.execute_insert(
            "INSERT INTO telegram_control_audit (action, target, chat_id, message_id, outcome, detail) VALUES (%s, %s, %s, %s, %s, %s)",
            (action, target, str(chat_id), int(message_id), outcome, (detail or "")[:200] or None))

    # ---- reads
    def get(self, ledger_id: int) -> Optional[Dict[str, Any]]:
        rows = self.db.execute_dict_query("SELECT * FROM telegram_messages WHERE id = %s", (int(ledger_id),))
        return rows[0] if rows else None

    def get_by_message(self, chat_id: str, message_id: int) -> Optional[Dict[str, Any]]:
        """The row a just-completed send/pin/edit produced, by the chat+message id Telegram gave back (its own id, not the ledger's)."""
        rows = self.db.execute_dict_query("SELECT * FROM telegram_messages WHERE chat_id = %s AND message_id = %s", (str(chat_id), int(message_id)))
        return rows[0] if rows else None

    @staticmethod
    def _where(target, kind, status, since, until, q) -> Tuple[str, tuple]:
        clauses, params = [], []
        if target:
            clauses.append("target = %s"); params.append(target)
        if kind:
            clauses.append("kind = %s"); params.append(kind)
        if status == "edited":
            clauses.append("status = 'sent' AND edit_count > 0")
        elif status:
            clauses.append("status = %s"); params.append(status)
        if since:
            clauses.append("sent_at >= %s"); params.append(since)
        if until:
            clauses.append("sent_at < %s"); params.append(until)
        if q:
            clauses.append("text ILIKE %s"); params.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", tuple(params)

    def list(self, *, target=None, kind=None, status=None, since=None, until=None, q=None, limit=50, offset=0) -> Tuple[List[Dict], int]:
        limit, offset = _clamp_page(limit, offset)
        where, params = self._where(target, kind, status, since, until, q)
        total = self.db.execute_dict_query(f"SELECT COUNT(*) AS n FROM telegram_messages{where}", params)[0]["n"]
        rows = self.db.execute_dict_query(
            f"SELECT * FROM telegram_messages{where} ORDER BY sent_at DESC, id DESC LIMIT %s OFFSET %s", params + (limit, offset))
        return rows, int(total)

    def counts(self, target: str, since: datetime) -> Dict[str, Any]:
        row = self.db.execute_dict_query(
            "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE sent_at >= %s) AS since_count, "
            "COUNT(*) FILTER (WHERE status = 'sent') AS live, COUNT(*) FILTER (WHERE status = 'sent' AND edit_count > 0) AS edited, "
            "COUNT(*) FILTER (WHERE status = 'deleted') AS deleted, MAX(sent_at) AS last_sent, MIN(sent_at) AS first_sent "
            "FROM telegram_messages WHERE target = %s", (since, target))[0]
        return {"total": int(row["total"]), "since_count": int(row["since_count"]), "live": int(row["live"]), "edited": int(row["edited"]),
                "deleted": int(row["deleted"]), "last_sent": row["last_sent"], "first_sent": row["first_sent"]}

    def kinds(self, target: Optional[str] = None) -> List[Dict[str, Any]]:
        where, params = self._where(target, None, None, None, None, None)
        rows = self.db.execute_dict_query(f"SELECT kind, COUNT(*) AS n FROM telegram_messages{where} GROUP BY kind ORDER BY n DESC, kind", params)
        return [{"kind": r["kind"], "count": int(r["n"])} for r in rows]

    def audit_for(self, chat_id: str, message_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        return self.db.execute_dict_query(
            "SELECT at, action, outcome, detail FROM telegram_control_audit WHERE chat_id = %s AND message_id = %s ORDER BY at DESC, id DESC LIMIT %s",
            (str(chat_id), int(message_id), int(limit)))


# ================================================================== memory (tests)
class MemoryLedger:
    """Same interface and semantics as PgLedger, in memory."""

    def __init__(self, clock: Callable[[], datetime] = _utcnow):
        self.rows: List[Dict[str, Any]] = []
        self.audit: List[Dict[str, Any]] = []
        self._clock = clock
        self._next = 1

    def _find(self, chat_id, message_id) -> Optional[Dict[str, Any]]:
        return next((r for r in self.rows if r["chat_id"] == str(chat_id) and r["message_id"] == int(message_id)), None)

    def record_sent(self, *, target, chat_id, message_id, kind, content_type, text, reply_markup, silent, disable_preview, sent_at=None) -> None:
        if self._find(chat_id, message_id):
            return
        self.rows.append({
            "id": self._next, "target": target, "chat_id": str(chat_id), "message_id": int(message_id), "kind": kind, "content_type": content_type,
            "text": text, "original_text": text, "reply_markup": json.loads(json.dumps(reply_markup)) if reply_markup else None,
            "silent": bool(silent), "disable_preview": bool(disable_preview), "pinned": False, "status": "sent", "edit_count": 0,
            "sent_at": sent_at or self._clock(), "edited_at": None, "deleted_at": None})
        self._next += 1

    def record_edited(self, *, chat_id, message_id, text) -> None:
        r = self._find(chat_id, message_id)
        if r:
            r.update(text=text, edit_count=r["edit_count"] + 1, edited_at=self._clock())

    def record_deleted(self, *, chat_id, message_id) -> None:
        r = self._find(chat_id, message_id)
        if r:
            r.update(status="deleted", deleted_at=r["deleted_at"] or self._clock(), pinned=False)

    def record_pinned(self, *, chat_id, message_id) -> None:
        r = self._find(chat_id, message_id)
        if r and r["status"] == "sent":
            r["pinned"] = True

    def record_unpinned(self, *, chat_id, message_id) -> None:
        r = self._find(chat_id, message_id)
        if r:
            r["pinned"] = False

    def audit_add(self, *, action, target, chat_id, message_id, outcome, detail=None) -> None:
        self.audit.append({"at": self._clock(), "action": action, "target": target, "chat_id": str(chat_id), "message_id": int(message_id),
                           "outcome": outcome, "detail": (detail or "")[:200] or None})

    def get(self, ledger_id):
        r = next((r for r in self.rows if r["id"] == int(ledger_id)), None)
        return dict(r) if r else None

    def get_by_message(self, chat_id, message_id):
        r = self._find(chat_id, message_id)
        return dict(r) if r else None

    def _match(self, r, target, kind, status, since, until, q) -> bool:
        if target and r["target"] != target:
            return False
        if kind and r["kind"] != kind:
            return False
        if status == "edited" and not (r["status"] == "sent" and r["edit_count"] > 0):
            return False
        if status and status != "edited" and r["status"] != status:
            return False
        if since and r["sent_at"] < since:
            return False
        if until and r["sent_at"] >= until:
            return False
        return not q or q.lower() in r["text"].lower()

    def list(self, *, target=None, kind=None, status=None, since=None, until=None, q=None, limit=50, offset=0):
        limit, offset = _clamp_page(limit, offset)
        hits = sorted((r for r in self.rows if self._match(r, target, kind, status, since, until, q)), key=lambda r: (r["sent_at"], r["id"]), reverse=True)
        return [dict(r) for r in hits[offset:offset + limit]], len(hits)

    def counts(self, target, since):
        mine = [r for r in self.rows if r["target"] == target]
        return {"total": len(mine), "since_count": sum(r["sent_at"] >= since for r in mine), "live": sum(r["status"] == "sent" for r in mine),
                "edited": sum(r["status"] == "sent" and r["edit_count"] > 0 for r in mine), "deleted": sum(r["status"] == "deleted" for r in mine),
                "last_sent": max((r["sent_at"] for r in mine), default=None), "first_sent": min((r["sent_at"] for r in mine), default=None)}

    def kinds(self, target=None):
        n: Dict[str, int] = {}
        for r in self.rows:
            if not target or r["target"] == target:
                n[r["kind"]] = n.get(r["kind"], 0) + 1
        return [{"kind": k, "count": c} for k, c in sorted(n.items(), key=lambda kv: (-kv[1], kv[0]))]

    def audit_for(self, chat_id, message_id, limit=20):
        hits = [a for a in self.audit if a["chat_id"] == str(chat_id) and a["message_id"] == int(message_id)]
        return [{k: a[k] for k in ("at", "action", "outcome", "detail")} for a in reversed(hits)][:limit]


# ================================================================== the hook TelegramClient calls
class LedgerRecorder:
    """What a channel client reports to. Lazy (nothing is imported or connected until the first event, so building a client stays free of any
    database) and NEVER raises: a ledger problem must not break a send or make a script retry one that was already delivered."""

    def __init__(self, target: str, ledger_factory: Optional[Callable[[], Any]] = None):
        if target not in TARGETS:
            raise ValueError(f"the ledger records channel targets only, not {target!r}")
        self.target = target
        self._factory = ledger_factory or _default_ledger
        self._ledger = None

    def _run(self, event: str, fn: str, **kw) -> None:
        try:
            if self._ledger is None:
                self._ledger = self._factory()
            getattr(self._ledger, fn)(**kw)
        except Exception as e:                                       # noqa: BLE001 - see the class docstring
            log.warning("message ledger: could not record %s (%s)", event, type(e).__name__)   # the error TYPE only: never text, never a token

    def sent(self, *, chat_id, message_id, kind, content_type, text, reply_markup=None, silent=False, disable_preview=True) -> None:
        self._run("send", "record_sent", target=self.target, chat_id=chat_id, message_id=message_id, kind=kind or "other",
                  content_type=content_type, text=text, reply_markup=reply_markup, silent=silent, disable_preview=disable_preview)

    def edited(self, *, chat_id, message_id, text) -> None:
        self._run("edit", "record_edited", chat_id=chat_id, message_id=message_id, text=text)

    def deleted(self, *, chat_id, message_id) -> None:
        self._run("delete", "record_deleted", chat_id=chat_id, message_id=message_id)

    def pinned(self, *, chat_id, message_id) -> None:
        self._run("pin", "record_pinned", chat_id=chat_id, message_id=message_id)

    def unpinned(self, *, chat_id, message_id) -> None:
        self._run("unpin", "record_unpinned", chat_id=chat_id, message_id=message_id)


def _default_ledger() -> PgLedger:
    from shared.database import db                                    # imported here: shared.database opens its pool on import
    return PgLedger(db)
