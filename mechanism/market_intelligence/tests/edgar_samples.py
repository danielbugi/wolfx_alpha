"""Canned EDGAR `submissions` documents (shape of data.sec.gov/submissions/CIK##########.json). No network."""
from datetime import datetime, timezone

CIK_A, CIK_B = 320193, 789019
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
UA = "First Light Research ops@first-light.finance"


def doc(cik, rows, files=()):
    cols = ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "form", "items", "primaryDocument")
    recent = {c: [r.get(c, "") for r in rows] for c in cols}
    return {"cik": f"{cik:010d}", "name": "X", "filings": {"recent": recent, "files": list(files)}}


def r(acc, form="8-K", filed="2026-09-18", accepted="2026-09-18T20:05:11.000Z", items="2.02,9.01", period="", doc_="a.htm"):
    return {"accessionNumber": acc, "form": form, "filingDate": filed, "acceptanceDateTime": accepted, "items": items,
            "reportDate": period, "primaryDocument": doc_}
