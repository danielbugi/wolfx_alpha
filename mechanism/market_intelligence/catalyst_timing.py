"""Which trading bar first reflects a filing, and which 8-K earnings release belongs to a periodic report. PURE: stdlib + filing_contract.

The SEC acceptance timestamp is the one PIT-relevant instant (see filing_contract). From it and a list of trading sessions this module decides, deterministically:
  * the REACTION bar: a filing accepted before the open or during the session is first fully reflected in that session's bar; one accepted at/after 16:00 ET (or on a non-session day) in the next
    session's bar. The pre-event reference is the close of the bar before it, so the window never starts after the information was public.
  * the DRIFT start: the open of the session after the reaction bar (what a trader could still capture).
It never rolls a weekend or holiday filing backwards, and it reports 'UNKNOWN' timing exactly as filing_session_timing does.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, datetime
from typing import Optional, Sequence, Tuple

from market_intelligence import filing_contract as F

RELEASE_MAX_DAYS_AFTER_PERIOD = 100          # an earnings 8-K filed later than this after the period end is not matched to that period


def reaction_bar(session_dates: Sequence[date], accepted_at: datetime) -> Optional[Tuple[int, str]]:
    """(index into `session_dates`, timing) of the first bar that fully reflects the filing, or None when the filing is after the last known session. `session_dates` must be sorted."""
    sessions = frozenset(session_dates)
    timing = F.filing_session_timing(accepted_at, sessions)
    d = accepted_at.astimezone(F.NY).date()
    if timing in ("BMO", "INTRADAY"):
        i = bisect_left(session_dates, d)                    # that session itself (it is in `sessions` by construction of the timing)
    else:                                                    # AMC or a non-session day: the next session
        i = bisect_right(session_dates, d)
    return (i, timing) if i < len(session_dates) else None


def match_release(period_end: date, periodic_filed: date, eight_ks: Sequence[F.FilingFact]) -> Optional[F.FilingFact]:
    """The 8-K that announced the period's results: an 8-K listing Item 2.02, filed after the period end and no later than the periodic report, within
    RELEASE_MAX_DAYS_AFTER_PERIOD of the period end. When several qualify, the one closest to the periodic report (the final results release)."""
    cands = [k for k in eight_ks if k.form_type == "8-K" and "2.02" in k.items and period_end < k.filing_date <= periodic_filed
             and (k.filing_date - period_end).days <= RELEASE_MAX_DAYS_AFTER_PERIOD]
    return max(cands, key=lambda k: (k.accepted_at, k.accession)) if cands else None


def filing_date_consistent(fact: F.FilingFact) -> bool:
    """EDGAR assigns the next business day to a filing accepted after 17:30 ET; otherwise the filing date is the Eastern acceptance date. A mismatch beyond that is a data-integrity flag."""
    local = fact.accepted_at.astimezone(F.NY)
    if fact.filing_date == local.date():
        return True
    return local.hour * 60 + local.minute >= 17 * 60 + 30 or local.weekday() >= 5 and (fact.filing_date - local.date()).days <= 3
