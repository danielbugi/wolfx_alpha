"""Forward paper-tracker runner: process ONE explicit trading session for all five registered variants (research only).

    PYTHONPATH=mechanism python -m research.paper_runner dry-run --session 2026-10-09 [--scope triggered|tracked]     read-only: computes everything, writes nothing, prints the summary
    PYTHONPATH=mechanism python -m research.paper_runner verify                                                      read-only audit of the stored evidence (hashes, chains, order, cursor)
    PYTHONPATH=mechanism python -m research.paper_runner run --next --confirm-production trading_production --arming-file F --code-ref <12-hex pin>
    PYTHONPATH=mechanism python -m research.paper_runner run --session S --activation-session ...                    (the FIRST session ever; explicit, no backfill)
                                                                                                                      WRITES; refuses unless armed for exactly this code ref and confirmed (NOT activated; see docs)
    exit codes: 0 processed / already processed | 2 input not ready yet (retry later) | 4 refused (gap, scope change, registry mismatch, ...) | 5 not armed | 6 up to date | 7 usage | 8 database connection failed   (3 = the wrapper's lock)
    <db> = --host H --dbname D --user U [--port P] [--password-env VAR]

What a session does (all in ONE transaction; nothing commits unless every step succeeded):
  1. checks the registry: all five registered variants present, hashes recomputed and equal to the code's;
  2. continues from the newest stored session snapshot (hash re-verified), refusing a gap: the session must be the next trading session after the last processed one (the first session ever needs
     --activation-session, an explicit decision; there is no backfill);
  3. refuses unless the input is complete (a finished candidate capture for the session, bars loaded for it);
  4. advances every open/pending observation with the session's bars, then registers the session's signals for every variant (one observation per variant, unless that variant already follows the
     symbol/direction), with the decision-time context as it was AVAILABLE (every item carries its own availability timestamp; anything not provably available at the cutoff is excluded);
  5. appends the observations and events, then the session cursor row (with the open-state snapshot), through `research.paper_store`;
  6. appends HINDSIGHT events (excursions, capture, missed continuation, big-winner tier) for closed positions whose later bars now exist; these are flagged `decision_time_information: false`.
It opens no order, calls no broker, sends nothing, and writes only the three migration-36 tables.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from research import exit_policies as X
from research import opportunity as OP
from research import paper_store as ST
from research import paper_tracker as PT

SCOPES = ("triggered", "tracked")
MIN_SYMBOLS_FOR_A_SESSION = 1000
HIST_BARS = max(X.channel_length(p) for p in X.POLICIES)
FEATURE_KEYS = ("atr_pct", "vol_ratio_10", "vol_ratio_50", "ret_5d", "ret_20d", "ret_60d", "dist_sma50_pct", "dist_sma200_pct", "pct_from_52w_high", "pct_from_52w_low", "gap_pct", "bar_range_atr",
                "close_location", "chan_squeeze_pctile", "dollar_vol_20")
CATALYST_DAYS = 15
HINDSIGHT_ENTRY_BARS = max(OP.HORIZONS)
HINDSIGHT_EXIT_BARS = OP.PREMATURE_BARS


class RunnerError(RuntimeError):
    pass


class SessionRefused(RunnerError):
    """A precondition failed; nothing was written."""


class InputNotReady(SessionRefused):
    """The session exists but its input is not complete yet (a later attempt may succeed); nothing was written."""


EXIT_OK, EXIT_NOT_READY, EXIT_REFUSED, EXIT_NOT_ARMED, EXIT_UP_TO_DATE, EXIT_USAGE, EXIT_DB = 0, 2, 4, 5, 6, 7, 8   # 3 is the wrapper's own (lock held); no runner failure may share a status the unit treats as success


@dataclass
class Signal:
    symbol: str
    direction: int
    atr: float
    hist_h: List[float]
    hist_l: List[float]
    context: Dict[str, Any]
    candidate_observation_id: Optional[int] = None


@dataclass
class SessionResult:
    session_date: date
    status: str                                   # processed | already_processed | dry_run
    counts: Dict[str, int] = field(default_factory=dict)
    written: Dict[str, int] = field(default_factory=dict)
    skipped: Dict[str, int] = field(default_factory=dict)
    open_state_hash: Optional[str] = None
    events: List[Dict[str, Any]] = field(default_factory=list)

    def summary(self) -> Dict[str, Any]:
        by_type: Dict[str, int] = {}
        for e in self.events:
            by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        return {"session": self.session_date.isoformat(), "status": self.status, "counts": self.counts, "written": self.written, "skipped": self.skipped, "events_by_type": by_type,
                "open_state_hash": self.open_state_hash}


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ market data
def session_calendar(cur, since: date, min_symbols: int = MIN_SYMBOLS_FOR_A_SESSION) -> List[date]:
    """Trading sessions = dates on which at least `min_symbols` symbols have a bar (the same notion of a loaded session the pipeline uses)."""
    cur.execute("SELECT date FROM stock_prices WHERE date >= %s GROUP BY date HAVING count(*) >= %s ORDER BY date", (since, min_symbols))
    return [r[0] for r in cur.fetchall()]


def bars_for(cur, session_date: date, symbols: Sequence[str]) -> Dict[str, Tuple[float, float, float, float]]:
    if not symbols:
        return {}
    cur.execute("SELECT symbol, open, high, low, close FROM stock_prices WHERE date = %s AND symbol = ANY(%s)", (session_date, list(symbols)))
    out = {}
    for s, o, h, l, c in cur.fetchall():
        vals = [float(x) if x is not None else float("nan") for x in (o, h, l, c)]
        if all(math.isfinite(v) for v in vals):
            out[s] = tuple(vals)
    return out


def hist_for(cur, session_date: date, symbols: Sequence[str], n: int = HIST_BARS) -> Dict[str, Tuple[List[float], List[float]]]:
    """The last `n` bars up to and including the signal session, per symbol (what the channel policies look at)."""
    if not symbols:
        return {}
    cur.execute("SELECT symbol, high, low FROM (SELECT symbol, date, high, low, row_number() OVER (PARTITION BY symbol ORDER BY date DESC) rn FROM stock_prices "
                "WHERE symbol = ANY(%s) AND date <= %s AND date > %s) t WHERE rn <= %s ORDER BY symbol, date", (list(symbols), session_date, session_date - timedelta(days=90), n))
    out: Dict[str, Tuple[List[float], List[float]]] = {}
    for s, h, l in cur.fetchall():
        hh, ll = out.setdefault(s, ([], []))
        hh.append(float(h))
        ll.append(float(l))
    return out


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ signal sources
def _jsonable(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if hasattr(v, "__float__") and not isinstance(v, (int, float, bool, str)):
        f = float(v)
        return f if math.isfinite(f) else None
    return v


def _try(cur, sql: str, params: Sequence[Any]) -> Optional[List[tuple]]:
    """Run a read query for an OPTIONAL context section; a missing table or column makes the section unavailable, never the session fail."""
    cur.execute("SAVEPOINT paper_ctx")
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.execute("RELEASE SAVEPOINT paper_ctx")
        return rows
    except Exception:  # noqa: BLE001
        cur.execute("ROLLBACK TO SAVEPOINT paper_ctx")
        return None


class CandidateSource:
    """The real forward source: the session's `candidate_observation` rows (the screener's own captured breakouts) with their `feature_snapshot`, plus decision-time context from the observed
    market / relative-strength / catalyst tables. Everything is filtered to what was available at `cutoff` and carries its own availability timestamp."""
    name = "candidate_observation"

    def ready(self, cur, session_date: date) -> Tuple[bool, str]:
        cur.execute("SELECT count(*) FROM candidate_capture_run r JOIN strategies s ON s.id = r.strategy_id WHERE s.strategy_key = 'donchian_breakout' AND s.strategy_version = 'v1' "
                    "AND r.session_date = %s AND r.status = 'complete'", (session_date,))
        n = cur.fetchone()[0]
        return (n > 0, "a complete candidate capture exists" if n > 0 else f"no complete candidate capture run for {session_date}")

    def signals(self, cur, session_date: date, scope: str, cutoff: datetime) -> Tuple[List[Signal], Dict[str, int]]:
        cur.execute("SELECT c.id, c.symbol, c.direction, c.signal_type, c.passed_guard, c.guard_reasons, c.alignment_score, c.quality_grade, c.combined_score, c.session_rank, c.ml_status, c.ml_score, "
                    "c.ml_model_version, c.tracked_intent, c.atr_source, c.breakout_dist_atr, c.distance_to_channel_pct, c.entry_close, f.id, f.sector, f.sector_asof, f.captured_at, f.features "
                    "FROM candidate_observation c JOIN strategies s ON s.id = c.strategy_id AND s.strategy_key = 'donchian_breakout' AND s.strategy_version = 'v1' "
                    "JOIN feature_snapshot f ON f.id = c.snapshot_id WHERE c.session_date = %s AND c.triggered AND (%s = 'triggered' OR c.tracked_intent) ORDER BY c.symbol, c.direction", (session_date, scope))
        rows = cur.fetchall()
        skipped: Dict[str, int] = {}
        symbols = sorted({r[1] for r in rows})
        hist = hist_for(cur, session_date, symbols)
        rs = self._relative_strength(cur, session_date, symbols, cutoff)
        cat = self._catalysts(cur, session_date, symbols, cutoff)
        regime = self._regime(cur, session_date, cutoff)
        out: List[Signal] = []
        for r in rows:
            (cid, sym, d, stype, passed, reasons, align, grade, comb, rank, mlst, mls, mlv, tracked, atr_src, bdist, dist, close, fid, sector, sector_asof, cap_at, feats) = r
            atr = (feats or {}).get("atr_14")
            if atr_src != "measured" or atr is None or not math.isfinite(float(atr)) or float(atr) <= 0:
                skipped["atr_not_measured"] = skipped.get("atr_not_measured", 0) + 1
                continue
            hh, ll = hist.get(sym, ([], []))
            if not hh:
                skipped["no_bars_for_channel"] = skipped.get("no_bars_for_channel", 0) + 1
                continue
            ctx = {"decision_cutoff": cutoff.isoformat(), "session": session_date.isoformat(),
                   "candidate": {"candidate_observation_id": cid, "signal_type": stype, "passed_guard": passed, "guard_reasons": reasons, "alignment_score": _jsonable(align), "quality_grade": grade,
                                 "combined_score": _jsonable(comb), "session_rank": rank, "ml_status": mlst, "ml_score": _jsonable(mls), "ml_model_version": mlv, "tracked_intent": tracked,
                                 "atr_source": atr_src, "breakout_dist_atr": _jsonable(bdist), "distance_to_channel_pct": _jsonable(dist), "entry_close": _jsonable(close)},
                   "features": {"snapshot_id": fid, "available_at": _jsonable(cap_at), "sector": sector, "sector_asof": _jsonable(sector_asof), **{k: _jsonable((feats or {}).get(k)) for k in FEATURE_KEYS}},
                   "market_regime": regime, "relative_strength": rs.get(sym), "catalysts": cat.get(sym, {"events": [], "section": "none_in_window"})}
            out.append(Signal(sym, int(d), float(atr), hh, ll, json.loads(json.dumps(ctx, allow_nan=False)), cid))
        return out, skipped

    def _regime(self, cur, session_date: date, cutoff: datetime) -> Dict[str, Any]:
        rows = _try(cur, "SELECT regime_state, regime_score, regime_strength_label, provenance, created_at FROM market_snapshot WHERE session_date = %s AND created_at <= %s "
                         "ORDER BY (provenance = 'observed') DESC, id DESC LIMIT 1", (session_date, cutoff))
        if not rows:
            return {"available": False, "reason": "no market snapshot available at the cutoff" if rows is not None else "market_snapshot not present"}
        s, sc, lab, prov, at = rows[0]
        return {"available": True, "regime_state": s, "regime_score": _jsonable(sc), "regime_strength_label": lab, "provenance": prov, "available_at": _jsonable(at)}

    def _relative_strength(self, cur, session_date: date, symbols: Sequence[str], cutoff: datetime) -> Dict[str, Any]:
        rows = _try(cur, "SELECT symbol, horizon_sessions, rs_percentile, vs_sector_pp, vs_spx_pp, sector, sector_pit_safe, provenance, created_at FROM stock_relative_strength "
                         "WHERE session_date = %s AND symbol = ANY(%s) AND state = 'ok' AND provenance = 'observed' AND created_at <= %s ORDER BY symbol, horizon_sessions", (session_date, list(symbols), cutoff))
        out: Dict[str, Any] = {}
        if rows is None:
            return out
        for sym, h, pct, vs_sec, vs_spx, sector, pit, prov, at in rows:
            out.setdefault(sym, {"available": True, "horizons": []})["horizons"].append({"horizon_sessions": h, "rs_percentile": _jsonable(pct), "vs_sector_pp": _jsonable(vs_sec), "vs_spx_pp": _jsonable(vs_spx),
                                                                                            "sector": sector, "sector_pit_safe": pit, "provenance": prov, "available_at": _jsonable(at)})
        return out

    def _catalysts(self, cur, session_date: date, symbols: Sequence[str], cutoff: datetime) -> Dict[str, Any]:
        rows = _try(cur, "SELECT e.symbol, e.event_type, r.event_time, r.session_timing, r.published_at, r.known_at, r.known_at_basis, r.pit_grade, r.source, r.eps_actual, r.eps_estimate, "
                         "(SELECT cc.label FROM catalyst_classification cc WHERE cc.revision_id = r.id AND cc.classified_at <= %s ORDER BY cc.classified_at DESC LIMIT 1) "
                         "FROM market_event e JOIN LATERAL (SELECT * FROM market_event_revision x WHERE x.event_key = e.event_key AND x.known_at IS NOT NULL AND x.known_at <= %s "
                         "ORDER BY x.revision DESC LIMIT 1) r ON TRUE WHERE e.symbol = ANY(%s) AND r.event_time BETWEEN %s AND %s ORDER BY e.symbol, r.event_time",
                    (cutoff, cutoff, list(symbols), session_date - timedelta(days=CATALYST_DAYS), session_date))
        out: Dict[str, Any] = {}
        if rows is None:
            return out
        for sym, etype, etime, timing, pub, known, basis, grade, src, eps_a, eps_e, label in rows:
            out.setdefault(sym, {"section": "events", "events": []})["events"].append({"event_type": etype, "event_time": _jsonable(etime), "session_timing": timing, "published_at": _jsonable(pub),
                                                                                       "known_at": _jsonable(known), "known_at_basis": basis, "pit_grade": grade, "source": src,
                                                                                       "eps_actual": _jsonable(eps_a), "eps_estimate": _jsonable(eps_e), "classification": label})
        return out


class ListSource:
    """A source of pre-built signals (a shadow run on a scratch database, or a test): session_date -> [Signal]."""
    name = "dataset_replay"

    def __init__(self, by_session: Mapping[date, Sequence[Signal]]):
        self.by_session = dict(by_session)

    def ready(self, cur, session_date: date) -> Tuple[bool, str]:
        return True, "list source"

    def signals(self, cur, session_date: date, scope: str, cutoff: datetime) -> Tuple[List[Signal], Dict[str, int]]:
        return list(self.by_session.get(session_date, [])), {}


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ hindsight enrichment
def hindsight_events(cur, session_date: date, limit: int = 5000) -> List[Dict[str, Any]]:
    """Hindsight events for closed positions whose later bars now exist (>= 250 bars from the entry and >= 60 after the exit, up to this session). Hindsight-only: nothing here is decision-time information."""
    cur.execute("SELECT o.id, v.variant_key, o.symbol, o.direction, o.signal_session, o.atr, c.payload FROM paper_observation o JOIN strategy_variant v ON v.id = o.variant_id "
                "JOIN paper_event c ON c.observation_id = o.id AND c.event_type = 'closed' WHERE NOT EXISTS (SELECT 1 FROM paper_event h WHERE h.observation_id = o.id AND h.event_type = 'hindsight') "
                "AND (c.payload ->> 'entry_session')::date <= %s - 330 ORDER BY o.id LIMIT %s", (session_date, limit))
    pending = cur.fetchall()
    out: List[Dict[str, Any]] = []
    for oid, key, sym, d, sig, atr, closed in pending:
        entry_session = date.fromisoformat(closed["entry_session"])
        cur.execute("SELECT open, high, low, close FROM stock_prices WHERE symbol = %s AND date >= %s AND date <= %s ORDER BY date", (sym, entry_session, session_date))
        rows = cur.fetchall()
        a = np.array([[float(x) if x is not None else float("nan") for x in r] for r in rows], dtype=float)
        if len(a) < HINDSIGHT_ENTRY_BARS or len(a) - int(closed["bars_held"]) < HINDSIGHT_EXIT_BARS:
            continue
        payload = OP.hindsight_payload(int(d), float(closed["entry_price"]), float(closed["exit_price"]), float(atr), int(closed["bars_held"]), a[:, 0], a[:, 1], a[:, 2], a[:, 3], 0, int(closed["bars_held"]) - 1)
        payload["computed_from_session"] = entry_session.isoformat()
        payload["computed_through_session"] = session_date.isoformat()
        out.append({"type": "hindsight", "ident": f"{key}|{sym}|{d}|{sig.isoformat()}", "session": session_date.isoformat(), "payload": json.loads(json.dumps(payload, allow_nan=False))})
    return out


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ the session
def process_session(cur, session_date: date, source, *, scope: str = "triggered", write: bool, run_id: Optional[str] = None, writer: str = "paper_runner", code_ref: str = "unknown",
                    activation_session: bool = False, calendar: Optional[Sequence[date]] = None, enrich: bool = True) -> SessionResult:
    """Process one session end to end. `write=False` computes the same events and returns them without touching any table (dry run). The caller owns the transaction."""
    if scope not in SCOPES:
        raise SessionRefused(f"unknown scope {scope!r}")
    variants, variant_ids = ST.load_variants(cur)
    last = ST.latest_session(cur)
    if last is not None and session_date <= last["session_date"]:
        return SessionResult(session_date, "already_processed")
    cal = list(calendar) if calendar is not None else session_calendar(cur, (last["session_date"] if last else session_date) - timedelta(days=1))
    if session_date not in cal:
        raise SessionRefused(f"{session_date} is not a loaded trading session (fewer than {MIN_SYMBOLS_FOR_A_SESSION} symbols have a bar)")
    if last is None:
        if not activation_session:
            raise SessionRefused("nothing has ever been processed: the first session needs --activation-session (an explicit decision; there is no backfill)")
        book = PT.PaperBook(variants)
    else:
        if last["scope"] != scope or last["source"] != source.name:
            raise SessionRefused(f"scope/source changed since the last session ({last['scope']}/{last['source']} -> {scope}/{source.name}): refusing to mix them")
        prev_idx = cal.index(last["session_date"]) if last["session_date"] in cal else None
        if prev_idx is None or prev_idx + 1 >= len(cal) or cal[prev_idx + 1] != session_date:
            raise SessionRefused(f"{session_date} is not the trading session right after the last processed session {last['session_date']} (a gap: process the missing sessions in order)")
        book = PT.PaperBook.from_open_state(variants, last["open_state"])
        if book.open_state_hash() != last["open_state_hash"]:
            raise SessionRefused("the restored open state does not reproduce its stored hash")
    ok, why = source.ready(cur, session_date)
    if not ok:
        raise InputNotReady(f"input not ready: {why}")
    cur.execute("SELECT clock_timestamp()")
    cutoff = cur.fetchone()[0]
    started_at = cutoff
    open_symbols = sorted({o.symbol for o in book.open_observations()})
    bars = bars_for(cur, session_date, open_symbols)
    book.on_session(session_date, bars)
    signals, skipped = source.signals(cur, session_date, scope, cutoff)
    skipped_open = registered = 0
    cand_ids: Dict[str, Optional[int]] = {}
    for sg in signals:
        res = book.register_signal(sg.symbol, sg.direction, session_date, sg.atr, sg.hist_h, sg.hist_l, sg.context)
        cand_ids[f"{sg.symbol}|{1 if sg.direction > 0 else -1}"] = sg.candidate_observation_id
        for outcome in res.values():
            registered += outcome == "registered"
            skipped_open += outcome != "registered"
    events = book.drain_events()
    if enrich:
        events += hindsight_events(cur, session_date)
    by = lambda t: sum(1 for e in events if e["type"] == t)  # noqa: E731
    counts = {"n_variants": len(variants), "n_candidates": len(signals), "n_registered": registered, "n_skipped_open": skipped_open, "n_entered": by("entered"), "n_closed": by("closed"),
              "n_not_enterable": by("not_enterable"), "n_hindsight": by("hindsight"), "n_open_after": len(book.open_observations())}
    state = book.open_state()
    result = SessionResult(session_date, "dry_run" if not write else "processed", counts, {}, skipped, ST.sha(state), events)
    if write:
        result.written = ST.write_paper_session(cur, session_date=session_date, scope=scope, source=source.name, variant_ids=variant_ids, events=events, counts=counts, open_state=state,
                                          run_id=run_id or uuid.uuid4().hex, writer=writer, code_ref=code_ref, started_at=started_at, candidate_ids=cand_ids)
    return result


def resolve_next(cur, calendar: Optional[Sequence[date]] = None) -> Tuple[Optional[date], str]:
    """The next session a timer should process: the trading session right after the newest processed one. (None, reason) when it is not loaded yet (the run is up to date) or when nothing has ever been
    processed (the first session must be named explicitly with --session --activation-session: no implicit backfill)."""
    last = ST.latest_session(cur)
    if last is None:
        return None, "nothing has ever been processed: name the first session explicitly (--session S --activation-session)"
    cal = list(calendar) if calendar is not None else session_calendar(cur, last["session_date"])
    later = [d for d in cal if d > last["session_date"]]
    if not later:
        return None, f"up to date: no trading session after {last['session_date']} is loaded yet"
    return later[0], f"the session after {last['session_date']}"


# ------------------------------------------------------------------------------------------------------------------------------------------------------------ CLI
def _connect(a, readonly: bool):
    import psycopg2
    pw = os.environ.get(a.password_env) if a.password_env else os.environ.get("DB_PASSWORD")
    c = psycopg2.connect(host=a.host, port=a.port, dbname=a.dbname, user=a.user, password=pw)
    c.set_session(readonly=readonly, autocommit=False)
    return c


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="research.paper_runner")
    p.add_argument("command", choices=["dry-run", "verify", "run"])
    p.add_argument("--session")
    p.add_argument("--scope", default="triggered", choices=SCOPES)
    p.add_argument("--next", action="store_true", help="process the trading session right after the newest processed one (what a timer runs)")
    p.add_argument("--host", default=os.environ.get("DB_HOST")), p.add_argument("--dbname", default=os.environ.get("DB_NAME")), p.add_argument("--user", default=os.environ.get("DB_USER"))
    p.add_argument("--port", default=os.environ.get("DB_PORT", "5432"))
    p.add_argument("--password-env", default=None)
    p.add_argument("--confirm-production", default=None)
    p.add_argument("--arming-file", default=None)
    p.add_argument("--activation-session", action="store_true")
    p.add_argument("--code-ref", default=os.environ.get("CODE_REF", "unknown"))
    a = p.parse_args(argv)
    if not (a.host and a.dbname and a.user):
        print("usage: --host --dbname --user are required", file=sys.stderr)
        return EXIT_USAGE
    if a.command in ("dry-run", "run") and not (a.session or a.next):
        print("--session or --next is required", file=sys.stderr)
        return EXIT_USAGE
    if a.session and a.next:
        print("--session and --next are mutually exclusive", file=sys.stderr)
        return EXIT_USAGE
    if a.command == "run":
        if a.confirm_production != a.dbname:
            print("refused: --confirm-production must equal --dbname", file=sys.stderr)
            return EXIT_NOT_ARMED
        if not a.arming_file or not os.path.isfile(a.arming_file):
            print("refused: the paper tracker is not armed (arming file missing); activation needs the owner's separate approval", file=sys.stderr)
            return EXIT_NOT_ARMED
        with open(a.arming_file, encoding="utf-8") as fh:
            armed_for = fh.read().strip()
        if armed_for != a.code_ref:
            print(f"refused: armed for {armed_for!r}, but this run's code ref is {a.code_ref!r} (re-pinning disarms until re-armed)", file=sys.stderr)
            return EXIT_NOT_ARMED
    try:
        conn = _connect(a, readonly=a.command != "run")
    except Exception as e:  # noqa: BLE001
        print(f"database connection failed: {type(e).__name__}", file=sys.stderr)
        return EXIT_DB
    try:
        cur = conn.cursor()
        if a.command == "verify":
            problems = ST.verify(cur)
            for x in problems:
                print("PROBLEM", x)
            print("OK" if not problems else f"{len(problems)} problem(s)")
            return 0 if not problems else 1
        if a.next:
            target, why = resolve_next(cur)
            if target is None:
                conn.rollback()
                print(json.dumps({"status": "nothing_to_do", "reason": why}))
                return EXIT_UP_TO_DATE if why.startswith("up to date") else EXIT_REFUSED
        else:
            target = date.fromisoformat(a.session)
        try:
            res = process_session(cur, target, CandidateSource(), scope=a.scope, write=a.command == "run", code_ref=a.code_ref, activation_session=a.activation_session)
        except InputNotReady as e:
            conn.rollback()
            print(f"not ready: {e}", file=sys.stderr)
            return EXIT_NOT_READY
        except (SessionRefused, ST.StoreError) as e:
            conn.rollback()
            print(f"refused: {e}", file=sys.stderr)
            return EXIT_REFUSED
        if a.command == "run":
            conn.commit()
        else:
            conn.rollback()
        print(json.dumps(res.summary(), indent=1))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
