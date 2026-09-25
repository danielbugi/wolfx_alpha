> **HISTORICAL DOCUMENT — NOT CURRENT PRODUCTION ARCHITECTURE.** Preserved for reference/history.
> For current architecture, see [docs/architecture/](../architecture/), [docs/operations/](../operations/),
> and [CLAUDE.md](../../CLAUDE.md). Moved here 2026-09-25 as part of the repository documentation
> consolidation — content below is unmodified from before the move except for this banner.

# PLATFORM_ARCHITECTURE.md — service map, auth/RBAC, hosting

> Added 2026-09-22. Companion to [CLAUDE.md](../../CLAUDE.md) (the day-to-day source of truth for what each
> subsystem does) — this file is the higher-altitude view: where the real service boundaries already are,
> what was just built to close the "wide open to the internet" gap, and what a future microservices /
> hosting move would look like. Update it whenever a service boundary or the auth model changes.

## 1. What "the system" actually is today (current-state map)

Before this session, **every piece of this platform ran as unauthenticated, implicitly-coupled code** —
not literally one process, but with no access boundary between the pieces. Four things are already
logically separate services; they just weren't packaged, deployed, or secured as such:

```
┌─────────────────────┐      ┌──────────────────────┐      ┌───────────────────────┐
│  Frontend             │      │  Backend API           │      │  Postgres               │
│  Next.js, :3001 dev    │◄────►│  FastAPI, :8000        │◄────►│  trading_production      │
│  (frontend/)           │ HTTP │  (backend/)            │ SQL  │  (one instance, shared    │
│  talks ONLY to the API │      │  reads/writes Postgres,│      │  by every service below)  │
└─────────────────────┘      │  reads frontend_data/  │      └───────────────────────┘
                                │  JSON produced by the  │                 ▲
                                │  mechanism pipeline    │                 │
                                └──────────────────────┘                 │
                                          ▲                                │
                                          │ (no coupling — separate         │
                                          │  process, own schedule)         │
┌──────────────────────────────┐         │                                │
│  Mechanism pipeline             │ writes to Postgres + frontend_data/ ───┘
│  (mechanism/) — NOT a server,   │
│  a cron/Task-Scheduler-driven   │
│  batch job: updaters, screener, │
│  ML training. Runs, finishes,   │
│  exits.                         │
└──────────────────────────────┘

┌──────────────────────────────┐
│  Telegram bot                   │ already its own long-running process
│  mechanism/alerts/run_bot.py    │ (aiogram long-polling, lock port 47831),
│  + one-shot channel senders     │ reads/writes its own Postgres tables
│  (send_daily_digest.py, etc.)   │ (digest_runs, bot_users, bot_tracked, …).
└──────────────────────────────┘ Talks to Postgres directly, never to the FastAPI backend.
```

**What was actually open before this session:** every FastAPI router (`screener`, `stock`, `alpha`,
`strategy`, `deep_value`, `system_health`, `ml_stats`, `performance`, `market`) and the inline
`/api/dashboard/*` routes in `backend/main.py` — anyone who could reach port 8000 could read (and, for
Telegram, given the one shared secret, sometimes write) everything. The Telegram bot's own access model
(`mechanism/alerts/access.py` — Owner in `.env`, invite-only `bot_access` table) was already solid; the
*dashboard* had nothing equivalent.

## 2. What this session built: auth + RBAC (Owner + Collaborator)

Two roles, matching how the system is actually used (one owner, one collaborator helping build it):

- **Owner** — full access, including Telegram channel compose/edit/delete/pin.
- **Collaborator** — signed-in read access to everything (dashboard, screener, strategy, alerts,
  system-health, ml-stats, performance, Telegram *views*) but cannot mutate anything. The only mutation
  surface anywhere in this system is Telegram channel posts, so "Collaborator" in practice means
  "everything except that."

Login is password + an emailed 6-digit 2FA code (not just a password), because this dashboard now controls
a public Telegram channel and the deactivate/reactivate/session-revoke tools an Owner needs.

### Design, mirroring `mechanism/alerts/access.py`'s philosophy
Fail closed (no `JWT_SECRET` → the auth system refuses to issue tokens, not "works insecurely"), hash every
secret before it touches the database (`dashboard_users.password_hash` is bcrypt; refresh tokens, 2FA
codes, and challenge tokens are sha256 — see `backend/auth/security.py`), and audit every privileged action
(`dashboard_auth_audit` — login attempts, lockouts, refresh, logout, user create/deactivate, session
revoke).

### Data model (`mechanism/add_dashboard_auth_tables.sql`)
- `dashboard_users` — the (currently two) accounts. `role` gates everything.
- `dashboard_login_challenges` — one row per in-progress login: password already checked, 2FA code emailed,
  not yet verified. Everything in it is hashed.
- `dashboard_sessions` — refresh tokens, hashed, revocable. This is what lets the Owner instantly end a
  Collaborator's session from `/users` without waiting for a 30-minute access token to expire on its own.
- `dashboard_auth_audit` — who did what, when, to whom. Never a password, code, or token.

### Flow
1. `POST /api/auth/login` (email + password) → on success, a 6-digit code is emailed and an opaque
   `challenge_token` is returned. Wrong password → 401 (with a small fixed delay to slow guessing).
2. `POST /api/auth/verify-2fa` (challenge_token + code) → on success, a short-lived JWT **access token**
   (default 30 min, in-memory on the client only) and a long-lived **refresh token** (default 30 days,
   stored in the browser's `localStorage`, hashed server-side in `dashboard_sessions`) are issued.
3. The frontend's single axios client (`frontend/src/services/api.ts`) attaches
   `Authorization: Bearer <access token>` to every request and, on a single 401, silently exchanges the
   refresh token for a new access token and retries once before giving up and signing the user out.
4. `POST /api/auth/refresh` / `POST /api/auth/logout` — as they sound. `GET /api/auth/me` — the current
   user. Owner-only: `GET/POST /api/auth/users`, `PATCH .../deactivate`, `PATCH .../reactivate`,
   `GET .../sessions`, `DELETE .../sessions/{id}`.

### Where it's enforced
- Every existing router got `dependencies=[Depends(require_authenticated_user)]` added to its
  `APIRouter(...)` constructor (`backend/routers/screener.py`, `stock.py`, `alpha.py`, `strategy.py`,
  `deep_value.py`, `system_health.py`, `ml_stats.py`, `performance.py`, `market.py`) — one line each.
- `backend/main.py`'s inline `/api/dashboard/*` routes got the same dependency added per-route.
  `/api/health` stays open (a liveness probe that leaks nothing).
- `backend/routers/telegram_control.py`'s read endpoints now also require
  `Depends(require_authenticated_user)`; its mutation endpoints (compose, edit, delete, pin, unpin,
  replace-photo) require **both** `Depends(require_owner)` (the new RBAC layer) **and** the pre-existing
  `Depends(require_control_token)` (the `TELEGRAM_CONTROL_TOKEN` secret, kept as defense-in-depth — not
  replaced).
- The frontend hides what a role can't use rather than just relying on the API 403: `/users` is absent from
  the Collaborator's nav (`frontend/src/lib/navigation.ts`'s `navGroupsForRole`), and the Telegram page
  (`frontend/src/app/telegram/page.tsx`) shows a "view only" notice instead of the compose/edit/delete UI
  when signed in as Collaborator.

### Bootstrap and emergency recovery
There is no self-signup and no "forgot password" flow in the web app. `backend/scripts/create_user.py` (run
directly on the machine, DB access required) covers both: `--email ... --role owner` creates the first
account; `--email ... --reset-password` is the emergency path if you're locked out (wrong password, 2FA
email never arrives) — it resets the password, reactivates the account if it had been deactivated, and
signs out every existing session for it. Passwords are always prompted (`getpass`), never a CLI argument.
The Owner creates the Collaborator normally from the `/users` page in the dashboard.

### A real bug found and fixed while building this
`backend/main.py` called bare `load_dotenv()`, which searches **upward from `main.py`'s own directory** —
and a stale, untracked, gitignored `backend/.env` (DB config only, last touched long before
`TELEGRAM_CONTROL_TOKEN` or any of this session's new `.env` keys existed) was shadowing the real
repo-root `.env` every time the backend was started the way this project's own docs say to
(`cd backend && uvicorn main:app --reload --port 8000`). Practical effect: **the Telegram Control Center's
edit/delete gate has been silently disabled** under that startup path, since `TELEGRAM_CONTROL_TOKEN` was
never actually read from the file it's documented to live in — and the new `JWT_SECRET`/`SMTP_*` config
would have hit the same problem. Fixed at the root: `main.py` now points `load_dotenv()` at the repo-root
`.env` explicitly (`Path(__file__).resolve().parent.parent / ".env"`), matching what CLAUDE.md §6 already
documented as the intended behavior. The stale file was moved aside, not deleted:
`backend/.env.stale-superseded-2026-09-22`.

## 3. Target near-term service boundary (still one VPS — no containers yet)

The four services in §1 are already the right seams. The next step, when there's appetite for it, is
formalizing that boundary rather than changing it:

| Service | Today | Near-term target |
|---|---|---|
| Frontend | `next dev` locally | Own deploy unit (see hosting recommendation below); no code coupling to the other three beyond the API's public URL. |
| Backend API | `uvicorn main:app` locally | Own process/port, same as today; the piece that would move behind a real domain + HTTPS first. |
| Mechanism pipeline | Windows Task Scheduler jobs, no server | Stays a scheduled batch job, not a server — containerizing it means a scheduled container run (e.g. a cron container or the VPS's own cron), not a long-running one. |
| Telegram bot | `run_bot.py`, long-polling, own lock port | Already the most "microservice-shaped" piece of this system — just needs a process supervisor (systemd/pm2) instead of a manually-started terminal. |

**Explicitly deferred, not built this session:** Dockerfiles per service, `docker-compose.yml`, a shared
network/service-discovery layer, moving `TELEGRAM_CONTROL_TOKEN` fully into RBAC (it stays as a second,
independent secret for now), and HTTPS/reverse-proxy termination on the VPS.

## 4. Hosting recommendation (write-up only — nothing below was migrated)

The user already runs an AWS EC2 instance and asked for a cost comparison against Supabase (Postgres) and
Vercel (frontend) before deciding anything.

- **Database — keep Postgres on the existing EC2 instance.** It's already there, already sized correctly
  for the ~1,000–2,500 symbol universe, and costs nothing beyond what's already being paid for the
  instance. Supabase's free tier auto-pauses an inactive project — a bad fit for a database a scheduled
  pipeline writes to every night regardless of whether anyone's looking at the dashboard. Supabase's paid
  tier (~$25/mo) is worth revisiting **only if** managed backups/point-in-time-recovery/failover become a
  priority later; until then it's pure added cost for no capability this project is currently missing.
- **Backend API + Telegram bot + pipeline — keep on the existing EC2 instance.** They already run there (or
  are meant to); moving them anywhere else before there's a second server to justify it would just add
  network hops between the API and its database for no benefit.
- **Frontend — Vercel's free tier.** It's a Next.js app built by the company that makes Vercel; the free
  tier is generous for a 2-user internal tool (no realistic chance of hitting its limits), deploys are
  zero-config from the existing repo, and it removes the need to also run `next build`/a Node process on
  the EC2 box behind nginx. Point `NEXT_PUBLIC_API_BASE_URL` at the EC2 backend's public HTTPS endpoint.
  **This does mean the EC2 backend needs a real domain + TLS certificate before this works** (Vercel's
  HTTPS frontend calling an HTTP-only EC2 backend would be blocked by the browser's mixed-content policy) —
  that's the one prerequisite this recommendation assumes gets done first.
- **Net added cost: $0** beyond what's already being paid for the EC2 instance and the domain (which
  already has the email mailbox used for 2FA).

## 5. Explicitly out of scope for this session

- Docker/compose/full containerization.
- HTTPS + reverse proxy setup on the EC2 box (a prerequisite for the Vercel-frontend recommendation above,
  not done here).
- Self-service password reset (today: the Owner resets a Collaborator by deactivating + recreating the
  account, or a direct DB update — acceptable at 2 users, revisit if that changes).
- Folding `TELEGRAM_CONTROL_TOKEN` fully into RBAC (kept as an independent second secret, deliberately).
- Inviting further users beyond Owner/Collaborator (the role model has room to grow — `role` is a plain
  `VARCHAR` `CHECK` constraint, not an enum baked into the JWT format — but nothing beyond two roles was
  asked for).
