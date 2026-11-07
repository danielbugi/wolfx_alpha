#!/usr/bin/env python3
"""Fundamental Updates publication record (migration 41, fundamental_update_publication): the ONLY source the public /signals API reads.

    publish(update, event_id, method_version)    -> 'published' | 'exists'
    revise(update_key, current, event_id, mv)    -> 'unchanged' | 'restated' | 'redated' | 'withdrawn' | 'already_withdrawn' | 'not_published' | 'lost_race'

Rules (tested in tests/test_update_ledger.py against a real PostgreSQL):
  * IDEMPOTENT: publishing an update that already has a version 1 changes nothing ('exists'). A correction is written as version n+1 only when the
    facts as displayed changed (update_model.classify_revision); UNIQUE (update_key, version) means two racing writers cannot both add version n+1
    (the loser gets 'lost_race' and writes nothing).
  * APPEND-ONLY: nothing is updated or deleted (the table's trigger refuses it); history is the sequence of versions.
  * FROZEN: public_json is stored exactly as published (news and links included) and never recomputed. A withdrawal keeps the last published facts.
  * NOTHING SENDS: this module only records. Telegram delivery (and its own claim) belongs to a later, separately approved phase.
No scheduled job calls this module.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from inflection.update_model import PublicUpdate, classify_revision

_INSERT = """INSERT INTO fundamental_update_publication (update_key, version, kind, changed, public_json, content_hash, render_version, symbol, quarter_end, event_type,
                 sector, public_date, event_id, method_version)
             VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s)
             ON CONFLICT (update_key, version) DO NOTHING RETURNING id"""


class Ledger:
    def __init__(self, connect) -> None:
        """connect(): a context manager yielding a psycopg2 connection, e.g. shared.db.get_sync_connection (the pool owns the connection)."""
        self._connect = connect

    def _insert(self, d: Dict[str, Any], version: int, kind: str, changed, event_id: Optional[int], method_version: str) -> bool:
        with self._connect() as conn:
            try:
                return self._insert_on(conn, d, version, kind, changed, event_id, method_version)
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _insert_on(conn, d, version, kind, changed, event_id, method_version) -> bool:
        cur = conn.cursor()
        cur.execute(_INSERT, (d["key"], version, kind, json.dumps(list(changed)), json.dumps(d, sort_keys=True), d["content_hash"], d["render_version"], d["symbol"],
                              d["quarter_end"], d["event_type"], d.get("sector"), d["public_date"], event_id, method_version))
        won = cur.fetchone() is not None
        conn.commit()
        return won

    def latest(self, update_key: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT version, kind, public_json FROM fundamental_update_publication WHERE update_key = %s ORDER BY version DESC LIMIT 1", (update_key,))
            row = cur.fetchone()
            conn.rollback()
        if row is None:
            return None
        pj = row[2] if isinstance(row[2], dict) else json.loads(row[2])
        return {"version": row[0], "kind": row[1], "public_json": pj}

    def publish(self, update: PublicUpdate, event_id: Optional[int], method_version: str) -> str:
        return "published" if self._insert(update.to_public_dict(), 1, "initial", [], event_id, method_version) else "exists"

    def revise(self, update_key: str, current: Optional[PublicUpdate], event_id: Optional[int], method_version: str) -> str:
        """Compare the latest published version with `current` (build_update(..., check_freshness=False), or None when the event is gone / not
        publishable) and record a correction version when the displayed facts changed."""
        last = self.latest(update_key)
        if last is None:
            return "not_published"
        if last["kind"] == "withdrawn":
            return "already_withdrawn"                   # a withdrawn update is not revived automatically: republishing needs a deliberate decision
        kind, changed = classify_revision(last["public_json"], current)
        if kind == "unchanged":
            return "unchanged"
        if kind == "withdrawn":
            d = dict(last["public_json"])
        else:
            d = current.to_public_dict()
            d["news"] = last["public_json"].get("news", [])  # the news shown at first publication stays: it is never re-selected later
        won = self._insert(d, last["version"] + 1, kind, changed, event_id, method_version)
        return kind if won else "lost_race"
