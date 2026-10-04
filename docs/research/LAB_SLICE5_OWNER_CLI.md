# Lab Slice 5 — Owner Dataset CLI and dataset readiness

Status: lab branch `lab/first-light-algo`. Operational research tooling only. Nothing here is deployed, scheduled, or run against production, and
Slice 5 adds **no migration**.

> **Reproducible ≠ point-in-time correct ≠ vendor correct ≠ predictive edge.**
> A dataset can be byte-for-byte reproducible and still contain back-dated stamps. It can be point-in-time consistent and still carry a wrong vendor
> price. It can be both and still carry no edge. This tooling proves the first and measures the second; it never claims the third or the fourth.

## What it is

A thin owner-facing command line around the Slice 3/4 infrastructure: `python -m research.lab.dataset_cli` (run with `PYTHONPATH=mechanism`).

    author the manifest  ->  validate  ->  build  ->  audit  ->  report  ->  (optionally) register

It consumes the existing contracts and adds no parallel implementation of manifest validation, hashing, point-in-time semantics, dataset assembly,
leakage auditing, baselines, or reporting. The new logic is only: reading an authoring spec, a portable manifest file, the readiness verdict, and the I/O.

| Module | Role | Kind |
|---|---|---|
| `lab/dataset_authoring.py` | strict authoring spec parser, portable manifest file, canonical JSON | pure |
| `lab/dataset_readiness.py` | the owner-facing readiness / provenance document and the fail-closed eligibility verdict | pure |
| `lab/dataset_cli.py` | argv, database connection, artifacts on disk, the one explicit registry write path | I/O |
| `lab/dataset_runner.py` | gained `verify_manifest` / `build_from_manifest` (the build path the CLI calls) | existing |
| `lab/dataset_reader.py` | gained `read_candidate_symbols` (the observed universe) | existing |
| `lab/dataset_contract.py` | gained `dataset_chunks` (`dataset_hash` is now defined over it, unchanged in value) | existing |

## Interface

Common database arguments (all subcommands): `--host H --dbname D --user U [--port 5432] [--password-env VAR] [--connect-timeout 10] [--repo-root PATH]`.
The password is read from the environment variable *named* by `--password-env` (default `PGPASSWORD`). It is never accepted on the command line and
never printed or written to an artifact. Flags are never abbreviated (`--password` is an error, not a prefix of `--password-env`).

    # 1. author a manifest from the database (read-only; writes only the file)
    dataset_cli author   <db> --spec spec.json --out manifest.json

    # 2. re-validate the file and recompute every input hash against the database (read-only)
    dataset_cli validate <db> --manifest manifest.json [--allow-code-sha-drift]

    # 3. build, audit, report, readiness (read-only; records nothing)
    dataset_cli build    <db> --manifest manifest.json --out-dir OUT [--quiet] [--require-eligible] [--allow-code-sha-drift]

    # 4. the only write: record the manifest, a diagnostic registration and one count-only validation result
    dataset_cli build    <db> --manifest manifest.json --out-dir OUT --register --experiment-name NAME [--require-eligible]

    # 5. reveal the test split (one-shot; needs a prior registered validation and the exact registration hash)
    dataset_cli build    <db> --manifest manifest.json --out-dir OUT --register --experiment-name NAME --include-test --registration-hash H

Exit codes: `0` ok; `1` failed closed (validation, fingerprint, code SHA, PIT, audit, reproducibility, dirty tree); `2` usage error; `3` database error
(unreachable, missing tables, constraint); `4` `--require-eligible` and the dataset is not model-research-eligible.

## Authoring a manifest

The spec (`lab_authoring_spec_v1`, JSON) is strict: every key is required or refused, all problems are listed at once, and nothing is defaulted.
Identity cannot be written by hand — `code_sha`, `input_hashes`, `created_at` and `manifest_hash` are rejected as unknown keys.

* Calendar: `{"file": "calendar.txt"}` (one ISO date per line, `#` comments) or `{"derive": {"start", "end"[, "benchmark_symbol"]}}`, which uses the existing
  `research.labels.sessions.derive_sessions` over the stored price bars and fails closed when they do not cover the range.
* Universe: `{"from_db": true}` takes the symbols with an observed candidate for the configured strategy inside the calendar and before the cutoff
  (sorted, de-duplicated, never empty), or `{"from_config": true}` with `config.universe_members` stated explicitly.
* The cutoff must carry a UTC offset; it is normalised to UTC. A cutoff in the future of the database clock is refused by the manifest library.
* Authoring needs a clean git tree (the code identity is `HEAD`), computes every input fingerprint with the shared `author_input_hashes`, then writes the
  manifest **file** (`lab_manifest_file_v1`: the manifest, its calendar, its hash and the database time it was authored at). The file is re-read and
  re-validated before it is written; it is refused if it already exists.

The file is self-contained, so `validate` and `build` need no registry row. Editing the document, truncating the calendar or changing a hash makes the
file fail its own re-derivation.

## Read-only guarantees

* Every connection the CLI opens by default is `set_session(readonly=True)` (so a write is refused by Postgres), `application_name=lab_dataset_cli`, and
  every read additionally runs inside the harness's `read_only_session`.
* `author`, `validate` and a plain `build` never open a writable connection. `_register` is the only function that does, and the only one that calls
  `RS.insert_*` / `record_validation` / `record_test` (a source-level guard test enforces this, and that the CLI contains no write SQL of its own).
* A build writes nothing to the database and refuses to write into a non-empty output directory.
* `run_context.json` (database target, mode, number of post-cutoff rows ignored, registration) is outside the reproducibility set on purpose.

## Registry opt-in

Recording requires `--register` **and** `--experiment-name`. It is one transaction on a separate writable connection: manifest (idempotent by hash),
diagnostic registration (idempotent by hash), then one count-only `validation` result (`n_rows`, `n_final`). Running again records nothing new and says
`validation_already_recorded`. `--register --require-eligible` on an ineligible dataset writes artifacts, records **nothing**, and exits 4.
`--register` cannot be combined with `--allow-code-sha-drift` (a recorded result must name the exact code that made it). The test split is revealed only
with `--include-test --registration-hash H` plus `--register`; it needs a prior validation result and the registry's one-shot rule and database triggers
refuse a second look. A different manifest under an already-registered dataset name and version is refused by the database (exit 3).

## Artifacts

Canonical (byte-reproducible): `dataset.jsonl` (its sha256 **is** `dataset_hash`), `audit.json/.txt`, `verification.json`, `report.json/.txt`,
`readiness.json/.txt`, `identities.json`. Context (not part of the reproducibility set): `run_context.json`. A failed build writes `failure.json` plus
whatever audit / verification evidence exists, and no `dataset.jsonl`.

Printed identities: `manifest_hash`, `dataset_hash`, `audit_hash`, `report_hash`, `readiness_hash`, `inputs_hash`, and the running and manifest code SHAs.

## Readiness and eligibility (fails closed)

`readiness.json` (`lab_dataset_readiness_v1`) reports: observed vs reconstructed coverage, per source (market, sector, stock relative strength, breadth,
catalyst, first-seen) with counts of observed / reconstructed-used / reconstructed-excluded / late / absent / unavailable / unknown-availability;
the earliest trustworthy PIT date; label maturity per split; manifest and code identity; all hashes; every known PIT limitation; and the verdict.

`model_research_eligible` is true only if **all eleven** checks pass; any failing or unevaluable check makes it false:

1. `audit_passed` — the Slice 4 leakage audit verdict is PASS.
2. `inputs_verified` — every manifest input hash recomputed and equal; nothing unverifiable.
3. `code_identity_exact` — running SHA equals the manifest SHA, clean tree (drift makes it ineligible).
4. `reconstructed_policy_is_exclude` — the manifest excludes reconstructed history.
5. `no_reconstructed_value_in_dataset` — zero reconstructed or unknown-provenance cells (unknown counts as reconstructed, never as observed).
6. `relative_strength_sector_pit_safe` — no relative-strength cell uses a sector map that is not point-in-time safe.
7. `observed_coverage_sufficient` — observed, in-time coverage >= 90% per provenance-bearing source.
8. `trusted_pit_date_determined` — an earliest trustworthy date exists and lies inside the train window.
9. `labels_present` — missing-label fraction of train+validation primary-horizon rows <= 5%.
10. `final_label_samples_sufficient` — final-labelled primary-horizon rows >= 300 train / 100 validation / 100 test.
11. `test_split_unevaluated` — the test split has not been revealed by this build.

Earliest trustworthy PIT date: the earliest `t0_session` D such that, from D onward, every enabled provenance source has an observed, in-time value in
>= 90% of primary-horizon rows, and D is no earlier than the day every source first has an observed value. When some source never has an observed value
it is "not determinable" with the reason. It is the date the dataset can vouch for, not a proof: the market and sector stamps are writer-settable.

The thresholds are declared in the document, not buried in code. They are conservative gates for "may a model be researched on this", not statistical
power calculations, and they are a policy the owner can change by an explicit code change.

## Reproducibility evidence

End-to-end through `main(argv)` and the real database connection, in `tests/test_dataset_cli_db.py`:

* **A** — two builds on an unchanged database: every canonical artifact byte-equal; identical manifest, dataset, audit, report and readiness hashes;
  identical human-readable stdout (modulo the output path); `run_context.json` equal.
* **B** — a second, independent disposable database (`CREATE DATABASE`, falling back to a separate schema only if the role cannot create one) seeded with
  the same world: the manifest authored independently there has the same `manifest_hash` and input hashes, the first database's manifest validates against
  it, and all builds are byte-identical.
* **C** — rows appended after the cutoff leave validation passing and every canonical artifact byte-equal; only `run_context.json` records how many were ignored.
* **D** — each pre-cutoff source mutation makes `validate` and `build` fail closed (`input_hash_mismatch`, `failure.json`, no `dataset.jsonl`). After
  legitimate re-authoring under a new `dataset_version`, the changed table's input hash, the manifest hash, the dataset hash and the report hash all change,
  and the old manifest still refuses.

## Known limitations

* A manifest file pins the calendar it was authored with; `build` does not re-derive it from the database.
* A spec with `calendar.derive` uses stored price bars; a vendor error in them is inherited (labels are simple `fwd_v1` returns).
* The stamps `captured_at` (candidates, market, sector) and `computed_at` (labels) are writer-settable defaults. Fingerprints prove the database has not
  changed since authoring, not that it was honest when written.
* Observed history is only as long as live capture has run; the sector series before that is reconstructed and never PIT-safe. On the data that exists
  today the verdict is expected to be "not eligible".
* Daily decision-deadline model; intraday timing is not modelled. The universe is only the observed candidate universe when derived from the database.
* One-shot test-split protection is enforced by the registry and its triggers, not by the CLI; a superuser can bypass any application-level rule.

## Technical debt

* `dataset_cli.py` imports two pure helpers of the label engine (`derive_sessions`, `CalendarError`) via a narrow, test-enforced carve-out in
  `test_import_separation.py`: it may import exactly those two names from `research.labels`, nothing else may import the CLI, and the CLI may not touch the
  label repository or runner.
* The readiness thresholds are module constants, not manifest fields; changing them changes `readiness_hash` but not `dataset_hash`.
* No packaging entry point; run as `python -m research.lab.dataset_cli`.

## What remains dormant

No scheduling, no production dataset build, no registry rows written anywhere outside tests, no capture, no model or feature code. Slice 6 has not begun.

## Before the first real-data run (owner checklist)

1. Run only against a database you intend to read, with a role that has `SELECT` on the lab tables; use a read-only role for `author`/`validate`/`build`.
   A writable role is needed only for `--register`.
2. Apply migrations 26-30 to that database (the CLI's preflight refuses with the missing table names) — a production migration is a separate, owner-approved step.
3. Commit the code you intend to run; the tree must be clean and `HEAD` becomes the manifest's code identity.
4. Choose the cutoff, windows, label maturity session and embargo deliberately (embargo must cover the longest horizon plus purge).
5. Author, `validate`, `build` without `--register`, read `readiness.txt`, and only then decide whether to record a validation result.
6. Treat `model_research_eligible: NO` as the expected answer until enough observed capture history exists, and never treat YES as an edge claim.
