# Infrastructure Plan — Deployment Safety & Observability

> Companion to [CICD_STRATEGY.md](CICD_STRATEGY.md) and [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md).
> Covers Phase 6 (deployment safety) and Phase 7 (observability). Backups/rollback detail that's
> specifically about disaster scenarios lives in [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md); this
> document covers the day-to-day mechanics.

## Phase 6 — Deployment safety

### 6.1 Health checks and restart policies

Every long-running container gets both a Compose `healthcheck:` (used by the CI/CD deploy gate — see
CICD_STRATEGY.md §6) and a `restart: unless-stopped` policy, so a crashed process (the exact failure
mode already observed with `uvicorn --reload`'s documented shutdown hang) comes back on its own
without a human SSH-ing in to restart it by hand — a direct fix for ARCHITECTURE_AUDIT.md finding #2.
`unless-stopped` (not `always`) is deliberate: it lets a developer intentionally `docker compose stop`
a service for maintenance without Docker fighting them by restarting it immediately.

### 6.2 Versioned images, never "deploy from whatever `git` state is checked out"

Every deploy references an immutable image tag (the git SHA — see CICD_STRATEGY.md §4), never
`latest` in the actual `docker compose up -d` invocation used for real deploys. This is what makes
rollback well-defined: "roll back" means "re-point the Compose file at the previous SHA tag and
`up -d` again," not "hope `git revert` + rebuild reproduces the exact previous state."

### 6.3 Rollback strategy

1. The deploy workflow records the currently-running image tag for each service *before* deploying
   the new one (`docker inspect` or a simple text file on the VPS, e.g. `/opt/donchian/.last-good-<service>`).
2. If the post-deploy health check (CICD_STRATEGY.md §6) fails, the workflow automatically re-runs
   `docker compose up -d <service>` pinned to the previous tag — no rebuild needed, this is fast
   (seconds, just a container swap).
3. If a bad deploy passes the health check but is discovered broken later (a correctness bug, not a
   crash), the same rollback is available as a manual one-line command run from the developer's own
   machine: `ssh deploy@vps "cd /opt/donchian && docker compose up -d --no-deps <service>"` with the
   Compose file's tag reference edited to the last-known-good SHA.
4. **A failed deployment must never touch the database schema irreversibly** — this is why database
   migrations (6.4) are a separate, explicitly-gated step from the application container swap, not
   bundled into the same `up -d`.

### 6.4 Database migration strategy

This project's schema changes today are hand-run `.sql` files (`mechanism/add_*.sql`,
`mechanism/create_trading_schema.sql`) applied manually against the live database — no migration
tool (Alembic, Flyway, golang-migrate) tracks *which* have been applied where. Recommended, in order
of priority:

1. **Immediately (independent of any hosting move):** adopt a lightweight migration tracker — a
   single `schema_migrations` table recording which of the existing `add_*.sql` files have been
   applied, checked at pipeline/backend startup. This alone would have caught the exact class of
   incident CLAUDE.md documents repeatedly (a table or column assumed to exist that wasn't actually
   migrated on a given environment).
2. **On the target infrastructure:** migrations become an explicit CI/CD step, gated separately from
   the application deploy: `docker compose run --rm backend python -m migrations.apply` runs and must
   succeed *before* the new application image is swapped in, never automatically as a side effect of
   `docker compose up -d`. A failed migration must stop the deploy, not roll back the schema
   automatically (schema rollbacks are rarely safe in general and never safe to automate blindly —
   see DISASTER_RECOVERY.md for the manual recovery path).
3. Additive-only migrations preferred (new tables/nullable columns) over destructive ones (dropped
   columns, renamed tables) wherever possible, so a rollback of the *application* image never needs a
   matching rollback of the *schema* — this project's own tables already lean this way in practice
   (CLAUDE.md's changelog shows table additions, not renames/drops, as the norm).

### 6.5 Backup strategy

See DISASTER_RECOVERY.md for full detail. Summary: nightly `pg_dump` of `trading_production` to
the VPS's disk, rotated (7 daily + 4 weekly), plus off-box replication to object storage (e.g., S3 or
a Backblaze B2 bucket — cheap, and the one genuinely non-negotiable piece of this whole plan, since
today **there is no backup at all** — ARCHITECTURE_AUDIT.md finding #1).

### 6.6 Secrets management

- Move off the current flat, unversioned `.env` file convention toward: **GitHub Actions secrets**
  for anything CI needs (the SSH deploy key, registry credentials) and **a single `.env` file that
  lives only on the VPS** (never in git, never in the CI runner's filesystem beyond the moment it's
  injected) for runtime secrets (`DB_PASSWORD`, `JWT_SECRET`, `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CONTROL_TOKEN`, `TIINGO_API_KEY`, `ALPACA_API_SECRET`, `SMTP_PASSWORD`).
- This is a **process** fix more than a new tool: the actual bugs logged in CLAUDE.md
  (`TELEGRAM_CONTROL_TOKEN` and `SMTP_HOST` both silently shadowed by `load_dotenv()`'s upward search
  finding a stale file) were caused by *ambiguous file location*, not by the absence of a dedicated
  secrets manager. Containerizing each service with an explicit, single `env_file:` reference in
  Compose eliminates the ambiguity by construction — there is no "upward search" inside a container
  with one file mounted at one path.
- A dedicated secrets manager (Doppler, Vault, AWS Secrets Manager) is **not recommended at this
  scale** — one VPS, one Owner, one Collaborator, a handful of secrets — it would add a vendor
  dependency and rotation-workflow complexity disproportionate to the actual risk. Revisit only if
  the team grows beyond two people or a compliance requirement demands it.
- Rotate `TELEGRAM_CONTROL_TOKEN` and `JWT_SECRET` once as part of the migration itself, since the
  current values have lived in a plaintext file with the confirmed history of at least one stale
  duplicate existing on disk unnoticed.

### 6.7 Production environment variable management

- One `.env` per environment (`production` only, to start — no separate staging environment is
  justified yet at 2 users; revisit if that changes), owned by Compose's `env_file:` directive per
  service so each container only sees the variables it actually needs (the bot doesn't need
  `JWT_SECRET`; the backend doesn't need `TELEGRAM_BOT_TOKEN` unless it's actually the one making
  Telegram Control Center calls — worth auditing exactly which service needs which var during
  containerization rather than giving every container the whole file).
- Fix the two known drift bugs as part of this move, not after: `ALLOWED_ORIGINS` must actually be
  read by `backend/main.py`'s CORS middleware (currently unused — CURRENT_ARCHITECTURE.md §7) so the
  hosted frontend's real domain can be set once, in one place, instead of hardcoded ports.

## Phase 7 — Observability

The user's priorities (single developer, avoid a heavy stack) point toward the smallest toolset that
actually closes today's real gaps — which, per ARCHITECTURE_AUDIT.md, are "nobody finds out when a
scheduled job silently fails or a process crashes," not "insufficient metrics granularity."

| Concern | Recommendation | Why this and not more |
|---|---|---|
| **Application logs** | Each container logs to stdout/stderr (already true — this project already uses Python's `logging` + a rotating file handler per CLAUDE.md's `ProgressBar`/`logger` description); Docker's default `json-file` log driver with size/rotation limits (`max-size: 10m, max-file: 3` in Compose) is sufficient — no log-shipping service needed at this volume | A dedicated log aggregator (Loki, ELK) is overkill for one VPS's worth of logs a single developer reads directly; `docker compose logs -f <service>` covers the actual day-to-day need |
| **Docker container logs/status** | `docker compose ps` + `docker stats` for ad hoc checks; `docker compose logs --since 1h` for recent history | Same reasoning — built into Docker already |
| **Server CPU/RAM/disk** | A lightweight agent-based free tier (e.g., Netdata's free single-node mode, or the VPS provider's own built-in monitoring dashboard if one exists — many do at no extra cost) | Free/near-free, no separate infrastructure to run, immediately actionable (a full disk is the single most likely silent-failure cause for a database-backed system like this one) |
| **Uptime monitoring** | An external free-tier uptime checker (UptimeRobot, Better Uptime's free tier, or similar) pinging `GET /api/health` every 1-5 min, alerting via email/Telegram (this project already has a Telegram bot — the owner's own private chat is a natural, zero-new-infrastructure alert channel) | External is important: a monitor running *on* the VPS can't tell you the VPS itself is unreachable |
| **Failed deployments** | Surfaced natively by GitHub Actions (a failed workflow run + the auto-rollback in §6.3) — add a Slack/Telegram/email notification on workflow failure so it doesn't require someone checking the Actions tab | No separate deployment-tracking tool needed; GitHub Actions already has this built in |
| **Service crashes** | Docker's `restart: unless-stopped` (§6.1) handles recovery automatically; pair with the uptime monitor above so a crash-loop (repeatedly restarting and failing) still surfaces as a real alert rather than silently cycling forever | A crash that self-heals within the health-check window is invisible without this pairing — worth explicitly wiring, since it's exactly the gap that let today's `--reload` hang go unnoticed until someone manually checked |
| **Database health** | `pg_isready` in the Compose healthcheck (§6.1) for liveness; a scheduled query (part of the existing `mechanism/diagnostic_tools/test_db_connection.py`, already in the repo) run daily via cron, alerting on failure the same way as the backup job (DISASTER_RECOVERY.md) | Reuses code that already exists rather than adopting a DB-monitoring SaaS product |
| **Alerting channel** | Telegram, via the bot's own owner DM (`BOT_OWNER_ID`) — this project already has a working, tested Telegram send path (`telegram_client.py`) that a simple monitoring cron job or GitHub Actions step can call directly; no new alerting vendor needed | Zero marginal cost, reuses infrastructure this project already trusts and has tested |

**Explicitly not recommended at this scale:** Prometheus + Grafana (a real stack to run and
maintain, disproportionate to one VPS and two users), Sentry/error-tracking SaaS (worth revisiting
only if unhandled exceptions become frequent enough that `docker compose logs` stops being a fast
enough way to find them), a dedicated APM tool (the project already built its own lightweight
`/api/performance/` latency tracker — CLAUDE.md documents real, specific perf fixes it already drove;
that in-app tool covers request-latency observability today without adding an external dependency).
