"""Smart Screener v2: session alignment, allowlisted sorting/filtering, bounded pagination, missing values kept missing,
read-only SQL, and the routes' validation/auth/CSV behaviour. No database: the SQL builder and the session resolution are
pure, and the routes are exercised with a fake service."""
import os
import re
import sys
from datetime import date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user  # noqa: E402
from routers.screener import get_discovery_service, screener_router  # noqa: E402
from services import discovery_service as D  # noqa: E402


# ------------------------------------------------------------------------------------------------------------ sessions
def _counts(days, n=3000, holes=()):
    return [(d, (100 if d in holes else n)) for d in days]


def test_session_resolution_uses_real_sessions_only_and_never_a_partial_date():
    days = [date(2026, 9, d) for d in range(1, 31) if date(2026, 9, d).weekday() < 5]
    partial = date(2026, 9, 30)
    s = D.resolve_sessions(_counts(days, holes=(partial,)), None)
    assert s["session"] == date(2026, 9, 29)                      # the half-loaded newest date is not a session
    assert s["previous_session"] == date(2026, 9, 28)
    with pytest.raises(D.InvalidRequest, match="not a market session"):
        D.resolve_sessions(_counts(days, holes=(partial,)), partial)
    with pytest.raises(D.InvalidRequest):
        D.resolve_sessions(_counts(days), date(2026, 9, 6))         # a Sunday


def test_dollar_volume_window_is_exactly_twenty_sessions_ending_at_the_session_or_none():
    days = [date(2026, 8, d) for d in range(1, 32) if date(2026, 8, d).weekday() < 5] + \
           [date(2026, 9, d) for d in range(1, 31) if date(2026, 9, d).weekday() < 5]
    s = D.resolve_sessions(_counts(days), date(2026, 9, 15))
    upto = [d for d in days if d <= date(2026, 9, 15)]
    assert s["dv_start"] == upto[-20] and len([d for d in upto if d >= s["dv_start"]]) == 20
    short = D.resolve_sessions(_counts(days[:10]), None)
    assert short["dv_start"] is None                               # fewer than 20 sessions: no dollar volume at all


def test_requested_session_is_used_as_is_never_moved_to_a_newer_one():
    days = [date(2026, 10, d) for d in (12, 13, 14, 15, 16)]
    s = D.resolve_sessions(_counts(days), date(2026, 10, 14))
    assert (s["session"], s["previous_session"]) == (date(2026, 10, 14), date(2026, 10, 13))
    assert s["recent_sessions"][0] == "2026-10-14"


def test_knowledge_cutoff_is_noon_new_york_the_day_after_in_naive_utc():
    assert D.knowledge_cutoff_utc(date(2026, 10, 16)) == datetime(2026, 10, 17, 16, 0)   # EDT
    assert D.knowledge_cutoff_utc(date(2026, 12, 4)) == datetime(2026, 12, 5, 17, 0)     # EST


# --------------------------------------------------------------------------------------------- validation and SQL build
def test_parse_accepts_only_allowlisted_parameters():
    q = D.parse_params([("min_rs_20", "80"), ("max_atr_pct", "6"), ("breakout_20", "true"), ("sector", "Technology"),
                        ("sector", "Energy"), ("q", "aa"), ("sort", "ret_20"), ("order", "asc"), ("page", "2"), ("page_size", "100"),
                        ("session", "2026-10-16")])
    assert q.ranges == {"rs_20": (80.0, None), "atr_pct": (None, 6.0)} and q.flags == {"breakout_20": True}
    assert q.sectors == ("Technology", "Energy") and q.session == date(2026, 10, 16) and (q.page, q.page_size) == (2, 100)
    for bad in ([("min_foo", "1")], [("drop", "1")], [("min_breakout_20", "1")], [("min_rs_20", "nan")], [("min_rs_20", "inf")],
                [("page", "x")], [("session", "16/10/2026")], [("breakout_20", "yes")]):
        with pytest.raises(D.InvalidRequest):
            D.parse_params(bad)


def test_normalise_rejects_bad_sort_order_preset_ranges_and_bounds():
    for kw in ({"sort": "close; drop table x"}, {"order": "sideways"}, {"preset": "moonshots"}, {"page": 0}, {"page_size": 0},
               {"page_size": D.MAX_EXPORT_ROWS + 1}, {"q": "A'B"}, {"q": "X" * 11}, {"ranges": {"rs_20": (90.0, 10.0)}},
               {"sectors": tuple("s" for _ in range(21))}):
        with pytest.raises(D.InvalidRequest):
            D.normalise(D.Query(**kw))


def test_presets_merge_under_explicit_filters():
    q = D.normalise(D.Query(preset="leaders", ranges={"rs_20": (90.0, None)}))
    assert q.ranges == {"rs_20": (90.0, None), "rs_60": (80.0, None)}       # the explicit 90 wins over the preset's 80
    q = D.normalise(D.Query(preset="new_highs"))
    assert q.flags == {"breakout_20": True}
    for key, p in D.PRESETS.items():
        assert p["sort"][0] in D.SORT_KEYS and p["sort"][1] in ("asc", "desc")
        D.normalise(D.Query(preset=key))                                       # every preset is valid against the allowlists


def test_where_clause_uses_only_allowlisted_columns_and_bound_values():
    q = D.normalise(D.Query(ranges={"dollar_vol_20": (5e6, None), "rs_60": (None, 99.0)}, flags={"new_high_20": False},
                            sectors=("Technology",), q="ab"))
    where, params = D.build_where(q)
    assert where == "dollar_vol_20 >= %s AND rs_60 <= %s AND new_high_20 IS FALSE AND sector = ANY(%s) AND symbol LIKE %s"
    assert params == [5e6, 99.0, ["Technology"], "AB%"]
    assert D.order_clause("atr_pct", "desc") == "atr_pct DESC NULLS LAST, symbol ASC"   # missing values never sort first
    with pytest.raises(AssertionError):
        D.order_clause("1; drop", "asc")


def test_bind_turns_positional_placeholders_into_named_ones_and_checks_the_count():
    sql, params = D._bind("SELECT %(s)s WHERE a >= %s AND b = ANY(%s)", {"s": 1}, [2, [3]])
    assert sql == "SELECT %(s)s WHERE a >= %(w0)s AND b = ANY(%(w1)s)" and params == {"s": 1, "w0": 2, "w1": [3]}
    with pytest.raises(AssertionError):
        D._bind("a = %s", {}, [])


def test_sql_is_read_only_pinned_to_one_session_and_bounded():
    sql = D.page_sql("TRUE", D.order_clause("rs_20", "desc"))
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|DROP|ALTER|GRANT|COPY)\b", sql, re.I)
    assert "CURRENT_DATE" not in sql and "NOW()" not in sql.upper()
    # every per-session table is read for exactly %(s)s / %(p)s, never "latest per symbol"
    assert "t.date = %(s)s" in sql and "t.date = %(p)s" in sql and "session_date = %(s)s" in sql and "date = %(s)s" in sql
    assert "provenance = 'observed'" in sql
    assert "d.date <= %(s)s" in sql and "<= %(cutoff)s" in sql                  # fundamentals known by the session only
    assert "LIMIT %(limit)s OFFSET %(offset)s" in sql
    assert "CASE WHEN dv.dv_bars = %(dv_n)s THEN dv.dollar_vol_20 END" in sql   # partial windows give no dollar volume
    assert D.MAX_PAGE_SIZE == 200 and D.MAX_EXPORT_ROWS == 5000


def test_definitions_cover_every_metric_with_a_family():
    d = D.definitions()
    assert {m["key"] for m in d["metrics"]} == set(D.METRICS)
    assert {m["family"] for m in d["metrics"]} == {D.OBSERVED, D.DERIVED, D.REFERENCE}
    assert all(m["definition"] for m in d["metrics"])
    assert {p["key"] for p in d["presets"]} == {"leaders", "new_highs", "tight_channels", "liquid_large_caps", "high_volatility", "strongest"}
    text = " ".join(m["definition"] for m in d["metrics"]).lower()
    for banned in ("buy", "sell", "predict", "forecast", "opportunit", "signal"):
        assert banned not in text


def test_csv_keeps_missing_values_empty_and_states_the_session():
    result = {"session": "2026-10-16", "total": 2, "truncated": False,
              "rows": [{"symbol": "AAA", "rs_20": 91.5, "market_cap": None}, {"symbol": "BBB", "rs_20": None}]}
    lines = D.to_csv(result).splitlines()
    assert "session 2026-10-16" in lines[0] and "Not investment advice" in lines[1]
    header = lines[2].split(",")
    assert header[0] == "symbol" and "rs_20" in header
    row_b = lines[4].split(",")
    assert row_b[0] == "BBB" and row_b[header.index("rs_20")] == ""            # missing stays empty, never 0


# ----------------------------------------------------------------------------------------------------------- the routes
class _FakeDiscovery:
    def __init__(self):
        self.calls = []

    def search(self, qr, export=False):
        self.calls.append((qr, export))
        qr = D.normalise(qr)
        if not export and qr.page_size > D.MAX_PAGE_SIZE:
            raise D.InvalidRequest("page_size must be at most 200")
        return {"session": "2026-10-16", "previous_session": "2026-10-15", "total": 1, "truncated": False, "page": qr.page,
                "page_size": qr.page_size, "rows": [{"symbol": "AAA", "rs_20": 90.0, "market_cap": None}]}


def _client(authenticated=True):
    app = FastAPI()
    app.include_router(screener_router)
    fake = _FakeDiscovery()
    app.dependency_overrides[get_auth_store] = lambda: object()
    app.dependency_overrides[get_discovery_service] = lambda: fake
    if authenticated:
        app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "collaborator")
    return TestClient(app), fake


def test_v2_routes_validate_and_map_errors_to_422():
    c, fake = _client()
    ok = c.get("/api/screener/v2/search?preset=leaders&sort=ret_20&page=2&page_size=50")
    assert ok.status_code == 200 and ok.json()["rows"][0]["symbol"] == "AAA"
    for qs in ("sort=evil", "order=up", "page_size=500", "min_unknown=1", "preset=nope", "session=2026-13-01", "bogus=1"):
        r = c.get(f"/api/screener/v2/search?{qs}")
        assert r.status_code == 422, qs
    assert c.get("/api/screener/v2/definitions").json()["limits"]["max_page_size"] == 200


def test_v2_export_ignores_paging_and_returns_csv():
    c, fake = _client()
    r = c.get("/api/screener/v2/export.csv?preset=liquid_large_caps&page=7&page_size=10")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="first-light-screener-2026-10-16.csv"' in r.headers["content-disposition"]
    qr, export = fake.calls[-1]
    assert export is True and (qr.page, qr.page_size) == (1, 50)                 # paging parameters were dropped
    assert "AAA" in r.text


def test_v2_routes_keep_authentication_and_are_get_only():
    anon, _ = _client(False)
    assert anon.get("/api/screener/v2/search").status_code == 401
    assert anon.get("/api/screener/v2/export.csv").status_code == 401
    c, _ = _client()
    assert c.post("/api/screener/v2/search").status_code == 405


# ------------------------------------------------------------------------- fundamentals provenance (ingestion-time data)
def test_fundamentals_are_labelled_ingestion_time_reference_not_point_in_time():
    b = D.fundamentals_basis(date(2026, 10, 16), "post_session")
    assert b["type"] == "ingestion_time_reference" and b["publication_time"] == "unknown" and b["point_in_time_verified"] is False
    assert b["cutoff_utc"] == "2026-10-17T16:00" and "Post-session reporting convention" in b["note"] and "Not an intraday" in b["note"]
    strict = D.fundamentals_basis(date(2026, 10, 16), "session_close")
    assert strict["cutoff_utc"] == "2026-10-16T20:00" == strict["session_close_utc"]           # 16:00 New York (EDT)
    assert D.session_close_utc(date(2026, 12, 4)) == datetime(2026, 12, 4, 21, 0)              # EST
    defs = D.definitions()
    assert "not verified point-in-time" in defs["families"][D.REFERENCE]
    assert "not verified point-in-time" in next(m["definition"] for m in defs["metrics"] if m["key"] == "market_cap")


def test_fundamentals_cutoff_is_allowlisted_and_reaches_the_query():
    assert D.parse_params([("fundamentals_cutoff", "session_close")]).fundamentals_cutoff == "session_close"
    with pytest.raises(D.InvalidRequest):
        D.normalise(D.parse_params([("fundamentals_cutoff", "whenever")]))
    sql = D.page_sql("TRUE", D.order_clause("rs_20", "desc"))
    assert "<= %(cutoff)s" in sql and "> %(close_utc)s" in sql and "AS ingested_at" in sql
    assert "fundamentals_after_close" in D.ROW_COLUMNS and "fundamentals_ingested_at" in D.ROW_COLUMNS


def test_csv_header_states_the_fundamentals_basis():
    result = {"session": "2026-10-16", "total": 0, "truncated": False, "rows": [],
              "fundamentals_basis": D.fundamentals_basis(date(2026, 10, 16), "session_close")}
    head = D.to_csv(result).splitlines()[1]
    assert "ingestion-time fundamentals (publication time unknown, not verified point-in-time)" in head
    assert "session_close 2026-10-16T20:00 UTC" in head


def test_v2_route_refuses_an_unknown_fundamentals_cutoff():
    c, _ = _client()
    assert c.get("/api/screener/v2/search?fundamentals_cutoff=session_close").status_code == 200
    assert c.get("/api/screener/v2/search?fundamentals_cutoff=tomorrow").status_code == 422


def test_reference_fields_for_momentum_today_come_from_the_same_rows_and_session():
    # P/E, P/S, quality and float come from the SAME fundamentals row as market cap (same cutoff and age rule); earnings
    # dates are relative to the session (next strictly after it, last on or before it); the company name is a plain join.
    for k in ("pe_ratio", "ps_ratio", "quality_score", "float_shares", "days_to_earnings", "days_since_earnings", "earnings_surprise_pct"):
        assert D.METRICS[k].family == D.REFERENCE and k in D.SORT_KEYS
    for k in ("company_name", "quality_grade", "next_earnings_date", "last_earnings_date"):
        assert k in D.ROW_COLUMNS
    sql = D.page_sql("TRUE", D.order_clause("pe_ratio", "asc"))
    fund = re.search(r"SELECT market_cap, sector, industry, date,.*?\) df ON true", sql, re.S).group(0)
    assert "pe_ratio, ps_ratio, overall_quality_score, quality_grade, float_shares" in fund and "<= %(cutoff)s" in fund
    assert "MIN(report_date) AS report_date FROM earnings_current WHERE is_current AND report_date > %(s)s GROUP BY symbol" in sql
    assert "WHERE is_current AND report_date <= %(s)s ORDER BY symbol, report_date DESC" in sql
    assert "(next_earnings_date - %(s)s::date) AS days_to_earnings" in sql
    assert "(%(s)s::date - last_earnings_date) AS days_since_earnings" in sql
    assert "not verified point-in-time" in D.METRICS["pe_ratio"].definition
