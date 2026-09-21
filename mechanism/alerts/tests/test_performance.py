"""Performance "since you added": every expected number below was worked out BY HAND (see the comments), never copied from the code.
Money is Decimal and rounds HALF-UP; an untrustworthy number is None, never a guess."""
import os
import sys
from datetime import date
from decimal import Decimal as Dec

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from alerts import performance as perf  # noqa: E402

REF = date(2026, 9, 14)                       # a Monday
LAST = date(2026, 9, 18)                      # the Friday of the last close


def row(sym="MSTR", kind="hold", price="140.00", shares="10", source="entered", ref=REF):
    return {"symbol": sym, "kind": kind, "ref_price": Dec(price), "ref_source": source, "ref_date": ref,
            "shares": None if shares is None else Dec(shares)}


def quote(close, prev=None, day=LAST):
    return {"date": day, "close": Dec(close), "prev_close": None if prev is None else Dec(prev)}


# ------------------------------------------------------------------ the arithmetic
def test_hand_computed_holding_to_the_cent():
    v = perf.build_view(row(), quote("153.92", "132.25"))
    # value 10 x 153.92 = 1539.20 ; cost 10 x 140.00 = 1400.00 ; change 139.20 ; since 13.92 / 140 = 9.942857...% ; day 21.67 / 132.25 = 16.385...%
    assert (v.value, v.cost, v.change) == (Dec("1539.20"), Dec("1400.00"), Dec("139.20"))
    assert perf.fmt_pct(v.since_pct) == "▲9.9%" and perf.fmt_pct(v.day_pct) == "▲16.4%"
    assert v.days == 4 and v.note is None and v.close == Dec("153.92")


def test_hand_computed_portfolio_totals_and_weights():
    a = perf.build_view(row("MSTR", price="140.00", shares="10"), quote("153.92"))      # cost 1400.00, value 1539.20
    b = perf.build_view(row("COIN", price="190.00", shares="5", ref=date(2026, 9, 16)), quote("194.25"))   # cost 950.00, value 971.25
    t = perf.portfolio_totals([a, b])
    # cost 2350.00 ; value 2510.45 ; change 160.45 ; 160.45 / 2350 = 6.8276...% ; weights 1539.20 / 2510.45 = 61.31% and 38.69%
    assert (t.cost, t.value, t.change) == (Dec("2350.00"), Dec("2510.45"), Dec("160.45"))
    assert perf.fmt_pct(t.change_pct) == "▲6.8%" and t.counted == 2 and t.left_out == 0
    assert t.largest == "MSTR" and perf.quantize(t.largest_weight_pct, Dec("0.01")) == Dec("61.31")
    assert perf.quantize(b.weight_pct, Dec("0.01")) == Dec("38.69")
    assert abs(a.weight_pct + b.weight_pct - 100) < Dec("0.0000001")                    # the weights add up


def test_fractional_shares_and_tiny_prices():
    v = perf.build_view(row(price="200.00", shares="0.5"), quote("210.00"))
    assert v.value == Dec("105.00") and v.cost == Dec("100.00") and v.change == Dec("5.00")        # 0.5 sh: 105.00 - 100.00
    tiny = perf.build_view(row(price="0.0500", shares="1000"), quote("0.0450"))
    assert tiny.cost == Dec("50.0000") and tiny.value == Dec("45.0000") and perf.fmt_pct(tiny.since_pct) == "▼10.0%"   # 0.045 / 0.05 - 1 = -10%


def test_a_loss_and_a_flat_position():
    down = perf.build_view(row(price="100.00", shares="3"), quote("97.90"))
    assert down.change == Dec("-6.30") and perf.fmt_pct(down.since_pct) == "▼2.1%" and perf.fmt_money(down.change) == "-$6.30"   # 293.70 - 300.00
    flat = perf.build_view(row(price="50.00", shares="2"), quote("50.00"))
    assert flat.change == Dec("0.00") and perf.fmt_pct(flat.since_pct) == "■0.0%"


def test_rounding_is_half_up_never_bankers_and_never_float():
    assert perf.fmt_pct(Dec("0.05")) == "▲0.1%" and perf.fmt_pct(Dec("0.15")) == "▲0.2%" and perf.fmt_pct(Dec("0.25")) == "▲0.3%"
    assert perf.fmt_pct(Dec("-2.05")) == "▼2.1%" and perf.fmt_pct(Dec("0.04")) == "■0.0%" and perf.fmt_pct(Dec("-0.04")) == "■0.0%"
    assert perf.fmt_money(Dec("2.675")) == "$2.68"                                     # float 2.675 would print 2.67
    assert perf.fmt_money(Dec("-0.004")) == "$0.00" and perf.fmt_money(Dec("1234567.891")) == "$1,234,567.89"
    assert perf.fmt_price(Dec("12.345")) == "$12.35" and perf.fmt_price(Dec("0.05")) == "$0.0500" and perf.fmt_price(Dec("1")) == "$1.00"
    assert perf.fmt_pct(None) == "n/a"


def test_shares_are_shown_without_trailing_zeros():
    assert perf.fmt_shares(Dec("10.000000")) == "10" and perf.fmt_shares(Dec("0.500000")) == "0.5" and perf.fmt_shares(Dec("1E+3")) == "1000"
    assert perf.fmt_shares(Dec("12.345678")) == "12.345678"


@pytest.mark.parametrize("ref,close,expected", [(REF, "150", 4), (date(2026, 9, 18), "150", 0), (date(2026, 9, 21), "150", 0)])
def test_days_since_added_never_go_negative(ref, close, expected):
    assert perf.build_view(row(ref=ref), quote(close)).days == expected                # added on a weekend after the last close -> 0


# ------------------------------------------------------------------ what is not trusted is None, not a guess
def test_no_price_means_no_numbers():
    for q in (None, {"date": LAST, "close": None, "prev_close": None}, quote("0")):
        v = perf.build_view(row(), q)
        assert v.note == perf.NOTE_NO_PRICE and v.since_pct is None and v.value is None and v.close is None


def test_a_jump_after_the_reference_date_is_a_split_not_a_return():
    facts = {"jump_dates": [date(2026, 9, 16)], "ref_close": None}
    v = perf.build_view(row(), quote("77.00", "76.00"), facts)                          # e.g. a 2-for-1 on the 16th: 153.9 -> 77.0
    assert v.note == perf.NOTE_ADJUSTED and v.since_pct is None and v.value is None and v.change is None and v.days is None
    assert v.close == Dec("77.00") and perf.fmt_pct(v.day_pct) == "▲1.3%"              # the last close and the day change are still true
    assert perf.portfolio_totals([v]) is None                                          # and it is kept out of the totals


def test_a_jump_before_the_reference_date_is_history():
    facts = {"jump_dates": [date(2026, 9, 10), REF], "ref_close": None}                # on or before the day added: the price already reflects it
    assert perf.build_view(row(), quote("150"), facts).note is None


def test_a_restated_reference_close_is_detected_but_only_for_close_based_references():
    stored_then = row(price="100.00", source="close", shares=None, kind="watch")
    assert perf.build_view(stored_then, quote("110"), {"jump_dates": [], "ref_close": Dec("50")}).note == perf.NOTE_ADJUSTED      # 100 -> 50 today
    assert perf.build_view(stored_then, quote("110"), {"jump_dates": [], "ref_close": Dec("96")}).note is None                    # 4%: within tolerance
    assert perf.build_view(stored_then, quote("110"), {"jump_dates": [], "ref_close": Dec("94")}).note == perf.NOTE_ADJUSTED     # 6%: not
    typed = row(price="100.00", source="entered", shares=None, kind="watch")
    assert perf.build_view(typed, quote("110"), {"jump_dates": [], "ref_close": Dec("50")}).note is None                         # a typed price is the user's own


def test_holdings_without_shares_or_a_usable_number_are_left_out_and_counted():
    ok = perf.build_view(row("A", price="10", shares="4"), quote("12"))                # cost 40, value 48
    no_shares = perf.build_view(row("B", shares=None), quote("150"))
    split = perf.build_view(row("C"), quote("77"), {"jump_dates": [date(2026, 9, 16)], "ref_close": None})
    gone = perf.build_view(row("D"), None)
    watch = perf.build_view(row("E", kind="watch", shares=None), quote("150"))
    t = perf.portfolio_totals([ok, no_shares, split, gone, watch])
    assert (t.cost, t.value, t.change, t.counted, t.left_out) == (Dec("40"), Dec("48"), Dec("8"), 1, 3)   # the watched stock is not a holding
    assert perf.fmt_pct(no_shares.since_pct) == "▲7.1%"                                 # 150 / 140 - 1 = 7.14%: its % since added is still shown
    assert perf.portfolio_totals([no_shares, watch]) is None


def test_watchlist_rows_never_get_value_cost_or_weight():
    v = perf.build_view(row(kind="watch", shares="10"), quote("153.92"))
    assert v.value is None and v.cost is None and v.change is None and v.since_pct is not None


def test_decimal_conversion_does_not_smuggle_float_noise():
    assert perf.D(0.1) == Dec("0.1") and perf.D("2") == Dec("2") and perf.D(None) is None and perf.D(Dec("1.10")) == Dec("1.10")
    assert perf.D(153.92) * 10 == Dec("1539.20")


@pytest.mark.parametrize("ratio,expected", [
    ("0.5", True), ("0.4999", True), ("0.52", False), ("0.515", True), ("0.485", True), ("0.48", False),        # 2-for-1 (+-3%)
    ("0.6667", True), ("0.66", True), ("0.7", False), ("0.64", False),                                          # 3-for-2
    ("0.3333", True), ("0.34", True), ("0.30", True), ("0.25", True), ("0.05", True),                           # 3-for-1 and bigger (hard rule below 0.323)
    ("2", True), ("2.05", True), ("2.1", False), ("1.94", True), ("1.9", False),                                # 1-for-2 reverse
    ("3", True), ("3.5", True), ("10", True), ("100", True),
    ("1", False), ("1.05", False), ("0.9", False), ("0.8", False), ("1.4", False), ("1.5", False), ("0.4", False), ("2.5", False),   # real moves
])
def test_which_close_to_close_ratios_count_as_a_split(ratio, expected):
    assert perf.is_split_like(Dec(ratio)) is expected


def test_split_detection_is_safe_on_junk():
    assert perf.is_split_like(None) is False and perf.is_split_like(0) is False and perf.is_split_like(-2) is False
