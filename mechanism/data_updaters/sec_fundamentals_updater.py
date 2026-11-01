#!/usr/bin/env python3
"""SEC EDGAR fundamentals updater: 10-Q / 10-K financial statements -> sec_fact (raw, append-only) and sec_quarter (normalized), with provenance.

Why: yfinance statements lag a report by days to weeks and Tiingo's statements cover only the Dow 30 on our plan, so a freshly reported quarter showed
the OLD quarter for a long time. EDGAR publishes the filing, with its acceptance time, the day it is filed. See docs/architecture/DATA_PROVIDERS.md.

Incremental, rate-limited, idempotent:
  * who is checked: a company never checked; a company whose earnings report (earnings_calendar, with a reported EPS) is newer than its newest periodic
    filing on file (checked at most every 12 hours); and everyone once a week (catches amendments). Nobody else costs a request.
  * what is fetched: the company's `submissions` document (one request) tells whether a periodic report we have not ingested exists; only then is the
    (large) `companyfacts` document fetched. Nothing new -> nothing written but the check time.
  * how: `sec_client` (declared User-Agent with a real contact, <= 5 requests/s by default, backoff, circuit breaker). Writes are upserts
    keyed on the filing, so a second run changes nothing; a restated value updates sec_quarter (revision_count + 1) while the earlier value stays in sec_fact.
  * no User-Agent contact configured (SEC_USER_AGENT) -> the run is skipped with a clear line and exit 0; the SEC requires the contact and none is invented.

Modes:  (default) run | --dry-run (fetch + normalize, no database writes) | --symbols A B | --reconcile (read-only comparison with quarterly_fundamentals)
        | --audit (read-only state counts)
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from data_updaters import sec_statements as S  # noqa: E402
from data_updaters.sec_client import SecCircuitOpen, SecClient, SecError, SecNotFound, UserAgentError, validate_user_agent  # noqa: E402
from shared import db, setup_logging  # noqa: E402

DATA_URL = "https://data.sec.gov"
TICKER_FILE_URL = "https://www.sec.gov/files/company_tickers.json"
TICKER_MAP_MAX_AGE_DAYS = 7
REPORT_RECHECK_HOURS = 12
SWEEP_DAYS = 7
PERIODIC = frozenset(S.PERIODIC_FORMS)

# Reconciliation tolerances: amounts 0.5% (rounding in thousands/millions), EPS 2 cents.
AMOUNT_REL_TOL = 0.005
EPS_ABS_TOL = 0.02


def norm_symbol(sym: str) -> str:
    return sym.strip().upper().replace(".", "-")


def parse_ticker_file(doc: Any) -> Dict[str, Tuple[int, str]]:
    """symbol -> (cik, company name). A symbol the file lists twice keeps its first (lowest-index) entry. Malformed entries are skipped."""
    out: Dict[str, Tuple[int, str]] = {}
    rows = doc.values() if isinstance(doc, dict) else doc
    for r in rows:
        try:
            sym, cik = norm_symbol(str(r["ticker"])), int(r["cik_str"])
        except (KeyError, TypeError, ValueError):
            continue
        if sym and cik > 0 and sym not in out:
            out[sym] = (cik, str(r.get("title") or ""))
    return out


def parse_recent_periodic(doc: Any, cik: int) -> List[Dict[str, Any]]:
    """The periodic reports in a `submissions` document's recent filings: accession, form, filed date, acceptance instant, period end."""
    try:
        doc_cik = int(str(doc.get("cik", "")).lstrip("0") or "0")
    except ValueError:
        doc_cik = 0
    if doc_cik != cik:
        raise S.SecStatementError(f"submissions document is for CIK {doc.get('cik')!r}, but CIK {cik} was requested; refused as a whole")
    recent = (doc.get("filings") or {}).get("recent") or {}
    acc, form, filed = recent.get("accessionNumber") or [], recent.get("form") or [], recent.get("filingDate") or []
    rep, acpt = recent.get("reportDate") or [], recent.get("acceptanceDateTime") or []
    out = []
    for i in range(min(len(acc), len(form), len(filed))):
        if form[i] not in PERIODIC:
            continue
        try:
            fd = date.fromisoformat(filed[i][:10])
        except (TypeError, ValueError):
            continue
        accepted = None
        if i < len(acpt) and isinstance(acpt[i], str) and acpt[i].endswith("Z"):
            try:
                accepted = datetime.fromisoformat(acpt[i].replace("Z", "+00:00"))
            except ValueError:
                accepted = None
        pe = None
        if i < len(rep) and isinstance(rep[i], str) and len(rep[i]) >= 10:
            try:
                pe = date.fromisoformat(rep[i][:10])
            except ValueError:
                pe = None
        out.append({"accession": acc[i], "form": form[i], "filed": fd, "accepted_at": accepted, "period_end": pe, "is_amendment": form[i].endswith("/A")})
    return sorted(out, key=lambda r: (r["filed"], r["accession"]))


def compare_value(metric: str, sec_value: float, other: float) -> bool:
    if metric == "eps_diluted":
        return abs(sec_value - other) <= EPS_ABS_TOL
    base = max(abs(sec_value), abs(other), 1.0)
    return abs(sec_value - other) <= AMOUNT_REL_TOL * base


class SecFundamentalsUpdater:
    def __init__(self, transport: Optional[SecClient] = None, logger=None, clock=time.monotonic):
        self.logger = logger or setup_logging(__name__)
        self._transport = transport
        self._clock = clock

    # ------------------------------------------------------------------ transport / config
    @staticmethod
    def config_from_env() -> Optional[str]:
        ua = os.environ.get("SEC_USER_AGENT", "").strip().strip("\"'").strip()      # an env file passed straight to docker keeps its quotes
        if not ua:
            return None
        return validate_user_agent(ua)             # operator + real contact email; a placeholder is refused

    @property
    def transport(self) -> SecClient:
        if self._transport is None:
            ua = self.config_from_env()
            if ua is None:
                raise UserAgentError("SEC_USER_AGENT is not set")
            self._transport = SecClient(ua)
        return self._transport

    def _get_json(self, url: str) -> Any:
        got = self.transport.fetch(url, capture=False)
        return json.loads(got.body)

    # ------------------------------------------------------------------ ticker map
    def refresh_ticker_map(self, force: bool = False) -> int:
        age = db.execute_dict_query("SELECT COUNT(*) AS n, MAX(fetched_at) AS newest FROM sec_symbol_cik")[0]
        if not force and age["n"] and age["newest"] is not None:
            fresh = db.execute_dict_query("SELECT NOW() - %s::timestamptz < make_interval(days => %s) AS ok", (age["newest"], TICKER_MAP_MAX_AGE_DAYS))[0]["ok"]
            if fresh:
                return 0
        mapping = parse_ticker_file(self._get_json(TICKER_FILE_URL))
        rows = [(s, c, n[:200]) for s, (c, n) in mapping.items()]
        q = ("INSERT INTO sec_symbol_cik (symbol, cik, company_name) VALUES %s ON CONFLICT (symbol) DO UPDATE SET "
             "cik = EXCLUDED.cik, company_name = EXCLUDED.company_name, fetched_at = NOW()")
        db.bulk_insert(q, rows)
        self.logger.info(f"SEC ticker map refreshed: {len(rows)} symbols")
        return len(rows)

    # ------------------------------------------------------------------ who is due
    def select_due(self, limit: Optional[int] = None) -> List[Tuple[str, int]]:
        q = """
            SELECT m.symbol, m.cik,
                   (rr.rd IS NOT NULL AND (st.newest_periodic_filed IS NULL OR st.newest_periodic_filed < rr.rd)) AS report_pending
            FROM (SELECT DISTINCT symbol FROM stock_prices) sp
            JOIN sec_symbol_cik m ON m.symbol = sp.symbol
            LEFT JOIN sec_fundamentals_state st ON st.cik = m.cik
            LEFT JOIN LATERAL (
                SELECT MAX(e.report_date) AS rd FROM earnings_calendar e
                WHERE e.symbol = sp.symbol AND e.eps_actual IS NOT NULL AND e.report_date BETWEEN CURRENT_DATE - 21 AND CURRENT_DATE
            ) rr ON TRUE
            WHERE sp.symbol NOT IN (SELECT symbol FROM inactive_symbols)
              AND (st.cik IS NULL
                   OR (rr.rd IS NOT NULL AND (st.newest_periodic_filed IS NULL OR st.newest_periodic_filed < rr.rd)
                       AND st.last_checked_at < NOW() - make_interval(hours => %s))
                   OR st.last_checked_at < NOW() - make_interval(days => %s))
            ORDER BY report_pending DESC, st.last_checked_at NULLS FIRST, m.symbol
        """
        seen, out = set(), []
        for r in db.execute_dict_query(q, (REPORT_RECHECK_HOURS, SWEEP_DAYS)):
            if r["cik"] in seen:
                continue
            seen.add(r["cik"])
            out.append((r["symbol"], int(r["cik"])))
        return out[:limit] if limit else out

    # ------------------------------------------------------------------ one company
    def _state(self, cik: int) -> Optional[Dict[str, Any]]:
        rows = db.execute_dict_query("SELECT * FROM sec_fundamentals_state WHERE cik = %s", (cik,))
        return rows[0] if rows else None

    def _write_state(self, cik: int, symbol: str, status: str, newest: Optional[Dict[str, Any]], facts_fetched: bool, error: Optional[str] = None) -> None:
        # A failed check is backdated so it comes due again in about a day instead of waiting out the weekly sweep.
        checked = datetime.now(timezone.utc) - timedelta(days=SWEEP_DAYS - 1) if status == "error" else datetime.now(timezone.utc)
        db.execute_insert("""
            INSERT INTO sec_fundamentals_state (cik, symbol, last_checked_at, last_status, newest_periodic_accession, newest_periodic_filed, facts_fetched_at, last_error)
            VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s THEN NOW() ELSE NULL END, %s)
            ON CONFLICT (cik) DO UPDATE SET symbol = EXCLUDED.symbol, last_checked_at = EXCLUDED.last_checked_at, last_status = EXCLUDED.last_status,
                newest_periodic_accession = COALESCE(EXCLUDED.newest_periodic_accession, sec_fundamentals_state.newest_periodic_accession),
                newest_periodic_filed = COALESCE(EXCLUDED.newest_periodic_filed, sec_fundamentals_state.newest_periodic_filed),
                facts_fetched_at = CASE WHEN %s THEN NOW() ELSE sec_fundamentals_state.facts_fetched_at END,
                last_error = EXCLUDED.last_error
        """, (cik, symbol, checked, status, newest["accession"] if newest else None, newest["filed"] if newest else None, facts_fetched, error, facts_fetched))

    def process(self, symbol: str, cik: int, dry_run: bool = False) -> Dict[str, Any]:
        """Check one company. Returns {status, new_filing, facts, quarters}; never raises for a per-company problem."""
        res = {"symbol": symbol, "cik": cik, "status": "ok", "new_filing": False, "facts": 0, "quarters": 0}
        try:
            subs = self._get_json(f"{DATA_URL}/submissions/CIK{cik:010d}.json")
            filings = parse_recent_periodic(subs, cik)
        except SecNotFound:
            res["status"] = "not_found"
        except (SecError, ValueError, S.SecStatementError) as e:
            res.update(status="error", error=str(e)[:300])
            if isinstance(e, SecCircuitOpen) or getattr(self._transport, "circuit_open", False):
                res["circuit_open"] = True
        if res["status"] != "ok":
            if not dry_run:
                self._write_state(cik, symbol, res["status"], None, False, res.get("error"))
            return res
        if not filings:
            res["status"] = "no_filings"
            if not dry_run:
                self._write_state(cik, symbol, "no_filings", None, False)
            return res
        newest = filings[-1]
        state = None if dry_run else self._state(cik)
        have_newest = bool(state and state.get("newest_periodic_accession") == newest["accession"] and state.get("facts_fetched_at") is not None)
        if have_newest:
            self._write_state(cik, symbol, "ok", newest, False)
            return res
        res["new_filing"] = True
        try:
            doc = self._get_json(f"{DATA_URL}/api/xbrl/companyfacts/CIK{cik:010d}.json")
            facts = S.parse_companyfacts(doc, cik)
        except SecNotFound:
            res["status"] = "no_xbrl"
            facts = []
        except (SecError, ValueError, S.SecStatementError) as e:
            res.update(status="error", error=str(e)[:300])
            if isinstance(e, SecCircuitOpen) or getattr(self._transport, "circuit_open", False):
                res["circuit_open"] = True
            if not dry_run:
                self._write_state(cik, symbol, "error", None, False, res["error"])
            return res
        if not facts and res["status"] == "ok":
            res["status"] = "no_xbrl"
        quarters = S.normalize(facts)
        res["facts"], res["quarters"] = len(facts), len(quarters)
        if dry_run:
            res["sample"] = [(q.metric, q.period_end.isoformat(), q.value, q.basis, q.accession) for q in quarters if q.metric == "revenue"][-3:]
            return res
        self._write_filings(cik, filings)
        self._write_facts(cik, facts)
        self._write_quarters(cik, quarters)
        self._write_state(cik, symbol, res["status"], newest, True)
        return res

    # ------------------------------------------------------------------ writes (all idempotent)
    def _write_filings(self, cik: int, filings: Sequence[Dict[str, Any]]) -> None:
        rows = [(f["accession"], cik, f["form"], f["filed"], f["accepted_at"], f["period_end"], f["is_amendment"]) for f in filings]
        db.bulk_insert("INSERT INTO sec_filing (accession, cik, form, filed_date, accepted_at, period_end, is_amendment) VALUES %s ON CONFLICT (accession) DO NOTHING", rows)

    def _write_facts(self, cik: int, facts: Sequence[S.RawFact]) -> None:
        rows = [(cik, f.accession, f.metric, f.tag, f.start, f.end, f.value, f.unit, f.fy, f.fp, f.form, f.filed) for f in facts]
        db.bulk_insert("INSERT INTO sec_fact (cik, accession, metric, tag, period_start, period_end, value, unit, fy, fp, form, filed_date) VALUES %s "
                       "ON CONFLICT (cik, accession, metric, period_start, period_end) DO NOTHING", rows)

    def _write_quarters(self, cik: int, quarters: Sequence[S.QuarterRow]) -> None:
        rows = [(cik, q.period_end, q.metric, q.value, q.unit, q.basis, q.fiscal_year, q.fiscal_quarter, q.accession, q.filed, q.form) for q in quarters]
        db.bulk_insert("""
            INSERT INTO sec_quarter (cik, period_end, metric, value, unit, basis, fiscal_year, fiscal_quarter, accession, filed_date, form) VALUES %s
            ON CONFLICT (cik, period_end, metric) DO UPDATE SET
                value = EXCLUDED.value, unit = EXCLUDED.unit, basis = EXCLUDED.basis, fiscal_year = EXCLUDED.fiscal_year,
                fiscal_quarter = EXCLUDED.fiscal_quarter, accession = EXCLUDED.accession, filed_date = EXCLUDED.filed_date,
                form = EXCLUDED.form, updated_at = NOW(),
                revision_count = sec_quarter.revision_count + CASE WHEN sec_quarter.value IS DISTINCT FROM EXCLUDED.value THEN 1 ELSE 0 END
            WHERE (sec_quarter.value, sec_quarter.basis, sec_quarter.accession, sec_quarter.fiscal_year, sec_quarter.fiscal_quarter)
                  IS DISTINCT FROM (EXCLUDED.value, EXCLUDED.basis, EXCLUDED.accession, EXCLUDED.fiscal_year, EXCLUDED.fiscal_quarter)
        """, rows)

    # ------------------------------------------------------------------ the run
    def run(self, limit: Optional[int] = None, max_seconds: Optional[float] = None, dry_run: bool = False, symbols: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        started = self._clock()
        if self.config_from_env() is None and self._transport is None:
            self.logger.warning("SEC_FUNDAMENTALS skipped: SEC_USER_AGENT is not set (the SEC requires a declared operator and contact; none is assumed)")
            return {"skipped": True}
        if symbols:
            if dry_run:
                mapping = parse_ticker_file(self._get_json(TICKER_FILE_URL))
                due = [(norm_symbol(s), mapping[norm_symbol(s)][0]) for s in symbols if norm_symbol(s) in mapping]
            else:
                self.refresh_ticker_map()
                rows = db.execute_dict_query("SELECT symbol, cik FROM sec_symbol_cik WHERE symbol = ANY(%s)", ([norm_symbol(s) for s in symbols],))
                due = [(r["symbol"], int(r["cik"])) for r in rows]
        else:
            self.refresh_ticker_map()
            due = self.select_due(limit)
        stats = {"due": len(due), "checked": 0, "new_filings": 0, "facts": 0, "quarters": 0, "errors": 0, "not_found": 0, "no_xbrl": 0,
                 "stopped_by_budget": False, "circuit_open": False}
        for symbol, cik in due:
            if max_seconds is not None and self._clock() - started > max_seconds:
                stats["stopped_by_budget"] = True
                break
            r = self.process(symbol, cik, dry_run=dry_run)
            stats["checked"] += 1
            stats["new_filings"] += 1 if r["new_filing"] else 0
            stats["facts"] += r["facts"]
            stats["quarters"] += r["quarters"]
            stats["errors"] += 1 if r["status"] == "error" else 0
            stats["not_found"] += 1 if r["status"] == "not_found" else 0
            stats["no_xbrl"] += 1 if r["status"] == "no_xbrl" else 0
            if dry_run and r.get("sample"):
                self.logger.info(f"DRY-RUN {symbol}: {r['sample']}")
            if r.get("circuit_open"):
                stats["circuit_open"] = True
                break
        stats["dry_run"] = dry_run
        self.logger.info("SEC_FUNDAMENTALS " + " ".join(f"{k}={v}" for k, v in sorted(stats.items())))
        return stats

    # ------------------------------------------------------------------ reconciliation and audit (read-only)
    def reconcile(self, symbols: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """Compare SEC quarters with quarterly_fundamentals (revenue, net income, EPS) on the same period end (+-5 days)."""
        sym_filter = "AND m.symbol = ANY(%s)" if symbols else ""
        params: Tuple = (([norm_symbol(s) for s in symbols],) if symbols else ())
        sec = db.execute_dict_query(f"""
            SELECT m.symbol, q.period_end, q.metric, q.value::float8 AS value, q.basis, q.filed_date
            FROM sec_quarter q JOIN sec_symbol_cik m ON m.cik = q.cik
            WHERE q.metric IN ('revenue', 'net_income', 'eps_diluted') {sym_filter}""", params)
        yf = db.execute_dict_query("""
            SELECT symbol, quarter AS period_end, revenue::float8 AS revenue, net_income::float8 AS net_income, eps::float8 AS eps
            FROM quarterly_fundamentals""" + (" WHERE symbol = ANY(%s)" if symbols else ""), params)
        by_sym: Dict[str, List[Dict[str, Any]]] = {}
        for r in yf:
            by_sym.setdefault(r["symbol"], []).append(r)
        col = {"revenue": "revenue", "net_income": "net_income", "eps_diluted": "eps"}
        out = {"compared": 0, "match": 0, "mismatch": 0, "no_counterpart": 0, "by_metric": {}, "examples": [], "newer_than_yfinance": []}
        newest_yf = {s: max(r["period_end"] for r in rs) for s, rs in by_sym.items()}
        newer = {}
        for r in sec:
            cand = [y for y in by_sym.get(r["symbol"], []) if abs((y["period_end"] - r["period_end"]).days) <= 5]
            val = cand[0][col[r["metric"]]] if cand else None
            if not cand or val is None:
                out["no_counterpart"] += 1
                if r["symbol"] in newest_yf and r["period_end"] > newest_yf[r["symbol"]]:
                    newer[r["symbol"]] = max(newer.get(r["symbol"], r["period_end"]), r["period_end"])
                continue
            out["compared"] += 1
            ok = compare_value(r["metric"], r["value"], val)
            m = out["by_metric"].setdefault(r["metric"], {"compared": 0, "match": 0})
            m["compared"] += 1
            m["match"] += 1 if ok else 0
            out["match" if ok else "mismatch"] += 1
            if not ok and len(out["examples"]) < 25:
                out["examples"].append({"symbol": r["symbol"], "period_end": str(r["period_end"]), "metric": r["metric"], "sec": r["value"], "yfinance": val, "basis": r["basis"]})
        out["newer_than_yfinance"] = sorted(f"{s}:{d}" for s, d in newer.items())[:50]
        out["newer_than_yfinance_count"] = len(newer)
        return out

    def audit(self) -> Dict[str, Any]:
        r = db.execute_dict_query("""
            SELECT (SELECT COUNT(*) FROM sec_symbol_cik) AS mapped,
                   (SELECT COUNT(*) FROM sec_fundamentals_state) AS checked,
                   (SELECT COUNT(*) FROM sec_fundamentals_state WHERE last_status = 'ok') AS ok,
                   (SELECT COUNT(*) FROM sec_fundamentals_state WHERE last_status = 'error') AS errors,
                   (SELECT COUNT(*) FROM sec_fundamentals_state WHERE last_status IN ('no_xbrl', 'not_found', 'no_filings')) AS no_us_gaap,
                   (SELECT COUNT(*) FROM sec_quarter) AS quarters,
                   (SELECT MAX(filed_date) FROM sec_filing) AS newest_filing,
                   (SELECT MAX(last_checked_at) FROM sec_fundamentals_state) AS last_check""")
        return {k: (str(v) if isinstance(v, (date, datetime)) else v) for k, v in r[0].items()}


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="SEC EDGAR fundamentals updater")
    p.add_argument("--symbols", nargs="+", help="only these symbols")
    p.add_argument("--limit", type=int, help="at most N companies")
    p.add_argument("--max-seconds", type=float, help="stop cleanly after this wall-clock budget (the rest stay due)")
    p.add_argument("--dry-run", action="store_true", help="fetch and normalize, write nothing")
    p.add_argument("--reconcile", action="store_true", help="read-only: compare SEC quarters with quarterly_fundamentals")
    p.add_argument("--audit", action="store_true", help="read-only: ingestion state counts")
    p.add_argument("--coverage", action="store_true", help="read-only: coverage by sector / period, freshness vs yfinance, missing fields, failures (JSON)")
    a = p.parse_args()
    u = SecFundamentalsUpdater()
    if a.coverage:
        from data_updaters import sec_coverage
        print(json.dumps(sec_coverage.coverage(db), indent=1, default=str))
        return 0
    if a.audit:
        print(json.dumps(u.audit(), indent=1))
        return 0
    if a.reconcile:
        print(json.dumps(u.reconcile(a.symbols), indent=1, default=str))
        return 0
    try:
        stats = u.run(limit=a.limit, max_seconds=a.max_seconds, dry_run=a.dry_run, symbols=a.symbols)
    except UserAgentError as e:
        print(f"SEC_FUNDAMENTALS refused: {e}", file=sys.stderr)
        return 2
    return 1 if stats.get("circuit_open") else 0


if __name__ == "__main__":
    sys.exit(main())
