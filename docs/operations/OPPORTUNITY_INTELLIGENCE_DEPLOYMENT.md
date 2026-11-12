# Opportunity Intelligence v1: deployment plan (owner-only release)

Status 2026-11-12: **prepared, not executed.** Runs only after the Fundamental Updates release is stable and on a separate owner GO.
No database migration, no schedule, no mechanism image, no Caddy change.

## What ships
| Part | From | Change |
|---|---|---|
| Backend image | `main` after PR #114 | `GET /api/opportunity/briefs` + `/briefs/{symbol}` (owner-only, read-only); the public Fundamental Updates router now answers 503 unless `PUBLIC_SIGNALS_ENABLED=1` (it is unset, so nothing public changes) |
| Frontend (Vercel) | the same `main` commit | owner page `/opportunities` + owner-only nav entry |

Other backend changes since the running image `ac0eb97f3f0f` (checked 2026-11-12): only the public-signals router/service (off by the switch) and
the opportunity router/service. Verified on production (read-only): the backend role `donchian_app` can SELECT every table the briefs read
(`fundamental_inflection_event`, `fundamental_feature`, `sec_filing`, `companies`, `earnings_calendar`, `price_discontinuities`,
`market_index_prices`, `stock_prices`); the backend image copies `mechanism/` (the briefs import `mechanism/opportunity` and `mechanism/inflection`).

## Prerequisites
1. PR #114 merged; CI green on the merge commit `M`.
2. `PUBLIC_SIGNALS_ENABLED` absent from `/opt/donchian/env/.env` (and the backend env file): `grep -c '^PUBLIC_SIGNALS_ENABLED=' …` = 0.
3. Fundamental Updates record-only stable (no deploy during its maintenance window).

## Steps
1. **Backend**: `gh workflow run cd.yml --ref main -f service=backend -f confirm_ci_passed=yes` (the owner approves the `production` environment).
   Record `CURRENT_SHA` / `PREVIOUS_GOOD_SHA` from the run (previous = `ac0eb97f3f0f`). The backend container restarts (seconds of API downtime);
   Postgres, Caddy, the bot and every timer are untouched.
2. **Frontend**: `git archive M frontend | tar -x` into a clean scratch folder, copy `frontend/.vercel/project.json`, `vercel deploy --prod --yes`.
   Record the previous production deployment id (rollback target) before deploying.

## Verification
- `GET /api/opportunity/briefs` without a session cookie → 401; as a non-owner → 403; as the owner → 200 with `version = brief_v1`,
  `counts.expectations_verified = 0`, the caveats list, and `view` accepting only `all|agreeing|discovery`.
- `GET /api/public/signals` → 503 "not available yet" (record-only rows stay private).
- `/opportunities` loads for the owner (three views); the nav entry is absent for other roles.
- Backend health and the other dashboard pages unchanged.

## Rollback
- Backend: `deploy/vps/rollback.sh` to `PREVIOUS_GOOD_SHA` (`ac0eb97f3f0f`), or re-run CD at the previous commit.
- Frontend: Vercel instant rollback to the recorded previous production deployment.
- Nothing to undo in the database (read-only feature).
