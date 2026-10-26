# Product release plan: MVP data consistency (prepared, NOT executed)

Status: **plan for owner approval**, prepared 2026-10-26 and verified that day. Nothing in it has been run. Nothing may run before the preconditions in §1 hold and the owner gives an explicit GO **after Stage 2 is COMPLETE**.

## 0. Frozen release manifest

### Reviewed code

**Commit `f7059e0dd69b141a9a32b92930b4b25e050c475b`** (`f7059e0`, the merge of PR #66), `main` CI green on 2026-10-26.

| Path at `f7059e0` | Git tree / blob |
|---|---|
| (root) | `ef832a604b816b5c2ee45cff0fbd3d52a2a927cc` |
| `backend/` | `a41cdd31b903feb1f4ab3ae57a0fd514b4c91ee3` |
| `frontend/` | `7aa25b59aba6761db1ced1a199c0fe9d49ead809` |
| `mechanism/` | `5558298225cce5cb9b03a4c63cfa96d5faa41ce6` |
| `deploy/` | `5e3bec5745e73a62e7fd35bb504ca0969fb6b94d` |
| `.github/` | `e088003d870aeff311455b0f0626b56bc9f29647` |
| `docker-compose.yml` / `docker-compose.prod.yml` | `7b9cf63f1d6f…` / `f9a230868fea…` |

### Release commit (`<RELEASE_SHA>`)

The CD workflow builds and tags the backend image from the **head of `main`** at dispatch: the tag is the first 12 characters of that commit, and only `main` may deploy to production. So `<RELEASE_SHA>` is `main`'s head on the day. It is acceptable **only if it is code-identical to `f7059e0`**. Before dispatch:

```
git fetch origin && git log --oneline f7059e0..origin/main           # every extra commit, named and reviewed
git diff --name-only f7059e0 origin/main -- . ':(exclude)docs'         # MUST print nothing
```

- **Docs only:** if `main` moved by docs only (e.g. this PR's merge), its head is the release commit and ships the reviewed code byte for byte.
- **Anything outside `docs/` changed:** stop. The extra commits need their own review, CI and a new owner GO. A moving branch tip is never deployed unreviewed.

### Targets

| | Release target | Rollback, first line | Rollback, second line |
|---|---|---|---|
| **Backend** | `ghcr.io/danielbugi/wolfx_alpha-backend:<RELEASE_SHA[:12]>`, built by CD from `<RELEASE_SHA>`; record its digest | `b8b706d33bab`, digest `sha256:ea814649898d…`: bundle `/opt/donchian/releases/b8b706d33bab/` present, image cached (verified 2026-10-26 17:02Z) | `c6827ff0ff76`, digest `sha256:088d9645d8ca…`: bundle present, image cached (verified) |
| **Frontend** | `vercel deploy --prod` of `git archive <RELEASE_SHA> frontend`; record the new deployment id | `first-light-dashboard-bezlzpzie`, id `dpl_Fb5PYYrjSMMiXPwRVJdcnGd61VDt`, production, aliases `dashboard.first-light.finance` and `first-light-dashboard.vercel.app` (verified with `vercel inspect`, 2026-10-26) | `first-light-dashboard-riumoavzt` |

### What ships

| PR | Backend | Frontend |
|---|---|---|
| #62 Watchlist Performance view | — | ✓ |
| #64 Overview hand-offs, "Screen this sector", Data as of, info tips | — | ✓ |
| #65 Overview movers + Stock Peers on the Screener engine; movers cache tied to the newest price date | ✓ | ✓ |
| #67 Overview tiles + sector heatmap on the Screener engine; cache | ✓ | ✓ |
| #66 one earnings read model with confidence; `/api/earnings` batch; Yahoo live lookup removed | ✓ | ✓ |
| #63, #68, #69 docs | — | — |

**Not in this release:**
- Stage 2 / the mechanism pin (its own window);
- the bot (`575fa5dc0599`);
- the paper tracker (`45c2ac39249d`);
- migrations (none needed);
- PR #49;
- `donchian-earnings-today.timer` (stays disabled).

## 1. Preconditions (all required)

1. **Stage 2 COMPLETE.**
   - The 2026-10-27 cutover is done, and the **first scheduled pipeline run on the new image (10-27 23:45 Jerusalem)** is verified, with the Stage 2 final report status **COMPLETE**.
   - If Stage 2 is **PARTIAL or ROLLED BACK**, this release is on hold pending a new owner review.
2. **Release commit:** `<RELEASE_SHA>` passes the §0 code-identity check; CI is green on it (all 6 jobs).
3. **Window: Wed 2026-10-28, 13:00–15:00 Jerusalem (11:00–13:00 UTC).**
   - Verified free against every enabled timer (unit files, 2026-10-26). The nearest jobs:
     - before: the backup at 11:45 (took 2 min on 10-26) and the tracker at 11:30;
     - after: the notice at 20:00 and the pipeline at 23:45 Jerusalem (17:45 New York).
   - It also precedes the US open (15:30 Jerusalem).
   - On the day, re-run `systemctl list-timers 'donchian-*'`: nothing due within 30 minutes, nothing running.
4. **Re-read production state on the day:**
   - backend `CURRENT_SHA` (expected `b8b706d33bab`) and `PREVIOUS_GOOD_SHA` (`c6827ff0ff76`), both bundles and images present;
   - Vercel production (expected `bezlzpzie`, `vercel ls` / `vercel inspect`).
5. **Explicit owner GO** for this release, after Stage 2 is COMPLETE.

## 2. Order and compatibility

**Backend first, then frontend, each with its own gates.**

| | Old frontend (`bezlzpzie`) | New frontend |
|---|---|---|
| **Old backend** (`b8b706d33bab`) | today | ❌ the Earnings page's watchlist rows need `/api/earnings` (absent); movers, tiles and earnings have no session or confidence |
| **New backend** | ⚠ **compatible during the day, not across a pipeline run** (below) | target |

**Why "old frontend + new backend" is only conditionally compatible.** The new backend sends **`null` instead of fabricated defaults**. The old frontend dereferences three such fields without a guard:
- `market_indicators.avg_rsi` and `market_indicators.avg_volume_ratio`, through `.toFixed()` on the Overview. These are null when no indicator exists for the session.
- A movers row's `price_change_pct`, through `.toFixed()`. It is null for an unusual-volume stock with no previous close.

These nulls do **not** occur on a normal session; all were present on 10-23. But they **can** occur:
- during the nightly pipeline, after prices for a new session load and before its indicators are computed;
- for a new listing.

Consequences:
- **Gate B3a** below checks the live payload before the frontend goes out.
- The two-version state (new backend, old frontend) **must not span a pipeline run**: the frontend follows the backend within the same window.
- If the frontend has to be rolled back, the backend is rolled back too, before 23:45 Jerusalem (§5).

## 3. Backend release

**Run (only after the §0 check):** `gh workflow run cd.yml --ref main -f service=backend -f confirm_ci_passed=yes`. Its `deploy` job runs `deploy.sh backend <tag>`. The container health gate rolls back automatically on failure, and the pointers are kept by `deploy.sh`.

Record the image tag and digest.

`deploy.sh` checks only container health. **Gates B1–B6 are run by the operator, read-only.** They use the in-container TestClient and read-only DB sessions, as in the PR verifications. Any failure → §5 rollback.

| Gate | Check | Pass |
|---|---|---|
| **B1 Health** | Container `healthy`; `/health` 200; `/openapi.json` lists `/api/earnings` | All true |
| **B2 Auth** | `/opt/donchian/scripts/auth_smoke.sh` (protected endpoints 401 without a token, 200 with one). Also `/api/earnings`, `/api/stock/AAPL/earnings`, `/api/dashboard/main-page-data`, `/api/screener/market-overview` without a token | 401 / 200 as expected |
| **B3 Session parity** | `main-page-data.movers.session` = `market-overview.session` = the Screener `search` session = the newest real session; every listed mover has a bar on it | All equal |
| | The unusual-volume tile = the Screener total for `min_close=1&min_volume_ratio=2&session=…` | Equal |
| **B3a Old-frontend safety** | `market_indicators.avg_rsi`, `avg_volume_ratio` non-null; no movers row with null `price_change_pct` | True. Otherwise do not soak: deploy the frontend now, or roll back. |
| **B3b Earnings confidence** | Read-model `next` (date + confidence) = Screener `next_earnings_date` / `next_earnings_confidence` over the full universe | 0 mismatches (10-26: 0 of 3,060) |
| | One live symbol per state through `/api/stock/<sym>/earnings`: upcoming **confirmed**, upcoming **unverified**, **no upcoming** date, **reported**, **awaiting results** | Each returns its state. Live examples on 10-26: confirmed A; unverified AIV (META until 10-28); none ABCB; reported A; awaiting AAP. All 6 row states were present in production on 10-26 (2,780 confirmed, 79 unverified, 805 superseded/hidden, 650 awaiting, 61,054 reported, 92 symbols with no upcoming date). |
| | A state absent on the day | Covered by the isolated fixtures in `backend/tests/test_earnings_read_model.py` (CI `db-bootstrap-integration`), **never** by editing production data |
| | `/api/stock/<sym>/earnings` keeps its `earnings_dates` shape; `/api/earnings?symbol=A&symbol=B` returns both | True |
| **B4 Cache freshness** | `movers.newest_price_date` and `market-overview.newest_price_date` = `MAX(date)` of `stock_prices` | True |
| | `X-Data-Session` header on `/api/dashboard/top-gainers` = the session | Equal |
| **B5 Latency** (warm, median of 5, in-container) | Screener page `search` | ≤ 1.0 s (measured 0.51) |
| | Movers build / market-overview build (cold) | ≤ 1.0 s each (0.55 / 0.51) |
| | `main-page-data` and `market-overview` (cached) | ≤ 0.1 s |
| | `/api/stock/<sym>/earnings` | ≤ 0.1 s (0.002) |
| | `/api/earnings` with 30 symbols | ≤ 0.2 s (0.012) |
| | Momentum board | ≤ 0.5 s (0.21) |
| | Stock context / Peers query | ≤ 1.0 s (0.52) |
| **B6 Error logs** | `docker logs --since 15m donchian-screener-backend-1` | No new ERROR or traceback |

**Soak: 30 minutes**, mandatory before the frontend. Re-check B1, B4 and B6 at the end. **A healthy backend does not prove the frontend ready**: §4 has its own gates.

## 4. Frontend release (only after §3 passes and the soak)

1. **Build from a clean export** of the release commit, as in the existing manual procedure (the Vercel project is not git-connected):
   - `git archive <RELEASE_SHA> frontend`
   - `npm ci`
   - `NEXT_PUBLIC_API_BASE_URL=https://api.first-light.finance`
   - `vercel deploy --prod`

   Record the new deployment id and URL.
2. **F1 Smoke (5 min):**
   - every product route returns 200: `/`, `/sectors`, `/sectors/technology`, `/screener`, `/stock/AAPL`, `/watchlist`, `/markets`, `/earnings`;
   - no console errors on Overview, Stock or Earnings;
   - the Overview movers card and the activity tiles show "session YYYY-MM-DD";
   - the unverified symbol from B3b shows "Previously reported: … — not confirmed by latest check" (no countdown) on its stock page, its chart marker and its Earnings page row;
   - the confirmed symbol shows an ordinary countdown.
3. **F2 Acceptance (mandatory):** `docs/product/MVP_ACCEPTANCE_CHECKLIST_2026-10-26.md` on the **deployed** build, on a phone and a laptop. Every **must** line passes.
4. **Failure** of F1 or of any must line → §5: roll back the frontend, then the backend before 23:45 Jerusalem (§2).

## 5. Rollback

| What | Target | How | Time |
|---|---|---|---|
| **Backend** | `b8b706d33bab` (becomes `PREVIOUS_GOOD_SHA` when the new tag deploys) | `/opt/donchian/scripts/rollback.sh backend` (`DEPLOY_MODE=rollback`; records the new tag in `FAILED_SHAS`) | ~1 min |
| Backend, second line | `c6827ff0ff76` (`KNOWN_GOOD_SHAS`) | `deploy.sh backend c6827ff0ff76` | ~1 min |
| **Frontend** | `bezlzpzie` (`dpl_Fb5PYYrjSMMiXPwRVJdcnGd61VDt`) | `vercel rollback https://first-light-dashboard-bezlzpzie-danielbugis-projects.vercel.app` (or `vercel promote`) | ~1 min |
| **Both** | Frontend first, then backend, **before the 23:45 Jerusalem pipeline** | Never the new frontend on the old backend (§2); never the old frontend on the new backend across a pipeline run (§2) | ~2 min |

**Data:** nothing to undo. There are no migrations, and the new endpoints are read-only. Caches are in-process and are dropped with the container.

## 6. Out of scope, unchanged

The Stage 2 image, pins, arming, timers, containers and scripts; the bot; the paper tracker; the private preview (`fl-preview-*`); `donchian-earnings-today.timer` (disabled).
