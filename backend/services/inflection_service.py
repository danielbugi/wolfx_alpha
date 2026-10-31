# File: backend/services/inflection_service.py
"""
Fundamental Inflection read model: the events and per-quarter features the inflection engine stored (mechanism/inflection, migration 40). READ ONLY.

Nothing here computes a signal or touches a write path. The numbers are research signals: explainable, deterministic and unvalidated as predictors (the Quant Lab holds their
out-of-sample results). Every row carries its source, the date its figures were public, how that date was determined and its data-quality status, and the API passes all of it through.
"""
import logging
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence

from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

METHOD_VERSION = "inflection_v1"
LABELS = {
    "profitability_turnaround": "Profitability turnaround",
    "revenue_acceleration": "Revenue acceleration",
    "margin_expansion": "Margin expansion",
    "operating_leverage_inflection": "Operating leverage inflection",
    "fcf_turnaround": "Free cash flow turnaround",
    "improvement_with_limited_repricing": "Fundamental improvement with limited price repricing",
}
SORTS = {"strength": "e.strength DESC, e.available_at DESC", "available_at": "e.available_at DESC, e.strength DESC", "score": "e.score DESC, e.strength DESC", "symbol": "e.symbol ASC"}
NOTICE = ("Research signals, not recommendations. Rule-based and explainable; whether inflections predict returns is tested separately in the Quant Lab and is not established. "
          "Statements come from the stored quarterly fundamentals; the date each was public is the earnings report date when known, otherwise an assumed lag (shown).")


def _key_metrics(kind: str, m: Dict[str, Any]) -> List[Dict[str, Any]]:
    def item(label, cur, prev, unit):
        return {"label": label, "current": cur, "previous": prev, "unit": unit}
    if kind == "revenue_acceleration":
        return [item("Revenue growth YoY", m.get("rev_yoy"), m.get("rev_yoy_prev"), "pct"), item("Revenue", m.get("revenue"), m.get("revenue_py"), "money")]
    if kind == "margin_expansion":
        return [item("Operating margin", m.get("op_margin"), m.get("op_margin_py"), "pct"), item("Change (bps)", m.get("op_margin_yoy_bps"), None, "bps")]
    if kind == "operating_leverage_inflection":
        return [item("Operating leverage (x)", m.get("op_leverage"), None, "ratio"), item("Revenue growth YoY", m.get("rev_yoy"), None, "pct")]
    if kind == "profitability_turnaround":
        return [item("Net income", m.get("net_income"), m.get("net_income_py"), "money"), item("Previous quarter", m.get("net_income_pq"), None, "money")]
    if kind == "fcf_turnaround":
        return [item("Free cash flow", m.get("free_cash_flow"), m.get("free_cash_flow_py"), "money"), item("FCF margin", m.get("fcf_margin"), None, "pct")]
    if kind == "improvement_with_limited_repricing":
        return [item("Inflection score", m.get("inflection_score"), None, "score"), item("Return vs sector (window)", m.get("excess_return_window"), None, "pct")]
    return []


class InflectionService:
    def __init__(self, get_connection: Callable) -> None:
        self._get_connection = get_connection

    def _query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql, params)
            rows = cur.fetchall()
            cur.close()
            return [dict(r) for r in rows]
        finally:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _shape(r: Dict[str, Any], today: date) -> Dict[str, Any]:
        ctx = r.get("context") or {}
        reaction = (ctx.get("reaction") or {})
        val_pub, val_now = ctx.get("valuation_at_publication") or {}, ctx.get("valuation_latest") or {}
        return {
            "id": r["id"], "symbol": r["symbol"], "company_name": r.get("company_name"), "sector": r.get("sector"),
            "event_type": r["event_type"], "label": LABELS.get(r["event_type"], r["event_type"]), "strength": float(r["strength"]), "severity": r["severity"], "score": float(r["score"]),
            "fiscal_year": r.get("fiscal_year"), "fiscal_quarter": r.get("fiscal_quarter"), "quarter_end": r["quarter_end"].isoformat(),
            "available_at": r["available_at"].isoformat(), "freshness_days": (today - r["available_at"]).days, "availability_basis": r["availability_basis"],
            "source": r.get("source"), "data_quality": r["data_quality"], "revision_count": r.get("revision_count", 0), "method_version": r.get("method_version"),
            "explanation": r["explanation"], "key_metrics": _key_metrics(r["event_type"], r["metrics"] or {}),
            "reaction_2d": reaction.get("ret_2d"), "excess_vs_sector_since_publication": ctx.get("excess_vs_sector_since_publication"),
            "market_cap": val_now.get("market_cap"), "valuation": {"ps_ratio": val_now.get("ps_ratio"), "pe_ratio": val_now.get("pe_ratio"), "ps_percentile_own_history": val_now.get("ps_percentile_own_history"),
                                                                    "ps_vs_sector_median": val_now.get("ps_vs_sector_median"), "ps_ratio_at_publication": val_pub.get("ps_ratio"),
                                                                    "snapshot_date": val_now.get("snapshot_date")},
        }

    def list_events(self, *, event_types: Optional[List[str]] = None, severity: Optional[str] = None, sector: Optional[str] = None, min_strength: Optional[float] = None,
                    min_market_cap: Optional[float] = None, max_market_cap: Optional[float] = None, since: Optional[date] = None, until: Optional[date] = None,
                    q: Optional[str] = None, sort: str = "strength", limit: int = 50, offset: int = 0, today: Optional[date] = None) -> Dict[str, Any]:
        today = today or date.today()
        where, params = ["e.method_version = %s"], [METHOD_VERSION]
        if event_types:
            where.append("e.event_type = ANY(%s)")
            params.append(event_types)
        if severity:
            where.append("e.severity = %s")
            params.append(severity)
        if min_strength is not None:
            where.append("e.strength >= %s")
            params.append(min_strength)
        if since:
            where.append("e.available_at >= %s")
            params.append(since)
        if until:
            where.append("e.available_at <= %s")
            params.append(until)
        if q:
            where.append("e.symbol LIKE %s")
            params.append(q.upper().replace("%", "") + "%")
        if sector:
            where.append("fd.sector = %s")
            params.append(sector)
        if min_market_cap is not None:
            where.append("(e.context->'valuation_latest'->>'market_cap')::numeric >= %s")
            params.append(min_market_cap)
        if max_market_cap is not None:
            where.append("(e.context->'valuation_latest'->>'market_cap')::numeric <= %s")
            params.append(max_market_cap)
        order = SORTS.get(sort, SORTS["strength"])
        base = f"""FROM fundamental_inflection_event e
            LEFT JOIN LATERAL (SELECT c.company_name FROM companies c WHERE c.symbol = e.symbol LIMIT 1) co ON true
            LEFT JOIN LATERAL (SELECT d.sector FROM daily_fundamentals d WHERE d.symbol = e.symbol AND d.sector IS NOT NULL ORDER BY d.date DESC LIMIT 1) fd ON true
            WHERE {' AND '.join(where)}"""
        total = self._query(f"SELECT COUNT(*) AS n {base}", params)[0]["n"]
        rows = self._query(f"""SELECT e.id, e.symbol, co.company_name, fd.sector, e.event_type, e.strength, e.severity, e.score, e.fiscal_year, e.fiscal_quarter, e.quarter_end, e.available_at,
                   e.availability_basis, e.source, e.data_quality, e.revision_count, e.method_version, e.explanation, e.metrics, e.context {base} ORDER BY {order} LIMIT %s OFFSET %s""",
                           params + [limit, offset])
        return {"method_version": METHOD_VERSION, "notice": NOTICE, "total": total, "count": len(rows), "offset": offset, "events": [self._shape(r, today) for r in rows]}

    def stock(self, symbol: str, today: Optional[date] = None) -> Dict[str, Any]:
        today = today or date.today()
        symbol = symbol.upper()
        ev = self._query("""SELECT e.id, e.symbol, NULL::text AS company_name, NULL::text AS sector, e.event_type, e.strength, e.severity, e.score, e.fiscal_year, e.fiscal_quarter, e.quarter_end,
                   e.available_at, e.availability_basis, e.source, e.data_quality, e.revision_count, e.method_version, e.explanation, e.metrics, e.context
            FROM fundamental_inflection_event e WHERE e.symbol = %s AND e.method_version = %s ORDER BY e.available_at DESC, e.strength DESC""", (symbol, METHOD_VERSION))
        feats = self._query("""SELECT quarter_end, fiscal_year, fiscal_quarter, available_at, availability_basis, metrics, score, data_quality, quality_notes, source, source_updated_at
            FROM fundamental_feature WHERE symbol = %s AND method_version = %s ORDER BY quarter_end""", (symbol, METHOD_VERSION))
        trend = []
        for f in feats:
            m = f["metrics"] or {}
            trend.append({"quarter_end": f["quarter_end"].isoformat(), "fiscal_year": f["fiscal_year"], "fiscal_quarter": f["fiscal_quarter"], "available_at": f["available_at"].isoformat(),
                          "availability_basis": f["availability_basis"], "revenue": m.get("revenue"), "operating_income": m.get("operating_income"), "net_income": m.get("net_income"),
                          "free_cash_flow": m.get("free_cash_flow"), "op_margin": m.get("op_margin"), "rev_yoy": m.get("rev_yoy"), "rev_accel_pp": m.get("rev_accel_pp"),
                          "cash_conversion": m.get("cash_conversion"), "score": float(f["score"]), "data_quality": f["data_quality"], "notes": f["quality_notes"] or []})
        latest = trend[-1] if trend else None
        return {"symbol": symbol, "method_version": METHOD_VERSION, "notice": NOTICE, "available": bool(feats), "latest_score": latest["score"] if latest else None,
                "latest_quarter_end": latest["quarter_end"] if latest else None, "events": [self._shape(r, today) for r in ev], "trend": trend,
                "data_status": {"quarters": len(trend), "partial_quarters": sum(1 for t in trend if t["data_quality"] != "ok"), "source": feats[-1]["source"] if feats else None}}

    def summary(self, days: int = 90, today: Optional[date] = None) -> Dict[str, Any]:
        today = today or date.today()
        by_type = self._query("SELECT event_type, severity, COUNT(*) AS n FROM fundamental_inflection_event WHERE method_version = %s AND available_at >= %s::date - %s GROUP BY 1, 2", (METHOD_VERSION, today, days))
        agg = self._query("SELECT (SELECT COUNT(*) FROM fundamental_feature WHERE method_version = %s) AS features, (SELECT COUNT(*) FROM fundamental_inflection_event WHERE method_version = %s) AS events, "
                          "(SELECT MAX(available_at) FROM fundamental_inflection_event WHERE method_version = %s) AS newest_event", (METHOD_VERSION,) * 3)[0]
        run = self._query("SELECT status, finished_at, asof, symbols_processed, events_new, errors FROM inflection_run_log WHERE method_version = %s ORDER BY id DESC LIMIT 1", (METHOD_VERSION,))
        types: Dict[str, Dict[str, int]] = {}
        for r in by_type:
            types.setdefault(r["event_type"], {"high": 0, "medium": 0, "low": 0})[r["severity"]] = r["n"]
        last = run[0] if run else None
        return {"method_version": METHOD_VERSION, "notice": NOTICE, "window_days": days, "available": bool(agg["features"]), "features": agg["features"], "events": agg["events"],
                "newest_event": agg["newest_event"].isoformat() if agg["newest_event"] else None,
                "by_type": [{"event_type": k, "label": LABELS.get(k, k), **v, "total": sum(v.values())} for k, v in sorted(types.items())],
                "last_run": ({"status": last["status"], "finished_at": last["finished_at"].isoformat() if last["finished_at"] else None, "asof": last["asof"].isoformat(),
                              "symbols_processed": last["symbols_processed"], "events_new": last["events_new"], "errors": last["errors"]} if last else None)}
