"""MarketService.get_available_filters() on an empty database (no DB needed: fake connection).

Found on the staging VPS (fresh schema, no rows yet): MIN/MAX/PERCENTILE over zero rows return
NULL, and float(None) raised -- /api/screener/filters returned 500 and /api/health reported the
screener component unhealthy. Unknown must be None, never a crash and never an invented 0.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.market_service import MarketService  # noqa: E402

NULL_PRICE = {"min_price": None, "max_price": None, "price_25th": None, "price_median": None, "price_75th": None}
NULL_MCAP = {"min_market_cap": None, "max_market_cap": None, "mc_25th": None, "mc_median": None, "mc_75th": None}


class _Cursor:
    def __init__(self, fetchall_rows, fetchone_rows):
        self._all = list(fetchall_rows)
        self._one = list(fetchone_rows)

    def execute(self, *_a, **_k):
        pass

    def fetchall(self):
        return self._all

    def fetchone(self):
        return self._one.pop(0)

    def close(self):
        pass


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, cursor_factory=None):
        return self._cursor

    def close(self):
        pass


def _service(sectors, price, mcap):
    return MarketService(lambda: _Conn(_Cursor(sectors, [price, mcap])))


def test_empty_tables_give_unknown_not_a_crash_or_zero():
    out = _service([], NULL_PRICE, NULL_MCAP).get_available_filters()
    assert out["sectors"] == []
    assert out["price_ranges"] == {"min": None, "max": None,
                                   "percentiles": {"25th": None, "median": None, "75th": None}}
    assert out["market_cap_ranges"] == {"min": None, "max": None,
                                        "percentiles": {"25th": None, "median": None, "75th": None}}


def test_real_values_are_still_converted():
    price = {"min_price": 1.5, "max_price": 900, "price_25th": 12.0, "price_median": 40, "price_75th": 110.25}
    mcap = {"min_market_cap": 2.0e8, "max_market_cap": 3.1e12, "mc_25th": 1e9, "mc_median": 5e9, "mc_75th": 2e10}
    out = _service([{"sector": "Technology", "stock_count": 3}], price, mcap).get_available_filters()
    assert out["sectors"] == [{"name": "Technology", "stock_count": 3}]
    assert out["price_ranges"]["min"] == 1.5 and isinstance(out["price_ranges"]["max"], float)
    assert out["price_ranges"]["percentiles"]["75th"] == 110.25
    assert out["market_cap_ranges"]["max"] == 3_100_000_000_000 and isinstance(out["market_cap_ranges"]["min"], int)


def test_a_real_zero_is_kept_not_treated_as_missing():
    mcap = dict(NULL_MCAP, min_market_cap=0)
    out = _service([], NULL_PRICE, mcap).get_available_filters()
    assert out["market_cap_ranges"]["min"] == 0
