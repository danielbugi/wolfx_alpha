# Lab Slice 4 — the read-only research harness

Status: **built on the lab branch, applied to nothing.** No migration (the registry from Slice 1–3 is reused), no scheduler, no
production credential, no write to any non-lab table. The harness *reads* the immutable observation layer and *records* only through the
existing registry (`record_validation` / `record_test`). Nothing here runs unless a person calls it.

Code: `mechanism/research/lab/dataset_{contract,reader,assemble,audit,baselines,report,runner}.py` (the first, third–sixth are pure: no
driver, clock, environment, file, network, numpy/pandas or randomness — enforced by AST guards in `test_dataset_pure.py`).

```
manifest_hash ─► load + re-validate manifest ─► parse config (fail closed) ─► code identity
   ─► read raw rows (DB-enforced READ ONLY txn, bounded by cutoff / span / universe / versions)
   ─► fingerprint every DB read + verify every manifest input hash (fail closed on mismatch / missing)
   ─► assemble (PIT masks, split/purge/embargo) ─► INDEPENDENT audit (any fatal finding raises BuildFailed)
   ─► dataset_hash ─► baselines ─► deterministic report (report_hash)
```

## 1. The dataset builder

`build_dataset(conn, manifest_hash, calendar, *, code, allow_code_sha_drift=False, include_test=False, registration_hash=None)`.

* The manifest is fetched by hash, its stored document is re-hashed (a tampered row is refused), re-validated in full (`revalidate_manifest`)
  and its calendar must match the explicit list the caller holds.
* The manifest's `config` is parsed strictly (`parse_config`): strategy, universe members, grace days, minimum sample, primary horizon,
  reconstructed-provenance policy, and one explicit block per optional source (market / sector / stock RS / catalyst / first-seen).
  Missing or inconsistent configuration raises `LabError` listing every problem; nothing is defaulted in a way that changes what is read.
* The running code must equal the manifest's `code_sha`; drift is refused unless `allow_code_sha_drift=True`, and the allowance is recorded
  in the report (`identity.code_check`).
* One row per **(candidate observation, label horizon)**; the canonical column list is `COLUMNS` in `dataset_contract.py`.

## 2. Point-in-time read semantics

A value is usable for an observation with decision date `t0` iff its availability stamp is non-null, `<= knowledge_cutoff_at`, and strictly
before the **decision deadline** (00:00 UTC of `t0 + (1 + grace)` days) — `is_known`. Otherwise the cell is **masked to NULL** and carries a
state, never a default:

| state | meaning |
|---|---|
| `ok` | a value from an eligible row was used |
| `absent` | no row exists for the key |
| `late` | a row exists but became available after the deadline (masked) |
| `unavailable` | the source itself stores NULL / "UNAVAILABLE" |
| `reconstructed_excluded` | only a reconstructed row existed and the policy is `exclude` |
| `no_sector` / `sector_name_late` / `sector_asof_after_t0` | the candidate's sector attribute is missing, was stamped after the deadline, or is as-of after `t0` |
| `unknown_availability` | catalyst events whose availability cannot be proven (PIT grade X) |
| `not_enabled` | the manifest does not enable that source |

* **Provenance:** observed rows are preferred; a reconstructed row is used only under `include_flagged` and is flagged on the row.
* **Candidates / labels** — never "latest": a label computed after the cutoff reads as `missing`; a candidate stamped after its deadline is
  dropped (`candidate_not_known_by_deadline`).
* **Market / breadth** — the `market_snapshot` of `t0` (`regime_components` carries breadth; absent components stay NULL).
* **Sector** — keyed by the *candidate's own* sector attribute (its feature snapshot), itself subject to the deadline.
* **Stock relative strength** — persisted `stock_relative_strength` rows only (never recomputed).
* **Catalysts** — per event key, the latest revision known by the deadline; classifications must also be known; cancelled or out-of-window
  events are excluded. `none_observed` is *not* proof that no catalyst existed (a standing limitation in every audit).
* **First-seen** — the chain of `source_observation` rows known by the deadline.
* Trust in the stamps differs: `candidate/feature/market/sector captured_at` and `label computed_at` are writer-settable defaults; the
  RS / source-observation / classification / event stamps are trigger-stamped. Both facts are printed in every audit.

## 3. Split, purge, embargo

`assign_split` (the Slice 1 contract) decides each row's split from `t0` and the horizon: a row whose label window leaves its window is
**purged** (`dropped`, reason recorded). The manifest must carry `embargo_sessions >= max(label_horizons) + purge_sessions` (60 + purge).
The audit does **not** reuse `assign_split`: it recomputes window membership, label-window end and the train → validation → test gap from
the explicit calendar by index arithmetic, and fails on any disagreement. Labels must be final-mature at `label_maturity_session`; a label
past it, or whose `horizon_session` is not the calendar's `t0 + h`, is fatal.

## 4. Input hash verification

Closes the Slice 3 limitation ("the manifest's input hashes are recorded, not verified").

| kind | inputs | verification |
|---|---|---|
| **cryptographic** — recomputed from content the caller holds | `contract.dataset_schema`, `calendar.sessions`, `universe.members` | exact recomputation |
| **database-derived** — deterministic query fingerprint | `db.candidate_observation`, `db.forward_return_label`, `db.market_snapshot`, `db.sector_snapshot`, `db.stock_relative_strength`, `db.market_event`, `db.catalyst_classification`, `db.source_observation` | sha256 of the rows a fixed versioned query (`q1`) returns for rows that existed at the cutoff |

A fingerprint proves *"the database still says what it said"*, not that the vendor was right. Because the tables are append-only, later
appends do not change it; any change of history does. Status is `OK`, `MISMATCH`, `MISSING` or (outside the vocabulary) reported as an
`unverifiable_input` limitation. `MISMATCH`/`MISSING` is fatal and the audit text names the failing key.

## 5. The dataset fingerprint contract

`dataset_hash = sha256(header ‖ rows)`:

* **header** — JSON of `{schema: lab_dataset_v1, manifest_hash, dataset name/version, label version & methodology, feature versions, code sha,
  ordered columns (name:type), row count}`; so version/methodology identity is bound.
* **rows** — one JSON array per row in the canonical `ROW_SORT` order (observation key, horizon), columns in canonical order, whatever the
  order the caller supplied.
* **cells** — `encode_cell` is type-strict: NULL is JSON `null` (never `""`, `0`, `NaN`); floats are shortest-repr; NaN/inf, bool-in-numeric
  and str-in-numeric are refused; dates ISO; timestamps UTC microsecond ISO.

Same manifest + same data ⇒ same hash (also across two independent databases); any value change ⇒ different hash.

## 6. The leakage audit

`dataset_audit.audit(...)` re-derives from the raw rows (not the assembler's helpers) and emits a machine-readable document
(`lab_dataset_audit_v1`, `audit_hash`) plus text on **every** build. Fatal codes (any → `BuildFailed`, audit and verification attached):
`input_hash_mismatch`, `input_hash_missing`, `row_accounting`, `t0_not_a_session`, `label_invalid`, `pit_violation`, `future_observation`,
`label_maturity_violation`, `split_boundary_violation`, `embargo_violation`, `masked_value_leak`, `reconstructed_policy_violation`,
`selection_mismatch` (a cell is not what the raw rows say was known: provenance, availability stamp, every market/breadth/sector/RS value, the
sector name state, and the catalyst state/count/labels are all re-derived).

Always reported (non-fatal, counted, never zero-filled): reconstructed inputs used/excluded, late values masked, unknown availability,
unavailable features, labels missing, labels ignored after the cutoff, rows purged/excluded by reason, final row counts by split, and the
standing PIT limitations. `test_dataset_audit_negative_db.py` corrupts a valid assembly one rule at a time and asserts each detection.

## 7. Baselines (`lab_baselines_v1`, diagnostic only)

No fitting, no hyperparameters, no search. At the manifest's primary horizon, per split: `null_unconditional` (all candidates, plus per
horizon), `donchian_signal` (tracked intent vs its control group, by signal type, Welch contrast), `donchian_x_regime`, `donchian_x_rs`
(fixed terciles of `rs_percentile`), `donchian_x_catalyst` — each with the availability histogram of its context. Every block carries `n`;
below `min_sample` the statistics are `insufficient_sample` (null), never 0. The **test split is withheld** (`{"status":"withheld"}`) unless
`include_test=True`, which additionally requires a registered experiment with a recorded validation result, no prior test, and not closed.
In-sample (train/validation) results make no predictive claim; the disclaimer is part of the output.

## 8. The diagnostic report (`lab_dataset_report_v1`)

Deterministic (sorted keys, rounded floats, `report_hash` over the body): identity (manifest/dataset hash, code SHA + code check, dataset
version, label methodology, feature versions, universe identity), PIT cutoff, windows, purge/embargo, row counts, per-column missingness,
label and outcome distributions, the full leakage audit, baselines, an explicit list of every metric lacking the minimum sample
(`insufficient_sample_warnings`), and the known PIT limitations. Rendered text is deterministic too.

## 9. Reproducibility proof (tests)

`test_dataset_repro_db.py`: repeated builds identical (rows, audit, baselines, report, hashes); two independent databases give the same
manifest hash and `dataset_hash`; post-cutoff appends are invisible; each of 10 mutations (label return, label MFE, candidate grade,
candidate score, market regime, sector return, RS percentile, event time, catalyst label, first-seen value) fails verification on exactly
its own `db.*` key; re-authoring on mutated data changes `dataset_hash` in exactly the one affected row.

## 10. Recording a run

`diagnostic_registration` (no model, budget 1, count-only metrics `n_rows`, `n_final`, a decision rule that decides nothing),
`record_validation`, `record_test`. The `dataset_hash`, `report_hash`, `audit_hash` and `inputs_hash` are stored as the result's
`artifact_hashes` through the existing append-only registry — no schema change, existing immutability untouched.

## Known limitations

* A query fingerprint is not a proof of vendor correctness, and a writer with INSERT rights could add a *pre-cutoff-stamped* row after the
  fact: candidate/feature/market/sector `captured_at` and label `computed_at` are writer-settable (the audit prints this on every run).
* Sector and RS history outside a live capture window is reconstructed (today's sector map) and never PIT-safe; the default policy
  `exclude` masks it, `include_flagged` keeps it flagged.
* The deadline model is daily (00:00 UTC of `t0 + 1 + grace`); intraday decision times are not modelled.
* `none_observed` for catalysts means "no usable classified event", not "no catalyst".
* The universe is the manifest's explicit list; its survivorship is the author's responsibility.
* Labels are fwd_v1 simple returns on stored prices; they inherit price restatement risk.
* The whole dataset is held in memory (fine at lab scale; a multi-million-row build needs a streaming reader).
* The in-process report contains no significance correction beyond counting comparisons (`n_comparisons`); it is descriptive.

## What must happen before any of this is production-active

All of it is dormant until an owner decides otherwise: migrations 26–30 applied (24/25 for Market Intelligence), real observation capture
running long enough to produce an observed (non-reconstructed) history, a registered manifest authored from the real database, a run of
the harness under a read-only role, and review of the first real audit. None of that is part of Slice 4.
