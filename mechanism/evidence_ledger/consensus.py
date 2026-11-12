"""Evidence Ledger v0: the PRE-RELEASE consensus stream, a separate, separately identifiable record kind.

A record says: at `captured_at` (the actual time this capture ran) the database held this EPS estimate for a report dated `report_date`, fetched
from the vendor at `vendor_fetched_at`. It is PRE-RELEASE only if BOTH times are before the earliest moment the release can be public
(00:00 New York on the report date, which covers releases before the open). Nothing is verified retroactively: a report already past is never
captured, and a record never changes; a new estimate later is a new record (revision) of the same key.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
STREAM = "consensus_pre_release_v0"


def release_start(report_date: date) -> datetime:
    return datetime.combine(report_date, time(0, 0), tzinfo=NY)


def _aware(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def consensus_record(row: Mapping[str, Any], captured_at: datetime) -> Optional[Dict[str, Any]]:
    """None unless the row is a genuine pre-release estimate at capture time."""
    est = row.get("eps_estimate")
    if est is None:
        return None
    rs = release_start(row["report_date"])
    fetched = _aware(row.get("fetched_at"))
    if not (captured_at < rs and fetched is not None and fetched < rs):
        return None
    return {"key": f"{row['symbol']}:{row['report_date'].isoformat()}", "stream": STREAM, "symbol": row["symbol"],
            "report_date": row["report_date"].isoformat(), "eps_estimate": float(est) if isinstance(est, Decimal) else est,
            "vendor": "earnings_calendar (yfinance)", "vendor_fetched_at": fetched.astimezone(timezone.utc).isoformat(),
            "captured_at": captured_at.astimezone(timezone.utc).isoformat(), "release_start": rs.astimezone(timezone.utc).isoformat()}
