"""DeepValueService balance-sheet checks, turnaround rule and scan filtering (no DB needed).

The old value-trap filter was daily_fundamentals.financial_health_score (market cap + P/B only).
These pin the replacement: a stock is excluded only when a check FAILS, missing inputs are
'unknown' (never assumed pass/fail), and a turnaround requires adjacent, non-stale quarters.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import deep_value_service as dvs  # noqa: E402
from services.deep_value_service import (  # noqa: E402
    DeepValueService,
    assess_balance_sheet,
    assess_turnaround,
    find_year_ago_quarter,
)


def _q(quarter=date(2026, 6, 30), **overrides):
    row = {
        "quarter": quarter,
        "net_income": 5e6,
        "total_debt": 50e6,
        "total_equity": 100e6,
        "current_assets": 80e6,
        "current_liabilities": 40e6,
        "cash": 30e6,
        "operating_cash_flow": 10e6,
        "free_cash_flow": 6e6,
    }
    row.update(overrides)
    return row


# ---- assess_balance_sheet -------------------------------------------------

def test_healthy_balance_sheet_passes_all_checks():
    r = assess_balance_sheet(_q(), "Technology")
    assert r["checks"] == {"equity": "pass", "leverage": "pass", "liquidity": "pass", "cash_flow": "pass"}
    assert r["values"]["debt_to_equity"] == 0.5
    assert r["values"]["current_ratio"] == 2.0
    assert r["quarter"] == "2026-06-30"


def test_negative_equity_with_cash_burn_fails_equity_and_leverage():
    r = assess_balance_sheet(_q(total_equity=-10e6, operating_cash_flow=-1e6), "Technology")
    assert r["checks"]["equity"] == "fail"
    assert r["checks"]["leverage"] == "fail"
    assert r["values"]["debt_to_equity"] is None  # ratio meaningless with negative equity, not reported


def test_negative_equity_with_positive_cash_flow_only_warns():
    # buyback-funded negative equity (MCD-style) while generating cash is not distress
    r = assess_balance_sheet(_q(total_equity=-10e6), "Consumer Cyclical")
    assert r["checks"]["equity"] == "warn"
    assert r["checks"]["leverage"] == "warn"


def test_high_leverage_boundary_and_cash_flow_dependence():
    assert assess_balance_sheet(_q(total_debt=200e6), "Utilities")["checks"]["leverage"] == "pass"  # exactly 2.0
    assert assess_balance_sheet(_q(total_debt=201e6), "Utilities")["checks"]["leverage"] == "warn"
    assert assess_balance_sheet(_q(total_debt=201e6, operating_cash_flow=0), "Utilities")["checks"]["leverage"] == "fail"


def test_breach_with_unknown_cash_flow_warns_not_fails():
    r = assess_balance_sheet(_q(total_debt=300e6, operating_cash_flow=None), "Industrials")
    assert r["checks"]["leverage"] == "warn"
    assert r["checks"]["cash_flow"] == "unknown"


def test_current_ratio_below_one():
    assert assess_balance_sheet(_q(current_assets=39e6), "Industrials")["checks"]["liquidity"] == "warn"
    r = assess_balance_sheet(
        _q(current_assets=39e6, operating_cash_flow=-1e6, free_cash_flow=-1e6, cash=30e6), "Industrials"
    )
    assert r["checks"]["liquidity"] == "fail"
    assert r["checks"]["cash_flow"] == "pass"  # 30 quarters of runway, yet current assets < current liabilities


def test_missing_inputs_are_unknown_not_pass_or_fail():
    r = assess_balance_sheet(
        _q(total_debt=None, current_assets=None, operating_cash_flow=None), "Industrials"
    )
    assert r["checks"]["leverage"] == "unknown"
    assert r["checks"]["liquidity"] == "unknown"
    assert r["checks"]["cash_flow"] == "unknown"


def test_no_quarterly_row_is_all_unknown():
    r = assess_balance_sheet(None, "Technology")
    assert set(r["checks"].values()) == {"unknown"}
    assert r["quarter"] is None


def test_financial_services_leverage_and_liquidity_not_applicable():
    r = assess_balance_sheet(_q(total_debt=900e6, current_assets=None), "Financial Services")
    assert r["checks"]["leverage"] == "n/a"
    assert r["checks"]["liquidity"] == "n/a"
    assert r["checks"]["equity"] == "pass"


def test_cash_burn_with_enough_runway_passes():
    # burning 5M FCF/quarter, 20M cash = exactly 4 quarters
    r = assess_balance_sheet(_q(operating_cash_flow=-3e6, free_cash_flow=-5e6, cash=20e6), "Healthcare")
    assert r["checks"]["cash_flow"] == "pass"
    assert r["values"]["cash_runway_quarters"] == 4.0


def test_cash_burn_with_short_runway_fails():
    r = assess_balance_sheet(_q(operating_cash_flow=-3e6, free_cash_flow=-5e6, cash=19e6), "Healthcare")
    assert r["checks"]["cash_flow"] == "fail"


def test_cash_burn_without_cash_data_is_unknown():
    r = assess_balance_sheet(_q(operating_cash_flow=-3e6, free_cash_flow=-5e6, cash=None), "Healthcare")
    assert r["checks"]["cash_flow"] == "unknown"


# ---- assess_turnaround ----------------------------------------------------

AS_OF = date(2026, 9, 23)


def test_turnaround_loss_to_profit_adjacent_quarters():
    assert assess_turnaround(_q(net_income=1e6), _q(date(2026, 3, 31), net_income=-1e6), AS_OF)


def test_no_turnaround_when_quarters_not_adjacent():
    # a missing quarter in between -> comparing Dec to Jun is not "just flipped"
    assert not assess_turnaround(_q(net_income=1e6), _q(date(2025, 12, 31), net_income=-1e6), AS_OF)


def test_no_turnaround_when_latest_quarter_stale():
    latest = _q(date(2026, 1, 31), net_income=1e6)
    previous = _q(date(2025, 10, 31), net_income=-1e6)
    assert not assess_turnaround(latest, previous, AS_OF)


def test_no_turnaround_when_net_income_missing_or_not_a_flip():
    prev = _q(date(2026, 3, 31), net_income=-1e6)
    assert not assess_turnaround(_q(net_income=None), prev, AS_OF)
    assert not assess_turnaround(_q(net_income=-1e6), prev, AS_OF)
    assert not assess_turnaround(_q(net_income=1e6), _q(date(2026, 3, 31), net_income=1e6), AS_OF)
    assert not assess_turnaround(_q(net_income=1e6), None, AS_OF)


# ---- find_year_ago_quarter -----------------------------------------------

def test_year_ago_quarter_picks_closest_to_one_year():
    quarters = [
        _q(date(2026, 6, 30)), _q(date(2026, 3, 31)), _q(date(2025, 12, 31)),
        _q(date(2025, 9, 30)), _q(date(2025, 6, 30), net_income=7e6), _q(date(2025, 3, 31)),
    ]
    ya = find_year_ago_quarter(quarters)
    assert ya["quarter"] == date(2025, 6, 30) and ya["net_income"] == 7e6


def test_year_ago_quarter_missing_is_none():
    assert find_year_ago_quarter([_q(), _q(date(2026, 3, 31))]) is None
    assert find_year_ago_quarter([_q(), _q(date(2025, 6, 30), net_income=None)]) is None
    assert find_year_ago_quarter([]) is None


# ---- scan -----------------------------------------------------------------

def _row(symbol, price, low=100.0, valuation=15.0, sector="Technology", health=20.0):
    return {
        "symbol": symbol, "sector": sector, "market_cap": 1e9, "pe_ratio": 12.0,
        "valuation_score": valuation, "financial_health_score": health,
        "overall_quality_score": 70.0, "quality_grade": "C",
        "donchian_low_12m": low, "current_price": price, "price_date": AS_OF,
    }


def test_scan_filters_splits_and_counts_exclusions(monkeypatch):
    rows = [
        _row("TURN", 105.0),
        _row("WATCH", 110.0),
        _row("TRAP", 102.0),
        _row("FAR", 150.0),  # 50% above low -> not near lows
        _row("PRICEY", 101.0, valuation=5.0),
    ]
    quarters = {
        "TURN": [_q(net_income=2e6), _q(date(2026, 3, 31), net_income=-2e6), _q(date(2025, 6, 30), net_income=9e6)],
        "WATCH": [_q(), _q(date(2026, 3, 31))],
        "TRAP": [_q(total_equity=-1e6, operating_cash_flow=-1e6), _q(date(2026, 3, 31))],
        "FAR": [_q(), _q(date(2026, 3, 31))],
        "PRICEY": [_q(), _q(date(2026, 3, 31))],
    }
    svc = DeepValueService(lambda: None)
    monkeypatch.setattr(svc, "_get_raw_data", lambda: (rows, quarters))

    out = svc.scan(near_low_pct=15.0, min_valuation_score=12.0)

    assert [e["symbol"] for e in out["turnaround_alerts"]] == ["TURN"]
    assert [e["symbol"] for e in out["deep_value_watch"]] == ["WATCH"]
    assert out["excluded_balance_sheet"] == 1
    turn = out["turnaround_alerts"][0]
    assert turn["previous_net_income"] == -2e6 and turn["latest_quarter"] == "2026-06-30"
    assert turn["price_date"] == "2026-09-23"
    assert turn["year_ago_quarter"] == "2025-06-30" and turn["year_ago_net_income"] == 9e6


def test_scan_keeps_stock_with_unknown_checks(monkeypatch):
    svc = DeepValueService(lambda: None)
    monkeypatch.setattr(svc, "_get_raw_data", lambda: ([_row("NODATA", 101.0)], {}))
    out = svc.scan()
    assert [e["symbol"] for e in out["deep_value_watch"]] == ["NODATA"]
    assert set(out["deep_value_watch"][0]["balance_sheet"]["checks"].values()) == {"unknown"}
    assert out["excluded_balance_sheet"] == 0


def test_thresholds_match_page_copy():
    # frontend/src/app/alerts/page.tsx states these numbers verbatim
    page = open(
        os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src", "app", "alerts", "page.tsx"),
        encoding="utf-8",
    ).read()
    assert f"≤ {dvs.MAX_DEBT_TO_EQUITY}" in page
    assert f"≥ {dvs.MIN_CURRENT_RATIO}" in page
    assert f"cash ≥ {int(dvs.MIN_CASH_RUNWAY_QUARTERS)} quarters" in page


# ---- API compatibility with the previously deployed frontend --------------------------------------------

def test_response_keeps_financial_health_score_unchanged(monkeypatch):
    svc = DeepValueService(lambda: None)
    monkeypatch.setattr(svc, "_get_raw_data", lambda: ([_row("A", 101.0, health=17.0)], {}))
    entry = svc.scan()["deep_value_watch"][0]
    assert entry["financial_health_score"] == 17.0  # passed through, never recomputed
    assert entry["valuation_score"] == 15.0


def test_old_health_floor_applies_only_when_passed(monkeypatch):
    rows = [_row("HI", 101.0, health=20.0), _row("LO", 101.0, health=5.0), _row("NONE", 101.0, health=None)]
    svc = DeepValueService(lambda: None)
    monkeypatch.setattr(svc, "_get_raw_data", lambda: (rows, {}))
    assert {e["symbol"] for e in svc.scan()["deep_value_watch"]} == {"HI", "LO", "NONE"}
    # old frontend always sent min_financial_health_score=10; old SQL also dropped NULL scores
    assert {e["symbol"] for e in svc.scan(min_financial_health_score=10)["deep_value_watch"]} == {"HI"}


def _client(rows):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from auth.dependencies import CurrentUser, get_auth_store, require_authenticated_user
    from routers.deep_value import deep_value_router, get_deep_value_service

    svc = DeepValueService(lambda: None)
    svc._get_raw_data = lambda: (rows, {})
    app = FastAPI()
    app.include_router(deep_value_router)
    app.dependency_overrides[get_auth_store] = lambda: object()
    app.dependency_overrides[require_authenticated_user] = lambda: CurrentUser(1, "t@example.com", "owner")
    app.dependency_overrides[get_deep_value_service] = lambda: svc
    return TestClient(app)


def test_http_old_frontend_request_keeps_old_contract():
    # exactly what the currently deployed frontend sends
    rows = [_row("HI", 101.0, health=20.0), _row("LO", 101.0, health=5.0)]
    r = _client(rows).get("/api/deep-value/scan?near_low_pct=15&min_valuation_score=12&min_financial_health_score=10")
    assert r.status_code == 200
    body = r.json()
    assert {"turnaround_alerts", "deep_value_watch"} <= body.keys()
    watch = body["deep_value_watch"]
    assert [e["symbol"] for e in watch] == ["HI"]
    old_fields = {"symbol", "sector", "current_price", "distance_from_low_pct", "donchian_low_12m", "valuation_score",
                  "financial_health_score", "overall_quality_score", "quality_grade", "pe_ratio", "market_cap",
                  "is_turnaround"}
    assert old_fields <= watch[0].keys()  # every field the old page reads is still there


def test_http_new_frontend_request():
    rows = [_row("HI", 101.0, health=20.0), _row("LO", 101.0, health=5.0)]
    r = _client(rows).get("/api/deep-value/scan?near_low_pct=15&min_valuation_score=12")
    assert r.status_code == 200
    body = r.json()
    assert {e["symbol"] for e in body["deep_value_watch"]} == {"HI", "LO"}  # no health floor when not sent
    assert "excluded_balance_sheet" in body and "balance_sheet" in body["deep_value_watch"][0]


def test_http_rejects_out_of_range_deprecated_param():
    assert _client([]).get("/api/deep-value/scan?min_financial_health_score=99").status_code == 422


def test_the_page_makes_no_predictive_or_conviction_claim_and_states_the_screen_is_descriptive():
    """The one historical test (docs/research/2026-10-03_alerts-deep-value-backtest) found the page rule lagging the universe: the copy must
    stay descriptive. A reintroduced hype word fails here."""
    page = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src", "app", "alerts", "page.tsx")
    with open(page, encoding="utf-8") as f:
        text = f.read().lower()
    for banned in ("conviction", "🚀", "strong buy", "will outperform", "guaranteed", "high-probability", "edge for"):
        assert banned not in text, banned
    assert "not a prediction or a recommendation" in text and "did not outperform" in text
