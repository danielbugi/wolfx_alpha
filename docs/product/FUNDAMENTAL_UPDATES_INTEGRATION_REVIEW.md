# Fundamental Updates: integration review before merging PR #101

2026-11-07. **Read-only review: nothing merged, deployed, applied, switched or sent.**

## 1. Migration numbering
| PR | Migration | Status |
|---|---|---|
| main | up to 40 | — |
| #101 Fundamental Updates | **41** `add_fundamental_update_publication.sql` | ready for a merge decision |
| #98 Research Missions | 41 `add_research_mission_tables.sql` | draft; live test still gated |
| #100 CIK lineage | 42 `add_sec_cik_lineage.sql` (stacked on #98) | draft; **Lab only** per the owner |

- **Collision:** #101 and #98 both claim 41.
- **Why it can't be fixed ahead of time:** the registry test (`test_migration_registry.py`) requires contiguous numbers on every branch, so a branch cut from main (which ends at 40) cannot reserve 42 or 43 in advance.
- **Resolution, a merge order:**
  - **#101 keeps 41.**
  - **#98 becomes 42** and **#100 becomes 43**, each renumbered when rebased after #101 lands.
- **Proof:** draft PR #102 (**DO NOT MERGE**) is main + #101 + #98 + #100, renumbered to 41/42/43:
  - all four conflicts resolved (compose mounts, registry, router includes, CI lists);
  - every "migration NN" reference renumbered;
  - full CI applies the sequence to a fresh database and runs every suite.
- **Safety nets:**
  - A naive merge of #98 after #101 conflicts in four files, so git refuses it.
  - Even a forced textual merge would fail the registry test (duplicate 41).

## 2. Merging #101 cannot change production
What #101 changes (31 files): new modules, a new API router, new pages, tests, docs, one compose line and one registry row. None of the files that drive production behaviour change:

| Concern | Evidence |
|---|---|
| Owner alert behaviour | `mechanism/inflection/alerts.py` untouched: same formatter, freshness rule, `ALERT_METHOD_VERSIONS = (inflection_v1,)` and destinations. A test pins the current alert text and asserts `alerts.py` imports none of the new modules. |
| Event source | `config.py`, `updater.py`, `pit.py`, `pipeline.py`, `runner.py` and `store.py` are untouched. |
| Timers / schedules | No `deploy/vps/*` unit, `automation_pipeline.sh` or timer file changed. 9 timers are live on the VPS, unchanged. |
| Delivery destinations | `telegram_client.py`, `post_delivery.py`, `run_bot.py` and `bot_service.py` untouched. Nothing new sends. |
| Running images | CD is `workflow_dispatch` only. The pipeline uses the derived image pinned in the root-owned `CURRENT_MECHANISM_SHA` (`1fc1753cdae0`); the backend is pinned at `ac0eb97f3f0f`; the bot at `575fa5dc0599`. A merge rebuilds none of them. |
| Compose / database | The VPS runs a **copied** compose file (`/opt/donchian/compose`, last changed Nov 2), not a git checkout. Its Postgres container has **no** init-script mounts. A merge cannot run SQL. |
| Frontend | Vercel is deployed manually from an export, not git-connected. |

**What a later, deliberate deploy of main would add:**
- **Backend:** `/api/public/signals`, which answers 503 until migration 41 exists. No data is exposed.
- **Frontend:** `/signals` pages showing "not available right now", and a "Fundamental Updates" navigation item. If main is deployed before migration 41, consider hiding that item.

## 3. Why v1 stopped updating after October 26 (read-only)
> Owner decision 2026-11-07: leave V1 unchanged. Limitation and dependencies: [INFLECTION_V1_REFRESH_CYCLE.md](INFLECTION_V1_REFRESH_CYCLE.md).

**V1 is not broken; it is waiting on its 25-day recheck cycle.**
- `quarterly_fundamentals_updater.py` re-checks a symbol only when its newest row's `updated_at` is more than **25 days** old (`recheck_after_days=25`).
- On **2026-10-13**, **3,055 symbols** were due at once (the batch last checked around Sep 18). All were refreshed that night, and their rows are stamped Oct 13–14.
- Every night since, only about **115 symbols** were due. These are listings with no quarterly statements (preferreds and notes such as AFGB and AFGC). They fail every time: on Nov 6, 115 were attempted and 0 succeeded. The 2026-10-26 "last update" is a single symbol.
- **The universe moves as one batch on a 25-day cycle**, so September quarters reported between Oct 14 and Nov 7 are not fetched until the batch comes due again: **753 symbols on Nov 7 and 2,186 on Nov 8**.
- **What happens next:** at about 3.7 s per symbol, the 2,700 s budget handles roughly 730 symbols a night, so v1 catches up over about **four weeknights (Nov 9–12)**.
  - New September-quarter v1 events then appear.
  - Owner alerts on the new image send only those public within 3 days, so **some genuine owner alerts may arrive from Monday**.
- **Possible fixes** (none made; a separate decision):
  - stagger the recheck across the universe (for example by symbol hash);
  - recheck sooner around each company's earnings date (`earnings_calendar` already exists);
  - stop re-trying listings that never return statements.

## 4. Night selection simulation (v2, five sessions, read-only)
> **Superseded 2026-11-07** by the revised policy (2 per lead type, operational support, non-operating divergence heuristic, 50-point cap) and its re-run: [FUNDAMENTAL_UPDATES_SELECTION.md](FUNDAMENTAL_UPDATES_SELECTION.md). The first run is kept below for comparison.

- **Code:** `mechanism/inflection/update_select.py` (pure), replayed by `update_simulate.py` over a read-only export (`fu_selection_simulation.sql`); 15 tests.
- **Rules:**
  - at most 3 posts a night and 1 per company;
  - SEC-verified date and a known filing accession;
  - point-in-time validity (detected on or after the public date, at most 104 days after quarter end);
  - **public within 3 days of the session** (the backlog guard);
  - market cap at publication of at least $300M;
  - no repeat of a company within 30 days.
- **Ranking:**
  1. preferred families present (net income, operating margin/leverage, revenue);
  2. independent families present (plus free cash flow);
  3. the lead type (net income > operating margin > revenue > operating leverage > FCF);
  4. the size of the lead change;
  5. market cap.

  The ranking is editorial salience, not a forecast.

| Session | New events | Excluded (reason: count) | Eligible companies | Posts | Outranked |
|---|---|---|---|---|---|
| Mon Nov 2 | 2,116 (v2's first run: backlog) | backlog 1,891 · not public type 148 · >104 days 5 · size 4 | 46 | FET, PTEN, CVEO | 43 |
| Tue Nov 3 | 1,011 (coverage expanded 1,866 → 2,831 companies) | backlog 863 · not public type 65 · >104 days 2 · size 1 | 47 | ICHR, LITE, MQ | 44 |
| Wed Nov 4 | 118 | size 4 · backlog 2 · not public type 1 | 67 | VSH, SBGI, MSGE | 64 |
| Thu Nov 5 | 214 | size 9 · not public type 2 · backlog 1 | 116 | AMC, CORZ, BKSY | 113 |
| Fri Nov 6 | 150 | size 12 · explanation inputs missing 1 | 90 | GTN, ETON, SVV | 87 |

- **The backlog guard works.** The two backlog nights (2,754 historical events) produced 3 posts each, all for quarters public within the last 3 days.
- **No verification failures:** no v2 event failed the SEC-date, accession or detected-before-public-date checks.

**Findings that need an owner decision before any sending:**
1. **Net income dominates:** "Net income turned positive" leads **14 of 15** posts. That follows from ranking net income first and from it usually coming with other families. For variety, a per-night type mix (at most 2 of one lead type) would bring in margin and revenue leaders (for example KRYS, REGN, LITE, FORM, CENX).
2. **Extreme net-income swings:**
   - CORZ (+568 points of revenue) and BKSY (+79) are selected; TFX (+235), GLIBA (+155) and NVST (+78) are outranked.
   - Swings this large usually come from one-off items (revaluations, gains), not operations.
   - Proposal: below a cap (for example 50 points), rank by size; above it, keep the event but do not let the swing size raise its rank, unless operating income also improved.
3. **Optics:** ETON is selected on Nov 6 for its September quarter (public Nov 5, correct). It was also one of the 16 misleading owner alerts (its June quarter, re-dated). The two are different quarters, but the name may look familiar to readers.
4. **Not checked:** headline merit and real-world materiality are not assessed by rules. A human glance at the first dev-channel week is advised.

## 5. Shared rate limiting for the public API
> Full technical design, operational impact and rollback: [docs/operations/EDGE_RATE_LIMIT_DESIGN.md](../operations/EDGE_RATE_LIMIT_DESIGN.md). The snippet below needs no `order` line (the module orders itself before `basic_auth`).

**Today:**
- Production runs **one uvicorn process** (`uvicorn main:app`, no `--workers`). The in-app token bucket is therefore already shared by every request to the API.
- Its weaknesses are a reset on backend restart and becoming per-worker if workers are ever added.
- Caddy (`caddy:2-alpine`, v2.11.4) sits in front with a plain `reverse_proxy`. It overwrites any client-sent `X-Forwarded-For` with the real client address, so the backend's per-IP key cannot be chosen by the client.

**Recommended shared solution: rate limit at the edge in Caddy** with the `github.com/mholt/caddy-ratelimit` module, keeping the in-app limiter as a second layer.
- It is enforced before Python runs, shared across any number of backend workers, and adds no new datastore.
- Requirements:
  - a small custom Caddy image built with `xcaddy`, with the module pinned to a commit and the image pinned by digest (one new build input, reviewed like the others);
  - a Caddyfile change on the VPS.

```dockerfile
FROM caddy:2.11.4-builder AS build
RUN xcaddy build v2.11.4 --with github.com/mholt/caddy-ratelimit@<pinned commit>
FROM caddy:2.11.4-alpine
COPY --from=build /usr/bin/caddy /usr/bin/caddy
```
```caddyfile
{
	order rate_limit before reverse_proxy
}
{$API_SITE_ADDRESS} {
	rate_limit {
		zone public_signals {
			match {
				path /api/public/*
			}
			key {remote_host}
			events 120
			window 1m
		}
	}
	# ... existing encode / header / reverse_proxy backend:8000
}
```

**Alternatives considered:**
- **Redis:** a new container plus a client library; more moving parts than needed for one VPS.
- **A PostgreSQL counter table:** shared and durable, but every public read would become a database write and need a new writable table. Wrong trade for a read-only endpoint.
- **Keeping only the in-app bucket:** correct today because there is one process; it becomes wrong the moment workers are added.

## 6. Migration 41: diff, apply and rollback
**The diff against main** (migration-related):
- `mechanism/add_fundamental_update_publication.sql` (new):
  - table `fundamental_update_publication`, with 6 constraints: `UNIQUE (update_key, version)`, version ≥ 1, kind in a closed list, version 1 ⇔ kind 'initial', key pattern, hash pattern;
  - 2 indexes;
  - the trigger function `fundamental_update_publication_append_only()` and 2 `ENABLE ALWAYS` triggers, refusing UPDATE/DELETE and TRUNCATE;
  - the view `fundamental_update_current`;
  - grants to `donchian_app`: SELECT and INSERT on the table, SELECT on the view, USAGE and SELECT on the sequence.
- `docker-compose.yml`: one init mount, `41_add_fundamental_update_publication.sql` (affects only a fresh database; production has none).
- `docs/architecture/MIGRATION_REGISTRY.md`: row 41.

It is additive only. It touches no existing table, and nothing writes to it until a separately approved writer runs.

**Apply (when approved):**
1. Pre-check: `SELECT to_regclass('fundamental_update_publication')` returns NULL.
2. Back up first: the existing nightly backup, or `pg_dump --schema-only` for a schema snapshot.
3. Apply: `docker exec -i donchian-screener-postgres-1 sh -c 'psql -v ON_ERROR_STOP=1 -1 -U "$POSTGRES_USER" -d trading_production' < mechanism/add_fundamental_update_publication.sql`.
4. Verify: `deploy/db/fundamental_update_publication_verify.sql` (read-only) must print `migration 41 verified`. It checks objects, constraints, the ALWAYS triggers, and that `donchian_app` has SELECT/INSERT but no UPDATE/DELETE/TRUNCATE.

**Rollback:** `deploy/db/fundamental_update_publication_rollback.sql`, run as one transaction.
- It **refuses if any publication row exists** unless a backup was taken and `SET fup.confirm_drop_with_rows = 'yes'` is given in the same session.
- It then drops the view, the table (its triggers, indexes and sequence go with it) and the function, and reports `migration 41 removed`.
- `/api/public/signals` answers 503 by itself afterwards.

**Tested on real PostgreSQL** (`test_update_ledger.py`, CI database job):
- applying the migration twice is harmless and keeps the data;
- the verify script passes;
- the rollback refuses with rows, and removes everything when confirmed (run inside a transaction, then reverted).
