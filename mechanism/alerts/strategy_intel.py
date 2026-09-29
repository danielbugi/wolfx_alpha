# mechanism/alerts/strategy_intel.py
"""
Strategy Intelligence for the private assistant: OWNER ONLY, READ ONLY.

Every number comes from mechanism/strategy_analytics (the same canonical layer the dashboard's /api/strategies uses); this
module only chooses which canonical query to run and parses what the owner typed. Nothing here computes a rate, an R or a
count, and nothing here writes: readonly_fetch() runs every query inside a READ ONLY transaction that is rolled back, so the
database itself refuses a write from this path.

Framework-free like bot_service.py (no aiogram), so it is unit-tested without Telegram. Screens are in strategy_screens.py.

Why owner only: Strategy Intelligence is internal operator data (performance, data health, research lineage) using the
strategy's own vocabulary (signal, stop, target, win rate). The assistant's members get the educational product under the
wording guard of PRIVATE_ASSISTANT_PLAN.md section 0; none of this reaches them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from psycopg2.extras import RealDictCursor

from strategy_analytics import analytics as A
from strategy_analytics import definitions as D

PAGE_SIZE = 8                  # signals per Telegram page: one screen, well under 4096 chars, <= 3 rows of symbol buttons
SYMBOL_HISTORY = 5             # other ledger signals offered for one symbol
UNAVAILABLE = "Strategy data is temporarily unavailable. Nothing is shown rather than a guess."

GRADES = ("A", "B", "C", "D", "F")
_TICKER = re.compile(r"^\$?([A-Za-z]{1,5}(?:[.-][A-Za-z]{1,2})?)$")


# ============================================================================ database: read only
def readonly_fetch(database) -> A.Fetch:
    """A strategy_analytics fetch bound to shared.database's pool, where every query runs in a READ ONLY transaction that is
    always rolled back: an INSERT/UPDATE/DELETE issued through it fails in Postgres itself."""
    def fetch(sql: str, params: Any = None) -> List[Dict[str, Any]]:
        with database.get_sync_connection() as conn:
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SET TRANSACTION READ ONLY")
                    cur.execute(sql, params)
                    return [dict(r) for r in cur.fetchall()] if cur.description else []
            finally:
                conn.rollback()
    return fetch


# ============================================================================ list filters
LIFECYCLE_CODES = {"o": "open", "h": "held", "r": "resolved", "a": "any_open"}   # a = status open, held included and marked


@dataclass(frozen=True)
class ListSpec:
    direction: str = ""        # 'bullish' | 'bearish' | ''
    lifecycle: str = ""        # 'o' | 'h' | 'r' | 'a' | ''
    grade: str = ""            # 'A'..'F' | ''
    today: bool = False        # the latest market session (the canonical reference session), never the wall clock
    sort: str = "newest"       # 'newest' | 'grade' (the screener's own recorded grade -- no invented ranking)

    def token(self) -> str:
        """5 fixed chars for callback data, e.g. 'Ua-tn' = bullish, all open, any grade, latest session, newest first."""
        return "".join([{"bullish": "U", "bearish": "D"}.get(self.direction, "-"), self.lifecycle or "-", self.grade or "-",
                         "t" if self.today else "-", "g" if self.sort == "grade" else "n"])

    @staticmethod
    def from_token(tok: str) -> Optional["ListSpec"]:
        if not re.fullmatch(r"[UD-][ohra-][ABCDF-][t-][ng]", tok or ""):
            return None
        return ListSpec({"U": "bullish", "D": "bearish"}.get(tok[0], ""), "" if tok[1] == "-" else tok[1],
                        "" if tok[2] == "-" else tok[2], tok[3] == "t", "grade" if tok[4] == "g" else "newest")


WORDS_DIRECTION = {"bullish": "bullish", "bull": "bullish", "long": "bullish", "longs": "bullish",
                   "bearish": "bearish", "bear": "bearish", "short": "bearish", "shorts": "bearish"}
WORDS_LIFECYCLE = {"open": "a", "active": "a", "held": "h", "hold": "h", "resolved": "r", "closed": "r"}
WORDS_TODAY = {"today", "today's", "todays", "latest", "new", "newest"}
WORDS_FILLER = {"signals", "signal", "show", "me", "all", "the", "any", "list", "trades", "positions", "setups", "breakouts",
                "please", "only", "with", "and", "of", "for", "are", "there", "what", "which", "do", "we", "have", "our",
                "current", "currently", "by", "sorted", "sort"}


def parse_list_args(text: Optional[str], base: ListSpec = ListSpec()) -> Tuple[Optional[ListSpec], Optional[str]]:
    """'/signals bullish grade A today' -> ListSpec. Returns (None, message) on a word it does not understand, so a typo is
    never silently ignored."""
    spec = base
    toks = re.findall(r"[a-z0-9'+-]+", (text or "").lower())
    i = 0
    while i < len(toks):
        t = toks[i]
        grade = re.fullmatch(r"([abcdf])-?grade|grade-?([abcdf])", t)
        if grade:
            spec = replace(spec, grade=(grade.group(1) or grade.group(2)).upper())
        elif t == "grade" and i + 1 < len(toks) and toks[i + 1] in ("a", "b", "c", "d", "f"):
            spec = replace(spec, grade=toks[i + 1].upper())
            i += 1
        elif t in ("a", "b", "c", "d", "f") and i + 1 < len(toks) and toks[i + 1] == "grade":
            spec = replace(spec, grade=t.upper())
            i += 1
        elif t in WORDS_DIRECTION:
            spec = replace(spec, direction=WORDS_DIRECTION[t])
        elif t in WORDS_LIFECYCLE:
            spec = replace(spec, lifecycle=WORDS_LIFECYCLE[t])
        elif t in WORDS_TODAY:
            spec = replace(spec, today=True)
        elif t == "grade" and (i == len(toks) - 1 or toks[i - 1] in ("by", "sort", "sorted")):
            spec = replace(spec, sort="grade")
        elif t in WORDS_FILLER:
            pass
        else:
            return None, t
        i += 1
    return spec, None


# ============================================================================ intents (typed text, owner only)
@dataclass(frozen=True)
class Intent:
    kind: str                  # summary | performance | directions | health | strategies | list | symbol | definition | best | help
    spec: ListSpec = ListSpec()
    symbol: str = ""
    term: str = ""


DEF_TERMS = [                  # (pattern, definitions.DEFINITIONS key) -- first match wins, most specific first
    (r"win ?rate", "win_rate"), (r"\bwins?\b|\bwinners?\b|\bwinning\b", "winner"), (r"average r|median r|\br\b|r[- ]multiple", "outcome_r"),
    (r"\bheld\b|\bhold\b", "held"), (r"expir", "expired"), (r"ambigu|same[- ]bar", "ambiguous"), (r"\bstopped\b|\bstop\b", "stopped"),
    (r"holding|sessions? held", "holding_period"), (r"resolved", "resolved"), (r"\bopen\b", "open"), (r"\bmae\b|adverse", "mae_r"),
    (r"milestone|target", "target_milestones"), (r"\bsignals?\b", "signal"),
]
_KEYWORDS = set(WORDS_DIRECTION) | set(WORDS_LIFECYCLE) | WORDS_TODAY | WORDS_FILLER | {"grade", "is", "a", "it", "doing", "donchian"}


def _ticker_after(pattern: str, original: str, require_upper: bool) -> str:
    m = re.search(pattern + r"\s+\$?(?P<ticker>[A-Za-z]{1,5}(?:[.-][A-Za-z]{1,2})?)\b[?.!]*\s*$", original, re.I)
    if not m:
        return ""
    cand = m.group("ticker")
    if cand.lower() in _KEYWORDS or (require_upper and cand != cand.upper()):
        return ""
    return cand.upper()


def parse_intent(text: str) -> Optional[Intent]:
    """Deterministic: a typed question -> which canonical query to run. It never produces a number; unknown text -> None (the
    normal assistant flow continues)."""
    original = (text or "").strip()
    low = original.lower()
    if not low or low.startswith("/"):
        return None
    # a question about what a word MEANS (not about its current value: "what's our win rate?" is a performance question)
    if re.search(r"\b(considered|define[ds]?|definition|meaning|means?|counts? as)\b|\bwhat('?s| is| are) (a|an)\b", low):
        for pat, key in DEF_TERMS:
            if re.search(pat, low):
                return Intent("definition", term=key)
    if re.search(r"\b(best|top|strongest|conviction|favou?rite)\b", low) and re.search(r"signal|trade|setup|pick|stock", low):
        return Intent("best")
    if re.search(r"\bhealth(y)?\b|data quality|\bstale\b|\bintegrity\b", low):
        return Intent("health")
    if re.search(r"\bcompare\b|\bvs\.?\b|\bversus\b|bullish (and|or|&) bearish|bearish (and|or|&) bullish|by direction|each direction", low):
        return Intent("directions")
    if re.search(r"\bstrategies\b|which strateg|what strateg", low):
        return Intent("strategies")
    if re.search(r"\bheld\b|on hold|split[- ]suspect", low):
        return Intent("list", spec=ListSpec(lifecycle="h"))
    sym = (_ticker_after(r"(what happened to|what'?s happening (with|to)|signal for|status of|details? (for|on)|how is|how'?s|how did)", original, False)
           or _ticker_after(r"\b(show|open|find|check|look up|lookup)", original, True))
    if sym:
        return Intent("symbol", symbol=sym)
    if re.search(r"win ?rate|average r|median r|\bperformance\b|\bperforming\b|\bresults?\b|\boutcomes?\b", low):
        return Intent("performance")
    if re.search(r"how('?s| is| are)\b.*\b(doing|going)\b|how many signals|how many (open|trades)|\bsummary\b|\boverview\b", low):
        return Intent("summary")
    if re.search(r"\b(signals?|trades?|positions?|setups?|breakouts?)\b", low) or re.search(r"\b(bullish|bearish)\b", low):
        spec, bad = parse_list_args(re.sub(r"[?.!,]", " ", low))
        if spec is not None:
            return Intent("list", spec=spec)
        loose = ListSpec()
        for t in re.findall(r"[a-z']+", low):                  # a free sentence: keep only the filter words we know
            spec2, _ = parse_list_args(t, loose)
            loose = spec2 or loose
        m = re.search(r"\b([abcdf])[- ]grade\b|\bgrade[- ]([abcdf])\b", low)
        if m:
            loose = replace(loose, grade=(m.group(1) or m.group(2)).upper())
        return Intent("list", spec=loose)
    return None


# ============================================================================ the service (all canonical calls)
class StrategyIntel:
    """Thin router over mechanism/strategy_analytics. Each method is one canonical call (or two, for a symbol: its signal list and
    the newest one's detail) -- no per-row follow-up queries."""

    def __init__(self, fetch: A.Fetch):
        self.fetch = fetch

    def strategies(self) -> List[Dict]:
        return A.list_strategies(self.fetch)

    def resolve(self, sid: Optional[int] = None, key: Optional[str] = None, version: Optional[str] = None
                ) -> Tuple[Optional[Dict], List[Dict]]:
        """The strategy to answer about: by id (buttons), by key/version (typed), else the only one that is tracking signals, else
        the only one registered. None when that is ambiguous -- the caller then shows the list instead of guessing."""
        all_ = self.strategies()
        if sid is not None:
            return next((s for s in all_ if s["id"] == sid), None), all_
        if key:
            cands = [s for s in all_ if s["key"] == key and (version is None or s["version"] == version)]
            return (cands[0] if len(cands) == 1 else None), all_
        tracking = [s for s in all_ if s["tracking"]["tracking_status"] == "tracking"]
        if len(tracking) == 1:
            return tracking[0], all_
        return (all_[0] if len(all_) == 1 else None), all_

    def summary(self, strategy: Dict) -> Dict:
        return A.summary(self.fetch, strategy)

    def health(self, strategy: Dict) -> Dict:
        return A.data_health(self.fetch, strategy)

    def signals(self, strategy: Dict, spec: ListSpec, offset: int) -> Tuple[Dict, Optional[date]]:
        ref = A.reference_session(self.fetch) if spec.today else None
        kw: Dict[str, Any] = {"sort": spec.sort, "limit": PAGE_SIZE, "offset": max(0, offset)}
        if spec.direction:
            kw["direction"] = spec.direction
        if spec.grade:
            kw["quality_grade"] = spec.grade
        if spec.lifecycle == "a":
            kw["status"] = "open"
        elif spec.lifecycle:
            kw["lifecycle_state"] = LIFECYCLE_CODES[spec.lifecycle]
        if spec.today:
            if ref is None:
                return {"items": [], "total": 0, "limit": PAGE_SIZE, "offset": 0, "has_more": False}, None
            kw["date_from"] = kw["date_to"] = ref
        return A.list_signals(self.fetch, strategy, **kw), ref

    def signal(self, strategy: Dict, ledger_id: int) -> Optional[Dict]:
        return A.get_signal(self.fetch, strategy, ledger_id)

    def symbol(self, strategy: Dict, symbol: str) -> Tuple[Dict, Optional[Dict]]:
        """Every recorded signal for the symbol (newest first) and the detail of the newest."""
        page = A.list_signals(self.fetch, strategy, symbol=symbol, sort="newest", limit=SYMBOL_HISTORY + 1)
        detail = A.get_signal(self.fetch, strategy, page["items"][0]["id"]) if page["items"] else None
        return page, detail


def definition(term: str) -> Optional[str]:
    """The canonical wording from strategy_analytics.definitions -- never a Telegram-specific interpretation."""
    return D.DEFINITIONS.get(term)


def clean_symbol(token: str) -> Optional[str]:
    m = _TICKER.match((token or "").strip())
    return m.group(1).upper() if m else None
