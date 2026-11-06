"""Read-only source connection (RESEARCH_SOURCE_DSN): FirstLightDb reads production data through it while missions, evidence, claims and the
spend ledger are written to a separate database (the store, `shared.db`).

A superuser source role is refused outright (it could switch read-only off). Three independent layers keep the source read-only, and the
connection is refused unless all of them are proven on the live session:
  1. the server session default: `-c default_transaction_read_only=on` in the connection options;
  2. the client: psycopg2 `set_session(readonly=True)`;
  3. a write probe at connect time: `CREATE TEMP TABLE` must fail with SQLSTATE 25006 (read_only_sql_transaction).
On top of that, `execute_dict_query` only accepts a single SELECT / WITH statement.

`assert_isolated(store, source)` refuses to run when the store and the source are the same server and database (fingerprint: database name,
postmaster start time, port and version; a different server never shares a postmaster start time to the microsecond).

The DSN carries a password: it is never logged, printed or put in an error message (errors name the variable, not its value).
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, List, Optional, Sequence

ENV = "RESEARCH_SOURCE_DSN"
STATEMENT_TIMEOUT_MS = 60_000
_SELECT = re.compile(r"^\s*(select|with)\b", re.I)
_FINGERPRINT_SQL = ("SELECT current_database() AS database, pg_postmaster_start_time()::text AS started, "
                    "COALESCE(inet_server_port(), 0) AS port, version() AS version")


class SourceNotReadOnly(RuntimeError):
    pass


class StoreNotIsolated(RuntimeError):
    pass


def _default_connect(dsn: str, **kw: Any):
    import psycopg2
    return psycopg2.connect(dsn, **kw)


class ReadOnlySourceDb:
    """The `execute_dict_query` interface FirstLightDb needs, over one read-only session."""

    def __init__(self, dsn: str, connect: Optional[Callable[..., Any]] = None) -> None:
        if not dsn:
            raise SourceNotReadOnly(f"{ENV} is empty")
        try:
            self.conn = (connect or _default_connect)(
                dsn, options=f"-c default_transaction_read_only=on -c statement_timeout={STATEMENT_TIMEOUT_MS}",
                application_name="research_missions_source")
        except Exception as e:
            raise SourceNotReadOnly(f"cannot connect with {ENV} ({type(e).__name__})") from None   # the message may echo the DSN
        self.conn.set_session(readonly=True, autocommit=True)
        self.proof = self._prove_read_only()

    def _scalar(self, sql: str) -> Any:
        cur = self.conn.cursor()
        cur.execute(sql)
        row = cur.fetchone()
        return row[0] if row else None

    def _prove_read_only(self) -> Dict[str, Any]:
        if self._scalar("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"):
            self.close()                                        # a superuser can switch read-only off; the source must be an ordinary role
            raise SourceNotReadOnly("the source role is a superuser: use a non-superuser (ideally SELECT-only) role")
        proof = {"transaction_read_only": self._scalar("SHOW transaction_read_only"),
                 "default_transaction_read_only": self._scalar("SHOW default_transaction_read_only")}
        if proof["transaction_read_only"] != "on" or proof["default_transaction_read_only"] != "on":
            self.close()
            raise SourceNotReadOnly(f"source session is not read-only: {proof}")
        try:
            self.conn.cursor().execute("CREATE TEMP TABLE research_missions_ro_probe (x int)")
        except Exception as e:
            if getattr(e, "pgcode", None) != "25006":
                self.close()
                raise SourceNotReadOnly(f"write probe failed for an unexpected reason ({type(e).__name__} {getattr(e, 'pgcode', None)})") from None
            proof["write_probe"] = "refused (25006 read_only_sql_transaction)"
            return proof
        self.close()
        raise SourceNotReadOnly("write probe succeeded: the source session accepts writes")

    def execute_dict_query(self, query: str, params: Optional[Sequence[Any]] = None) -> List[Dict[str, Any]]:
        if not _SELECT.match(query) or ";" in query.strip().rstrip(";"):
            raise SourceNotReadOnly("the source connection only runs a single SELECT / WITH statement")
        from psycopg2.extras import RealDictCursor
        cur = self.conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


def fingerprint(db: Any) -> Dict[str, Any]:
    return db.execute_dict_query(_FINGERPRINT_SQL)[0]


def assert_isolated(store: Any, source: Any) -> Dict[str, Any]:
    s, r = fingerprint(store), fingerprint(source)
    if s == r:
        raise StoreNotIsolated(f"the store and the source are the same database ({s['database']}): results must go to a separate database")
    return {"store": {k: s[k] for k in ("database", "started", "port")}, "source": {k: r[k] for k in ("database", "started", "port")}}


def from_env() -> Optional[ReadOnlySourceDb]:
    dsn = os.environ.get(ENV, "").strip()
    return ReadOnlySourceDb(dsn) if dsn else None
