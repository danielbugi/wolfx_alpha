# Forward paper tracker (research slice, 2026-10-12/13)

Status (2026-10-13): **merged (PR #14); built, dry-run and shadow-tested on a scratch restore; NOT activated in production.** No production table holds tracker data, no unit is installed, no pin or arming file exists.
Production activation is a separate owner approval (section 6).

## 1. What it is

A daily, independent, *paper-only* follower of the five registered strategy variants (`donchian_breakout__exit_a_atr_stop_fixed_2r`, `..._3r`, `exit_b_donchian_trail_10`, `exit_c_atr_trail_3x`,
`exit_d_hybrid_atr_stop_to_donchian_10`, all `v1`, identities verified against production's `strategy_variant` by hash). Each trading session it takes that session's captured candidates
(`candidate_observation`), registers every candidate into each variant, enters at the next session's open, advances every open position bar by bar with the same engine as the batch experiment
(`exit_policies.open_position/advance`), and records everything as an append-only, hash-chained event stream. It places no order, calls no broker, sends no message and does not touch signals,
screening, the collector, the bot or Telegram.

Per variant the state is independent: one open position per (variant, symbol, direction); a variant's exit never affects another's.

## 2. Storage (migration 36; isolated, additive, `IF NOT EXISTS`)

| Table | Holds | Guarantees |
|---|---|---|
| `paper_session` | one row per processed trading session: scope, code ref, input counts, the open-position snapshot and its hash, chain link to the previous session | strictly consecutive sessions, the DB refuses gaps / reordering; snapshot hash re-verified before the next session uses it |
| `paper_observation` | one row per (session, variant, candidate): frozen decision-time context (`pit_*` features, candidate fields, catalyst events with publication timestamps, market regime when available) | immutable; FK to `strategy_variant` and `candidate_observation` (read-only) |
| `paper_event` | `entered`, `closed`, `hindsight`, ... one hash-chained stream per observation | immutable; event order and chain linkage enforced by the database; `hindsight` events carry `decision_time_information: false` |

Immutability: `ENABLE ALWAYS` triggers block UPDATE/DELETE/TRUNCATE even for a superuser session with `session_replication_role = replica`; the runtime role has INSERT/SELECT only
(`research_roles_verify.sql`: 73/73 mutation attempts and 21/21 schema-tampering attempts refused). Rollback (`deploy/db/rollback_36.sql`) refuses while evidence exists.

Decision-time vs hindsight: everything recorded when a candidate is registered or an exit happens is information available at that time. Maximum favourable excursion after the signal, capture
ratio against it, and "premature exit" (price continued >= 2R within 60 bars after the exit) can only be known later; they are written as separate `hindsight` events, never merged into the features.

## 3. How a session runs (`mechanism/research/paper_runner.py`)

`python -m research.paper_runner dry-run|run --next ...` processes **exactly one session, in one transaction**: the one right after the newest processed session. It refuses (writes nothing) when
the input is not complete (no complete candidate capture run yet, exit 2, a later fire retries), when a gap or a changed scope/source is detected, when the registry no longer matches the
registered hashes, or when the stored snapshot fails its hash (exit 4, a human looks). Re-running a processed session is a no-op. The first session ever must be named explicitly
(`--session S --activation-session`): there is no implicit backfill into forward evidence.

Exit statuses: `0` processed / already processed, `2` input not ready, `3` wrapper lock held, `4` refused, `5` not armed / bad pin, `6` up to date, `7` usage, `8` database connection failed.
Only 0, 2, 3, 6 are success to systemd. (A review of this slice found 2/3 colliding with usage/connection failures in the first draft; they are now distinct and tested.)

## 4. Validation done (scratch restore of the 2026-10-12 backup, `donchian_app` role, never production)

Evidence: `docs/research/evidence/quant_lab_2026-10-11/ (`paper_shadow_real.json`, `paper_shadow_historical.json`, `strategy_experiment.json`)`.

* **Unit/DB tests:** 20 DB tests (migration, privileges, immutability, order/chain rules, idempotency, drift, failed-session rollback, tampered snapshot, rollback script, `resolve_next`, CLI refusals and exit codes), 16 wrapper tests (every refusal happens before a container starts), pure store tests, opportunity and big-winner analysis tests.
* **Real-source shadow (`paper_shadow_real.json`):** the one real forward capture that exists (session 2026-10-09, 250 candidates) -> dry run wrote nothing; the real write created 1,250 observations (5 variants x 250) and the same open-state hash as the dry run; restore verified; re-run a no-op; next session refused as "input not ready"; audit clean. Honest context availability on that session (final code): sector known for 249/250, guard flags present; market regime and relative strength unavailable for all 250 ("no observed market snapshot available at the cutoff" / "no observed relative-strength row at the cutoff"); no catalysts in the window. Absent context is recorded as absent, never filled.
* **Real consecutive sessions (`paper_shadow_real_sequence.json`, a read-only live snapshot of production restored into a scratch):** the three real captured sessions 2026-10-09 (activation), 2026-10-12, 2026-10-13 processed in order as the daily job would: dry run equals write each time; 1,250 entered on 10-12 and 1,270 on 10-13 at the next open; 11 + 25 closes; one-open-position skips (525, 1,005) counted; 3,849 open observations at the end with the snapshot hash verified; re-run a no-op; `resolve_next` says up to date; audit clean; 2,556 distinct event hashes. Regime context is present for 254 / 273 signals of the two post-collector sessions (NEUTRAL) and relative strength for 253 / 273. Caveat: in this replay the availability cutoff is the shadow's run time, so these sessions see rows that exist now; the daily job's cutoff is its own run time.
* **Historical replay shadow (`paper_shadow_historical.json`):** 946 consecutive sessions (2023-01-03 .. 2026-10-09), 143 symbols, 14,284 signals pushed through the same store/runner code one session at a time: 20,882 positions, 20,338 closed, 15,122 hindsight events; **0 mismatches against the batch experiment** on exit price, reason, bars held, R, MFE, MAE, initial stop, initial risk and entry/exit sessions; dry run equals write; re-runs change nothing; audit clean. Mean 0.61 s/session (max 1.7 s).
* **Footprint:** about 140 MB for those 946 sessions with 5 variants (open snapshot ~72 KB average per session). A production session registers ~250 candidates x 5 variants; expect on the order of 20-50 MB per year of session snapshots plus the observations/events. Retention is an owner decision.

None of this says any variant is profitable. The batch experiment's test-period net expectancy at the registered 60-bar cap is negative for all five variants (-0.049 to -0.024 R). The tracker exists to
measure forward behaviour, including tails, honestly; it does not change that finding.

## 5. Artifacts committed but dormant

* `deploy/vps/run_paper_tracker.sh` (own pin `/opt/donchian/CURRENT_PAPER_SHA`, own arming file `/opt/donchian/PAPER_TRACKER_ARMED` that must contain exactly the pin, flock, one-off `docker compose run --rm`). It reads/writes neither the collector's pin nor its arming file, and starts no long-lived container.
* `deploy/vps/donchian-paper-tracker.service.proposed` / `.timer.proposed` (09:30 and 11:30 Asia/Jerusalem: after the forward collector, 06:45 with a retry at 08:15, which writes the observed market-regime and relative-strength rows; the second fire is the retry). Not installed, not enabled; tests assert nothing installed or CD references them.

## 5b. Configuration (owner defaults, 2026-10-13)

* **Universe:** the session's `candidate_observation` rows of the forward capture with `triggered`, scope `triggered` (every triggered candidate; `tracked_intent` is recorded per observation, so the narrower `tracked` view is a query, not a second run). The existing tracked/triggered semantics are not changed.
* **Variants:** the five registered, immutable `v1` identities, verified by hash against `strategy_variant` before every session.
* **Costs:** every `closed` event records net R at 0, **10 (primary)** and 25 bps round-trip (`cost_model`, `r_net_std`, `r_net_actual`), computed with the batch formula (gross minus bps x entry / 1R). Costs never alter an exit.
* **Retention:** all forward evidence is kept (append-only tables); storage is monitored (see `docs/operations/MIGRATION_36_PAPER_TRACKER.md`).
* **Activation session:** the first eligible FUTURE market session after the owner's final approval, named explicitly on the first run (`--session S --activation-session`). No historical backfill into production forward tables; replays stay in the research domain.
* **Absent context:** market regime, relative strength and catalysts are recorded with an explicit `available: false` and a reason (`... not present` when the migration is not applied, `no observed ... at the cutoff` when the table is empty, `query_error:<SQLSTATE>` if a query breaks). Only `observed` market snapshots are used (reconstructed ones are never point-in-time safe).

## 6. Shortest safe path to daily activation (each step needs the owner's go; none has been run)

1. **Merge** the research PR after CI (no production effect).
2. **Image:** run the existing mechanism *build-and-push* from the approved main commit (it deploys nothing: all runtime services require an explicit `IMAGE_TAG`, never `:latest`) so an image containing the tracker exists in ghcr; note its 12-hex tag `TAG` and digest. `CURRENT_MECHANISM_SHA`, the bot pin and the collector arming file are not touched.
3. **Backup**, then **migration 36** exactly as in `docs/operations/MIGRATION_36_PAPER_TRACKER.md` (preflight -> apply -> roles + verifier -> `EXACT`). Requires migration 35 (applied). Additive; rollback script prepared. Production preflight on 2026-10-13: `OK state=ABSENT`.
4. **Install on the VPS (root, one-time):** copy `run_paper_tracker.sh` to `/opt/donchian/scripts/`, write `TAG` to `/opt/donchian/CURRENT_PAPER_SHA`, rename the two `.proposed` files into `/etc/systemd/system/` and `daemon-reload` (do not enable yet).
5. **Production dry run:** `run_paper_tracker.sh dry-run` -> read-only; expect `status: dry_run`, 5 x candidates registrations, nothing written (confirm by row counts).
6. **Activation session:** choose the first session explicitly (the newest fully captured one), run once manually with `--session S --activation-session` after writing `TAG` into `PAPER_TRACKER_ARMED`.
7. **Enable the timer.** From then on each fire processes the next session automatically; `PAPER_TRACKER_ARMED` is the kill switch (delete it: the wrapper refuses, nothing else changes), and re-pinning disarms it.

Owner decisions still open: activation session, retention, whether to track only `triggered` candidates (default) or `tracked`, displayed cost assumption.

## 7. Known limits

* **Market context (read-only production check, 2026-10-14):** the forward collector (armed, 06:45 with a retry at 08:15 Asia/Jerusalem) writes `observed` `market_snapshot` and `stock_relative_strength` rows for the session it observes; they exist from session 2026-10-12 on (regime NEUTRAL, full component weight; 3,061 of 3,064 symbols have an `ok` 20-session relative strength; 22 symbols have no sector). Sessions before that (e.g. the first capture, 2026-10-09) have none, because the collector only started writing then. The tracker records only what exists when it runs, so it must run after the collector (hence the 09:30 / 11:30 fires). A session without a row is recorded as absent with the reason. `market_event` / `market_event_revision` / `catalyst_classification` are EMPTY: no job feeds catalysts yet, so catalyst context is recorded as `unavailable: no event has been recorded yet`, not as 'none in the window'.
* Catalyst context attaches only events with a recorded publication timestamp at or before the decision time; none were attached to the 2026-10-09 capture.
* One real forward session exists. Everything else in the shadow evidence is a dataset replay and is labelled as such.
* Executions are idealised (next-bar open, fixed cost assumption, no liquidity model, no partial fills).
