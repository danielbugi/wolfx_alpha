#!/usr/bin/env python3
# mechanism/alerts/qa_live.py
"""
Live QA for the First Light bot -- the checks a fake network cannot make.

    python mechanism/alerts/qa_live.py                # READ-ONLY health check (no message is sent anywhere)
    python mechanism/alerts/qa_live.py --send-to-owner  # also send every distinct bot screen to YOUR PRIVATE CHAT with the bot, labelled [QA n/N], silent

Health check: bot identity, registered commands vs the code, webhook / pending updates, whether the bot process is running
(via its single-instance lock port), chat types and the bot's admin rights, snapshot freshness and ATR coverage.
--send-to-owner: renders the real replies (levels for several real stocks, /help, /about, the acknowledgement prompt with its button,
the refusal / invitation notices, the group pointer with its button, the channel header keyboard) from the live snapshot and sends them to
your private chat with the bot (BOT_OWNER_ID) - NEVER a channel: assistant screens belong in the private chat, the channels carry only
data, promotion, news and information. Telegram itself is the judge of the HTML and the keyboards: a reply it rejects is reported here
with its error text. The bot token is never printed.
"""
import argparse
import html
import os
import socket
import sys
from datetime import date
from pathlib import Path

MECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MECH))
sys.path.insert(0, str(MECH.parent))

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(MECH.parent / ".env", override=False)

from alerts import deeplink, texts  # noqa: E402
from alerts.bot_service import BotService, PgStore  # noqa: E402
from alerts.digest_format import header_keyboard  # noqa: E402
from alerts.telegram_client import TelegramClient, TelegramError  # noqa: E402

API = "https://api.telegram.org"
EXPECTED_COMMANDS = {"today", "portfolio", "watchlist", "add", "stock", "remove", "levels", "guide", "help"}      # the default menu; the owner's extra menu is per-chat
results = []          # (status, what, detail)


def record(status: str, what: str, detail: str = "") -> None:
    results.append((status, what, detail))
    print(f"  [{status:4}] {what}" + (f" - {detail}" if detail else ""))


def call(token: str, method: str, **payload) -> dict:
    return requests.post(f"{API}/bot{token}/{method}", json=payload, timeout=20).json()


def health(token: str) -> str:
    print("HEALTH CHECK (read-only)")
    me = call(token, "getMe")["result"]
    record("PASS", "bot identity", f"@{me['username']}")
    record("PASS" if os.getenv("BOT_OWNER_ID", "").isdigit() else "FAIL", "BOT_OWNER_ID is set in .env",
           "invite-only mode" if os.getenv("BOT_OWNER_ID", "").isdigit() else "unset: nobody can use the bot; send /start to see your id")
    record("PASS" if not me.get("can_join_groups") else "WARN", "bot cannot be added to groups",
           "" if not me.get("can_join_groups") else "BotFather > /mybots > this bot > Bot Settings > Allow Groups > Turn off")
    cmds = {c["command"] for c in call(token, "getMyCommands").get("result", [])}
    missing = EXPECTED_COMMANDS - cmds
    record("PASS" if not missing else "FAIL", "command menu matches the code",
           "all registered" if not missing else f"missing {sorted(missing)} - restart run_bot.py to register them")
    hook = call(token, "getWebhookInfo")["result"]
    record("PASS" if not hook.get("url") else "FAIL", "no webhook (polling needs none)", hook.get("url", ""))
    pending = hook.get("pending_update_count", 0)
    record("PASS" if pending == 0 else "WARN", "no unanswered updates waiting", f"{pending} pending" if pending else "the bot is keeping up")

    lock_port = int(os.getenv("BOT_LOCK_PORT", "47831"))
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", lock_port))
            record("FAIL", "bot process is running", f"nothing holds lock port {lock_port}: start it with  python mechanism/alerts/run_bot.py")
        except OSError:
            record("PASS", "bot process is running", f"lock port {lock_port} is held (exactly one copy can hold it)")

    for label, key in (("dev", "TELEGRAM_DEV_CHAT_ID"), ("prod", "TELEGRAM_CHAT_ID")):
        chat = call(token, "getChat", chat_id=os.getenv(key))
        if not chat.get("ok"):
            record("FAIL", f"{label} chat reachable", chat.get("description", ""))
            continue
        kind = chat["result"]["type"]
        member = call(token, "getChatMember", chat_id=os.getenv(key), user_id=me["id"]).get("result", {})
        record("PASS" if member.get("status") == "administrator" else "WARN", f"{label} chat is a {kind}; bot is {member.get('status')}")
    record("INFO", "where the bot answers",
           "private chat: invited users only; groups: a pointer, never data; channel: never (only the 3 educational popups are public)")

    from shared import db
    needed = {"bot_access", "bot_invites", "bot_audit", "bot_tracked", "news_items", "news_fetched", "bot_chart_cache", "bot_requests", "funnel_events"}
    have = {r["table_name"] for r in db.execute_dict_query(
        "SELECT table_name FROM information_schema.tables WHERE table_name = ANY(%s)", (sorted(needed),))}
    record("PASS" if not needed - have else "FAIL", "assistant tables exist (access, tracker, news, chart cache)",
           "" if not needed - have else f"missing {sorted(needed - have)}: apply mechanism/add_assistant_tables.sql, add_tracker_tables.sql and add_access_flow_tables.sql")
    keys_ok = bool(os.getenv("ALPACA_API_KEY")) and bool(os.getenv("ALPACA_API_SECRET"))
    record("PASS" if keys_ok else "WARN", "news provider keys (ALPACA_API_KEY / ALPACA_API_SECRET) are set",
           "" if keys_ok else "the News button will say 'not available' until they are set")
    run = db.execute_dict_query("SELECT session_date, universe_n FROM digest_runs ORDER BY session_date DESC LIMIT 1")
    if not run:
        record("FAIL", "snapshot exists", "digest_runs is empty: run send_daily_digest.py --snapshot-only")
    else:
        age = (date.today() - run[0]["session_date"]).days
        record("PASS" if age <= 4 else "WARN", "snapshot is fresh", f"session {run[0]['session_date']} ({age} days old), {run[0]['universe_n']} liquid stocks")
        cov = db.execute_dict_query("SELECT COUNT(*) n, COUNT(atr) a FROM digest_stocks WHERE session_date = %s", (run[0]["session_date"],))[0]
        record("PASS" if cov["n"] and cov["a"] == cov["n"] else "FAIL", "every stock has an ATR (needed by /levels)", f"{cov['a']}/{cov['n']}")
    return me["username"]


def replies(bot_username: str):
    """(label, text, keyboard) for every distinct reply the bot can produce, rendered from the live snapshot."""
    from shared import db
    svc = BotService(PgStore(db))
    sample = [r["symbol"] for r in db.execute_dict_query(
        "SELECT symbol FROM digest_stocks WHERE session_date = (SELECT MAX(session_date) FROM digest_runs) AND category = 'breakout' "
        "AND atr IS NOT NULL ORDER BY (list_ranks IS NULL), symbol LIMIT 2")]
    near = [r["symbol"] for r in db.execute_dict_query(
        "SELECT symbol FROM digest_stocks WHERE session_date = (SELECT MAX(session_date) FROM digest_runs) AND category = 'near_breakout' "
        "AND atr IS NOT NULL ORDER BY symbol LIMIT 1")]
    none_group = [r["symbol"] for r in db.execute_dict_query(
        "SELECT symbol FROM digest_stocks WHERE session_date = (SELECT MAX(session_date) FROM digest_runs) AND category IS NULL "
        "AND atr IS NOT NULL ORDER BY dv20 DESC LIMIT 1")]
    wild = [r["symbol"] for r in db.execute_dict_query(
        "SELECT symbol FROM digest_stocks WHERE session_date = (SELECT MAX(session_date) FROM digest_runs) AND atr IS NOT NULL AND close > 0 "
        "AND 2 * atr >= close ORDER BY symbol LIMIT 1")]
    ack_kb = {"inline_keyboard": [[{"text": texts.ACK_BUTTON, "callback_data": "ack:"}]]}
    open_kb = lambda payload: {"inline_keyboard": [[{"text": texts.OPEN_PRIVATE_CHAT, "url": deeplink.link(bot_username, payload)}]]}  # noqa: E731
    from alerts import screens
    from alerts.tracker import TrackerService
    store = PgStore(db)
    session = store.latest_session()
    rows = store.list_rows(session["session_date"]) if session else []
    tracker = TrackerService(store)

    def kb_of(screen):
        return {"inline_keyboard": [[{"text": b.text, **({"url": b.url} if b.url else {"callback_data": b.data or "noop"})} for b in row]
                                    for row in screen.rows]} if screen.rows else None

    out = []
    for n in (1, 2, 3, 4):
        sc = screens.onboarding(n)
        out.append((f"onboarding step {n}/4", sc.text, kb_of(sc)))
    sc = screens.today(session, rows, "b", 0, {})
    out.append(("today's lists - breakout tab (live snapshot)", sc.text, kb_of(sc)))
    sc = screens.today(session, rows, "n", 0, {})
    out.append(("today's lists - near-breakout tab (live snapshot)", sc.text, kb_of(sc)))
    if rows:
        top = sorted(rows, key=lambda r: -len(r["list_ranks"]))[0]
        sc = screens.stock_card(top["symbol"], top, session, None, "tb0")
        out.append((f"stock card {top['symbol']} (live snapshot, not tracked)", sc.text, kb_of(sc)))
        sc = screens.hold_prompt(top["symbol"], None, "tb0")
        out.append(("portfolio prompt", sc.text, kb_of(sc)))
    sc = screens.tracked_list("hold", [], None, session, 0)
    out.append(("portfolio - empty", sc.text, kb_of(sc)))
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    zero = {k: 0 for k in ("opened_channel", "requested", "approved", "finished_guide", "activated", "active_7d", "waiting", "declined_cooling")}
    funnel_sample = {**zero, "opened_channel": 12, "requested": 5, "approved": 3, "finished_guide": 3, "activated": 2, "active_7d": 3, "waiting": 1}
    for label, screen in (
            ("ACCESS: a stranger opens the bot (Request access)", screens.not_invited(123456789, {"reason": None})),
            ("ACCESS: what is this?", screens.what_is_this(True)),
            ("ACCESS: request sent", screens.Screen(texts.REQUEST_SENT)),
            ("ACCESS: request waiting", screens.not_invited(123456789, {"reason": "pending"})),
            ("ACCESS: access closed", screens.not_invited(123456789, {"reason": "closed"})),
            ("ACCESS: declined, cooling down", screens.not_invited(123456789, {"reason": "cooldown", "until": now})),
            ("OWNER: a request arrives (Approve / Decline)", screens.owner_request(123456789)),
            ("OWNER: /requests", screens.requests_list(2, [{"telegram_user_id": 123456789, "requested_at": now}, {"telegram_user_id": 987654321, "requested_at": now}])),
            ("OWNER: /funnel", screens.funnel_screen(funnel_sample, {**funnel_sample, "opened_channel": 40, "requested": 12, "approved": 8})),
            ("APPROVED: the person is told", screens.Screen(texts.ACCESS_GRANTED_USER))):
        out.append((label, screen.text, kb_of(screen)))
    out += [("onboarding done + quick start", texts.ONBOARDING_DONE, None), ("/privacy", texts.PRIVACY, None), ("/help", texts.HELP, None),
            ("/about", texts.ABOUT, None)]
    for sym in sample + near + none_group:
        out.append((f"/levels {sym}", svc.render_levels(sym), None))
    if wild:
        out.append((f"/levels {wild[0]} (2xATR >= price: must say 'not available')", svc.render_levels(wild[0]), None))
    out += [("/levels ZZZZ (unknown symbol)", svc.render_levels("ZZZZ"), None),
            ("private: not invited", texts.NOT_INVITED.format(uid=123456789), None),
            ("private: invitation not valid", texts.INVITE_INVALID.format(uid=123456789), None),
            ("private: invitation accepted", texts.WELCOME_INVITED, None),
            ("group: pointer + button", texts.GROUP_POINTER, open_kb("help")),
            ("error reply", texts.ERROR_REPLY, None),
            ("channel header keyboard: 3 popups + the neutral assistant link",
             "Header keyboard test. This is the only button set the public channel carries.", None)]
    out.append(("  header keyboard", "Header buttons (popups need the bot running):", header_keyboard(bot_username)))
    return out


def qa_body(k: int, n: int, label: str, text: str) -> str:
    """Labelled QA message. The LABEL is escaped (it may contain < or >); `text` is already valid Telegram HTML."""
    return f"<b>[QA {k}/{n}]</b> {html.escape(label.strip(), quote=False)}\n———\n{text}"


def send_tour(bot_username: str, only: str = "") -> None:
    print("\nSEND TO YOUR PRIVATE CHAT WITH THE BOT (Telegram judges the HTML and the keyboards)")
    tg = TelegramClient.from_env("owner", dry_run=False)
    items = replies(bot_username)
    for k, (label, text, kb) in enumerate(items, 1):
        if only and only.lower() not in label.lower():
            continue
        body = qa_body(k, len(items), label, text)
        try:
            tg.send_message(body, silent=True, reply_markup=kb)
            record("PASS", f"Telegram accepted: {label.strip()}")
        except TelegramError as e:
            record("FAIL", f"Telegram REJECTED: {label.strip()}", str(e)[:160])


def main() -> int:
    ap = argparse.ArgumentParser(description="Live QA for the First Light bot")
    ap.add_argument("--send-to-owner", "--send-to-dev", dest="send_tour", action="store_true",
                    help="send every distinct bot screen to YOUR PRIVATE CHAT with the bot (labelled, silent). Never a channel. "
                         "(--send-to-dev is the old name and now does the same)")
    ap.add_argument("--only", default="", help="with --send-to-owner: send only the screens whose label contains this text")
    args = ap.parse_args()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set in .env")
    username = health(token)
    if args.send_tour:
        send_tour(username, args.only)
    fails = [r for r in results if r[0] == "FAIL"]
    print(f"\n{sum(r[0] == 'PASS' for r in results)} passed, {sum(r[0] == 'WARN' for r in results)} warnings, {len(fails)} FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
