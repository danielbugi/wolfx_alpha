#!/usr/bin/env python3
# mechanism/alerts/run_bot.py
"""
First Light private assistant (aiogram 3, long polling). INVITE-ONLY -- see PRIVATE_ASSISTANT_PLAN.md and BOT_DESIGN_REPORT.md.

    python mechanism/alerts/run_bot.py

What a user gets (private chat, after an invitation): a 4-step guide on first contact, a persistent menu (Today's lists / Portfolio /
Watchlist / Help), the channel's daily lists with each stock shown once, a card per stock (facts, news, chart, ATR levels), and a
Watchlist + Portfolio that remember the price and the day each stock was added and show how it changed SINCE then. Typing a ticker
opens its card. Navigation edits the message in place.

Who is let in (access.py): the owner (BOT_OWNER_ID in .env) always; everyone else by a one-time invitation link (/invite) or /approve.
A person who is not let in gets ONE refusal message and nothing is stored about them.
Where it answers: PRIVATE chat only. In a GROUP one neutral pointer, never data. In a CHANNEL never. The only public interaction is
the three educational popups under the channel header (definitions, no personal content).

Every number is read from Postgres (the daily snapshot + stock_prices); the bot never calls a price provider. News comes from one
provider call per symbol per few hours, shared by all users; a chart is rendered once per symbol per session and re-sent by file_id.
Separate process from the 06:00 broadcast; only ONE copy may run per machine (a second copy would split Telegram's updates).

.env: TELEGRAM_BOT_TOKEN (required), BOT_OWNER_ID (your numeric Telegram id), BOT_ACCESS_MODE (approve | auto | closed; default approve), ALERTS_TIMEZONE (day boundary for "added on"), ALPACA_API_KEY
/ ALPACA_API_SECRET (news). Optional: BOT_MSG_LIMIT (typed messages / hour / user), BOT_POPUP_LIMIT (button taps / hour / user),
BOT_HEAVY_LIMIT (news + chart requests / hour / user), BOT_GROUP_LIMIT, BOT_LOCK_PORT. Ctrl+C stops it.
"""
import asyncio
import html
import logging
import os
import re
import socket
import sys
import time
from datetime import date
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

MECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MECH))
sys.path.insert(0, str(MECH.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(MECH.parent / ".env", override=False)

print("Loading the private assistant bot (importing aiogram takes a few seconds)...", flush=True)
from aiogram import Bot, Dispatcher, F, Router  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError  # noqa: E402
from aiogram.filters import Command, CommandObject, CommandStart  # noqa: E402
from aiogram.types import (BotCommand, BotCommandScopeChat, BufferedInputFile, CallbackQuery, ErrorEvent,  # noqa: E402
                           InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup)

from alerts import chart as chart_mod  # noqa: E402
from alerts import deeplink, insights, morning, screens, texts  # noqa: E402
from alerts.access import INVITE_TTL_HOURS, MODES, OWNER, RETENTION_DAYS, Access, parse_user_id  # noqa: E402
from alerts.bot_service import SYMBOL_RE, BotService, PgStore, RateLimiter  # noqa: E402
from alerts.performance import D, fmt_price, pct_change  # noqa: E402
from alerts.tracker import SYMBOL_TOKEN, TrackerService, parse_add_args, parse_price_shares  # noqa: E402

log = logging.getLogger("first_light_bot")
PRIVATE = F.chat.type == "private"
GROUP = F.chat.type.in_({"group", "supergroup"})
MEMBER_COMMANDS = ("start", "agree", "guide", "today", "stock", "add", "watch", "remove", "unwatch", "portfolio", "p", "watchlist", "w",
                   "mylist", "levels", "export", "deleteme", "privacy", "help", "about", "full", "aligned", "history", "week", "scan", "morning", "screen")
COMMANDS = [BotCommand(command="today", description="Today's lists from the channel"),
            BotCommand(command="portfolio", description="Your portfolio since you added it"),
            BotCommand(command="watchlist", description="Your watchlist since you added it"),
            BotCommand(command="add", description="Add a stock, e.g. /add AAPL 140.5 10"),
            BotCommand(command="stock", description="Open a stock card, e.g. /stock AAPL"),
            BotCommand(command="remove", description="Remove a stock, e.g. /remove AAPL"),
            BotCommand(command="levels", description="ATR risk framework, e.g. /levels AAPL"),
            BotCommand(command="guide", description="The quick tour"),
            BotCommand(command="help", description="All commands")]
OWNER_COMMANDS = [BotCommand(command="invite", description="One-time invitation link"),
                  BotCommand(command="approve", description="Give a Telegram id access"),
                  BotCommand(command="revoke", description="Block a Telegram id"),
                  BotCommand(command="users", description="How many people have access"),
                  BotCommand(command="requests", description="Who is waiting for access"),
                  BotCommand(command="funnel", description="Channel opens, requests, approvals"),
                  BotCommand(command="status", description="Snapshot age and access counts")]
TOO_MANY = "Too many requests - please try again in a while."
PENDING_TTL_S = 600
NAV_RE = r"^(td|sc|nw|ch|lv|aw|ah|hc|rm|ry|pf|wl|dy|cx|ex|hp|fl|al|hs|noop)(:|$)"
ADD_USAGE = ("Send a symbol, and optionally your price and shares:\n<code>/add AAPL</code> - watchlist at the last close\n"
             "<code>/add AAPL 140.5</code> - watchlist at your price\n<code>/add AAPL 140.5 10</code> - portfolio: price and shares")
BAD_NUMBER = "I could not read that. Use plain numbers with a dot, for example <code>/add AAPL 140.5 10</code>."
MENU_LABELS = {texts.MENU_TODAY: "today", texts.MENU_PORTFOLIO: "portfolio", texts.MENU_WATCHLIST: "watchlist", texts.MENU_HELP: "help"}


def menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.MENU_TODAY), KeyboardButton(text=texts.MENU_PORTFOLIO)],
                  [KeyboardButton(text=texts.MENU_WATCHLIST), KeyboardButton(text=texts.MENU_HELP)]],
        resize_keyboard=True, is_persistent=True, input_field_placeholder="Type a ticker, for example AAPL")


def to_markup(screen: screens.Screen) -> Optional[InlineKeyboardMarkup]:
    if not screen.rows:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b.text, url=b.url) if b.url else InlineKeyboardButton(text=b.text, callback_data=b.data or "noop")
         for b in row] for row in screen.rows])


def open_bot_keyboard(bot_username: str, payload: str, label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, url=deeplink.link(bot_username, payload))]])


def acquire_single_instance(port: int) -> socket.socket:
    """Hold a localhost port for the life of the process: a second copy fails to bind and exits with a clear message. (Two
    pollers on one token make Telegram answer 409 Conflict and split the updates, so buttons would seem to work at random.)"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        raise SystemExit(f"Another run_bot.py is already running on this machine (lock port {port}). "
                         "Stop it first - two copies would split Telegram's updates.")
    s.listen(1)
    return s


def build_dispatcher(service: BotService, access: Access, msg_limit: RateLimiter, popup_limit: RateLimiter,
                     bot_username: str = "", group_limit: Optional[RateLimiter] = None, tracker: Optional[TrackerService] = None,
                     news=None, chart_fn: Optional[Callable] = None, heavy_limit: Optional[RateLimiter] = None,
                     clock: Callable[[], float] = time.monotonic, today_fn: Callable[[], date] = date.today) -> Dispatcher:
    router = Router()
    group_limit = group_limit or RateLimiter(5, 60)              # replies per minute per group: never let one group flood
    heavy_limit = heavy_limit or RateLimiter(30, 3600)           # news + chart requests per user per hour
    tracker = tracker or TrackerService(service.store)
    chart_fn = chart_fn or chart_mod.render_chart
    store = service.store
    pending: Dict[int, Dict] = {}                                # uid -> the portfolio prompt waiting for "price [shares]"

    async def db(fn, *args):
        return await asyncio.to_thread(fn, *args)            # the DB layer is synchronous psycopg2

    async def safe_answer(cb: CallbackQuery, *args, **kwargs) -> None:
        """Answer a button tap. Telegram rejects answers to taps older than ~15 s (the bot was down when the user tapped):
        that must never abort the handler."""
        try:
            await cb.answer(*args, **kwargs)
        except TelegramBadRequest as e:
            log.info("stale or invalid callback ignored: %s", e)

    async def rate_ok(uid: int, refuse, limiter: RateLimiter) -> bool:
        if limiter.allow(uid):
            return True
        await refuse(TOO_MANY)
        return False

    async def admit(uid: int, refuse, limiter: RateLimiter, refusal: Optional[str] = None,
                    message: Optional[Message] = None) -> Optional[str]:
        """Rate limit (cheap, local), then authorisation. Returns the role ('owner' / 'member'), or None after the refusal was
        sent. Nothing is written for a person who is not authorised. For a typed message the refusal is the request-access screen."""
        if not await rate_ok(uid, refuse, limiter):
            return None
        role = await db(access.role, uid)
        if role is None:
            if message is not None:
                await send_refusal(message, uid)
            else:
                await refuse(refusal or texts.NOT_INVITED_POPUP)
        return role

    async def send_refusal(message: Message, uid: int, invalid_invite: bool = False) -> None:
        state = await db(access.request_state, uid)
        await send(message, screens.not_invited(uid, state, invalid_invite))

    async def notify_owner(bot, screen: screens.Screen) -> bool:
        """Tell the owner (Approve / Decline buttons). A failure never blocks the person: their request stays waiting."""
        if access.owner_id is None:
            return False
        try:
            await bot.send_message(access.owner_id, screen.text, reply_markup=to_markup(screen))
            return True
        except Exception:                                      # noqa: BLE001 - the owner may never have opened the bot
            log.info("could not notify the owner")
            return False

    async def notify_user_access(bot, uid: int) -> bool:
        """Tell an approved person they are in and start the guide. They opened the bot before, so the bot may write to them."""
        try:
            await bot.send_message(uid, texts.ACCESS_GRANTED_USER)
            guide = screens.onboarding(1)
            await bot.send_message(uid, guide.text, reply_markup=to_markup(guide), disable_web_page_preview=True)
            return True
        except Exception:                                      # noqa: BLE001 - they may have blocked the bot
            log.info("could not notify an approved user")
            return False

    # ------------------------------------------------------------------ sending / editing screens
    async def send(message: Message, screen: screens.Screen) -> None:
        await message.answer(screen.text, reply_markup=to_markup(screen), disable_web_page_preview=True)

    async def show(cb: CallbackQuery, screen: screens.Screen) -> None:
        """Navigation edits the message in place; a photo message cannot become text, and an edit Telegram refuses falls back to a
        new message."""
        msg = cb.message
        if not isinstance(msg, Message):
            return
        try:
            if msg.photo or msg.document:
                await msg.answer(screen.text, reply_markup=to_markup(screen), disable_web_page_preview=True)
            else:
                await msg.edit_text(screen.text, reply_markup=to_markup(screen), disable_web_page_preview=True)
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return
            await msg.answer(screen.text, reply_markup=to_markup(screen), disable_web_page_preview=True)

    # ------------------------------------------------------------------ data -> screens (sync, run in a thread)
    def load_today(uid: int, tab: str, page: int) -> screens.Screen:
        session = store.latest_session()
        rows = store.list_rows(session["session_date"]) if session else []
        return screens.today(session, rows, tab, page, tracker.tracked_map(uid), today_fn())

    def load_card(uid: int, sym: str, ctx: str, notice: Optional[str] = None) -> screens.Screen:
        session = store.latest_session()
        row = store.stocks(session["session_date"], [sym]).get(sym) if session else None
        return screens.stock_card(sym, row, session, tracker.view(uid, sym), ctx, today_fn(), notice)

    def load_list(uid: int, kind: str, page: int) -> screens.Screen:
        views, totals = tracker.views(uid, kind)
        return screens.tracked_list(kind, views, totals, store.latest_session(), page, today_fn())

    def load_full(uid: int, tab: str, order: str, page: int) -> screens.Screen:
        session = store.latest_session()
        cat = screens.TAB_CAT.get(tab, "breakout")
        rows = store.category_rows(session["session_date"], cat) if session else []
        return insights.full_list(session, rows, tab, order, page, tracker.tracked_map(uid), today_fn())

    def load_aligned(uid: int, page: int) -> screens.Screen:
        session = store.latest_session()
        rows = store.category_rows(session["session_date"], "breakout") if session else []
        highs = store.range_highs([r["symbol"] for r in rows], session["session_date"]) if rows else {}
        return insights.aligned_screen(session, rows, highs, tracker.tracked_map(uid), page, today_fn())

    def load_history(sym: str, ctx: str) -> screens.Screen:
        return insights.history_screen(sym, store.breakout_history(sym), ctx)

    def load_week(uid: int) -> screens.Screen:
        tracked = store.tracked(uid)
        closes = store.recent_closes([t["symbol"] for t in tracked], insights.WEEK_SESSIONS + 1) if tracked else {}
        return insights.week_screen(insights.week_rows(tracked, closes), store.latest_session(), today_fn())

    def load_screen(uid: int, spec: Dict) -> screens.Screen:
        session = store.latest_session()
        rows = store.scan_rows(session["session_date"]) if session else []
        return insights.screen_screen(session, spec, insights.run_screen(spec, rows), tracker.tracked_map(uid), today_fn())

    def scan_file() -> Optional[Tuple[bytes, str]]:
        session = store.latest_session()
        if not session:
            return None
        text = insights.scan_csv(session, store.scan_rows(session["session_date"]))
        return text.encode("utf-8-sig"), f"first_light_scan_{session['session_date']:%Y-%m-%d}.csv"

    def prompt_screen(uid: int, sym: str, ctx: str) -> screens.Screen:
        q = store.quotes([sym]).get(sym)
        return screens.hold_prompt(sym, D(q["close"]) if q and q.get("close") is not None else None, ctx, tracker.view(uid, sym))

    def levels_text(sym: str) -> str:
        return service.render_levels(sym, today_fn())

    async def gate_ack(uid: int, reply) -> bool:
        """The educational notice must be accepted before any data: otherwise the guide starts."""
        if await db(service.is_acknowledged, uid):
            return True
        await reply(screens.onboarding(1))
        return False

    async def after_admit(message: Message, uid: int, keep_pending: bool = False) -> bool:
        """For an authorised user: a command abandons a half-finished prompt, last_seen is updated, and the educational notice must
        have been accepted (otherwise the guide starts). False = the user was already answered."""
        if not keep_pending:
            pending.pop(uid, None)
        await db(service.touch_user, uid)
        return await gate_ack(uid, lambda s: send(message, s))

    async def command_gate(message: Message) -> Optional[str]:
        """Authorised + acknowledged. Returns the role, or None (the user was already answered: refusal, or the guide)."""
        uid = message.from_user.id
        role = await admit(uid, message.answer, msg_limit, message=message)
        if role is None:
            return None
        return role if await after_admit(message, uid) else None

    async def nav_gate(cb: CallbackQuery, limiter: RateLimiter, need_ack: bool = True) -> Optional[str]:
        uid = cb.from_user.id
        role = await admit(uid, lambda t: safe_answer(cb, t, show_alert=True), limiter, texts.NOT_INVITED_POPUP)
        if role is None:
            return None
        await db(service.touch_user, uid)
        if need_ack and not await db(service.is_acknowledged, uid):
            await safe_answer(cb)
            await show(cb, screens.onboarding(1))
            return None
        return role

    # ------------------------------------------------------------------ private chat: start, guide, acknowledgement
    @router.message(CommandStart(), PRIVATE)
    async def on_start(message: Message, command: CommandObject):
        uid, payload = message.from_user.id, command.args
        if not await rate_ok(uid, message.answer, msg_limit):
            return
        if payload == "ch":                                       # the channel's "Private assistant" button: an AGGREGATE counter, no user id
            await db(access.note_channel_open)
        role = await db(access.role, uid)
        if role is None:
            if not deeplink.is_invite(payload):
                await send_refusal(message, uid)
                return
            result, note = await db(access.redeem, uid, payload)
            if result == "revoked":
                await message.answer(texts.INVITE_REVOKED)
                return
            if result != "ok":
                await send_refusal(message, uid, invalid_invite=True)
                return
            role = "member"
            await message.answer(texts.WELCOME_INVITED)
            if access.owner_id is not None:                   # tell the owner who joined, so they can /revoke that id later
                try:
                    await message.bot.send_message(
                        access.owner_id, f"Invitation used ({html.escape(note or 'no note', quote=False)}): "
                                         f"Telegram id <code>{uid}</code>")
                except Exception:                             # noqa: BLE001 - the owner may never have opened the bot
                    log.info("could not notify the owner about an accepted invitation")
        await db(service.touch_user, uid)
        pending.pop(uid, None)
        if await db(service.is_acknowledged, uid):
            await message.answer("Welcome back. Use the menu below, or type a ticker such as AAPL.", reply_markup=menu_keyboard())
        else:
            await send(message, screens.onboarding(1))

    @router.message(Command("guide"), PRIVATE)
    async def on_guide(message: Message):
        uid = message.from_user.id
        if await admit(uid, message.answer, msg_limit, message=message):
            await db(service.touch_user, uid)
            await send(message, screens.onboarding(1))

    async def complete_onboarding(uid: int, target: Message, role: Optional[str]) -> None:
        await db(service.acknowledge, uid)
        await target.answer(texts.ONBOARDING_DONE, reply_markup=menu_keyboard())

    @router.callback_query(F.data.regexp(r"^(ob:[1-4]|ob:ack|ack:.*)$"))
    async def on_onboarding(cb: CallbackQuery):
        uid = cb.from_user.id
        role = await admit(uid, lambda t: safe_answer(cb, t, show_alert=True), popup_limit, texts.NOT_INVITED_POPUP)
        if role is None:
            return
        await db(service.touch_user, uid)
        arg = cb.data.split(":", 1)[1] if cb.data.startswith("ob:") else "ack"
        if arg == "ack":
            await safe_answer(cb, "Thanks")
            if isinstance(cb.message, Message):
                await show(cb, screens.Screen(texts.ONBOARDING_LAST + "\n\n<i>Accepted.</i>"))
                await complete_onboarding(uid, cb.message, role)
            else:                                              # the prompt is too old for Telegram to hand it to us: still record it
                await db(service.acknowledge, uid)
            return
        await safe_answer(cb)
        await show(cb, screens.onboarding(int(arg)))

    @router.message(Command("agree"), PRIVATE)
    async def on_agree(message: Message):
        """Typed fallback for the acknowledgement button (a tap made while the bot was down is lost)."""
        uid = message.from_user.id
        role = await admit(uid, message.answer, msg_limit, message=message)
        if role is not None:
            await db(service.touch_user, uid)
            await complete_onboarding(uid, message, role)

    @router.callback_query(F.data.startswith("def:"))
    async def on_definition(cb: CallbackQuery):
        """The ONLY public interaction: definitions of ATR / vol x / the groups, shown from the channel header. No invitation
        needed (no personal or strategy content, nothing stored), but rate-limited per user."""
        if await rate_ok(cb.from_user.id, lambda t: safe_answer(cb, t, show_alert=True), popup_limit):
            await safe_answer(cb, texts.DEFINITIONS.get(cb.data[4:], "Unknown item."), show_alert=True)

    # ------------------------------------------------------------------ private chat: the main screens
    async def do_today(message: Message, tab: str = "b"):
        await send(message, await db(load_today, message.from_user.id, tab, 0))

    async def do_list(message: Message, kind: str):
        await send(message, await db(load_list, message.from_user.id, kind, 0))

    async def do_help(message: Message, role: str):
        await message.answer(screens.help_screen(role == OWNER, INVITE_TTL_HOURS, RETENTION_DAYS).text,
                             reply_markup=to_markup(screens.help_screen(role == OWNER)), disable_web_page_preview=True)

    @router.message(Command("today"), PRIVATE)
    async def on_today(message: Message):
        if await command_gate(message):
            await do_today(message)

    @router.message(Command("portfolio", "p"), PRIVATE)
    async def on_portfolio(message: Message):
        if await command_gate(message):
            await do_list(message, "hold")

    @router.message(Command("watchlist", "w", "mylist"), PRIVATE)
    async def on_watchlist(message: Message):
        if await command_gate(message):
            await do_list(message, "watch")

    @router.message(Command("help"), PRIVATE)
    async def on_help(message: Message):
        role = await command_gate(message)
        if role:
            await do_help(message, role)
            await message.answer("The menu is below.", reply_markup=menu_keyboard())

    @router.message(Command("about"), PRIVATE)
    async def on_about(message: Message):
        if await admit(message.from_user.id, message.answer, msg_limit, message=message):
            await message.answer(texts.ABOUT)

    @router.message(Command("privacy"), PRIVATE)
    async def on_privacy(message: Message):
        if await admit(message.from_user.id, message.answer, msg_limit, message=message):
            await message.answer(texts.PRIVACY)

    async def open_card(message: Message, sym: str, notice: Optional[str] = None, ctx: str = "x"):
        await send(message, await db(load_card, message.from_user.id, sym, ctx, notice))

    @router.message(Command("stock"), PRIVATE)
    async def on_stock(message: Message, command: CommandObject):
        if not await command_gate(message):
            return
        toks = (command.args or "").split()
        if len(toks) != 1 or not SYMBOL_TOKEN.match(toks[0]):
            await message.answer("Send one symbol, for example <code>/stock AAPL</code>, or just type the ticker.")
            return
        await open_card(message, toks[0].replace("$", "").upper().rstrip(".-"))

    @router.message(Command("levels"), PRIVATE)
    async def on_levels(message: Message, command: CommandObject):
        if await command_gate(message):
            await message.answer(await db(levels_text, command.args or ""), disable_web_page_preview=True)

    @router.message(Command("add", "watch"), PRIVATE)
    async def on_add(message: Message, command: CommandObject):
        if not await command_gate(message):
            return
        uid = message.from_user.id
        args = parse_add_args(command.args)
        if args.error in ("no_symbol",):
            await message.answer(ADD_USAGE)
            return
        if args.error == "bad_number":
            await message.answer(BAD_NUMBER)
            return
        if args.error:
            await message.answer("Give one symbol at a time when you include a price, for example <code>/add AAPL 140.5 10</code>.")
            return
        if len(args.symbols) == 1:
            sym = args.symbols[0]
            if args.shares is not None:
                res = await db(tracker.add_hold, uid, sym, args.price, args.shares)
            else:
                res = await db(tracker.add_watch, uid, sym, args.price)
            await open_card(message, sym, screens.add_notice(res))
            return
        results = [await db(tracker.add_watch, uid, s) for s in args.symbols]
        await message.answer(screens.multi_add_summary(results))

    @router.message(Command("remove", "unwatch"), PRIVATE)
    async def on_remove(message: Message, command: CommandObject):
        if not await command_gate(message):
            return
        toks = (command.args or "").split()
        if len(toks) != 1 or not SYMBOL_TOKEN.match(toks[0]):
            await message.answer("Send one symbol, for example <code>/remove AAPL</code>.")
            return
        sym = toks[0].replace("$", "").upper().rstrip(".-")
        n = await db(tracker.remove, message.from_user.id, sym)
        await message.answer(f"Removed {html.escape(sym, quote=False)} from your lists." if n
                             else f"{html.escape(sym, quote=False)} is not on your lists.")

    async def send_export(message: Message, uid: int):
        csv_text = await db(tracker.export_csv, uid)
        if csv_text.count("\n") <= 1:                          # header only
            await message.answer(texts.EXPORT_EMPTY)
            return
        await message.answer_document(BufferedInputFile(csv_text.encode("utf-8-sig"), filename="first_light_export.csv"),
                                      caption="Everything you saved. Prices are end-of-day.")

    @router.message(Command("export"), PRIVATE)
    async def on_export(message: Message):
        if await command_gate(message):
            await send_export(message, message.from_user.id)

    @router.message(Command("full"), PRIVATE)
    async def on_full(message: Message, command: CommandObject):
        if not await command_gate(message):
            return
        arg = (command.args or "").strip().lower()
        tab = "n" if arg.startswith(("near", "n")) else "b"
        await send(message, await db(load_full, message.from_user.id, tab, "g", 0))

    @router.message(Command("aligned"), PRIVATE)
    async def on_aligned(message: Message):
        if await command_gate(message):
            await send(message, await db(load_aligned, message.from_user.id, 0))

    @router.message(Command("history"), PRIVATE)
    async def on_history(message: Message, command: CommandObject):
        if not await command_gate(message):
            return
        toks = (command.args or "").split()
        if len(toks) != 1 or not SYMBOL_TOKEN.match(toks[0]):
            await message.answer("Send one symbol, for example <code>/history AAPL</code>.")
            return
        await send(message, await db(load_history, toks[0].replace("$", "").upper().rstrip(".-"), "x"))

    @router.message(Command("week"), PRIVATE)
    async def on_week(message: Message):
        if await command_gate(message):
            await send(message, await db(load_week, message.from_user.id))

    @router.message(Command("scan"), PRIVATE)
    async def on_scan(message: Message):
        if not await command_gate(message):
            return
        if not await rate_ok(message.from_user.id, message.answer, heavy_limit):
            return
        got = await db(scan_file)
        if not got:
            await message.answer("There is no scan data yet. Try again after the next daily scan.")
            return
        data, name = got
        await message.answer_document(BufferedInputFile(data, filename=name),
                                      caption="Every stock in the daily scan with its group and facts. End-of-day data, educational.")

    @router.message(Command("morning"), PRIVATE)
    async def on_morning(message: Message, command: CommandObject):
        if not await command_gate(message):
            return
        uid = message.from_user.id
        arg = (command.args or "").strip().lower()
        if arg == "on":
            session = await db(store.latest_session)
            await db(store.dm_on, uid, session["session_date"] if session else None)
            await message.answer(texts.MORNING_ON)
        elif arg == "off":
            await db(store.dm_forget, uid)
            await message.answer(texts.MORNING_OFF)
        elif arg == "":
            await message.answer(texts.MORNING_STATUS_ON if await db(store.dm_status, uid) else texts.MORNING_STATUS_OFF)
        else:
            await message.answer(texts.MORNING_USAGE)

    @router.message(Command("screen"), PRIVATE)
    async def on_screen(message: Message, command: CommandObject):
        if not await command_gate(message):
            return
        spec, err = insights.parse_screen(command.args)
        if command.args is None or (err and not (command.args or "").strip()):
            await message.answer(insights.SCREEN_USAGE)
            return
        if err:
            await message.answer(html.escape(err, quote=False) + "\n\n" + insights.SCREEN_USAGE)
            return
        await send(message, await db(load_screen, message.from_user.id, spec))

    @router.message(Command("deleteme"), PRIVATE)
    async def on_deleteme(message: Message):
        if await command_gate(message):
            await send(message, screens.erase_confirm())

    # ------------------------------------------------------------------ button taps (edit in place)
    def sym_of(token: str) -> Optional[str]:
        return token.upper() if SYMBOL_RE.match(token.upper()) else None

    async def send_chart(cb: CallbackQuery, uid: int, sym: str, ctx: str) -> None:
        session = await db(store.latest_session)
        if not session:
            await safe_answer(cb, "There is no scan data yet.", show_alert=True)
            return
        sd = session["session_date"]
        view = await db(tracker.view, uid, sym)
        quote = (await db(store.quotes, [sym])).get(sym)
        close = quote["close"] if quote else None
        day_pct = pct_change(D(quote["close"]), D(quote["prev_close"])) if quote and quote.get("prev_close") else None
        cached = None if view else await db(store.chart_file_id, sym, sd)   # a personal chart (with the user's price line) is not shared
        photo, png, adjusted = cached, None, False
        if not cached:
            bars = await db(store.bars, sym, 126)
            personal = view is not None and view.note is None
            try:
                png = await asyncio.to_thread(chart_fn, sym, bars, float(view.ref_price) if personal else None,
                                              view.ref_date if personal else None,
                                              f"your price {fmt_price(view.ref_price)}" if personal else "your price")
            except ValueError:
                await safe_answer(cb, "Not enough price history for a chart.", show_alert=True)
                return
            photo = BufferedInputFile(png, filename=f"{sym}.png")
            first = bars[0]["date"]
            adjusted = any(d > first for d in (await db(store.history_facts, [(sym, first)])).get(sym, {}).get("jump_dates", []))
        caption, rows = screens.chart_caption(sym, close, day_pct, quote["date"] if quote else None, view, ctx, adjusted)
        sent = await cb.message.answer_photo(photo, caption=caption, reply_markup=to_markup(screens.Screen("", rows)))
        if png is not None and view is None and sent.photo:
            await db(store.save_chart_file_id, sym, sd, sent.photo[-1].file_id)

    @router.callback_query(F.data.regexp(NAV_RE))
    async def on_nav(cb: CallbackQuery):
        uid = cb.from_user.id
        parts = (cb.data or "").split(":")
        action = parts[0]
        if action == "noop":
            await safe_answer(cb)
            return
        if not isinstance(cb.message, Message):
            await safe_answer(cb, "This message is too old - use the menu below.", show_alert=True)
            return
        if not await nav_gate(cb, popup_limit):
            return
        sym = sym_of(parts[1]) if len(parts) > 1 and action in ("sc", "nw", "ch", "lv", "aw", "ah", "hc", "rm", "ry", "hs") else None
        ctx = parts[2] if len(parts) > 2 else "x"
        if action in ("sc", "nw", "ch", "lv", "aw", "ah", "hc", "rm", "ry", "hs") and not sym:
            await safe_answer(cb, "Unknown item.", show_alert=True)
            return
        pending.pop(uid, None)                                 # tapping anywhere else abandons a half-finished prompt
        if action == "td":
            await safe_answer(cb)
            tab = parts[1] if len(parts) > 1 else "b"
            page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            await show(cb, await db(load_today, uid, tab, page))
        elif action == "sc":
            await safe_answer(cb)
            await show(cb, await db(load_card, uid, sym, ctx))
        elif action == "fl":
            await safe_answer(cb)
            tab = parts[1] if len(parts) > 1 else "b"
            order = parts[2] if len(parts) > 2 else "g"
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            await show(cb, await db(load_full, uid, tab, order, page))
        elif action == "al":
            await safe_answer(cb)
            page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            await show(cb, await db(load_aligned, uid, page))
        elif action == "hs":
            await safe_answer(cb)
            await show(cb, await db(load_history, sym, ctx))
        elif action in ("pf", "wl"):
            await safe_answer(cb)
            page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            await show(cb, await db(load_list, uid, "hold" if action == "pf" else "watch", page))
        elif action == "lv":
            await safe_answer(cb)
            await show(cb, screens.levels_screen(sym, await db(levels_text, sym), ctx))
        elif action == "nw":
            if not await rate_ok(uid, lambda t: safe_answer(cb, t, show_alert=True), heavy_limit):
                return
            await safe_answer(cb, "Fetching headlines...")
            result = await db(news.get, sym) if news is not None else None
            await show(cb, screens.news_screen(sym, result, ctx))
        elif action == "ch":
            if not await rate_ok(uid, lambda t: safe_answer(cb, t, show_alert=True), heavy_limit):
                return
            await safe_answer(cb, "Drawing the chart...")
            await send_chart(cb, uid, sym, ctx)
        elif action == "aw":
            res = await db(tracker.add_watch, uid, sym, None)
            await safe_answer(cb, "Added to your watchlist" if res.code == "ok" else "Done")
            await show(cb, await db(load_card, uid, sym, ctx, screens.add_notice(res)))
        elif action == "ah":
            await safe_answer(cb)
            pending[uid] = {"symbol": sym, "ctx": ctx, "exp": clock() + PENDING_TTL_S}
            await show(cb, await db(prompt_screen, uid, sym, ctx))
        elif action == "hc":
            res = await db(tracker.add_hold, uid, sym, None, None)
            pending.pop(uid, None)
            await safe_answer(cb, "Saved to your portfolio" if res.code in ("ok", "updated") else "Done")
            await show(cb, await db(load_card, uid, sym, ctx, screens.add_notice(res)))
        elif action == "rm":
            await safe_answer(cb)
            row = await db(tracker.get, uid, sym)
            if not row:
                await show(cb, await db(load_card, uid, sym, ctx))
            else:
                await show(cb, screens.remove_confirm(sym, row["kind"], ctx))
        elif action == "ry":
            n = await db(tracker.remove, uid, sym)
            await safe_answer(cb, "Removed" if n else "It was not on your lists")
            await show(cb, await db(load_card, uid, sym, ctx, "Removed from your lists." if n else None))
        elif action == "ex":
            await safe_answer(cb)
            await send_export(cb.message, uid)
        elif action == "hp":
            await safe_answer(cb)
            role = await db(access.role, uid)
            await show(cb, screens.help_screen(role == OWNER, INVITE_TTL_HOURS, RETENTION_DAYS))
        elif action == "dy":
            n = await db(tracker.delete_everything, uid)
            await safe_answer(cb, "Erased")
            await show(cb, screens.Screen(texts.DELETED if n else texts.NOTHING_TO_ERASE))
        elif action == "cx":
            await safe_answer(cb)
            await show(cb, screens.Screen("Cancelled."))

    # ------------------------------------------------------------------ private chat: owner only
    # The filter is the owner's id. Anyone else typing these commands falls through to the catch-all below, which treats them as
    # unknown text (a refusal for people who are not let in) -- the owner commands are never revealed.
    OWNER_ONLY = F.from_user.id.in_({access.owner_id} if access.owner_id is not None else set())     # no owner = matches nobody

    @router.message(Command("invite"), PRIVATE, OWNER_ONLY)
    async def on_invite(message: Message, command: CommandObject):
        uid = message.from_user.id
        if not await rate_ok(uid, message.answer, msg_limit):
            return
        payload = await db(access.create_invite, uid, command.args)
        if not bot_username:
            await message.answer("The bot's username is unknown, so a link cannot be built.")
            return
        await message.answer(f"One-time invitation (valid {INVITE_TTL_HOURS} hours). Send it privately to the person:\n"
                             f"<code>{deeplink.link(bot_username, payload)}</code>", disable_web_page_preview=True)

    @router.message(Command("approve"), PRIVATE, OWNER_ONLY)
    async def on_approve(message: Message, command: CommandObject):
        target = parse_user_id(command.args)
        if not await rate_ok(message.from_user.id, message.answer, msg_limit):
            return
        if not target:
            await message.answer("Send the numeric Telegram id, for example: /approve 123456789")
            return
        waiting = await db(store.request_status, target)
        await message.answer(await db(access.approve, message.from_user.id, target))
        if waiting and waiting["status"] == "pending" and not access.is_owner(target):
            await notify_user_access(message.bot, target)              # they asked in the bot, so tell them they are in

    @router.message(Command("revoke"), PRIVATE, OWNER_ONLY)
    async def on_revoke(message: Message, command: CommandObject):
        target = parse_user_id(command.args)
        if await rate_ok(message.from_user.id, message.answer, msg_limit):
            await message.answer(await db(access.revoke, message.from_user.id, target) if target
                                 else "Send the numeric Telegram id, for example: /revoke 123456789")

    @router.message(Command("users"), PRIVATE, OWNER_ONLY)
    async def on_users(message: Message):
        if await rate_ok(message.from_user.id, message.answer, msg_limit):
            c = await db(access.summary)
            await message.answer(f"Access: {c['active']} active · {c['revoked']} revoked · {c['open_invites']} open invitation(s). "
                                 "The owner is not counted.")

    @router.message(Command("status"), PRIVATE, OWNER_ONLY)
    async def on_status(message: Message):
        if not await rate_ok(message.from_user.id, message.answer, msg_limit):
            return
        session, c = await db(store.latest_session), await db(access.summary)
        if session:
            age = (today_fn() - session["session_date"]).days
            snap = f"{session['session_date']:%a %d %b} ({age} day(s) old) · {session['universe_n']:,} stocks"
        else:
            snap = "none yet"
        await message.answer(f"<b>Status</b>\nSnapshot: {snap}\nAccess: {c['active']} active · {c['revoked']} revoked · "
                             f"{c['open_invites']} open invitation(s)")

    # ------------------------------------------------------------------ request access (strangers) and the owner's decisions
    @router.callback_query(F.data.regexp(r"^rq:(new|info|back)$"))
    async def on_request(cb: CallbackQuery):
        """Open to anyone (a stranger has no access yet) but rate-limited. NOTHING is stored unless the person taps Request access."""
        uid = cb.from_user.id
        if not await rate_ok(uid, lambda t: safe_answer(cb, t, show_alert=True), popup_limit):
            return
        action = cb.data[3:]
        if action in ("info", "back"):
            await safe_answer(cb)
            state = await db(access.request_state, uid)
            await show(cb, screens.what_is_this(state["can_request"]) if action == "info" else screens.not_invited(uid, state))
            return
        res = await db(access.request, uid)
        if res.code == "sent":
            await safe_answer(cb, texts.REQUEST_POPUP_SENT)
            await show(cb, screens.Screen(texts.REQUEST_SENT))
            await notify_owner(cb.bot, screens.owner_request(uid))
        elif res.code == "auto_approved":
            await safe_answer(cb, texts.ACCESS_GRANTED_USER)
            await db(service.touch_user, uid)
            await show(cb, screens.Screen(texts.ACCESS_GRANTED_USER))
            if isinstance(cb.message, Message):
                await send(cb.message, screens.onboarding(1))
            await notify_owner(cb.bot, screens.Screen(texts.OWNER_AUTO_NOTICE.format(uid=uid)))
        else:
            popup = {"pending": texts.REQUEST_POPUP_ALREADY, "closed": texts.REQUEST_POPUP_CLOSED, "full": texts.REQUEST_POPUP_FULL,
                     "blocked": texts.REQUEST_POPUP_BLOCKED, "member": texts.REQUEST_POPUP_MEMBER,
                     "cooldown": texts.REQUEST_POPUP_COOLDOWN.format(date=screens._day(res.until))}[res.code]
            await safe_answer(cb, popup, show_alert=True)

    @router.callback_query(F.data.regexp(r"^r[ad]:[0-9]{1,15}$"), OWNER_ONLY)
    async def on_request_decision(cb: CallbackQuery):
        approve, target = cb.data[1] == "a", int(cb.data[3:])
        if approve:
            result = await db(access.approve_request, cb.from_user.id, target)
            if result != "approved":
                await safe_answer(cb, texts.OWNER_NO_REQUEST.format(uid=target), show_alert=True)
                return
            told = await notify_user_access(cb.bot, target)
            await safe_answer(cb, "Approved")
            await show(cb, screens.Screen(texts.OWNER_APPROVED.format(uid=target) + ("" if told else texts.OWNER_NOT_NOTIFIED)))
            return
        result = await db(access.decline_request, cb.from_user.id, target)
        if result != "declined":
            await safe_answer(cb, texts.OWNER_NO_REQUEST.format(uid=target), show_alert=True)
            return
        status = await db(store.request_status, target)
        told = True
        try:
            await cb.bot.send_message(target, texts.ACCESS_DECLINED_USER.format(date=screens._day(status.get("cooldown_until") if status else None)))
        except Exception:                                      # noqa: BLE001
            told = False
        await safe_answer(cb, "Declined")
        await show(cb, screens.Screen(texts.OWNER_DECLINED.format(uid=target) + ("" if told else texts.OWNER_NOT_NOTIFIED)))

    @router.callback_query(F.data.regexp(r"^r[ad]:[0-9]{1,15}$"))                    # well-formed but pressed by a non-owner
    async def on_request_decision_denied(cb: CallbackQuery):
        await safe_answer(cb, "Only the owner can do this.", show_alert=True)

    @router.message(Command("requests"), PRIVATE, OWNER_ONLY)
    async def on_requests(message: Message):
        if await rate_ok(message.from_user.id, message.answer, msg_limit):
            count, rows = await db(access.waiting, 4)
            await send(message, screens.requests_list(count, rows))

    @router.message(Command("funnel"), PRIVATE, OWNER_ONLY)
    async def on_funnel(message: Message):
        if await rate_ok(message.from_user.id, message.answer, msg_limit):
            m7, m30 = await db(access.funnel, 7), await db(access.funnel, 30)
            await send(message, screens.funnel_screen(m7, m30))

    # ------------------------------------------------------------------ private chat: typed text (menu buttons, price replies, tickers)
    async def take_pending(uid: int) -> Optional[Dict]:
        p = pending.get(uid)
        if p and p["exp"] < clock():
            pending.pop(uid, None)
            return None
        return p

    @router.message(PRIVATE)
    async def on_text(message: Message):
        uid = message.from_user.id
        role = await admit(uid, message.answer, msg_limit, message=message)
        if role is None:
            return
        source = getattr(getattr(message, "forward_origin", None), "chat", None) or getattr(message, "forward_from_chat", None)
        if role == OWNER and source is not None:                 # the owner forwarded a post: tell them that chat's id (for .env)
            await message.answer(f"That post is from <b>{html.escape(source.title or 'a chat', quote=False)}</b> ({source.type}).\n"
                                 f"Chat id: <code>{source.id}</code>\n"
                                 "Put it in .env as TELEGRAM_DEV_CHAT_ID (the test channel) or TELEGRAM_CHAT_ID (production, at launch).")
            return
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            await message.answer("Send /help to see what I can do, or use the menu below.")
            return
        if not await after_admit(message, uid, keep_pending=text not in MENU_LABELS):
            return
        if text in MENU_LABELS:                                # the persistent menu
            action = MENU_LABELS[text]
            if action == "today":
                await do_today(message)
            elif action == "portfolio":
                await do_list(message, "hold")
            elif action == "watchlist":
                await do_list(message, "watch")
            else:
                await do_help(message, role)
            return
        p = await take_pending(uid)
        if p:                                                  # the reply to "send your price and shares"
            price, shares, err = parse_price_shares(text)
            if err:
                await message.answer("I could not read that. Send the price, then the shares, for example <code>140.5 10</code> "
                                     "- or tap Cancel on the message above.")
                return
            pending.pop(uid, None)
            res = await db(tracker.add_hold, uid, p["symbol"], price, shares)
            await open_card(message, p["symbol"], screens.add_notice(res), p["ctx"])
            return
        toks = text.split()
        if len(toks) == 1 and SYMBOL_TOKEN.match(toks[0]):     # a bare ticker opens its card
            await open_card(message, toks[0].replace("$", "").upper().rstrip(".-"))
            return
        await message.answer("Type a ticker such as AAPL, or use the menu below.")

    # ------------------------------------------------------------------ group / supergroup: never data, only a pointer
    @router.message(Command(*MEMBER_COMMANDS), GROUP)
    async def on_group_command(message: Message):
        if message.from_user is None or not group_limit.allow(message.chat.id):
            return
        kb = open_bot_keyboard(bot_username, "help", texts.OPEN_PRIVATE_CHAT) if bot_username else None
        await message.reply(texts.GROUP_POINTER, reply_markup=kb)

    # ------------------------------------------------------------------ errors
    dp = Dispatcher()
    dp.include_router(router)

    @dp.errors()
    async def on_error(event: ErrorEvent):
        """Anything that escapes a handler (database down, a Telegram hiccup): log it, tell the user, keep the bot alive."""
        log.exception("handler failed: %r", event.exception)
        upd = event.update
        try:
            if upd.callback_query:
                await upd.callback_query.answer(texts.ERROR_REPLY, show_alert=True)
            elif upd.message:
                await upd.message.answer(texts.ERROR_REPLY)
        except Exception:                                      # noqa: BLE001 - the error reply itself must not raise
            log.exception("could not deliver the error reply")
        return True

    return dp


def owner_id_from_env() -> Optional[int]:
    raw = os.getenv("BOT_OWNER_ID", "").strip()
    if not raw:
        return None
    if not re.fullmatch(r"[0-9]{1,15}", raw):                  # ASCII digits only: \d also matches e.g. full-width digits
        raise SystemExit("BOT_OWNER_ID in .env must be your numeric Telegram id (digits only).")
    return int(raw)


async def morning_round(bot, store, access, today: Optional[date] = None) -> Dict[str, int]:
    """One pass of the morning message: everyone opted in and not yet handled for the latest session gets a message only if one of their own
    stocks changed. A person who lost access or blocked the bot is switched off (no retry storm); any other send failure is retried next pass."""
    plan = await asyncio.to_thread(morning.plan_round, store, today)
    counts = {"sent": 0, "nothing": 0, "switched_off": 0, "failed": 0}
    for uid, text, sd in plan:
        if await asyncio.to_thread(access.role, uid) is None:
            await asyncio.to_thread(store.dm_forget, uid)
            counts["switched_off"] += 1
            continue
        if text:
            try:
                await bot.send_message(uid, text, disable_web_page_preview=True)
            except TelegramForbiddenError:
                await asyncio.to_thread(store.dm_forget, uid)
                counts["switched_off"] += 1
                continue
            except Exception:                                    # noqa: BLE001 - not marked, so the next pass tries again
                log.warning("morning message failed for a member; will retry")
                counts["failed"] += 1
                continue
            counts["sent"] += 1
        else:
            counts["nothing"] += 1
        await asyncio.to_thread(store.dm_mark, uid, sd)
    return counts


async def morning_loop(bot, store, access, every_s: int = 300) -> None:
    while True:
        try:
            counts = await morning_round(bot, store, access)
            if counts["sent"] or counts["switched_off"] or counts["failed"]:
                log.info("morning round: %s", counts)
        except Exception:                                        # noqa: BLE001 - a bad pass must not end the loop
            log.exception("morning round failed")
        await asyncio.sleep(every_s)


async def purge_loop(access: Access) -> None:
    """Once a day: delete the saved data of users revoked more than RETENTION_DAYS ago."""
    while True:
        try:
            n = await asyncio.to_thread(access.purge)
            if n:
                log.info("purged saved data of %d revoked user(s)", n)
        except Exception:                                      # noqa: BLE001 - never let housekeeping stop the bot
            log.exception("purge failed")
        await asyncio.sleep(24 * 3600)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token or re.search(r"your[_-]?token|changeme|xxxx", token, re.I):
        raise SystemExit("TELEGRAM_BOT_TOKEN is empty/placeholder in .env")
    owner_id = owner_id_from_env()
    lock = acquire_single_instance(int(os.getenv("BOT_LOCK_PORT", "47831")))    # noqa: F841 - held for the process lifetime
    from alerts.news_service import NewsService
    from shared import db as database

    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    store = PgStore(database)
    mode = os.getenv("BOT_ACCESS_MODE", "approve").strip().lower()
    if mode not in MODES:
        raise SystemExit(f"BOT_ACCESS_MODE in .env must be one of {MODES} (got {mode!r}).")
    access = Access(store, owner_id, mode, int(os.getenv("BOT_MAX_MEMBERS", "25")), int(os.getenv("BOT_MAX_PENDING", "100")))
    log.info("Access mode: %s (approve = the owner decides each request, auto = accept up to BOT_MAX_MEMBERS, closed = no requests)", mode)
    tracker = TrackerService(store, tz=os.getenv("ALERTS_TIMEZONE", "Asia/Jerusalem"))
    dp = build_dispatcher(BotService(store), access,
                          RateLimiter(int(os.getenv("BOT_MSG_LIMIT", "120")), 3600),
                          RateLimiter(int(os.getenv("BOT_POPUP_LIMIT", "300")), 3600),
                          me.username, RateLimiter(int(os.getenv("BOT_GROUP_LIMIT", "5")), 60), tracker=tracker,
                          news=NewsService(store), heavy_limit=RateLimiter(int(os.getenv("BOT_HEAVY_LIMIT", "30")), 3600))
    if owner_id is None:
        log.warning("BOT_OWNER_ID is not set: nobody new can be let in (fail closed). Send /start to @%s to see your Telegram id, "
                    "put it in .env as BOT_OWNER_ID and restart.", me.username)
    else:
        log.info("Starting @%s (invite-only; owner id set)", me.username)
    if me.can_join_groups:
        log.warning("The bot can be added to groups. It never shows data there, but turn 'Allow Groups' off in BotFather.")
    await bot.set_my_commands(COMMANDS)
    if owner_id is not None:
        try:
            await bot.set_my_commands(COMMANDS + OWNER_COMMANDS, scope=BotCommandScopeChat(chat_id=owner_id))
        except TelegramBadRequest:                             # the owner has not opened the bot yet: the menu is set next start
            log.info("owner command menu not set (the owner has not started the bot yet)")
    purge_task = asyncio.create_task(purge_loop(access))
    morning_task = asyncio.create_task(morning_loop(bot, store, access))
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        purge_task.cancel()
        morning_task.cancel()


if __name__ == "__main__":
    if sys.platform == "win32":                                # aiodns (used by aiohttp for DNS) refuses Windows' default Proactor loop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
