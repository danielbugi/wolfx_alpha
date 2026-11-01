#!/usr/bin/env python3
"""Fundamental Inflection updater: incremental, idempotent, logged. Intended for the pipeline after the quarterly-fundamentals step.

    python -m inflection.updater                      # symbols with new fundamentals or recent events, as of today
    python -m inflection.updater --full               # every symbol
    python -m inflection.updater --symbols AAPL HRI   # these symbols
    python -m inflection.updater --asof 2026-07-31    # reproduce a historical day (point-in-time)
    python -m inflection.updater --dry-run            # compute, write nothing
    python -m inflection.updater --audit              # read-only counts

One log line per run:  INFLECTION due=.. processed=.. features=.. events_new=.. events_revised=.. events_unchanged=.. errors=..  (also written to inflection_run_log).
Live SEC requests are not part of this module: it reads the stored quarterly fundamentals (and, once the SEC pipeline is active, can be pointed at its quarters).
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from inflection import config as C  # noqa: E402
from inflection import pipeline as P  # noqa: E402
from inflection import pit as PIT  # noqa: E402
from inflection import store as S  # noqa: E402
from inflection import context as X  # noqa: E402
from shared import db, setup_logging  # noqa: E402

BATCH = 150


class InflectionUpdater:
    def __init__(self, logger=None) -> None:
        self.logger = logger or setup_logging(__name__)

    def run(self, asof: Optional[date] = None, symbols: Optional[Sequence[str]] = None, full: bool = False, dry_run: bool = False, limit: Optional[int] = None, source: str = "yfinance",
            since: Optional[date] = None) -> Dict[str, Any]:
        asof = asof or date.today()
        pit = source == "sec_pit"
        sec = source == "sec" or pit
        mv = C.METHOD_VERSION_SEC_PIT if pit else C.METHOD_VERSION_SEC if sec else C.METHOD_VERSION
        if not db.execute_dict_query("SELECT to_regclass('fundamental_inflection_event') AS t")[0]["t"]:
            self.logger.warning("INFLECTION skipped: migration 40 (fundamental inflection tables) is not applied")
            return {"skipped": True, "reason": "migration 40 not applied", "errors": 0}
        if sec and not db.execute_dict_query("SELECT to_regclass('sec_quarter') AS t")[0]["t"]:
            self.logger.warning("INFLECTION skipped: migration 38 (SEC tables) is not applied")
            return {"skipped": True, "reason": "migration 38 not applied", "errors": 0}
        due = list(symbols) if symbols else (S.sec_symbols() if sec else [r["symbol"] for r in db.execute_dict_query("SELECT DISTINCT symbol FROM quarterly_fundamentals ORDER BY symbol")] if full else S.due_symbols(asof))
        if limit:
            due = due[:limit]
        stats = {"due": len(due), "processed": 0, "features": 0, "events_new": 0, "events_revised": 0, "events_unchanged": 0, "errors": 0, "dry_run": dry_run, "asof": asof.isoformat(), "source": source}
        if not due:
            self.logger.info("INFLECTION nothing due")
            return stats
        run_id = None if dry_run else S.start_run(asof, len(due), mv)
        try:
            inputs = S.load_inputs_sec(asof, due) if source == "sec" else S.load_inputs(asof, None)          # all fundamentals are small; the sector benchmark needs every symbol's prices
            index = X.SectorIndex(inputs.prices, inputs.sector_of) if inputs.prices else None
            for i in range(0, len(due), BATCH):
                batch = due[i:i + BATCH]
                feats: List[Dict[str, Any]] = []
                evs: List[Dict[str, Any]] = []
                versions = S.load_sec_filing_versions(batch) if pit else {}
                for sym in batch:
                    try:
                        if pit:
                            facts, accepted = versions.get(sym, ([], {}))
                            items = PIT.pit_items(facts, accepted, inputs.sector_of.get(sym), since, PIT.known_quarters(PIT.with_availability(facts, accepted), asof))
                            if not items:
                                continue
                            f, e = P.build_symbol_pit(sym, items, inputs.sector_of.get(sym), inputs.prices.get(sym), index, inputs.valuation, asof, mv)
                            feats += f
                            evs += e
                            stats["processed"] += 1
                            continue
                        rows = inputs.quarterly.get(sym) or []
                        if not rows:
                            continue
                        f, e = P.build_symbol(sym, rows, inputs.report_dates.get(sym, []), inputs.sector_of.get(sym), inputs.prices.get(sym), index, inputs.valuation, asof, mv)
                        feats += f
                        evs += e
                        stats["processed"] += 1
                    except Exception as exc:  # noqa: BLE001 - one symbol must never stop the run
                        stats["errors"] += 1
                        self.logger.warning(f"INFLECTION {sym} failed: {type(exc).__name__}: {exc}")
                if not dry_run:
                    stats["features"] += S.upsert_features(feats) if feats else 0
                    c = S.upsert_events(evs)
                    for k in ("new", "revised", "unchanged"):
                        stats[f"events_{k}"] += c[k]
                else:
                    stats["features"] += len(feats)
                    stats["events_new"] += len(evs)
            status = "ok" if stats["errors"] == 0 else "partial"
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            stats["errors"] += 1
            self.logger.error(f"INFLECTION run failed: {type(exc).__name__}: {exc}")
            if run_id:
                S.finish_run(run_id, processed=stats["processed"], features=stats["features"], new=stats["events_new"], revised=stats["events_revised"], errors=stats["errors"], status=status, note=str(exc))
            raise
        if run_id:
            S.finish_run(run_id, processed=stats["processed"], features=stats["features"], new=stats["events_new"], revised=stats["events_revised"], errors=stats["errors"], status=status)
        self.logger.info("INFLECTION " + " ".join(f"{k}={v}" for k, v in stats.items()))
        return stats

    def audit(self) -> Dict[str, Any]:
        r = db.execute_dict_query("""
            SELECT (SELECT COUNT(*) FROM fundamental_feature WHERE method_version = %s) AS features,
                   (SELECT COUNT(*) FROM fundamental_inflection_event WHERE method_version = %s) AS events,
                   (SELECT COUNT(*) FROM fundamental_inflection_event WHERE method_version = %s AND available_at >= CURRENT_DATE - 30) AS events_30d,
                   (SELECT MAX(available_at) FROM fundamental_inflection_event WHERE method_version = %s) AS newest_event,
                   (SELECT status FROM inflection_run_log ORDER BY id DESC LIMIT 1) AS last_run_status,
                   (SELECT finished_at FROM inflection_run_log ORDER BY id DESC LIMIT 1) AS last_run_finished""", (C.METHOD_VERSION,) * 4)
        return {k: (str(v) if isinstance(v, (date, datetime)) else v) for k, v in r[0].items()}


def main() -> int:
    import argparse
    import json
    p = argparse.ArgumentParser(description="Fundamental inflection updater")
    p.add_argument("--asof"); p.add_argument("--symbols", nargs="+"); p.add_argument("--full", action="store_true"); p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int); p.add_argument("--audit", action="store_true"); p.add_argument("--source", choices=["yfinance", "sec", "sec_pit"], default="yfinance")
    p.add_argument("--since", help="sec_pit only: evaluate quarters first public on or after this date (default: the last 200 days)")
    a = p.parse_args()
    u = InflectionUpdater()
    if a.audit:
        print(json.dumps(u.audit(), indent=1))
        return 0
    stats = u.run(asof=date.fromisoformat(a.asof) if a.asof else None, symbols=a.symbols, full=a.full, dry_run=a.dry_run, limit=a.limit, source=a.source,
                   since=(date.fromisoformat(a.since) if a.since else (date.today() - timedelta(days=200)) if a.source == "sec_pit" and not a.full else None))
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
