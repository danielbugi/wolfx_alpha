# File: backend/services/research_missions_service.py
"""
Research Missions read model (mechanism/research_missions, migration 41). Owner only (the router enforces it).

Reads missions, their steps, evidence, verified/unverified claims, companies, relations, latest report and model-call costs. The only write is
creating a DRAFT mission: nothing here runs a mission or makes a paid call (missions run from the CLI, by hand, in the MVP).
"""
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from psycopg2.extras import RealDictCursor

_MECHANISM = str(Path(__file__).resolve().parents[2] / "mechanism")
if _MECHANISM not in sys.path:
    sys.path.append(_MECHANISM)

from research_missions import config as RC  # noqa: E402
from research_missions.budget import limits_from_env  # noqa: E402
from research_missions.llm import paid_calls_enabled  # noqa: E402

logger = logging.getLogger(__name__)


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


class ResearchMissionsService:
    def __init__(self, get_connection: Callable) -> None:
        self._get_connection = get_connection

    def _run(self, sql: str, params: Sequence[Any] = (), write: bool = False) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()] if cur.description else []
            cur.close()
            if write:
                conn.commit()
            return rows
        finally:
            if not write:
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ reads
    def list_missions(self, status: Optional[str] = None, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        where, params = [], []
        if status:
            where.append("status = %s")
            params.append(status)
        if kind:
            where.append("kind = %s")
            params.append(kind)
        rows = self._run(f"""SELECT id, kind, title, domain, status, spent_usd, budget_usd, created_at, updated_at FROM research_mission
                             {('WHERE ' + ' AND '.join(where)) if where else ''} ORDER BY updated_at DESC LIMIT 200""", params)
        return [{**r, "spent_usd": float(r["spent_usd"]), "budget_usd": float(r["budget_usd"]), "created_at": _iso(r["created_at"]), "updated_at": _iso(r["updated_at"])} for r in rows]

    def get_mission(self, mission_id: int) -> Optional[Dict[str, Any]]:
        m = self._run("SELECT * FROM research_mission WHERE id = %s", (mission_id,))
        if not m:
            return None
        mission = {k: _iso(v) for k, v in m[0].items()}
        mission["spent_usd"], mission["budget_usd"] = float(m[0]["spent_usd"]), float(m[0]["budget_usd"])
        steps = [{k: _iso(v) for k, v in r.items()} for r in self._run("SELECT step, status, detail, started_at, finished_at FROM research_step WHERE mission_id = %s ORDER BY id", (mission_id,))]
        evidence = [{k: _iso(v) for k, v in r.items()} for r in self._run("""
            SELECT e.label, d.source, d.title, d.publisher, d.url, d.published_at, d.license, e.quote FROM research_evidence e JOIN research_document d ON d.id = e.document_id
            WHERE e.mission_id = %s ORDER BY e.id""", (mission_id,))]
        claims = self._run("""
            SELECT c.label, c.kind, c.text, c.confidence, c.status, c.verifier_note,
                   COALESCE(ARRAY_AGG(e.label ORDER BY e.id) FILTER (WHERE e.id IS NOT NULL), '{}') AS evidence
            FROM research_claim c LEFT JOIN research_claim_evidence ce ON ce.claim_id = c.id LEFT JOIN research_evidence e ON e.id = ce.evidence_id
            WHERE c.mission_id = %s GROUP BY c.id ORDER BY c.id""", (mission_id,))
        companies = self._run("SELECT symbol, name, in_universe, role, exposure, claim_labels, risk_labels FROM research_company WHERE mission_id = %s ORDER BY id", (mission_id,))
        relations = self._run("SELECT from_entity, to_entity, relation, claim_labels FROM research_relation WHERE mission_id = %s ORDER BY id", (mission_id,))
        report = self._run("SELECT version, markdown, created_at FROM research_report WHERE mission_id = %s ORDER BY version DESC LIMIT 1", (mission_id,))
        usage = self._run("""SELECT COUNT(*) FILTER (WHERE status IN ('settled', 'failed')) AS calls, COUNT(*) FILTER (WHERE status = 'refused') AS refused,
                                    COALESCE(SUM(cost_usd), 0) AS cost_usd FROM research_llm_usage WHERE mission_id = %s""", (mission_id,))[0]
        return {"mission": mission, "steps": steps, "evidence": evidence, "claims": [dict(c, evidence=list(c["evidence"])) for c in claims], "companies": companies,
                "relations": relations, "report_markdown": report[0]["markdown"] if report else None, "report_version": report[0]["version"] if report else None,
                "usage": {"calls": int(usage["calls"]), "refused": int(usage["refused"]), "cost_usd": float(usage["cost_usd"])}}

    def budget(self) -> Dict[str, Any]:
        lim = limits_from_env()
        r = self._run("""SELECT to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM') AS month,
                                COALESCE(SUM(cost_usd) FILTER (WHERE status IN ('settled', 'failed')), 0) AS spent,
                                COALESCE(SUM(reserved_usd) FILTER (WHERE status = 'reserved'), 0) AS reserved
                         FROM research_llm_usage WHERE created_at >= date_trunc('month', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'""")[0]
        spent, reserved = float(r["spent"]), float(r["reserved"])
        return {"month": r["month"], "cap_usd": lim.monthly_usd, "mission_cap_usd": lim.mission_usd, "spent_usd": round(spent, 6), "reserved_usd": round(reserved, 6),
                "remaining_usd": round(lim.monthly_usd - spent - reserved, 6), "paid_calls_enabled": paid_calls_enabled()}

    # ------------------------------------------------------------------ the one write: a draft
    def create_draft(self, kind: str, title: str, question: str, domain: str, params: Dict[str, Any], budget_usd: Optional[float], created_by: str) -> Dict[str, Any]:
        if domain not in RC.DOMAINS:
            raise ValueError(f"unknown domain; configured: {', '.join(sorted(RC.DOMAINS))}")
        if kind == "thematic" and not RC.DOMAINS[domain]["industries"]:
            raise ValueError("a thematic mission needs a domain with a candidate universe")
        if kind == "company" and not (params.get("symbol") or params.get("event_id")):
            raise ValueError("a company mission needs params.symbol or params.event_id")
        cap = limits_from_env().mission_usd
        budget = cap if budget_usd is None else max(0.0, min(float(budget_usd), cap))
        row = self._run("""INSERT INTO research_mission (kind, title, question, domain, params, budget_usd, created_by) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
                           RETURNING id, status""", (kind, title, question, domain, json.dumps(params), budget, created_by), write=True)[0]
        return {"id": row["id"], "status": row["status"], "budget_usd": budget}

    @staticmethod
    def domains() -> List[Dict[str, Any]]:
        return [{"key": k, "label": v["label"], "thematic": bool(v["industries"]), "sources": v["sources"]} for k, v in RC.DOMAINS.items()]
