# Lab Slice 8 — No-Sector Semantics & Sector Provenance Hardening

> LAB ONLY. Nothing here is deployed, migrated, scheduled or activated. Production, the S11 validation and `CURRENT_MECHANISM_SHA` are untouched.
> No migration was written and none is proposed in this slice.

## 1. Option B — the exact semantics (owner decision)

> The candidate remains valid, but if trustworthy sector identity was unavailable at the decision point, sector-relative strength is unavailable.

* The symbol **stays in the universe**; it is never excluded, and no row is dropped for want of a sector.
* Its sector-relative RS (`rs_vs_sector_pp`) is **NULL** with the explicit state `rs_vs_sector__state = 'no_sector'`. It is never `0` and never a
  market-relative number. Its market-relative fields (`rs_ret_pct`, `rs_vs_spx_pp`, `rs_percentile`) stay its own and are separate columns.
* The state is distinguishable from every neighbouring case:

| Situation at the decision point | Evidence (`sector_provenance.classify_sector_evidence`) | `rs_vs_sector__state` | Sector-relative value | Readiness check 6 | Audit |
|---|---|---|---|---|---|
| 1. PIT-safe sector + valid RS (the ONLY observed measurement) | observed + fresh | `ok` | kept | pass | pass |
| 2. No PIT-safe sector, no value | unavailable / `no_sector` | `no_sector` | NULL | **pass (Option B)** | pass + limitation `sector_relative_unavailable` |
| 3a. Reconstructed / not-PIT-safe sector but a value is present | reconstructed / unsafe | `sector_reconstructed` / `unsafe_value` | kept (so it is visible) | **FAIL** | limitation `sector_relative_unsafe_value` |
| 3b. A value but no sector at all | — | `unsafe_value` | kept | **FAIL** | same |
| 4. Unknown provenance (blank source, missing as-of, unrecognised provenance) | unknown | `sector_unknown_provenance` | NULL (masked) | pass (nothing carried) | **fatal** `sector_provenance_unknown` |
| 5. Genuinely missing source observation (no RS row) | — | `absent` / `unavailable` | NULL | pass | pass |
| 6. Fresh sector learned later than the decision (as-of after t0, or captured after the deadline) | unavailable / `asof_after_t0` / `name_late` | `sector_unconfirmed` | NULL | pass | pass |
| 7. Stale sector (> 30 days) | observed + stale | `sector_stale` | NULL (masked) | pass | pass + limitation `sector_relative_value_masked` |
| 8. RS row and candidate disagree on the sector | — | `sector_identity_conflict` | NULL (masked) | pass | pass + limitation `sector_relative_value_masked` |

Check 6 (`relative_strength_sector_pit_safe`) is decided from the **value**, never from a state label (`dataset_readiness.sector_relative_unsafe`): a NULL
value can never be unsafe, a non-NULL value is safe only in the single state `ok` backed by an observed, PIT-safe, sector-bearing, agreeing RS row. The
independent audit re-derives the expected cell from the raw rows and fails any assembly that differs.

No market-relative fallback, no universe filtering and no global weakening exist anywhere: the readiness rule is exactly "an unavailable value is not an
unsafe value, but a carried value must be provably safe".

Informational only (**no threshold, deliberately**): `coverage.sector_relative` reports how sparse the sector-relative feature is. See limitations.

## 2. Identities that change (and what stays reproducible)

| Identity | Before | After | Why |
|---|---|---|---|
| Dataset schema | `lab_dataset_v1` (hash `84dd207c…527e1`, frozen, verified against the pre-Slice-8 code) | `lab_dataset_v2` (hash `0e919310…ed1`) | two columns added: `rs_sector`, `rs_vs_sector__state` |
| Audit schema | `lab_dataset_audit_v1` | `lab_dataset_audit_v2` | audit re-derives the sector-relative cell and sector evidence |
| Readiness schema | `lab_dataset_readiness_v1` | `lab_dataset_readiness_v2` | check 6 semantics + `sector_relative` summary |
| Research status schema | `lab_research_status_v1` | `lab_research_status_v2` | `sector_relative` section; `stock_rs_ok_cells_without_sector` is now `info` |
| Preflight schema | `forward_collection_preflight_v1` | `forward_collection_preflight_v2` | owner-decision machinery removed |
| `QUERY_VERSIONS` / `SOURCE_COLUMNS` / DB fingerprints | q1 | **unchanged** | the same stored columns are read; no database change |

* **Old historical hashes stay reproducible under the old contract.** `dataset_chunks/dataset_hash/encode_row` take a `schema`; under
  `lab_dataset_v1` the two v2-only columns are projected away, so a v1 byte stream is exactly what the v1 code produced *provided no v2-only masking
  occurred* (a masked value would have been kept under v1). A manifest authored under v1 declares the v1 schema hash; the v2 builder refuses it
  by design (hash mismatch), and the audit adds a hint that points at the manifest-pinned `code_sha` — the sanctioned way to rebuild an old dataset.
* **New builds are deterministic**: a real double build in the DB tests gives equal `dataset_hash`, audit and readiness.
* **Readiness hashes legitimately change** (schema string, new `sector_relative` block). Baselines recorded in section 8.

## 3. Sector source chain (code-verified)

| # | Stage | Origin | Source timestamp | Availability timestamp | Freshness determination | Sector change representable historically? | Today's sector projected backward? | Restatement can rewrite apparent history? |
|---|---|---|---|---|---|---|---|---|
| 1 | Vendor: yfinance `info.get('sector')` | Yahoo's **current** classification at fetch time | **none** (the vendor gives no as-of) | our fetch time | none at the vendor | no (current value only) | n/a | yes — the vendor may silently reclassify; nothing in the payload says so |
| 2 | `daily_fundamentals.sector` (`fundamentals_updater.py`, `ON CONFLICT (symbol, date) DO UPDATE SET sector=…, updated_at=…`) | the fetch above, stored per `(symbol, date)` | `date` = the fetch's trading date (an assumption about when we fetched, not a vendor fact) | `created_at` / `updated_at` — naive (no tz), no trigger enforcing them | none | yes, across **different** dates (a later fetch is a new row) | no, per-date rows are separate | **yes within one date**: a same-date refetch overwrites the value in place and the old value is lost. **Not PIT-safe by itself** — a `date` column is not an availability proof |
| 3 | Capture reader `research.repository.fetch_sectors` | latest row with `date <= session AND date >= session - 30d` | the row's `date` → stored as `feature_snapshot.sector_asof` | the snapshot's own `captured_at` (stamped; append-only) | **bounded to 30 days (`SECTOR_WINDOW_DAYS`)**; older ⇒ sector stored as NULL, i.e. *unavailable*, not flagged stale | yes — one immutable snapshot per capture; sector learned at T never edits an earlier snapshot | no — only rows `<= session` are read | no for stored snapshots (immutable); a later same-date rewrite can only change what a LATER capture reads |
| 4 | MI sector map `market_intelligence.inputs.load_sector_map` | rule `pit_evidenced`: latest row `date <= session` with `greatest(created_at, updated_at)::date <= session`; rule `projected`: today's metadata projected back (always *reconstructed*) | row `date` | `created_at`/`updated_at` | **no recency window** (a row of any age qualifies) | pit_evidenced yes; projected no | `projected` does (labelled reconstructed, never PIT-safe) | an in-place rewrite *after* the session makes the row ineligible for that session (no future leak) but also changes which older row is chosen on a re-run |
| 5 | `stock_relative_strength` (migration 29, writer `stock_rs_rows.py`) | MI `rs_v1` over the sector map | session | `created_at`, DB-stamped | none of its own; `sector_pit_safe` ⇐ observed provenance + PIT-evidenced map. DB CHECKs: `NOT sector_pit_safe OR (provenance='observed' AND sector IS NOT NULL)`, `vs_sector_pp IS NULL OR sector IS NOT NULL`, `state='ok'` iff `ret_pct` not null | per session row, append-only | reconstructed rows are labelled and excluded | rows are never rewritten |
| 6 | Dataset (`dataset_assemble`) + audit + readiness | the candidate's own snapshot (stage 3) **and** the RS row (stage 5) | `sector_asof`, `session_date` | the rows' own stamps, `is_known(avail, t0, grace, cutoff)` | **the new `SECTOR_MAX_AGE_DAYS = 30` contract** (below) | yes — per `(symbol, t0)` row | never; the dataset does not read `daily_fundamentals` | no |

### Why 30 days, and what happens after

`SECTOR_WINDOW_DAYS = 30` is a constant in the capture reader; the code does not record its derivation. The contract adopts the same figure
(`SECTOR_MAX_AGE_DAYS = 30`) so the dataset never accepts a sector the capture layer itself would have refused. It is conservative for a field that
changes rarely and is refreshed by a daily/weekly updater; it is **not** a measured property. After 30 days: capture stores NULL (unavailable); the
dataset contract classifies any stored sector older than 30 days as `observed_stale` and never lets it support a sector-relative value.
Because the capture reader cannot hand back a row older than 30 days, in practice `observed_stale` is a defence-in-depth state (another writer, a
tampered or hand-edited row). It is tested, not currently produced by the live capture path.

### The explicit, fail-closed sector evidence contract (`research/lab/sector_provenance.py`, pure)

`classify_sector_evidence(sector, source, asof, t0, provenance, available, max_age_days=30)`, first matching rule wins:

1. provenance not in `{observed, reconstructed}` → **UNKNOWN** (`unrecognised_provenance`)
2. sector blank / NULL / `'Unknown'` → **UNAVAILABLE** (`no_sector`)
3. provenance `reconstructed` → **RECONSTRUCTED** (never usable)
4. snapshot not yet available at the deadline → **UNAVAILABLE** (`name_late`)
5. blank source → **UNKNOWN** (`source_missing`)
6. missing as-of → **UNKNOWN** (`asof_missing`)
7. as-of after t0 → **UNAVAILABLE** (`asof_after_t0`)
8. age > 30 days → **OBSERVED_STALE**
9. otherwise **OBSERVED_FRESH** — the only usable class.

Unknown is never silently converted to "no sector": the audit raises a fatal finding, so an unrecognised input cannot hide behind Option B's allowance.

### Schema assessment (the STOP condition)

The instruction was to stop and report a schema deficiency before designing any migration if the schema cannot guarantee historical immutability.
Result: **no blocking deficiency for the dataset path.**

* The dataset reads only append-only, DB-stamped observations (`feature_snapshot` – trigger `feature_snapshot_immutable_row` rejects UPDATE/DELETE –
  `stock_relative_strength`, `sector_snapshot`…). A sector learned at T is a *new* row stamped at T, so it cannot alter an earlier row. Tested.
* The dataset never reads `daily_fundamentals`. Tested (static check + a build-hash-unchanged test after an in-place rewrite).

Deficiencies that exist **outside** the dataset path (reported, no migration designed):

1. `daily_fundamentals` keeps no observation history: a same-date refetch overwrites `sector` and the previous value is unrecoverable, and
   `created_at/updated_at` are naive timestamps. Any *reconstruction* from this table can therefore never be proved point-in-time; it is labelled
   `reconstructed` and excluded under the research policy `exclude`, which is the correct handling and is unchanged.
2. The MI `pit_evidenced` sector map has no recency bound, so the MI writer can flag an old sector `sector_pit_safe = true`. The dataset layer cross-checks
   it against the candidate's own bounded evidence, so a stale sector never yields a passing value in the dataset — but the producer flag itself can
   over-claim. Changing it would alter `mi_v2` output and is an **owner decision**, not made here.
3. Recommendation only (needs owner review before any design): an append-only sector-observation history (a stamped row per vendor sector value
   change) would remove deficiency 1 for any future reconstruction.

## 4. Component changes

* **Assembler** (`dataset_assemble.py`): always computes the candidate's sector evidence; the RS block goes through `relative_sector_cell`; adds
  `rs_sector` and `rs_vs_sector__state`; market-relative fields are always kept.
* **Audit** (`dataset_audit.py`, v2): independently re-derives candidate evidence and the expected sector-relative cell for every row; fatal
  `sector_provenance_unknown`; limitations `sector_relative_unavailable`, `sector_relative_value_masked`, `sector_relative_unsafe_value`;
  `sector_relative_states` histogram; legacy-contract hint.
* **Readiness** (`dataset_readiness.py`, v2): check 6 judged from values; informational `sector_relative` summary (`threshold: None`); source coverage
  counts unknown-provenance and stale sectors.
* **Status** (`research_status*.py`, v2): `sector_relative` section; `stock_rs_ok_cells_without_sector` is severity `info`.
* **Collector** (`forward_collection`): `NO_SECTOR_POLICY = "B_null_sector_relative"`; preflight v2 (no owner-decision blocker, answers YES in a
  healthy schema); the verify step fails (as a permanent problem) only a sector-relative **value** without a PIT-safe sector.

## 5. Evidence for the three owner questions

1. **Can a legitimate no-sector stock remain in the research universe without failing readiness solely because its sector-relative RS is unavailable?**
   Yes. `test_sector_option_b_db.py` builds a real database where one symbol has no sector anywhere: it keeps as many dataset rows as the symbols with a
   sector, its sector-relative value is NULL with `no_sector`, the audit passes and check 6 passes. `test_convergence_no_sector_db.py` repeats it for
   ~262 sessions through the real writers: every session `COMPLETE`, all Slice 5 data checks pass, the preflight answers YES.
2. **Can any unsafe / reconstructed / stale sector information result in a non-null RS value that passes PIT/readiness rules?** No. Evidence is
   executed, not argued: `test_exhaustive_candidate_and_rs_combinations_…` runs 7 candidate-evidence shapes × 5 RS-row shapes × 2 reconstructed
   policies over real raw rows and asserts, on the assembled values, that a carried sector-relative value is "safe" only for fresh-observed-safe-agreeing;
   plus dedicated stale / unknown-provenance / reconstructed / not-PIT-safe / no-sector-with-value / unconfirmed / conflicting tests, and the pure
   boundary sweep in `test_sector_provenance_pure.py`.
3. **Can information about a sector learned today alter what the system believes was knowable yesterday?** No, for the dataset path.
   `test_learning_a_sector_at_T_leaves_every_row_decided_before_T_byte_identical` builds the same world with and without a sector learned at T and
   asserts every earlier row is byte-identical (and the hash of the early rows is equal, while the full hashes differ); a sector whose as-of or capture
   stamp is after the decision is not known at it; the stored snapshot rejects UPDATE/DELETE; and an in-place `daily_fundamentals` rewrite leaves the
   dataset hash unchanged.

## 6. Known limitations / owner decisions left open

* Coverage interplay: more than 10% of candidates without a sector still fails `observed_coverage_sufficient` (the sector **context** source needs ≥ 90%
  observed). That is a separate, deliberate check, not Option B's concern. Whether a universe with many legitimately sector-less names should be allowed a
  lower floor is an owner decision.
* No minimum **sector-relative** coverage is enforced (informational only), so a dataset whose sector-relative feature is mostly NULL is eligible; a
  consumer must treat the column as sparse.
* The MI producer's missing staleness bound and the `daily_fundamentals` overwrite semantics (section 3) are reported, not changed.
* `observed_stale` is not currently producible by the live capture path (it is bounded to 30 days); it is a tested defence-in-depth state.
* MCP servers (claude.ai Gmail / Google Calendar / higgsfield) need authorization through the claude.ai connector settings; this session cannot do it.

## 7. Activation blockers that remain (collector)

Unchanged by Slice 8 and all owner-gated: production migrations 24/25 and the rest the preflight names; runtime-role grants; the deliberate amendment
of the "no deploy unit references the collector" guards; the scheduler install; the `model_version='unknown'` → NULL fix stage after S11; and S11
completing. Slice 8 removes only the no-sector decision.

## 8. Readiness-hash baselines (the four fixed pure-fixture readiness documents, `scratchpad/ready_hash.py`)

They change because the readiness schema string changed (`lab_dataset_readiness_v2`) and the document gained the `sector_relative` block; none of the
fixtures' verdicts changed. Old (Slice 7) → new (Slice 8):

| # | Old | New |
|---|---|---|
| 1 | `d543806b…cc742c` | `eb0d865a973800c75ef1f3b0590d8c21496645a44329f58c269874f698027fcd` |
| 2 | `7aa16acb…f5` | `59171d9ec91308794e1b30440e7653c4e35164739b1c68b297a973d75b6689ad` |
| 3 | `edd43f83…531b` | `87ebb8e3edcf48293a53d52b623be4737a436993937b7731b156358e556c32b0` |
| 4 | `ee1a1a7c…cf1e` | `a203f9ed371c8a3b2f2a9faaab290827de778893019ddaa5af4c627cf0531459` |

Old documents remain reproducible by running the pre-Slice-8 commit (`108b66f`); no test was weakened to absorb the change (the only test assertions that
changed are the ones whose meaning Option B deliberately changed: no-sector cells no longer fail check 6, and `stock_rs_ok_cells_without_sector` is `info`).
