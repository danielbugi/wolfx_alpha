"""fwd_v1 runner. Not scheduled anywhere: no timer, no importer in the live pipeline. Session-explicit (`as_of_session` is a
required argument, never derived from the clock), dry-run by default, deterministic and idempotent (the label table's UNIQUE
key + ON CONFLICT DO NOTHING make a re-run, or two concurrent runs, write each label exactly once).

    python -m research.labels.runner --as-of 2026-10-02            # dry run: what WOULD be written
    python -m research.labels.runner --as-of 2026-10-02 --apply    # writes (needs INSERT on forward_return_label)
    python -m research.labels.runner --as-of 2026-10-02 --restatements   # read-only restatement report

`--as-of` is the latest session the caller vouches is complete for stock_prices AND market_index_prices. It must be a
session of the derived calendar (otherwise the run fails closed): a session with no bars cannot be an as-of.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from research.labels import fwd_v1 as f
from research.labels import repository as repo
from research.labels import sessions as cal

log = logging.getLogger("research.labels")


@dataclass
class RunReport:
    as_of_session: date
    applied: bool
    observations: int = 0
    evaluated: int = 0
    final: int = 0
    void: int = 0
    pending: Dict[str, int] = field(default_factory=dict)
    inserted: int = 0
    already_present: int = 0
    restated: List[Dict[str, Any]] = field(default_factory=list)
    calendar_source: str = ""


def _prepare(conn, todo, as_of: date, benchmark_symbol: str, authoritative: Optional[Mapping[date, Any]]):
    start = min(o.t0_session for o, _ in todo)
    sessions, source = cal.derive_sessions(conn, start, as_of, benchmark_symbol, authoritative)
    if as_of not in sessions:
        raise f.CalendarError(f"as_of_session {as_of} has no (or too thin) stock coverage: it is not a completed session")
    bars = repo.fetch_bars(conn, [o.symbol for o, _ in todo], start, as_of)
    bench = repo.fetch_benchmark(conn, benchmark_symbol, start, as_of)
    return sessions, source, bars, bench


def run(connect: Callable[[], AbstractContextManager], as_of_session: date, *, apply: bool = False,
        horizons: Sequence[int] = f.HORIZONS, limit: Optional[int] = None, benchmark_symbol: str = "^GSPC",
        authoritative: Optional[Mapping[date, Any]] = None) -> RunReport:
    if not isinstance(as_of_session, date):
        raise TypeError("as_of_session must be an explicit date")
    report = RunReport(as_of_session=as_of_session, applied=apply)
    pending: Counter = Counter()
    with connect() as conn:
        todo = repo.fetch_observations(conn, before=as_of_session, label_version=f.LABEL_VERSION, horizons=horizons,
                                       limit=limit)
        report.observations = len(todo)
        if not todo:
            return report
        sessions, source, bars, bench = _prepare(conn, todo, as_of_session, benchmark_symbol, authoritative)
        report.calendar_source = source
        cur = conn.cursor()
        for obs, hs in todo:
            for h in hs:
                report.evaluated += 1
                out = f.compute(obs, h, sessions, bars.get(obs.symbol, {}), bench, as_of_session,
                                benchmark_symbol=benchmark_symbol, calendar_source=source)
                if isinstance(out, f.Pending):
                    pending[out.reason.split(":")[0]] += 1
                    continue
                report.final += out.is_final
                report.void += not out.is_final
                if apply:
                    inserted, stored_hash = repo.insert_label(cur, out)
                    if inserted:
                        report.inserted += 1
                    else:
                        report.already_present += 1
                        if stored_hash and stored_hash != out.input_hash:
                            report.restated.append({"observation_id": obs.id, "horizon": h})
        if apply:
            conn.commit()
        else:
            conn.rollback()
    report.pending = dict(pending)
    return report


def restatement_report(connect: Callable[[], AbstractContextManager], as_of_session: date, *, benchmark_symbol: str = "^GSPC",
                       authoritative: Optional[Mapping[date, Any]] = None) -> List[Dict[str, Any]]:
    """Read-only. Recomputes the input hash of every stored FINAL label from today's stored prices. A mismatch means the
    provider restated history after the label was written; the label is left as it is (immutable) and is reported here so a
    dataset builder can exclude or re-version it."""
    out: List[Dict[str, Any]] = []
    with connect() as conn:
        stored = repo.fetch_labels(conn, f.LABEL_VERSION, status="final")
        if not stored:
            return out
        obs_by_id = {r["observation_id"]: f.Observation(r["observation_id"], r["symbol"], int(r["direction"]),
                                                         r["session_date"], float(r["entry_close"])) for r in stored}
        todo = [(o, ()) for o in obs_by_id.values()]
        sessions, source, bars, bench = _prepare(conn, todo, as_of_session, benchmark_symbol, authoritative)
        for r in stored:
            o = obs_by_id[r["observation_id"]]
            now = f.compute(o, r["horizon_sessions"], sessions, bars.get(o.symbol, {}), bench, as_of_session,
                            benchmark_symbol=benchmark_symbol, calendar_source=source)
            h = r["input_hash"].strip()
            if isinstance(now, f.Pending):
                out.append({"observation_id": o.id, "horizon": r["horizon_sessions"], "change": "inputs_no_longer_available"})
            elif now.input_hash != h:
                out.append({"observation_id": o.id, "horizon": r["horizon_sessions"], "change": "inputs_restated",
                            "now_status": now.label_status})
        conn.rollback()
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", required=True, type=date.fromisoformat, help="latest completed session (explicit; never inferred)")
    ap.add_argument("--apply", action="store_true", help="write labels (default: dry run)")
    ap.add_argument("--restatements", action="store_true", help="read-only restatement report; writes nothing")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    from shared.database import db
    if not db.initialize_sync_pool():
        print("database unavailable", file=sys.stderr)
        return 2
    try:
        if args.restatements:
            rows = restatement_report(db.get_sync_connection, args.as_of)
            print(f"restated or vanished labels: {len(rows)}")
            for r in rows[:50]:
                print(r)
            return 1 if rows else 0
        rep = run(db.get_sync_connection, args.as_of, apply=args.apply, limit=args.limit)
        print(rep)
        return 0
    except f.CalendarError as e:
        print(f"calendar error (failing closed): {e}", file=sys.stderr)
        return 3
    finally:
        db.close_pools()


if __name__ == "__main__":
    sys.exit(main())
