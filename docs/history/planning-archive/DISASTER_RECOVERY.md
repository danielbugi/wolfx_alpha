> **HISTORICAL DOCUMENT — NOT CURRENT PRODUCTION ARCHITECTURE.** A pre-execution planning document
> from before the VPS migration/cutover was actually carried out. Preserved for reference/history only.
> For current architecture, see [docs/architecture/](../../architecture/), [docs/operations/](../../operations/),
> [CLAUDE.md](../../../CLAUDE.md), and [../../devops/CUTOVER_PLAN.md](../../devops/CUTOVER_PLAN.md) (the
> one actively-maintained infrastructure doc, kept in place — not archived — while Gate 5 remains
> outstanding). Moved here 2026-09-25; content below is unmodified except for this banner.

# Disaster Recovery

> Companion to [INFRASTRUCTURE_PLAN.md](INFRASTRUCTURE_PLAN.md). This document exists because
> ARCHITECTURE_AUDIT.md's #1 finding is stark: **there is currently no backup of anything, anywhere,
> for this system.** A single disk failure on the current local machine would be a total, unrecoverable
> loss of the entire `trading_production` database — years of price history, the ML dataset, every
> Telegram user's access grant and portfolio, dashboard accounts. This is the single highest-priority
> item in the whole migration, independent of hosting choice.

## 1. What's actually irreplaceable vs. re-derivable

| Data | Irreplaceable? | Why |
|---|---|---|
| `stock_prices`, `technical_indicators`, weekly/monthly variants | **Partially re-derivable, at real cost** — history could theoretically be re-fetched from Tiingo/Alpaca/yfinance, but (a) that costs real API quota/money, (b) vendor data is restated over time (CLAUDE.md documents this exact problem already corrupting derived tables once), so a re-fetch today would not exactly reproduce what was stored historically, (c) the free/cheap tiers this project uses may not offer full historical depth on re-fetch |
| `ml_breakout_dataset_v2` | Re-derivable from `stock_prices` (it's built by a deterministic script) — **but only if `stock_prices` survives** |
| Trained model files (`ml_training/models/*.joblib`) | Re-derivable by re-running training — **currently stored only on local disk, not in Postgres or object storage; this is its own small single point of failure, worth including in the backup scope** |
| `dashboard_users`, `dashboard_sessions` | **Not re-derivable** — real account data; loss means both the Owner and Collaborator are locked out (recoverable via `create_user.py --reset-password`, but only if the box itself survives to run that command) |
| `bot_users`, `bot_access`, `bot_invites`, `bot_tracked` (portfolios/watchlists) | **Not re-derivable** — this is real user data (Telegram user IDs, tracked positions, access grants) with no source of truth anywhere else |
| `telegram_messages` (control-center ledger) | **Not re-derivable** — Telegram gives bots no channel history API (documented in CLAUDE.md); once this ledger is gone, the Control Center loses all pre-loss message history permanently, even though the actual channel posts remain visible on Telegram itself |
| `digest_runs`, `digest_stocks` (daily snapshots) | Not re-derivable after the fact for past dates (each day's snapshot reflects that day's live prices at send time) |
| Application code | Fully recoverable — it's in git | 
| `.env` / secrets | Not in git (correctly) — **must have a separate, deliberate backup of its own** (see §4), since losing it means every external integration (Telegram, Tiingo, Alpaca, SMTP) needs to be manually re-configured even if the box itself is fine |

**Conclusion: the Postgres database is the one asset that needs a real, tested, off-box backup
strategy. Everything else is either in git (code) or acceptable to lose with a manual recovery
process (local model files, easily retrained).**

## 2. Recommended backup strategy

1. **Nightly `pg_dump`** of the full `trading_production` database, timed to run *after* the nightly
   pipeline completes (so the backup captures a consistent, fully-updated state) — e.g., 04:00 Israel
   time, after the 02:00 pipeline's typical completion.
2. **Local retention on the VPS:** 7 daily dumps + 4 weekly dumps, rotated (simple `find -mtime +N
   -delete` cron logic — no backup software needed at this scale).
3. **Off-box copy, mandatory:** sync each night's dump to object storage (S3, Backblaze B2, or the
   hosting provider's equivalent — pick whichever is cheapest; B2 is typically the lowest-cost option
   for this volume). This is the step that actually protects against the VPS itself being lost
   (disk failure, provider incident, accidental `docker volume rm`), not just against a bad
   application-level mistake. **Cost is trivial** — a database of this size (a few GB, growing slowly)
   costs cents per month in object storage.
4. **Encrypt backups at rest** (most object storage providers offer this as a checkbox — SSE-S3/B2's
   native encryption is sufficient; no need for client-side encryption complexity at this scale) since
   the dump contains real user data (Telegram IDs, portfolio holdings, hashed but still
   sensitive credentials).
5. **`ml_training/models/*.joblib` + `_meta.json`:** include in the same nightly off-box sync (a
   simple `rclone`/`aws s3 sync` of the directory) — cheap insurance against the local-disk single
   point of failure noted in §1, even though these are technically re-derivable by re-running
   training.
6. **Test the restore, not just the backup.** A backup that has never been restored is unverified.
   Recommend a quarterly (or at minimum, one-time post-migration) exercise: spin up a throwaway
   Postgres container, restore the latest dump into it, and confirm `mechanism/diagnostic_tools/
   test_db_connection.py` and a few real queries succeed against it.

## 3. Recovery Time / Recovery Point Objectives (recommended targets, not currently met by anything)

| Scenario | Target RPO (max acceptable data loss) | Target RTO (max acceptable time to restore) | How achieved |
|---|---|---|---|
| VPS disk failure / total loss | ≤ 24h (one nightly dump) | ≤ 2-4h | Provision a new VPS, restore latest off-box dump, redeploy containers from the ghcr.io images (already versioned — no rebuild needed) |
| Accidental bad migration / data corruption | ≤ 24h | ≤ 1h | Restore the pre-corruption dump into a fresh Postgres container/volume; do **not** attempt to hand-patch corrupted data live |
| Bad application deploy (not data-related) | 0 (no data loss) | ≤ 5 min | Automated rollback via CI/CD health-check gate (INFRASTRUCTURE_PLAN.md §6.3) — this is the fast path and should catch the large majority of real incidents before they ever touch data |
| Secrets/`.env` loss | 0 (kept in a password manager or encrypted vault separate from the VPS — a one-time manual step, not automated) | ≤ 30 min | Re-populate `.env` on a new VPS from the secure secondary copy |
| Telegram bot token compromise | N/A (security incident, not data loss) | ≤ 15 min | Revoke + reissue the bot token via BotFather, update `.env`, redeploy the bot container only |

## 4. Secrets backup (separate from the database backup)

The `.env` file itself should have **one** secure secondary copy outside the VPS — a password
manager entry or an encrypted note, not a second plaintext file anywhere. This is deliberately manual
and low-tech: at two users, a dedicated secrets-vault tool (per INFRASTRUCTURE_PLAN.md §6.6) isn't
justified, but *zero* secondary copies is what caused this exact category of near-miss already
(the stale, un-backed-up `backend/.env.stale-superseded-2026-09-22` file existing by accident, not by
design). One deliberate, secure copy closes this gap without over-engineering it.

## 5. What happens today, right now, if the current local machine's disk fails

Total, permanent loss of every table in CLAUDE.md §5 — no recovery path exists. This should be treated
as the most urgent single item in this entire plan; even before any hosting migration begins, a
one-off `pg_dump` + upload to any cloud storage bucket today would immediately close most of the risk
described in this document, independent of and well before the full migration in
[MIGRATION_PLAN.md](MIGRATION_PLAN.md) is carried out.
