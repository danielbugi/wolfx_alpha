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

from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import pandas as pd

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


def load_discontinuities(conn, session_date: date) -> Tuple[Dict[str, List[date]], int]:
    """({symbol: [dates]}, number of those rows detected AFTER the session). The second number is retroactive information a live run
    cannot have had; a non-zero value on a run claimed as observed is a contradiction the runner refuses."""
    cur = conn.cursor()
    cur.execute("SELECT symbol, date, (detected_at::date > %s) FROM price_discontinuities WHERE date BETWEEN %s AND %s",
                (session_date, window_start(session_date), session_date))
    rows = cur.fetchall()
    conn.commit()
    out: Dict[str, List[date]] = {}
    late = 0
    for sym, d, after in rows:
        out.setdefault(sym, []).append(d)
        late += bool(after)
    return out, late


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
