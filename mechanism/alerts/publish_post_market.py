#!/usr/bin/env python3
# mechanism/alerts/publish_post_market.py
"""
The post-market package -- Daily Digest, Momentum Board, Top Gainers, Market Health -- published together as
soon as the session's market data is fresh, with per-post idempotency (mechanism/alerts/post_delivery.py,
table telegram_post_delivery) so a partial failure only retries what is actually still missing.

    python mechanism/alerts/publish_post_market.py                          # DRY RUN: shows what would be attempted
    python mechanism/alerts/publish_post_market.py --send --to prod         # refresh data, then publish
    python mechanism/alerts/publish_post_market.py --send --to prod --skip-update   # data is already fresh (called
                                                                                       # from automation_pipeline.sh
                                                                                       # right after its own steps 1-2)

Two callers:
  - automation_pipeline.sh, right after steps 1-2 (market index + daily prices) and its own freshness check,
    with --skip-update: the data is already there, this call only re-derives the target session and does the
    claim-and-send loop.
  - donchian-postmarket-retry.timer (every ~20 min, 23:45-06:00 Israel), WITHOUT --skip-update: this script
    itself refreshes market_index_updater.py + daily_data_updater.py, checks freshness, and publishes -- it
    deliberately does NOT touch weekly/monthly/fundamentals/quarterly/screener/ML (automation_pipeline.sh's
    steps 3-12), so a 20-minute retry cadence stays cheap.

Exit 0 in every "nothing wrong, just nothing to do right now" case (no completed session yet, data still
stale, everything already delivered) -- the retry timer's "becomes a safe no-op once done" requirement holds
with no special-casing in the .service file. Exit 1 only for a genuine operational failure (the updaters
themselves failing), matching automation_pipeline.sh's own step semantics.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Optional

MECH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MECH))
sys.path.insert(0, str(MECH.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(MECH.parent / ".env", override=False)

from shared import db, market_calendar  # noqa: E402
from alerts import channel_content as cx  # noqa: E402
from alerts import post_delivery  # noqa: E402
from alerts.deeplink import link as deeplink_link  # noqa: E402
from alerts.digest_format import PRIVATE_ASSISTANT_BUTTON  # noqa: E402
from alerts.send_channel_posts import load_context  # noqa: E402
from alerts.telegram_client import TelegramClient, text_length  # noqa: E402
from data_updaters.check_price_freshness import check as check_freshness  # noqa: E402

PYTHON = sys.executable
REPO_ROOT = MECH.parent

# claim-kind name -> the channel_content.py builder kind it maps to (None for daily_digest, which is not a
# single Post -- it is send_daily_digest.py's own photo+messages flow, invoked as a subprocess below).
# The three non-None values are now a deliberate identity map (both sides renamed to match on 2026-09-25 --
# see channel_content.py's KINDS comment) -- kept as an explicit dict rather than a set/tuple so the
# None-marker for daily_digest's special case stays obvious, and so this table's own shape still documents
# "these are the four claim kinds" without relying on a reader inferring it from a list.
POST_MARKET_KINDS = {"daily_digest": None, "momentum_board": "momentum_board", "top_gainers": "top_gainers", "market_health": "market_health"}


def run_step(script: str, *extra_args: str) -> None:
    """Run one updater script the same way automation_pipeline.sh does; raises on a non-zero exit."""
    cmd = [PYTHON, str(REPO_ROOT / script), *extra_args]
    print(f"Running {script} ...")
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise SystemExit(f"ABORT: {script} exited {result.returncode}")


def send_daily_digest(session: date, to: str, image: bool, buttons: bool, force: bool) -> None:
    """Subprocess, not an in-process call: send_daily_digest.py's photo+multi-message send is proven, tested
    code -- reused unchanged rather than refactored, to avoid risking its existing behavior. Its own internal
    market_calendar 'digest:{to}' mark stays as a secondary safety net; the telegram_post_delivery claim this
    module takes on "daily_digest" is the primary one that decides whether to even attempt this call again."""
    args = [str(REPO_ROOT / "mechanism/alerts/send_daily_digest.py"), "--send", "--to", to, "--date", session.isoformat()]
    if image:
        args.append("--image")
    if buttons:
        args.append("--buttons")
    if force:
        args.append("--force")
    result = subprocess.run([PYTHON, *args], cwd=REPO_ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"send_daily_digest.py exited {result.returncode}")


def send_single_post(kind: str, ctx: cx.Ctx, to: str, username: Optional[str]) -> Optional[int]:
    """One of the three channel_content.py-built posts (board / top_gainers / health). Returns the Telegram
    message_id, or None if there was nothing honest to post (not a failure -- e.g. the board has no listed
    stock that closed higher today; that claim is marked sent with no message so it is not retried forever)."""
    post = cx.build_post(kind, ctx)
    if post is None:
        return None
    tg = TelegramClient.from_env(to, dry_run=False)
    kb = None
    if post.button:
        username = username or tg.get_me()["username"]
        kb = {"inline_keyboard": [[{"text": PRIVATE_ASSISTANT_BUTTON, "url": deeplink_link(username, "ch")}]]}
    if post.image:
        if text_length(post.text) > cx.CAPTION_LIMIT:
            raise RuntimeError(f"'{kind}' caption is {text_length(post.text)} chars (limit {cx.CAPTION_LIMIT})")
        return tg.send_photo(post.image, post.text, silent=True, reply_markup=kb, kind=post.kind)
    ids = tg.send_message(post.text, disable_preview=post.disable_preview, silent=True, reply_markup=kb, kind=post.kind)
    return ids[0] if ids else None


def publish(session: date, to: str, send: bool, image: bool, buttons: bool, force: bool) -> int:
    ctx: Optional[cx.Ctx] = None
    sent, skipped, failed, dry = [], [], [], []
    for kind in POST_MARKET_KINDS:
        if not send:
            already = post_delivery.already_sent(db, session, kind, to)
            dry.append(f"{kind}: {'already delivered' if already else 'would attempt'}")
            continue
        claim = post_delivery.claim(db, session, kind, to)
        if claim is None:
            skipped.append(kind)
            print(f"SKIP {kind}: already delivered for {session} ({to}), or another run is sending it now")
            continue
        try:
            if kind == "daily_digest":
                send_daily_digest(session, to, image, buttons, force)
                message_id = None
            else:
                if ctx is None:
                    ctx = load_context(session, with_news=False, with_scoreboard=False)
                message_id = send_single_post(POST_MARKET_KINDS[kind], ctx, to, None)
            post_delivery.mark_sent(db, claim, message_id)
            sent.append(kind)
            print(f"SENT {kind}")
        except Exception as e:                        # noqa: BLE001 - one kind's failure must not stop the others
            post_delivery.mark_failed(db, claim, f"{type(e).__name__}: {e}")
            failed.append(kind)
            print(f"FAILED {kind}: {type(e).__name__}: {e}")
    if not send:
        print("DRY RUN:\n  " + "\n  ".join(dry))
        return 0
    print(f"\nPost-market package for {session} ({to}): sent={sent} skipped={skipped} failed={failed}")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--send", action="store_true", help="really claim and send (dev unless --to prod)")
    ap.add_argument("--to", choices=["dev", "prod"], default="dev")
    ap.add_argument("--skip-update", action="store_true", help="market index + daily price data is already fresh (called from automation_pipeline.sh)")
    ap.add_argument("--min-coverage", type=float, default=0.90)
    ap.add_argument("--image", action="store_true", default=True)
    ap.add_argument("--no-image", dest="image", action="store_false")
    ap.add_argument("--buttons", action="store_true", default=True)
    ap.add_argument("--no-buttons", dest="buttons", action="store_false")
    ap.add_argument("--force", action="store_true", help="bypass send_daily_digest.py's own trading-day gate (does not bypass the per-kind claim)")
    args = ap.parse_args()

    if not args.skip_update:
        run_step("mechanism/data_updaters/market_index_updater.py")
        run_step("mechanism/data_updaters/daily_data_updater.py")

    sessions, source = market_calendar.get_sessions()
    target = market_calendar.latest_completed(sessions)
    if target is None:
        print("No completed US session on the calendar yet -- nothing to do.")
        return 0
    print(f"Target session: {target} (calendar source: {source})")

    ok, msg = check_freshness(target, args.min_coverage)
    print(("FRESH: " if ok else "NOT FRESH: ") + msg)
    if not ok:
        print("Exiting without publishing -- the retry timer will try again.")
        return 0

    return publish(target, args.to, args.send, args.image, args.buttons, args.force)


if __name__ == "__main__":
    sys.exit(main())
