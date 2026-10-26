# Product release plan: MVP data consistency (prepared, NOT executed)

Status: **plan for owner approval**, prepared 2026-10-26. Nothing in it has been run, and nothing may run before the preconditions in §1 hold.

## 0. What ships

The release is one `main` commit, the merge of PR #66 (written `<RELEASE_SHA>` below; the first 12 characters are the image tag). **Candidate: `f7059e0dd69b`**, `main` CI green 2026-10-26. Re-confirm it is still `main`'s head, or re-pick, on the day.

| PR | Backend | Frontend |
|---|---|---|
| #62 Watchlist Performance view | — | ✓ |
| #64 Overview hand-offs, "Screen this sector", Data as of, info tips | — | ✓ |
| #65 Overview movers + Stock Peers on the Screener engine; movers cache tied to the newest price date | ✓ | ✓ |
| #67 Overview tiles + sector heatmap on the Screener engine; cache | ✓ | ✓ |
| #66 one earnings read model with confidence; `/api/earnings` batch; Yahoo live lookup removed | ✓ | ✓ |
| #63, #68 docs | — | — |

**Not in this release:**
- Stage 2 / the mechanism pin (its own window);
- the bot;
- the paper tracker;
- migrations (none needed);
- PR #49.

## 1. Preconditions (all required)

1. **Stage 2 is complete:** the 2026-10-27 cutover is done and the **first scheduled pipeline run on the new image (10-27 23:45 Jerusalem) is verified**, with the Stage 2 final report status COMPLETE.
2. **CI:** green on `<RELEASE_SHA>` (all 6 jobs), and the CD `build-and-push` for that SHA has produced the backend image.
3. **Timing:** no deploy inside any scheduled job.
   - Proposed slot: Wed 2026-10-28, **13:00–15:00 Jerusalem** (11:00–13:00 UTC). That is after the 11:45 backup, before the 23:45 pipeline, and before the US open (15:30 Jerusalem).
   - Check `systemctl list-timers` first: nothing due within 30 minutes.
4. **Production state recorded:**
   - backend `CURRENT_SHA` = `b8b706d33bab`, `PREVIOUS_GOOD_SHA` = `c6827ff0ff76` (re-read on the day);
   - Vercel production = `first-light-dashboard-bezlzpzie` (re-read with `vercel ls` on the day).
5. **Owner GO** for this release, separate from this plan's approval.

## 2. Order

**Backend first, then frontend, each with its own gates.**

| | Old frontend (`bezlzpzie`) | New frontend |
|---|---|---|
| **Old backend** (`b8b706d33bab`) | today | ❌ Earnings page watchlist rows (batch endpoint missing); movers, tiles and earnings without session or confidence |
| **New backend** | ✅ compatible (only additive fields; `/api/stock/{s}/earnings` keeps its shape) | target |

So the backend can be released and soaked alone. The frontend must never go first.

## 3. Backend release

**Run:** `gh workflow run cd.yml --ref main -f service=backend -f confirm_ci_passed=yes`. Its `deploy` job runs `deploy.sh backend <tag>`. The container health gate rolls back automatically on failure, and the pointers are kept by `deploy.sh`.

`deploy.sh` checks only container health. **Gates B1–B6 are run by the operator, read-only.** They use the in-container TestClient and read-only DB sessions, as in the PR verifications. Any failure → §5 rollback.

| Gate | Check | Pass |
|---|---|---|
| **B1 Health** | Container `healthy`; `/health` 200; `/openapi.json` lists `/api/earnings` | All true |
| **B2 Auth** | `/opt/donchian/scripts/auth_smoke.sh`: protected endpoints 401 without a token, 200 with one. Also `/api/earnings`, `/api/dashboard/main-page-data`, `/api/screener/market-overview` without a token. | 401 / 200 as expected |
| **B3 Correctness** | `main-page-data.movers.session` = the newest real session; every listed symbol has a bar on it | True |
| | `market-overview.session` = the same session; the unusual-volume tile = the Screener total for `min_close=1&min_volume_ratio=2` | Equal |
| | Earnings: read-model `next` (date + confidence) = Screener `next_earnings_date` / `next_earnings_confidence`, full universe | 0 mismatches |
| | One symbol per confidence state: at least one confirmed, one unverified, one none | As the DB rule says |
| | `/api/stock/<sym>/earnings` keeps its `earnings_dates` shape; `/api/earnings?symbol=A&symbol=B` returns both | True |
| **B4 Cache freshness** | `movers.newest_price_date` and `market-overview.newest_price_date` = `MAX(date)` of `stock_prices` | True |
| | `X-Data-Session` header present on `/api/dashboard/top-gainers` | Present |
| **B5 Latency** (warm, median of 5, in-container) | Screener page `search` | ≤ 1.0 s (measured 0.51) |
| | Movers build / market-overview build (cold) | ≤ 1.0 s each (0.55 / 0.51) |
| | `main-page-data` and `market-overview` (cached) | ≤ 0.1 s |
| | `/api/stock/<sym>/earnings` | ≤ 0.1 s (0.002) |
| | `/api/earnings` with 30 symbols | ≤ 0.2 s (0.012) |
| | Momentum board | ≤ 0.5 s (0.21) |
| | Stock context / Peers query | ≤ 1.0 s (0.52) |
| **B6 Logs** | `docker logs --since 15m` for the backend | No new ERROR or traceback |

**Soak:** 30 minutes with B1 and B6 re-checked. **A healthy backend does not prove the frontend ready**: §4 has its own gates.

## 4. Frontend release (only after §3 passes and the soak)

1. **Build from a clean export** of the same commit, as in the existing manual procedure (the Vercel project is not git-connected):
   - `git archive <RELEASE_SHA> frontend`
   - `npm ci`
   - `NEXT_PUBLIC_API_BASE_URL=https://api.first-light.finance`
   - `vercel deploy --prod`

   Record the new deployment URL.
2. **F1 Smoke (5 min):**
   - every product route returns 200: `/`, `/sectors`, `/sectors/technology`, `/screener`, `/stock/AAPL`, `/watchlist`, `/markets`, `/earnings`;
   - no console errors on Overview, Stock or Earnings;
   - the Overview movers card shows "session YYYY-MM-DD";
   - an unverified symbol (pick one from B3) shows "Previously reported: … — not confirmed by latest check" on its stock page and in the Earnings page row.
3. **F2 Acceptance:** `docs/product/MVP_ACCEPTANCE_CHECKLIST_2026-10-26.md` on the **deployed** build, on a phone and a laptop. Every **must** line passes.
4. **Failure** of F1 or of any must line → §5 frontend rollback. The backend stays: it is compatible with the old frontend.

## 5. Rollback

| What | Target | How | Time |
|---|---|---|---|
| **Backend** | `b8b706d33bab` (becomes `PREVIOUS_GOOD_SHA` when the new tag deploys) | `/opt/donchian/scripts/rollback.sh backend` (`DEPLOY_MODE=rollback`; records the new tag in `FAILED_SHAS`) | ~1 min |
| Backend, second line | `c6827ff0ff76` (`KNOWN_GOOD_SHAS`) | `deploy.sh backend c6827ff0ff76` | ~1 min |
| **Frontend** | `first-light-dashboard-bezlzpzie` (current production) | `vercel rollback <bezlzpzie URL>` (or `vercel promote`) | ~1 min |
| **Both** | Frontend first, then backend | Never leave the new frontend on the old backend (§2) | ~2 min |

**Data:** nothing to undo. There are no migrations, and the new endpoints are read-only. Caches are in-process and are dropped with the container.

## 6. Out of scope, unchanged

- The Stage 2 image, pins, arming, timers, containers and scripts.
- The bot (`575fa5dc0599`), the paper tracker (`45c2ac39249d`), the private preview (`fl-preview-*`), and `donchian-earnings-today.timer` (stays disabled).
