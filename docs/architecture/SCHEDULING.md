# Scheduling — production timers

> **Purpose:** the authoritative scheduler map. **Production scheduling = systemd on the VPS.**
> Windows Task Scheduler is a development/reference/rollback artifact from before the 2026-09
> migration — it is **not** active production scheduling, and its 6 jobs are confirmed `Disabled`.
> **Source of truth:** `deploy/vps/donchian-*.service`, `deploy/vps/donchian-*.timer` — verbatim,
> SHA-256-verified copies of what's installed at `/etc/systemd/system/` on the VPS. These are a
> **snapshot, not a live sync**: re-diff before trusting them to reflect a change made directly on
> the VPS after 2026-09-25 — see `deploy/vps/README.md`'s "Scheduling" section for the verification
> command.
> **Last verified:** 2026-09-25, read directly off the live VPS via root SSH.

## 1. All timers, one table

All Israel-local schedules use systemd's native per-timer `Asia/Jerusalem` calendar tag — DST-safe
by construction — **except `donchian-nightly-backup.timer`**, which predates that convention and
hardcodes a UTC offset (a known, low-priority inconsistency: it will drift an hour at the next DST
change; not yet fixed).

| Timer | Israel schedule | Executable | Purpose | Updates data? | Sends Telegram? |
|---|---|---|---|---|---|
| `donchian-pipeline.timer` | 23:45, 01:00 | `run_pipeline.sh` → `automation_pipeline.sh` (in the `pipeline` compose service) | Full 13-step daily pipeline | Yes (price/index, weekly, monthly, fundamentals, quarterly) | Yes — calls the post-market publisher inline (step ~3) and again as a retry (step 13) |
| `donchian-postmarket-retry.timer` | 23:45, then every ~20 min through 06:00 | `run_postmarket_retry.sh` → `publish_post_market.py` (in the `channel-sender` service) | Bounded freshness retry for **just** the 4 post-market posts | Yes, but only market index + daily price (never the heavy steps) | Yes |
| `donchian-firstlight1-prices.timer` | 05:00 | `firstlight1_updateonly.sh` | Price safety-net + bot snapshot | Yes | No (`--snapshot-only`) |
| `donchian-earnings-today.timer` | 10:00 | `run_channel_sender.sh mechanism/alerts/send_earnings_today.py` | Pre-market "who reports today" | No | Yes (one post, own kind) |
| `donchian-notice-midday.timer` | 12:00 | `run_channel_sender.sh mechanism/alerts/send_channel_notices.py --slot 1` | Disclaimer + assistant-promo reminder, slot 1 | No | Yes (2 posts) |
| `donchian-notice-evening.timer` | 20:00 | Same, `--slot 2` | Same, slot 2 | No | Yes |
| `donchian-nightly-backup.timer` | 23:30 **UTC** (not Israel-tagged) | `nightly_backup.sh trading_production` | Production DB dump | No (read-only) | No |
| `donchian-bot.service` (not a timer) | continuous | `docker compose --profile bot up -d bot` | Private assistant, long-polling | No | DMs only, never the channel |
| `donchian-docker-firewall.service` (not a timer) | boot / `docker.service` restart | `donchian-docker-firewall.sh` | Re-applies the DOCKER-USER iptables filter | No | No |

## 2. Per-timer detail

### `donchian-pipeline.timer` / `.service`
```
[Timer]
OnCalendar=*-*-* 23:45:00 Asia/Jerusalem
OnCalendar=*-*-* 01:00:00 Asia/Jerusalem
Persistent=true
```
Two fires a day: 23:45 (the primary attempt, ~45 min after the 16:00 ET close given
`MARKET_SETTLE_MINUTES=30`) and 01:00 (a same-session retry if the vendor bar wasn't published yet
at 23:45). **The 01:00 fire is a no-op once the day is already marked done** by
`mechanism/shared/market_calendar.py`'s session-mark step — `Persistent=true` means a missed fire
(e.g. VPS was down) catches up on next boot rather than being silently skipped. `TimeoutStartSec=4h`.
Runs steps 1–13 of `automation_pipeline.sh`; see [SYSTEM_OVERVIEW.md](SYSTEM_OVERVIEW.md) §3 for how
this differs from the lightweight retry below.

### `donchian-postmarket-retry.timer` / `.service`
```
[Timer]
OnCalendar=*-*-* 23:45:00 Asia/Jerusalem
OnCalendar=*-*-* 0,1,2,3,4,5:0/20:00 Asia/Jerusalem
OnCalendar=*-*-* 06:00:00 Asia/Jerusalem
Persistent=false
```
Fires at 23:45, then on a `:00/20` minute pattern from 00:00 through 05:00, then once more at 06:00.
**`Persistent=false` is deliberate** — unlike every other timer here, this one must *not* catch up on
a stale fire after a VPS outage, since that could mean sending a retry for a session that's no longer
"today's" target. Idempotency comes entirely from `telegram_post_delivery` (see
[TELEGRAM_PUBLISHING.md](TELEGRAM_PUBLISHING.md)), so a fire that finds nothing missing is a fast,
safe no-op — this is what makes the ~20-minute cadence acceptable. `TimeoutStartSec=30m`.

### `donchian-firstlight1-prices.timer` / `.service`
`OnCalendar=*-*-* 05:00:00 Asia/Jerusalem`, `Persistent=true`. A price-only safety net independent of
the pipeline above — runs `firstlight1_updateonly.sh`, never sends to Telegram
(`send_daily_digest.py --snapshot-only`). `TimeoutStartSec=1h`.

### `donchian-earnings-today.timer` / `.service`
`OnCalendar=*-*-* 10:00:00 Asia/Jerusalem`, `Persistent=true`. Fully independent of the post-market
package — its own gate is `market_calendar.is_trading_day()` (a different check than
`check_price_freshness`), keyed by the **NY** calendar date to match the sender's own convention.
`TimeoutStartSec=15m`.

### `donchian-notice-midday.timer` / `donchian-notice-evening.timer`
`OnCalendar` at 12:00 / 20:00 Asia/Jerusalem, `Persistent=true`. Two rotating posts a day
(disclaimer pointer + assistant promo), gated per-slot-per-day, unrelated to market data.
`TimeoutStartSec=15m` each.

### `donchian-nightly-backup.timer` / `.service`
```
[Timer]
# 02:30 Israel time -- after the (not-yet-enabled) 23:45/01:00 VPS pipeline window
OnCalendar=*-*-* 23:30:00 UTC
Persistent=true
RandomizedDelaySec=300
```
Runs `nightly_backup.sh trading_production` — a read-only `pg_dump` inside the container, no app
health dependency. **This is the one timer that predates the Asia/Jerusalem-tag convention** used by
every timer above; its comment computes the Israel-time equivalent by hand instead of letting systemd
do it, so it will silently drift by an hour whenever Israel's DST offset changes. See
[../operations/BACKUPS.md](../operations/BACKUPS.md) for the full backup/restore story.

### `donchian-bot.service` (continuous, not a timer)
`Type=oneshot RemainAfterExit=yes`, `WantedBy=multi-user.target`. Starts (and, via `ExecStop`, stops)
the `bot` Compose service — `run_bot.py`, long-polling. Docker's own `restart: unless-stopped`
handles crash recovery once running; this unit is only the start/stop wiring, not a supervisor loop.

### `donchian-docker-firewall.service` (continuous, not a timer)
Re-applies the DOCKER-USER iptables filter (internet → containers restricted to tcp/80, tcp/443)
at boot and after every Docker restart (`PartOf=docker.service`). Infrastructure, not application
scheduling — included here for completeness since it's a systemd unit on the same host.

## 3. Failure semantics, not current failure state

**A systemd service showing `failed` right now describes the last run's outcome, not this
document's architecture.** `check_price_freshness` exiting non-zero when a vendor bar genuinely
hasn't landed yet is *intended* behavior — that's what tells `donchian-pipeline.service` to report
failed and lets the 01:00 fire (or the postmarket-retry timer) pick it back up. Don't read a `failed`
status as "the schedule is broken"; check the actual log line first
(`journalctl -u donchian-pipeline.service`).

## 4. Windows Task Scheduler — legacy, not active

Before the VPS migration, six Windows Task Scheduler jobs (`DonchianScreenerDailyPipeline`,
`FirstLight-1-UpdatePrices` through `FirstLight-5-EarningsToday`) ran the equivalent of the timers
above. **All six are confirmed `Disabled`** and kept only as a manual Gate-5-class rollback path —
never re-enable one while the VPS is active (see [../../CLAUDE.md](../../CLAUDE.md) §12 and
[../operations/ROLLBACK.md](../operations/ROLLBACK.md)). The root `.ps1` wrapper scripts
(`run_first_light_morning.ps1` etc.) that these jobs used to call still exist and still work — they
are genuinely useful for local/dev-channel testing, just no longer part of the production path. See
[../dev/LOCAL_SETUP.md](../dev/LOCAL_SETUP.md).
