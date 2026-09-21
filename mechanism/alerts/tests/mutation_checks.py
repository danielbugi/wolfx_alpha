#!/usr/bin/env python3
"""
Mutation checks for the private assistant and the channel tooling (PRIVATE_ASSISTANT_PLAN.md section 8, ground rule 7).

A green test suite only proves the tests pass. This script proves they can FAIL: it re-introduces one realistic bug at a time, runs the
suite, and expects a failure. A mutant the suite does not catch ("MISSED") is a hole in the tests. Every mutant is restored in a `finally`.

    python mechanism/alerts/tests/mutation_checks.py --check            # fast: are all patterns still present in the code? (no tests run)
    python mechanism/alerts/tests/mutation_checks.py                    # run everything (39 mutants, ~10-20 minutes)
    python mechanism/alerts/tests/mutation_checks.py --group flow       # only one group: access | tracker | flow | guards

Groups (39 mutants, all caught on 2026-09-21): access (9, phase 7.0), tracker (16), flow (10, request-access), guards (4: production lock +
channel isolation). Not named test_*.py, so pytest does not collect it. If `--check` reports NOT FOUND, the code moved: update that pattern
(the bug it models is still worth testing) - do not delete the mutant.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
A = ROOT / "mechanism" / "alerts"

GROUPS = {
    "access": [                                                        # phase 7.0: invite-only access, groups, logs
        ("auth bypass: role() admits everybody", A / "access.py",
         '        return MEMBER if self.store.access_status(uid) == "active" else None', "        return MEMBER"),
        ("group reply leaks the levels card", A / "run_bot.py",
         "        await message.reply(texts.GROUP_POINTER, reply_markup=kb)",
         "        await message.reply(await db(service.render_levels, 'MSTR'), disable_web_page_preview=True)"),
        ("owner commands open to every user", A / "run_bot.py",
         "OWNER_ONLY = F.from_user.id.in_({access.owner_id} if access.owner_id is not None else set())", "OWNER_ONLY = F.from_user.id > 0"),
        ("invitation code written to the log", A / "run_bot.py",
         "            result, note = await db(access.redeem, uid, payload)",
         "            log.info('redeem %s', payload)\n            result, note = await db(access.redeem, uid, payload)"),
        ("stranger data stored before the access check", A / "run_bot.py",
         "        role = await db(access.role, uid)\n        if role is None:\n            if not deeplink.is_invite(payload):",
         "        await db(service.touch_user, uid)\n        role = await db(access.role, uid)\n        if role is None:\n            if not deeplink.is_invite(payload):"),
        ("revoked user can redeem a new invitation", A / "bot_service.py",
         '            if row and row[0] == "revoked":\n                conn.rollback()\n                return "revoked", None',
         '            if False:\n                return "revoked", None'),
        ("per-ticker levels button back in the channel header", A / "digest_format.py",
         '        [{"text": PRIVATE_ASSISTANT_BUTTON, "url": deeplink.link(bot_username, "ch")}],',
         '        [{"text": PRIVATE_ASSISTANT_BUTTON, "url": deeplink.link(bot_username, "ch")}, '
         '{"text": "MSTR levels", "url": deeplink.link(bot_username, "lv_MSTR")}],'),
        ("purge deletes active users too (PgStore SQL)", A / "bot_service.py",
         "WHERE status = 'revoked' AND revoked_at < NOW()",
         "WHERE status IN ('revoked', 'active') AND NOW() > NOW() - INTERVAL '1 day' OR revoked_at < NOW()"),
        ("database error admits people (fail open)", A / "run_bot.py",
         "        role = await db(access.role, uid)\n        if role is None:\n            if message is not None:",
         "        try:\n            role = await db(access.role, uid)\n        except Exception:\n            role = 'member'\n        if role is None:\n            if message is not None:"),
    ],
    "tracker": [                                                       # watchlist / portfolio / performance / news / chart
        ("percentage maths wrong (no -1)", A / "performance.py", "    return (now / ref - 1) * 100", "    return (now / ref) * 100"),
        ("banker's rounding instead of half-up", A / "performance.py", "return x.quantize(exp, rounding=ROUND_HALF_UP)",
         "return x.quantize(exp, rounding=__import__('decimal').ROUND_HALF_EVEN)"),
        ("split guard switched off", A / "performance.py", "    if not facts:\n        return False\n    ref_date", "    return False\n    ref_date"),
        ("common split factors not recognised", A / "price_guard.py",
         "    return any(abs(r / f - 1) <= SPLIT_TOLERANCE for f in SPLIT_FACTORS)", "    return False"),
        ("SQL leaks other users' tracked rows", A / "bot_service.py", '"WHERE telegram_user_id = %s ORDER BY symbol", (uid,))',
         '"WHERE telegram_user_id <> -1 AND %s IS NOT NULL ORDER BY symbol", (uid,))'),
        ("button taps skip the access gate", A / "run_bot.py",
         "        if not await nav_gate(cb, popup_limit):\n            return\n        sym = sym_of", "        if False:\n            return\n        sym = sym_of"),
        ("data shown before the notice is accepted", A / "run_bot.py",
         "        await reply(screens.onboarding(1))\n        return False", "        return True"),
        ("news fetched on every request (no cache)", A / "news_service.py",
         "    def _fresh(self, state: Optional[Dict]) -> bool:\n        if not state:\n            return False",
         "    def _fresh(self, state: Optional[Dict]) -> bool:\n        return False\n        if not state:\n            return False"),
        ("chart rendered on every request (no file_id reuse)", A / "run_bot.py",
         "        cached = None if view else await db(store.chart_file_id, sym, sd)", "        cached = None"),
        ("half-finished prompt survives a command", A / "run_bot.py", "        if not keep_pending:\n            pending.pop(uid, None)", "        pass"),
        ("a stock appears on both tabs", A / "screens.py",
         '    by_tab = {t: order_rows([r for r in listed if r["category"] == c]) for t, c in TAB_CAT.items()}',
         "    by_tab = {t: order_rows([r for r in listed if True]) for t, c in TAB_CAT.items()}"),
        ("watchlist cap not enforced", A / "tracker.py", "CAP_WATCH = 25", "CAP_WATCH = 999"),
        ("re-pricing wipes the saved shares", A / "tracker.py",
         '            final_shares = shares if shares is not None else (D(existing.get("shares")) if existing and existing["kind"] == "hold" else None)',
         "            final_shares = shares"),
        ("a holding is downgraded by add_watch", A / "tracker.py", '        if existing and existing["kind"] == "hold" and kind == "watch":', "        if False:"),
        ("a typed price is ignored (last close used)", A / "tracker.py", "        ref = price if price is not None else last", "        ref = last"),
        ("erase-everything deletes every user's data", A / "tracker.py", "        return self.store.remove_tracked(uid, None)",
         "        return self.store.remove_tracked(uid, None) + self.store.remove_tracked(uid + 1, None)"),
    ],
    "flow": [                                                          # request-access flow
        ("a person is stored just for looking at the screen", A / "access.py",
         '        if self.role(uid) is not None:\n            return {"can_request": False, "reason": "member", "until": None}',
         '        self.store.add_request(uid)\n        if self.role(uid) is not None:\n            return {"can_request": False, "reason": "member", "until": None}'),
        ("the decline cool-down is ignored", A / "access.py", 'if req and req.get("cooldown_active"):', "if False:"),
        ("closed mode still accepts requests", A / "access.py",
         '        if self.mode == "closed":\n            return {"can_request": False, "reason": "closed", "until": None}', "        pass"),
        ("a revoked person can ask again", A / "access.py",
         '        if self.store.access_status(uid) == "revoked":\n            return {"can_request": False, "reason": "blocked", "until": None}', "        pass"),
        ("anyone can press Approve / Decline", A / "run_bot.py",
         '@router.callback_query(F.data.regexp(r"^r[ad]:[0-9]{1,15}$"), OWNER_ONLY)', '@router.callback_query(F.data.regexp(r"^r[ad]:[0-9]{1,15}$"))'),
        ("approving works for someone who never asked", A / "access.py",
         '        if self.store.decide_request(uid, True) is None:\n            return "none"\n        self.store.set_access(uid, "active", owner)',
         '        self.store.decide_request(uid, True)\n        self.store.set_access(uid, "active", owner)'),
        ("decline deletes instead of remembering the cool-down", A / "access.py",
         "        if self.store.decide_request(uid, False) is None:", "        if self.store.decide_request(uid, True) is None:"),
        ("the waiting-list cap is ignored", A / "access.py", "if self.store.pending_requests(0)[0] >= self.max_pending:", "if False:"),
        ("auto mode ignores its member cap", A / "access.py", 'self.store.access_counts()["active"] < self.max_members', "True"),
        ("stale requests are never purged", A / "access.py",
         "        self.store.purge_requests(PENDING_DAYS, COOLDOWN_DAYS)     # unanswered requests and finished cool-downs are deleted too\n", ""),
    ],
    "guards": [                                                        # production lock + channel isolation
        ("production launch lock removed", A / "telegram_client.py",
         'if target == "prod" and not dry_run and not prod_sending_enabled():', "if False:"),
        ("digest sender imports the assistant's screens", A / "send_daily_digest.py",
         "from alerts.alert_builder import Skip  # noqa: E402", "from alerts.alert_builder import Skip  # noqa: E402\nfrom alerts import screens  # noqa: E402,F401"),
        ("the assistant tour is pointed at the dev channel", A / "qa_live.py",
         'TelegramClient.from_env("owner", dry_run=False)', 'TelegramClient.from_env("dev", dry_run=False)'),
        ("the replay sends the tour to the channel by default", ROOT / "replay_dev_channel.ps1", "if ($Tour) {", "if ($true) {"),
    ],
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Mutation checks: prove the tests can fail")
    ap.add_argument("--check", action="store_true", help="only verify that every pattern is still present in the code (fast, runs no tests)")
    ap.add_argument("--group", choices=sorted(GROUPS), help="run one group only")
    args = ap.parse_args()
    chosen = {args.group: GROUPS[args.group]} if args.group else GROUPS
    total = sum(len(v) for v in chosen.values())
    survived, missing = [], []
    n = 0
    for group, mutants in chosen.items():
        print(f"\n== {group} ({len(mutants)} mutants)")
        for name, path, old, new in mutants:
            n += 1
            original = path.read_text(encoding="utf-8")
            if original.count(old) < 1:
                print(f"[NOT FOUND] {name}: the pattern is gone from {path.name} - update it")
                missing.append(name)
                continue
            if args.check:
                print(f"[ok      ] {name}")
                continue
            try:
                path.write_text(original.replace(old, new, 1), encoding="utf-8")
                r = subprocess.run([sys.executable, "-m", "pytest", "mechanism/alerts/tests", "-q", "-x", "-p", "no:cacheprovider"],
                                   cwd=ROOT, capture_output=True, text=True, timeout=900)
                tail = [ln for ln in r.stdout.strip().splitlines() if ln.strip()][-1]
                caught = r.returncode != 0
                print(f"[{'CAUGHT' if caught else 'MISSED'}] ({n}/{total}) {name}  ->  {tail}", flush=True)
                if not caught:
                    survived.append(name)
            finally:
                path.write_text(original, encoding="utf-8")                # always restore the original file
    print(f"\n{total} mutants | patterns not found: {missing or 'none'}" + ("" if args.check else f" | SURVIVING (test holes): {survived or 'none'}"))
    return 1 if (missing or survived) else 0


if __name__ == "__main__":
    sys.exit(main())
