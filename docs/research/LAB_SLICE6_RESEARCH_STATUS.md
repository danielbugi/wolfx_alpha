# Lab Slice 6 — Real-data observation status and research readiness

Status: lab branch `lab/first-light-algo`. Operational research tooling only. Nothing here is deployed, scheduled, or run against production, and
Slice 6 adds **no migration**, no writer, no timer and no model code.

> **Reproducible ≠ point-in-time correct ≠ vendor correct ≠ predictive edge.**
> Slice 6 measures the first two on the history that exists. It never claims the third or the fourth, and its document says so in a field
> (`predictive_edge_claim: "none"`) that a test pins.

## What it is

`python -m research.lab.dataset_cli status` (run with `PYTHONPATH=mechanism`): a read-only report of what forward-observed research history exists,
how trustworthy it is, and what keeps the existing Slice 5 contract from passing. It answers one question: *is the history we are accumulating
becoming usable, and if not, what exactly is wrong?*

| Module | Role | Kind |
|---|---|---|
| `lab/research_status.py` | builds the versioned status document, the canonical hash, the text rendering; holds the collector facts, the integrity catalogue and the "not proven" list | pure |
| `lab/research_status_reader.py` | the SELECT-only reads (per-session aggregates; no row payloads) | I/O, read-only |
| `lab/dataset_readiness.py` | `assess_data` extracted from `assess` (checks 5-10); hash-identical for every Slice 5 world | shared |
| `lab/dataset_cli.py` | the `status` subcommand; never calls `_register` | I/O |

Flow: `spec (config + calendar + cutoff)` → reader aggregates per session, per source → `build_status(inp)` (pure) → `status.json` + `status.txt`;
volatile facts (database target, database time, post-cutoff rows ignored, code identity) go to `status_context.json`, outside the canonical document.

**Slice 5 stays the only readiness authority.** The status contains no `model_research_eligible` key. Its "contract" section evaluates the *same*
`assess_data` function Slice 5 uses on a provisional dataset (checks 5-10) and lists checks 1-4 and 11 as "needs a real build". A DB test asserts the
status's data checks equal the `readiness.json` checks Slice 5 produces on the same world.

## Interface

    dataset_cli status <db> --spec spec.json [--cutoff ISO|db-now] [--out-dir DIR] [--json] [--quiet] [--require-no-readiness-failures]

`<db>` is the common set: `--host H --dbname D --user U [--port 5432] [--password-env VAR] [--connect-timeout 10] [--repo-root PATH]`. The password
comes from the environment variable named by `--password-env`, never argv, never output. `--spec` is the same authoring spec `author` takes (only its
config, calendar, windows and cutoff are used). `--cutoff` replaces the spec's cutoff (an ISO time **with** a UTC offset, or `db-now`): the status is a
pure function of the cutoff, so `--cutoff` answers "what was known by then". `--register` is rejected (exit 2).

Exit codes: `0` ok; `1` failed closed (bad spec/calendar, future cutoff, internal hash re-derivation failure); `2` usage; `3` database error (including a
missing table: the preflight names it); `4` `--require-no-readiness-failures` and at least one failure is listed.

Safe against a read-only role: `set_session(readonly=True)`, `application_name=lab_dataset_cli`, the harness's `read_only_session`, SELECT/WITH only.
A test runs the whole command as a real role holding `SELECT` only and a logging cursor proves every statement is SELECT/WITH/SET/SHOW. It does not
touch the experiment registry (no registry tables are required or read).

## Sources monitored (from the Slice 4/5 contracts; the code wins)

| Source | Table(s) | Provenance | Forward collector today |
|---|---|---|---|
| `candidates` | `candidate_observation`, `candidate_capture_run` | run status + stamp | screener hook in the daily pipeline (three gates, default OFF) |
| `market` | `market_snapshot` | `observed` / `reconstructed` / unknown | **none scheduled** — `market_intelligence/runner.py`, operator CLI |
| `sector` | `sector_snapshot` | same | **none scheduled** — same runner |
| `stock_rs` | `stock_relative_strength` | same | **none scheduled** — same runner |
| `labels` | `forward_return_label` | n/a (`computed_at` stamp) | **none scheduled** — manual label runner |
| `catalyst` | `market_event_revision` / `catalyst_classification` | event model | **no collector exists** |
| `first_seen` | `source_observation` | event model | **no collector exists** |

Sources actually required are those the spec enables (a disabled source is not "failing"). The `COLLECTORS` table in `research_status.py` states these
facts; the database cannot say them.

## Per-source report

For each source: `capture_state` (`never_captured` / `stalled` / `active`; `active` = a trustworthy observation among the last 3 due sessions),
dormant-vs-active, first and latest observed session, observed sessions, expected sessions (the calendar sessions that are *due*: decision deadline
inside the cutoff; sessions still inside the grace window are listed as pending, not missing), coverage %, missing sessions as ranges (exact count
always; at most 50 ranges listed), reconstructed rows, unknown-provenance rows, late rows, longest and current consecutive streak.

Joint history = the sessions on which candidates **and** every enabled provenance source were observed in time. It gives the joint floor, joint
coverage since the floor, the current/best consecutive run, and the **authoritative earliest trustworthy PIT date** — taken from the Slice 5 rule
(`dataset_readiness`), not re-derived.

Label maturity: candidates, mature, final, void, unlabelled-mature, not-yet-mature, and the approximate final-labelled primary-horizon rows per
train / validation / test against 300 / 100 / 100.

## Integrity checks (31; reported, never repaired)

`INTEGRITY_CATALOGUE` — each has a severity (`blocker` / `warning` / `info`), an exact count and up to five example sessions. Blockers also become
readiness failures. History is never edited or filtered to look healthier; ambiguous history is *counted*.

* candidates: run not complete (partial / failed / running with no later complete run), run hash drift, snapshot drift, rows late, rows back-dated,
  rows before activation, rows off-calendar.
* market / sector / stock_rs (each): rows late, rows back-dated, reconstructed rows (info), unknown-provenance rows (blocker), sessions holding both
  an observed and a reconstructed row (info: reconstruction shadowed), off-calendar rows.
* sector: observed sessions whose distinct-sector count varies.
* stock_rs: sessions with more than one run content hash (conflicting observations, blocker); observed `ok` cells for symbols with no sector (warning:
  they carry `sector_pit_safe=false`, which Slice 5 check 6 counts); observed `ok` cells with an unsafe sector map (blocker).
* labels: computed before their own horizon began (impossible availability, blocker); off-calendar.

"Late" = stamp at or after the session's decision deadline (`00:00 UTC` of `t0 + 1 + grace` days). "Back-dated" = stamp before the session's own UTC
day began. The reader's SQL deadline expression is pinned against `dataset_contract.decision_deadline` / `is_known` at the boundaries, under five
session time zones.

## Readiness failures and the estimate

`readiness_failures` lists, with scope, what keeps the Slice 5 contract from passing: a required source `never_captured` / `stalled`, each failing
data-derived Slice 5 check, an integrity blocker, a non-exclude reconstructed policy, and **structural** failures (a required source with no scheduled
collector that is *not* currently being observed; labels with no collector while the contract's data checks do not pass). A structural item for a source
that *is* being observed moves to `structural_notes` — someone is running it, so it is fragile, not blocking.

`remaining_history_estimate` is in **trading sessions**, never a date:

* `null` with a stated reason when a required source is not active ("waiting does not change that") or fewer than 20 sessions are jointly observed;
* `0` when every data-derived Slice 5 check already passes (a real build still decides, and structural notes still apply);
* otherwise `ceil(rows_short / observed_rate) + primary_horizon + 2 × (embargo + purge)`, labelled a lower bound.

## Canonical document and hash

`lab_research_status_v1`: `scope` (strategy, cutoff, grace, horizon, calendar, universe, windows), `sources`, `history`, `integrity`, `contract`,
`readiness_failures`, `structural_notes`, `remaining_history_estimate`, `policy`, `schema_guarantees`, `unproven`, `status_hash` =
`canonical_hash` (the manifest module's canonical JSON, sha256) of every other field. No timestamp of the run, path, host or user is inside it.
Repeated runs, an independent database with the same history, and any session time zone produce the identical hash; rows arriving after the cutoff
do not change it; any change in the visible history does (all tested). `status_context.json` carries the volatile facts and the hash it describes.

## Tests (disposable Postgres only)

* `test_research_status_pure.py` — 34 tests on hand-built inputs (empty, complete, gaps, late start, stalled, reconstructed, overlap, unknown
  provenance, late, back-dated, off-calendar, run states, drift, conflicts, label timing, the 31-check catalogue, dormancy, structural notes,
  estimate rules, hash determinism and tamper detection, no volatile metadata, no eligibility key, no edge claim).
* `test_research_status_db.py` — 37 tests through `main(argv)` against throwaway schemas: empty and activation-only history, unhealthy and healthy
  worlds, partial coverage, late start, stalled source, reconstructed-only and overlapping history, late and back-dated rows, candidates before
  activation, partial / failed / running capture runs, hash drift, multiple RS runs, no-sector RS, labels computed before horizon, post-cutoff rows,
  repeatability, independent-schema reproducibility, time-zone invariance, SQL-vs-Python boundary, logging-cursor read-only proof, a real SELECT-only
  role, `--register` refused, output-directory and cutoff errors, no password in output, Slice 5 agreement, registry tables untouched.
* `test_dataset_pure.py` / `test_import_separation.py` — source guards: the reader issues SELECT only, `status` never registers, only the reader
  may read the activation table, only the existing function may write it.

## Disposable-world examples

Seeded by `tests/status_world.py`, run through the real CLI against a throwaway schema (3 symbols, 800-session calendar, cutoff 2025-03-26 12:00Z).

**UNHEALTHY — the realistic first weeks after activating capture and changing nothing else** (exit 4 with `--require-no-readiness-failures`; hash
`4f193e34…04f3`): candidates ACTIVE 8/8; market, sector, stock_rs `never_captured`; contract not evaluated; 8 readiness failures (the
contract is `slice5_data_checks_not_evaluable`, three `*_capture_never_captured`, three `*_no_scheduled_collector`, `labels_no_scheduled_collector`); no estimate
("not estimable: no observed history is accumulating for market, sector, stock_rs — waiting does not change that").

**HEALTHY — contiguous, complete, observed history** (exit 0; hash `26f70f04…28ee`): all four provenance-bearing sources ACTIVE 581/581, 100 %
coverage, streak 581, zero reconstructed / unknown / late; the Slice 5 data checks all pass (train 540/300, validation 240/100, test 240/100); earliest
trustworthy PIT date 2023-01-02; no readiness failures; four *structural notes* remain (market/sector/RS/labels depend on a manual step); estimate 0.
A healthy status is **not** eligibility: `audit_passed`, `inputs_verified`, `code_identity_exact` and `test_split_unevaluated` still need a real
`build`.

## Known limitations

* A market/sector/RS/label stamp (`captured_at`, `computed_at`) is a writer-settable default: a stamp inside the deadline is evidence, not proof.
  Candidates' run rows, RS `created_at`, events and first-seen are DB-stamped.
* A session with no capture is reported missing; the tool cannot tell a crashed run from a run nobody scheduled.
* The calendar is the one in the spec; a wrong calendar makes expected-session counts wrong.
* Daily decision-deadline model; intraday timing is not modelled.
* The last mature session's label is computed after the cutoff, so it can appear as "unlabelled-mature" (faithful to the cutoff rule).
* The collector facts are declared from the repository at Slice 6; if a timer is later added the table must be updated (a test pins the current
  claim that no scheduled collector exists for market/sector/RS/labels).
* `status` needs the tables of the enabled sources. On production today (migrations 24-30 not applied) it exits 3 and names the missing tables; it
  never guesses.

## Technical debt

* The reader's per-session aggregation re-states the Slice 4 deadline rule in SQL (pinned against the Python rule by a boundary test) because
  the status must not load row payloads; the two spellings must be kept in lockstep.
* `COLLECTORS` is a hand-maintained declaration, enforced by a test rather than derived from the scheduler.
* No packaging entry point; run as `python -m research.lab.dataset_cli`.

## What remains dormant

Capture (candidates OFF in production), market/sector/RS writers (unscheduled, migrations 24/25/29 not applied), the label runner, the event
collectors, the experiment registry, every model. Slice 6 changes none of it.

---

# Owner activation plan (NOT executed; for a later, separately approved step)

**Precondition:** Release B S11 has issued its decision and S12 is separately authorised. Nothing below is started by this slice.

## A. Capture of candidates (the only scheduled forward collector)

Three independent keys (from `RELEASE_B_ACTIVATION.md`): image containing the hook (already pinned, `cfd83f72f960`), `RESEARCH_CAPTURE_ENABLED=1` in
the pipeline's env, and an `enabled` row in `research_capture_activation`.

1. Choose the first captured session `E` (an explicit, future, trading date; never inferred) strictly after the S11 decision and not before
   `GUARDS_EFFECTIVE_FROM`.
2. As the admin identity, call `research_capture_set_state(<donchian_breakout strategy_id>, 'enabled', E, '<note>')` (the only permitted writer).
3. Set `RESEARCH_CAPTURE_ENABLED=1` in the env file the pipeline reads; do not restart services (the next scheduled run picks it up).
4. Verify (see C).

## B. What capture alone does **not** activate (needs owner decisions, each its own approved stage)

1. Apply migrations 24, 25, 29 (market/sector/RS tables, events) and 26 (labels) to production, then re-run `deploy/db/research_roles.sql` and
   `research_roles_verify.sql` as `MARKET_INTELLIGENCE.md` requires.
2. Decide a schedule for the market-intelligence runner (a systemd timer, committed to `deploy/vps/`) so market, sector and RS history accumulates
   after every session without an operator; and a schedule for the label runner once horizons mature.
3. Resolve the no-sector RS cells (see the owner answer) — a data/policy decision, not a code accident.

## Rollback

* Capture: `research_capture_set_state(<id>, 'disabled', <next session>, '<note>')` stops capture from that session on (append-only; history stays);
  set `RESEARCH_CAPTURE_ENABLED=0`. Either key alone also stops it. Nothing is deleted.
* Rows already captured are immutable; correct them only through the maintenance hatch (two distinct admins).
* Pinning the previous mechanism image (`PREVIOUS_GOOD_SHA`) removes the hook entirely.

## Verification (after the first captured session)

1. `VAL schema --expect exact --capture active` and `VAL config --env-file $ENVF --expect-capture on` (all three keys agree).
2. `VAL capture --session E --guards-from D` (one `complete` run, `hash_drift = 0`, observations reconcile).
3. `dataset_cli status` with a **read-only** role against production, `--cutoff db-now`: candidates `ACTIVE`, no integrity blocker, coverage 100 % of
   due sessions since `E`; then daily, comparing the status hash only when the history is unchanged.
4. Confirm the S11-baseline tables did not change outside the intended rows (ledger fingerprint, delivery hash).

---

# The owner's question

**"If we activated observation capture today and changed nothing else, would the resulting forward history eventually satisfy the existing Slice 5
research-readiness contract? If not, exactly what is still missing?"**

**No.** Activating candidate capture alone would give a clean, growing history of *candidates* — and nothing else the contract requires:

1. **Market, sector and stock-RS provenance has no scheduled writer.** The only writer is the Market Intelligence runner, an operator CLI; migrations
   24/25/29 are not applied in production. Slice 5 check 7 needs ≥ 90 % observed, in-time coverage for each; with nothing writing, coverage stays 0 %
   forever. Waiting does not change that (the status says so and refuses to estimate).
2. **No forward labels.** The label runner is manual and its table (migration 26) is not applied. Without labels no candidate becomes a final-labelled
   row, so checks 9 (labels present) and 10 (≥ 300 / 100 / 100 final-labelled rows) cannot pass.
3. **Probable check-6 problem:** observed RS cells for symbols with no sector carry `sector_pit_safe=false`; check 6 counts them as unsafe. If the real
   universe contains sector-less symbols, check 6 fails regardless of how long history accumulates. The status reports this as
   `stock_rs_ok_cells_without_sector` and counts it exactly; the answer needs real data and a policy decision (drop such cells, or exclude those symbols).
4. **Catalyst and first-seen have no collector at all** — only relevant if the owner's spec enables them; today they are optional.
5. Volume: even with 1-3 solved, the contract needs the final-labelled rows. At roughly the post-guard candidate rate, that is a function of sessions
   observed *jointly* (candidates + all sources) plus the 20-session label lag and the embargo/purge gaps — the status computes this in sessions once
   ≥ 20 jointly observed sessions exist, and says "not estimable" before then.
6. Checks 1-4 and 11 (audit, input hashes, exact code identity, test split untouched) are properties of a real `build`, not of history, and still apply.

What *would* make it eventually pass: capture on **plus** (a) migrations 24/25/29/26, (b) a scheduled market-intelligence runner and a label runner
that observe every session going forward, (c) the no-sector decision, then roughly the status's estimated number of sessions. None of that is
authorised or started by Slice 6.
