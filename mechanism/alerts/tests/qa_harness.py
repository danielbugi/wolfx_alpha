"""Shared QA machinery for the bot tests: an in-memory store, a fake Telegram network and a driver that pushes raw updates
through the REAL dispatcher (handlers, filters, rate limits, access gate, error handler) without any network.

Every message the bot tries to send or edit is checked with tg_html.problems(), so a reply that real Telegram would reject
("can't parse entities") fails the test instead of silently reaching a user as nothing. News and charts are fakes: no test
touches Alpaca or renders a real chart unless it says so.
"""
import asyncio
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from alerts import bot_service as bs  # noqa: E402
from alerts.access import Access  # noqa: E402
from alerts.news_service import NewsService, url_hash  # noqa: E402
from alerts.tracker import TrackerService  # noqa: E402
import tg_html  # noqa: E402

aiogram = pytest.importorskip("aiogram")
from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.exceptions import TelegramBadRequest  # noqa: E402
from aiogram.methods import (AnswerCallbackQuery, EditMessageText, GetMe, SendDocument, SendMessage, SendPhoto,  # noqa: E402
                             TelegramMethod)
from aiogram.types import Chat, Document, Message, PhotoSize, User  # noqa: E402

from alerts.run_bot import build_dispatcher  # noqa: E402

BOT_USERNAME = "test_first_light_bot"
OWNER_ID = 4242
TOKEN = "123456789:" + "A" * 35
SESSION_DATE = date(2026, 9, 18)                                  # a Friday
TODAY = date(2026, 9, 21)                                         # the Monday the tests "run" on
SESSION = {"session_date": SESSION_DATE, "universe_n": 2915, "up_n": 980, "down_n": 1894, "counts": {}}
TINY_PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00"
            b"\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82")


def stock(sym, cat=None, close=10.0, ret=1.0, rvol=1.5, rng=1.2, below=3.5, ranks=None, prev=None, atr=None):
    return {"symbol": sym, "category": cat, "prev_category": prev, "close": close, "ret1_pct": ret, "rvol": rvol,
            "range_atr": rng, "below_high_pct": below, "dv20": 5e7, "list_ranks": ranks, "atr": atr}


class MemoryStore:
    """Same interface as PgStore, in memory. `broken=True` makes every call raise, like a database that is down."""

    def __init__(self, stocks=(), session=SESSION, broken=False):
        self.session, self._stocks, self.users, self.broken = session, {s["symbol"]: s for s in stocks}, {}, broken
        self.access, self.invites, self.audit_log, self.clock = {}, {}, [], time.time
        self.tracked_rows = {}                                    # uid -> {symbol: row}
        self.facts = {}                                           # symbol -> {"jump_dates": [...], "ref_close": ...} (the price-guard inputs)
        self.news_fetched, self.news_rows, self.chart_ids = {}, {}, {}
        self.requests, self.events = {}, {}                          # request-access flow: uid -> row ; event -> count
        self.prices = {}                                          # symbol -> [(date, close), ...] oldest first
        for s in stocks:                                          # a previous close consistent with the snapshot's 1-day change
            prev = s["close"] / (1 + s["ret1_pct"] / 100) if s["ret1_pct"] > -100 else s["close"]
            self.prices[s["symbol"]] = [(SESSION_DATE - timedelta(days=1), prev), (SESSION_DATE, s["close"])]

    def _check(self):
        if self.broken:
            raise RuntimeError("database is down")

    def latest_session(self):
        self._check()
        return self.session

    def stocks(self, session_date, symbols):
        self._check()
        return {s: self._stocks[s] for s in symbols if s in self._stocks}

    def touch_user(self, uid):
        self._check()
        self.users.setdefault(uid, {"ack": False})

    def is_acknowledged(self, uid):
        self._check()
        return self.users.get(uid, {}).get("ack", False)

    def acknowledge(self, uid):
        self._check()
        self.users.setdefault(uid, {})["ack"] = True

    # --- tracker
    def tracked(self, uid):
        self._check()
        return [dict(r) for _, r in sorted(self.tracked_rows.get(uid, {}).items())]

    def upsert_tracked(self, uid, symbol, kind, ref_price, ref_source, ref_date, shares, first_price):
        self._check()
        self.users.setdefault(uid, {"ack": False})
        rows = self.tracked_rows.setdefault(uid, {})
        old = rows.get(symbol)
        rows[symbol] = {"symbol": symbol, "kind": kind, "ref_price": Decimal(str(ref_price)), "ref_source": ref_source,
                        "ref_date": ref_date, "shares": None if shares is None else Decimal(str(shares)),
                        "first_added_at": old["first_added_at"] if old else datetime.now(timezone.utc),
                        "first_price": old["first_price"] if old else Decimal(str(first_price))}

    def remove_tracked(self, uid, symbols):
        self._check()
        rows = self.tracked_rows.get(uid, {})
        gone = list(rows) if symbols is None else [s for s in symbols if s in rows]
        for s in gone:
            del rows[s]
        return len(gone)

    def quotes(self, symbols):
        self._check()
        out = {}
        for s in symbols:
            hist = self.prices.get(s)
            if hist:
                out[s] = {"date": hist[-1][0], "close": Decimal(str(hist[-1][1])),
                          "prev_close": Decimal(str(hist[-2][1])) if len(hist) > 1 else None}
        return out

    def history_facts(self, pairs):
        self._check()
        return {s: self.facts.get(s, {"jump_dates": [], "ref_close": None}) for s, _ in pairs}

    def list_rows(self, session_date):
        self._check()
        return [s for s in self._stocks.values() if s["list_ranks"]]

    def bars(self, symbol, limit=126):
        self._check()
        close = self.prices.get(symbol, [(None, 10.0)])[-1][1]
        n = limit if symbol in self.prices else 0
        return [{"date": SESSION_DATE - timedelta(days=n - 1 - i), "open": close, "high": close * 1.02, "low": close * 0.98,
                 "close": close, "volume": 1_000_000} for i in range(n)]

    # --- request-access flow + funnel counters (same semantics as PgStore)
    COOLDOWN_S = 7 * 86400

    def request_status(self, uid):
        self._check()
        r = self.requests.get(uid)
        if not r:
            return None
        active = r["status"] == "declined" and r["decided_at"] + self.COOLDOWN_S > self.clock()
        return {**r, "cooldown_active": active,
                "cooldown_until": datetime.fromtimestamp(r["decided_at"] + self.COOLDOWN_S, timezone.utc) if r["decided_at"] else None}

    def add_request(self, uid):
        self._check()
        r = self.requests.get(uid)
        if r and not (r["status"] == "declined" and r["decided_at"] + self.COOLDOWN_S <= self.clock()):
            return False
        self.requests[uid] = {"status": "pending", "requested_at": self.clock(), "decided_at": None}
        return True

    def pending_requests(self, limit):
        self._check()
        rows = sorted(((u, r) for u, r in self.requests.items() if r["status"] == "pending"), key=lambda x: x[1]["requested_at"])
        return len(rows), [{"telegram_user_id": u, "requested_at": datetime.fromtimestamp(r["requested_at"], timezone.utc)} for u, r in rows[:limit]]

    def decide_request(self, uid, approve):
        self._check()
        r = self.requests.get(uid)
        if not r or r["status"] != "pending":
            return None
        if approve:
            del self.requests[uid]
            return "approved"
        r.update(status="declined", decided_at=self.clock())
        return "declined"

    def purge_requests(self, pending_days, cooldown_days):
        self._check()
        now = self.clock()
        gone = [u for u, r in self.requests.items() if (r["status"] == "pending" and r["requested_at"] < now - pending_days * 86400)
                or (r["status"] == "declined" and r["decided_at"] < now - cooldown_days * 86400)]
        for u in gone:
            del self.requests[u]
        return len(gone)

    def count_event(self, event):
        self._check()
        self.events[event] = self.events.get(event, 0) + 1

    def funnel_metrics(self, days):
        self._check()
        members = [u for u, a in self.access.items() if a["status"] == "active"]
        return {"opened_channel": self.events.get("opened_channel", 0), "requested": self.events.get("requested", 0), "approved": len(members),
                "finished_guide": sum(1 for u in members if self.users.get(u, {}).get("ack")),
                "activated": sum(1 for u in members if self.tracked_rows.get(u)),
                "active_7d": sum(1 for u in members if u in self.users),
                "waiting": sum(1 for r in self.requests.values() if r["status"] == "pending"),
                "declined_cooling": sum(1 for r in self.requests.values() if r["status"] == "declined")}

    # --- news + chart caches
    def news_state(self, symbol):
        self._check()
        return self.news_fetched.get(symbol)

    def news_recent(self, symbol, limit):
        self._check()
        return sorted(self.news_rows.get(symbol, []), key=lambda r: r["published_at"], reverse=True)[:limit]

    def save_news(self, symbol, items, ok, now):
        self._check()
        rows = self.news_rows.setdefault(symbol, [])
        known = {r["url_hash"] for r in rows}
        rows.extend(dict(i) for i in items if i["url_hash"] not in known)
        self.news_fetched[symbol] = {"fetched_at": now, "ok": ok}

    def chart_file_id(self, symbol, session_date):
        self._check()
        return self.chart_ids.get((symbol, session_date))

    def save_chart_file_id(self, symbol, session_date, file_id):
        self._check()
        self.chart_ids[(symbol, session_date)] = file_id

    # --- access layer: same semantics as PgStore (a revoked user never consumes an invitation; used-up / expired ones are 'invalid')
    def access_status(self, uid):
        self._check()
        return self.access.get(uid, {}).get("status")

    def set_access(self, uid, status, invited_by=None):
        self._check()
        self.access[uid] = {"status": status, "invited_by": invited_by, "revoked_at": self.clock() if status == "revoked" else None}

    def add_invite(self, code_hash, created_by, ttl_hours, note):
        self._check()
        self.invites[code_hash] = {"created_by": created_by, "max_uses": 1, "uses": 0, "expires": self.clock() + ttl_hours * 3600, "note": note}

    def redeem_invite(self, code_hash, uid):
        self._check()
        if self.access.get(uid, {}).get("status") == "revoked":
            return "revoked", None
        inv = self.invites.get(code_hash)
        if not inv or inv["uses"] >= inv["max_uses"] or inv["expires"] <= self.clock():
            return "invalid", None
        inv["uses"] += 1
        self.set_access(uid, "active", inv["created_by"])
        return "ok", inv["note"]

    def access_counts(self):
        self._check()
        n = lambda s: sum(1 for a in self.access.values() if a["status"] == s)             # noqa: E731
        open_inv = sum(1 for i in self.invites.values() if i["uses"] < i["max_uses"] and i["expires"] > self.clock())
        return {"active": n("active"), "revoked": n("revoked"), "open_invites": open_inv}

    def audit(self, actor, action, target=None, detail=None):
        self._check()
        self.audit_log.append((actor, action, target, detail))

    def purge_revoked(self, days):
        self._check()
        gone = [u for u, a in self.access.items() if a["status"] == "revoked" and a["revoked_at"] < self.clock() - days * 86400
                and (u in self.users or u in self.tracked_rows)]
        for u in gone:
            self.users.pop(u, None)
            self.tracked_rows.pop(u, None)
        return len(gone)


STOCKS = [stock("AAPL", None, 189.3, 0.4, 0.9, 0.8, 1.6, atr=7.304),
          stock("COIN", "near_breakout", 194.25, 11.7, 3.0, 1.9, 1.0, {"gainers": 3, "atr": 4}, "near_breakout", atr=12.575),
          stock("MSTR", "breakout", 153.92, 16.4, 3.1, 2.6, 0.0, {"gainers": 3, "atr": 3}, "near_breakout", atr=9.352),
          stock("MSFT", None, 410.0, -0.2, 1.0, 0.7, 4.0),                                  # no ATR in the snapshot row
          stock("WILD", None, 5.0, 1.0, 1.0, 1.0, 9.0, atr=3.0),                            # 2 x ATR >= price: no levels
          stock("BRK.B", None, 480.0, 0.1, 1.0, 0.9, 2.0, atr=6.0)]                         # a ticker with a dot


class FakeNews:
    """Stands in for Alpaca. `.calls` records every provider call; `.fail = True` simulates an outage; `.empty = True` no headlines."""

    def __init__(self):
        self.calls, self.fail, self.empty = [], False, False

    def __call__(self, symbol):
        self.calls.append(symbol)
        if self.fail:
            raise RuntimeError("provider down")
        if self.empty:
            return []
        now = datetime.now(timezone.utc)
        return [{"published_at": now - timedelta(hours=3), "headline": f"{symbol} jumps as volume surges & analysts <react>",
                 "source": "benzinga", "url": f"https://example.com/{symbol}/1?a=1&b=2", "url_hash": url_hash(f"https://example.com/{symbol}/1?a=1&b=2")},
                {"published_at": now - timedelta(days=2), "headline": f"{symbol} announces new product",
                 "source": "reuters", "url": f"https://example.com/{symbol}/2", "url_hash": url_hash(f"https://example.com/{symbol}/2")}]


def service(stocks=STOCKS, **store_kwargs):
    svc = bs.BotService(MemoryStore(stocks, **store_kwargs))
    svc.fake_news, svc.chart_calls = FakeNews(), []
    return svc


class FakeSession(BaseSession):
    """Records every Telegram method the bot calls. `stale_callbacks` makes answerCallbackQuery fail the way Telegram does when a
    button is tapped while the bot is down; `refuse_edits` makes editMessageText fail the way Telegram does for 'message is not
    modified'."""

    def __init__(self, stale_callbacks=False, refuse_edits=None):
        super().__init__()
        self.calls, self.stale_callbacks, self.refuse_edits = [], stale_callbacks, refuse_edits

    async def close(self):
        return None

    async def make_request(self, bot, method: TelegramMethod, timeout=None):
        self.calls.append(method)
        n = len(self.calls)
        if isinstance(method, GetMe):
            return User(id=999, is_bot=True, first_name="First Light", username=BOT_USERNAME)
        if isinstance(method, AnswerCallbackQuery) and self.stale_callbacks:
            raise TelegramBadRequest(method=method, message="Bad Request: query is too old and response timeout expired")
        if isinstance(method, EditMessageText) and self.refuse_edits:
            raise TelegramBadRequest(method=method, message=self.refuse_edits)
        chat = Chat(id=getattr(method, "chat_id", 1), type="private")
        if isinstance(method, SendMessage):
            return Message(message_id=n, date=datetime.now(), chat=chat, text=method.text)
        if isinstance(method, SendPhoto):
            return Message(message_id=n, date=datetime.now(), chat=chat,
                           photo=[PhotoSize(file_id=f"file-id-{n}", file_unique_id=f"u{n}", width=1080, height=640)])
        if isinstance(method, SendDocument):
            return Message(message_id=n, date=datetime.now(), chat=chat, document=Document(file_id=f"doc-{n}", file_unique_id=f"d{n}"))
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        if False:
            yield b""


def msg(uid, text, n=1, chat_type="private", chat_id=None):
    """A raw Telegram 'message' update. Commands get the bot_command entity Telegram adds."""
    cmd = {"entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]} if text.startswith("/") else {}
    chat = chat_id if chat_id is not None else (uid if chat_type == "private" else -1000000000 - uid)
    return {"update_id": n, "message": {"message_id": n, "date": int(time.time()), "chat": {"id": chat, "type": chat_type},
                                        "from": {"id": uid, "is_bot": False, "first_name": "T"}, "text": text, **cmd}}


def tap(uid, data, n=1, chat_type="private", photo=False):
    chat = uid if chat_type == "private" else -1000000000 - uid
    body = {"message_id": 7, "date": int(time.time()), "chat": {"id": chat, "type": chat_type},
            "from": {"id": 999, "is_bot": True, "first_name": "bot"}, "text": "x"}
    if photo:                                                                     # a tap made on a photo message (the chart)
        del body["text"]
        body["photo"] = [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]
    return {"update_id": n, "callback_query": {"id": str(n), "from": {"id": uid, "is_bot": False, "first_name": "T"},
                                               "chat_instance": "ci", "data": data, "message": body}}


def channel_post(text="/levels MSTR", n=1):
    """What a channel post looks like. The bot never asks Telegram for these (allowed_updates), so the dispatcher must not react."""
    return {"update_id": n, "channel_post": {"message_id": n, "date": int(time.time()), "chat": {"id": -1001, "type": "channel", "title": "c"},
                                             "text": text}}


def _sender_ids(update):
    body = update.get("message") or update.get("callback_query") or {}
    return [body["from"]["id"]] if "from" in body else []


def drive(updates, svc=None, msg_limit=None, popup_limit=None, group_limit=None, stale_callbacks=False,
          bot_username=BOT_USERNAME, check_html=True, owner_id=OWNER_ID, enroll=True, heavy_limit=None, refuse_edits=None,
          with_news=True, chart_fn=None, today=TODAY, clock=None, access_mode="approve", max_members=25, max_pending=100):
    """Push raw updates through the real dispatcher. Returns (service, recorded Telegram calls).

    `enroll=True` (default) first admits every sender as an active member AND marks them as having accepted the notice, so the
    feature tests start after the guide. Access / onboarding tests pass `enroll=False` and set the statuses themselves. Production
    code has no 'open mode'."""
    svc = svc or service()
    if enroll:
        for u in updates:
            for uid in _sender_ids(u):
                if uid != owner_id:                                    # written directly: works even for a store that is 'broken'
                    svc.store.access.setdefault(uid, {"status": "active", "invited_by": owner_id, "revoked_at": None})
                    svc.store.users.setdefault(uid, {"ack": True})
    access = Access(svc.store, owner_id, access_mode, max_members, max_pending)
    news = NewsService(svc.store, fetch=svc.fake_news) if with_news else None

    def fake_chart(symbol, bars, ref_price=None, ref_date=None, ref_label="your price"):
        svc.chart_calls.append({"symbol": symbol, "bars": len(bars), "ref_price": ref_price, "ref_date": ref_date})
        if len(bars) < 5:
            raise ValueError("not enough price history for a chart")
        return TINY_PNG

    async def go():
        sess = FakeSession(stale_callbacks=stale_callbacks, refuse_edits=refuse_edits)
        bot = Bot(TOKEN, session=sess)
        dp = build_dispatcher(svc, access, msg_limit or bs.RateLimiter(500, 3600), popup_limit or bs.RateLimiter(500, 3600),
                              bot_username=bot_username, group_limit=group_limit or bs.RateLimiter(100, 60),
                              tracker=TrackerService(svc.store, today=lambda: today), news=news, chart_fn=chart_fn or fake_chart,
                              heavy_limit=heavy_limit or bs.RateLimiter(500, 3600), today_fn=lambda: today,
                              **({"clock": clock} if clock else {}))
        for u in updates:
            await dp.feed_raw_update(bot, u)
        return sess.calls

    calls = asyncio.run(go())
    if check_html:
        for c in calls:
            if isinstance(c, (SendMessage, EditMessageText)):
                bad = tg_html.problems(c.text)
                assert not bad, f"Telegram would reject this reply: {bad}\n{c.text[:300]}"
            if isinstance(c, SendPhoto) and c.caption:
                bad = tg_html.problems(c.caption, 1024)
                assert not bad, f"Telegram would reject this caption: {bad}\n{c.caption[:300]}"
            ui_problems(c)
    return svc, calls


def ui_problems(method):
    """UI invariants for every inline keyboard the bot ever produces (SKILLS/telegram-bot-ui-design + Telegram's limits)."""
    kb = getattr(method, "reply_markup", None)
    if not kb or not hasattr(kb, "inline_keyboard"):
        return
    assert len(kb.inline_keyboard) <= 8, "too many keyboard rows"
    for row in kb.inline_keyboard:
        assert 1 <= len(row) <= 3, f"more than 3 buttons in a row: {[b.text for b in row]}"
        for b in row:
            assert b.text.strip() and len(b.text) <= 40, f"bad button text {b.text!r}"
            assert (b.callback_data is None) != (b.url is None), "a button needs exactly one of callback_data / url"
            assert len((b.callback_data or "").encode()) <= 64, f"callback data too long: {b.callback_data}"


def sent(calls):
    return [c for c in calls if isinstance(c, SendMessage)]


def texts_sent(calls):
    return [c.text for c in sent(calls)]


def edits(calls):
    return [c for c in calls if isinstance(c, EditMessageText)]


def texts_edited(calls):
    return [c.text for c in edits(calls)]


def photos(calls):
    return [c for c in calls if isinstance(c, SendPhoto)]


def documents(calls):
    return [c for c in calls if isinstance(c, SendDocument)]


def answers(calls):
    return [c for c in calls if isinstance(c, AnswerCallbackQuery)]


def keyboard_urls(send_message):
    kb = send_message.reply_markup
    return [b.url for row in kb.inline_keyboard for b in row if b.url] if kb and hasattr(kb, "inline_keyboard") else []


def buttons(method):
    """[(text, callback_data-or-url)] of the inline keyboard of a sent/edited message or photo."""
    kb = getattr(method, "reply_markup", None)
    if not kb or not hasattr(kb, "inline_keyboard"):
        return []
    return [(b.text, b.callback_data or b.url) for row in kb.inline_keyboard for b in row]


def everything_shown(calls):
    """All text a user could read: sent messages, edits, photo captions."""
    return texts_sent(calls) + texts_edited(calls) + [p.caption for p in photos(calls) if p.caption]
