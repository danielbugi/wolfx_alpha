"""Read-only database inputs for the Market Intelligence runner. Plain cursors on a caller-supplied connection; no driver import, no
write, no clock. Every read is bounded by an explicit session date: nothing here knows what "today" is.

Sector map and point-in-time evidence
-------------------------------------
`daily_fundamentals.sector` is a yfinance `info` field. The audit of the development copy of the table (see docs/architecture/
MARKET_INTELLIGENCE.md, "Sector PIT audit") found that 96% of its rows were created in ONE bulk load whose `created_at` is long after the
row's `date`: those rows are today's classification projected backwards, not what was known then. Only rows whose own write time is on or
before the session carry evidence of what was known by it. Two rules are therefore offered:

    pit_evidenced   latest row per symbol with date <= session AND greatest(created_at, updated_at)::date <= session.
                    A symbol with no such row is UNCLASSIFIED (never given another symbol's, a later row's or an 'Unknown' sector).
    projected       latest row per symbol with date <= session, whatever its write time. Full coverage, but the sector is today's metadata
                    projected backwards: always `reconstructed`, never PIT-safe.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import pandas as pd

NY = ZoneInfo("America/New_York")
LOOKBACK_CALENDAR_DAYS = 420        # 252 sessions + the 200-bar minimum + holidays, with margin
INDEX_SYMBOLS = ("^GSPC", "^VIX", "^RUT")
RULE_PIT_EVIDENCED, RULE_PROJECTED = "pit_evidenced", "projected"
RULES = (RULE_PIT_EVIDENCED, RULE_PROJECTED)


def window_start(session_date: date) -> date:
    return session_date - timedelta(days=LOOKBACK_CALENDAR_DAYS)


def load_price_panel(conn, session_date: date) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """(close, high, low): date x symbol float matrices for [session - lookback, session]. Nothing after the session is read."""
    cur = conn.cursor()
    cur.execute("SELECT symbol, date, high, low, close FROM stock_prices WHERE date BETWEEN %s AND %s",
                (window_start(session_date), session_date))
    rows = cur.fetchall()
    conn.commit()
    if not rows:
        return None, None, None
    df = pd.DataFrame(rows, columns=["symbol", "date", "high", "low", "close"])
    df["date"] = pd.to_datetime(df["date"])
    out = []
    for col in ("close", "high", "low"):
        m = df.pivot(index="date", columns="symbol", values=col).sort_index().astype(float)
        out.append(m)
    return out[0], out[1], out[2]


def load_index_closes(conn, session_date: date, symbols: Iterable[str] = INDEX_SYMBOLS) -> Dict[str, pd.Series]:
    cur = conn.cursor()
    cur.execute("SELECT symbol, date, close FROM market_index_prices WHERE symbol = ANY(%s) AND date BETWEEN %s AND %s AND close IS NOT NULL",
                (list(symbols), window_start(session_date), session_date))
    rows = cur.fetchall()
    conn.commit()
    out: Dict[str, List[Tuple[Any, float]]] = {}
    for sym, d, c in rows:
        out.setdefault(sym, []).append((pd.Timestamp(d), float(c)))
    return {s: pd.Series({d: c for d, c in sorted(v)}, dtype=float) for s, v in out.items()}


def knowledge_cutoff(session_date: date) -> datetime:
    """The instant after which a discontinuity detection is retroactive information for an `observed` run of `session_date`: noon New York time on the
    calendar day AFTER the session (an aware datetime). The overnight cycle that loads the session (pipeline, ML dataset rebuild, both collector fires) is
    over by then, and the next cycle starts hours later (>= 20:45 UTC), so a detection stamped inside that window was known to the run that wrote the
    observation, and anything stamped after it was not. The cutoff is derived from the session only; it never reads a clock."""
    return datetime.combine(session_date + timedelta(days=1), time(12, 0), NY).astimezone(timezone.utc)


def load_discontinuities(conn, session_date: date) -> Tuple[Dict[str, List[date]], int, int]:
    """({symbol: [event dates]}, late, untrusted).

    `price_discontinuities.date` is when the discontinuity HAPPENED; `detected_at` is the first time THIS SYSTEM saw it (the dataset builder keeps it
    across rebuilds and only moves it for a genuinely new or materially changed row). Only `detected_at` says what a live run could have known:
      late       rows detected after `knowledge_cutoff(session)`: retroactive information a live run cannot have had.
      untrusted  rows whose detection time cannot be believed: missing, or in the future of the database clock. They count against an `observed` run.
    A stamp is never EARLIER than the true first detection (rebuilds before the provenance fix re-stamped every row at the rebuild, which is a late
    UPPER bound on it, not a fabricated earlier time), so a row that passes the cutoff test is genuinely known by then; an ambiguous legacy row can only err toward 'late'.
    The naive `detected_at` is read in the database session's own TimeZone, exactly how the writer's `clock_timestamp()` stored it."""
    cur = conn.cursor()
    cur.execute(
        "SELECT symbol, date, CASE WHEN detected_at IS NULL THEN 'untrusted' "
        "WHEN (detected_at AT TIME ZONE current_setting('TimeZone')) > clock_timestamp() THEN 'untrusted' "
        "WHEN (detected_at AT TIME ZONE current_setting('TimeZone')) > %s THEN 'late' ELSE 'ok' END "
        "FROM price_discontinuities WHERE date BETWEEN %s AND %s",
        (knowledge_cutoff(session_date), window_start(session_date), session_date))
    rows = cur.fetchall()
    conn.commit()
    out: Dict[str, List[date]] = {}
    late = untrusted = 0
    for sym, d, verdict in rows:
        out.setdefault(sym, []).append(d)
        late += verdict == "late"
        untrusted += verdict == "untrusted"
    return out, late, untrusted


# Reasons an `observed` run can be refused for lack of scan evidence (migration 32). Each is a distinct, coded fact; none is ever downgraded to success.
SCAN_OK = "ok"
SCAN_UNAVAILABLE = "scan_evidence_unavailable"        # migration 32 (table / fingerprint functions) not applied: no evidence can exist
SCAN_PRICES_NOT_AT_SESSION = "price_data_not_at_session"
SCAN_MISSING = "scan_missing"                         # no scan row for this session at all (the step was skipped, or the pipeline did not pass --session)
SCAN_FAILED = "scan_failed"                           # only failed scans exist for this session
SCAN_INPUT_MISMATCH = "scan_input_mismatch"           # prices differ from the input of every completed scan (changed after it, or another price version)
SCAN_RESULT_MISMATCH = "scan_result_mismatch"         # price_discontinuities is no longer what the matching scan produced
SCAN_AFTER_CUTOFF = "scan_after_cutoff"               # the matching scan completed after the session's knowledge cutoff


def discontinuity_scan_evidence(conn, session_date: date) -> Dict[str, Any]:
    """Read-only. Does the database hold a COMPLETE price-discontinuity scan that is evidence for an `observed` run of `session_date`?

    All of these must hold at once: the session's own bar is the newest stored price bar; a complete scan exists FOR THIS SESSION whose price-input fingerprint equals
    the fingerprint of the prices as stored NOW (so a scan of another session, of an earlier price version, or of a partial load never counts); the scan's
    result fingerprint equals the price_discontinuities table as stored NOW; and the earliest such scan completed (database clock) no later than
    `knowledge_cutoff(session)`. Returns {'ok', 'reason', 'detail'}; `reason` is one of the SCAN_* codes."""
    cutoff = knowledge_cutoff(session_date)
    cur = conn.cursor()
    try:
        cur.execute("SELECT newest_bar, n_symbols, n_price_rows, fingerprint FROM research_price_input_fingerprint(%s)", (session_date,))
        newest, n_symbols, n_rows, input_fp = cur.fetchone()
        cur.execute("SELECT n_rows, fingerprint FROM research_discontinuity_result_fingerprint()")
        n_found, result_fp = cur.fetchone()
        cur.execute("SELECT max(date) FROM stock_prices")                          # the detector reads the whole table: a later bar means the scan is not of this session
        newest = cur.fetchone()[0]
        cur.execute("SELECT id, status, failure_reason, input_fingerprint, result_fingerprint, finished_at FROM price_discontinuity_scan "
                    "WHERE session_date = %s ORDER BY id", (session_date,))
        scans = cur.fetchall()
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        if getattr(e, "pgcode", None) in ("42P01", "42883"):                      # undefined_table / undefined_function
            return {"ok": False, "reason": SCAN_UNAVAILABLE, "detail": {"hint": "migration 32 is not applied"}}
        raise
    detail: Dict[str, Any] = {"session": session_date.isoformat(), "cutoff": cutoff.isoformat(), "newest_price_bar": newest.isoformat() if newest else None,
                              "current_input_fingerprint": input_fp, "current_result_fingerprint": result_fp, "scans_for_session": len(scans)}

    def verdict(reason: str, **extra: Any) -> Dict[str, Any]:
        for key, value in extra.items():
            detail[key] = value
        return {"ok": reason == SCAN_OK, "reason": reason, "detail": detail}

    if newest != session_date:
        return verdict(SCAN_PRICES_NOT_AT_SESSION)
    if not scans:
        return verdict(SCAN_MISSING)
    complete = [r for r in scans if r[1] == "complete"]
    if not complete:
        return verdict(SCAN_FAILED, failure_reasons=sorted({r[2] for r in scans if r[2]}))
    same_input = [r for r in complete if r[3] == input_fp]
    if not same_input:
        return verdict(SCAN_INPUT_MISMATCH, scan_input_fingerprints=sorted({r[3] for r in complete}))
    matching = [r for r in same_input if r[4] == result_fp]
    if not matching:
        return verdict(SCAN_RESULT_MISMATCH, scan_result_fingerprints=sorted({r[4] for r in same_input}))
    first = min(matching, key=lambda r: (r[5], r[0]))
    if first[5] > cutoff:
        return verdict(SCAN_AFTER_CUTOFF, scan_id=first[0], scan_finished_at=first[5].isoformat())
    return verdict(SCAN_OK, scan_id=first[0], scan_finished_at=first[5].isoformat(), n_discontinuities=n_found, n_symbols=n_symbols, n_price_rows=n_rows)


def load_sector_map(conn, session_date: date, rule: str) -> Tuple[Dict[str, Optional[str]], Dict[str, Any]]:
    if rule not in RULES:
        raise ValueError(f"rule must be one of {RULES}")
    cur = conn.cursor()
    known = ("AND greatest(created_at, coalesce(updated_at, created_at))::date <= %(t)s" if rule == RULE_PIT_EVIDENCED else "")
    cur.execute(
        "SELECT DISTINCT ON (symbol) symbol, sector, date, greatest(created_at, coalesce(updated_at, created_at))::date "
        f"FROM daily_fundamentals WHERE date <= %(t)s {known} ORDER BY symbol, date DESC", {"t": session_date})
    rows = cur.fetchall()
    cur.execute("SELECT count(DISTINCT symbol) FROM daily_fundamentals WHERE date <= %s", (session_date,))
    n_with_rows = cur.fetchone()[0]
    conn.commit()
    smap = {sym: sector for sym, sector, _, _ in rows}
    projected = sum(1 for _, _, _, written in rows if written is None or written > session_date)
    audit = {
        "rule": rule, "source": "daily_fundamentals.sector", "n_symbols_with_rows": int(n_with_rows), "n_in_map": len(smap),
        "n_classified": sum(1 for v in smap.values() if v and str(v).strip() and str(v).strip().lower() != "unknown"),
        "n_without_evidence": int(n_with_rows) - len(smap) if rule == RULE_PIT_EVIDENCED else 0,
        "n_projected_backwards": projected,
        "pit_evidenced": projected == 0 and rule == RULE_PIT_EVIDENCED,
        "latest_row_date": max((d for _, _, d, _ in rows), default=None),
    }
    if audit["latest_row_date"] is not None:
        audit["latest_row_date"] = audit["latest_row_date"].isoformat()
    return smap, audit


def latest_stored_dates(conn) -> Dict[str, Optional[date]]:
    """The newest stock and index bar in the database. Used only to decide whether a requested session is the latest one (a run for an older
    session cannot be a live capture); it is a property of the data, not of the wall clock."""
    cur = conn.cursor()
    cur.execute("SELECT (SELECT max(date) FROM stock_prices), (SELECT max(date) FROM market_index_prices WHERE symbol = ANY(%s))",
                (list(INDEX_SYMBOLS),))
    s, i = cur.fetchone()
    conn.commit()
    return {"stock_prices": s, "market_index_prices": i}
