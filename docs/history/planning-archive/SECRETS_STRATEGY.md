> **HISTORICAL DOCUMENT — NOT CURRENT PRODUCTION ARCHITECTURE.** A pre-execution planning document
> from before the VPS migration/cutover was actually carried out. Preserved for reference/history only.
> For current architecture, see [docs/architecture/](../../architecture/), [docs/operations/](../../operations/),
> [CLAUDE.md](../../../CLAUDE.md), and [../../devops/CUTOVER_PLAN.md](../../devops/CUTOVER_PLAN.md) (the
> one actively-maintained infrastructure doc, kept in place — not archived — while Gate 5 remains
> outstanding). Moved here 2026-09-25; content below is unmodified except for this banner.

# Production Secrets Strategy

> Phase 4A — design only. No secret has been created, rotated, copied, or stored anywhere as a
> result of this document. Companion to
> [PRODUCTION_INFRASTRUCTURE.md](PRODUCTION_INFRASTRUCTURE.md) and
> [CD_DESIGN.md](CD_DESIGN.md). Builds on `INFRASTRUCTURE_PLAN.md` §6.6's earlier recommendation
> (no dedicated secrets-manager product at this scale), now made concrete and complete.

## 1. Hard constraints (restated, and how each is satisfied)

| Constraint | How it's satisfied |
|---|---|
| Must not exist in Git | The real production `.env` is never committed — `docker/.env.example` (already in git, Phase 2) contains only placeholder/dummy values, confirmed by direct inspection in Phase 2 and again by grep in Phase 3 |
| Must not exist in Docker images | Confirmed empirically in Phase 2 (`docker run --rm <image> find / -iname "*.env*"` returned nothing) and `.dockerignore` explicitly excludes `.env`/`.env.*` from every build context |
| Must not be stored in GitHub workflow files | `ci.yml` (Phase 3) has zero `secrets.*` references; the CD workflow design in `CD_DESIGN.md` uses only the minimum GitHub Actions secrets needed to reach the VPS (§3 below), never application secrets |
| Must not appear in CI logs | Nothing sensitive is echoed anywhere in `ci.yml`; the CD design's deploy step passes the SSH key via GitHub's native secret-masking (Actions automatically redacts any string that matches a registered secret's value in logs) |
| Must not be copied from local dev `.env` automatically | No automation anywhere copies the real root `.env` to the VPS or to GitHub — provisioning the production `.env` is an explicit, one-time, manual action (§5) |

## 2. Secrets inventory and placement decision

For every secret this application actually uses (per `CURRENT_ARCHITECTURE.md` §6's real inventory),
a placement decision, using the framework requested: **(A) VPS only, (B) GitHub Actions secret, or
(C) external secrets manager**.

| Secret | Used by | Placement | Why |
|---|---|---|---|
| `DB_PASSWORD` | Postgres, backend, bot, pipeline | **A — VPS only** | Never leaves the host; the backend/bot/pipeline containers read it from the VPS's own `.env` via `env_file:`, never from GitHub |
| `JWT_SECRET` | backend auth | **A — VPS only** | Signs dashboard session tokens; has no reason to exist outside the running backend process |
| `TELEGRAM_BOT_TOKEN` | bot, channel-sender | **A — VPS only** | The single most consequential secret in this system — controls the real public channel and every private chat. Rotated once as part of migration (see §6) |
| `TELEGRAM_CONTROL_TOKEN` | backend (Telegram Control Center) | **A — VPS only** | Independent, second secret for the same subsystem — deliberately kept separate per `CURRENT_ARCHITECTURE.md`'s documented defense-in-depth design; not merged into anything else here |
| `TIINGO_API_KEY`, `ALPACA_API_KEY`/`ALPACA_API_SECRET` | pipeline (data updaters) | **A — VPS only** | Vendor credentials, used only by the scheduled pipeline container |
| `SMTP_PASSWORD` | backend (2FA email) | **A — VPS only** | Used only by the backend process |
| `BOT_OWNER_ID` | bot, alerts | **A — VPS only** (not secret-sensitive in the traditional sense — it's a Telegram user ID, not a credential — but still kept out of git alongside the rest of `.env` for consistency, and because it gates every owner-only bot command) | |
| `PROD_SENDING_ENABLED` | bot, channel-sender | **A — VPS only**, but treated specially: this is a *safety gate*, not a credential. Its value (0/1) is exactly the kind of thing that should require a deliberate, visible, manual edit on the VPS — never something a deploy pipeline flips automatically | |
| SSH deploy key (private half) | GitHub Actions → VPS | **B — GitHub Actions secret** (`DEPLOY_SSH_KEY`) | The one credential that must exist in GitHub, because that's where the deploy job runs. Scoped narrowly (§4) |
| VPS host/IP, deploy username | GitHub Actions → VPS | **B — GitHub Actions secret or variable** (`DEPLOY_HOST`, `DEPLOY_USER` — the host/IP arguably isn't secret, but keeping it out of the workflow file as a variable/secret means it can change without editing tracked YAML) | |
| GHCR authentication | GitHub Actions → GHCR (push) / VPS → GHCR (pull) | **B — GitHub Actions secret for the push side** (`GITHUB_TOKEN`, automatically provided by Actions, no manual secret needed for pushing from a workflow in the same repo); **A — VPS only for the pull side** (a GHCR personal access token or, if the package is made public, no credential needed at all — see §4) | |

**Nothing is placed in category C (external secrets manager).** Per `INFRASTRUCTURE_PLAN.md` §6.6's
original reasoning, unrevised: one VPS, one Owner, one Collaborator, a handful of secrets — a
dedicated vault product (Vault, Doppler, AWS Secrets Manager) would add a vendor dependency and a
rotation workflow disproportionate to the actual risk here. **Revisit only if** the team grows
beyond two people, a compliance requirement demands centralized secret auditing, or the number of
distinct environments needing these secrets grows beyond "one VPS."

## 3. GitHub Actions secrets — the complete, minimal list

Exactly four, all deployment-mechanics, zero application secrets:

| Secret name | Purpose | Scope |
|---|---|---|
| `DEPLOY_SSH_KEY` | Private key for the CD workflow to SSH into the VPS | Repository secret, used only by the (not-yet-activated) CD workflow |
| `DEPLOY_HOST` | VPS hostname/IP | Repository secret (or a plain Actions *variable*, since it's not sensitive — either works; kept as a secret here for consistency with the others and so it's masked in logs by default) |
| `DEPLOY_USER` | The non-root deploy account name | Repository secret/variable, same reasoning |
| *(GHCR push auth)* | **Not a separate secret** — `GITHUB_TOKEN` is automatically injected into every workflow run by GitHub Actions itself, scoped to the repository, and is sufficient to push to `ghcr.io/<owner>/...` for a package owned by the same repo/org | N/A |

No `TELEGRAM_BOT_TOKEN`, no `DB_PASSWORD`, no `JWT_SECRET` — none of the application's own secrets
ever need to reach GitHub, because the CD workflow's job is only to tell the VPS *which image tag*
to pull, never to run the application itself.

## 4. Container registry authentication

- **Push (GitHub → GHCR):** uses the automatic `GITHUB_TOKEN`, standard `docker/login-action`
  pattern, `packages: write` permission granted *only* to the specific CD job that pushes (never
  the CI workflow from Phase 3, which stays `contents: read` only).
- **Pull (VPS → GHCR):** two options, recommendation given:
  - **Recommended: make the GHCR packages private but grant the VPS's own scoped PAT read-only
    `packages:read` access**, stored in the VPS's own `.env` (category A, not GitHub) — the VPS
    authenticates to GHCR itself when pulling, independent of the CD workflow's push credential.
  - Alternative (simpler, slightly less private): make the GHCR packages public. Since this is
    application code, not secrets (the images contain no `.env`, confirmed in Phase 2), the actual
    confidentiality risk of a public image is low — but the *default* recommendation is still
    private + scoped PAT, since "public by default" is an easy thing to regret later and costs
    nothing extra now.

## 5. How the production `.env` actually gets onto the VPS (a manual, one-time action)

There is **deliberately no automation** for this — per the explicit constraint that secrets must
never be "copied from the local development `.env` automatically." The one-time provisioning
sequence (a manual action *you* perform, not something this design automates):

1. On your own machine, create a **new** file (not a copy of the real root `.env`) containing only
   the production values, using `docker/.env.example`'s structure as the template for which keys
   are needed.
2. Copy that file to the VPS over SSH (`scp`) directly into `/opt/donchian/env/.env`, `chmod 600`,
   owned by the `deploy` user.
3. Delete the local staging copy from your machine once confirmed on the VPS.
4. Never re-run this except for a deliberate secret rotation (§6).

This is intentionally a manual, boring, one-time human action — exactly the kind of step that
should NOT be automated, because automating "copy secrets to a new environment" is precisely the
mechanism that would violate the "never copied automatically" constraint if it existed as a script
anyone could re-run.

## 6. Secret rotation as part of migration

Recommended (not yet executed) to rotate these two specifically, since their current values have
lived in a plaintext local file with a confirmed history of at least one stale duplicate existing on
disk unnoticed (`backend/.env.stale-superseded-2026-09-22`, per `CLAUDE.md`'s own changelog):

- `TELEGRAM_CONTROL_TOKEN` — low blast radius to rotate (only the dashboard's Telegram Control
  Center uses it), good first candidate.
- `JWT_SECRET` — rotating it invalidates every existing dashboard session (both Owner and
  Collaborator get logged out once) — trivial to recover from (just log back in), acceptable
  during a migration window.

**Not recommended to rotate during migration:** `TELEGRAM_BOT_TOKEN` (rotating it means talking to
BotFather and re-pointing the *already-running* production bot mid-migration — higher risk, and
Phase 2/3 never found a reason to distrust this specific credential; defer to a calmer moment) and
vendor API keys (`TIINGO_API_KEY`, `ALPACA_API_KEY`) unless there's a specific reason to believe
they were exposed.

## 7. Preventing local dev / Claude tooling from ever touching production again

**Direct response to the incident recorded in `CI_IMPLEMENTATION.md` §9** (an accidental real-DB
connection during Phase 3's local CI investigation, caused by `backend/main.py`'s
`load_dotenv(..., override=True)` picking up the real root `.env` when a command was run directly
on the host). Concrete, layered recommendations:

1. **Never run `python -c "import main"` (or any bare invocation of `main.py`) directly on the host
   outside Docker for testing purposes.** Documented explicitly in `CI_IMPLEMENTATION.md` already;
   restated here as a standing rule for this project, not just a one-off note.
2. **For any future local investigation that needs to import backend code, prefer the Docker
   stack's dummy `docker/.env.example`-configured containers** (already running/available from
   Phase 2) over a bare host invocation — this was in fact the correction already applied mid-Phase
   3 once the incident was noticed.
3. **A concrete, low-effort code-level improvement worth considering (not implemented here —
   would be an application-code change requiring your separate sign-off, flagged per your standing
   instruction, not actioned):** `backend/main.py` could optionally support an
   `APP_ENV=test`/`SKIP_DB_INIT` style guard that defers the eager connection-pool construction when
   explicitly requested, making it possible to `import main` for static analysis without any live
   DB at all. This is a "nice to have" for tooling safety, not a blocker for anything in this phase.
4. **Rename/relocate the real local `.env` is *not* recommended** — CLAUDE.md already documents
   exactly why `.env` lives at the repo root and why `load_dotenv`'s path is explicit
   (`Path(__file__).resolve().parent.parent / ".env"`); moving it would just relocate the same risk,
   not remove it.
5. **The clearest actual safeguard is organizational, not technical:** now that this exact failure
   mode is documented in two places (`CI_IMPLEMENTATION.md` and here), any future session (human or
   Claude) working on this repo has a specific, named thing to avoid, rather than a generic
   "be careful."

## 8. Summary table (for the Phase 4A final report)

| Secret | Lives on VPS | Lives in GitHub | Lives in an external vault |
|---|---|---|---|
| `DB_PASSWORD` | ✅ | ❌ | ❌ |
| `JWT_SECRET` | ✅ | ❌ | ❌ |
| `TELEGRAM_BOT_TOKEN` | ✅ | ❌ | ❌ |
| `TELEGRAM_CONTROL_TOKEN` | ✅ | ❌ | ❌ |
| `TIINGO_API_KEY` / `ALPACA_API_KEY`/`SECRET` | ✅ | ❌ | ❌ |
| `SMTP_PASSWORD` | ✅ | ❌ | ❌ |
| `DEPLOY_SSH_KEY` | ❌ (public half only, in `~deploy/.ssh/authorized_keys`) | ✅ | ❌ |
| `DEPLOY_HOST` / `DEPLOY_USER` | N/A (it's the VPS itself) | ✅ | ❌ |
| GHCR pull credential | ✅ (if packages kept private) | ❌ | ❌ |
