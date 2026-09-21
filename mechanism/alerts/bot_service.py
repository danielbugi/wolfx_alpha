# mechanism/alerts/bot_service.py
"""
Logic of the interactive First Light bot, free of any Telegram framework (run_bot.py wires it to aiogram) so it can be
unit-tested. Design rules (see MILESTONES.md 6C):

  * ECONOMIC: every answer is a read of the precomputed snapshot (digest_runs / digest_stocks). Nothing here calls a price
    provider, so cost does not grow with the audience. Per-user rate limits bound what one person can ask for.
  * EDUCATIONAL: facts and numbers only; the framing lives in texts.py and a test scans all wording for advice-style words.
  * MINIMAL DATA: the only stored personal data is the Telegram user id, when they acknowledged the disclaimer, and their
    tracked stocks (symbol, the price they gave or the last close, the day, optional shares) - see tracker.py.
"""
from __future__ import annotations

import html
import re
import time
from collections import defaultdict, deque
from datetime import date, timedelta
from typing import Callable, Deque, Dict, List, Optional, Protocol

from alerts.levels import risk_framework
from alerts.message_format import fmt_cap, fmt_price
from alerts.access import COOLDOWN_DAYS
from alerts.performance import is_split_like
from alerts.texts import DISCLAIMER_SHORT, LEVELS_HOW, LEVELS_NOTE

MAX_SYMBOLS_PER_COMMAND = 50
STALE_AFTER_DAYS = 4
LONG_CATEGORIES = ("breakout", "near_breakout")
GROUP_LABEL = {"breakout": "Breakout", "near_breakout": "Near breakout"}
LIST_LABEL = {"gainers": "gainers", "atr": "ATR", "volume": "volume"}
INDENT = " "
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def _arrow(pct: float) -> str:
    return f"{'▲' if pct > 0 else '▼' if pct < 0 else '■'}{abs(pct):.1f}%"


def parse_symbols(text: Optional[str], limit: int = MAX_SYMBOLS_PER_COMMAND) -> List[str]:
    """'aapl, $msft  BRK.B' -> ['AAPL', 'MSFT', 'BRK.B'] (validated shape, de-duplicated, capped)."""
    seen: List[str] = []
    for tok in re.split(r"[\s,;]+", (text or "").upper().replace("$", " ")):
        tok = tok.rstrip(".-")                       # 'MSTR.' at the end of a sentence; 'BRK.B' keeps its inner dot
        if tok and SYMBOL_RE.match(tok) and tok not in seen:
            seen.append(tok)
        if len(seen) >= limit:
            break
    return seen


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
class Store(Protocol):
    def latest_session(self) -> Optional[Dict]: ...
    def stocks(self, session_date, symbols: List[str]) -> Dict[str, Dict]: ...
    def touch_user(self, uid: int) -> None: ...
    def is_acknowledged(self, uid: int) -> bool: ...
    def acknowledge(self, uid: int) -> None: ...
    # tracker (Watchlist + Portfolio)
    def tracked(self, uid: int) -> List[Dict]: ...
    def upsert_tracked(self, uid: int, symbol: str, kind: str, ref_price, ref_source: str, ref_date, shares, first_price) -> None: ...
    def remove_tracked(self, uid: int, symbols: Optional[List[str]]) -> int: ...
    def quotes(self, symbols: List[str]) -> Dict[str, Dict]: ...
    def history_facts(self, pairs: List) -> Dict[str, Dict]: ...
    def list_rows(self, session_date) -> List[Dict]: ...
    def bars(self, symbol: str, limit: int = 126) -> List[Dict]: ...
    # request-access flow + funnel counters (aggregate only)
    def request_status(self, uid: int) -> Optional[Dict]: ...
    def add_request(self, uid: int) -> bool: ...
    def pending_requests(self, limit: int): ...
    def decide_request(self, uid: int, approve: bool) -> Optional[str]: ...
    def purge_requests(self, pending_days: int, cooldown_days: int) -> int: ...
    def count_event(self, event: str) -> None: ...
    def funnel_metrics(self, days: int) -> Dict[str, int]: ...
    # news + chart caches (shared by every user)
    def news_state(self, symbol: str) -> Optional[Dict]: ...
    def news_recent(self, symbol: str, limit: int) -> List[Dict]: ...
    def save_news(self, symbol: str, items: List[Dict], ok: bool, now) -> None: ...
    def chart_file_id(self, symbol: str, session_date) -> Optional[str]: ...
    def save_chart_file_id(self, symbol: str, session_date, file_id: str) -> None: ...


class PgStore:
    """Postgres implementation (tables: mechanism/add_digest_tables.sql). `db` is shared.database.db."""

    def __init__(self, db):
        self.db = db

    def latest_session(self) -> Optional[Dict]:
        rows = self.db.execute_dict_query("SELECT * FROM digest_runs ORDER BY session_date DESC LIMIT 1")
        return rows[0] if rows else None

    def stocks(self, session_date, symbols: List[str]) -> Dict[str, Dict]:
        if not symbols:
            return {}
        rows = self.db.execute_dict_query(
            "SELECT * FROM digest_stocks WHERE session_date = %s AND symbol = ANY(%s)", (session_date, symbols))
        return {r["symbol"]: r for r in rows}

    def touch_user(self, uid: int) -> None:
        self.db.execute_insert(
            "INSERT INTO bot_users (telegram_user_id) VALUES (%s) "
            "ON CONFLICT (telegram_user_id) DO UPDATE SET last_seen = NOW()", (uid,))

    def is_acknowledged(self, uid: int) -> bool:
        rows = self.db.execute_dict_query(
            "SELECT acknowledged_at FROM bot_users WHERE telegram_user_id = %s", (uid,))
        return bool(rows and rows[0]["acknowledged_at"])

    def acknowledge(self, uid: int) -> None:
        self.db.execute_insert(
            "INSERT INTO bot_users (telegram_user_id, acknowledged_at) VALUES (%s, NOW()) "
            "ON CONFLICT (telegram_user_id) DO UPDATE SET acknowledged_at = COALESCE(bot_users.acknowledged_at, NOW()), "
            "last_seen = NOW()", (uid,))

    # --- tracker (tables: mechanism/add_tracker_tables.sql). Every query is scoped by telegram_user_id.
    def tracked(self, uid: int) -> List[Dict]:
        return self.db.execute_dict_query(
            "SELECT symbol, kind, ref_price, ref_source, ref_date, shares, first_added_at, first_price FROM bot_tracked "
            "WHERE telegram_user_id = %s ORDER BY symbol", (uid,))

    def upsert_tracked(self, uid, symbol, kind, ref_price, ref_source, ref_date, shares, first_price) -> None:
        """One transaction: the bot_users row must exist (foreign key), then insert-or-update. first_added_at / first_price are only
        written on insert, so a watch -> hold move keeps the facts of the day the stock was first added."""
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO bot_users (telegram_user_id) VALUES (%s) ON CONFLICT (telegram_user_id) DO NOTHING", (uid,))
            cur.execute(
                "INSERT INTO bot_tracked (telegram_user_id, symbol, kind, ref_price, ref_source, ref_date, shares, first_price) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (telegram_user_id, symbol) DO UPDATE SET "
                "kind = EXCLUDED.kind, ref_price = EXCLUDED.ref_price, ref_source = EXCLUDED.ref_source, "
                "ref_date = EXCLUDED.ref_date, shares = EXCLUDED.shares, updated_at = NOW()",
                (uid, symbol, kind, ref_price, ref_source, ref_date, shares, first_price))
            conn.commit()

    def remove_tracked(self, uid: int, symbols: Optional[List[str]]) -> int:
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            if symbols is None:
                cur.execute("DELETE FROM bot_tracked WHERE telegram_user_id = %s", (uid,))
            else:
                cur.execute("DELETE FROM bot_tracked WHERE telegram_user_id = %s AND symbol = ANY(%s)", (uid, symbols))
            n = cur.rowcount
            conn.commit()
            return n

    def quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """Latest and previous stored close per symbol (stock_prices, indexed by symbol+date): {sym: {date, close, prev_close}}."""
        if not symbols:
            return {}
        rows = self.db.execute_dict_query(
            "SELECT symbol, date, close FROM (SELECT symbol, date, close, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) rn "
            "FROM stock_prices WHERE symbol = ANY(%s) AND date >= CURRENT_DATE - 30) t WHERE rn <= 2 ORDER BY symbol, date DESC",
            (symbols,))
        out: Dict[str, Dict] = {}
        for r in rows:
            q = out.setdefault(r["symbol"], {"date": r["date"], "close": r["close"], "prev_close": None})
            if q["date"] != r["date"]:
                q["prev_close"] = r["close"]
        return out

    def history_facts(self, pairs: List) -> Dict[str, Dict]:
        """What the price-adjustment guard needs, per symbol: close-to-close jumps that look like a split (see
        performance.is_split_like) in the stored series since the reference date, plus the registry price_discontinuities, and
        today's stored close on the reference date."""
        if not pairs:
            return {}
        symbols = [s for s, _ in pairs]
        refs = [d for _, d in pairs]
        lo = min(refs)
        facts = {s: {"jump_dates": [], "ref_close": None} for s in symbols}
        for r in self.db.execute_dict_query(                     # candidates only (a wide net); performance.is_split_like decides
                "SELECT symbol, date, close / pc AS ratio FROM (SELECT symbol, date, close, "
                "LAG(close) OVER (PARTITION BY symbol ORDER BY date) pc FROM stock_prices "
                "WHERE symbol = ANY(%s) AND date >= %s::date - 10) t "
                "WHERE date >= %s AND pc > 0 AND (close / pc < 0.70 OR close / pc > 1.90)", (symbols, lo, lo)):
            if is_split_like(r["ratio"]):
                facts[r["symbol"]]["jump_dates"].append(r["date"])
        for r in self.db.execute_dict_query("SELECT symbol, date FROM price_discontinuities WHERE symbol = ANY(%s) AND date >= %s",
                                            (symbols, lo)):
            facts[r["symbol"]]["jump_dates"].append(r["date"])
        for r in self.db.execute_dict_query(
                "SELECT v.symbol, sp.close FROM (SELECT unnest(%s::text[]) AS symbol, unnest(%s::date[]) AS ref_date) v "
                "JOIN LATERAL (SELECT close FROM stock_prices WHERE symbol = v.symbol AND date <= v.ref_date "
                "ORDER BY date DESC LIMIT 1) sp ON TRUE", (symbols, refs)):
            facts[r["symbol"]]["ref_close"] = r["close"]
        return facts

    def bars(self, symbol: str, limit: int = 126) -> List[Dict]:
        """The last `limit` daily bars of one symbol, oldest first (stock_prices; for the chart)."""
        rows = self.db.execute_dict_query(
            "SELECT date, open, high, low, close, volume FROM stock_prices WHERE symbol = %s ORDER BY date DESC LIMIT %s", (symbol, limit))
        return list(reversed(rows))

    def list_rows(self, session_date) -> List[Dict]:
        """The stocks that appear in at least one of the channel's lists on that session (each stock once)."""
        return self.db.execute_dict_query(
            "SELECT * FROM digest_stocks WHERE session_date = %s AND list_ranks IS NOT NULL ORDER BY symbol", (session_date,))

    # --- request-access flow (tables: mechanism/add_access_flow_tables.sql). A row exists ONLY after the person tapped "Request access".
    def request_status(self, uid: int) -> Optional[Dict]:
        rows = self.db.execute_dict_query(
            "SELECT status, requested_at, decided_at, "
            "(status = 'declined' AND decided_at + make_interval(days => %s) > NOW()) AS cooldown_active, "
            "decided_at + make_interval(days => %s) AS cooldown_until FROM bot_requests WHERE telegram_user_id = %s",
            (COOLDOWN_DAYS, COOLDOWN_DAYS, uid))
        return rows[0] if rows else None

    def add_request(self, uid: int) -> bool:
        """True = a new pending request was stored. False = one is already pending, or a declined one is still cooling down."""
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO bot_requests (telegram_user_id, status) VALUES (%s, 'pending') "
                "ON CONFLICT (telegram_user_id) DO UPDATE SET status = 'pending', requested_at = NOW(), decided_at = NULL "
                "WHERE bot_requests.status = 'declined' AND bot_requests.decided_at + make_interval(days => %s) <= NOW() RETURNING 1",
                (uid, COOLDOWN_DAYS))
            stored = cur.fetchone() is not None
            conn.commit()
            return stored

    def pending_requests(self, limit: int):
        n = self.db.execute_dict_query("SELECT COUNT(*) AS n FROM bot_requests WHERE status = 'pending'")[0]["n"]
        rows = self.db.execute_dict_query(
            "SELECT telegram_user_id, requested_at FROM bot_requests WHERE status = 'pending' ORDER BY requested_at LIMIT %s", (limit,))
        return int(n), rows

    def decide_request(self, uid: int, approve: bool) -> Optional[str]:
        """Approve = the row is deleted (the person moves to bot_access); decline = kept as 'declined' for the cool-down.
        None when there is no pending request (already decided, purged, or never made)."""
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            if approve:
                cur.execute("DELETE FROM bot_requests WHERE telegram_user_id = %s AND status = 'pending' RETURNING 1", (uid,))
            else:
                cur.execute("UPDATE bot_requests SET status = 'declined', decided_at = NOW() "
                            "WHERE telegram_user_id = %s AND status = 'pending' RETURNING 1", (uid,))
            found = cur.fetchone() is not None
            conn.commit()
            return ("approved" if approve else "declined") if found else None

    def purge_requests(self, pending_days: int, cooldown_days: int) -> int:
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM bot_requests WHERE (status = 'pending' AND requested_at < NOW() - make_interval(days => %s)) "
                        "OR (status = 'declined' AND decided_at < NOW() - make_interval(days => %s))", (pending_days, cooldown_days))
            n = cur.rowcount
            conn.commit()
            return n

    def count_event(self, event: str) -> None:
        self.db.execute_insert("INSERT INTO funnel_events (day, event, n) VALUES (CURRENT_DATE, %s, 1) "
                               "ON CONFLICT (day, event) DO UPDATE SET n = funnel_events.n + 1", (event,))

    def funnel_metrics(self, days: int) -> Dict[str, int]:
        """Counts for the last `days` days. Events are aggregate counters; the rest is derived from tables that already exist."""
        ev = {r["event"]: int(r["n"]) for r in self.db.execute_dict_query(
            "SELECT event, COALESCE(SUM(n), 0) AS n FROM funnel_events WHERE day > CURRENT_DATE - %s GROUP BY event", (days,))}
        cohort = self.db.execute_dict_query(
            "WITH a AS (SELECT telegram_user_id, approved_at FROM bot_access WHERE status = 'active' AND approved_at > NOW() - make_interval(days => %s)) "
            "SELECT COUNT(*) AS approved, "
            "COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM bot_users u WHERE u.telegram_user_id = a.telegram_user_id AND u.acknowledged_at IS NOT NULL)) AS finished_guide, "
            "COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM bot_tracked t WHERE t.telegram_user_id = a.telegram_user_id "
            "AND t.first_added_at <= a.approved_at + INTERVAL '24 hours')) AS activated FROM a", (days,))[0]
        active = self.db.execute_dict_query(
            "SELECT COUNT(*) AS n FROM bot_users u JOIN bot_access a ON a.telegram_user_id = u.telegram_user_id "
            "WHERE a.status = 'active' AND u.last_seen > NOW() - INTERVAL '7 days'")[0]["n"]
        waiting = self.db.execute_dict_query("SELECT COUNT(*) FILTER (WHERE status = 'pending') AS p, COUNT(*) FILTER (WHERE status = 'declined') AS d FROM bot_requests")[0]
        return {"opened_channel": ev.get("opened_channel", 0), "requested": ev.get("requested", 0), "approved": int(cohort["approved"]),
                "finished_guide": int(cohort["finished_guide"]), "activated": int(cohort["activated"]), "active_7d": int(active),
                "waiting": int(waiting["p"]), "declined_cooling": int(waiting["d"])}

    # --- news + chart caches (tables: add_tracker_tables.sql)
    def news_state(self, symbol: str) -> Optional[Dict]:
        rows = self.db.execute_dict_query("SELECT fetched_at, ok FROM news_fetched WHERE symbol = %s", (symbol,))
        return rows[0] if rows else None

    def news_recent(self, symbol: str, limit: int) -> List[Dict]:
        return self.db.execute_dict_query(
            "SELECT published_at, headline, source, url FROM news_items WHERE symbol = %s ORDER BY published_at DESC LIMIT %s",
            (symbol, limit))

    def save_news(self, symbol: str, items: List[Dict], ok: bool, now) -> None:
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            for it in items:
                cur.execute("INSERT INTO news_items (symbol, published_at, headline, source, url, url_hash, fetched_at) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (symbol, url_hash) DO NOTHING",
                            (symbol, it["published_at"], it["headline"], it.get("source"), it["url"], it["url_hash"], now))
            cur.execute("INSERT INTO news_fetched (symbol, fetched_at, ok) VALUES (%s, %s, %s) "
                        "ON CONFLICT (symbol) DO UPDATE SET fetched_at = EXCLUDED.fetched_at, ok = EXCLUDED.ok", (symbol, now, ok))
            conn.commit()

    def chart_file_id(self, symbol: str, session_date) -> Optional[str]:
        rows = self.db.execute_dict_query(
            "SELECT file_id FROM bot_chart_cache WHERE symbol = %s AND session_date = %s", (symbol, session_date))
        return rows[0]["file_id"] if rows else None

    def save_chart_file_id(self, symbol: str, session_date, file_id: str) -> None:
        self.db.execute_insert(
            "INSERT INTO bot_chart_cache (symbol, session_date, file_id) VALUES (%s, %s, %s) "
            "ON CONFLICT (symbol, session_date) DO UPDATE SET file_id = EXCLUDED.file_id, created_at = NOW()",
            (symbol, session_date, file_id))

    # --- access layer (tables: mechanism/add_assistant_tables.sql; logic: access.py)
    def access_status(self, uid: int) -> Optional[str]:
        rows = self.db.execute_dict_query("SELECT status FROM bot_access WHERE telegram_user_id = %s", (uid,))
        return rows[0]["status"] if rows else None

    def set_access(self, uid: int, status: str, invited_by: Optional[int] = None) -> None:
        self.db.execute_insert(
            "INSERT INTO bot_access (telegram_user_id, status, invited_by, approved_at, revoked_at) "
            "VALUES (%s, %s, %s, CASE WHEN %s = 'active' THEN NOW() END, CASE WHEN %s = 'revoked' THEN NOW() END) "
            "ON CONFLICT (telegram_user_id) DO UPDATE SET status = EXCLUDED.status, "
            "approved_at = CASE WHEN EXCLUDED.status = 'active' THEN NOW() ELSE bot_access.approved_at END, "
            "revoked_at = CASE WHEN EXCLUDED.status = 'revoked' THEN NOW() ELSE NULL END",
            (uid, status, invited_by, status, status))

    def add_invite(self, code_hash: str, created_by: int, ttl_hours: int, note: Optional[str]) -> None:
        self.db.execute_insert(
            "INSERT INTO bot_invites (code_hash, created_by, expires_at, note) "
            "VALUES (%s, %s, NOW() + make_interval(hours => %s), %s)", (code_hash, created_by, ttl_hours, note))

    def redeem_invite(self, code_hash: str, uid: int):
        """One transaction: a revoked user never consumes an invitation; the UPDATE's own conditions make a used-up or expired
        invitation (or a race between two people opening the same link) fail without a separate check."""
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT status FROM bot_access WHERE telegram_user_id = %s", (uid,))
            row = cur.fetchone()
            if row and row[0] == "revoked":
                conn.rollback()
                return "revoked", None
            cur.execute("UPDATE bot_invites SET uses = uses + 1 WHERE code_hash = %s AND uses < max_uses AND expires_at > NOW() "
                        "RETURNING note, created_by", (code_hash,))
            inv = cur.fetchone()
            if not inv:
                conn.rollback()
                return "invalid", None
            cur.execute("INSERT INTO bot_access (telegram_user_id, status, invited_by, approved_at) VALUES (%s, 'active', %s, NOW()) "
                        "ON CONFLICT (telegram_user_id) DO UPDATE SET status = 'active', approved_at = NOW(), revoked_at = NULL",
                        (uid, inv[1]))
            conn.commit()
            return "ok", inv[0]

    def access_counts(self) -> Dict[str, int]:
        rows = self.db.execute_dict_query("SELECT status, COUNT(*) AS n FROM bot_access GROUP BY status")
        counts = {r["status"]: int(r["n"]) for r in rows}
        open_inv = self.db.execute_dict_query(
            "SELECT COUNT(*) AS n FROM bot_invites WHERE uses < max_uses AND expires_at > NOW()")[0]["n"]
        return {"active": counts.get("active", 0), "revoked": counts.get("revoked", 0), "open_invites": int(open_inv)}

    def audit(self, actor: int, action: str, target: Optional[int] = None, detail: Optional[str] = None) -> None:
        self.db.execute_insert("INSERT INTO bot_audit (actor, action, target, detail) VALUES (%s, %s, %s, %s)",
                               (actor, action, target, detail))

    def purge_revoked(self, days: int) -> int:
        """Delete the saved personal data (watchlist, acknowledgement) of users revoked more than `days` ago. The bot_access row
        stays, so they remain blocked."""
        with self.db.get_sync_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM bot_users WHERE telegram_user_id IN (SELECT telegram_user_id FROM bot_access "
                        "WHERE status = 'revoked' AND revoked_at < NOW() - make_interval(days => %s))", (days,))
            n = cur.rowcount                                   # bot_watchlist rows go with them (ON DELETE CASCADE)
            conn.commit()
            return n


# ---------------------------------------------------------------------------
# Abuse / cost controls
# ---------------------------------------------------------------------------
class RateLimiter:
    """Sliding window: at most `max_calls` per `window_s` seconds per key (user id)."""

    def __init__(self, max_calls: int, window_s: float, now: Callable[[], float] = time.monotonic):
        self.max_calls, self.window_s, self._now = max_calls, window_s, now
        self._hits: Dict[int, Deque[float]] = defaultdict(deque)

    def allow(self, key: int) -> bool:
        t = self._now()
        q = self._hits[key]
        while q and t - q[0] >= self.window_s:
            q.popleft()
        if len(q) >= self.max_calls:
            return False
        q.append(t)
        return True


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class BotService:
    def __init__(self, store: Store):
        self.store = store

    # --- user / acknowledgement
    def touch_user(self, uid: int) -> None:
        self.store.touch_user(uid)

    def is_acknowledged(self, uid: int) -> bool:
        return self.store.is_acknowledged(uid)

    def acknowledge(self, uid: int) -> None:
        self.store.acknowledge(uid)

    # --- ATR risk framework (educational; same arithmetic as the dashboard's Strategy page, see levels.py)
    def render_levels(self, text: Optional[str], today: Optional[date] = None) -> str:
        symbols = parse_symbols(text, limit=1)
        if not symbols:
            return "Send one symbol after the command, for example: /levels AAPL"
        session = self.store.latest_session()
        if not session:
            return "There is no scan data yet. Try again after the next daily scan."
        sym = symbols[0]
        row = self.store.stocks(session["session_date"], [sym]).get(sym)
        if not row:
            return f"{html.escape(sym)} is not in the daily scan (liquid US stocks only)."
        fw = risk_framework(row["close"], row.get("atr"))
        if fw is None:
            return (f"The ATR risk framework is not available for {html.escape(sym)} in the latest scan "
                    "(ATR missing, or 2 x ATR is not below the price).")
        sd = session["session_date"]
        group = GROUP_LABEL.get(row["category"], "not in a long-side group")
        lines = [f"<b>{html.escape(sym)}</b> · ATR risk framework",
                 f"US close {sd:%a %d %b} · reference price {fmt_price(fw['close'])}"]
        if (today or date.today()) - sd > timedelta(days=STALE_AFTER_DAYS):
            lines.append(f"Note: the latest scan is from {sd:%d %b}, so this may be out of date.")
        facts = [f"Group: {group} · day {_arrow(row['ret1_pct'])}",
                 f"ATR(14): {fmt_price(fw['atr'])} = {fw['atr_pct']:.1f}% of price"]
        if row.get("rvol") is not None:
            facts.append(f"Volume {row['rvol']:.1f}× its 50-day median")
        more = []
        if row.get("range_atr") is not None:
            more.append(f"range {row['range_atr']:.1f}× ATR")
        if row.get("below_high_pct") is not None and row["category"] != "breakout":
            more.append(f"{row['below_high_pct']:.1f}% below 20-day high")
        if more:
            line = " · ".join(more)
            facts.append(line[0].upper() + line[1:])              # not str.capitalize(): it would turn "ATR" into "atr"
        facts.append(f"Avg daily trading value: {fmt_cap(row['dv20'])}")
        lines += ["", "<b>Facts</b>", *facts, "",
                  "<b>Risk level</b> (2×ATR below the price)",
                  f"{fmt_price(fw['risk_level'])} · ▼{fw['risk_pct']:.1f}% · this distance is 1R", "",
                  "<b>Reference levels above the price</b>"]
        lines += [f"{lv['r']}R  {fmt_price(lv['price'])} ▲{lv['pct']:.1f}% (+{lv['atr_multiple']:g}×ATR)" for lv in fw["levels"]]
        ratios = " · ".join(f"{lv['reward_to_risk']:g}:1" for lv in fw["levels"])
        lines += [f"Reward-to-risk of these distances: {ratios}", "", LEVELS_NOTE, "",
                  f"<blockquote expandable>{html.escape(LEVELS_HOW, quote=False)}</blockquote>", "", DISCLAIMER_SHORT]
        return "\n".join(lines)
