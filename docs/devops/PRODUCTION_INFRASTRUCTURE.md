# Production Infrastructure

> Phase 4A — design only. Nothing in this document has been provisioned, deployed, or executed.
> Builds directly on [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md),
> [RESOURCE_REQUIREMENTS.md](RESOURCE_REQUIREMENTS.md), and the real containers validated in
> Phase 2 / the CI validated in Phase 3.

## 1. Production topology — confirmed, not changed

```
                    Vercel (frontend, free tier, auto-deploy from `main`)
                                 │  HTTPS
                                 ▼
                 ┌─────────────────────────────────────┐
                 │         One Linux VPS (Docker Compose)  │
                 │                                          │
   HTTPS ────────┼─► Caddy (reverse proxy, auto TLS)          │
   (api domain)  │        │                                  │
                 │        ▼                                  │
                 │   Backend API (uvicorn, no --reload)       │
                 │        │                                  │
                 │        ▼                                  │
                 │   PostgreSQL (Docker, named volume)  ◄─────┼─── Bot (aiogram, long-poll)
                 │        ▲                                  │
                 │        │                                  │
                 │   Pipeline (scheduled, one-shot)           │
                 │   Channel-sender (scheduled, one-shot)     │
                 └─────────────────────────────────────┘
```

**This is the same target architecture from Phase 1 (`TARGET_ARCHITECTURE.md`, Option C), unchanged.**
Explicitly confirming, per your instruction to justify any change:

- **Nothing discovered in Phase 2 changes the topology.** The two SQL bugs
  (`create_trading_schema.sql`'s invalid `GRANT ... VIEWS` and `PRINT` statements) and the backend
  Dockerfile's missing `mechanism`/`ml_training` dependency were implementation bugs, not
  architectural findings — fixing them didn't move any service to a different host or change which
  services exist.
- **Nothing discovered in Phase 3 changes the topology either.** CI confirmed the existing test/lint
  commands, found two more pre-existing dead files, and validated the same three Docker images this
  document assumes. No new service was needed to make CI work.
- **One resource-sizing refinement carries forward from Phase 1** (already recorded in
  `RESOURCE_REQUIREMENTS.md` §3): the recommended VPS spec is **4 vCPU / 8 GB RAM**, not the rougher
  2 vCPU/4 GB figure floated earlier in `TARGET_ARCHITECTURE.md` — driven by the ML-training
  resource spike, not by anything found in Phase 2/3. Restated here as the authoritative number.
- **One new, concrete finding from this phase's schema inspection** (§7 of the summary in
  `PRODUCTION_MIGRATION_RUNBOOK.md`, detailed there): production's real schema has 6 tables the
  tracked SQL migration files never created (schema drift). This doesn't change the topology, but it
  is the single most important fact governing how the database migration must be done — see
  `PRODUCTION_MIGRATION_RUNBOOK.md`.

## 2. VPS specification

| Resource | Value | Source |
|---|---|---|
| vCPU | **4** | `RESOURCE_REQUIREMENTS.md` §3, unrevised — Phase 2's measured idle usage (backend 119 MiB, frontend N/A on VPS, postgres 23 MiB on an near-empty test DB) came in *under* estimate, which supports this spec rather than argues for more |
| RAM | **8 GB** | Same — the untested variable (a full-universe pipeline run + real XGBoost training) is still the number that actually sizes the box; 8 GB retains the planned headroom |
| SSD | **80–100 GB** | `RESOURCE_REQUIREMENTS.md` §3 math: ~4.5 GB real DB today (confirmed again this phase via a live, read-only query) + growth + Docker images + OS + backup staging |
| Architecture | x86_64 | Matches what was built/tested in Phase 2/3 (no ARM-specific concerns raised, and `postgres:16-alpine`/`caddy:2-alpine` images used here are multi-arch, but sticking with x86_64 avoids any unnecessary variable during a first migration) |

## 3. Linux distribution

**Ubuntu 22.04 LTS or 24.04 LTS.** Reasoning:
- Longest support window of the mainstream, free options (5 years standard, matches this project's
  own multi-year time horizon already evident in its own changelog).
- Docker's official installation instructions and `apt` repository are first-class for Ubuntu;
  every image/tool already used in this project (`postgres:16-alpine`, `caddy:2-alpine`,
  `python:3.11-slim`, `node:20-slim`) is validated against glibc/musl combinations that work
  identically regardless of host distro, since they run inside containers — the *host* OS choice
  only affects the Docker Engine layer, not the application layer.
- No feature in this stack needs a bleeding-edge kernel or unusual distro (no GPU passthrough, no
  exotic filesystem, no Kubernetes) — Ubuntu LTS is the boring, well-supported default, matching
  the "avoid unnecessary DevOps complexity" priority from Phase 1.

## 4. Docker requirements

- Docker Engine (not Docker Desktop — that's a Mac/Windows-only product; the Linux VPS runs the
  Engine + CLI + Compose plugin directly, installed via Docker's official `apt` repository).
- Docker Compose v2 (the `docker compose` plugin, not the legacy standalone `docker-compose`
  binary) — matches exactly what was built and validated in Phase 2/3 (`docker-compose.yml`,
  `docker-compose.prod.yml` both target Compose v2 syntax already).
- No Docker Swarm, no Kubernetes — a single-host `docker compose` deployment matches the
  already-approved architecture.

## 5. Firewall rules

| Port | Direction | Purpose |
|---|---|---|
| 22 | Inbound, restricted | SSH — see §6 |
| 80 | Inbound | HTTP, only for Caddy's ACME HTTP-01 challenge and redirect-to-HTTPS |
| 443 | Inbound | HTTPS — the only real traffic port |
| 5432 | **Not exposed** | PostgreSQL stays on the Docker-internal network only, exactly as validated in Phase 2 — never bound to a host port |
| Everything else | Denied by default | `ufw default deny incoming` (or the cloud provider's security-group equivalent) |
| Outbound | Allowed | The pipeline/bot need outbound HTTPS to Tiingo, Alpaca, Yahoo Finance, Telegram's API, and SMTP — no outbound restriction needed at this scale |

## 6. SSH configuration

- Key-based authentication only; `PasswordAuthentication no` in `sshd_config`.
- `PermitRootLogin no` — a dedicated non-root deploy user (`deploy`) with `sudo` limited to exactly
  the Docker/systemd commands it needs, not blanket root.
- The CD workflow's SSH key (see `CD_DESIGN.md`) is a **separate** key from any personal
  administrative key, scoped only to what the deploy user can do.
- Optional, recommended: `fail2ban` for SSH brute-force protection, or restrict inbound 22 to a
  known IP range if the operator has a stable IP — either is a cheap, standard hardening step, not
  unique to this project.

## 7. Filesystem / directory layout

```
/opt/donchian/
├── compose/
│   ├── docker-compose.yml            # the exact file already built/validated in Phase 2 — deployed, not rewritten
│   └── docker-compose.prod.yml       # the prod overlay, filled in with real ghcr.io tags at deploy time
├── env/
│   └── .env                          # production secrets — see SECRETS_STRATEGY.md. chmod 600, owned by `deploy`, NEVER in git
├── data/
│   └── postgres/                     # NOT used directly — Postgres uses a named Docker volume, not a bind mount,
│                                       # to match exactly what Phase 2 validated (docker-compose.yml's `postgres_data:`
│                                       # named volume). This directory is reserved only if a future decision moves to a
│                                       # bind mount for easier host-level backup access — not the default.
├── logs/
│   ├── pipeline/                     # bind-mounted into the pipeline container (matches docker-compose.yml's ./logs mount)
│   └── channel-sender/
├── backups/
│   ├── daily/                        # rotating local pg_dump files — see DISASTER_RECOVERY.md / this doc §backup below
│   └── staging/                      # a dump temporarily staged here before the off-box sync completes
├── breakout_results/                 # bind-mounted, matches the existing app's file-based output (CURRENT_ARCHITECTURE.md §4)
├── frontend_data/                    # same
├── reports/                          # same (market cards, promo images)
├── ml_training/
│   └── models/                       # bind-mounted so trained model artifacts survive a container recreate
└── CURRENT_SHA / PREVIOUS_GOOD_SHA   # two small text files recording the deployed image tags — see CD_DESIGN.md's rollback design
```

This mirrors the bind-mount paths already defined in `docker-compose.yml` (`./logs`, `./data`,
`./breakout_results`, `./frontend_data`, `./reports`, `./ml_training/models`) — the production
layout is not a redesign, it's the same relative paths anchored at `/opt/donchian/` instead of the
repo root, so the Compose file needs no path changes to move from local validation to production.

## 8. Docker volume strategy

- **`postgres_data`** — a named Docker volume (not a host bind mount), exactly as validated in
  Phase 2. This is the one piece of state that must never be lost; its backup strategy is
  independent of the volume type (a `pg_dump` reads through the running Postgres process regardless
  of where the underlying files live).
- **Everything else** (`logs/`, `data/`, `breakout_results/`, `frontend_data/`, `reports/`,
  `ml_training/models/`) — host bind mounts, matching Phase 2's design exactly, so the host-level
  retention/pruning cron job already flagged as necessary (`RESOURCE_REQUIREMENTS.md` §0,
  `PRODUCTION_READINESS_CHECKLIST.md`) can act on them directly without going through Docker.

## 9. Backup and log directories

Covered in full in `PRODUCTION_MIGRATION_RUNBOOK.md` §8 (backup) and this document's §7 above
(directory layout). Summary: `/opt/donchian/backups/daily/` holds rotating local `pg_dump` files
(7 daily + 4 weekly, per `DISASTER_RECOVERY.md` §2, unrevised); `/opt/donchian/logs/` holds the
pipeline/channel-sender's own log output plus Docker's own `json-file` driver logs (rotated via
Compose's `logging:` options, not yet added to `docker-compose.prod.yml` — flagged as a small,
concrete follow-up action, not implemented in this design-only phase since it's a one-line addition
better made once real production log volume is observed).

## 10. What is explicitly NOT done in this phase

- No VPS was created.
- No DNS was touched.
- No secret was generated or rotated.
- No file was copied to any server (there is no server yet).
- The directory layout above is a specification for Phase 4B's provisioning step, not something
  that exists anywhere yet.
