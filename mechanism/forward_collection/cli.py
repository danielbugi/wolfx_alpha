"""python -m forward_collection run|verify|verify-sector-history|preflight|activation   (dry-run by default; --apply needs --code-ref; nothing here schedules anything)."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from typing import Any, Callable, Optional, Sequence

from . import contract as C
from . import orchestrator as O
from . import steps as S


def _default_sessions(now):
    from shared import market_calendar as mc
    return mc.get_sessions(now.astimezone(mc.NY).date())


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="forward_collection", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "verify"):
        s = sub.add_parser(name)
        g = s.add_mutually_exclusive_group(required=True)
        g.add_argument("--session", type=date.fromisoformat)
        g.add_argument("--latest-completed", action="store_true", help="the latest completed US session on the market calendar")
        s.add_argument("--feature-set-version", default=S.FEATURE_SET_VERSION)
        s.add_argument("--grace-days", type=int, default=1, help="the dataset spec's availability_grace_days")
        if name == "run":
            s.add_argument("--apply", action="store_true")
            s.add_argument("--code-ref")
            s.add_argument("--with-capture-check", action="store_true", help="also require a complete candidate capture run for the session")
            s.add_argument("--with-sector-history-check", action="store_true",
                           help="also require the authoritative sector history to have been refreshed for the session's universe (read-only verification)")
    s = sub.add_parser("verify-sector-history", help="read-only: was the authoritative sector history refreshed for the session? (exit 0/2/4)")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--session", type=date.fromisoformat)
    g.add_argument("--latest-completed", action="store_true")
    s.add_argument("--grace-days", type=int, default=1)
    s = sub.add_parser("preflight")
    s.add_argument("--spec", required=True)
    s = sub.add_parser("activation", help="read-only: the complete activation preflight (architecture / environment / activation layers)")
    s.add_argument("--spec", required=True)
    s.add_argument("--runtime-role", default="donchian_app")
    s.add_argument("--gate", action="append", default=[], metavar="NAME=yes",
                   help="an owner/operational gate the caller attests to (repeatable); a gate that is not supplied is NO")
    return p


def main(argv: Optional[Sequence[str]] = None, *, connect: Optional[Callable[[], Any]] = None, clock: Optional[Callable[[], Any]] = None,
         sessions_provider: Optional[Callable[[Any], Any]] = None, out: Callable[[str], None] = print) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "run" and args.apply and not args.code_ref:
        print("--apply requires --code-ref (the revision of the code that computes the rows)", file=sys.stderr)
        return C.EXIT_REFUSED
    closer = None
    if connect is None:
        from shared.database import db
        if not db.initialize_sync_pool():
            print("database unavailable", file=sys.stderr)
            return C.EXIT_REFUSED
        connect, closer = db.get_sync_connection, db.close_pools
    try:
        if args.cmd == "preflight":
            from . import preflight
            with open(args.spec, encoding="utf-8") as fh:
                rep = preflight.run_preflight(connect, json.load(fh))
            out(json.dumps(rep, sort_keys=True, default=str))
            return C.EXIT_COMPLETE if rep["collector_stack_ready_for_activation"] == "YES" else C.EXIT_INCOMPLETE
        if args.cmd == "activation":
            from . import activation
            gates = dict(g.split("=", 1) for g in args.gate if "=" in g)
            with open(args.spec, encoding="utf-8") as fh:
                rep = activation.run_activation_preflight(connect, json.load(fh), gates=gates, runtime_role=args.runtime_role)
            out(json.dumps(rep, sort_keys=True, default=str))
            return C.EXIT_COMPLETE if rep["forward_research_collection_ready_for_activation"] == "YES" else C.EXIT_INCOMPLETE
        from shared import market_calendar as mc
        clock = clock or O.db_clock(connect)
        sessions_provider = sessions_provider or _default_sessions
        try:
            now = clock()
            sessions, source = sessions_provider(now)
            if source == "weekday-fallback":
                raise mc.SessionError("no trustworthy trading calendar (weekday fallback): refusing to collect")
            day, how = mc.resolve_session(args.session, now, sessions)
            if day is None:
                raise mc.SessionError("the calendar is empty")
        except mc.SessionError as e:
            print(f"refused (failing closed): {e}", file=sys.stderr)
            return C.EXIT_REFUSED
        if args.cmd == "verify":
            ctx = O.Ctx(connect, day, True, None, args.grace_days, O.decision_deadline(day, args.grace_days), {})
            r = S.make_verify(args.feature_set_version)(ctx)
            out(json.dumps({"schema": C.SCHEMA, "session": day.isoformat(), "how": how, **r.to_dict()}, sort_keys=True, default=str))
            return C.EXIT_COMPLETE if r.ok() else (C.EXIT_MISSED if r.detail.get("permanent_problems") else C.EXIT_INCOMPLETE)
        if args.cmd == "verify-sector-history":
            ctx = O.Ctx(connect, day, False, None, args.grace_days, O.decision_deadline(day, args.grace_days), {}, clock)
            r = S.make_sector_history_verify()(ctx)
            out(json.dumps({"schema": C.SCHEMA, "session": day.isoformat(), "how": how, **r.to_dict()}, sort_keys=True, default=str))
            return C.EXIT_COMPLETE if r.ok() else (C.EXIT_MISSED if r.detail.get("permanent_problems") else C.EXIT_INCOMPLETE)
        steps = S.make_steps(feature_set_version=args.feature_set_version, with_capture=args.with_capture_check,
                             with_sector_history=args.with_sector_history_check)
        rep = O.run_session(connect, day, steps, apply=args.apply, grace_days=args.grace_days, code_ref=args.code_ref, clock=clock)
        out(json.dumps({**rep.to_dict(), "session_resolution": how}, sort_keys=True, default=str))
        return rep.exit_code()
    finally:
        if closer:
            closer()


if __name__ == "__main__":
    sys.exit(main())
