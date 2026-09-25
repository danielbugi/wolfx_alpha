# Testing

> **Purpose:** what test suite covers what, and the commands to actually run them. This is a map,
> not a policy document — see [../architecture/CI_CD.md](../architecture/CI_CD.md) for what CI
> itself runs automatically on every push.
> **Last verified:** 2026-09-25, against the actual test directories on disk.

## Coverage map

| Suite | Command | What it validates | Needs a real DB? |
|---|---|---|---|
| Backend auth | `python -m pytest backend/auth/tests -q` | Password hashing, JWT issuance/verification, lockout behavior | No |
| Backend (general) | `python -m pytest backend/tests -q` | `market_service.py` filter logic. **Genuinely thin** — 2 files total; no router has a dedicated test. Don't assume an endpoint's behavior is verified just because it exists. | No |
| Mechanism / alerts | `python -m pytest mechanism/alerts/tests -q` | The largest suite by far (25 files, ~544 `test_` functions) — digest/board/gainers/health builders, access control, the bot's screens/tracker, channel control, **`post_delivery.py`'s claim/reclaim/concurrency**, `publish_post_market.py`'s orchestration | Some tests need Postgres and self-skip cleanly if unreachable (e.g. `test_post_delivery.py`) |
| Mechanism / data updaters | `python -m pytest mechanism/data_updaters/tests -q` | `earnings_calendar_updater.py` | Check per-file |
| Mechanism mutation checks | `python mechanism/alerts/tests/mutation_checks.py --check` (or `--group <name>`) | Re-introduces known-bad code patterns and asserts the test suite actually catches each one — a check on the tests themselves, not the code | No |
| ML training | `python -m pytest ml_training/tests -q` | Feature engineering (`price_features.py`), a DB train/serve parity check (`test_feature_parity_db.py` — skips if Postgres unreachable), the new `fundamentals_features.py` (unit-tested standalone, not yet wired into live training) | Parity test needs Postgres; others don't |
| Frontend types/lint | `cd frontend && npx tsc --noEmit && npx next lint` | Type errors, lint violations — no unit test framework is in place for `frontend/src` today | No |
| Frontend build | `cd frontend && NEXT_DIST_DIR=.next-build npm run build` | An isolated production build (never touches the dev `.next` cache) | No |
| Docker/Compose validation | CI's `docker-validate` job (`.github/workflows/ci.yml`) | Both Compose files parse; production-safety invariants hold (no schema bootstrap in prod, no `.env.example` fallback, ports 80/443 only, bot behind a profile, every image SHA-pinned); all 3 images actually build | No (ephemeral, no real secrets) |
| DB bootstrap | CI's `db-bootstrap-integration` job | Every migration file (discovered from `docker-compose.yml`'s own list) applies cleanly to a fresh Postgres, in order; table/view counts; PK/FK/UNIQUE/index/grant facts for the newest table groups; a real INSERT/SELECT/DELETE round trip through a foreign key | A real ephemeral Postgres, spun up by the CI job itself |

## Telegram publishing / concurrency — specifically

`mechanism/alerts/tests/test_post_delivery.py` is the one to read if you're touching anything in
[../architecture/TELEGRAM_PUBLISHING.md](../architecture/TELEGRAM_PUBLISHING.md)'s claim/retry
contract. It exercises, against real Postgres (self-skips if unreachable):

- A fresh claim succeeding, a second claim on the same key failing while the first is still fresh.
- A stale `'reserved'` row becoming reclaimable after the timeout; a `'failed'` row being reclaimable
  immediately.
- **A real two-thread race** (`ThreadPoolExecutor`, two separate pooled connections) proving exactly
  one of two simultaneous claims wins — the actual concurrency guard under test, not just the Python
  wrapper around it.
- `publish_post_market.publish()`'s orchestration: a fresh session sends all 4 kinds; a second call
  sends nothing (already delivered); a partial failure only retries the missing kind(s); a dry run
  never claims or sends anything.

Synthetic, far-future session dates (`2099-01-14`/`-15`) are used throughout specifically so test
runs can never collide with or mask real production/dev delivery rows.

**A rule for any new test here**: never send a real Telegram message as part of a test run — stub
`TelegramClient`/the sender functions, as the existing tests already do (see `_stub()` in
`test_post_delivery.py` for the established pattern).

## Running everything relevant to a typical change

```bash
# Backend-only change
python -m pytest backend/auth/tests backend/tests -q

# Mechanism/ML change
python -m pytest mechanism/alerts/tests mechanism/data_updaters/tests ml_training/tests -q
python mechanism/alerts/tests/mutation_checks.py --check

# Frontend change
cd frontend && npx tsc --noEmit && npx next lint && NEXT_DIST_DIR=.next-build npm run build

# Anything touching docker-compose.yml, a migration file, or a systemd unit
# -- push and let ci.yml's docker-validate / db-bootstrap-integration jobs run;
#    both spin up real ephemeral infrastructure that's impractical to fully replicate by hand
```

## What's genuinely undertested (know this before assuming otherwise)

- Every backend router except auth — no dedicated test file exists for `screener.py`, `stock.py`,
  `strategy.py`, `deep_value.py`, `system_health.py`, `ml_stats.py`, `momentum_board.py`,
  `momentum_leaders.py`, `performance.py`, `telegram_control.py`, `bot_access.py`, `market.py`,
  `alpha.py`.
- `frontend/src` has no unit test framework — `tsc`/`lint`/`build` catch type and build errors, not
  behavioral regressions.
- A live-verification report (a real browser session, a real API round trip) sometimes stands in for
  automated test coverage on a given change — check for one before assuming a feature is either
  tested or untested; this project has a real history of both directions being wrong to assume.
