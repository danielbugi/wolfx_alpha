# Lab Slice 7 — Collector readiness and forward-observation design

Status: lab branch `lab/first-light-algo`. **Nothing here is deployed, scheduled, installed or run against production.** Slice 7 adds no migration,
no timer, no unit under `deploy/`, no change to the screener, the pipeline, the status contract or any Slice 3–6 writer/reader. It adds one
dormant package (`mechanism/forward_collection/`), its tests, scheduler *drafts* under `docs/research/scheduler_drafts/*.proposed`, and three
test-guard amendments that keep the package activation-neutral.

> **Collector-ready ≠ research-ready ≠ model-ready ≠ predictive edge.**
> Slice 7 proves the first (and, in a disposable world, that the second becomes reachable by elapsed time alone). It makes **no** claim about the
> last two and contains no training, fitting, feature selection, tuning or optimisation of any kind.

## The question this slice answers

*If we later activate the complete collector stack exactly as designed here and nothing operational fails, will forward history converge toward the
Slice 5 research-readiness contract — without reconstruction or backfill?*

Proof: `tests/test_convergence_db.py` simulates a disposable world from **zero** trustworthy history, session by session on a simulated clock,
through the **real** writers only (the screener's candidate-capture hook, the Market Intelligence runner, the fwd_v1 label runner). Nothing inserts
an "ideal" row; the only things written directly are what the production price/fundamentals updaters write (bars, index prices, fundamentals).
After 262 simulated sessions the Slice 6 status reports `data_checks_all_pass = true`. The same simulation with symbols that have no sector **also**
passes since Slice 8 (owner decision: Option B, see "No-sector policy"); before Slice 8 it did not.

> **Slice 8 update:** the no-sector policy is resolved (Option B). Details, the changed identities and the sector provenance chain are in
> [LAB_SLICE8_NO_SECTOR_SEMANTICS.md](LAB_SLICE8_NO_SECTOR_SEMANTICS.md). Where this document describes the policy as unresolved, read it as the
> historical Slice 7 state.

## What was added

| Path | Role | Kind |
|---|---|---|
| `mechanism/forward_collection/contract.py` | vocabulary (steps / outcomes / verdicts / exit codes), the per-source collector contract, the pure session classifier, the declared scheduler design, the no-sector policy | pure |
| `mechanism/forward_collection/orchestrator.py` | one explicit session → ordered steps; advisory lock; dependency skipping; deadline refusal; bounded transient retry; verdict | I/O |
| `mechanism/forward_collection/steps.py` | **the only module that imports the existing writers** (MI runner, fwd_v1 label runner); a read-back `verify`; `capture_verify` | I/O |
| `mechanism/forward_collection/preflight.py` | read-only activation preflight, `collector stack ready for activation: YES/NO` | read-only |
| `mechanism/forward_collection/cli.py`, `__main__.py` | `python -m forward_collection run \| verify \| preflight` — dry-run by default; `--apply` needs `--code-ref` | CLI |
| `docs/research/scheduler_drafts/*.proposed` | the scheduler design as unit/timer/wrapper drafts. **Installed by nobody.** | docs |
| `.github/workflows/ci.yml` | adds `mechanism/forward_collection/tests` to the two existing pytest invocations | CI definition |

The package computes nothing. Every research value is still produced by the existing writers; Slice 7 only decides *when*, *in what order*, *with
what dependency rules* and *how a session is declared complete*.

## Collector architecture

### Source → writer map (the contract, `contract.SOURCE_CONTRACT`)

| Dataset source | Writer | Orchestrated here | Catches up after a missed run? |
|---|---|---|---|
| candidates | the screener's capture hook (inside the daily pipeline, **unchanged**) | verified only (`capture_verify`) | **no** — exists only inside its own session's screener run |
| market, breadth, sector, stock_rs | `market_intelligence` runner `run(provenance="observed", with_stock_rs=True)` — one transaction | `observe` | **no** — an observed row requires the session to be the newest loaded bar |
| labels | `research.labels.runner.run` (fwd_v1) | `labels` | **yes** — a label depends only on bars up to its own horizon session |
| catalyst, first_seen | **none exists** | — | — |

**Candidate capture is integrated, not rewritten.** The screener already captures candidates at their session; `capture_verify` reads
`candidate_capture_run` (and counts candidate rows without a sector) and never creates anything. Whether capture runs at all is the existing
separately-approved `RESEARCH_CAPTURE_ENABLED` + activation-row mechanism; the package never names or flips it (a guard test enforces that).

**Catalyst / first-seen policy (explicit).** No collector exists for either. They are *optional only by being absent from the dataset spec*. A spec
that enables either one is an **activation blocker** (`dataset_sources_have_collectors`), never silently treated as optional: it could never
accumulate history. The status contract (Slice 6) already reports these sources as having no collector; Slice 7 does not change that.

### Dependency / order graph

```
 (screener, 23:45 / 01:00 Asia/Jerusalem — unchanged)         (this collector, 03:15 and 08:15 Asia/Jerusalem the day after)
   price updaters ──► candidate capture ──┐
                                          ▼
                                 capture_verify (read-only, only with --with-capture-check)
                                          │
                                          ▼
                    observe  ─────────────┼──────────►  verify   (read-back; depends on observe)
   (market+sector+stock RS, atomic)       │                ▲
                                          ▼                │
                                       labels ─────────────┘  (independent of observe; catches up by itself)
```

`DEPENDS_ON = {verify: (observe,), labels: (), observe: ()}`. A step whose dependency did not succeed is recorded `skipped_dependency` — it never
reports success because another step was absent, and `labels` still runs when `observe` failed (labels do not depend on market context).

### Exact per-session flow (`orchestrator.run_session`)

1. Take a Postgres advisory lock (`LOCK_KEY`). Held elsewhere → verdict `LOCKED`, nothing attempted (exit 3, not an alert).
2. `observe`: **refused** when `clock() >= decision_deadline(session, grace)`; **failed** (`session_is_not_the_newest_loaded_session`) when a newer
   session's bars are already stored; otherwise one MI-runner call, which writes market + sector + stock RS in a single transaction or nothing.
3. `labels`: one label-runner call for the session as the explicit "latest completed" session; writes every label whose horizon has matured.
4. `verify`: reads the rows back and checks they exist, are complete (universe/market/sector/RS counts), were stamped before the decision deadline,
   carry no unknown/reconstructed provenance in place of the observed row, and reports RS `ok` cells without a PIT-safe sector.
5. Classify. `COMPLETE` needs **every** required step to have succeeded; a failed/refused/skipped/absent step can never be `COMPLETE` and a dry run is
   never `COMPLETE`. A re-run after the deadline of a session collected on time is `COMPLETE` through the read-back (`already_present`), not `MISSED`.

Verdicts and exit codes: `COMPLETE`/`DRY_RUN` 0 · `INCOMPLETE` 2 · `LOCKED` 3 · `MISSED` 4 · refused (no trustworthy calendar, bad arguments) 5. Alert
codes: 2, 4, 5.

## Idempotency, retry, partial failure, crash

| Situation | Behaviour (all pinned by tests) |
|---|---|
| duplicate invocation / second fire of the day | MI runner and label runner are `ON CONFLICT DO NOTHING`; step outcomes `already_present`; row counts unchanged |
| two processes at once | the second sees the advisory lock → `LOCKED`, writes nothing |
| transient connection error | bounded in-process retry (3) on `OperationalError`/`InterfaceError` only; any other error is a failed step, not retried |
| crash / error mid-`observe` | one transaction: nothing partial exists; the next run re-computes the same content hash |
| partial session (e.g. RS insert fails) | the whole observation rolls back; verdict `INCOMPLETE`; a re-run before the deadline repairs it |
| labels fail, observe ok | `INCOMPLETE` (labels missing); next run catches up; observed rows untouched |
| observe past the decision deadline | **refused**, never written late; the status keeps the gap visible (`MISSED` if nothing was collected on time) |
| a newer session's bars already loaded | observe **fails** `session_is_not_the_newest_loaded_session` → `MISSED` (never backfilled as observed) |
| reconstructed rows present for the session | never satisfy `verify`; reported as `reconstructed_rows_ignored`; the observed row is still required |
| off-calendar / future / in-progress / weekday-fallback calendar | **refused** (exit 5) before any write — the CLI fails closed |
| restated vendor bar after a label was written | the label is **never rewritten**; the existing read-only `restatement_report` surfaces it (see limitations) |

**No collector fabricates success because another dependency is absent.** `verify` is a read-back of the database, not a restatement of what the
previous steps claimed.

## Labels: maturity, catch-up, identity

Label identity is `(observation_id, horizon_sessions, label_version)`; a label row is immutable. The runner writes a label only when its horizon
session has completed on the explicit calendar, so no future information reaches an earlier cutoff (`computed_as_of_session >= horizon_session`,
pinned over the whole simulated history; zero violations). Missed runs need no special handling: the next run evaluates every unlabelled pair whose
horizon has matured, and `test_labels_catch_up_after_missed_runs_and_equal_the_continuous_world` proves a world with skipped runs ends with
**identical labels, values and input hashes** to the continuously collected world (only the honest `computed_as_of_session` differs). Maturity
boundaries are tested explicitly: the session before maturity yields no label; the maturity session yields exactly one.

## Simulated clock (test scaffolding only)

`lab_sim_now()` reads the GUC `lab.sim_now`; inside the **disposable schema only**, the availability-stamp column defaults and the RS stamp trigger
point at it. Every connection the stack opens sees the world's current simulated instant, so a session's capture is stamped at its evening and its
collection the next morning, as the scheduler design says. Production defaults (`NOW()` / `clock_timestamp()`) are untouched.

## Scheduler design (drafts only — `docs/research/scheduler_drafts/`)

| Item | Design |
|---|---|
| Command | `python -m forward_collection run --latest-completed --apply --code-ref <mechanism image tag>` (via `run_forward_collection.sh.proposed`: `docker compose run --rm` at `CURRENT_MECHANISM_SHA`, `PYTHONPATH=/app/mechanism`) |
| Timing / timezone | 03:15 and 08:15 `Asia/Jerusalem`, every day (the latest completed session is what is collected; on non-session days the first fire is a verified no-op) |
| Why 03:15 (SUPERSEDED: the first fire is now 04:00, see `deploy/vps/donchian-forward-collection.timer`; the 14-day earnings re-fetch night ended 03:13:43) | the 01:00 retry of the 23:45 pipeline finishes ~02:30 (87-minute run); the evaluator is 03:30; margin ≥ 30 min. Both fires are **before** the decision deadline of the session (grace 1 ⇒ deadline = 00:00 UTC of session+2) |
| Ordering | after the screener's price load + capture (hard requirement: observe needs the session's own bars as the newest bar) |
| Lock | `flock` in the wrapper + the Postgres advisory lock in the orchestrator |
| Timeout | 45 minutes (`TimeoutStartSec`) |
| Retry | in-process transient-connection retry only; otherwise the 08:15 fire is the retry |
| Catch-up | labels catch up on any later run; an unobserved session is never backfilled as observed; `Persistent=true` for missed timer fires |
| Logging | one JSON `SessionReport` line per run to journald + the wrapper log; no secrets |
| Health verification | `python -m forward_collection verify --latest-completed` (read-only, exit 0/2/4) and the Slice 6 `dataset_cli status` |
| Failure alert | `OnFailure=donchian-forward-collection-alert.service` for exit 2/4/5; exit 3 alone is not an alert (`SuccessExitStatus=3`) |

`contract.validate_design` pins these numbers against each other and against the spec's `availability_grace_days` (`required_grace_days() == 1`);
the preflight turns an inconsistency into a blocker. The drafts live under `docs/research/`, not `deploy/`, so no deploy path references the package
(a guard test asserts it).

## Activation preflight (read-only)

`python -m forward_collection preflight --spec <authoring spec>` → `collector stack ready for activation: YES|NO` (schema `forward_collection_preflight_v2`;
the Slice 7 `ready_ignoring_owner_decision` / `owner_decision` fields are gone because no owner decision is pending any more). Checks: required tables and the RS stamp function exist ·
the runtime role holds INSERT+SELECT on the five collector tables and SELECT on the inputs · a trading calendar is derivable from stored bars ·
capture state is *reported* (non-blocking) · every enabled dataset source has a collector · the spec's versions equal what the writers write ·
the scheduler design is self-consistent and agrees with the spec's grace days · **the no-sector policy is resolved** (a recorded fact: Option B).

In a healthy schema it answers **YES** (it answered NO in Slice 7 with `no_sector_policy_resolved` as the only blocker).

## No-sector policy — RESOLVED in Slice 8: Option B (the Slice 7 analysis follows, unchanged)

A symbol with no point-in-time-evidenced sector gets an RS row with `state='ok'`, `sector NULL`, `sector_pit_safe=false` (the writer's existing
never-omit rule). Slice 5 check 6 (`relative_strength_sector_pit_safe`) counts such a cell against readiness, so a candidate symbol without a sector
blocks `data_checks_all_pass` for any dataset window containing it. Measured: `tests/test_convergence_no_sector_db.py` (same 262-session world, three
symbols without a sector) → every session still collects `COMPLETE`, the Slice 5 data checks do **not** all pass, the failing check is
`relative_strength_sector_pit_safe`.

| Option | Effect | Assessment |
|---|---|---|
| **A** exclude the symbol | drops it from RS rows | **Rejected.** Violates the writer's never-omit rule (an omitted row is indistinguishable from a stock outside the universe) and silently changes the universe and every coverage figure. |
| **B** keep the symbol; its sector-relative cell is *unavailable* | the writer already stores it this way; the dataset assembler/audit would stop counting that cell against the sector-PIT check because no sector value was ever claimed | **Recommended — but changes Slice 4/5 assembly semantics** (and therefore the dataset hash of any dataset containing such a cell). Requires an owner decision and a deliberate, separately reviewed Slice 4/5 change. |
| **C** market-relative fallback | substitutes a market-relative value | **Rejected.** Changes the meaning of an existing feature and mixes two definitions in one column. |

Slice 7 did not choose (`NO_SECTOR_POLICY = "unresolved"`). The owner then chose **Option B**, and Slice 8 made the reviewed Slice 4/5 change:
`NO_SECTOR_POLICY = "B_null_sector_relative"`; the verify step now fails only a sector-relative VALUE that lacks a PIT-safe sector
(`rs_sector_relative_values_without_pit_safe_sector`); a no-sector symbol is a legitimate row whose sector-relative value is NULL. The collector still
*reports* the count (`capture_verify.candidate_rows_without_sector`, `verify.rs_ok_cells_without_sector`, the status's
`stock_rs_ok_cells_without_sector`, now severity `info`).

## Convergence proof

`tests/test_convergence_db.py` (real writers, simulated clock, scaled universe minimums — see limitations):

* starts with every collector table empty;
* runs 262 sessions, each: bars load → candidate capture at the session's evening → the orchestrator at the scheduled first fire;
* every session is `COMPLETE`; observed market/universe rows = 262; **zero** reconstructed/unknown rows in any table;
* Slice 6 status at session 61 and 121: `jointly_observed_sessions` = 61 / 121, contract not passing, reason stated;
* labels exist for exactly the (candidate, horizon) pairs whose horizon has matured — none early, none duplicated;
* at the end, `contract.evaluated = true`, `data_checks_all_pass = true`, an authoritative earliest-trustworthy-PIT date exists, no
  `slice5_data_check` failure remains;
* scanning earlier cutoffs over the final database (every row carries its own availability stamp, so an earlier cutoff reproduces what a live reader
  would have seen) shows readiness is **monotonic**: once it passes it never regresses;
* re-running the whole stack on the last session changes no row count; the status hash is deterministic.

**Sessions needed before the data-derived checks pass in the seeded world: 262 sessions (the first cutoff at which readiness passes in the scan is session 262, i.e. index 261; pinned by `test_scanning_the_cutoffs_shows_readiness_is_monotonic_and_first_passes_at_the_designed_floor`).** This is an intrinsic floor of the Slice 5/6 spec, not
a collector artefact: train window + 60-session embargo + validation + 60 + test + the 63-session label-maturity margin ≈ 262 sessions
(≈ one trading year), plus enough candidates per window (`MIN_FINAL_LABELS` 300 / 100 / 100). No amount of collector speed shortens it, and no
reconstruction can substitute for it (reconstructed rows never satisfy observed coverage — pinned).

## Activation procedure (a FUTURE, separately approved sequence — none of it is performed by Slice 7)

Each step is its own owner approval; none is implied by a merge.

1. ~~Resolve the no-sector policy~~ (done: Option B, Slice 8). Decide the dataset spec (windows, `availability_grace_days ≥ 1`).
2. Apply the Market Intelligence / research migrations to production (24/25 and the rest the preflight names), forward-safe, each verified by a real
   query. Grant the runtime role INSERT+SELECT per `research_roles.sql`.
3. Deliberately amend the "no deploy unit references the collector" guard (`test_activation_neutral.py`, `test_guards.py`) in the same PR that adds the
   unit — the guard exists to make this a visible, reviewed act.
4. Run `python -m forward_collection preflight` against production, read-only → expect `YES`.
5. Enable candidate capture (existing mechanism, existing approval) **before or together with** the collector — observation without candidates is not
   research history.
6. Install the unit/timer from the drafts (`donchian-forward-collection.{service,timer}`, wrapper, alert unit), committed to `deploy/vps/`.
7. First fire: confirm `COMPLETE` for a real session, then let time accumulate.

## Rollback

`systemctl disable --now donchian-forward-collection.timer`. Everything the stack wrote is append-only and provenance-labelled; nothing needs
deleting and nothing is rewritten. Capture is rolled back by its own existing off-switch. Removing the unit returns the system to today's state; the
accumulated rows remain valid history.

## Post-activation verification

Each morning after the 08:15 fire: `python -m forward_collection verify --latest-completed` exits 0; the Slice 6 `dataset_cli status` shows
`jointly_observed_sessions` rising by one per session, no `*_rows_late`/`*_backdated`/`candidate_run_not_complete` integrity entries, and
`stock_rs_ok_cells_without_sector` at the level the owner's policy expects. Periodically (weekly) run the existing read-only
`research.labels.runner.restatement_report`.

## What remains dormant

Everything. The package is imported by nothing outside itself; no timer, unit, wrapper, compose entry or workflow references it (CI runs only its
tests); no migration is added or applied; capture stays off; `CURRENT_MECHANISM_SHA` is untouched; nothing was run against production.

## Known limitations / technical debt

* **Intrinsic time floor** (~262 sessions) — cannot be shortened; the collector can only avoid losing sessions.
* A **missed observed session is unrecoverable** by design (needs the session's own bars as the newest bar). The collector can only report it.
* **Scaled universe in the long simulation**: the 262-session test runs an 80-stock universe with the writers' 1,000-stock floors lowered (test scale
  only; ~25 minutes at 1,100 stocks). The real floors are exercised at real size by a separate multi-session test.
* **daily_fundamentals freshness is a hard dependency** of the candidate-side sector (the capture reads the latest fundamentals row within 30 days of
  the session) and of the MI sector map. A stale fundamentals updater silently turns candidates into "no sector"; the collector reports it
  (`candidate_rows_without_sector`) but does not own the updater.
* The label step does not auto-run the restatement report (cost: recomputes every final label); it is a documented weekly check.
* A session missed for good keeps alerting (exit 4) at each fire until the next session is collected.
* `labels_no_scheduled_collector` stays a structural note in the Slice 6 status until activation is real — the status is deliberately unchanged here.
* Catalyst and first-seen have no collector (explicit, a blocker only if a spec enables them).
* The `-m forward_collection` resolution inside the production image cannot be proven in the lab (verified at activation).
