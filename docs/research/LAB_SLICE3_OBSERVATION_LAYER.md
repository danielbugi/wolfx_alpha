# Lab Slice 3 — the immutable observation layer (migrations 27, 28, 29)

Status: **built on the lab branch, applied to nothing.** Migrations 27–30 exist only in `mechanism/` and `docker-compose.yml`'s init list
on `lab/first-light-algo`; production (Hetzner VPS) has 22/23 applied and is unchanged. Nothing described here is scheduled, and no
collector, classifier or runner flag below runs unless an owner explicitly activates it.

Three tables, one shared design: **append-only, database-stamped availability, explicit version identity, absence is not zero.**

| Mig. | Table(s) | Answers | Availability = |
|---|---|---|---|
| 27 | `source_observation`, `source_poll` | What did First Light know about a mutable external input, when did it first know it, from whom, and how did it change? | `observed_at` / `polled_at` (DB `clock_timestamp()`) |
| 28 | `catalyst_classification` | What did a classifier think a stored fact *meant*, as of when? | `classified_at` (DB-stamped) |
| 29 | `stock_relative_strength` | What did `rs_v1` measure for each stock on each session? | `created_at` (DB-stamped), observed rows only |

## 1. First-seen observations (migration 27)

Code: `market_intelligence/first_seen.py` (pure), `first_seen_store.py` (DB).

- A **series** is `(source, dataset, subject, period)` — e.g. "provider X's `eps_estimate` for AAPL, fiscal period P". `source` and
  `dataset` are free text chosen by an adapter; no vendor, field name or enum is encoded in the schema.
- The log stores **changes only**. Re-seeing an identical value writes no observation; the run is recorded in `source_poll` (what was
  asked, how it ended, which subjects were covered), which is the only way to say "this series was still current at T" without mutating
  anything. `A → B → A` is three rows: a reversion is information.
- Rows form a **hash chain**: `seq` 1, 2, 3 …, each naming the `value_hash` of its predecessor. A trigger refuses a row whose predecessor
  does not match, so a gap, fork or reorder cannot be inserted; `verify_chain` re-checks an extracted chain.
- `observed_at` is stamped by a BEFORE INSERT trigger. A caller cannot choose, back-date or forward-date it. The provider's own claimed
  time is kept as `source_asof` for audit and is **never** availability (PIT grade B: "our ingestion time").
- There is **no backfill path.** History that predates the table is absent, and absence is never filled in or read as zero.
- Values are stored verbatim (canonical JSON, NaN/Infinity refused). Derived quantities (`surprise_as_known`, revision trend) are computed
  at read time from the chain, never stored as facts.
- `record_run(..., apply=False)` is a dry run: it reads chain heads, returns the plan, writes nothing.
- Reads for research use `as_of(chain, t)` / `read_as_of`: the latest observation with `observed_at <= t`, or nothing.

The earnings-calendar mapper `observations_from_earnings_calendar` shows the intended adapter shape; estimates, revisions and
guidance use the same `Observation` type with different `dataset` strings.

## 2. Catalyst classification (migration 28)

Code: `market_intelligence/classification_store.py`; contract type `CatalystClassification` in `filing_contract`.

- A classification **references** an immutable migration-25 `market_event_revision` (`revision_id`) and **pins** the `fact_hash` it read;
  a trigger refuses a row whose pinned hash differs from the revision's. A fact is never rewritten by its interpretation, and
  `classification_store` contains no statement that writes `market_event*`.
- Append-only and **supersedable**: a new classifier version, model re-run or human override is a new row that may name the row it
  replaces (`supersedes_id`). The replaced row stays, so "what did we believe on day D" (`classifications_as_of`) is always answerable.
- Classifier identity is mandatory and typed by method: `model` rows need `model_id` + `prompt_hash`; `human` rows a reviewer id; `rule`
  rows a rule id. A rule is deterministic and carries **no** confidence; confidence exists only for model/human rows.
- Availability is `classified_at`. A classification produced today about a 2024 filing is not "known in 2024"; a deterministic rule's
  output can instead be re-derived from the fact by a feature builder at the fact's own availability.
- Requires migration 25 (refuses to apply otherwise).

## 3. Per-stock relative-strength persistence (migration 29)

Code: `market_intelligence/stock_rs_rows.py` (pure), `store.write_stock_rs` / `get_stock_rs`, `runner --with-stock-rs` (default off).

- One row per `(session, symbol, horizon)` for **every** stock in the session's universe, including unavailable ones, which are stored as
  `state='unavailable'` with NULLs — never zero and never omitted (an omitted row cannot be told from a stock outside the universe).
  CHECKs make state and NULLs agree; NaN/Infinity cannot be stored.
- Fields copy the `rs_v1` definitions rather than recomputing: `ret_pct`, `vs_spx_pp`, `vs_sector_pp` (NULL without a sector or with too few
  valid sector members), `rs_percentile` (NULL below the minimum valid universe).
- `model_version` (`rs_v1` …) and `feature_set_version` (`mi_v2` …) are part of identity. A different definition is a new version and new
  rows; nothing is overwritten.
- **Provenance is part of the UNIQUE key**, exactly as in migration 24: `observed` (computed by the live run for that session, DB-stamped)
  and `reconstructed` (recomputed later; must carry `reconstruction_basis`) coexist and never overwrite each other. Research reads are
  observed-only by default.
- `sector_pit_safe` can be TRUE only on an observed row whose sector map was point-in-time evidenced. A vs-sector measurement therefore
  always travels with the truth about its sector tag. (Sector history in this system is reconstructed — see MARKET_INTELLIGENCE.md — so
  expect it to be FALSE until a PIT sector source exists.)
- Independent of 24/25/26: the link to the session snapshot is by `(session_date, feature_set_version, provenance)`, not a foreign key.

## 4. Guarantees and their limits

- **Enforced by the database:** append-only (UPDATE/DELETE/TRUNCATE triggers, `ENABLE ALWAYS`), runtime role SELECT+INSERT only,
  DB-stamped availability, hash-chain integrity, fact-hash pinning, state/NULL agreement, collision-safe reapplication (marker comments,
  refuses foreign objects).
- **Enforced only by caller discipline / pure code:** that an adapter maps a provider field to the right `dataset`; that `source_asof`
  is not misused as availability by a feature builder (the read helpers never do).
- **Not provided:** any history before the first poll; PIT-safe sectors; any provider adapter. Grade-B availability means "when we
  ingested", which for a late-polled series is later than when the world knew it — conservative, never optimistic.
- **Activation (owner decision, none done):** apply 27/29 (and 28 after 25), re-run `deploy/db/research_roles.sql` + verifier, then
  schedule a collector / enable `--with-stock-rs`. Until then every table is empty or absent.
