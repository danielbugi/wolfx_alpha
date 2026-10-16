"""CIK -> trading-symbol links from the SEC ticker file: parsing and the append-only update PLAN. PURE: stdlib only (no database, clock, network, environment).

The SEC's `company_tickers.json` describes the PRESENT. So the link is never a fact about a filing; it is a separately recorded, dated observation:

  * OBSERVED link   we captured the file on `snapshot_date`; the pair (cik, symbol) holds from that date (`valid_from = snapshot_date`).
  * RECONSTRUCTED   today's file projected backwards (`valid_from` unknown): only ever created on request, always labelled, never upgraded.
  * A later file that no longer carries a pair ENDS it (`absent_on` = that file's date). The true change lies between the last file that carried
    the pair and that date; that uncertainty is recorded, not resolved by guessing.
  * Nothing is edited: a ticker change is an end of the old pair plus a new link for the new pair, both stamped by the database when recorded, so a
    decision taken before they were recorded can never see them.

A truncated or garbled file must not end thousands of links: `plan_update` refuses to close more than `MAX_CLOSE_RATIO` of the open pairs in one
file (a real day changes a handful), and refuses a snapshot older than one already applied.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

SOURCE = "sec_company_tickers"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
MAX_CLOSE_RATIO = 0.20
MIN_PAIRS_FOR_PLAN = 1000                      # a ticker file with fewer pairs than this is not the SEC file (the real one holds ~10,000)
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-/]{0,19}$")


class IdentifierError(ValueError):
    """The ticker file or the requested update is unusable; nothing is applied."""


@dataclass(frozen=True)
class Pair:
    cik: int
    symbol: str


@dataclass(frozen=True)
class OpenLink:
    """A stored link that has no closure yet."""
    link_id: int
    cik: int
    symbol: str
    basis: str
    valid_from: Optional[date]


@dataclass
class ParsedTickers:
    pairs: List[Pair] = field(default_factory=list)
    rejected: List[Tuple[Any, str]] = field(default_factory=list)
    counters: Counter = field(default_factory=Counter)
    symbols_on_several_ciks: Dict[str, List[int]] = field(default_factory=dict)


def parse_company_tickers(body: bytes) -> ParsedTickers:
    """The SEC file is {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "..."}, ...}. A malformed entry is rejected with a reason and never
    repaired; a document that is not that shape is refused as a whole."""
    try:
        doc = json.loads(body)
    except ValueError as e:
        raise IdentifierError(f"ticker file is not JSON: {e}") from e
    if not isinstance(doc, Mapping) or not doc:
        raise IdentifierError("ticker file is not a non-empty JSON object")
    out = ParsedTickers()
    seen: set = set()
    by_symbol: Dict[str, set] = defaultdict(set)
    for key, row in doc.items():
        out.counters["entries"] += 1
        if not isinstance(row, Mapping):
            out.rejected.append((key, "entry is not an object"))
            continue
        cik, tk = row.get("cik_str"), row.get("ticker")
        if isinstance(cik, str) and cik.isdigit():
            cik = int(cik)
        if not isinstance(cik, int) or isinstance(cik, bool) or cik <= 0:
            out.rejected.append((key, f"cik_str {cik!r} is not a positive integer"))
            continue
        if not isinstance(tk, str) or not _SYMBOL_RE.match(tk.strip().upper()):
            out.rejected.append((key, f"ticker {tk!r} is empty or malformed"))
            continue
        pair = Pair(cik, tk.strip().upper())
        if pair in seen:
            out.counters["duplicate_pair"] += 1
            continue
        seen.add(pair)
        out.pairs.append(pair)
        by_symbol[pair.symbol].add(cik)
    out.symbols_on_several_ciks = {s: sorted(c) for s, c in by_symbol.items() if len(c) > 1}
    out.counters["pairs"] = len(out.pairs)
    out.counters["rejected"] = len(out.rejected)
    return out


@dataclass
class LinkPlan:
    snapshot_date: date
    new_links: List[Tuple[Pair, str, Optional[date]]] = field(default_factory=list)      # (pair, basis, valid_from)
    closures: List[OpenLink] = field(default_factory=list)                              # the open links the file no longer carries
    unchanged: int = 0

    def summary(self) -> Dict[str, Any]:
        return {"snapshot_date": self.snapshot_date.isoformat(), "new_links": len(self.new_links), "closures": len(self.closures), "unchanged": self.unchanged,
                "new_by_basis": dict(Counter(b for _, b, _ in self.new_links))}


def plan_update(parsed: ParsedTickers, snapshot_date: date, open_links: Sequence[OpenLink], *, latest_applied: Optional[date] = None,
                reconstruct: bool = False, max_close_ratio: float = MAX_CLOSE_RATIO) -> LinkPlan:
    """What applying this file would append. `open_links` = stored links with no closure. `reconstruct` additionally records today's pairs as
    RECONSTRUCTED (valid_from unknown) -- an explicit, labelled choice, never implied."""
    if len(parsed.pairs) < MIN_PAIRS_FOR_PLAN:
        raise IdentifierError(f"ticker file carries {len(parsed.pairs)} usable pairs (< {MIN_PAIRS_FOR_PLAN}): refusing to treat it as the SEC file")
    if latest_applied is not None and snapshot_date < latest_applied:
        raise IdentifierError(f"snapshot {snapshot_date} is older than the latest applied {latest_applied}; links are only ever extended forwards")
    present = {(p.cik, p.symbol) for p in parsed.pairs}
    plan = LinkPlan(snapshot_date)
    open_obs = {(l.cik, l.symbol) for l in open_links if l.basis == "observed"}
    open_rec = {(l.cik, l.symbol) for l in open_links if l.basis == "reconstructed"}
    for p in parsed.pairs:
        if (p.cik, p.symbol) in open_obs:
            plan.unchanged += 1
        else:
            plan.new_links.append((p, "observed", snapshot_date))
        if reconstruct and (p.cik, p.symbol) not in open_rec:
            plan.new_links.append((p, "reconstructed", None))
    gone = [l for l in open_links if (l.cik, l.symbol) not in present]
    # a reconstructed link that is still open but whose pair is still present stays; only absent pairs are closed
    base = max(1, len({(l.cik, l.symbol) for l in open_links}))
    if len({(l.cik, l.symbol) for l in gone}) / base > max_close_ratio:
        raise IdentifierError(f"this file would end {len(gone)} of {base} open pairs (> {max_close_ratio:.0%}); a truncated or wrong file is the likely cause, nothing applied")
    plan.closures = gone
    return plan


# ------------------------------------------------------------------ reading side (pure): which symbol did a filer have on a filing date, as known at a cutoff
@dataclass(frozen=True)
class StoredLink:
    link_id: int
    cik: int
    symbol: str
    basis: str
    valid_from: Optional[date]
    recorded_at: Any                       # tz-aware datetime
    ended_absent_on: Optional[date] = None
    ended_recorded_at: Any = None


def canonical_symbol(s: str) -> str:
    """One spelling for share-class tickers across sources ('BRK-B', 'BRK.B', 'BRK/B' are the same listing)."""
    return re.sub(r"[./]", "-", s.strip().upper())


def _covering(links: Iterable[StoredLink], on: date, cutoff, *, cik: Optional[int] = None, symbol: Optional[str] = None) -> List[StoredLink]:
    """The links, among those RECORDED at or before `cutoff`, that hold on the filing date `on`: started by then, and not closed by a closure that was itself recorded at or before `cutoff`."""
    want = canonical_symbol(symbol) if symbol is not None else None
    out = []
    for l in links:
        if (cik is not None and l.cik != cik) or (want is not None and canonical_symbol(l.symbol) != want) or l.recorded_at > cutoff:
            continue
        if l.valid_from is not None and l.valid_from > on:
            continue
        if l.ended_absent_on is not None and l.ended_recorded_at is not None and l.ended_recorded_at <= cutoff and l.ended_absent_on <= on:
            continue
        out.append(l)
    return out


def symbols_as_of(links: Iterable[StoredLink], cik: int, on: date, cutoff) -> Tuple[List[str], str]:
    """(symbols, basis) of one filer on a filing date as known at `cutoff`, using only links and closures recorded at or before it.
    A filer with several listed classes (GOOG / GOOGL, BRK-A / BRK-B) has several symbols: they are all returned, because an issuer-level filing concerns every class.
    ([], 'unmapped') = no covering link. Basis is 'observed' only if every covering link is observed (a reconstructed link is never upgraded)."""
    hits = _covering(links, on, cutoff, cik=cik)
    if not hits:
        return [], "unmapped"
    seen: Dict[str, str] = {}
    for l in hits:
        seen.setdefault(canonical_symbol(l.symbol), l.symbol)
    return sorted(seen.values(), key=canonical_symbol), ("observed" if all(l.basis == "observed" for l in hits) else "reconstructed")


def claimants(links: Iterable[StoredLink], symbol: str, on: date, cutoff) -> List[int]:
    """The CIKs that hold `symbol` on `on` as known at `cutoff`. More than one means the symbol is claimed twice (a re-used ticker whose old owner's closure is not yet recorded,
    or a conflicting file): a filing must then NOT be attached to the symbol -- it is ambiguous, not guessed."""
    return sorted({l.cik for l in _covering(links, on, cutoff, symbol=symbol)})
