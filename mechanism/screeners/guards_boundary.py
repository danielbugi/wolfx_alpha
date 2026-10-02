"""GUARDS_EFFECTIVE_FROM -- the explicit, session-dated boundary at which the screener's universe guards start to apply.

Why it exists: the guards (screeners/universe_guards.py) change which breakouts enter the signal ledger (~4-6% fewer).
Deploying a new mechanism image must NOT silently change the ledger population mid-history, so the guards are gated by
an operator-set session date instead of by "the image that contains them":

  * unset / empty          guards are INERT -- the screener behaves exactly as it did before the guards existed.
  * YYYY-MM-DD (= D)       guards apply to every session >= D and to no session < D.

The decision depends only on (D, the session being screened). It never reads the wall clock, the image pin time or
the newest bar in the database, so re-running a historical session reproduces the same decision. A malformed value is
a configuration error and raises -- it is never read as "unset", because that would silently turn the guards off.

Stdlib only: this must stay importable when the guard implementation itself (pandas / ML features) fails to import,
so the screener can still tell "guards not requested" apart from "guards requested but unavailable".
"""
from __future__ import annotations

import os
import re
from datetime import date
from typing import Mapping, Optional

ENV_VAR = "GUARDS_EFFECTIVE_FROM"
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class GuardsBoundaryError(ValueError):
    """GUARDS_EFFECTIVE_FROM is set to something that is not a real YYYY-MM-DD date."""


def parse_effective_from(raw: Optional[str]) -> Optional[date]:
    """None for unset/blank; the date for a valid ISO value; GuardsBoundaryError for anything else."""
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if not _ISO_DATE.match(value):
        raise GuardsBoundaryError(f"{ENV_VAR}={value!r} is not YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise GuardsBoundaryError(f"{ENV_VAR}={value!r} is not a real calendar date") from e


def effective_from(env: Optional[Mapping[str, str]] = None) -> Optional[date]:
    return parse_effective_from((os.environ if env is None else env).get(ENV_VAR))


def guards_apply(session: date, boundary: Optional[date]) -> bool:
    """True iff the guards apply to `session`: a boundary is set and the session is on or after it."""
    return boundary is not None and session >= boundary


def describe(session: date, boundary: Optional[date]) -> str:
    """The one stable log line (the activation runbook's validation greps for `Universe guards mode:`)."""
    if boundary is None:
        return f"Universe guards mode: INERT (session {session}; {ENV_VAR} is unset)"
    state = "ACTIVE" if guards_apply(session, boundary) else "INERT"
    cmp = ">=" if state == "ACTIVE" else "<"
    return f"Universe guards mode: {state} (session {session} {cmp} {ENV_VAR}={boundary})"
