# CI Implementation

> Phase 3 of the DevOps migration (see [ARCHITECTURE_AUDIT.md](ARCHITECTURE_AUDIT.md) and
> [CICD_STRATEGY.md](CICD_STRATEGY.md) for the earlier design work this implements). **CI only —
> no deployment.** The workflow builds, lints, tests, and validates that Docker images build; it
> never pushes an image anywhere, never touches a server, and needs zero secrets.

## 1. What exists in the repository (inspected before writing anything)

Per the Phase 3 instruction not to invent commands the repo doesn't support, every command below
was actually run locally before being put in the workflow.

| Area | Command | Result when run locally |
|---|---|---|
| Backend tests | `python -m pytest backend/auth/tests -q` | 10 passed, 2.76s. **This is the only real pytest suite backend has** — `backend/test_step1.py`, `backend/test_step2.py` are manual `requests`-based scripts meant to be run by hand against an already-running server (their `test_endpoint()` functions take required positional args, so pytest's default collection would error on them if pointed at `backend/` broadly); `backend/test_simple_queries.py` is a 0-byte empty file. None of the three are part of CI. |
| Mechanism + ML tests | `python -m pytest mechanism/alerts/tests mechanism/data_updaters/tests ml_training/tests -q` | **1,689 passed, 1 skipped**, 68.84s on this dev machine (a 12-core/51GB workstation — a GitHub-hosted runner (2 cores) will be slower; see §7). The one skip is a DB-parity test that self-skips when Postgres is unreachable, by design. `mechanism/data_updaters/tests/` is a real suite not previously called out in `CLAUDE.md`'s summary command — included here. |
| Backend/mechanism linting | — | **None configured.** No `.flake8`, `ruff.toml`, `.pylintrc`, or `pyproject.toml` anywhere in the repo. Reported, not invented — no linter config was added. |
| Python syntax validation | `python -m py_compile <files>` | 143/145 backend+mechanism+ml_training files compile; **2 fail** with a pre-existing `UnicodeDecodeError` (invalid UTF-8 bytes, likely a mis-saved emoji) — `mechanism/quick_analyzer.py` and `ml_training/deployment/ml_integration.py`. Both are already documented in `CLAUDE.md` as legacy/retired, unused code. Excluded from the CI syntax check with an explicit comment, not fixed (would be an existing-application-code change, out of scope without separate approval). |
| Frontend TypeScript | `npx tsc --noEmit` | Clean, exit 0 |
| Frontend lint | `npm run lint` (`next lint`) | Clean — 0 warnings/errors. Note: prints a real deprecation warning ("`next lint` is deprecated and will be removed in Next.js 16") — not a CI blocker today, flagged for future awareness. |
| Frontend build | `NEXT_PUBLIC_API_BASE_URL=... NEXT_DIST_DIR=.next-build npm run build` | Clean production build, 14 routes. **Must use the exact dist-dir name `.next-build`** — a different name (tested with `.next-ci-test`) causes Next.js to rewrite `tsconfig.json`'s `include` array as a side effect, which would leave an unwanted diff. |
| Docker Compose validation | `docker compose --env-file docker/.env.example config --quiet` (base and `-f docker-compose.yml -f docker-compose.prod.yml`) | Both valid |
| Docker image builds | `docker compose build backend / pipeline / frontend` | All three build cleanly (verified in Phase 2 and re-confirmed here) |

## 2. Workflow structure

One file: **`.github/workflows/ci.yml`**. Six jobs, all read-only (`permissions: contents: read`
at the workflow level and repeated per-job for defense in depth), no secrets referenced anywhere.

```
                    ┌─────────┐
                    │ changes   │  (dorny/paths-filter — always runs)
                    └────┬────┘
        ┌────────────────┼────────────────┬──────────────────┬───────────────────┐
        ▼                ▼                ▼                  ▼                   ▼
 frontend-ci       backend-ci       mechanism-ci       docker-validate    db-bootstrap-integration
 (tsc/lint/build)  (py_compile/     (py_compile/       (compose config +  (fresh Postgres, 16 SQL
                    import check/    pytest)            build all 3       files, table/view/
                    pytest)                             images, no push)  privilege checks)
```

All five downstream jobs run in **parallel** (no job needs another except `changes`) — there is no
reason to serialize them, and running in parallel is what keeps total wall-clock time down.

## 3. Triggers

```yaml
on:
  pull_request:
  push:
    branches: [main]
```

Every PR (against any base) and every push to `main`. A `concurrency` group cancels a run's jobs
if a newer commit lands on the same ref (saves runner minutes on rapid force-pushes; harmless on
`main` since each push has a distinct ref there in practice).

## 4. Change detection — the reasoning, not just the filters

Implemented with `dorny/paths-filter@v3`, one filter group per job, with an explicit `infra` group
that gates *every* job (per the Phase 3 instruction that infrastructure-file changes should run
all relevant validation).

| Filter | Paths | Why |
|---|---|---|
| `infra` | `**/Dockerfile`, `docker-compose.yml`, `docker-compose.prod.yml`, `.dockerignore`, `**/requirements.txt`, `frontend/package.json`/`package-lock.json`, `docker/.env.example`, `.github/workflows/**` | Anything that changes *how* the repo builds or is tested — every job re-verifies |
| `frontend` | `frontend/**` | Self-contained; no cross-package imports found |
| `backend` | `backend/**`, `mechanism/shared/**`, `mechanism/alerts/**`, `ml_training/**` | **Not a blind directory filter.** `backend/services/telegram_control_service.py`, `bot_access_service.py`, and `momentum_board_service.py` import `mechanism.alerts.*` and `mechanism.shared.*` by path (`sys.path.append`), and — confirmed empirically during Phase 2 — `bot_access_service.py`'s import chain transitively reaches `ml_training/` too. A change to any of those three areas can break backend imports exactly the way Phase 2 found, so all three are in scope for `backend-ci`. |
| `mechanism` | `mechanism/**`, `ml_training/**` | `mechanism/ml_enhancement/ml_signal_enhancer.py` imports `ml_training/features/price_features.py` for live inference (`CLAUDE.md`'s documented data flow) — a real, intentional cross-package dependency, not incidental |

`docker-validate` runs if *any* of frontend/backend/mechanism/infra changed (it builds all three
images regardless of which one's source changed, since Compose validation covers the whole
topology). `db-bootstrap-integration` runs if `mechanism` or `infra` changed (schema files live
under `mechanism/`, and `infra` covers the workflow file itself).

**Example behavior:**
- A change to `frontend/src/app/page.tsx` only → `frontend-ci` runs; `backend-ci`, `mechanism-ci`,
  `db-bootstrap-integration` are skipped; `docker-validate` still runs (it's gated on `frontend`
  too, since it builds the frontend image).
- A change to `mechanism/shared/database.py` only → `backend-ci` **and** `mechanism-ci` both run
  (shared-dependency fan-out), plus `docker-validate` and `db-bootstrap-integration`.
- A change to `backend/Dockerfile` only → `infra` is true → **all five jobs run**, even though no
  Python or TypeScript source changed, because the build definition itself changed.
- A change to `docs/devops/*.md` only → `changes` runs (cheap), nothing else does.

## 5. Caching

| Layer | Mechanism | Targets |
|---|---|---|
| Node | `actions/setup-node@v4` with `cache: npm`, keyed on `frontend/package-lock.json` | `frontend-ci` |
| Python | `actions/setup-python@v5` with `cache: pip`, keyed on each job's specific `requirements.txt` files | `backend-ci`, `mechanism-ci` |
| Docker layers | `docker/build-push-action@v5` with `cache-from/cache-to: type=gha`, a **separate cache scope per image** (`backend`, `mechanism`, `frontend`) so they don't evict each other | `docker-validate` |

The Docker layer cache is the one that matters most for the concern raised in the Phase 3 brief:
the mechanism image took **~455s uncached locally** (matplotlib/xgboost/alpaca-py/aiogram and
friends). With the GHA cache backend, only a real `mechanism/requirements.txt` change invalidates
that layer — an unrelated code change in `mechanism/alerts/*.py` rebuilds in seconds, not minutes,
because the `pip install` layer above it is untouched and cache-hits.

## 6. Database bootstrap integration test

A dedicated job (`db-bootstrap-integration`) with a `postgres:16-alpine` **service container** —
started fresh by GitHub Actions for this job only, torn down with the runner VM afterward. No
production data, no production credentials; `POSTGRES_PASSWORD: ci_test_only_password` is a
throwaway value that protects nothing and is reused nowhere else.

This exists specifically to protect the two bugs Phase 2 found by hand:

1. `GRANT ALL PRIVILEGES ON ALL VIEWS IN SCHEMA public TO trading_user;` — invalid PostgreSQL
   syntax, aborted the entire 16-file bootstrap after only file 1.
2. Two `PRINT '...';` statements (T-SQL, not PostgreSQL) at the end of the same file.

Both were fixed with your explicit approval at the time (see `mechanism/create_trading_schema.sql`'s
git history) — this job is what stops either class of regression from landing silently again. It
applies all 16 files with `psql -v ON_ERROR_STOP=1`, in the same order `docker-compose.yml` uses,
then asserts:
- ≥ 36 tables in `public` (the exact count Phase 2 verified)
- ≥ 3 views in `public` (`latest_stock_data`, `latest_fundamentals`, `ml_training_data`)
- `trading_user` can `INSERT`/`SELECT`/`DELETE` on a real table (`companies`) and `SELECT` from
  both `latest_stock_data` and `ml_training_data`

Verified locally against a genuinely fresh `postgres:16-alpine` container (not the already-bootstrapped
one from Phase 2) using the exact same command sequence the workflow runs — see §9.

**What this job does *not* cover:** the backend's own router-import regression (missing
`asyncpg`/`ml_training` — the third bug Phase 2 found, in `backend/Dockerfile` rather than the SQL
file). That's `backend-ci`'s job — its dependency-install step deliberately mirrors
`backend/Dockerfile`'s install list exactly, and its import-validation step dynamically imports
every module under `backend/routers/` and `backend/services/`, which is precisely the check that
would have caught it in a clean environment. Two separate bugs, two separate guards — not merged
into one job, so a failure's log tells you which class of problem it is without cross-referencing.

## 7. Docker image build validation

`docker-validate` builds all three images (`backend/Dockerfile`, `mechanism/Dockerfile`,
`frontend/Dockerfile`) via `docker/build-push-action@v5` with `push: false, load: true` — proves
each image builds and loads into the runner's local Docker daemon; nothing is pushed to any
registry (Phase 3 explicitly excludes this). It also validates both Compose files parse and
resolve correctly (`docker compose config --quiet`, base and the `docker-compose.prod.yml` overlay).

This job exists because — as the Phase 3 brief notes — a developer's local Docker cache can hide a
missing dependency that a clean build would catch. It's the same reasoning that caught the
`asyncpg`/`ml_training` gap in Phase 2: that bug was invisible until an image was built from a
truly clean layer cache.

## 8. Expected runtime

Not benchmarked against a real GitHub-hosted runner (Phase 3 doesn't push to GitHub yet — see §9),
so these are estimates grounded in local measurements, not promises:

| Job | Local measurement | Estimated CI runtime (2-core runner, cold cache) | Estimated CI runtime (warm cache) |
|---|---|---|---|
| `changes` | N/A (trivial) | ~10s | ~10s |
| `frontend-ci` | `npm ci` + tsc + lint + build, all fast | ~2-4 min | ~1-2 min |
| `backend-ci` | pytest: 2.76s; import check: <1s | ~2-3 min (mostly `pip install`) | ~30-60s |
| `mechanism-ci` | pytest: part of the 68.84s combined run | ~3-5 min (heavy `pip install`: matplotlib/xgboost/alpaca-py) | ~1-2 min |
| `docker-validate` | mechanism image ~455s uncached locally (per the Phase 3 brief) | ~8-12 min cold | ~2-4 min warm (GHA layer cache) |
| `db-bootstrap-integration` | 16 files + checks: a few seconds once Postgres is ready | ~1-2 min (mostly waiting for the Postgres service to become healthy) | same (no caching applies here) |

Since jobs run in parallel, **total wall-clock time ≈ the slowest job**, not the sum — realistically
`docker-validate` is the long pole, especially on a cold cache.

## 9. Local/static validation performed (no GitHub push)

Per the explicit instruction not to push to GitHub to test this workflow, everything below was
verified locally instead:

- YAML parses correctly (`yaml.safe_load`); all 6 jobs present; every gated job declares
  `needs: changes` and has the expected `if` condition; every job has `permissions: contents: read`.
- Every embedded shell/Python snippet was **extracted from the parsed YAML** (not retyped by hand)
  and executed for real:
  - `backend-ci`'s dynamic router/service import check: 29/29 modules imported cleanly, 0 need a
    live DB.
  - `mechanism-ci`'s `py_compile` command (with the two legacy-file exclusions): 111 files compile
    clean.
  - `frontend-ci`'s exact build command (`NEXT_DIST_DIR=.next-build`): clean build, confirmed no
    `tsconfig.json` side effect.
  - `db-bootstrap-integration`'s full 16-file `psql` loop + all three verification queries: run
    against a **freshly-created, disposable `postgres:16-alpine` container** (not the
    already-bootstrapped Phase 2 one) — 36 tables, 3 views, `trading_user` INSERT/SELECT/DELETE
    and both view reads all passed, then the container was removed.
- `docker compose config --quiet` re-verified for both the base file and the `-prod.yml` overlay.
- `act` (a local GitHub Actions runner) is not installed on this machine and was not installed for
  this task — the per-step extraction-and-execution approach above covers the substance of what
  `act` would verify, without adding a new tool dependency for a one-time check.

**One real mistake made and corrected during this verification, worth recording:** an early test of
`npx tsc --noEmit`/build behavior with a custom `NEXT_DIST_DIR` value accidentally left a modified
`frontend/tsconfig.json` in the working tree (Next.js rewrites it as a side effect of an unfamiliar
dist-dir name). It was caught via `git diff` immediately after and reverted with `git checkout --`
before anything else touched the file. This is exactly why the workflow uses the project's
already-established `.next-build` name rather than an arbitrary one.

**A second incident worth recording:** while investigating whether `backend/main.py` could be
import-checked without a live database, an early local test (`python -c "import main"`, run
directly on the host from `backend/`, outside Docker) unintentionally connected to the **real**
local PostgreSQL instance — `main.py`'s `load_dotenv(..., override=True)` picked up the real
root `.env` (present on this host, absent in a clean CI runner) and `main.py` opens its connection
pool eagerly at import time, not lazily. No queries were executed (pool construction just opens
idle connections), but this did touch real credentials on this development machine, which the task
instructions for this phase said to avoid. This is *not* a risk in actual CI (GitHub-hosted runners
have no access to this machine's `.env` or network), but it's why `backend-ci`'s import-validation
step deliberately imports individual `routers.*`/`services.*` modules rather than `main` itself —
confirmed those import cleanly with zero database access needed.

## 10. Security

- **No production `.env` is loaded anywhere.** `docker-validate` uses `docker/.env.example` (the
  same dummy-value file built and used throughout Phase 2 — invalid Telegram token,
  `PROD_SENDING_ENABLED=0`, throwaway DB creds). `db-bootstrap-integration` uses its own separate,
  even-more-disposable Postgres service with hardcoded CI-only values that aren't reused anywhere.
- **No GitHub Actions secrets are referenced anywhere in the workflow file** — grep confirms zero
  `secrets.*` usages. Nothing in Phase 3 needs one (no registry push, no SSH, no deployment).
- **Nothing prints a secret to logs** — there are no secrets to print; the only credential-shaped
  values in the file are the CI-only Postgres password and the frontend's `NEXT_PUBLIC_API_BASE_URL`
  build arg, which is a public URL, not a secret, by the same reasoning `next.config.ts` already
  encodes (it's inlined into the browser bundle on purpose).
- **Least-privilege permissions**: `permissions: contents: read` at the workflow level, repeated
  per job. No job requests `contents: write`, `packages: write`, `id-token: write`, or anything
  else.
- **No deployment credentials are required or referenced** — consistent with Phase 3's scope
  (CI only).

## 11. What's explicitly out of scope for Phase 3 (deferred, not built)

- Pushing images to `ghcr.io` or any registry.
- Any deploy step (SSH, `docker compose up` against a real host, health-check gating, rollback).
- Mutation-testing checks (`mechanism/alerts/tests/mutation_checks.py`) — real and valuable, but a
  separate, slower verification layer the Phase 3 brief didn't ask for; worth a follow-up workflow
  or a scheduled (non-blocking) job later.
- Fixing the two pre-existing broken-encoding files (`mechanism/quick_analyzer.py`,
  `ml_training/deployment/ml_integration.py`) — reported and excluded, not fixed, per the
  instruction to stop before touching existing application code.
- Adding a Python linter (flake8/ruff/etc.) — none exists today; not invented here, per the
  instruction to report missing validation rather than manufacture it.
