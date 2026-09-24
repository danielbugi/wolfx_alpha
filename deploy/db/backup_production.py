"""Read-only production backup: pg_dump + exact per-table row counts from the SAME snapshot.

Runs on the machine that hosts the current production Postgres (the owner's Windows PC today).
Never writes to the database: one REPEATABLE READ, READ ONLY transaction exports its snapshot,
pg_dump dumps exactly that snapshot (--snapshot), and every table is counted inside the same
transaction -- so a restore can be verified by exact equality even while the live system writes.

Outputs (outside the repo, default %USERPROFILE%/donchian_backups/<UTC timestamp>/):
  production.dump        pg_dump custom format (-Fc), compressed
  inventory.json         server/db facts, per-table exact row counts, sequences, schema object counts
  production.dump.sha256
Optional --offbox copies the three files to the VPS (root-only /opt/donchian/backups/<label>/) over SSH.

Connection settings come from the repo's .env (DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD) or
--env; the password is passed to child processes via PGPASSWORD and never printed or written.

usage: python deploy/db/backup_production.py [--env PATH] [--out DIR] [--offbox root@HOST --ssh-key PATH]
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

REPO = Path(__file__).resolve().parents[2]
PG_BIN_CANDIDATES = [r"C:\Program Files\PostgreSQL\16\bin", r"C:\Program Files\PostgreSQL\17\bin", "/usr/bin"]


def read_env(path: Path) -> dict:
    vals = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def find_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for d in PG_BIN_CANDIDATES:
        for cand in (Path(d) / name, Path(d) / f"{name}.exe"):
            if cand.exists():
                return str(cand)
    sys.exit(f"{name} not found (install PostgreSQL client tools or add them to PATH)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default=str(REPO / ".env"))
    ap.add_argument("--out", default=str(Path.home() / "donchian_backups"))
    ap.add_argument("--offbox", help="e.g. root@116.203.220.219 -- copies the backup to /opt/donchian/backups/prod/<ts>/")
    ap.add_argument("--ssh-key", help="private key for --offbox")
    ap.add_argument("--label", default="prod", help="off-box folder name (default prod; use e.g. tooling-test for rehearsals)")
    args = ap.parse_args()

    env = read_env(Path(args.env))
    conn_kw = dict(host=env.get("DB_HOST", "localhost"), port=int(env.get("DB_PORT", "5432")),
                   dbname=env["DB_NAME"], user=env["DB_USER"], password=env["DB_PASSWORD"])
    out_dir = Path(args.out) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if REPO in out_dir.resolve().parents:
        sys.exit("refusing to write a production dump inside the repository")
    out_dir.mkdir(parents=True, exist_ok=False)

    pg_dump = find_tool("pg_dump")
    dump_version = subprocess.run([pg_dump, "--version"], capture_output=True, text=True, check=True).stdout.strip()

    conn = psycopg2.connect(**conn_kw)
    conn.set_session(isolation_level="REPEATABLE READ", readonly=True)
    cur = conn.cursor()
    cur.execute("SELECT pg_export_snapshot()")
    snapshot = cur.fetchone()[0]

    child_env = dict(os.environ, PGPASSWORD=conn_kw["password"])
    dump_path = out_dir / "production.dump"
    print(f"dumping {conn_kw['dbname']}@{conn_kw['host']}:{conn_kw['port']} (snapshot {snapshot}) -> {dump_path}")
    subprocess.run([pg_dump, "-h", conn_kw["host"], "-p", str(conn_kw["port"]), "-U", conn_kw["user"],
                    "-d", conn_kw["dbname"], "-Fc", "-Z", "6", f"--snapshot={snapshot}", "-f", str(dump_path)],
                   env=child_env, check=True)

    cur.execute("SELECT current_setting('server_version'), pg_database_size(current_database()), "
                "current_setting('TimeZone'), pg_encoding_to_char(encoding), datcollate "
                "FROM pg_database WHERE datname = current_database()")
    server_version, db_size, tz, encoding, collate = cur.fetchone()
    cur.execute("SELECT schemaname, tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') "
                "ORDER BY 1, 2")
    tables = cur.fetchall()
    counts = {}
    for schema, table in tables:
        cur.execute(f'SELECT count(*) FROM "{schema}"."{table}"')
        counts[f"{schema}.{table}"] = cur.fetchone()[0]
    cur.execute("SELECT schemaname||'.'||sequencename, last_value FROM pg_sequences "
                "WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1")
    sequences = {name: val for name, val in cur.fetchall()}
    cur.execute("SELECT count(*) FROM pg_views WHERE schemaname NOT IN ('pg_catalog','information_schema')")
    n_views = cur.fetchone()[0]
    cur.execute("SELECT contype, count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
                "WHERE n.nspname NOT IN ('pg_catalog','information_schema') GROUP BY 1")
    constraints = {k: v for k, v in cur.fetchall()}
    cur.execute("SELECT count(*) FROM pg_indexes WHERE schemaname NOT IN ('pg_catalog','information_schema')")
    n_indexes = cur.fetchone()[0]
    cur.execute("SELECT extname, extversion FROM pg_extension ORDER BY 1")
    extensions = dict(cur.fetchall())
    conn.rollback()
    conn.close()

    sha = hashlib.sha256()
    with open(dump_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    digest = sha.hexdigest()
    (out_dir / "production.dump.sha256").write_text(f"{digest}  production.dump\n", encoding="ascii")

    inventory = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "snapshot": snapshot,
        "database": conn_kw["dbname"], "host": conn_kw["host"], "server_version": server_version,
        "pg_dump_version": dump_version, "db_size_bytes": db_size, "timezone": tz, "encoding": encoding,
        "collate": collate, "dump_bytes": dump_path.stat().st_size, "dump_sha256": digest,
        "tables": len(counts), "views": n_views, "indexes": n_indexes, "constraints_by_type": constraints,
        "extensions": extensions, "row_counts": counts, "sequences": sequences,
    }
    (out_dir / "inventory.json").write_text(json.dumps(inventory, indent=2, default=str), encoding="utf-8")
    print(f"OK: {len(counts)} tables, {sum(counts.values()):,} rows, {n_views} views, "
          f"dump {dump_path.stat().st_size / 1e6:.1f} MB, sha256 {digest[:16]}...")
    print(f"backup directory: {out_dir}")

    if args.offbox:
        ts = out_dir.name
        ssh = ["ssh", "-o", "BatchMode=yes"] + (["-i", args.ssh_key] if args.ssh_key else [])
        scp = ["scp", "-q", "-o", "BatchMode=yes"] + (["-i", args.ssh_key] if args.ssh_key else [])
        remote = f"/opt/donchian/backups/{args.label}/{ts}"
        subprocess.run(ssh + [args.offbox, f"install -d -m 700 /opt/donchian/backups/{args.label} {remote}"], check=True)
        # relative names + cwd: scp would read a Windows path like "E:\..." as host "E"
        subprocess.run(scp + ["production.dump", "production.dump.sha256", "inventory.json", f"{args.offbox}:{remote}/"],
                       cwd=out_dir, check=True)
        check = subprocess.run(ssh + [args.offbox, f"cd {remote} && chmod 600 * && sha256sum -c production.dump.sha256"],
                               capture_output=True, text=True)
        print(check.stdout.strip() or check.stderr.strip())
        if check.returncode != 0:
            sys.exit("off-box copy FAILED checksum verification")
        print(f"off-box copy verified: {args.offbox}:{remote}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
