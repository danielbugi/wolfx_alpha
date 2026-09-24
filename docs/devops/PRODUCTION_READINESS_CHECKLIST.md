# Production Readiness Checklist

> To be worked through **before** [MIGRATION_PLAN.md](MIGRATION_PLAN.md) Phase 5 (infrastructure
> migration) begins. Nothing on this checklist has been done yet — it is the gate, not a status
> report. Organized so each item states *what "done" looks like*, not just a topic name.

## PostgreSQL backup
- [ ] Nightly `pg_dump` of `trading_production` scheduled via cron on the VPS, timed after the
      02:00 pipeline's typical completion (DISASTER_RECOVERY.md §2).
- [ ] Local retention rotation in place (7 daily + 4 weekly) so the dump directory itself doesn't
      become an unbounded disk-growth risk (the same class of gap found live in `logs/` and
      `breakout_results/` — RESOURCE_REQUIREMENTS.md §0).
- [ ] Dump includes **all** databases/schemas actually needed to fully reconstruct the system —
      confirm nothing (e.g., a separate schema, an extension configuration) is silently excluded.

## PostgreSQL restore test
- [ ] At least one full restore of the latest dump into a **throwaway** Postgres container performed
      and verified (row counts match source, a handful of real queries return sane results) —
      DISASTER_RECOVERY.md §2 explicitly flags an untested backup as unverified.
- [ ] Restore procedure is written down step-by-step (not just "run `pg_restore`") so it's executable
      under stress by whoever is on call, including a time-boxed dry run to sanity-check the RTO
      target in DISASTER_RECOVERY.md §3 (≤2-4h for a total-loss scenario).

## Secrets
- [ ] Every secret currently in the local `.env` (DB password, `JWT_SECRET`, `TELEGRAM_BOT_TOKEN`,
      `TELEGRAM_CONTROL_TOKEN`, `TIINGO_API_KEY`, `ALPACA_API_SECRET`, `SMTP_PASSWORD`) has a
      **rotated** value for production, not the value that's been sitting in a plaintext local file
      — INFRASTRUCTURE_PLAN.md §6.6 specifically calls for rotating `TELEGRAM_CONTROL_TOKEN` and
      `JWT_SECRET` as part of this move, given the confirmed history of a stale duplicate `.env`
      existing on disk unnoticed.
- [ ] `.env` on the VPS is **not** in git, is not world-readable (`chmod 600`), and is owned by the
      deploy user only.
- [ ] `ALLOWED_ORIGINS` is actually wired into `backend/main.py`'s CORS middleware (currently a
      dead/unused variable — CURRENT_ARCHITECTURE.md §7) and set to
      `https://dashboard.first-light.finance` (approved hostname, PRODUCTION_MIGRATION_RUNBOOK.md's
      "Domain" section — not yet live), not left as the hardcoded `localhost` list.
- [ ] One secure secondary copy of the production `.env` exists outside the VPS (password
      manager/encrypted note — DISASTER_RECOVERY.md §4), so a VPS loss doesn't also mean
      re-requesting every third-party credential from scratch.
- [ ] No secret value appears in a GitHub Actions log, a Docker image layer, or a committed file —
      spot-check by grepping recent Action run logs and `docker history` on each built image.

## Firewall
- [ ] Inbound rules limited to exactly: 22 (SSH, ideally from a restricted IP set or with
      fail2ban/rate-limiting), 80 and 443 (reverse proxy). Nothing else open by default.
- [ ] **PostgreSQL's 5432 is not exposed to the public internet** — confirmed by an external port
      scan against the VPS's public IP after setup, not just by reading the Compose file (Docker's
      own port publishing can surprise operators if a `ports:` mapping is used instead of an internal
      network).
- [ ] The Telegram bot needs no inbound port at all (long-polling is outbound-only) — confirmed no
      rule was accidentally left open for it.

## SSH
- [ ] Key-based auth only; password auth disabled in `sshd_config`.
- [ ] Root login disabled; deploy operations use a dedicated non-root user.
- [ ] The GitHub Actions deploy key is scoped to exactly what it needs (the deploy user, restricted if
      possible to only running the deploy command, e.g. via a forced command or a narrowly-scoped
      sudoers entry for `docker compose` only) — not a general-purpose root/admin key.

## Docker security
- [ ] Containers run as a non-root user inside the image where practical (Postgres's official image
      already does this; the custom backend/bot/pipeline images should too).
- [ ] No `--privileged` containers; no unnecessary host bind mounts beyond what's needed (the
      Postgres data volume, and read-only mounts where write access isn't required).
- [ ] Docker's own daemon socket is not exposed to any container that doesn't explicitly need it
      (none of this system's services need Docker-in-Docker access).
- [ ] Base images are from official/trusted sources (`python:3.11-slim`, `node:20-slim`,
      `postgres:16` or similar) with versions pinned, not `latest`, so a base-image update can't
      silently change production behavior.

## Persistent volumes
- [ ] Postgres data lives on a **named Docker volume** (not an anonymous volume, which is harder to
      locate/back up), documented in the Compose file with a clear name (e.g., `postgres_data`).
- [ ] `ml_training/models/` (the trained model artifacts — RESOURCE_REQUIREMENTS.md §1 flags this as
      a soft single point of failure on local disk today) is either included in the backup sync or
      explicitly accepted as "re-derivable by retraining, not backed up" — a deliberate decision, not
      an oversight.
- [ ] Confirm what happens to the named volume on `docker compose down` — never `down -v` in any
      deploy script or muscle-memory command; verified by reading the actual deploy script, not
      assumed.

## Automatic backups
- [ ] The nightly `pg_dump` cron job is itself monitored — a failed/skipped backup should alert
      (INFRASTRUCTURE_PLAN.md §Phase 7's "database health" row), not fail silently. A backup system
      nobody would notice failing is equivalent to not having one.
- [ ] Backup job failure uses the same Telegram-owner-DM alert channel already recommended for other
      monitoring, so there's exactly one place to check, not several.

## Off-server backups
- [ ] Nightly dumps sync to object storage (S3/B2/equivalent) **off the VPS**, confirmed by actually
      checking the bucket after a real run, not just reading the sync script (DISASTER_RECOVERY.md §2).
- [ ] Off-box backups are encrypted at rest (provider-native encryption is sufficient at this scale).
- [ ] Retention policy on the off-box copies is deliberate (e.g., 30 daily + 12 monthly) — not "keep
      everything forever" (cost creep) and not "keep only what's local" (defeats the purpose of an
      off-box copy).

## HTTPS
- [ ] `api.first-light.finance` points at the VPS and `dashboard.first-light.finance` at Vercel
      (approved hostnames, PRODUCTION_MIGRATION_RUNBOOK.md's "Domain" section — **neither is live
      yet**, no DNS/Vercel changes have been made) — no service is reachable over plain HTTP in
      production.
- [ ] TLS certificates are automated (Let's Encrypt via Traefik/Caddy's built-in ACME support, or
      Certbot) — not a manually-renewed certificate that will quietly expire.
- [ ] Mixed-content is verified impossible: the Vercel-hosted (HTTPS) `dashboard.first-light.finance`
      frontend's `NEXT_PUBLIC_API_BASE_URL` points at `https://api.first-light.finance`, not
      `http://` — confirmed by loading the deployed frontend in a real browser and checking for
      console mixed-content errors, not just by reading the env var value (TARGET_ARCHITECTURE.md
      §1, Option C's stated prerequisite).

## DNS
- [ ] A/AAAA (or CNAME, per provider) record for `api.first-light.finance` points at the VPS's IP,
      with a TTL short enough to allow a fast cutover/rollback if the VPS ever needs to change (e.g.,
      300s during the migration window, raised afterward). `dashboard.first-light.finance` is
      configured on Vercel's side instead (its own CNAME/verification instructions), not as a
      VPS-pointed record. **Not yet done.**
- [ ] The domain used for 2FA emails (`SMTP_FROM`) has correct SPF/DKIM records if a real mailbox is
      used in production, so 2FA codes don't land in spam — a live, not just theoretical, login-flow
      risk.

## Monitoring
- [ ] An external uptime monitor (outside the VPS) polls `GET /api/health` on a short interval and
      alerts via the Telegram owner-DM channel on failure (INFRASTRUCTURE_PLAN.md §Phase 7).
- [ ] Server-level CPU/RAM/disk monitoring is active (Netdata free tier or the provider's built-in
      dashboard) and the operator has actually looked at it once under normal load to know what
      "normal" looks like, before needing it during an incident.
- [ ] The Telegram bot's liveness is monitored by *some* signal (a heartbeat file check, or the lock
      port responding) — not assumed healthy just because the container shows "running" in Docker,
      since a hung-but-not-crashed process wouldn't be caught by `restart: unless-stopped` alone.

## Disk alerts
- [ ] An alert fires **before** the disk fills, not after (e.g., at 80% used) — sized against the
      real growth-rate numbers in RESOURCE_REQUIREMENTS.md §0/§3, not a generic default.
- [ ] The two confirmed unbounded-growth risks (`logs/`, `breakout_results/`) have an actual
      retention/rotation policy implemented (e.g., a cron job pruning files older than N days) before
      go-live — not just monitored and manually cleaned up reactively.
- [ ] `ml_training/models/` has a retention policy too (e.g., keep the last N versions) — currently
      unbounded on local disk (RESOURCE_REQUIREMENTS.md §0).

## Service health checks
- [ ] Every long-running container (backend, bot, Postgres) has a Compose `healthcheck:` that
      reflects real liveness, not just "the process didn't exit" (INFRASTRUCTURE_PLAN.md §6.1) —
      verified by deliberately breaking one health check in a staging environment and confirming
      Docker/the deploy pipeline actually notices.
- [ ] `restart: unless-stopped` is set on all three long-running containers, confirmed by actually
      killing each process once in staging and observing the automatic recovery.

## Deployment rollback
- [ ] The CI/CD pipeline's automatic rollback-on-failed-health-check (CICD_STRATEGY.md §6,
      INFRASTRUCTURE_PLAN.md §6.3) has been **exercised at least once in staging** — deliberately ship
      a broken health check and confirm the previous image tag is restored automatically, not just
      designed on paper.
- [ ] The manual rollback command (re-pin the Compose file to a previous SHA tag and `up -d`) is
      documented somewhere the operator can find it at 2am, not only in this document's memory.
- [ ] Database migrations are confirmed to be additive-only for the initial launch (no destructive
      schema change ships without an explicit, separate, reviewed decision — INFRASTRUCTURE_PLAN.md
      §6.4), so an application rollback never needs a matching schema rollback.

## One item not on the original list, added because §0's measurement surfaced it
- [ ] **Decide and implement a retention policy for `logs/pipeline_*.log` and
      `breakout_results/*.json` before the first production pipeline run**, not after disk fills up —
      this was found as a live, already-existing gap (444 MB and 177 MB respectively, unbounded)
      during this analysis, not a hypothetical risk.
