# File: backend/services/telegram_control_service.py
"""
Telegram Control Center - the backend side (TELEGRAM_CONTROL_MILESTONES.md).

All the rules (production lock, wrong-chat guard, 48 h delete window, limits, wording guard, audit) live in mechanism/alerts/channel_control.py, next
to the senders that write the ledger, so there is ONE definition of what may be done to a channel post. This module only wires that logic to the
backend's pooled database connection. It imports the mechanism package by path (appended, never first: the backend's own top-level modules
`utils`, `services`, `routers` must keep winning) and does not import mechanism's `shared.database`, which would open a second pool.
"""
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from psycopg2.extras import RealDictCursor

_MECHANISM = str(Path(__file__).resolve().parents[2] / "mechanism")
if _MECHANISM not in sys.path:
    sys.path.append(_MECHANISM)

from alerts.channel_control import ChannelControl  # noqa: E402
from alerts.message_ledger import PgLedger  # noqa: E402


class PooledDb:
    """The two calls PgLedger needs (shared.database.db's interface), on top of the backend's connection pool. A pooled connection's close()
    rolls back and returns it to the pool, so every call takes one, uses it and closes it."""

    def __init__(self, get_connection: Callable):
        self._get = get_connection

    def execute_dict_query(self, query: str, params: Optional[Tuple] = None) -> List[Dict]:
        conn = self._get()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def execute_insert(self, query: str, params: Optional[Tuple] = None) -> bool:
        conn = self._get()
        try:
            with conn.cursor() as cur:
                cur.execute(query, params)
            conn.commit()
            return True
        finally:
            conn.close()

    @contextmanager
    def get_sync_connection(self):
        """The third shape of shared.database.db's interface (mechanism/alerts/bot_service.py's PgStore uses this for
        multi-statement transactions such as decide_request). The caller commits/rolls back explicitly; close() here
        just returns the connection to the backend's pool."""
        conn = self._get()
        try:
            yield conn
        finally:
            conn.close()


def build_control(get_connection: Callable) -> ChannelControl:
    return ChannelControl(PgLedger(PooledDb(get_connection)))
