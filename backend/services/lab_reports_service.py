# File: backend/services/lab_reports_service.py
"""
Quant Lab reports - read-only serving of versioned research artifacts (backend/lab_reports/*.json, produced offline by
ml_training/experiments/build_lab_reports.py from evidence files). No database, no network, no write path.

Every report must carry its provenance and its caveats and be marked research-only; a file that does not is reported as invalid and never
served. Report ids are looked up in the index (never used as a path), so a request cannot name an arbitrary file.
"""
import json
import os
import re
from typing import Any, Dict, List, Optional

DEFAULT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lab_reports")
ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
REQUIRED = ("schema", "id", "kind", "title", "headline", "generated_at", "code_ref", "status", "use", "caveats", "provenance", "data")
KINDS = ("strategy_lab", "ml", "catalyst")
NOTICE = "Research evidence only. Not a trading signal, not advice, and not used by the live signal path."


def validate(doc: Any) -> List[str]:
    """Reasons a report may not be served (empty = valid)."""
    if not isinstance(doc, dict):
        return ["not a JSON object"]
    problems = [f"missing {k}" for k in REQUIRED if k not in doc]
    if problems:
        return problems
    if doc["status"] != "research_only":
        problems.append("status must be research_only")
    if doc["kind"] not in KINDS:
        problems.append("unknown kind")
    if not isinstance(doc["caveats"], list) or not doc["caveats"]:
        problems.append("caveats must be a non-empty list")
    prov = doc["provenance"]
    if not isinstance(prov, dict) or not prov.get("source"):
        problems.append("provenance.source is required")
    if not ID_RE.match(str(doc["id"])):
        problems.append("invalid id")
    return problems


class LabReportsService:
    def __init__(self, directory: Optional[str] = None) -> None:
        self.directory = directory or DEFAULT_DIR

    def _read(self, name: str) -> Any:
        with open(os.path.join(self.directory, name), encoding="utf-8") as fh:
            return json.load(fh)

    def _index(self) -> List[Dict[str, Any]]:
        try:
            idx = self._read("index.json")
        except (OSError, ValueError):
            return []
        return [r for r in idx.get("reports", []) if isinstance(r, dict) and ID_RE.match(str(r.get("id", ""))) and str(r.get("file", "")) == f"{r['id']}.json"]

    def list_reports(self) -> Dict[str, Any]:
        out, invalid = [], []
        for r in self._index():
            try:
                doc = self._read(r["file"])
            except (OSError, ValueError):
                invalid.append({"id": r["id"], "problems": ["unreadable"]})
                continue
            p = validate(doc)
            if p or doc.get("id") != r["id"]:
                invalid.append({"id": r["id"], "problems": p or ["id does not match the index"]})
                continue
            out.append({"id": doc["id"], "kind": doc["kind"], "title": doc["title"], "headline": doc["headline"], "generated_at": doc["generated_at"], "code_ref": doc["code_ref"],
                        "status": doc["status"]})
        return {"available": bool(out), "notice": NOTICE, "reports": out, "invalid": invalid}

    def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        if not ID_RE.match(report_id or ""):
            return None
        entry = next((r for r in self._index() if r["id"] == report_id), None)
        if entry is None:
            return None
        try:
            doc = self._read(entry["file"])
        except (OSError, ValueError):
            return None
        if validate(doc) or doc.get("id") != report_id:
            return None
        return doc
