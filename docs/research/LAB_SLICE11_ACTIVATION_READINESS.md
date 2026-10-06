# Lab Slice 11 — Production activation readiness and real-source validation (NO ACTIVATION)

> **Slice 12 update (2026-10-06):** terminology corrected (a no-sector state is *inferred* from `quoteType`, never asserted by the vendor), the dotted-symbol
> defect is fixed at the vendor boundary, the production `DATA_PROVIDER` is `tiingo`, and §7 is superseded by the evidence-updated runbook and the gate
> classification in `LAB_SLICE12_PRODUCTION_READINESS.md`.

Branch `lab/first-light-algo`, draft PR #1 (CI only, DO NOT MERGE). Nothing here was applied, enabled, installed, deployed or merged. Production and
the S11 passive validation were not touched. No production read was made (see §10 for the reads that are *requested*, not performed).

Evidence classes are kept apart everywhere: **LIVE_VENDOR** (a yfinance call made from the dev machine on 2026-10-06), **FROZEN_DEV** (read-only SELECTs on
the frozen Windows development database; not production), **SYNTHETIC** (the real code over hand-built inputs; documents policy, says nothing about the
world), **CODE_READ** (read from source, not exercised against the vendor).

---

## 1. Source map (Part 1)

| | **yfinance `Ticker.info`** | **Tiingo** (`fundamentals/<sym>/meta` via `get_fundamentals`) |
|---|---|---|
| Evidence | LIVE_VENDOR, 40 symbols, saved: `docs/research/evidence/yfinance_live_probe_2026-10-06.json` | CODE_READ only. Not probed: needs credentials, and the Power plan covers the Dow-30 only |
| Sector field | `sector` (11-name Yahoo taxonomy; the frozen dev DB stores exactly these 11, 0 outside it) | `sector` in the meta body |
| Missing representation | the `sector` key is **absent** (equities with no classification; all 8 live ETF/MF cases) | `None` / absent / blank |
| Empty-string / `"unknown"` | not observed live; treated as not-a-sector (never stored) | treated as ambiguous |
| Explicit "no sector" assertion | **none**. Vendor never says "this has no sector". What it does say, structurally, is `quoteType`: `ETF` / `MUTUALFUND` (live: 6 + 2) | none. A blank cannot be told from a failure |
| Request failure | exception (timeout / HTTP) → `request_failed` | `get_fundamentals` **swallows every error into `None`** → indistinguishable from "no data" |
| Unknown symbol | a **one-key** body (live: `ZZZZXQ`, `QQQQQQ9`) → `invalid_response / response_too_sparse` | `None` |
| Response timestamp | none in the body. We stamp `captured_at` ourselves | none relied on |
| Vendor as-of / effective timestamp | **none**. `source_asof` is always `None` | none |
| Operating company, `sector` absent | live: preferreds/notes (AFGB, DDT, DUKB, DTG) → `invalid_response / operating_company_sector_absent`, never no-sector | n/a |
| Dotted share classes | `BRK.B`, `BF.B` get **no sector**; `BRK-B`, `BF-B`, `BRK-A` return full sectors (LIVE_VENDOR). The updater has no dot→dash mapping. **Pre-existing coverage defect, not fixed here** (§9) | not probed |
| Current normalization | `sector_history_recorder.classify/yfinance_meta/yfinance_company_info`; one parser, shared by the writer and the dry run | `sector_history_recorder` records it as **diagnostic**; `tiingo_meta` None/blank/"unknown" → `invalid_response / ambiguous_source_none` |
| Ordering / priority | **authoritative** (the only source that carries sector identity) | **diagnostic only** — recorded on its own chain, never substituted, never admissible |
| Other reachable sources | none for sector. EDGAR SIC codes are a different taxonomy and are not mapped (no vendor-neutral mapping exists); not probed | |

### Non-mutating dry run
`sector_history_recorder.dry_run(symbol, source, *, raw=None, error=None, head=None)` — no database handle exists in its scope, and it calls the same
`classify` / `decide` the writer calls (no second parser). It returns: normalized outcome, proposed poll state (`chain_effect`), proposed observation
(`change_kind`, next `seq`), `source_identity`, `source_asof` (always None — the vendor supplies none), `raw_payload_hash` (only for answered outcomes),
`admissible_for_forward_history`, `rejection_reason`, and `writes: False`. CLI: `python mechanism/data_updaters/sector_history_recorder.py --fixture <json>` (offline) or `--live-yfinance SYM,SYM` (read-only network call). `test_sector_dry_run_live_fixture_pure.py` replays the 40 live responses: 30 `sector`, 6 ETF + 2 MUTUALFUND `no_sector`, 2 sparse
`invalid_response`; the dry-run outcome equals the production classifier on every case.

**Findings that change policy** (all in code now): (1) yfinance has no no-sector assertion, so the no-sector state is *inferred* by the recorder, and only from
`quoteType ∈ {ETF, MUTUALFUND}`; anything else without a sector is a coded `invalid_response`, never a no-sector. (2) A sparse body is a vendor answer
that supplies nothing — it counts as a *failure* for freshness verification (§4), not as accounted.

## 2. Authoritative source and fallback (Part 2)

Three distinct states are never merged: **provider failed** (`request_failed`) ≠ **provider answered with no usable sector** (`invalid_response`, coded)
≠ **no-sector state inferred from an approved `quoteType`** (`no_sector`, yfinance only; the vendor asserts nothing).

| Criterion | A: single authority, others diagnostic | B: ordered fallback | C: consensus, disagreement fails closed |
|---|---|---|---|
| PIT semantics | one chain, one taxonomy | a chain mixing vendors; a "change" may be a vendor switch, not a reclassification | needs both fresh every day |
| Inferred no-sector | yfinance only (quoteType evidence) | the fallback cannot express it → switching *manufactures* a sector or loses the no-sector | inherits the weaker source (Tiingo: never) |
| Outage | head ages; stale at 30 d; nothing invented | fills gaps, with identity from a different taxonomy | an outage of either source blocks everything |
| Source switching / return-to-primary | n/a | primary returns and disagrees: reclassification or a vendor artefact? **Unresolvable from the data** | n/a |
| Reproducibility | strongest: one vendor, one rule | depends on which vendor was up that day | strong but brittle |
| Operational complexity | lowest | highest (two writers, switch state) | medium |
| Coverage | lowest (Tiingo's Dow-30 plan adds almost nothing anyway) | highest | lowest |

**Recommendation: A.** `yfinance_info` owns forward sector identity; `tiingo_meta` is diagnostic. Coverage alone would favour B, but B's return-to-primary
ambiguity corrupts the one property the history exists to provide (a reclassification that is *real*). This is a **writer/reader policy only**: schema 31
(`sector_observation` / `sector_poll` / `sector_reconstruction`) is sufficient, the chain identity is unchanged, **migration 32 is not required**.
Enforced: the spec validator rejects any `sector_history.source` other than the authoritative one (`dataset_contract.AUTHORITATIVE_HISTORY_SOURCE`).

### The owner-mandated conflict rule (implemented)
History says (inferred) `no_sector` **and** the candidate evidence says a sector (fresh or stale) → relation `sector_no_sector_conflict`, effective state
UNAVAILABLE, the candidate's sector *name* is kept for display only, and the sector-relative feature is unavailable until the discrepancy is explained.
Neither source wins silently (`sector_history.R_NO_SECTOR_CONFLICT`; tested).

### Policy answers
* **Primary fails today, Tiingo has a sector.** Recorded: one `request_failed` poll on the **yfinance** chain (`chain_effect=none`: head neither erased nor
  refreshed). Tiingo's answer, if captured at all, lands only on its own diagnostic chain. Today's research may use the yfinance **head** while its
  currency (max of head capture and the latest eligible same-value confirmation) is ≤ 30 days (`SECTOR_MAX_AGE_DAYS`, operational and unvalidated);
  after that the evidence is STALE, then UNAVAILABLE. It never uses Tiingo's sector.
* **Primary returns tomorrow and disagrees with yesterday's Tiingo.** Nothing to reconcile: no fallback identity was ever recorded. If it disagrees with
  its *own* earlier head, that is an ordinary `changed` reclassification at tomorrow's `captured_at`; days before still resolve to the old sector.
* **Disagreement on the same day** (yfinance fresh vs Tiingo fresh differing) stays a diagnostic fact in the cross-check (`sector_identity_conflict`
  between two *fresh* sources), never an identity change.

## 3. Fundamentals → Slice 10 contract normalization (question 3)
Deterministic: the fundamentals updater's yfinance `info` body is the single input; `_fetch_yfinance_raw` → `_yfinance_sector_meta` → the recorder's
`classify`. The mapping is a total function of the body (+ `quoteType` + key count). Same body → same outcome, same `raw_payload_hash`. The updater remains
the **only** writer (guard `test_sector_history_single_writer.py`); the recorder is INSERT-only; a recorder failure is logged and counted, never raised into
fundamentals ingestion.

## 4. Collector dependency (Part 3, question 4)
`SOURCE_CONTRACT["sector_history"]["step"]` is no longer `None`: it is `sector_history_verify` (`STEP_SECTOR_HISTORY`), step 5 of the collector, no
dependency on another step, `verified_by_collector`. The collector **verifies** (read-only) that the fundamentals updater's recorder refreshed the
session's universe; it never writes sector history. Opt-in per run with `--with-sector-history-check`, which the proposed scheduler command carries from
day one.

* **What satisfies a session.** Window `[session − 4 days (MAX_REFRESH_GAP_DAYS), the decision deadline)`. A symbol is *accounted* if it has an authoritative
  poll in the window that is an answer: `sector`, `no_sector`, or an `invalid_response` that is **not** sparse (e.g. `operating_company_sector_absent`).
  Required: accounted share ≥ 90% (`MIN_ACCOUNTED_SHARE`, the existing coverage floor; **not** changed, no sector-specific threshold added).
* **Freshness interaction.** Same-value confirmation polls extend currency; so the dependency is met by the pipeline's step 5, which already refreshes
  fundamentals for the full active universe every trading day (verify on the VPS — §10). Required cadence: at least one fundamentals refresh per
  4-day window before the session; daily is what exists.
* **Outcomes.**

| Situation | Reason | Collector state |
|---|---|---|
| fundamentals did not run / recorder flag off | `refresh_absent` | INCOMPLETE until the decision deadline, then MISSED |
| ran, vendor failed (only `request_failed`) for most of the universe | `vendor_failure` | same |
| ran for part of the universe (> 10% never polled, or failed/sparse) | `partial_refresh` | same |
| any symbol's chain fails verification | `chain_broken` | permanent (never repaired by a re-run) |
| legitimate no-sector (ETF / fund) or operating-company-sector-absent | accounted | **does not** fail the session (Option B preserved) |
| tables absent / no universe | `sector_history_tables_absent` / `no_universe` | refused, not guessed |

  Availability (how many symbols *have* a usable sector) is **reported, not gated**.
* **Can the full stack converge naturally to Slice 5 readiness with no `step: None`?** Yes in architecture: no source has `step: None`, every enabled source
  has an implemented step, and the real preflight reports that structurally (§6). What remains is environmental/owner state (§6–§8), not a structural blocker.

## 5. Real-world coverage observations (Part 4) — `docs/research/evidence/sector_coverage_2026-10-06.json`
Observational only; no threshold changed; no sector-relative threshold created.

**FROZEN_DEV** (dev DB, last loaded price session 2026-09-23, 3,068 universe symbols; NOT production): latest stored sector — 3,043 available (99.2%),
24 null, 1 with no fundamentals row; 11 distinct sector values, 0 outside the yfinance taxonomy; 4 symbols with more than one distinct sector in their
history, 1 whose history mixes set and missing; the 3 dotted/slashed symbols (`BF.B`, `BRK.B`, `BRK/A`) all lack a stored sector.

**LIVE_VENDOR vs FROZEN_DEV** (40 live yfinance responses): of the 26 symbols present in both with a live sector, **26 agree, 0 disagree**; 14 are not in
the frozen universe (ETFs/funds, foreign ADRs, deliberately unknown symbols).

**Not measurable here** (needs live Tiingo credentials, not used): yfinance-only vs Tiingo-only vs both-agree vs both-disagree on the same symbols; the
frozen `sector` has no per-row vendor label so a vendor split cannot be derived from it.

**SYNTHETIC**: 16 (candidate × history) pairs through the real cross-check — documents the fail-closed matrix, including the owner's no-sector/Tech case.

## 6. Complete activation preflight (Part 5)
`python -m forward_collection activation --spec <spec> --runtime-role donchian_app [--gate NAME=yes …]`, read-only (read-only session; the module contains no
write verbs and never names a history table — both asserted by tests). Three separate answers:

* **architecture-ready** — dataset sources all have implemented collector steps; sector history has a real collector dependency; scheduler command verifies it;
  spec/collector versions match; scheduler design consistent; no-sector policy resolved; drafts present. **YES** (real repo).
* **environment-ready** — migrations 22 and 24–31 applied (catalog derived from the compose init list and the SQL files, not hand-kept); research tables
  guarded by ENABLE ALWAYS triggers; the runtime role exists with no privileged attributes and is least-privilege on every research table (SELECT+INSERT
  only on 24–31); fundamentals refresh recent. Proven **YES** in a faithful throwaway database (`full_env.py`: every compose migration + the real roles
  script under throwaway role names) as the least-privilege role in a read-only session; each layer has a test that breaks exactly one real thing and
  flips it to NO. **Real production: not performed** (§10).
* **activation-ready** — owner gates, each defaulting to NO and opened only by an explicit `yes`: `s11_passed`, `model_version_fix_applied`,
  `owner_activation_approved`, `backup_verified`, `authoritative_vendor_verified_live`, `mechanism_image_pinned`; plus the automatic gate
  `scheduler_units_committed` (a `.service` **and** `.timer` under `deploy/vps/donchian-forward-collection*`).

**"Forward research collection ready for activation" = YES only if all three are YES. Today: NO**, by design: the units are not committed and no gate is
open. Nothing is monkeypatched to produce a YES; the YES test builds a real schema.

Unresolved blockers: S11 pass (dormant, crons armed); the `model_version='unknown'` fix (isolated stage; 165 historical prod ledger rows); owner
activation approval; a verified backup; live vendor verification of the authoritative source from the VPS; committed scheduler units; the
production state of migrations 24–31 (24/25 are the Market Intelligence ones and need their own approval).

## 7. Activation runbook (DOCUMENT ONLY — not executed; no secrets in it)
Preconditions (all gates): S11 PASS declared; `model_version` fix shipped and verified; explicit owner approval *per step group*. Every step below is a
production mutation unless marked read-only.

| # | Action | Expected | Verification | Rollback |
|---|---|---|---|---|
| 1 | Backup/snapshot: `deploy/db/` backup tooling + a labelled pre-activation dump (row counts of `signal_ledger`, `telegram_post_delivery`, capture tables recorded) | dump file + checksum | restore into a scratch DB; counts and the S11 baselines (ledger 1022 rows / fp `7db4e45e…`, delivery 28) match | delete the scratch DB only |
| 2 | Read-only preflight (`validate_release_b.py` + `forward_collection activation`) | architecture YES; environment shows exactly the NOs the next steps fix | the report itself | none (read-only) |
| 3 | Migrations in order **24, 25, 26, 27, 28, 29, 30, 31** (24/25 are the Market Intelligence migrations: they need their own explicit owner approval, and the preflight's `migrations_22_and_24_to_31_applied` check requires all eight). `docker exec -i donchian-screener-postgres-1 psql -U trading_user -d trading_production -v ON_ERROR_STOP=1 -1 -f - < mechanism/<file>.sql` one at a time | each applies cleanly, all `IF NOT EXISTS` | a real query after each (`to_regclass`, trigger presence, 0 rows) | additive only: leave tables (empty, unused); `DROP` only via the owner-approved rollback scripts |
| 4 | Re-run `deploy/db/research_roles.sql` then `research_roles_verify.sql` (no default privileges: new tables are invisible to `donchian_app` until re-run) | `donchian_app` SELECT+INSERT on 24–31 tables; no UPDATE/DELETE | verify script green; preflight `runtime_role_is_least_privilege…` OK | `research_roles_rollback.sql` (point services back at the prior `DB_USER` first) |
| 5 | Schema verification | table/trigger/function set equals the derived catalog | preflight `migrations_22_and_24_to_31_applied` + `research_tables_guarded…` | as step 3 |
| 6 | Mechanism image/version verification (`CURRENT_MECHANISM_SHA` must contain Slice 1–11 code; **a deliberate, separate image bump**) | one pinned tag for pipeline, bot and collector | `docker inspect` of every consumer; no dual pin (see `donchian-bot.service.proposed` caveat) | repin to `cfd83f72f960` |
| 7 | Sector writer config: `SECTOR_HISTORY_RECORDER_ENABLED=1` in the VPS env file (fundamentals updater only) | next pipeline run writes polls/observations; first run records `first` observations | `sector_poll` rows exist, chain verifies, ledger fingerprints unchanged | unset the flag; history rows stay (immutable) |
| 8 | Vendor/source verification from the VPS: the dry-run CLI in **live** mode over ~40 symbols (read-only) | same bucket tally as the 2026-10-06 sample (equities → sector, ETFs/MFs → no_sector) | the dry-run JSON; gate `authoritative_vendor_verified_live` | none (read-only) |
| 9 | Candidate capture: three keys — mechanism image (step 6), `RESEARCH_CAPTURE_ENABLED=1`, and an `enabled` boundary row via `research_capture_set_state` by a research-admin person | candidate/feature rows appear for the *next* session only | capture row counts; ledger unchanged | `research_capture_set_state(...,'disabled')`; unset flag |
| 10 | Market/sector/RS collection (needs 24/25/29 as the spec dictates) | snapshots written by the collector run | collector status | disable the timer |
| 11 | Forward-return labels (migration 26 consumers) | labels appear only after maturity | `forward_collection` status | disable the timer |
| 12 | Timer/service install: commit `donchian-forward-collection{,-alert}.{service,timer}` + wrapper to `deploy/vps/`, **amending `test_guards.py` and `test_activation_neutral.py` in the same commit**; install on the VPS; `systemctl enable --now …timer` | 03:15 / 08:15 Asia/Jerusalem, Tue–Sat | `systemctl list-timers`, `systemd-analyze verify`, first fire's log | `systemctl disable --now …timer` |
| 13 | First-session verification (the first session after capture + recorder + timer are all on) | collector COMPLETE; sector verification OK; capture rows reconcile with the ledger; **no other writer touched** | `forward_collection status`, ledger/delivery fingerprints | stop-collection (§8) |
| 14 | Status/readiness verification | `research_status` shows the session as forward-observed with provenance | the status report | quarantine (§8) |
| 15 | Rollback criteria (any ⇒ stop, §8) | — | a ledger/delivery fingerprint changes; a capture error reaches the screener result; a chain fails verification; the runtime role holds UPDATE/DELETE; S11 evidence disturbed | — |

## 8. Rollback runbook
**A. Stop future collection** (always reversible, no data touched): `systemctl disable --now donchian-forward-collection.timer`; unset
`SECTOR_HISTORY_RECORDER_ENABLED`; `research_capture_set_state(…,'disabled')`; unset `RESEARCH_CAPTURE_ENABLED`; repin the image if the image was the cause.

**B. Undo collected research history.** Normally **do not delete**: observations and polls are immutable and append-only by design. Quarantine instead,
through the existing provenance architecture: record the affected sessions/symbols as invalidated in a new provenance/maintenance row (two-person
approved ticket), so the dataset assembler and readiness check exclude them; the raw rows remain as evidence. Physical deletion is a research-admin
maintenance action (approved, begun ticket) and a last resort. Table removal is `rollback22.sql`-class, data-loss gated.

## 9. Known limitations and technical debt
* **Dotted share classes** (`BRK.B`, `BF.B`; frozen dev has `BRK/A` too) get no sector from yfinance; the dash forms work. The updater has no dot→dash
  mapping. Pre-existing; fixing it changes which symbol string the vendor sees, so it is flagged, not changed.
* A fundamentals poll stamped after 00:00Z on the day after the session is *accounted* but not PIT-eligible for that session.
* `SECTOR_MAX_AGE_DAYS=30` operational, unvalidated. The 90% accounted share is borrowed from the existing coverage floor.
* yfinance has no `as-of`; `source_asof` stays `None`. The no-sector state rests on `quoteType`, a vendor classification that could change.
* Tiingo behaviour is CODE_READ only.
* Corrections to Slice 10: its §7 "Explicit no-sector vs failure" bullet and §8 are **superseded** (banner added there).

## 10. Production reads requested (NOT performed; waiting for owner approval)
1. On the VPS, read-only: freshness of `daily_fundamentals` (max date, row count for the last session) — confirms the cadence in §4.
2. The production `DATA_PROVIDER` value (informational: the dev machine has `tiingo`).
3. Whether the VPS can reach yfinance and the 40-symbol live dry run (step 8 in read-only form) — vendor behaviour from the VPS's network.
4. Count of dotted symbols in the production active universe.
5. Tiingo live behaviour: needs credentials and cost approval; only needed if the owner wants the diagnostic comparison.

## 11. Answers to the five questions
1. **Provider owning forward sector identity:** yfinance (`yfinance_info`): it is the only source that supplies the structural evidence (`quoteType`) for an inferred no-sector, it supplies
   the 11-name taxonomy the dev history already uses, and one authoritative chain keeps reclassification meaningful. Tiingo is diagnostic.
2. **Primary fails, fallback has a sector:** a `request_failed` poll on the yfinance chain; the head is not erased or refreshed; today's research uses the
   yfinance head while currency ≤ 30 days, then it goes stale; Tiingo's sector is never used.
3. **Primary returns and disagrees:** an ordinary `changed` reclassification on its own chain; no fallback record exists to reconcile.
4. **Convergence without a structural blocker:** yes for architecture (no `step: None`; verified by tests and the preflight). Environment and activation
   remain NO until the owner steps below.
5. **Between the lab branch and the first production forward-observed session:** (a) S11 PASS; (b) the `model_version='unknown'` fix, shipped; (c) merge
   decision for PR #1; (d) approval and execution of steps 1–14 above, in order, each with its verification; (e) a deliberate image bump; (f) the
   guard-amending scheduler-unit commit; (g) separate owner approval of the 24/25 Market Intelligence migrations (the preflight requires 24–31 all applied; whether 24/25 could be left out is an owner decision that would change that check); (h) the first
   session after everything is on must be observed, not assumed.
