# mechanism/alerts/performance.py
"""
Performance "since you added" for the Watchlist and the Portfolio (BOT_DESIGN_REPORT.md section 5). Pure functions, no I/O, so the
arithmetic is asserted to the cent by test_performance.py.

Honesty rules (FRONTEND_FIX_MILESTONES.md section 0 applies to the bot too):
  * Money is computed with Decimal and rounded HALF-UP only when it is shown.
  * A number that cannot be trusted is None, never a guess: no stored close, or the price series was adjusted after the reference
    (split / vendor restatement) -> the caller shows "n/a" with the reason.
  * Facts only: this module never says what to do with a position.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional

# A close-to-close move is treated as a split / adjustment (not a price move) when it is beyond ~3x, or lands on a COMMON split factor:
# 1/3 (3-for-1), 1/2 (2-for-1), 2/3 (3-for-2), 2 and 3 (reverse splits) within SPLIT_TOLERANCE. Anything else stays a real move, because
# a wrongly-flagged crash only shows "n/a - check your broker", whereas a missed split shows a fabricated return.
SPLIT_FACTORS = (Decimal(1) / 3, Decimal(1) / 2, Decimal(2) / 3, Decimal(2), Decimal(3))
SPLIT_TOLERANCE = Decimal("0.03")
HARD_JUMP_HIGH, HARD_JUMP_LOW = Decimal("3.09"), Decimal("0.323")
RESTATE_TOLERANCE = Decimal("0.05")     # a stored reference close that differs >5% from today's series for that day was restated
CENT = Decimal("0.01")

NOTE_NO_PRICE = "no_price"
NOTE_ADJUSTED = "adjusted"


def D(x) -> Optional[Decimal]:
    """float / int / str / Decimal -> Decimal (floats go through str so 0.1 stays 0.1). None stays None."""
    if x is None:
        return None
    return x if isinstance(x, Decimal) else Decimal(str(x))


def quantize(x: Decimal, exp: Decimal = CENT) -> Decimal:
    return x.quantize(exp, rounding=ROUND_HALF_UP)


def pct_change(now: Decimal, ref: Decimal) -> Decimal:
    """(now / ref - 1) * 100, unrounded. ref must be > 0."""
    return (now / ref - 1) * 100


def is_split_like(ratio) -> bool:
    """True when a close / previous-close ratio looks like a split or an adjustment rather than a price move."""
    r = D(ratio)
    if r is None or r <= 0:
        return False
    if r > HARD_JUMP_HIGH or r < HARD_JUMP_LOW:
        return True
    return any(abs(r / f - 1) <= SPLIT_TOLERANCE for f in SPLIT_FACTORS)


def is_adjusted(row: Dict, facts: Optional[Dict]) -> bool:
    """True when the stored price series no longer measures the same thing the user's reference price did.
    `facts` = {"jump_dates": [date, ...] (close-to-close jumps / known discontinuities in the stored series),
               "ref_close": Decimal | None (today's stored close on the reference date)}."""
    if not facts:
        return False
    ref_date = row["ref_date"]
    if any(d > ref_date for d in facts.get("jump_dates", [])):
        return True
    ref_close = D(facts.get("ref_close"))
    if row["ref_source"] == "close" and ref_close and ref_close > 0:
        return abs(ref_close / D(row["ref_price"]) - 1) > RESTATE_TOLERANCE
    return False


@dataclass
class PositionView:
    symbol: str
    kind: str                            # 'watch' | 'hold'
    ref_price: Decimal
    ref_source: str                      # 'entered' | 'close'
    ref_date: date
    shares: Optional[Decimal]
    close: Optional[Decimal] = None
    quote_date: Optional[date] = None
    day_pct: Optional[Decimal] = None
    since_pct: Optional[Decimal] = None
    days: Optional[int] = None
    note: Optional[str] = None           # None | NOTE_NO_PRICE | NOTE_ADJUSTED
    cost: Optional[Decimal] = None       # shares x ref_price
    value: Optional[Decimal] = None      # shares x close
    change: Optional[Decimal] = None     # value - cost
    weight_pct: Optional[Decimal] = None


def build_view(row: Dict, quote: Optional[Dict], facts: Optional[Dict] = None) -> PositionView:
    v = PositionView(row["symbol"], row["kind"], D(row["ref_price"]), row["ref_source"], row["ref_date"], D(row.get("shares")))
    if not quote or quote.get("close") is None or D(quote["close"]) <= 0:
        v.note = NOTE_NO_PRICE
        return v
    close = D(quote["close"])
    v.close, v.quote_date = close, quote["date"]
    prev = D(quote.get("prev_close"))
    if prev and prev > 0:
        v.day_pct = pct_change(close, prev)
    if is_adjusted(row, facts):
        v.note = NOTE_ADJUSTED                                     # the last close is still right; everything measured from the reference is not
        return v
    v.since_pct = pct_change(close, v.ref_price)
    v.days = max(0, (v.quote_date - v.ref_date).days)
    if v.kind == "hold" and v.shares:
        v.cost, v.value = v.shares * v.ref_price, v.shares * close
        v.change = v.value - v.cost
    return v


@dataclass
class Totals:
    cost: Decimal
    value: Decimal
    change: Decimal
    change_pct: Optional[Decimal]
    counted: int                         # positions inside the totals
    left_out: int                        # holdings without shares or without a trustworthy number
    largest: Optional[str] = None
    largest_weight_pct: Optional[Decimal] = None


def portfolio_totals(views: List[PositionView]) -> Optional[Totals]:
    """Totals over the holdings that have shares AND a trustworthy value; also fills each view's weight_pct. None if nothing counts."""
    holds = [v for v in views if v.kind == "hold"]
    counted = [v for v in holds if v.value is not None and v.cost is not None]
    if not counted:
        return None
    cost, value = sum((v.cost for v in counted), Decimal(0)), sum((v.value for v in counted), Decimal(0))
    for v in counted:
        v.weight_pct = v.value / value * 100 if value > 0 else None
    top = max(counted, key=lambda v: v.value)
    return Totals(cost, value, value - cost, pct_change(value, cost) if cost > 0 else None, len(counted), len(holds) - len(counted),
                  top.symbol, top.weight_pct)


# ------------------------------------------------------------------ display helpers (rounding happens here, HALF-UP)
def fmt_money(x: Decimal) -> str:
    q = quantize(x)
    if q == 0:
        q = abs(q)                                               # a rounded-away loss must not print as "$-0.00"
    return f"-${abs(q):,.2f}" if q < 0 else f"${q:,.2f}"


def fmt_price(x: Decimal) -> str:
    """Prices below $1 keep 4 decimals (as the channel does); the rest 2."""
    return f"${quantize(x, Decimal('0.0001')):,.4f}" if x < 1 else f"${quantize(x):,.2f}"


def fmt_shares(x: Decimal) -> str:
    s = f"{x.normalize():f}"
    return s if "." not in s else s.rstrip("0").rstrip(".")


def fmt_pct(x: Optional[Decimal], signed_arrow: bool = True) -> str:
    """'▲9.9%' / '▼2.1%' / '■0.0%' (the same arrows the channel uses); 'n/a' for None."""
    if x is None:
        return "n/a"
    q = quantize(x, Decimal("0.1"))
    if not signed_arrow:
        return f"{q:+.1f}%"
    return f"{'▲' if q > 0 else '▼' if q < 0 else '■'}{abs(q):.1f}%"
