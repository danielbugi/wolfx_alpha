# Release B — activation: operator guide and read-only validation tool

> **Status: PREPARED, NOT EXECUTED.** Nothing here has been run against production. Production is frozen at `4d9bf93`
> (`CURRENT_MECHANISM_SHA=4d9bf93069cb`); migrations 22 and 23 are not applied; no `donchian_*` role exists; services run as the
> bootstrap superuser. Every stage that changes production needs its own explicit owner go-ahead (commit ≠ deploy).
>
> The full audit, stage-by-stage actions, STOP conditions and the rollback matrix are in
> `agent_reports/architecture/2026-10-02_release-b-production-activation-runbook.md` (a local, git-ignored working document — not in the repository). This file is the operator-facing summary
> and the reference for the validation tool. Where they differ, **this file wins**.

## 1. What the prerequisites changed

| | Change | Where |
|---|---|---|
| P1 | Migration 23 `mechanism/add_ml_models_registry_columns.sql` adds the 3 `ml_models` columns. `momentum_predictor.py` no longer runs DDL at runtime; it only checks the columns exist and fails loudly if not. | `docker-compose.yml` init list, CI migration floor 23 |
| P2 | The postgres container and the four applications have separate credential chains. Postgres: `POSTGRES_BOOTSTRAP_*` → `DB_*`. Each app: `<SVC>_DB_*` → `APP_DB_*` → `DB_*`. **Never switch a service by editing `DB_USER`.** | [ENVIRONMENT.md](../dev/ENVIRONMENT.md) |
| P3 | `research_roles.sql` covers tables, views, sequences and functions; the verify script proves the runtime role can do the runtime operations and cannot create/alter/drop/truncate/maintain. The script is convergent (re-running undoes drift). | [RESEARCH_DB_ROLES.md](RESEARCH_DB_ROLES.md) |
| P4 | `GUARDS_EFFECTIVE_FROM`: unset/blank = guards **inert**; `YYYY-MM-DD` = guards apply to sessions ≥ that date; malformed = the run fails (never silently unset). The decision is a pure function of (boundary, explicit session). | `mechanism/screeners/guards_boundary.py` |
| P5 | `mechanism/validate_release_b.py` — read-only validation for every stage (§3). | this file |
| P6 | Docs corrected: frontend is deployed **manually** with the Vercel CLI (DEPLOYMENT.md §3, CI_CD.md); `DB_USER` hazard; env variables; this guide. | |

### Two things the owner must accept before S6

1. **The image is not behaviour-neutral.** Production `4d9bf93` has no universe guards. They arrived in `7ea07b1` together with
   ranking by *combined score* and the *ML-unprocessed fallback*. `GUARDS_EFFECTIVE_FROM` gates only the guards; the ranking and
   fallback change takes effect on the first pipeline run on the new image, flag or no flag.
2. **Roles/grants are a point-in-time snapshot.** There are no default privileges. After **any** later migration re-run
   `research_roles.sql` and `research_roles_verify.sql`; a table added by migration 24/25 is uncovered until you do, and any new
   *immutable* table must first be added to the §6 exclusion list of the role script.

## 2. Order (each arrow is a separate owner go-ahead)

push → CI green → `cd.yml service=mechanism` builds image `<A>` (build only, **no** pin change) → S0 re-verify → S1 backup → S2
preflight → S3 apply migration 22 → S4 verify EXACT → S5 apply migration 23 → *(window ends; behaviour unchanged)* → S6 pin `<A>`
→ S7 set `GUARDS_EFFECTIVE_FROM=D` → S8 validate the first guarded session → S9/S9b roles → S10 switch services one at a time →
S11 full scheduled session as `donchian_app` → S12 capture activation → S13 validate the first captured session → S14 backend →
S15 frontend (**manual Vercel deploy**, see DEPLOYMENT.md §3).

Capture needs three independent keys: image `<A>`, `RESEARCH_CAPTURE_ENABLED=1`, and an `enabled` boundary row. Migration 22 alone
does nothing observable.

## 3. The validation tool

Exit codes: **0** no FAIL; **1** at least one FAIL (a runbook STOP condition); **2** usage error or cannot connect. Each check
prints `PASS|FAIL|WARN|INFO <name>: <detail>`. A query error is reported as a FAIL, never a pass.

It cannot write: the session is opened with `default_transaction_read_only=on` and psycopg2 `readonly=True`; every statement must
be a `SELECT`/`WITH`/`SHOW` without write keywords; the transaction is always rolled back. It never prints a secret —
`config --env-file` keeps only `GUARDS_EFFECTIVE_FROM` and `RESEARCH_CAPTURE_ENABLED` and ignores every other line.

**Run it with the bootstrap identity, not the runtime role.** `connections` must see other users' sessions; from a role that
cannot (not superuser, not `pg_read_all_stats`) it reports FAIL `connections.visibility` instead of a false pass. The env file's
`DB_USER`/`DB_PASSWORD` stay the bootstrap pair by design (P2), so on the VPS:

```bash
A=<12-hex activation sha>; ENVF=/opt/donchian/env/.env
VAL() { docker run --rm -i --network donchian-screener_app_net --env-file "$ENVF" -e DB_HOST=postgres \
          --entrypoint python ghcr.io/danielbugi/wolfx_alpha-mechanism:"$A" mechanism/validate_release_b.py "$@"; }
# confirm the network name once with: docker network ls | grep app_net
```

| Stage | Command | What it enforces |
|---|---|---|
| S0 | `VAL schema --expect absent --ml-models absent` · `VAL roles --expect absent` | migration 22 absent (exact fingerprint of nothing), no `donchian_*` role. **A failing import here is a STOP before anything changes** (it proves the image carries the tool and the golden fingerprint). |
| S4 | `VAL schema --expect exact --capture inactive --ml-models absent` | migration 22 EXACT vs the golden fingerprint; all triggers `ENABLE ALWAYS`; row counts 0; no activation row; no capture run; lineage all-or-none. |
| S5 | `VAL schema --expect exact --capture inactive` | the 3 `ml_models` columns now present (the default for `--ml-models`). |
| S7 | `VAL config --env-file $ENVF --expect-guards set --check-boundary-vs-db` | exactly one valid `GUARDS_EFFECTIVE_FROM`; `D` strictly after the latest completed session. |
| S6 / S11 | `VAL config --env-file $ENVF --expect-guards unset` | boundary unset while only the image is deployed. |
| S8 | `journalctl -u donchian-pipeline.service --since "<fire>" \| VAL guards --session D --expect active --log -` (add `--results <path>` to also check `metadata.universe_guards`) | exactly one mode line `ACTIVE` and one `dropped N/M` line; `M` in 2,900–3,100; `N/M` ≤ 15%; today's candidate count within [50%, 200%] of the 5-session median; ledger ≤ results breakouts; no ledger symbol with a price discontinuity in its lookback. |
| S8 | `VAL delivery --session D` | a `prod` post-market row `sent`, no duplicate. |
| S9 | `VAL roles --expect present` | attributes, no membership of owner/admin by the app, all 8 tables + 8 functions owned by `donchian_owner`, runtime coverage of every non-research table/view/sequence/function, the five maintenance/`set_state` functions executable by the admin group only, no CREATE on schema/database. |
| S9b | `VAL roles --expect present` | WARN while the admin group has a single member (the hatch needs two distinct people). |
| S10 | `VAL connections --forbid-user trading_user` | no service still connects as the bootstrap user (psql/pg_dump/the tool itself are allowed). Run after each service switch. |
| S12 | `VAL schema --expect exact --capture active` · `VAL config --env-file $ENVF --expect-capture on` | boundary row enabled; kill switch on; all three keys agree. |
| S13 | `VAL capture --session E` | exactly one run row, `complete`, `hash_drift = 0`, observations = captured + already-captured, `captured + already + stale_skipped = candidates`, linked ledger rows, triggers still `ALWAYS`. |

`--results` needs the screener's `multi_timeframe_ml_enhanced_*.json` for the session; mount it into the container
(`-v <host data dir>:/data:ro`) — confirm the host path at S0, it is not assumed here.

## 4. What is *not* covered by the tool

It validates state; it does not decide. It cannot tell whether `D` was the right date, whether a 12% drop is *plausible* beyond
the fixed thresholds, or that a post's wording is right. Those remain human judgements at the STOP gates.

## 5. Local rehearsal

Everything above was rehearsed on a scratch PostgreSQL 16 with the real roles applied and migrations 19–23 on a production-shaped
schema: `python -m pytest mechanism/research/tests -q` (needs `DB_HOST/DB_PORT/DB_USER/DB_PASSWORD` for a disposable server; the
tests create and drop their own throwaway databases and roles). The tests do **not** run against production and must never be
pointed at it.
