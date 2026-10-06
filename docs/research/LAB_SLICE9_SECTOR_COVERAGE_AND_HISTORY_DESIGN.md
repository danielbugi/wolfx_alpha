# Lab Slice 9 — Sector coverage policy and a point-in-time sector-history design

Status: DESIGN AND ANALYSIS. Lab branch `lab/first-light-algo` only. No migration was created or applied, nothing was deployed, capture
stays off, production and S11 were not touched, and the Slice 8 semantics (Option B) are unchanged.

Evidence labels used throughout: **[CODE]** read directly from the code; **[SYNTHETIC]** measured on a disposable, planted world driven through
the real assembler and readiness code (`mechanism/research/tests/sector_coverage_sim.py`, pinned by `test_sector_coverage_policy_pure.py`);
**[REASONING]** architectural argument, no data; **[UNKNOWN]** cannot be answered from lab data, with the production observation that would answer it.
Nothing in this document is a statement about real vendor data: no real vendor data was queried.

---------------------------------------------------------------------------------------------------------------------------------------------

## Part 1 — What the coverage numbers actually tell a consumer

### 1.1 Three different quantities (all [CODE])

| Quantity | Where measured | Counts a row when |
|---|---|---|
| **Sector-context coverage** (the 90% floor, `observed_coverage_sufficient`) | `dataset_readiness.py`, PRIMARY-horizon assembled rows | `sector__state == OK` AND `sector__provenance == observed`. This is the candidate's sector evidence AND the sector-aggregate snapshot, together. |
| **Usable sector-relative RS coverage** | `_sector_relative_summary` (informational, `threshold: None`) | `rs_vs_sector_pp` is a number. Decided from the VALUE, not from provenance labels. |
| **Market-relative RS coverage** | `rs_vs_spx_pp` | Unaffected by the sector (Option B has no market-relative fallback). |

Because the floor reads provenance of the context and the sector-relative figure reads the value, the two **diverge in both directions**
[SYNTHETIC]:

* sector known and fresh but the RS model produced no value: context 100%, usable sector-relative 50% — **the floor is blind to it**;
* sector aggregate snapshot absent but the stock's own sector-relative RS present: usable 100%, context 88–50% — **the floor fails although the
  feature is complete**.

So neither number can stand in for the other. That is the architectural reason the owner's decision to keep them separate is correct.

### 1.2 Coverage sweep [SYNTHETIC] (100 symbols, 4 sectors, 43 landing sessions, 4300 primary rows)

`ctx` = sector-context observed, `sec-rel` = usable sector-relative, `mkt-rel` = market-relative, `rows` = rows without a sector-relative value.
Shapes: **random** = independent cells; **symbol cluster** = whole symbols lose their sector; **session scatter / outage** = whole sessions lose it.

| Shape | Target | ctx | sec-rel | mkt-rel | Rows w/o sec-rel | Symbols hit (fully) | Sessions hit | Failed data checks | Earliest trusted PIT date |
|---|---|---|---|---|---|---|---|---|---|
| any | 100% | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 | none | 2023-01-02 |
| random | 95% | 0.950 | 0.950 | 1.000 | 215 | 91 (0) | 42 | none | 2023-01-02 |
| random | 90% | 0.900 | 0.900 | 1.000 | 430 | 100 (0) | 43 | none | 2023-01-02 |
| random | 89% | 0.890 | 0.890 | 1.000 | 473 | 100 (0) | 43 | floor + PIT date | none |
| random | 75% | 0.750 | 0.750 | 1.000 | 1075 | 100 (0) | 43 | floor + PIT date | none |
| random | 50% | 0.500 | 0.500 | 1.000 | 2150 | 100 (0) | 43 | floor + PIT date | none |
| symbol cluster | 95% | 0.950 | 0.950 | 1.000 | 215 | 5 (5) | 43 | none | 2023-01-02 |
| symbol cluster | 90% | 0.900 | 0.900 | 1.000 | 430 | 10 (10) | 43 | none | 2023-01-02 |
| symbol cluster | 75% | 0.750 | 0.750 | 1.000 | 1075 | 25 (25) | 43 | floor + PIT date | none |
| symbol cluster | 50% | 0.500 | 0.500 | 1.000 | 2150 | 50 (50) | 43 | floor + PIT date | none |
| session scatter | 95% | 0.954 | 0.954 | 1.000 | 200 | 100 (0) | 2 (2) | none | 2023-01-02 |
| session scatter | 90% | 0.907 | 0.907 | 1.000 | 400 | 100 (0) | 4 (4) | none | 2023-01-02 |
| session scatter | 89% | 0.884 | 0.884 | 1.000 | 500 | 100 (0) | 5 (5) | floor | 2023-04-24 |
| session scatter | 75% | 0.744 | 0.744 | 1.000 | 1100 | 100 (0) | 11 (11) | floor + PIT date | 2024-10-29 |
| outage early | 95% | 0.954 | 0.954 | 1.000 | 200 | 100 (0) | 2 (2) | none | 2023-01-24 |
| outage early | 90% | 0.907 | 0.907 | 1.000 | 400 | 100 (0) | 4 (4) | none | 2023-02-15 |
| outage early | 50% | 0.488 | 0.488 | 1.000 | 2200 | 100 (0) | 22 (22) | floor | 2023-09-05 |
| outage late | 95% | 0.954 | 0.954 | 1.000 | 200 | 100 (0) | 2 (2) | none | 2023-01-02 |
| outage late | 89% | 0.884 | 0.884 | 1.000 | 500 | 100 (0) | 5 (5) | floor + PIT date | none |

(Full table: the `main()` of the simulator; session shapes round to whole sessions, hence 0.907 / 0.884.)

What a consumer receives:

* **Never a zero and never a borrowed number.** At every coverage level the missing value is NULL with state `no_sector`, the symbol stays, and
  `rs_vs_spx_pp` is intact. A model sees a missing feature, not a fabricated one. [SYNTHETIC, pinned]
* **At and above 90% the dataset is accepted, below it it is rejected** — the floor is inclusive at exactly 0.90. [SYNTHETIC, pinned]
* **The floor is count-based, so it is blind to shape.** At the same 90%: scattered cells touch all 100 symbols lightly (no symbol fully lost, worst
  session 14% missing); a symbol cluster removes 10 symbols entirely (the worst 10 symbols carry 100% of the gaps); a session cluster removes 4
  whole sessions (worst session 100% missing). All three pass. [SYNTHETIC]
* **The PIT-date gate is a different, partly independent signal.** An early outage costs trusted history (earliest date moves from 2023-01-02 to
  2023-02-15) without failing the floor; a late outage of equal size costs none. The same row count is cheap or expensive depending on *when*.
  [SYNTHETIC, pinned]
* **Market-relative RS is 100% in every scenario** — Option B means a sector gap never degrades the market-relative feature. [SYNTHETIC, pinned]

### 1.3 Can missingness itself introduce bias?

The hypotheses below are all **plausible** and none is **demonstrated**. The lab has no vendor data to test any of them.

| Hypothesis (sector missing is correlated with …) | Mechanism | Evidence | Status |
|---|---|---|---|
| small / new companies | thinly covered names are the likelier ones for the vendor to return no sector | none | UNPROVEN |
| exchange or symbol class (ETFs, ADRs, funds, SPAC shells, preferreds) | such instruments have no GICS-like sector at all — legitimately absent, not "missing" | none (vendor behaviour not sampled) | UNPROVEN, likely structural |
| newly added universe members | no `daily_fundamentals` row yet, so no row inside the 30-day window | none | UNPROVEN |
| stale fundamentals groups | the updater refreshes in batches; a group skipped for >30 days falls out of the window together | none; follows from [CODE] (30-day window, no row on empty response) | MECHANISM EXISTS, magnitude UNKNOWN |
| sector change / reclassification | a re-keyed symbol may have a gap or a conflict between two classifications | none | UNPROVEN, handled by design in Part 2 |

What the lab **can** show [SYNTHETIC]: if the missing symbols were also the lower-return ones (planted: the ten highest-numbered "smallest" symbols,
return −0.02), then *all data checks pass* while the complete-case sample (rows with a usable sector-relative value) is shifted by about +0.0019 (-0.00166 to +0.00022)
in mean directional return, versus a shift of roughly +0.0001 (-0.00166 to -0.00157) when the same 10% is missing at random. That is a statement about the **gate's
blindness to correlated missingness**, not about production.

What **cannot yet be known** and the production observation that would answer it (read-only, aggregate; **not performed**; the frozen Windows
development database would need explicit owner authorization, and the VPS must not be queried for it in this slice):

1. Cross-tabulate `sector IS NULL` (latest row per symbol) against market cap, exchange, listing age, quote type, fundamentals-row age.
2. Per-session and per-symbol concentration of NULL / stale sector over the last N sessions.
3. The empirical rate at which the vendor returns a non-empty response without a sector, versus returns nothing.
4. How often a symbol's sector value actually changes between refreshes.

Until these exist, the correct position is: **coverage is necessary information, not sufficient information**. A passed floor means "enough rows",
not "unbiased rows".

### 1.4 Recommendation on a future sector-relative threshold

* **Architectural reasoning [REASONING].** A threshold on usable sector-relative coverage would encode an assumption about what a *consumer*
  needs. Different consumers need different things: a sector-neutral momentum feature needs high coverage; a market-relative-only model does not
  use the feature at all; an ablation study may *want* the gap. A global fatal threshold would either be too strict for the third kind or too
  loose for the first. It would also have to be chosen with no data about the shape of real missingness.
* **Empirical evidence [SYNTHETIC only].** The simulations show the figure is *informative* (it diverges from the floor) and that the floor cannot
  see concentration. They do not and cannot show what a "good enough" level is.
* **Recommendation.** Keep it **informational globally** (as in Slice 8). Add three things that are reporting, not gating: (a) report it next to the
  floor in readiness/status (already present); (b) add a **concentration diagnostic** (symbols fully affected, sessions fully affected, share of gaps in the worst 10 symbols/sessions)
  because the count figure hides it; (c) let an experiment **declare its own minimum** usable sector-relative coverage in its pre-registration,
  checked by the experiment runner, never by the dataset readiness. Do not pick a number now. Revisit when forward data exists and item 1–4 above
  have been observed.

### 1.5 The 90% floor

* **Architectural reasoning [REASONING].** The floor's job is *research integrity*, not feature selection: it refuses to publish a dataset whose
  point-in-time context is mostly unobserved. Lowering it because the source is weak would convert a data-quality signal into noise and make the
  framework more permissive exactly when evidence is weakest. Keeping it is the conservative choice and costs only datasets that would have been
  mostly reconstruction anyway. It is also inclusive-at-90, which is a defined, testable edge (pinned).
* **Empirical evidence [SYNTHETIC only].** The edge behaves exactly as specified (90% passes, 89% fails). Nothing in the lab shows that 90% is the
  *right* number; it is a **policy constant**, like the 30-day window below. Its virtue is not that it is validated but that it is strict,
  explicit and reported.
* **Known weakness, not a reason to lower it:** it is count-based and so cannot see clustering or composition (§1.2, §1.3). The remedy is more
  diagnostics beside it, not a lower number.

### 1.6 The 30-day freshness rule

`SECTOR_MAX_AGE_DAYS = 30` equals `SECTOR_WINDOW_DAYS = 30` ([CODE] `research/repository.py:19`), which mirrors the screener's own join
(`multi_timeframe_screener.py` ~700–720; its only comment is "Get recent fundamental data"). A search of the working tree and of `git log -S`
found **no derivation, benchmark or discussion** — only the commits that introduced the constant. **It is an operational policy constant, not
validated truth.** In particular: nothing shows that a sector classification goes stale after 30 days (sector classifications change rarely —
that is an [UNKNOWN] to be measured), and the 30 days came from "how recent must the fundamentals row be for the screener's other columns"
rather than from sector dynamics. The history design below therefore stores the **observation and its age** and applies the window at **read
time**, so the constant can be changed or per-experiment-declared without rewriting any history.

---------------------------------------------------------------------------------------------------------------------------------------------

## Part 2 — Append-only sector observation history (design only; nothing implemented)

### 2.1 The question the history must answer

> What sector classification did the system actually **know** for symbol X at decision time T, and what evidence supports that claim?

Two facts must never be merged:

* **"We first knew this classification at T"** (`observed_at`, our clock) — the only one a point-in-time dataset may use.
* **"It became economically true at T"** — unknowable from yfinance, which returns the *current* classification with no trustworthy as-of. We
  never claim it. The vendor's own as-of (if any) is stored for audit and **never** used for selection.

### 2.2 Why today's sources cannot serve

[CODE] `daily_fundamentals` is written by `fundamentals_updater.py` as a row dated `datetime.now().date()` with
`ON CONFLICT (symbol, date) DO UPDATE SET sector = EXCLUDED.sector`. That is: **mutable** (a same-day refresh rewrites the value), **naive-time**
(`datetime.now()`), **silent on failure** (an empty or <5-key vendor response writes *no row*, indistinguishable from "never polled"), and it
cannot say "the vendor explicitly returned no sector" other than as a NULL row that is also what an update would leave. The history design must not
depend on it being historically correct, so it is **not** the evidence source; it remains the screener's operational input and a discrepancy
cross-check only.

### 2.3 Architecture (mirrors migration 27 — an existing, reviewed precedent)

Three tables, same pattern as `source_observation` / `source_poll` (hash chain, DB-stamped observed time, immutable by role *and* trigger, no
backfill path):

1. **`sector_observation`** — one row per **change** (or first observation), per symbol, hash-chained.
2. **`sector_poll`** — one row per **refresh attempt or per run**, recording the *outcome*, including failure and "vendor returned no sector".
3. **`sector_reconstruction`** — a **separate** table for imported / reconstructed history. Never the same table, never satisfies forward coverage.

Splitting change-rows from attempt-rows is deliberate: a refresh that returns the same value must not create a new "classification" row (it
would make it look like the classification changed), yet "we checked again on day D and it was still X" is real evidence of **currency**.

### 2.4 The seven states — how each is represented

| State | Representation | Derived or stored |
|---|---|---|
| **fresh observation** | latest `sector_observation` for the symbol, and (`T − observed_at` ≤ max-age, or a later `sector_poll` confirming it within max-age) | derived at read time |
| **stale observation** | same, but neither its `observed_at` nor a confirming poll is within max-age | derived at read time |
| **refresh with same value** | a `sector_poll` row (`outcome = confirmed_same`) referencing the chain head it confirmed; **no** new observation row | stored (poll) |
| **refresh with changed value** | a new `sector_observation` row: `seq+1`, `prev_value_hash` = old head, `change_kind = changed`; the old row is untouched and stays selectable for any earlier T | stored (observation) + poll row |
| **failed refresh** | a `sector_poll` row (`outcome = failed`, reason code, no value). Does **not** end the previous observation; it simply fails to renew its currency | stored (poll) |
| **explicit vendor no-sector response** | a `sector_observation` row whose `sector` is NULL and `no_sector_reason = vendor_returned_none` (first time, or a *change* from a value to none); repeats are `confirmed_same` polls | stored |
| **no observation at all** | no row for the symbol at/before the decision cutoff | derived (absence) |

Freshness (fresh/stale) is **never stored** because it depends on T and on the window, which is a policy constant that must remain changeable.

Important consequence: *failed refresh* and *explicit no-sector* are different rows with different meanings. Today both collapse into "no
row / NULL"; the dataset layer currently maps both to `no_sector`, which is correct for Option B but loses *why*.

### 2.5 Selection at decision time T, tie-breaking and conflicts

For a symbol and decision cutoff `T` (the existing rule `is_known(avail, t0, grace, cutoff)`, grace = 1 day, is reused unchanged — `avail` is
`observed_at`):

1. Candidates = observation rows with `observed_at ≤ cutoff` and `observed_at < decision_deadline(t0, grace)`.
2. Select the row with the **highest `seq`** among candidates (the chain order, not the timestamp, is authoritative; `observed_at` is
   monotone within a chain by construction).
3. A **later** classification never rewrites earlier knowledge: a dataset built for an earlier T selects the earlier row. A Tech → Health Care
   reclassification observed on day D is visible only to decision times ≥ D (+ grace). Training sessions before D keep "Tech", exactly as the
   system believed then.
4. **Chain integrity is checked at selection**: `prev_value_hash` must equal the previous row's `value_hash` and `seq` must be gapless. A broken
   chain → the symbol's sector is `sector_identity_conflict` (existing cell state), the symbol stays (Option B), and the dataset reports it.
5. **Tie-breaking.** Within a chain there are no ties (unique `(symbol, seq)`). A duplicate-writer race that produced two rows with the same `seq`
   is prevented by the unique key; the loser's transaction fails and is recorded as a failed poll, it does not create a second head.
6. **Conflicting evidence from different sources** (e.g. a future second vendor): the table is keyed per `(symbol, source)`; selection across
   sources is **not** automatic. Two sources disagreeing at T → `sector_identity_conflict`, **fail closed to NULL sector-relative**, never
   "pick the newer" or "pick the majority".
7. **Currency** of the selected row = fresh/stale against max-age using the latest confirming poll, computed at read time.

### 2.6 Proposed schema (specification only, no SQL; **future migration number: 31 — reserved in this document, NOT consumed**)

Verified this slice: the docker-compose init list ends at `30_add_dataset_experiment_registry_tables.sql`, and no worktree or branch claims 31.
The number must be re-checked when the migration is actually written.

**`sector_observation`** (append-only change log)

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL | surrogate, PK |
| `symbol` | TEXT NOT NULL | same normalisation as the existing universe |
| `source` | TEXT NOT NULL | vendor identifier, e.g. `yfinance`; part of the chain key |
| `seq` | INTEGER NOT NULL | 1.. per `(symbol, source)`, gapless |
| `sector` | TEXT NULL | normalised value; NULL only with `no_sector_reason` |
| `sector_raw` | TEXT NULL | exactly what the vendor returned, before normalisation |
| `no_sector_reason` | TEXT NULL | e.g. `vendor_returned_none`, `vendor_field_absent`; NULL when `sector` is set |
| `change_kind` | TEXT NOT NULL | `first`, `changed`, `became_none`, `became_set` |
| `observed_at` | TIMESTAMPTZ NOT NULL | DB-stamped by a BEFORE INSERT trigger (`clock_timestamp()`), caller value ignored |
| `effective_session` | DATE NOT NULL | the first trading session on which a decision may use it (derived from `observed_at` and the session calendar; stored for audit) |
| `source_asof` | TIMESTAMPTZ NULL | vendor-provided as-of if any; **audit only, never selected on** |
| `provenance` | TEXT NOT NULL | `observed_forward` only (CHECK); reconstructed history lives elsewhere |
| `raw_payload_hash` | TEXT NOT NULL | hash of the raw vendor response, so a claim can be tied to bytes |
| `raw_payload` | JSONB NULL | bounded, optional; may be a minimal projection (sector/industry fields), not the whole response |
| `writer` / `code_ref` | TEXT NOT NULL | writing component and git SHA, as the other research tables do |
| `prev_value_hash` | TEXT NULL | NULL iff `seq = 1` |
| `value_hash` | TEXT NOT NULL | hash over (symbol, source, seq, sector, no_sector_reason, prev_value_hash) |

Constraints: UNIQUE `(symbol, source, seq)`; UNIQUE `(symbol, source, value_hash)`; CHECK `(sector IS NOT NULL) <> (no_sector_reason IS NOT NULL)`;
CHECK `(seq = 1) = (prev_value_hash IS NULL)`; CHECK `provenance = 'observed_forward'`; CHECK `change_kind` in the enumerated set; CHECK
`observed_at` not before `source_asof` is **not** imposed (vendor clocks are untrusted). Indexes: `(symbol, source, seq DESC)` for "chain head",
`(symbol, observed_at)` for "known by cutoff", `(effective_session)` for coverage reporting.

**`sector_poll`** (attempt / currency evidence)

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL | PK |
| `run_id` | TEXT NOT NULL | identifies the updater run |
| `symbol`, `source` | TEXT NOT NULL | |
| `attempted_at` | TIMESTAMPTZ NOT NULL | DB-stamped by trigger |
| `outcome` | TEXT NOT NULL | `confirmed_same`, `changed` (points to the new observation), `no_sector`, `failed` |
| `failure_reason` | TEXT NULL | coded, no free-text vendor error that might carry secrets; required iff `outcome = failed` |
| `observation_id` | BIGINT NULL | FK to the observation confirmed or created; NULL for `failed` |
| `raw_payload_hash` | TEXT NULL | |
| `code_ref` | TEXT NOT NULL | |

Constraints: CHECK on `outcome`/`failure_reason`/`observation_id` coherence; UNIQUE `(run_id, symbol, source)` so a retried run cannot double-record.
Per-symbol polls are a high-volume table (≈ universe × refresh frequency); see retention.

**`sector_reconstruction`** (imported / reconstructed history — separate table)

Columns: `symbol`, `sector`, `reconstructed_from` (e.g. `daily_fundamentals:<id>`), `row_date`, `imported_at` (DB-stamped), `import_batch`,
`method`, `provenance` (CHECK = `reconstructed`). It has **no** `effective_session` selection role and is **never** read by the forward-coverage
code path. It exists so a researcher can opt in, visibly, to a reconstructed training window.

**Immutability (same two layers as migration 27)**

1. The runtime role gets **SELECT + INSERT only** on all three tables (plus sequence USAGE/SELECT/UPDATE). No UPDATE, DELETE, TRUNCATE. Added to
   `deploy/db/research_roles.sql` append-only list and to `research_roles_verify.sql`.
2. `ENABLE ALWAYS` BEFORE UPDATE / DELETE row triggers and a BEFORE TRUNCATE trigger that call the existing guard function and raise, so even a
   privileged session that merely has the table privilege cannot edit history by accident. A BEFORE INSERT trigger stamps `observed_at` /
   `attempted_at` with `clock_timestamp()` and validates the chain link (`seq = head.seq + 1`, `prev_value_hash = head.value_hash`).
3. A collision-guard section with a marker COMMENT (as in 27) so a re-application cannot silently replace the table.
4. **No maintenance hatch.** A genuinely wrong observation is *superseded* by a later one (and, if needed, a documented `correction` change kind
   with a reference), never deleted.

**Retention.** `sector_observation` and `sector_reconstruction`: forever (tiny: one row per symbol per *change*). `sector_poll`: the confirming
evidence for the *latest* confirmation per symbol must be retained forever; older confirmations may be summarised into per-run counts after a
long, explicitly owner-set period — but only through a dated, reviewed maintenance migration, never by the runtime role. Default: keep everything;
the volume (≈ 3k rows per refresh run) is small.

**Expected query patterns.** (1) "chain head per symbol at cutoff" (indexed range + `DISTINCT ON`); (2) "latest confirming poll per symbol at
cutoff" (index `(symbol, source, attempted_at DESC)` to add); (3) coverage by session (aggregate on `effective_session`); (4) chain-integrity scan
(window function over `seq`/`prev_value_hash`); (5) audit join to `daily_fundamentals` for discrepancies. All are read-only and bounded by the
decision cutoff.

### 2.7 Writer design — where the forward sector observation happens

Candidates and trade-offs:

| Candidate | For | Against |
|---|---|---|
| **A. The fundamentals updater, at the moment it receives the vendor response** | the only place the raw vendor response is in hand (so `raw_payload_hash` is genuine); single write per symbol per refresh; naturally covers explicit-no-sector and failure; no additional vendor calls | couples a research-integrity write to the production updater (a production-path change needing its own review, tests, deploy); an exception in the history write must not break fundamentals ingestion |
| B. A post-step recorder reading `daily_fundamentals` afterwards | decoupled from the updater | records what the *mutable table* says, not what the vendor said; loses failures and raw payload; same-day overwrites already happened — it re-creates the problem it is meant to remove |
| C. Candidate capture (the forward collector) | already point-in-time aware; only candidate symbols | bounded to candidates (the very limitation being removed); a symbol not a candidate today has no history when it becomes one; couples sector history to capture activation |
| D. The Market Intelligence observation | already has `source_observation` machinery | MI is deliberately strategy-neutral and unenabled (migrations 24/25 not applied); `test_import_separation` forbids lab code importing it; would make MI a prerequisite of the lab |

**Recommendation: A — one authoritative writer, embedded in the fundamentals updater, in the same process that receives the vendor response.**
Rules that make it safe:

* A single function (`record_sector_observation(...)`) is the **only** code allowed to write the three tables; a test (extending the existing
  writer-name guard) fails if any other module references them, so no second writer can create a competing history.
* It runs inside a **SAVEPOINT** after the vendor response is parsed and before/alongside the existing `daily_fundamentals` upsert. A failure
  rolls back only the savepoint, increments a counter, and **never** aborts the fundamentals write. (The failure itself is then represented by the
  next successful poll or by an absence the dataset treats as `no sector`; if even the failed-poll insert fails, the symbol simply has a stale
  or missing observation — the safe direction.)
* It never reads `daily_fundamentals` and never uses the lab's sector fetch; the two stay independent so they can be cross-checked.
* Contract text and code must not reference the Market Intelligence runner (existing guard) and the collector must not import the writer.
* The writer is dormant behind an explicit flag until the owner activates it, exactly like capture.

### 2.8 Dataset integration — a later slice, with the current protection kept

Today's evidence is candidate-bounded (`candidate_sector_evidence`, `fetch_sectors` from `daily_fundamentals`). The migration to the history is
staged so protection never drops:

1. **Cross-check stage (shadow).** Assembler, audit and collector verify read **both** sources. For each (symbol, T): if they agree → proceed; if
   the history is *less fresh or absent* → the **safer source wins**, i.e. the cell is the more conservative state (NULL sector-relative with
   the corresponding state); if they **disagree on the value** → `sector_identity_conflict`, fail closed, and the discrepancy is counted and
   reported. The history can *tighten* a cell but not *loosen* one during transition.
2. **Per-consumer changes:** *assembler* — selects by §2.5 and labels provenance `observed_history`; *audit* — adds chain-integrity and
   discrepancy counts; *readiness* — adds `sector_history_effective_from` (the first session from which forward history exists) and counts
   history-backed rows separately from candidate-backed rows (the 90% floor continues to apply to the union rule "the safer source"); *research
   status* — reports chain integrity, poll success rate, coverage by session; *collector verify* — re-derives the selected observation from the
   chain and requires it to match what was captured; *restatement report* — lists symbols whose sector changed after capture, so a restated
   classification is visible as a *new* observation rather than silently changing old rows.
3. **Cut-over** only when the discrepancy count has been zero (or fully explained) over an owner-chosen window. The old path is then kept as the
   cross-check, not removed.
4. **Schema versioning:** a `lab_dataset_v3` / readiness v3 only when selection actually changes; the cross-check stage is additive diagnostics
   and does not bump the schema.

### 2.9 Historical data — never "observed"

* No backfill writes to `sector_observation`; `provenance` is CHECK-constrained to `observed_forward`.
* Imported `daily_fundamentals` goes only to `sector_reconstruction` with `provenance = reconstructed` (existing evidence class
  `RECONSTRUCTED`). It is **excluded** from the forward-observed coverage and from the 90% floor numerator. A researcher may opt in to a
  reconstructed training window; the dataset labels every such cell `sector_reconstructed` and the readiness reports it separately.
* The 165 + 1022 historical production rows referenced elsewhere in the project are unrelated to this table and are untouched.

---------------------------------------------------------------------------------------------------------------------------------------------

## Part 3 — Activation impact

### 3.1 Can Slice 8, as designed, safely start collecting useful forward history without the new table? — **Yes, with a bounded loss.**

[CODE/REASONING] Capture stores, per candidate and session, the sector evidence the dataset layer later uses (candidate-bounded, classified as
`OBSERVED_FRESH` / `OBSERVED_STALE` with the source row's date). It is point-in-time safe: it records what `daily_fundamentals` said *at capture
time*, stamped by the DB. It fails closed (Option B) when it cannot support a claim. So activating now yields **safe** data; nothing it produces is
wrong. What it does **not** yield is *why* a sector was absent, *when it changed*, or *what happened to symbols that were not candidates*.

### 3.2 Would activating now create significant debt? — **Moderate, concentrated in one place.**

The debt is not wrongness but **irrecoverable resolution**:

### 3.3 What is permanently lost if we activate first and build the history later?

1. **Reclassification timing for non-candidates.** Sector for a symbol is only recorded while it is a candidate; when it later becomes one, its
   earlier classification is gone. There is no way to know what it was classified as before the first candidate day.
2. **The first-known time for every symbol.** The history's `observed_at` for a symbol is the day it is first recorded; recording starts later
   than the first refresh, so "first known at" is permanently later than reality for everything before the writer exists.
3. **The raw vendor response** (`raw_payload_hash`/payload) for the period before the writer — claims cannot be tied to bytes.
4. **Failed-refresh and explicit-no-sector distinctions** — today both look like a missing/NULL row. The share of "vendor said none" vs "vendor
   failed" for the pre-writer period is unrecoverable, and it is exactly the missingness-bias evidence in §1.3.
5. **Same-day overwrites.** `ON CONFLICT DO UPDATE` already destroys intra-day changes; every day before the writer is another day of such loss.
6. **A clean `sector_history_effective_from`.** Without an explicit marker the first forward sessions look the same as later ones; the marker
   (and its cut-over discrepancy window) is what makes the transition auditable.

Items 1, 2 and 4 are not recoverable by any later import; reconstruction can only produce `reconstructed` rows, never `observed` ones.

### 3.4 Owner recommendation

| Option | Trade-off |
|---|---|
| **1. Implement the history first, then activate collection** | Cleanest forward record, no permanent loss. Costs: migration 31 + production-updater change + a deploy, delaying collection. Appropriate **if collection is not time-critical**. |
| **2. Activate collection now, implement history as a fast follow, recorded with a marker** | Starts accumulating candidate-bounded evidence immediately and safely. Loses items 1–6 above for the gap period only. Acceptable **if** the gap is short and the marker `sector_history_effective_from` is set when the writer goes live. |
| 3. Activate and defer indefinitely | Not recommended: the loss in §3.3 grows daily, and the missingness evidence in §1.3 is never gathered. |

**Recommendation: Option 1 if the owner is not under a time constraint; otherwise Option 2 with the writer as the very next slice.** The writer is
small relative to the dataset layer (one function, three tables, a guard), and most of the permanent loss is in the *updater-boundary* data that
only the writer can capture. Either way: do not label any pre-writer period "observed history".

### 3.5 Explicit answers

1. **Implement history BEFORE activating, or can it follow?** It *can* safely follow (§3.1) — activation produces safe, fail-closed data. It
   *should* come first only if the owner values complete first-known-time/raw-evidence over starting the clock now. Recommendation: first if
   not time-critical, else immediately after with a marker.
2. **Information permanently lost** — §3.3 items 1–6.
3. **Does the 90% floor still make sense?** Architecturally **yes** (conservative research-integrity gate, strict, explicit, inclusive edge). Empirically
   it is **unvalidated** — [SYNTHETIC] only: it behaves as specified and is blind to concentration/composition (§1.2, §1.3). Keep it; add
   diagnostics beside it; do not lower it.
4. **Should usable sector-relative coverage get its own threshold?** Architecturally **not globally** — consumers differ and the right level is
   unknowable without forward data. Empirically **nothing yet** supports any number. Keep it informational; add concentration diagnostics; allow an
   experiment to declare its own minimum at pre-registration. Revisit after forward data and the §1.3 production observations exist.

---------------------------------------------------------------------------------------------------------------------------------------------

## The 23 unexplained skips from the Slice 8 combined DB run

* **Cause (evidence: `scratchpad/pgdisp2.log`, test output).** The disposable Postgres logged `FATAL: out of memory` at 10:29:35 — host memory
  pressure — 14 s after pytest started. `shared.database` builds its singleton connection pool **once, at import**. That one-shot init failed, the pool
  stayed `None`, and two screener test files (`test_signal_ledger_writer.py`, `test_evaluate_signal_ledger.py`) had a broad `except` around their
  DB helper that converted the resulting `RuntimeError` into a **skip**. Category: environment (host memory) × DB lifecycle (one-shot pool) ×
  test isolation (a failure mode silently becoming a skip). Not connection exhaustion, not schema, not role/permission state, not test ordering.
* **Reproducible?** Not on demand (host memory pressure is not reproducible). It **is** reproducible *mechanically* by forcing `db.sync_pool = None`
  — done in a new test.
* **Fix (test infra only, no screener behaviour change).** The two helpers now retry pool initialisation once
  (`if db.sync_pool is None: db.initialize_sync_pool()`) before deciding the database is unavailable; `test_db_pool_recovery.py` fails without
  the retry and passes with it. Screeners suite locally: 166 passed, 0 skipped.
* **Why it did not hide a failure:** the real CI treats any skip as a failure, and the CI run for the Slice 8 commit was green with 0 skips.

---------------------------------------------------------------------------------------------------------------------------------------------

## Known limitations of this slice

* Part 1 is a synthetic laboratory: it demonstrates the behaviour of the gate and the information a consumer receives, not the properties of real vendor data.
* The 30-day window is documented as an operational constant but its *appropriateness* is unmeasured.
* No real vendor sector-response behaviour (none / absent / changed) has been observed; §1.3 rows are hypotheses.
* The schema is a specification: constraint details (e.g. the exact chain-link trigger, the session-calendar derivation of `effective_session`) must be re-verified when the migration is written.
* The writer lives in a production updater; its change would need its own production-path review, which this slice does not do.

## Technical debt carried forward

* The updater's silent-on-failure and same-day-overwrite behaviour (cannot be repaired without the writer).
* The floor's blindness to concentration/composition (diagnostics proposed, not implemented).
* Two sector sources (candidate-bounded evidence and `daily_fundamentals`) until the cross-check stage lands.

## Slice 10 recommendation

**Slice 10 = concentration/missingness diagnostics (reporting only) in readiness/status**, which needs no migration and addresses the one
weakness this slice found in the existing gate; **or**, if the owner approves the Part 2 design, **the sector-history migration (31) + the single writer
behind a flag**, on the lab branch only, with the production-path updater change reviewed separately. Owner chooses; neither starts without approval.
