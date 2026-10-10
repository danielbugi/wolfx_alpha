# First Light Algo — SEC EDGAR event contract (Slice 2)

Status: contract + a small tested module (`mechanism/market_intelligence/filing_contract.py`, 45 tests in `tests/test_filing_contract.py`).
**There is no parser, no client, no scheduler, no network call and no database write.** Not merged, not applied to production. It rides on
migration 25's event model, which is itself unapplied in production.

## 1. The chain

```
filing ──► timestamp ──► company / identifier ──► filing type ──► source / provenance ──► FACTUAL event record ──► (optional, later) catalyst classification
 EDGAR      SEC accepted_at    CIK (not a ticker)      form + items       sec_edgar, accession    FilingFact → market_event      versioned, separate, supersedable
```

Left of the arrow = **fact** (what the filing says about itself). Right = **interpretation** (what we or a model think it means). They never share
a row and the second can never write to the first.

## 2. The fact (`FilingFact`)

| Field | Origin | Rule |
|---|---|---|
| `accession` | source | `NNNNNNNNNN-YY-NNNNNN`; the filing's identity and the event's `source_ref`; malformed → refused |
| `cik` | source | positive integer; the **only** identity a fact asserts |
| `form_type` | source, verbatim | e.g. `8-K`, `8-K/A`, `10-Q`; an unlisted form is still a valid fact (it just has no `form_family`) |
| `filing_date` | source | the date EDGAR assigns (a filing accepted after the cut-off carries the next business day — it is **not** the acceptance date) |
| `accepted_at` | source | the SEC acceptance timestamp. Must be timezone-aware; stored UTC; **never defaulted, never inferred, never rounded** |
| `items` | source, verbatim | 8-K/6-K item numbers (`2.02`, `9.01`); each must look like an item number |
| `report_period`, `primary_document` | source | optional |
| `amends_accession` | source | only on an amendment form; a filing cannot amend itself |

`build_fact(raw)` raises on a missing `accession`/`cik`/`form_type`/`filing_date`/`accepted_at`: a fact is never built from a partial record. A
fact is a frozen dataclass with a stable `content_hash()` (sha-256 of canonical JSON) — that hash is how a classification pins what it read.

**What the AI may not do.** Nothing that produces a fact accepts a model's output: timestamps, filing types, financial values, company identity and
every other factual field are copied from the source or the record is refused. A model, a heuristic or a human may only produce a
**classification** (§5). `test_a_classification_has_no_field_to_rewrite_a_fact` and `test_a_classification_cannot_smuggle_a_fact_through_evidence`
pin this structurally: the classification type has no field named for a fact, and its `evidence` may cite only `items` / `form_type` (copies of
the fact's own values).

## 3. Company / identifier

A fact carries a **CIK**, never a ticker. Symbols are reused, renamed and delisted, and the SEC's public ticker file describes the present only.
So the CIK → symbol link is a separate record (`IdentifierLink`) with `valid_from` / `valid_to` and a basis:

* `observed` — we captured the mapping on a known date and kept it.
* `reconstructed` — today's mapping projected backwards. Reported as such, never upgraded.

`resolve_symbol(fact, links)` returns `(symbol, basis)`, `(None, "unmapped")` or `(None, "ambiguous")`. Resolution uses the **filing date**, so a
symbol reused by another company resolves correctly. An unmapped fact is still a valid fact; it is simply not attached to a tradable symbol.

## 4. Timestamp, session timing and point-in-time semantics

`to_event_draft(fact, symbol, symbol_basis, trading_sessions)` produces a migration-25 `EventDraft`:

| M25 field | Value |
|---|---|
| `event_key` | `sec\|<accession>` (stable; a filing is one event) |
| `event_type` | `regulatory` — the fact asserts **no meaning** (not `earnings_reported`; that is an interpretation) |
| `status` | `reported` |
| `published_at` / `known_at` | the SEC `accepted_at`; `known_at_basis = vendor_published` |
| `pit_grade` | `A` — a proven source time, and a filing is never edited (an amendment is a new accession) |
| `session_timing` | derived from `accepted_at` in New York time: BMO < 09:30, INTRADAY 09:30–16:00, AMC ≥ 16:00; `UNKNOWN` on a weekend or outside a supplied session calendar (never rolled forward). DST-aware. |
| `event_time` | the **New York** date of acceptance (not the UTC date) |
| `eps_*`, `revenue_*` | all `NULL` — a filing fact carries no financial value (XBRL values are a separate fact type, §6) |
| `payload` | `fact_hash`, `cik`, `form_type`, `form_family`, `filing_date`, `items`, `amends_accession`, `symbol_basis` |
| `provenance` | `observed` for live capture; a historical backfill is `reconstructed` with a stated basis and stays out of the ML view |

Caveats a consumer must respect (all documented, none hidden):

1. **Timing is of the filing, not the announcement.** An earnings press release usually precedes its 8-K. `accepted_at` is therefore a *late*
   bound on public knowledge: using it can only make a feature later than reality, never earlier — the safe direction for leakage.
2. **Acceptance ≠ dissemination.** EDGAR may make a filing public some time after acceptance. A feature builder must apply and **record** an
   availability lag. Once live capture exists, our own `ingested_at` (database-stamped) is a hard ceiling on when we knew; measuring the gap
   between acceptance and first-seen is the way to size the lag from evidence rather than assumption. This contract deliberately does not invent
   a number.
3. **Early-close sessions** are not modelled by the bucketing (16:00 is used); pass the session calendar and extend it when this matters.
4. **Backfilled history is not observed.** EDGAR's archive lets us reconstruct *what was filed and when* very reliably, but "we would have seen it
   then" is not the same claim as "it was filed then"; reconstructed rows carry that distinction and are excluded from models by default.

## 5. Interpretation (`CatalystClassification`) — optional, later, versioned

| Field | Meaning |
|---|---|
| `fact_accession`, `fact_hash` | exactly which fact, and the exact content that was read; `matches(fact)` is false if the fact differs |
| `classifier`, `classifier_version` | a new version is a **new row**, never an edit |
| `method` | `rule` (deterministic, no confidence) / `model` / `human` |
| `label` | the classifier version's own vocabulary (e.g. `earnings_release`) |
| `evidence` | citations of the fact's own `items` / `form_type` only |
| `confidence` | `[0,1]`, only for `model`/`human` |
| `supersedes` | id of the classification this replaces; the older one remains |

The module ships one example rule, `classify_by_items` (`items_rule` v1): an 8-K listing Item 2.02 → `earnings_release`. It reads only the
fact's own `items`; it reads no text and estimates nothing. A text-reading or model classifier would be a different `classifier`/version, would
cite only the fact, and any number it extracted (e.g. a guidance range) would be a **separate, human- or parser-verified fact**, never a field of
a classification.

Storage of classifications needs its own append-only table (registry item 3; **no migration number is allocated here** — numbers are assigned in
the commit that adds the SQL, per `MIGRATION_REGISTRY.md`). Until then the type is a design with tests, not persisted.

## 6. Not in this contract (on purpose)

* The HTTP client, rate limiting and the User-Agent contact string (an owner decision; the SEC requires a descriptive contact).
* XBRL **numeric facts** (revenue, EPS, shares) as a second fact type with `filed` time, taxonomy tag, unit, period and a GAAP/adjusted basis; they
  follow the same fact/interpretation split and map to M25's estimate/actual columns.
* Full-text parsing of press releases. A parser is a *fact extractor* only if deterministic and tested against filings; anything probabilistic is
  a classification.
* Scheduling and production activation.

## 7. Failure modes the contract is built against

| Risk | Defence |
|---|---|
| A model "fills in" a missing timestamp or form type | `build_fact` has no default and no inference path; missing → refused |
| A reclassification rewrites history | classification is a separate immutable record that pins `fact_hash`; supersession by reference |
| Local-time ambiguity (UTC vs New York dates, DST) | acceptance stored as UTC; event date and timing computed in `America/New_York`; DST tested |
| Symbol reuse / change | CIK identity; validity-windowed links; resolution by filing date; `ambiguous`/`unmapped` are explicit |
| An amendment treated as an edit | new accession, `amends_accession` link, original untouched |
| Backfilled history masquerading as live | provenance `reconstructed` + basis; `ml_view` excludes it |
| Feature uses a filing before it was public | `known_at` = acceptance; availability lag recorded by the feature builder; `visible_as_of` / `ml_view` gate |

## 8. What CI must prove

`mechanism/market_intelligence/tests/test_filing_contract.py` is pure (no database) and runs in the real-Postgres job with the rest of the
package. It needs the host `tzdata` (present on the CI runner; on Windows the `tzdata` package is required for `zoneinfo`).
