# mechanism/alerts/tracker.py
"""
Watchlist + Portfolio of the private assistant (BOT_DESIGN_REPORT.md). Framework-free and I/O-free apart from the Store it is given,
so it is unit-tested with an in-memory store. The service returns STRUCTURED results (codes + numbers); wording lives in screens.py.

Rules:
  * A stock can be added if it is in the latest daily scan (liquid US stocks) - the same universe the channel lists come from.
  * ONE row per (user, symbol): a stock is watched OR held. Moving a watched stock to the portfolio keeps its first-added facts.
  * The reference price is what the user typed, or the last close when they did not ('entered' / 'close' is stored, so the screens can
    say which). Nothing is defaulted silently.
  * Every store call is scoped by the Telegram user id (isolation is tested, including with hostile symbols).
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from alerts import performance as perf
from alerts.performance import D, PositionView, Totals

CAP_WATCH = 25
CAP_HOLD = 25
PRICE_MIN, PRICE_MAX = Decimal("0.0001"), Decimal("10000000")
SHARES_MIN, SHARES_MAX = Decimal("0.000001"), Decimal("1000000000")
SYMBOL_TOKEN = re.compile(r"^\$?[A-Za-z][A-Za-z0-9.\-]{0,9}$")
NUMBER_TOKEN = re.compile(r"^\$?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?$|^\$?\.[0-9]+$")   # ASCII digits only; "1,405.50" ok, "1e9" not


def parse_number(token: str) -> Optional[Decimal]:
    """'140.5' / '$1,405.50' -> Decimal; anything else (exponent, comma decimals, words, unicode digits, nan) -> None."""
    tok = (token or "").strip()
    if not NUMBER_TOKEN.match(tok):
        return None
    try:
        return Decimal(tok.replace("$", "").replace(",", ""))
    except InvalidOperation:                                          # pragma: no cover - the regex already guarantees a valid number
        return None


@dataclass
class AddArgs:
    symbols: List[str]
    price: Optional[Decimal] = None
    shares: Optional[Decimal] = None
    error: Optional[str] = None                                       # 'no_symbol' | 'bad_number' | 'too_many_numbers' | 'price_needs_one_symbol'


def parse_add_args(text: Optional[str], symbol_limit: int = 25) -> AddArgs:
    """'AAPL' / 'AAPL MSFT COIN' -> watch each; 'AAPL 140.5' -> watch at that price; 'AAPL 140.5 10' -> hold 10 shares at 140.5."""
    symbols: List[str] = []
    numbers: List[Decimal] = []
    for tok in re.split(r"[\s;]+", (text or "").strip()):
        if not tok:
            continue
        if SYMBOL_TOKEN.match(tok):
            s = tok.replace("$", "").upper().rstrip(".-")
            if s and s not in symbols:
                symbols.append(s)
        elif (n := parse_number(tok)) is not None:
            numbers.append(n)
        else:
            return AddArgs([], error="bad_number")
    if not symbols:
        return AddArgs([], error="no_symbol")
    if len(numbers) > 2:
        return AddArgs(symbols, error="too_many_numbers")
    if numbers and len(symbols) > 1:
        return AddArgs(symbols, error="price_needs_one_symbol")
    price = numbers[0] if numbers else None
    shares = numbers[1] if len(numbers) > 1 else None
    if (price is not None and not PRICE_MIN <= price <= PRICE_MAX) or (shares is not None and not SHARES_MIN <= shares <= SHARES_MAX):
        return AddArgs(symbols[:symbol_limit], error="bad_number")
    return AddArgs(symbols[:symbol_limit], price, shares)


def parse_price_shares(text: Optional[str]) -> Tuple[Optional[Decimal], Optional[Decimal], Optional[str]]:
    """The reply to the portfolio prompt: '140.5' or '140.5 10' -> (price, shares, None); otherwise (None, None, 'bad_number')."""
    toks = (text or "").split()
    if not 1 <= len(toks) <= 2:
        return None, None, "bad_number"
    nums = [parse_number(t) for t in toks]
    if any(n is None for n in nums):
        return None, None, "bad_number"
    price, shares = nums[0], nums[1] if len(nums) > 1 else None
    if not PRICE_MIN <= price <= PRICE_MAX or (shares is not None and not SHARES_MIN <= shares <= SHARES_MAX):
        return None, None, "bad_number"
    return price, shares, None


@dataclass
class AddResult:
    code: str                    # ok | updated | already | already_held | unknown_symbol | no_snapshot | no_price | cap_full | bad_number
    symbol: str = ""
    kind: str = ""
    ref_price: Optional[Decimal] = None
    ref_source: str = ""
    last_close: Optional[Decimal] = None
    shares: Optional[Decimal] = None
    moved_from_watch: bool = False


class TrackerService:
    def __init__(self, store, today: Optional[Callable[[], date]] = None, tz: str = "Asia/Jerusalem"):
        self.store = store
        self._today = today or (lambda: datetime.now(ZoneInfo(tz)).date())

    # ------------------------------------------------------------------ adding
    def _last_close(self, symbol: str, snapshot_row: Optional[Dict]) -> Optional[Decimal]:
        q = self.store.quotes([symbol]).get(symbol)
        if q and q.get("close") is not None:
            return D(q["close"])
        return D(snapshot_row["close"]) if snapshot_row and snapshot_row.get("close") is not None else None

    def _add(self, uid: int, symbol: str, kind: str, price: Optional[Decimal], shares: Optional[Decimal]) -> AddResult:
        """Semantics: new -> 'ok'; watch -> hold = a move ('ok', moved_from_watch); same kind again with a price or shares = 'updated'
        (the reference restarts today); same kind again with nothing new = 'already' (no write); a holding is never downgraded to the
        watchlist ('already_held')."""
        session = self.store.latest_session()
        if not session:
            return AddResult("no_snapshot", symbol)
        row = self.store.stocks(session["session_date"], [symbol]).get(symbol)
        if not row:
            return AddResult("unknown_symbol", symbol)
        last = self._last_close(symbol, row)
        if price is not None and not PRICE_MIN <= price <= PRICE_MAX:
            return AddResult("bad_number", symbol, kind)
        if price is None and last is None:
            return AddResult("no_price", symbol, kind)
        current = {r["symbol"]: r for r in self.store.tracked(uid)}
        existing = current.get(symbol)
        if existing and existing["kind"] == "hold" and kind == "watch":
            return AddResult("already_held", symbol, "hold", D(existing["ref_price"]), existing["ref_source"], last, D(existing.get("shares")))
        if existing and existing["kind"] == kind and price is None and shares is None:
            return AddResult("already", symbol, kind, D(existing["ref_price"]), existing["ref_source"], last, D(existing.get("shares")))
        if not existing or existing["kind"] != kind:                    # a new row, or a watch -> hold move: both can hit the cap
            if sum(1 for r in current.values() if r["kind"] == kind) >= (CAP_WATCH if kind == "watch" else CAP_HOLD):
                return AddResult("cap_full", symbol, kind)
        ref = price if price is not None else last
        source = "entered" if price is not None else "close"
        moved = bool(existing and existing["kind"] == "watch" and kind == "hold")
        final_shares = None
        if kind == "hold":                                              # re-pricing a holding must not wipe the shares already saved
            final_shares = shares if shares is not None else (D(existing.get("shares")) if existing and existing["kind"] == "hold" else None)
        self.store.upsert_tracked(uid, symbol, kind, ref, source, self._today(), final_shares,
                                  first_price=D(existing["first_price"]) if existing else ref)
        return AddResult("updated" if existing and not moved else "ok", symbol, kind, ref, source, last, final_shares,
                         moved_from_watch=moved)

    def add_watch(self, uid: int, symbol: str, price: Optional[Decimal] = None) -> AddResult:
        return self._add(uid, symbol, "watch", price, None)

    def add_hold(self, uid: int, symbol: str, price: Optional[Decimal] = None, shares: Optional[Decimal] = None) -> AddResult:
        return self._add(uid, symbol, "hold", price, shares)

    def remove(self, uid: int, symbol: Optional[str]) -> int:
        """symbol=None removes everything the user tracks."""
        return self.store.remove_tracked(uid, None if symbol is None else [symbol])

    def delete_everything(self, uid: int) -> int:
        return self.store.remove_tracked(uid, None)

    # ------------------------------------------------------------------ reading
    def get(self, uid: int, symbol: str) -> Optional[Dict]:
        return next((r for r in self.store.tracked(uid) if r["symbol"] == symbol), None)

    def views(self, uid: int, kind: Optional[str] = None) -> Tuple[List[PositionView], Optional[Totals]]:
        rows = [r for r in self.store.tracked(uid) if kind in (None, r["kind"])]
        if not rows:
            return [], None
        symbols = [r["symbol"] for r in rows]
        quotes = self.store.quotes(symbols)
        facts = self.store.history_facts([(r["symbol"], r["ref_date"]) for r in rows])
        views = [perf.build_view(r, quotes.get(r["symbol"]), facts.get(r["symbol"])) for r in rows]
        totals = perf.portfolio_totals(views) if kind in (None, "hold") else None
        views.sort(key=lambda v: (v.since_pct is None, -(v.since_pct or 0), v.symbol))         # best since-added first, n/a last
        return views, totals

    def view(self, uid: int, symbol: str) -> Optional[PositionView]:
        row = self.get(uid, symbol)
        if not row:
            return None
        return perf.build_view(row, self.store.quotes([symbol]).get(symbol), self.store.history_facts([(symbol, row["ref_date"])]).get(symbol))

    def tracked_map(self, uid: int) -> Dict[str, str]:
        return {r["symbol"]: r["kind"] for r in self.store.tracked(uid)}

    # ------------------------------------------------------------------ export (the user's right to their own data)
    def export_csv(self, uid: int) -> str:
        views, _ = self.views(uid)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["symbol", "list", "your_price", "price_source", "date_added", "shares", "last_close", "close_date",
                    "change_since_added_pct", "note"])
        for v in views:
            w.writerow([v.symbol, "portfolio" if v.kind == "hold" else "watchlist", v.ref_price, v.ref_source, v.ref_date.isoformat(),
                        "" if v.shares is None else perf.fmt_shares(v.shares), "" if v.close is None else v.close,
                        v.quote_date.isoformat() if v.quote_date else "",
                        "" if v.since_pct is None else perf.quantize(v.since_pct, Decimal("0.01")), v.note or ""])
        return out.getvalue()
