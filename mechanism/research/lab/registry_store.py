"""SQL for migration 30 on a raw psycopg2 cursor. Minimum privileges: SELECT + INSERT on dataset_manifest, experiment_registration,
experiment_result. Nothing here updates, deletes or truncates (the triggers would refuse anyway), reads a clock, or trains anything.

Idempotence is by content hash: registering the same manifest/registration twice returns the stored row; a different document that reuses a
name/version raises (a dataset or experiment is never silently replaced -- register a new version)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from psycopg2.extras import Json

from research.lab import manifest as M

MANIFEST_COLS = ("dataset_name", "dataset_version", "manifest_hash", "code_sha", "label_version", "label_methodology_version", "label_horizons",
                 "feature_versions", "universe_id", "universe_hash", "knowledge_cutoff_at", "label_maturity_session", "train_start", "train_end",
                 "validation_start", "validation_end", "test_start", "test_end", "embargo_sessions", "purge_sessions", "calendar_source",
                 "input_hashes", "config", "manifest")
JSON_COLS = {"feature_versions", "input_hashes", "config", "manifest", "model_spec", "evaluation_plan", "metrics", "artifact_hashes"}
REG_COLS = ("experiment_name", "registration_hash", "manifest_hash", "code_sha", "label_version", "feature_versions", "model_spec",
            "search_budget", "seed", "evaluation_plan", "hypothesis")


def db_now(cur):
    cur.execute("SELECT clock_timestamp()")
    return cur.fetchone()[0]


def _insert(cur, table: str, cols: Sequence[str], row: Dict[str, Any], key: str) -> Optional[int]:
    vals = [Json(row[c]) if c in JSON_COLS else row[c] for c in cols]
    cur.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                f"ON CONFLICT ({key}) DO NOTHING RETURNING id", vals)
    r = cur.fetchone()
    return r[0] if r else None


def insert_manifest(cur, m: M.Manifest) -> Dict[str, Any]:
    """Returns {id, created: bool}. Same hash => the stored row. Same name+version but different content => UniqueViolation."""
    new = _insert(cur, "dataset_manifest", MANIFEST_COLS, m.row(), "manifest_hash")
    if new is not None:
        return {"id": new, "created": True}
    cur.execute("SELECT id FROM dataset_manifest WHERE manifest_hash = %s", (m.manifest_hash,))
    return {"id": cur.fetchone()[0], "created": False}


def get_manifest(cur, manifest_hash: str) -> Optional[Dict[str, Any]]:
    cur.execute("SELECT manifest, created_at FROM dataset_manifest WHERE manifest_hash = %s", (manifest_hash,))
    r = cur.fetchone()
    return None if r is None else {"manifest": r[0], "created_at": r[1]}


def insert_registration(cur, reg: M.Registration) -> Dict[str, Any]:
    new = _insert(cur, "experiment_registration", REG_COLS, reg.row(), "registration_hash")
    if new is not None:
        return {"id": new, "created": True}
    cur.execute("SELECT id FROM experiment_registration WHERE registration_hash = %s", (reg.registration_hash,))
    return {"id": cur.fetchone()[0], "created": False}


def result_kinds(cur, registration_hash: str) -> List[str]:
    cur.execute("SELECT result_kind FROM experiment_result WHERE registration_hash = %s ORDER BY id", (registration_hash,))
    return [r[0] for r in cur.fetchall()]


def append_result(cur, spec: M.ResultSpec, reg: M.Registration) -> int:
    """Validate with the pure rules (against the stored history), then INSERT. The database re-checks the same rules independently."""
    row = M.validate_result(spec, reg, result_kinds(cur, spec.registration_hash))
    cur.execute("INSERT INTO experiment_result (registration_hash, result_kind, metrics, n_configs_tried, artifact_hashes, code_sha, note) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (row["registration_hash"], row["result_kind"], Json(row["metrics"]), row["n_configs_tried"], Json(row["artifact_hashes"]),
                 row["code_sha"], row["note"]))
    return cur.fetchone()[0]
