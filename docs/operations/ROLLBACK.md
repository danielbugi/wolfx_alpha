# Rollback — decision framework

> **Purpose:** what to do when something breaks, scenario by scenario. Concise and operational —
> for *how* each subsystem normally deploys, see [DEPLOYMENT.md](DEPLOYMENT.md).
> **Last verified:** 2026-09-25.

## The one rule that overrides every scenario below

**Windows is no longer an automatically safe production fallback.** Its copy of `trading_production`
was frozen at the VPS migration cutover and has received zero writes since — every day the VPS runs,
the two databases diverge further. Falling back to Windows today would mean silently losing
everything written to the VPS since the freeze (digest snapshots, Telegram delivery state, new
dashboard-auth sessions, ML predictions, price data for every trading day since). **Any Windows
fallback requires an explicit divergence assessment first** — at minimum, a row-count/date-range
comparison between the two databases — never a reflexive "switch back to the old thing."

## 1. Backend deployment failure

`deploy.sh` already auto-rolls-back on a failed health check — restores `PREVIOUS_GOOD_SHA` and
still exits non-zero, so the failure is visible in the workflow run. Usually nothing further to do.

If a deploy *succeeded* (healthy) but the new version is behaviorally wrong (a real bug, not a
startup failure):
```bash
# On the VPS, or via the deploy account:
/opt/donchian/scripts/rollback.sh
```
This redeploys `PREVIOUS_GOOD_SHA` — the exact previous image **and** that commit's own Compose
config (release bundles are per-commit, so a rollback can't accidentally pair an old image with a
newer, incompatible config).

## 2. Mechanism deployment failure (pipeline / bot / channel-sender)

No automatic health gate exists for this image (see [DEPLOYMENT.md](DEPLOYMENT.md) §2 for why).
Recovery is manual:
```bash
echo "<previous-good-sha>" > /opt/donchian/CURRENT_MECHANISM_SHA
echo "IMAGE_TAG=<previous-good-sha>" > /opt/donchian/env/mechanism_image_tag.env
systemctl restart donchian-bot.service   # only if the bot itself needs to move back
```
`pipeline`/`channel-sender` pick this up automatically on their next scheduled fire (they're
re-invoked fresh each time, not long-running). If the bad mechanism image broke a scheduled run
mid-way, check `journalctl -u donchian-pipeline.service` (or the relevant unit) for what actually
happened before assuming a simple re-pin fixes it — a partially-applied schema change or a
part-way-through data write needs its own investigation, not just an image swap.

## 3. Scheduler / Telegram-publishing failure

First distinguish **expected** from **broken** — see
[../architecture/SCHEDULING.md](../architecture/SCHEDULING.md) §3: a `check_price_freshness` miss
that leaves `donchian-pipeline.service` in a `failed` state is *intended* behavior, not an incident,
and `donchian-postmarket-retry.timer` (or the pipeline's own 01:00 fire) is expected to pick it back
up on its own. Only escalate if:
- Multiple scheduled retries have passed and the session is still not delivered
  (`SELECT * FROM telegram_post_delivery WHERE market_session = ...` — see
  [../architecture/DATABASE.md](../architecture/DATABASE.md) §4), or
- `PROD_SENDING_ENABLED` was unexpectedly `0` (or unset) when it should be `1`, or
- A post was sent with visibly wrong content (a data-correctness issue, not a scheduling one).

For an actual bad send already on the channel: use the Telegram Control Center dashboard (`/telegram`)
to edit or delete it — never a manual `curl` against the Telegram API outside `TelegramClient`, since
that would bypass the `telegram_messages` ledger and leave the dashboard's record of what happened
permanently wrong.

For a stuck claim (a `'reserved'` row that will never resolve because its process is truly gone):
it self-heals after `RECLAIM_AFTER_MINUTES` (10) — no manual intervention needed. Manually clearing a
`telegram_post_delivery` row should be a last resort, and only after confirming no send is actually
still in flight (clearing a row while a process is genuinely still working could cause a duplicate
send — the one failure mode this whole system exists to prevent).

## 4. Database issue

Decision point: **is a full restore actually necessary, or is this a smaller, targeted fix?**

- Bad data from one bad pipeline run (e.g. a vendor sent corrupted bars for one day) → usually a
  targeted `DELETE`/`UPDATE` for that date/symbol range, re-run the affected updater, not a restore.
- Schema corruption, a botched manual migration, or genuinely unknown-scope damage → a full restore
  is warranted. See [BACKUPS.md](BACKUPS.md) for what's available and its current limitations (the
  off-box copy is not yet at a fully independent third location — know that before treating any
  restore path as bulletproof).
- **Before any restore**: take a fresh backup of the current (possibly-damaged) state first — a
  restore is not reversible once newer data is discarded, and comparing before/after is often what
  actually explains what went wrong.
- A full restore is a Gate-1-adjacent, high-blast-radius operation. Never perform one without
  explicit approval, and never without `verify_restore.sh`-style validation against a disposable
  copy first if there's any time pressure to get it right the first time.

## 5. Larger VPS outage

1. Confirm scope first — is this a VPS-level outage (host down, network unreachable) or a
   container/service-level one? `ssh` reachability alone answers most of this.
2. If the VPS is reachable but a service is down: check `systemctl status` for the relevant unit,
   `docker ps` for the container, before assuming a full recovery is needed.
3. If the VPS is genuinely unreachable and down for an extended period: this is the one scenario
   where a Windows fallback might eventually be warranted — but **only** after the divergence
   assessment at the top of this document, and only as an explicit, approved decision, never an
   automatic "switch back" reflex. The Windows scheduled tasks are `Disabled` specifically so this
   can't happen by accident.
4. Recovery once the VPS is back: confirm Postgres started cleanly, confirm the trading-day gate's
   state (`data/session_state.json`, `telegram_post_delivery`) reflects reality before trusting the
   next scheduled run to behave correctly — an outage spanning a scheduled fire can leave state in
   an unusual but generally self-healing shape (see §3 above), but is worth a deliberate check
   rather than an assumption.

## Quick reference

| Scenario | First action | Escalates to |
|---|---|---|
| Backend deploy fails health check | Nothing — auto-rollback already happened | Investigate the failing commit |
| Backend deploy healthy but wrong | `deploy/vps/rollback.sh` | — |
| Mechanism deploy bad | Re-pin `CURRENT_MECHANISM_SHA` + `mechanism_image_tag.env`, restart `bot` if needed | Investigate what a mid-run failure left behind |
| Pipeline `failed` (freshness miss) | Nothing — expected, self-retries | Only if it persists past the next 1–2 scheduled retries |
| Bad Telegram post already sent | Edit/delete via `/telegram` dashboard | — |
| Corrupted/wrong data, known scope | Targeted `DELETE`/re-run the updater | Full restore, if scope is unknown |
| VPS unreachable | Confirm scope, wait/investigate | Windows fallback — only after divergence assessment + explicit approval |
