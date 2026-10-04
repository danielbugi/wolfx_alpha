# Dataset manifest and experiment registry (migration 30, `research.lab`)

Status: built on the lab branch, **not applied anywhere, trains nothing.** This is the gate that must exist before any ML work: a dataset
and an experiment are written down, immutably, *before* a result exists. No optimisation has been started.

Code: `mechanism/research/lab/manifest.py` (pure validation + hashing; no DB, clock, env or network), `registry_store.py` (INSERT/SELECT on
a psycopg2 cursor). Tables: `dataset_manifest`, `experiment_registration`, `experiment_result`.

## 1. Dataset manifest — identity of a dataset

`manifest_hash` = sha256 of the canonical document (everything except `created_at`, which the database stamps). Two datasets are the same
dataset iff their hashes are equal. Required content:

| Field | Meaning |
|---|---|
| `code_sha` (+ `code_tree_clean`) | commit that produced the dataset; a dirty tree cannot produce a manifest |
| `dataset_name`, `dataset_version` | a name+version with different content is refused, never replaced |
| `label_version`, `label_methodology_version`, `label_horizons` | `fwd_v1` / `fwd_v1.m1`; horizons ⊆ {1,3,5,10,20,60} |
| `feature_versions` | map of feature set → version (`mi_v2` → `1`) |
| `universe_id`, `universe_hash` | universe definition and the hash of its membership + rule |
| `knowledge_cutoff_at` | the PIT cutoff: nothing first known after this is in the dataset |
| `label_maturity_session` | session by which every label in the test window is final |
| `windows` | train / validation / test, each `[start, end]` on sessions of the explicit calendar |
| `embargo_sessions`, `purge_sessions` | gap between windows, in **trading sessions** |
| `calendar_source`, calendar | the explicit session calendar the counts are measured on |
| `input_hashes`, `config` | content hashes of inputs (labels, snapshots …) and the build configuration |
| `created_at` | database clock |

## 2. Experiment registration — the claim, before the result

An experiment names **one** manifest and declares: code SHA (clean tree), feature subset (⊆ the manifest's features), model family and
configuration, `search_budget`, `seed`, `evaluation_plan` (`metrics`, `primary_metric` ∈ metrics, `decision_rule`), and a hypothesis. Its
identity is `registration_hash`. The label version must equal the manifest's (trigger-checked).

## 3. Results — appended, never edited

`experiment_result.result_kind` ∈ `validation | test | failed | abandoned`.

- `n_configs_tried` is cumulative against the registered `search_budget`; exceeding it is refused.
- A `test` result needs a prior `validation` result.
- The test window is evaluated **once** per experiment (partial unique index). Re-testing after seeing a result is a new experiment.
- After `failed` / `abandoned` the experiment is closed.
- `validation`/`test` carry non-empty metrics; `failed`/`abandoned` carry none.

## 4. Leakage controls (enforced in pure code; the database repeats what it can prove)

1. **Embargo ≥ 60 + purge sessions.** A `fwd_v1` label at the end of a window reads up to 60 sessions of future prices; the next window
   must start further than that or its features overlap the previous window's label outcomes. `label_horizons` is confined to ≤ 60.
2. **Sessions, not calendar days.** Windows must start and end on sessions of the explicit calendar; the gap is counted in sessions. The
   database can only prove the necessary lower bound in calendar days (CHECK); the exact session count is the pure validator's job — so
   **always build manifests through `build_manifest`**, never raw SQL.
3. **Strict order** train < validation < test.
4. **Label maturity:** `label_maturity_session` ≥ test end + longest horizon + 3 void-grace sessions; the cutoff cannot precede it.
5. **No future cutoff** (injected "now" in code; database clock in a trigger).
6. **`assign_split`:** a row is placed in a window only if its *whole* label window `[t0, t0 + horizon]` lies inside it; otherwise it is
   `purged` or `embargo`.
7. **Clean tree:** a SHA that does not describe the code is not provenance.
8. **Test-once, budget, validation-first, closure** (section 3) — protects against selection on the test set.

## 5. Contract notes and limits

- The fwd_v1 constants (`HORIZONS`, `LABEL_VERSION`, `METHODOLOGY_VERSION`, `VOID_GRACE_SESSIONS`) are **mirrored** in `manifest.py` rather than
  imported (`test_import_separation` forbids any module outside `research/labels` importing it). A pin test fails if they drift.
- The evaluation plan's *content* rules (primary metric is one of the metrics) are pure-validator only; the database enforces key presence.
- The registry records and constrains; it does not verify that `input_hashes` match real files, or that a training script honoured the
  manifest. A harness that consumes manifests must call `assign_split` and recompute the hashes.
- Logically sits on migrations 22 and 26 but has **no foreign key** to them: a manifest is a pure record and apply order is free.
- Activation: apply migration 30, re-run `deploy/db/research_roles.sql` + verifier. Until then the module is dormant.
