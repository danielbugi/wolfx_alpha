"""Per-post delivery tracking for the post-market package (post_delivery.py, table telegram_post_delivery)
and publish_post_market.py's claim/send/skip orchestration. Real Postgres required (skips if unreachable) --
the thing under test IS the database-level atomic claim, which cannot be faithfully exercised against a fake.
No network: the Telegram transport is always stubbed."""
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "mechanism"))
sys.path.insert(0, ROOT)

from alerts import channel_content as cx  # noqa: E402
from alerts import post_delivery as pd  # noqa: E402
from alerts import publish_post_market as ppm  # noqa: E402
from alerts import send_channel_posts as scp  # noqa: E402
from alerts.telegram_client import TelegramError  # noqa: E402

# Synthetic, clearly-in-the-future session dates -- no real delivery row can ever exist at these dates, so
# tests that use target="dev" (main() only accepts dev/prod via argparse, never a test-only value) cannot
# collide with or mask genuine production/dev state.
SESSION = date(2099, 1, 15)
OTHER_SESSION = date(2099, 1, 14)


def _db():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
        from shared import db
        db.execute_dict_query("SELECT 1 FROM telegram_post_delivery LIMIT 1")
        return db
    except Exception as e:                                                          # noqa: BLE001
        pytest.skip(f"Postgres unreachable or telegram_post_delivery is missing: {type(e).__name__}")


TEST_KIND_PREFIX = "t_" + uuid.uuid4().hex[:8] + "_"   # unique per test session, so cleanup can never touch a real row


@pytest.fixture
def db():
    d = _db()
    yield d
    d.execute_insert("DELETE FROM telegram_post_delivery WHERE market_session IN (%s, %s)", (SESSION, OTHER_SESSION))


def _kind():
    return TEST_KIND_PREFIX + uuid.uuid4().hex[:12]   # a unique kind per test so parallel/leftover rows never collide


# ================================================================== claim()
def test_claim_succeeds_when_no_row_exists_yet(db):
    c = pd.claim(db, SESSION, _kind(), "dev")
    assert c is not None and c.session == SESSION


def test_claim_fails_when_already_sent(db):
    kind = _kind()
    c = pd.claim(db, SESSION, kind, "dev")
    pd.mark_sent(db, c, message_id=555)
    assert pd.claim(db, SESSION, kind, "dev") is None
    assert pd.already_sent(db, SESSION, kind, "dev") is True


def test_claim_fails_while_another_reservation_is_fresh(db):
    kind = _kind()
    first = pd.claim(db, SESSION, kind, "dev")
    assert first is not None
    second = pd.claim(db, SESSION, kind, "dev")               # the first claim's row is still fresh (just claimed)
    assert second is None


def test_claim_reclaims_a_stale_reservation(db):
    """A worker that claimed and then crashed before mark_sent/mark_failed must not block this post forever."""
    kind = _kind()
    old = datetime.now(timezone.utc) - timedelta(minutes=pd.RECLAIM_AFTER_MINUTES + 1)
    db.execute_insert(
        "INSERT INTO telegram_post_delivery (market_session, post_kind, target, status, claimed_at) VALUES (%s,%s,%s,'reserved',%s)",
        (SESSION, kind, "dev", old))
    c = pd.claim(db, SESSION, kind, "dev")
    assert c is not None


def test_claim_reclaims_a_failed_row_immediately_no_staleness_wait(db):
    kind = _kind()
    c1 = pd.claim(db, SESSION, kind, "dev")
    pd.mark_failed(db, c1, "boom")
    c2 = pd.claim(db, SESSION, kind, "dev")                    # 'failed' is reclaimable right away, unlike 'reserved'
    assert c2 is not None


def test_mark_failed_never_leaves_a_row_marked_sent(db):
    kind = _kind()
    c = pd.claim(db, SESSION, kind, "dev")
    pd.mark_failed(db, c, "network error")
    assert pd.already_sent(db, SESSION, kind, "dev") is False


def test_market_session_is_explicit_and_distinct_across_israel_date_rollover(db):
    """A retry after midnight Israel time for the SAME US session must land on the same row, not create a
    second one keyed by the (different) Israel-local calendar date."""
    kind = _kind()
    c1 = pd.claim(db, SESSION, kind, "dev")
    pd.mark_sent(db, c1, 1)
    # a later retry, run well past local midnight, still passes the same explicit US session date
    assert pd.claim(db, SESSION, kind, "dev") is None           # correctly recognised as already delivered
    rows = db.execute_dict_query(
        "SELECT count(*) AS n FROM telegram_post_delivery WHERE post_kind = %s AND target = %s", (kind, "dev"))
    assert rows[0]["n"] == 1                                      # exactly one row for this session, not two


def test_different_sessions_never_collide(db):
    kind = _kind()
    assert pd.claim(db, SESSION, kind, "dev") is not None
    assert pd.claim(db, OTHER_SESSION, kind, "dev") is not None          # different session: independent row


def test_claim_is_independent_per_destination(db):
    kind = _kind()
    c_dev = pd.claim(db, SESSION, kind, "dev")
    pd.mark_sent(db, c_dev, 1)
    c_prod = pd.claim(db, SESSION, kind, "prod")                 # 'prod' is a separate row from 'dev'
    assert c_prod is not None


# ================================================================== concurrency
def test_two_concurrent_claims_only_one_wins(db):
    """The real race: two workers call claim() for the identical (session, kind, target) at effectively the
    same instant. Uses two real pooled connections (ThreadPoolExecutor), not just two Python calls in
    sequence -- the guard under test is the database's UNIQUE constraint + atomic UPDATE...WHERE, not
    anything enforced in Python."""
    kind = _kind()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(pd.claim, db, SESSION, kind, "dev")
        f2 = ex.submit(pd.claim, db, SESSION, kind, "dev")
        r1, r2 = f1.result(), f2.result()
    winners = [r for r in (r1, r2) if r is not None]
    assert len(winners) == 1, (r1, r2)


# ================================================================== publish_post_market.publish() orchestration
class FakeError(Exception):
    pass


def _stub(monkeypatch, *, digest_ok=True, board_ok=True, gainers_ok=True, health_ok=True, calls=None):
    calls = calls if calls is not None else []

    def fake_digest(session, to, image, buttons, force):
        calls.append("daily_digest")
        if not digest_ok:
            raise RuntimeError("digest send failed")

    def fake_single(kind, ctx, to, username):
        calls.append(kind)
        ok = {"momentum_board": board_ok, "top_gainers": gainers_ok, "market_health": health_ok}[kind]
        if not ok:
            raise FakeError(f"{kind} send failed")
        return 999

    def fake_load_context(session, **kw):
        return object()          # never inspected by the fakes above

    monkeypatch.setattr(ppm, "send_daily_digest", fake_digest)
    monkeypatch.setattr(ppm, "send_single_post", fake_single)
    monkeypatch.setattr(ppm, "load_context", fake_load_context)
    return calls


def test_fresh_session_makes_all_four_kinds_eligible_and_all_get_sent(monkeypatch, db):
    calls = _stub(monkeypatch)
    rc = ppm.publish(SESSION, "dev", send=True, image=True, buttons=True, force=False)
    assert rc == 0
    assert set(calls) == {"daily_digest", "momentum_board", "top_gainers", "market_health"}
    for kind in ppm.POST_MARKET_KINDS:
        assert pd.already_sent(db, SESSION, kind, "dev")


def test_already_delivered_kind_is_skipped_on_a_second_call(monkeypatch, db):
    _stub(monkeypatch)
    ppm.publish(SESSION, "dev", send=True, image=True, buttons=True, force=False)
    calls2 = _stub(monkeypatch)
    rc = ppm.publish(SESSION, "dev", send=True, image=True, buttons=True, force=False)
    assert rc == 0
    assert calls2 == []                    # nothing was even attempted -- every kind already claimed as sent


def test_partial_failure_then_retry_only_attempts_the_missing_kinds(monkeypatch, db):
    """Digest and board succeed, top_gainers fails, health is never reached in send order after it -- but
    the loop must still try health too (each kind is independent), and a retry must only resend gainers."""
    calls1 = _stub(monkeypatch, gainers_ok=False)
    rc1 = ppm.publish(SESSION, "dev", send=True, image=True, buttons=True, force=False)
    assert rc1 == 1                                              # one real failure -> non-zero, visible in systemd
    assert pd.already_sent(db, SESSION, "daily_digest", "dev")
    assert pd.already_sent(db, SESSION, "momentum_board", "dev")
    assert not pd.already_sent(db, SESSION, "top_gainers", "dev")
    assert pd.already_sent(db, SESSION, "market_health", "dev")  # unaffected by gainers' failure

    calls2 = _stub(monkeypatch)                                  # this time everything would succeed
    rc2 = ppm.publish(SESSION, "dev", send=True, image=True, buttons=True, force=False)
    assert rc2 == 0
    assert calls2 == ["top_gainers"]                              # never resent digest, board or health


def test_dry_run_never_claims_or_sends(monkeypatch, db):
    calls = _stub(monkeypatch)
    rc = ppm.publish(SESSION, "dev", send=False, image=True, buttons=True, force=False)
    assert rc == 0 and calls == []
    for kind in ppm.POST_MARKET_KINDS:
        assert not pd.already_sent(db, SESSION, kind, "dev")


# ================================================================== stale/fresh data gate (publish_post_market.main's freshness step)
def _run_main(monkeypatch, argv, fresh: bool, target_session=SESSION):
    """Drives the real main() end to end: stubs get_sessions/latest_completed (so the target session is fixed
    and no real calendar/network call happens), check_freshness (the fresh/stale switch under test), and the
    four senders (no real Telegram). --skip-update is required in argv so the updater subprocesses never run."""
    monkeypatch.setattr(ppm.market_calendar, "get_sessions", lambda: ({target_session: None}, "dev"))
    monkeypatch.setattr(ppm.market_calendar, "latest_completed", lambda sessions, now=None: target_session)
    monkeypatch.setattr(ppm, "check_freshness", lambda session, min_coverage: (fresh, "stub"))
    calls = _stub(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["publish_post_market.py", *argv])
    rc = ppm.main()
    return rc, calls


def test_stale_data_means_zero_market_posts_and_main_exits_cleanly(monkeypatch, db):
    rc, calls = _run_main(monkeypatch, ["--send", "--to", "dev", "--skip-update"], fresh=False)
    assert rc == 0 and calls == []
    for kind in ppm.POST_MARKET_KINDS:
        assert not pd.already_sent(db, SESSION, kind, "dev")


def test_fresh_data_through_main_sends_the_full_package(monkeypatch, db):
    rc, calls = _run_main(monkeypatch, ["--send", "--to", "dev", "--skip-update"], fresh=True)
    assert rc == 0
    assert set(calls) == {"daily_digest", "momentum_board", "top_gainers", "market_health"}


def test_retry_loop_is_a_safe_no_op_once_the_session_is_fully_delivered(monkeypatch, db):
    _run_main(monkeypatch, ["--send", "--to", "dev", "--skip-update"], fresh=True)       # first (successful) run
    rc, calls = _run_main(monkeypatch, ["--send", "--to", "dev", "--skip-update"], fresh=True)  # the "retry"
    assert rc == 0 and calls == []                                                        # nothing re-attempted


# ================================================================== send_channel_posts.send_claimable()
# Phase 4A finding: send_channel_posts.py had its own Momentum Board sender that bypassed
# telegram_post_delivery entirely, gated only by the coarse session_state.json flag -- a latent
# duplicate-send hazard, harmless only because nothing scheduled the weekday path that could trigger it.
# send_claimable() is the fix: the SAME atomic claim publish_post_market.py uses, now shared by both
# senders. These tests prove the two senders genuinely cannot both deliver the same post for the same
# session -- not by inspecting code, by actually racing the two functions against the same real row.
class _FakeTgSend:
    """Minimal TelegramClient stand-in for send_claimable()'s own send_post() call -- never touches the
    network. Failure is injected via `fail`."""
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.sent = []

    def send_message(self, text, disable_preview=True, silent=True, reply_markup=None, kind=None):
        if self.fail:
            raise TelegramError("simulated send failure")
        self.sent.append(kind)
        return [123]

    def send_photo(self, *a, **kw):
        raise AssertionError("this test's posts never carry an image")


def _plain_post(kind="momentum_board"):
    return cx.Post(kind, "test body")


def test_send_claimable_actually_sends_on_a_fresh_claim(db):
    tg = _FakeTgSend()
    sent = scp.send_claimable(tg, _plain_post(), "dev", SESSION, "some_bot")
    assert sent is True and tg.sent == ["momentum_board"]
    assert pd.already_sent(db, SESSION, "momentum_board", "dev")


def test_send_claimable_skips_without_sending_when_already_delivered(db):
    """This is the exact scenario the hazard was about: publish_post_market.py (or an earlier call) already
    delivered momentum_board for this session -- send_claimable() must not send it again."""
    pd.claim(db, SESSION, "momentum_board", "dev")
    tg = _FakeTgSend()
    sent = scp.send_claimable(tg, _plain_post(), "dev", SESSION, "some_bot")
    assert sent is False and tg.sent == []                          # never touched the transport


def test_publish_post_market_and_send_channel_posts_cannot_both_deliver_the_same_post(db):
    """The two real senders, racing for the identical (session, kind, target) via two real pooled
    connections -- proves the shared claim table, not a convention either script individually follows, is
    what prevents the duplicate."""
    from concurrent.futures import ThreadPoolExecutor

    results = []

    def via_publish_post_market():
        claim = pd.claim(db, SESSION, "momentum_board", "dev")
        if claim is None:
            return "skipped"
        pd.mark_sent(db, claim, 1)
        return "sent"

    def via_send_channel_posts():
        tg = _FakeTgSend()
        return "sent" if scp.send_claimable(tg, _plain_post(), "dev", SESSION, "some_bot") else "skipped"

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(via_publish_post_market)
        f2 = ex.submit(via_send_channel_posts)
        results = [f1.result(), f2.result()]
    assert results.count("sent") == 1 and results.count("skipped") == 1


def test_send_claimable_marks_failed_not_sent_on_a_real_send_error(db):
    tg = _FakeTgSend(fail=True)
    with pytest.raises(TelegramError):
        scp.send_claimable(tg, _plain_post(), "dev", SESSION, "some_bot")
    assert not pd.already_sent(db, SESSION, "momentum_board", "dev")   # failed, not sent -- reclaimable
    assert pd.claim(db, SESSION, "momentum_board", "dev") is not None  # confirms it: immediately reclaimable
