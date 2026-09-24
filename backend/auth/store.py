# File: backend/auth/store.py
"""
Database access for dashboard_users / dashboard_login_challenges / dashboard_sessions / dashboard_auth_audit
(mechanism/add_dashboard_auth_tables.sql). Takes `get_connection: Callable` exactly like
backend/services/telegram_control_service.py's PooledDb, so this reuses the backend's own pooled connection
(_PooledConnection in main.py) instead of opening a second pool via mechanism.shared.database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from psycopg2.extras import RealDictCursor


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuthStore:
    def __init__(self, get_connection: Callable):
        self._get = get_connection

    # --- users --------------------------------------------------------
    def get_user_by_email(self, email: str) -> Optional[Dict]:
        conn = self._get()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM dashboard_users WHERE email = %s", (email.lower().strip(),))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        conn = self._get()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM dashboard_users WHERE id = %s", (user_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    def list_users(self) -> List[Dict]:
        conn = self._get()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT id, email, role, is_active, created_at, last_login_at FROM dashboard_users ORDER BY created_at")
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def create_user(self, email: str, password_hash: str, role: str) -> int:
        conn = self._get()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO dashboard_users (email, password_hash, role) VALUES (%s, %s, %s) RETURNING id",
                    (email.lower().strip(), password_hash, role),
                )
                new_id = cur.fetchone()[0]
            conn.commit()
            return new_id
        finally:
            conn.close()

    def set_user_active(self, user_id: int, is_active: bool) -> None:
        conn = self._get()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE dashboard_users SET is_active = %s WHERE id = %s", (is_active, user_id))
            conn.commit()
        finally:
            conn.close()

    def set_password(self, user_id: int, password_hash: str) -> None:
        conn = self._get()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE dashboard_users SET password_hash = %s WHERE id = %s", (password_hash, user_id))
            conn.commit()
        finally:
            conn.close()

    def revoke_all_sessions_for_user(self, user_id: int) -> None:
        conn = self._get()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE dashboard_sessions SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL", (utcnow(), user_id))
            conn.commit()
        finally:
            conn.close()

    def touch_last_login(self, user_id: int) -> None:
        conn = self._get()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE dashboard_users SET last_login_at = %s WHERE id = %s", (utcnow(), user_id))
            conn.commit()
        finally:
            conn.close()

    # --- login challenges (2FA) ---------------------------------------
    def create_login_challenge(self, user_id: int, challenge_token_hash: str, code_hash: str, ttl_minutes: int) -> int:
        conn = self._get()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO dashboard_login_challenges (user_id, challenge_token_hash, code_hash, expires_at)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (user_id, challenge_token_hash, code_hash, utcnow() + timedelta(minutes=ttl_minutes)),
                )
                new_id = cur.fetchone()[0]
            conn.commit()
            return new_id
        finally:
            conn.close()

    def get_challenge_by_token_hash(self, challenge_token_hash: str) -> Optional[Dict]:
        conn = self._get()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM dashboard_login_challenges WHERE challenge_token_hash = %s", (challenge_token_hash,))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    def increment_challenge_attempts(self, challenge_id: int) -> None:
        conn = self._get()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE dashboard_login_challenges SET attempts = attempts + 1 WHERE id = %s", (challenge_id,))
            conn.commit()
        finally:
            conn.close()

    def consume_challenge(self, challenge_id: int) -> None:
        conn = self._get()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE dashboard_login_challenges SET consumed_at = %s WHERE id = %s", (utcnow(), challenge_id))
            conn.commit()
        finally:
            conn.close()

    # --- sessions (refresh tokens) -------------------------------------
    def create_session(self, user_id: int, refresh_token_hash: str, ttl_days: int, user_agent: Optional[str]) -> int:
        conn = self._get()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO dashboard_sessions (user_id, refresh_token_hash, expires_at, user_agent)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (user_id, refresh_token_hash, utcnow() + timedelta(days=ttl_days), user_agent),
                )
                new_id = cur.fetchone()[0]
            conn.commit()
            return new_id
        finally:
            conn.close()

    def get_session_by_token_hash(self, refresh_token_hash: str) -> Optional[Dict]:
        conn = self._get()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM dashboard_sessions WHERE refresh_token_hash = %s", (refresh_token_hash,))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    def touch_session(self, session_id: int) -> None:
        conn = self._get()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE dashboard_sessions SET last_used_at = %s WHERE id = %s", (utcnow(), session_id))
            conn.commit()
        finally:
            conn.close()

    def revoke_session(self, session_id: int) -> None:
        conn = self._get()
        try:
            with conn.cursor() as cur:
                cur.execute("UPDATE dashboard_sessions SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL", (utcnow(), session_id))
            conn.commit()
        finally:
            conn.close()

    def list_sessions_for_user(self, user_id: int) -> List[Dict]:
        conn = self._get()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """SELECT id, created_at, expires_at, revoked_at, user_agent, last_used_at
                       FROM dashboard_sessions WHERE user_id = %s ORDER BY created_at DESC""",
                    (user_id,),
                )
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_session_by_id_for_user(self, session_id: int, user_id: int) -> Optional[Dict]:
        conn = self._get()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM dashboard_sessions WHERE id = %s AND user_id = %s", (session_id, user_id))
                row = cur.fetchone()
                return dict(row) if row else None
        finally:
            conn.close()

    # --- audit -----------------------------------------------------------
    def audit(self, action: str, user_id: Optional[int] = None, actor_user_id: Optional[int] = None, detail: Optional[str] = None) -> None:
        conn = self._get()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO dashboard_auth_audit (action, user_id, actor_user_id, detail) VALUES (%s, %s, %s, %s)",
                    (action, user_id, actor_user_id, detail),
                )
            conn.commit()
        finally:
            conn.close()

    def recent_audit(self, limit: int = 50) -> List[Dict]:
        conn = self._get()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM dashboard_auth_audit ORDER BY at DESC LIMIT %s", (limit,))
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()
