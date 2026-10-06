# Lab Slice 10 — Append-only point-in-time sector observation history (Option B)

Status: IMPLEMENTED ON THE LAB BRANCH ONLY (`lab/first-light-algo`). Migration 31 is **not applied to production**; the writer flag
`SECTOR_HISTORY_RECORDER_ENABLED` is **OFF by default and set nowhere in deployment**; the collector is dormant; scheduler drafts are
uninstalled; capture is unchanged; production and S11 were not touched. Implements the design approved after
[LAB_SLICE9](LAB_SLICE9_SECTOR_COVERAGE_AND_HISTORY_DESIGN.md). Slice 8/9 decisions are preserved: the 90% sector-context floor, Option B
no-sector semantics, informational global sector-relative coverage, reconstructed history never counting as observed, and the
candidate-bounded Slice 8 protection (the history only *tightens* it).

## 1. What was built

| Piece | File |
|---|---|
| Migration 31 (3 tables, 9 triggers, 6 functions) | `mechanism/add_sector_history_tables.sql` |
| The ONE authoritative writer (flag-gated) | `mechanism/data_updaters/sector_history_recorder.py` + a hook in `fundamentals_updater.py` |
| Pure PIT selector, hash chain, dual-source cross-check | `mechanism/research/lab/sector_history.py` |
| Read-only DB access, assembler/audit/readiness/status/CLI wiring | `dataset_reader.py`, `dataset_assemble.py`, `dataset_audit.py`, `dataset_readiness.py`, `dataset_cli.py`, `research_status*.py`, `dataset_contract.py`, `sector_provenance.py` |
| Role scripts (append-only treatment) | `deploy/db/research_roles{,_rollback,_verify}.sql` |
| Registry / compose / env docs | `docker-compose.yml` (init mount 31), `MIGRATION_REGISTRY.md`, `ENVIRONMENT.md` |

## 2. Schema (migration 31)

* **`sector_observation`** — one row per *change* of a symbol's effective sector per vendor; chain key `(symbol, source)`; `seq`,
  `change_kind` (first / changed / became_none / became_set, validated against the head), `prev_value_hash`, `value_hash`, `sector` (NULL =
  explicit vendor "no sector" with `no_sector_reason` vendor_null / vendor_blank / vendor_unknown_label), `sector_raw`, `source_asof` (always NULL
  today), `captured_at`, `effective_session`, `provenance` (CHECK-pinned to `observed_forward`), `raw_payload_hash`, `raw_payload`, `run_id`,
  `writer`, `code_ref`. `UNIQUE (symbol, source, seq)`
  and `UNIQUE (symbol, source, value_hash)`; named CHECKs on states, hash shape and the sector/no-sector pairing.
* **`sector_poll`** — one row per refresh attempt `(run_id, symbol, source)` (UNIQUE): `response_state` (`sector` / `no_sector` /
  `request_failed` / `invalid_response`), `chain_effect` (`created_observation` / `confirmed_head` / `none`), FK to the observation it created or
  confirmed, `attempted_at` (DB-stamped).
* **`sector_reconstruction`** — a separate store (no key, FK or timestamp semantics shared with the forward tables) for any classification
  projected backwards. **Nothing writes it** (guarded); the reader never selects from it.

## 3. Permissions and immutability

* Runtime role: `SELECT, INSERT` only; research/read roles `SELECT`. No UPDATE/DELETE/TRUNCATE grant anywhere.
* Independently of privileges: `ENABLE ALWAYS` BEFORE UPDATE/DELETE **row** triggers and BEFORE TRUNCATE **statement** triggers that always raise,
  on all three tables (six triggers), plus three BEFORE INSERT stamp triggers. `ENABLE ALWAYS` means they fire even under
  `session_replication_role = replica`. **There is no maintenance hatch**: a correction is a new observation.
* A superuser can still disable triggers (as with every Postgres table). The chain is integrity *evidence*, not a substitute for permissions.

## 4. Hash chain

`value_hash = sha256` over a length-prefixed, schema-versioned (`sector_obs_v1`) encoding of `(symbol, source, seq, sector, no_sector_reason,
sector_raw, captured_at, provenance, raw_payload_hash, prev_value_hash)`, **computed by the database** (`research_sector_row_hash`) in the BEFORE INSERT trigger, never
supplied by the caller. Under a per-`(symbol, source)` advisory lock the trigger requires `seq = head.seq + 1` and
`prev_value_hash = head.value_hash`, so a fork cannot be inserted; `captured_at` is `clock_timestamp()` stamped *after* the lock, so it is monotone
along a chain. The pure verifier (`sector_history.verify_chain`) recomputes every hash independently in Python (parity-tested against the SQL).

## 5. Writer integration

* One call site: `FundamentalsUpdater.update_symbol` → `_record_sector_history`, behind `shr.enabled()`.
* **Flag**: `SECTOR_HISTORY_RECORDER_ENABLED`, on only for exactly `"1"`; default OFF; in no compose/deploy file (guarded).
* **SAVEPOINT isolation**: the recorder uses its **own connection** (never the upsert's) and one SAVEPOINT per unit; a failure rolls back to the
  savepoint, is logged with symbol/source/run/reason (`sector history NOT recorded for …`), is counted (`attempted/recorded/duplicate/failed`),
  appears in the run result under `sector_history`, and is **never raised** into the fundamentals ingestion. A recorder that cannot even be built, and
  a transaction-level failure (aborted connection), are both observable and non-fatal. The recorded outcome is independent of the fundamentals upsert.
* **Single-writer guard** (`test_sector_history_single_writer.py`): only reviewed non-test files may name a history table; only the recorder
  writes them, only by `INSERT`, only to `observation`/`poll`; nothing writes `sector_reconstruction`; nothing copies `daily_fundamentals` into
  the history; the recorder is imported only by the updater; exactly one `enabled()` / constructor / `.record(` call site.

## 6. Three separate timestamp semantics

| Meaning | Column | Semantics |
|---|---|---|
| Vendor-claimed validity time | `source_asof` | **Always NULL**: yfinance info and Tiingo meta supply the *current* classification with no validity date, and none is invented. Audit only; never selected on. |
| When our system observed it | `captured_at` | DB-stamped `clock_timestamp()`; cannot be back- or forward-dated; the **availability time** fed to `is_known`. |
| Which session may consume it | `effective_session` | DB-stamped UTC date of `captured_at`. Combined with `is_known` it blocks next-calendar-day leaks inside the grace window. |

## 7. PIT selection algorithm (`sector_history.select`)

1. Per `(symbol, source)`: eligible rows = `effective_session <= t0` **and** `C.is_known(captured_at, t0, grace, cutoff)` (the same rule every other
   dataset input uses).
2. No eligible row → `no_history` (UNAVAILABLE / history_absent). Eligible rows must be a gapless `seq 1..n` prefix and pass the pure chain
   verification; otherwise → `broken` (fail closed, `sector_identity_conflict`). Later rows are never consulted.
3. The **latest eligible chain observation** is the head. Explicit no-sector (`sector IS NULL`) stays a meaningful verdict (`explicit_no_sector`).
4. Currency = `max(head capture, latest eligible same-value confirmed_head poll)`; failed polls never extend it. Freshness is derived **at read
   time** from the currency UTC date: ≤ `SECTOR_MAX_AGE_DAYS` (30, operational, **not empirically validated**) is fresh, older is stale.
5. Nothing is stored as "fresh" or "stale"; reconstruction rows are never read.

### Same-value refresh, reclassification, vendor failure, explicit no-sector

* **Same-value refresh**: no new observation (`value_hash` unique); a `confirmed_head` poll is recorded and extends freshness only from its own
  capture time. Re-seeing the value does not rewrite the past.
* **Reclassification** A→B: a new chained observation at its own `captured_at`; days before it still resolve to A, days after to B. A→B→A is three rows.
* **Vendor failure** (timeout, no company info, invalid): a poll with `chain_effect = none`; the head is neither erased nor refreshed.
* **Explicit no-sector vs failure**: only yfinance can express an explicit no-sector (observation with `sector NULL`). A Tiingo None/blank/"unknown"
  is *ambiguous* → `invalid_response / ambiguous_source_none`, no observation. A failure is never a no-sector.

## 8. Dual-source cross-check (Slice 8 candidate evidence is NOT replaced)

`crosscheck(candidate, history)`: history can only tighten (safer rank wins; ties keep the candidate's own). A broken chain, or two **fresh**
sources naming different sectors → `sector_identity_conflict` (a cell-level mask, not dataset-fatal; the readiness check
`sector_history_chain_intact` blocks eligibility on chain problems). A candidate with a fresh sector but no history → `sector_unconfirmed`.
History ahead of weaker candidate evidence changes nothing. A later observation never repairs an earlier row (both sides are evaluated at `t0`).
The reports (assembler summary, audit counts, readiness, research status) carry the relation counts and examples.

Opt-in only (`sector_history={"source": …}` in the spec; `schema_hash` is unaffected by the history registry): enabling it makes pre-collection
windows `sector_unconfirmed` **by design** — before forward collection there is nothing for the history to confirm. An explicit-no-sector history
against a candidate saying a sector gives `sector__state = no_sector` but `rs_vs_sector__state = sector_unconfirmed` (fail closed).

## 9. Reconstructed isolation

`sector_reconstruction` is structurally separate, never read by the forward selector, never written by any code, and a
`reconstructed` provenance injected into the evidence stays `RECONSTRUCTED_EXCLUDED` and cannot satisfy the observed-coverage floor.

## 10. Known limitations and technical debt

* DB roles cannot tell two Python modules of the same service apart; the single-writer rule is a **static-guard + trigger-validated** property.
* Source split on fallback: the chain is per `(symbol, source)`; a Tiingo→yfinance fallback starts a second chain. The reader uses the configured source.
* Date-level granularity only (`effective_session`); no intraday semantics.
* `source_asof` is NULL: the vendor's own reclassification date is unknown; only our observation time exists.
* 30-day staleness is an operational constant, not measured.
* Concentration diagnostics deliberately not added (out of scope).

## 11. Not done, deliberately

Migration 31 not applied to production; roles not re-applied; writer flag not set; no timers/cron; no backfill; no concentration diagnostics;
`CURRENT_MECHANISM_SHA` unchanged; collector `step: None` for `sector_history` still blocks activation of that source.
