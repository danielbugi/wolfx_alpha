# File: backend/routers/lab_reports.py
"""
Quant Lab reports API - internal, authenticated, READ-ONLY. Serves the versioned research artifacts in data/lab_reports (see services/lab_reports_service.py).
Nothing here reads or writes the database and nothing influences screening, scoring or any live signal.
"""
from fastapi import APIRouter, Depends, HTTPException

from auth.dependencies import require_authenticated_user
from services.lab_reports_service import LabReportsService

lab_reports_router = APIRouter(
    prefix="/api/lab",
    tags=["lab"],
    dependencies=[Depends(require_authenticated_user)],
)


def get_lab_reports_service() -> LabReportsService:
    return LabReportsService()


@lab_reports_router.get("/reports")
def list_reports(service: LabReportsService = Depends(get_lab_reports_service)):
    return service.list_reports()


@lab_reports_router.get("/reports/{report_id}")
def get_report(report_id: str, service: LabReportsService = Depends(get_lab_reports_service)):
    doc = service.get_report(report_id)
    if doc is None:
        raise HTTPException(status_code=404, detail={"code": "report_not_found"})
    return doc
