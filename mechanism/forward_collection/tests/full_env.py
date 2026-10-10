"""A throwaway production-shaped DATABASE for the activation preflight (explicit imports only; never a conftest name).

Bootstraps a fresh database from docker-compose.yml's own init list (the files production was built from, in apply order), applies the real role
script under throwaway role names, and gives the preflight a real login as the LEAST-PRIVILEGE runtime role. Needs a SUPERUSER DB role and a
`trading_user` role (the baseline init files assume it); callers skip otherwise. Mirrors research/tests/test_roles_full_schema.py (the two
packages cannot share a helper: nothing outside forward_collection may import it)."""
import os
import re
import uuid
from datetime import date, timedelta

import psycopg2
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ROLES_SQL = os.path.join(ROOT, "deploy", "db", "research_roles.sql")
APP_PASSWORD = "fc-activation-test-only"
ANCHOR = date(2026, 3, 6)                       # a Friday: the newest loaded session of the throwaway market
WEEKDAYS = 40


def connect_args():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    return dict(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), dbname=os.environ["DB_NAME"],
                user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"])


def init_files():
    with open(os.path.join(ROOT, "docker-compose.yml"), encoding="utf-8") as fh:
        pairs = re.findall(r"\./(mechanism/[\w./-]+\.sql):/docker-entrypoint-initdb\.d/(\d+)_", fh.read())
    return [os.path.join(ROOT, p) for p, _ in sorted(pairs, key=lambda x: int(x[1]))]


def weekdays(n=WEEKDAYS, end=ANCHOR):
    out, d = [], end
    while len(out) < n:
        if d.isoweekday() < 6:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


class Env:
    def __init__(self, dbname, prefix, args):
        self.dbname, self.args, self.prefix = dbname, args, prefix
        self.names = {"donchian_owner": f"{prefix}_owner", "donchian_app": f"{prefix}_app", "donchian_research_admin": f"{prefix}_admin"}
        self.owner, self.app, self.group = (self.names[k] for k in ("donchian_owner", "donchian_app", "donchian_research_admin"))
        self.db_args = dict(args, dbname=dbname)

    def admin(self):
        conn = psycopg2.connect(**self.db_args)
        conn.cursor().execute("SET search_path = public")
        conn.commit()
        return conn

    def roles_sql(self):
        with open(ROLES_SQL, encoding="utf-8") as fh:
            text = fh.read()
        for fixed in sorted(self.names, key=len, reverse=True):
            text = text.replace(fixed, self.names[fixed])
        return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("\\"))

    def app_connect_factory(self):
        def connect():
            return psycopg2.connect(**dict(self.db_args, user=self.app, password=APP_PASSWORD))
        return connect

    def admin_connect_factory(self):
        return lambda: psycopg2.connect(**self.db_args)

    def exec_admin(self, sql, *params):
        conn = self.admin()
        try:
            conn.cursor().execute(sql, params or None)
            conn.commit()
        finally:
            conn.close()

    def load_market(self, *, symbols=("AAA", "BBB", "CCC"), fundamentals_through=ANCHOR):
        """A minimal throwaway market: every weekday bar for each symbol and the S&P index, and a fundamentals row per symbol up to a date."""
        days = weekdays()
        conn = self.admin()
        try:
            cur = conn.cursor()
            for d in days:
                for s in symbols:
                    cur.execute("INSERT INTO stock_prices (symbol, date, open, high, low, close, adj_close, volume) VALUES (%s,%s,10,11,9,10,10,1000)", (s, d))
                cur.execute("INSERT INTO market_index_prices (symbol, date, open, high, low, close, volume) VALUES ('^GSPC',%s,10,11,9,10,1000)", (d,))
            for s in symbols:
                cur.execute("INSERT INTO daily_fundamentals (symbol, date, sector) VALUES (%s,%s,'Technology')", (s, fundamentals_through))
            conn.commit()
        finally:
            conn.close()


def make_env(prefix_root="fca"):
    """Returns (env, teardown). Skips (via pytest.skip) when the cluster cannot host the throwaway database."""
    try:
        args = connect_args()
        boot = psycopg2.connect(**args)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres unreachable: {type(e).__name__}")
    cur = boot.cursor()
    cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    if not cur.fetchone()[0]:
        boot.close()
        pytest.skip("needs a superuser DB role (CREATE DATABASE/ROLE)")
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'trading_user'")
    if not cur.fetchone():
        boot.close()
        pytest.skip("the baseline init files assume a 'trading_user' role")
    boot.rollback()
    boot.autocommit = True
    prefix = f"{prefix_root}_" + uuid.uuid4().hex[:8]
    dbname = prefix + "_db"
    boot.cursor().execute(f'CREATE DATABASE "{dbname}"')
    env = Env(dbname, prefix, args)

    def teardown():
        try:
            bcur = boot.cursor()
            bcur.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{dbname}' AND pid <> pg_backend_pid()")
            bcur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
            for r in (env.app, env.group, env.owner):
                bcur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (r,))
                if bcur.fetchone():
                    bcur.execute(f'DROP ROLE "{r}"')
        finally:
            boot.close()

    try:
        conn = env.admin()
        cur = conn.cursor()
        for path in init_files():
            with open(path, encoding="utf-8") as fh:
                cur.execute(fh.read())
            conn.commit()
        conn.close()
        conn = env.admin()
        conn.cursor().execute(env.roles_sql())
        conn.commit()
        conn.close()
        env.exec_admin(f"ALTER ROLE \"{env.app}\" PASSWORD '{APP_PASSWORD}'")
    except Exception:
        teardown()
        raise
    return env, teardown
