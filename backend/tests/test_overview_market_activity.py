"""Overview market activity (tiles + sector heatmap) = one Smart Screener v2 evaluation for one explicit session
(DiscoveryService.market_overview, served through MarketService.get_market_overview with the same response keys).

Real-Postgres part (self-skips without a superuser database; CI's db-bootstrap-integration job runs it): on the
throwaway market of test_overview_movers.py -- PSKY (newest bar the day before) is not counted, BRZE's unusual volume and
FLAT's overbought RSI from earlier sessions are not counted, the $1 population rule (no liquidity floor) is applied,
the sector membership is the Screener's, and missing averages are null.
Pure part: the service maps the engine payload to the existing keys without inventing values."""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import test_overview_movers as M  # noqa: E402  (the throwaway production-shaped database and its market)

from services import discovery_service as D  # noqa: E402
from services.market_service import MarketService  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_cache():
    import services.market_service as MS
    MS._overview_cache.update(watermark=None, at=0.0, data=None)
    yield
    MS._overview_cache.update(watermark=None, at=0.0, data=None)


@pytest.fixture(scope="module")
def db():
    with M._fresh_db() as connect:
        conn = connect()
        M._load(conn)
        cur = conn.cursor()
        # fundamentals ingested before the session's cutoff, so the Screener's (reference) sector applies
        cur.execute("UPDATE daily_fundamentals SET created_at = %s, updated_at = %s", (M.S, M.S))
        cur.execute("UPDATE technical_indicators SET rsi_14 = 75 WHERE symbol = 'UP' AND date = %s", (M.S,))
        cur.execute("UPDATE technical_indicators SET rsi_14 = 25 WHERE symbol = 'DOWN' AND date = %s", (M.S,))
        stale = M._sessions()[-4]
        cur.execute("INSERT INTO technical_indicators (symbol, date, volume_ratio, rsi_14) VALUES ('FLAT', %s, 1.0, 80)", (stale,))
        conn.commit()
        conn.close()
        yield connect


def test_counts_use_only_the_session_and_its_indicators(db):
    o = D.DiscoveryService(db).market_overview()
    b = o["breadth"]
    assert o["session"] == "2026-10-23"
    # population: bar on 10-23 and close >= $1 -> UP, DOWN, FLAT, VOL, BRZE, THIN, SHORT (no PSKY: newest bar 10-22; no PENNY: $0.80)
    assert b["total_stocks"] == 7 and o["universe"] == 8
    assert (b["gainers"], b["losers"], b["unchanged"]) == (3, 1, 3)          # UP, THIN, SHORT / DOWN / FLAT, VOL, BRZE
    assert b["unusual_volume_count"] == 3                                     # VOL 3x, THIN 5x, SHORT 4x -- not BRZE (7.81x was 3 sessions ago)
    assert (b["overbought_count"], b["oversold_count"], b["rsi_known"]) == (1, 1, 2)   # FLAT's RSI 80 was 3 sessions ago
    assert b["avg_rsi"] == pytest.approx(50.0)


def test_sector_heatmap_uses_the_screener_membership_and_the_same_population(db):
    o = D.DiscoveryService(db).market_overview()
    assert o["sectors"] == [{"sector": "Technology", "stock_count": 7, "avg_performance": pytest.approx(10.0), "up_count": 3}]


def test_the_market_service_keeps_its_keys_and_states_session_and_population(db):
    r = MarketService(db).get_market_overview()
    assert set(r["market_breadth"]) >= {"total_stocks", "gainers", "losers", "advance_decline_ratio", "unusual_volume_count",
                                        "overbought_count", "oversold_count"}
    assert r["market_breadth"]["advance_decline_ratio"] == 3.0
    assert r["session"] == "2026-10-23" and r["population"]["min_close"] == 1.0 and r["population"]["liquidity_floor"] is None
    assert r["sector_performance"][0]["sector"] == "Technology"


def test_missing_indicators_are_null_never_a_default(db):
    conn = db()
    conn.cursor().execute("DELETE FROM technical_indicators WHERE date = %s", (M.S,))
    try:
        r = MarketService(lambda: M._NoCommit(conn)).get_market_overview()
        assert r["market_indicators"]["avg_rsi"] is None and r["market_indicators"]["avg_volume_ratio"] is None   # not 50.0 / 1.0
        assert r["market_breadth"]["unusual_volume_count"] == 0 and r["coverage"]["rsi_known"] == 0
    finally:
        conn.rollback()
        conn.close()


def test_one_statement_however_many_symbols(db):
    conn = db()
    log = []
    D.DiscoveryService(lambda: M._Counting(conn, log)).market_overview()
    assert len(log) == 4                                      # read-only, newest date, session window, the one overview statement


# ------------------------------------------------------------------------------------------------------------- pure
class _Fake:
    def __init__(self, losers):
        self.losers = losers

    def market_overview(self):
        return {"session": "2026-10-23", "previous_session": "2026-10-22", "newest_price_date": "2026-10-23", "universe": 10,
                "breadth": {"total_stocks": 9, "gainers": 9, "losers": self.losers, "unchanged": 0, "change_known": 9,
                            "unusual_volume_count": 0, "volume_ratio_known": 0, "overbought_count": 0, "oversold_count": 0,
                            "rsi_known": 0, "avg_volume_ratio": None, "avg_rsi": None, "total_market_cap": None, "market_cap_known": 0},
                "sectors": [{"sector": "Energy", "stock_count": 9, "avg_performance": 1.234, "up_count": 9}]}


def test_no_losers_gives_no_ratio_and_missing_averages_stay_null(monkeypatch):
    monkeypatch.setattr(D.DiscoveryService, "market_overview", lambda self: _Fake(0).market_overview())
    r = MarketService(lambda: None).get_market_overview()
    assert r["market_breadth"]["advance_decline_ratio"] is None             # not gainers / 1
    assert r["market_indicators"] == {"avg_volume_ratio": None, "avg_rsi": None, "total_market_cap": None}
    assert r["sector_performance"] == [{"sector": "Energy", "stock_count": 9, "avg_performance": 1.23, "up_count": 9}]


def test_rsi_column_does_not_change_the_screener_rows():
    assert "rsi_14" not in D.ROW_COLUMNS


def test_the_tile_counts_equal_the_screener_views_they_link_to(db):
    svc = D.DiscoveryService(db)
    b = svc.market_overview()["breadth"]
    unusual = svc.search(D.Query(ranges={"close": (1.0, None), "volume_ratio": (2.0, None)}))
    assert unusual["total"] == b["unusual_volume_count"]                       # the tile's "See them in the Screener" view
    assert svc.search(D.Query(ranges={"close": (1.0, None)}))["total"] == b["total_stocks"]


def test_cached_only_while_no_newer_price_date_exists(monkeypatch):
    import services.market_service as MS
    calls = []
    monkeypatch.setattr(D.DiscoveryService, "market_overview", lambda self: calls.append(1) or dict(_Fake(5).market_overview()))
    MS._overview_cache.update(watermark=None, at=0.0, data=None)
    newest = {"d": "2026-10-23"}
    monkeypatch.setattr(MS.MarketService, "_newest_price_date", lambda self: newest["d"])
    svc = MS.MarketService(lambda: None)
    svc.get_market_overview()
    svc.get_market_overview()
    assert calls == [1]                                               # same newest price date: served from the cache
    newest["d"] = "2026-10-26"
    svc.get_market_overview()
    assert calls == [1, 1]                                            # a newer date: rebuilt, never the old session as newest
    newest["d"] = None
    svc.get_market_overview()
    assert calls == [1, 1, 1]                                         # watermark unreadable: nothing cached is trusted
    MS._overview_cache.update(watermark=None, at=0.0, data=None)
