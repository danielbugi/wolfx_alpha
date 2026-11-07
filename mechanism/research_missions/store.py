"""Persistence for missions. `DbStore` writes the migration-41 tables; `MemoryStore` has the same contract for tests and dry runs.
Documents and evidence are append-only (database triggers enforce it); a document seen before is reused by (source, url, content hash)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from psycopg2.extras import Json

from research_missions.sources import Doc

STEPS = ("plan", "collect", "analyze", "verify", "report")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryStore:
    def __init__(self) -> None:
        self.missions: Dict[int, Dict[str, Any]] = {}
        self.steps: List[Dict[str, Any]] = []
        self.docs: Dict[tuple, Dict[str, Any]] = {}
        self.evidence: Dict[int, List[Dict[str, Any]]] = {}
        self.claims: Dict[int, List[Dict[str, Any]]] = {}
        self.companies: Dict[int, List[Dict[str, Any]]] = {}
        self.relations: Dict[int, List[Dict[str, Any]]] = {}
        self.reports: Dict[int, List[str]] = {}

    def create_mission(self, kind: str, title: str, question: str, domain: str, params: Dict[str, Any], budget_usd: float, created_by: Optional[str] = None) -> int:
        mid = len(self.missions) + 1
        self.missions[mid] = {"id": mid, "kind": kind, "title": title, "question": question, "domain": domain, "params": params, "status": "draft",
                              "budget_usd": budget_usd, "spent_usd": 0.0, "error": None, "created_by": created_by, "created_at": _now(), "updated_at": _now()}
        return mid

    def get_mission(self, mid: int) -> Dict[str, Any]:
        return dict(self.missions[mid])

    def set_status(self, mid: int, status: str, error: Optional[str] = None) -> None:
        self.missions[mid].update(status=status, error=error, updated_at=_now())

    def add_spent(self, mid: int, usd: float) -> None:
        self.missions[mid]["spent_usd"] += usd

    def start_step(self, mid: int, step: str) -> int:
        self.steps.append({"id": len(self.steps) + 1, "mission_id": mid, "step": step, "status": "running", "detail": {}})
        return len(self.steps)

    def finish_step(self, sid: int, status: str, detail: Dict[str, Any]) -> None:
        self.steps[sid - 1].update(status=status, detail=detail)

    def last_done(self, mid: int, step: str) -> Optional[Dict[str, Any]]:
        done = [s for s in self.steps if s["mission_id"] == mid and s["step"] == step and s["status"] == "done"]
        return done[-1] if done else None

    def add_evidence(self, mid: int, docs: Sequence[Doc]) -> List[Dict[str, Any]]:
        ev = self.evidence.setdefault(mid, [])
        for d in docs:
            key = (d.source, d.url, d.sha256)
            doc = self.docs.setdefault(key, {"id": len(self.docs) + 1, **d.__dict__})
            ev.append({"label": f"E{len(ev) + 1}", "document_id": doc["id"], "quote": d.excerpt, "source": d.source, "title": d.title, "publisher": d.publisher,
                       "published_at": d.published_at, "license": d.license, "url": d.url})
        return list(ev)

    def get_evidence(self, mid: int) -> List[Dict[str, Any]]:
        return list(self.evidence.get(mid, []))

    def save_analysis(self, mid: int, analysis: Dict[str, Any], universe: Sequence[str]) -> None:
        self.claims[mid] = [dict(c) for c in analysis["claims"]]
        uni = set(universe)
        self.companies[mid] = [{**c, "in_universe": c["symbol"] in uni} for c in analysis["companies"]]
        self.relations[mid] = list(analysis["relations"])

    def get_claims(self, mid: int) -> List[Dict[str, Any]]:
        return [dict(c) for c in self.claims.get(mid, [])]

    def update_claims(self, mid: int, claims: Sequence[Dict[str, Any]]) -> None:
        self.claims[mid] = [dict(c) for c in claims]

    def get_companies(self, mid: int) -> List[Dict[str, Any]]:
        return list(self.companies.get(mid, []))

    def get_relations(self, mid: int) -> List[Dict[str, Any]]:
        return list(self.relations.get(mid, []))

    def save_report(self, mid: int, markdown: str) -> int:
        self.reports.setdefault(mid, []).append(markdown)
        return len(self.reports[mid])


class DbStore:
    def __init__(self, db) -> None:
        self.db = db

    def create_mission(self, kind: str, title: str, question: str, domain: str, params: Dict[str, Any], budget_usd: float, created_by: Optional[str] = None) -> int:
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO research_mission (kind, title, question, domain, params, budget_usd, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                        (kind, title, question, domain, Json(params), budget_usd, created_by))
            mid = cur.fetchone()[0]
            conn.commit()
            return int(mid)

    def get_mission(self, mid: int) -> Dict[str, Any]:
        rows = self.db.execute_dict_query("SELECT * FROM research_mission WHERE id = %s", (mid,))
        if not rows:
            raise KeyError(f"mission {mid} not found")
        r = dict(rows[0])
        r["budget_usd"], r["spent_usd"] = float(r["budget_usd"]), float(r["spent_usd"])
        return r

    def set_status(self, mid: int, status: str, error: Optional[str] = None) -> None:
        self.db.execute_insert("UPDATE research_mission SET status = %s, error = %s, updated_at = NOW() WHERE id = %s", (status, error, mid))

    def add_spent(self, mid: int, usd: float) -> None:
        self.db.execute_insert("UPDATE research_mission SET spent_usd = spent_usd + %s, updated_at = NOW() WHERE id = %s", (usd, mid))

    def start_step(self, mid: int, step: str) -> int:
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO research_step (mission_id, step, status) VALUES (%s, %s, 'running') RETURNING id", (mid, step))
            sid = cur.fetchone()[0]
            conn.commit()
            return int(sid)

    def finish_step(self, sid: int, status: str, detail: Dict[str, Any]) -> None:
        self.db.execute_insert("UPDATE research_step SET status = %s, detail = %s, finished_at = NOW() WHERE id = %s",
                               (status, Json(detail, dumps=lambda o: json.dumps(o, default=str)), sid))

    def last_done(self, mid: int, step: str) -> Optional[Dict[str, Any]]:
        rows = self.db.execute_dict_query("SELECT * FROM research_step WHERE mission_id = %s AND step = %s AND status = 'done' ORDER BY id DESC LIMIT 1", (mid, step))
        return dict(rows[0]) if rows else None

    def add_evidence(self, mid: int, docs: Sequence[Doc]) -> List[Dict[str, Any]]:
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM research_evidence WHERE mission_id = %s", (mid,))
            n = int(cur.fetchone()[0])
            for d in docs:
                cur.execute("""INSERT INTO research_document (source, url, title, publisher, published_at, license, content_sha256, excerpt, meta)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (source, url, content_sha256) DO NOTHING RETURNING id""",
                            (d.source, d.url, d.title, d.publisher, d.published_at, d.license, d.sha256, d.excerpt, Json(d.meta, dumps=lambda o: json.dumps(o, default=str))))
                row = cur.fetchone()
                if row is None:
                    cur.execute("SELECT id FROM research_document WHERE source = %s AND url = %s AND content_sha256 = %s", (d.source, d.url, d.sha256))
                    row = cur.fetchone()
                n += 1
                cur.execute("INSERT INTO research_evidence (mission_id, document_id, label, quote, locator) VALUES (%s, %s, %s, %s, %s)",
                            (mid, row[0], f"E{n}", d.excerpt, d.url))
            conn.commit()
        return self.get_evidence(mid)

    def get_evidence(self, mid: int) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.db.execute_dict_query("""
            SELECT e.id, e.label, e.document_id, e.quote, d.source, d.title, d.publisher, d.published_at, d.license, d.url
            FROM research_evidence e JOIN research_document d ON d.id = e.document_id WHERE e.mission_id = %s ORDER BY e.id""", (mid,))]

    def save_analysis(self, mid: int, analysis: Dict[str, Any], universe: Sequence[str]) -> None:
        ev_ids = {r["label"]: r["id"] for r in self.get_evidence(mid)}
        uni = set(universe)
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            for c in analysis["claims"]:
                cur.execute("""INSERT INTO research_claim (mission_id, label, kind, text, confidence, status, verifier_note) VALUES (%s, %s, %s, %s, %s, %s, %s)
                               ON CONFLICT (mission_id, label) DO NOTHING RETURNING id""",
                            (mid, c["label"], c["kind"], c["text"], c["confidence"], c["status"], c["verifier_note"]))
                row = cur.fetchone()
                if row is None:
                    continue
                for lab in c["evidence"]:
                    if lab in ev_ids:
                        cur.execute("INSERT INTO research_claim_evidence (claim_id, evidence_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (row[0], ev_ids[lab]))
            for co in analysis["companies"]:
                cur.execute("""INSERT INTO research_company (mission_id, symbol, name, in_universe, role, exposure, claim_labels, risk_labels)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (mission_id, symbol) DO NOTHING""",
                            (mid, co["symbol"], co["name"], co["symbol"] in uni, co["role"], co["exposure"], Json(co["claim_labels"]), Json(co["risk_labels"])))
            for r in analysis["relations"]:
                cur.execute("INSERT INTO research_relation (mission_id, from_entity, to_entity, relation, claim_labels) VALUES (%s, %s, %s, %s, %s)",
                            (mid, r["from_entity"], r["to_entity"], r["relation"], Json(r["claim_labels"])))
            conn.commit()

    def get_claims(self, mid: int) -> List[Dict[str, Any]]:
        rows = self.db.execute_dict_query("""
            SELECT c.label, c.kind, c.text, c.confidence, c.status, c.verifier_note,
                   COALESCE(ARRAY_AGG(e.label ORDER BY e.id) FILTER (WHERE e.id IS NOT NULL), '{}') AS evidence
            FROM research_claim c LEFT JOIN research_claim_evidence ce ON ce.claim_id = c.id LEFT JOIN research_evidence e ON e.id = ce.evidence_id
            WHERE c.mission_id = %s GROUP BY c.id ORDER BY c.id""", (mid,))
        return [dict(r, evidence=list(r["evidence"])) for r in rows]

    def update_claims(self, mid: int, claims: Sequence[Dict[str, Any]]) -> None:
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            for c in claims:
                cur.execute("UPDATE research_claim SET status = %s, verifier_note = %s WHERE mission_id = %s AND label = %s", (c["status"], c["verifier_note"], mid, c["label"]))
            conn.commit()

    def get_companies(self, mid: int) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.db.execute_dict_query("SELECT symbol, name, in_universe, role, exposure, claim_labels, risk_labels FROM research_company WHERE mission_id = %s ORDER BY id", (mid,))]

    def get_relations(self, mid: int) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.db.execute_dict_query("SELECT from_entity, to_entity, relation, claim_labels FROM research_relation WHERE mission_id = %s ORDER BY id", (mid,))]

    def save_report(self, mid: int, markdown: str) -> int:
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM research_report WHERE mission_id = %s", (mid,))
            v = int(cur.fetchone()[0])
            cur.execute("INSERT INTO research_report (mission_id, version, markdown) VALUES (%s, %s, %s)", (mid, v, markdown))
            conn.commit()
            return v
