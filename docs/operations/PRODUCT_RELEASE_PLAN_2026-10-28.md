# Product release plan: MVP data consistency (prepared, NOT executed)

Status: **plan for owner approval**, prepared 2026-10-26 and verified that day; Option A safety evidence in §2b (two safeguards still missing). Nothing in it has been run. Nothing may run before the preconditions in §1 hold and the owner gives an explicit GO **after Stage 2 is COMPLETE**.

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

### Code release candidate: `4522d29` (supersedes `f7059e0`)

**Commit `4522d2962377725245e72e196c674629b1e9fd78`** (the merge of PR #70, null-safe frontend). `main` CI run 40376820009 is green.

| Path at `4522d29` | Git tree / blob | vs `f7059e0` |
|---|---|---|
| (root) | `e130de4ddae9273417412950eab3e96adcf1fdb2` | changed (frontend only) |
| `backend/` | `a41cdd31b903feb1f4ab3ae57a0fd514b4c91ee3` | **identical** |
| `frontend/` | `72fe84d1168a7fdd0182f534dc15a9573b8ea064` | changed |
| `mechanism/`, `deploy/`, `.github/`, both compose files | as in the table above | **identical** |

`git diff --stat f7059e0 4522d29 -- . ':(exclude)docs'` touches 9 files, all under `frontend/`:
- `lib/marketOverview.ts` (new);
- `app/page.tsx`, `services/api.ts`, `components/dashboard/MarketMovers.tsx` and `SummaryTiles.tsx`;
- four test files.

**The backend image built from `4522d29` therefore runs exactly the backend code reviewed at `f7059e0`.** The identity check below now uses `4522d29` in place of `f7059e0`.

### Release commit (`<RELEASE_SHA>`)

The CD workflow builds and tags the backend image from the **head of `main`** at dispatch: the tag is the first 12 characters of that commit, and only `main` may deploy to production. So `<RELEASE_SHA>` is `main`'s head on the day. It is acceptable **only if it is code-identical to `4522d29`**. Under Option A (§2a), the release branch points exactly at `4522d29`, so the check is trivially empty. Before dispatch:

```
git fetch origin && git log --oneline 4522d29..origin/main           # every extra commit, named and reviewed
git diff --name-only 4522d29 origin/main -- . ':(exclude)docs'         # MUST print nothing
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

## 2a. Soak isolation, transition and residual risk (owner refinement, 2026-10-26)

### Why §2's rule is not enough

A day-of B3a check proves only that **today's** payload is null-free. Nulls are produced by later transitions:
- a new session's prices loaded before its indicators (the nightly pipeline);
- a new listing without a previous close.

So the old frontend must never depend on the new backend for longer than a short, supervised, daytime switch.

### The new frontend is null-safe (PR #70)

- The market-overview payload is normalised once, where it is fetched.
- Every Overview figure, the movers rows, the heatmap and the "Sectors today" tile render null, missing or partially loaded values as "—". They never crash or show a default.
- Regression tests cover null, missing blocks and fields, partially loaded and garbage values, and are mutation-checked.

PR #70 is merged as `4522d29` (§0), which is `<RELEASE_SHA>`.

### Option A (recommended): build once, soak in isolation, promote the same artifact

Public traffic never sees an un-soaked backend, and the deployed image is byte-for-byte the soaked one.

1. **Pin the artifact, not `main`.**
   - Create `release/product-<date>` pointing exactly at `<RELEASE_SHA>`.
   - Dispatch `cd.yml` (`service=backend`) **on that branch**. `build-and-push` runs, re-running CI's backend slice, and pushes `wolfx_alpha-backend:<RELEASE_SHA[:12]>`.
   - The `deploy` job is refused by the `production` environment, whose branch policy is `main` only (verified 2026-10-26). This is how the Stage 2 images were built.
   - Record the image digest. **Everything downstream uses this tag and digest, never `main`'s tip.**
2. **Isolated backend soak (30 min, no public traffic).**
   - On the VPS, run the image as a separate container `fl-release-soak`. The exact controls and their evidence are in §2b. **Do not copy `fl-preview-backend`'s environment**: it carries the Alpaca keys, the production `JWT_SECRET` and a writable DB login.
   - Run gates B1–B6 (§3) against it, in-container (`docker exec -i fl-release-soak python -`, TestClient with the auth dependency overridden, as in the PR verifications). Re-run B1, B4 and B6 at 30 minutes.
   - The live backend and frontend are untouched throughout. Remove the container afterwards.
3. **Stage the frontend, without switching the domain.**
   - From `git archive <RELEASE_SHA> frontend`, run `vercel build --prod`, then `vercel deploy --prebuilt --prod --skip-domain`.
   - The result is a production deployment that is **not** aliased to `dashboard.first-light.finance`. Record its id. Smoke-check its URL as far as CORS allows: static pages and bundle load.
4. **Switch (supervised, daytime, about 2 minutes).**
   - Deliver the `<RELEASE_SHA>` bundle (`docker-compose.yml`, `docker-compose.prod.yml`, `docker/Caddyfile.prod` from `git archive`) to `releases/<RELEASE_SHA[:12]>/`.
   - Run the documented operator path, `EXPECTED_DIGEST=<soaked digest> /opt/donchian/scripts/deploy.sh backend <RELEASE_SHA[:12]>`. It is health-gated, verifies the digest (PR #72), auto-restores on failure and keeps the pointers (§2b).
   - **Immediately** run `vercel promote <staged deployment id>`.
5. **Public gates.** Run F1, then F2 on the promoted build. Run B3/B4 once more on the public backend.

**Residual risk (Option A):** the old frontend runs on the new backend for about 1–2 minutes, between `deploy.sh` turning healthy and `vercel promote`, in a daytime window with no price-loading job scheduled. A null can only appear if a price load happens in those minutes. None is scheduled (§1.3), and the movers and overview caches change only when a new price date is stored.

**Deviation to approve:** this uses `deploy.sh` directly (the documented manual path) instead of the CD `deploy` job. The reason: the CD job always deploys a **fresh** build of `main`'s tip, which is not the soaked artifact.

**Option A is the approved approach (owner, 2026-10-26). Option B is not an approved fallback.** If Option A cannot run safely, the release waits.

### Option B: no isolation (CD on `main`) — NOT approved, kept for the record

1. **Merge freeze** on `main` for the release (no merges until the release is done or rolled back).
2. `git rev-parse origin/main` = `<RELEASE_SHA>`; the §0 identity check passes.
3. Dispatch `cd.yml --ref main`.
4. **Immediately** confirm the run's `headSha` = `<RELEASE_SHA>` (`gh run list --workflow cd.yml -L 1 --json headSha`). If it differs, **cancel the run before its `deploy` job starts**; the build takes several minutes.
5. After the deploy, confirm the running tag = `<RELEASE_SHA[:12]>`.
6. **Soak with public traffic on the old frontend** (30 min), then deploy the frontend.

**Residual risk (Option B):** the old frontend runs on the new backend for the whole ~30-minute soak. It is protected only by the B3a check and the absence of a price load in the window. The payload is static between price loads (§1.3), so the practical risk is low, but it is **not** structurally excluded as in Option A.

### Rollback rule (unchanged, both options)

- If the frontend returns to `bezlzpzie`, the backend returns to `b8b706d33bab` as well, **before the 23:45 Jerusalem pipeline**.
- Order: frontend first, then backend.
- The new frontend is never left on the old backend, and the old frontend is never left on the new backend across a pipeline run.

## 2b. Option A safety evidence (verified 2026-10-26, read-only)

**Verdict: Option A must NOT be executed yet.** Two safeguards the owner required are missing today:
- a **read-only DB login** for the soak (§2b.1, item 2);
- a **digest-exact promotion** (§2b.2, item 1).

Both now have a prepared correction (§2b.4); neither is installed or created in production. Nothing below has been changed in production.

### 2b.1 Soak isolation

| # | Requirement | Evidence (2026-10-26) | Status |
|---|---|---|---|
| 1 | **Not publicly reachable** | The precedent `fl-preview-backend` publishes only `127.0.0.1:8100`. `docker inspect` shows network `donchian-screener_app_net` with **no aliases**. Caddy routes to the service name `backend` only. The soak copies this: `-p 127.0.0.1:8101:8000`, no `--network-alias`. | Met by design. **Verify at run time**: `ss -ltn` shows `127.0.0.1:8101` only, and `https://api.first-light.finance` still answers from `donchian-screener-backend-1`. |
| 2 | **Read-only DB credentials** | `pg_roles` has no read-only login. The login roles are `donchian_app` (writable), `forward_research_admin` and `trading_user` (superuser). `PGOPTIONS=-c default_transaction_read_only=on` is a session default that the client can override, so it is not an enforcement boundary. The precedent's container uses a writable login. | **GAP, correction prepared and tested** (§2b.4 a); role created only after Stage 2. |
| 3 | **No workers, schedulers, collectors or jobs** | The backend has no scheduler or worker. The only background activity is the lifespan's `_main_page_prewarm_loop` thread (`backend/main.py`): SELECTs that fill the container's own in-process cache, and it stops with the container. No systemd unit or timer references a soak container. | Audited and accepted by the owner (§2b.4 c). |
| 4 | **Cannot send notifications or other side effects** | Telegram (`bot_access_service`) needs `TELEGRAM_BOT_TOKEN`; email needs `SMTP_HOST`; the news and market-calendar fetches need `ALPACA_API_KEY/SECRET`. **None is given to the soak.** Without them: no Telegram call; logins fail at the DB (no write grant) before any email; Alpaca calls fail and fall back. The precedent's environment **does** carry the Alpaca keys and the production `JWT_SECRET`, so it must not be copied. | Met by the env file below. |
| 5 | **No mutation of shared cache or production state** | There is no Redis or shared cache: all backend caches are in-process. GET paths that would write are the news cache (`news_items`, `news_fetched`), login/session rows (`auth/store.py`), and the market-calendar cache file. With a SELECT-only role the DB writes fail in Postgres. The cache file lands in the container's own filesystem because **no volume is mounted**. | Met **once item 2 is fixed**. Run-time check: `has_table_privilege('fl_release_soak', t, 'INSERT')` is false for every table. |
| 6 | **Fully removable** | No volumes, `--restart no`, no network alias, no systemd unit. Removal: `docker rm -f fl-release-soak`; delete the root-only env file; `DROP OWNED BY fl_release_soak; DROP ROLE fl_release_soak;`. Then verify that `docker ps -a` and `pg_roles` no longer list either. | Met. |

**Soak container (template; nothing here is run yet):**

```
# /root/fl-release-soak.env (mode 600, root only; deleted after the soak). Variable names only, no values in docs:
#   DB_HOST=postgres DB_PORT DB_NAME=trading_production DB_USER=fl_release_soak DB_PASSWORD=<soak role password>
#   JWT_SECRET=<fresh random value, used only by this container, discarded>   ENVIRONMENT=release-soak
#   PGOPTIONS=-c default_transaction_read_only=on   PROD_SENDING_ENABLED=false   DB_POOL_MAX=4
#   NOT set: TELEGRAM_*, SMTP_*, ALPACA_*, TIINGO_*, *_DB_USER/_PASSWORD of other services
docker run -d --name fl-release-soak --network donchian-screener_app_net -p 127.0.0.1:8101:8000 \
  --restart no --env-file /root/fl-release-soak.env \
  ghcr.io/danielbugi/wolfx_alpha-backend@<soaked digest>
```

The container runs the image **by digest**, so the soaked bytes are pinned from the first minute.

### 2b.2 Promotion

| # | Requirement | Evidence | Status |
|---|---|---|---|
| 1 | **Deploys the exact soaked digest** | `deploy.sh` runs `dc pull backend` by **tag** and checks only that the running container uses that tag name. A tag re-pushed between the soak and the switch would deploy different bytes, unnoticed. | **GAP, correction in PR #72** (§2b.4 b): pre-switch and post-switch digest checks; installed only after Stage 2. |
| 2 | **No rebuild from moving `main`** | `deploy.sh` never builds; it pulls a tag. The tag is built once, on `release/product-<date>` at `4522d29`. The CD `deploy` job is not used. | Met. |
| 3 | **Same health-gated checks as CD** | The CD deploy job pipes the bundle to the forced command `ci-entry.sh`, which runs `deploy.sh backend <tag>`. The manual path runs that same `deploy.sh`. On 2026-10-26 the installed `deploy.sh` (`626600d2…`), `ci-entry.sh` (`f4887bd5…`) and `rollback.sh` (`23d91c0d…`) were sha256-identical to the repo at `4522d29`. The one difference is the bundle: CD's comes from the checkout, the manual one from `git archive 4522d29`. Its three files must match the blobs in §0 before the run, because `deploy.sh` also reloads Caddy from that bundle. | Met (with the bundle blob check). |
| 4 | **Rollback-pointer correctness** | `test_deploy_rollback_pointer.py` covers pointer movement on success, failure, rollback, a failed `PREVIOUS_GOOD`, and refusal of a `FAILED_SHAS` tag: 18 tests on `main`, 22 with #72, **22 passed** on 2026-10-26. Live pointers: `CURRENT_SHA=b8b706d33bab`, `PREVIOUS_GOOD_SHA=c6827ff0ff76`, `KNOWN_GOOD_SHAS=57f2ab6b864d 662cb38593ed c6827ff0ff76`, `FAILED_SHAS=190349e8a3db`. Expected after success: `CURRENT_SHA=<new>`, `PREVIOUS_GOOD_SHA=b8b706d33bab`, and `b8b706d33bab` appended to `KNOWN_GOOD_SHAS`. | Met. Verify after the switch. |
| 5 | **Automatic rollback on failure** | On a failed health gate (or, with #72, a digest mismatch), `deploy.sh` records the tag in `FAILED_SHAS` and restores `PREVIOUS` (tested, including "restored previous good backend" with no "ALSO failed"). | Met. |
| 6 | **A known-good previous release is preserved** | Bundles `releases/b8b706d33bab/` and `releases/c6827ff0ff76/` are present and both images cached (§0 Targets). The bundle pruning keeps `CURRENT`, `PREVIOUS_GOOD` and the 3 newest `KNOWN_GOOD`, so after the switch both rollback lines survive. | Met. |

### 2b.3 Frontend cutover evidence (candidate `4522d29`)

- A local production build of the `4522d29` frontend was replayed in Chrome against responses produced by the **candidate backend code**. That code ran in memory inside the live backend container: an import hook served the `4522d29` backend sources; the DB session was read-only; no lifespan, so no prewarm thread. Nothing was written to the container.
- All 44 captured endpoints answered 200.
- **37 / 37 checks passed** in two scenarios:
  - the real session 2026-10-23;
  - the same payload with the nulls a partly loaded session produces (null averages and counts, a mover without price or change, an empty unusual-volume list, a sector without a change).
- Checks covered: no page error; no NaN or "undefined"; no horizontal overflow (laptop, and phone 390 px); the session is stated on the activity and movers cards; unknown values show "—"; the empty unusual-volume list says why; AIV shows "Previously reported: Nov 5 — not confirmed by latest check".
- Pages: Overview, stock (unverified and confirmed), Earnings, Watchlist, Screener.
- **Staging without touching the domain:** `vercel build --prod` and `vercel deploy --prebuilt --prod --skip-domain` (§2a step 3). The public alias stays on `bezlzpzie` until `vercel promote`.
- **Incompatibility window:** only between `deploy.sh` turning healthy and `vercel promote`, about 1–2 minutes, supervised, daytime, no price load scheduled. The new frontend is null-safe in both directions.
- **Combined rollback unchanged (§5):** frontend first, then backend, before 23:45 Jerusalem.

### 2b.4 Owner decisions of 2026-10-27, and the evidence behind them

Owner decisions: the read-only role is **approved for preparation and for setup after Stage 2, never before the cutover**; the cache warmer **may stay enabled if proven read-only and side-effect-free**; PR #72 is a **conditional GO to merge** once the safeguards below are verified; product deployment is **not approved**.

**a. Temporary read-only role `fl_release_soak`: prepared, tested on a throwaway database, NOT created in production.**

Files (`deploy/db/soak/`): `release_soak_role_setup.sql`, `release_soak_role_verify.sql`, `release_soak_role_teardown.sql`. The password is a psql variable (`-v soak_pw=…`), never in a file.

Privilege design (`NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS`, `CONNECTION LIMIT 6`, member of no role, owns nothing):
- `GRANT CONNECT`, `USAGE` on `public`, `SELECT` on all tables and views;
- **`REVOKE ALL` on 17 credential, member and message tables** the data endpoints never read: `dashboard_users`, `dashboard_sessions`, `dashboard_login_challenges`, `dashboard_auth_audit`, `bot_access`, `bot_audit`, `bot_chart_cache`, `bot_invites`, `bot_requests`, `bot_settings`, `bot_tracked`, `bot_user_settings`, `bot_users`, `bot_watchlist`, `telegram_messages`, `telegram_control_audit`, `telegram_post_delivery`;
- no INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER, CREATE, sequence, function or default-privilege grant;
- `default_transaction_read_only=on`, `statement_timeout=60s` and an idle-in-transaction timeout as second layers only.

Production facts that make the design sufficient (read-only queries, 2026-10-27):
- No table has a PUBLIC grant, no row-level security, no default ACLs, one extension (`plpgsql`).
- The five SECURITY DEFINER functions that take arguments (`research_maintenance_open/close/approve/begin`, `research_capture_set_state`) are executable only by their owner and `donchian_research_admin`. `research_guard_immutable` is a trigger function. So PUBLIC, and therefore the soak role, cannot reach a function that writes as its owner.
- There is no privileged alternative login the soak could use: its env file holds only this role's credentials. `trading_user` (superuser), `donchian_app`, `donchian_owner` and the research logins are never given to it.
- The soak also receives no production secret: a fresh random `JWT_SECRET` and no Telegram, SMTP, Alpaca or Tiingo values (§2b.1).

Test on a throwaway Postgres 16 cluster (a separate port, deleted afterwards; the frozen Windows database was not touched), with a mirror of the schema: owners `trading_user` and `donchian_owner`, the credential tables, and a restricted SECURITY DEFINER function:
- Catalog audit (`release_soak_role_verify.sql`): **9 / 9 PASS**, including "no write privilege on any table, via the role or PUBLIC", no CREATE on the schema or database, no privilege on the sensitive tables, no EXECUTE on any SECURITY DEFINER function with arguments.
- Behaviour **as the role, with the session read-only default deliberately switched off**: INSERT, UPDATE, DELETE, TRUNCATE, CREATE TABLE, ALTER TABLE, DROP TABLE, CREATE INDEX, SELECT on `dashboard_users`, SELECT on `bot_users`, the definer function, `GRANT … TO PUBLIC` (no privileges granted) and CREATE ROLE **all failed** ("permission denied" / "must be owner" / "no privileges were granted"). Reads of `stock_prices` and a view succeeded.
- Teardown (`DROP OWNED BY`, `DROP ROLE`): zero roles left.
- **Known, harmless:** PUBLIC may create session-local TEMP tables. They vanish at disconnect and are visible to no other session.

In production, after Stage 2 is COMPLETE and with the owner present: run the setup, then the verify script (every line must be PASS and every probe an ERROR), then start the soak. After the soak: the teardown script, delete the env file, and confirm `pg_roles` and `docker ps -a` no longer list the role or the container.

**b. PR #72 (`EXPECTED_DIGEST` in `deploy.sh`), updated for the two required checks.**
- Optional and operator-only (`ci-entry.sh` cannot set it), validated as `sha256:<64 hex>`.
- **Before the switch:** the tag is pulled and its digest must equal the expected one. A missing image, an image with no digest, or a different digest aborts with exit 1. **Nothing is changed**: no compose file is activated, the running release is untouched, and no pointer or `FAILED_SHAS` entry is written.
- **After the switch:** the digest of the image the running container was created from must equal it too (this covers a tag re-pushed between the two checks). A mismatch counts as a failed rollout and triggers the established health-gated restore of the previous release; the replaced tag is recorded in `FAILED_SHAS`, and the previous release keeps its place as `PREVIOUS_GOOD_SHA` / known-good.
- Tests (`test_deploy_rollback_pointer.py`, 27 passed): correct digest; mismatch before the switch (production untouched); missing image; image with no digest; mismatch after the switch (restore); correct digest with a failed health check (restore); pointer integrity through a digest-verified deploy and a later rollback. Both checks were mutation-tested: disabling the pre-switch block fails 3 tests, disabling the post-switch block fails 1.
- No new deployment path: it is the same `deploy.sh`, and the variable is read only from the operator's environment.
- Install on the VPS **only after Stage 2 is COMPLETE**, with owner approval: the installed copy's sha256 must equal the repo file.

**c. Cache warmer: audited, side-effect-free.** Call path: `lifespan` → `_main_page_prewarm_loop` → `_build_main_page_data` → (`_screener_movers` → `DiscoveryService.movers`) plus one summary query → `set_cached_data`.
- **Database:** the engine runs every statement after `SET TRANSACTION READ ONLY` (`DiscoveryService._run`), enforced by Postgres. The summary query is a plain SELECT. Under the soak role no write is possible anyway.
- **Cache:** `cache` and `cache_timestamps` are module-level dicts inside the process. There is no Redis or other shared store, and no cache file written on this path.
- **External effects:** no HTTP client, no Telegram, no SMTP, no Alpaca call on this path.
- **Other background activity:** the warmer is the only thread the lifespan starts. There are no schedulers or workers in the backend.
- **Run-time proof (2026-10-27, candidate code run in memory inside the live backend container, read-only session, nothing written):** one full `_build_main_page_data()` built the cache (session 2026-10-26). The only statement type issued through the app's cursors was SELECT, no outbound socket was opened, and no new thread was started.
- **Verdict:** it stays enabled in the soak.

### 2b.5 Remaining blockers before any product deployment

1. Stage 2 COMPLETE: the Tuesday 2026-10-27 cutover succeeds, the first 23:45 Jerusalem pipeline run is checked and passes, and the final report says COMPLETE. PARTIAL or ROLLED BACK holds the product release.
2. PR #72 reviewed, CI green, merged (conditional GO given); installed on the VPS after Stage 2, sha256 matching the repo.
3. The read-only role created after Stage 2 and verified (§2b.4 a).
4. Code release candidate re-verified on the day (§0).
5. The isolated 30-minute soak passes; the frontend candidate is staged without changing the public domain.
6. A **separate explicit owner GO** to switch the production backend and frontend (window Wed 2026-10-28, 13:00–15:00 Jerusalem, §1).

## 3. Backend release

**Run (only after the §0 check and once the §2b.5 blockers are cleared):** Option A (§2a, §2b). Option B is not approved. `deploy.sh` rolls back automatically on a failed health gate (and, with #72, on a digest mismatch), and keeps the pointers.

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
