# Opportunity Intelligence: architecture (v0 → v2)

Status 2026-11-12: **v1 built on this PR (owner-only, read-only), NOT deployed.** Real output: a read-only run against production for session 2026-11-10 (section 5). Follows the Value Expansion audit (draft PR #106, ranks 1 and 2) and the owner's
2026-11-11 decision that Opportunity Intelligence is the next product priority after the Fundamental Updates release.

## 1. What it is, in one sentence

For every company whose fundamentals changed in a filing, **one explainable record** that joins *what changed* (SEC-dated figures), *whether the
business supports it* (operating signals, the divergence heuristic), *how the market reacted* (earnings surprise, price reaction, strength and
relative strength), each fact with its source and the time it became known — and, once the Evidence Ledger runs, *what happened next*.

**What it is not:** a prediction. Every test so far found no demonstrated edge (audit §0). The product is **change intelligence**: "what changed,
verified from the filing, in plain language, the day it became public". No investment score, no target, no advice wording
(`CHANNEL_VOICE_AND_COPY.md` guard). Whether any of it predicts anything is the Evidence Ledger's job, by rules fixed in advance.

## 2. Principles (binding for every version)

1. **Reuse, never recompute.** Each fact comes from the module that already owns it (table below). The brief is a *join*, not a new engine.
2. **Point-in-time or labelled.** Every field carries `as_of` and `pit` (`true` / `false` + reason). Inputs that are overwritten in place
   (`earnings_calendar` estimates, `daily_fundamentals` ratios — audit §2.4) are shown as "current vendor value", never as "known at the time".
3. **No predictive score.** Ordering uses transparent, factual keys (§5). Nothing is called a score, rating, rank of attractiveness or signal.
4. **Owner first.** v0 and v1 are owner-only and read-only. Public exposure (v2) reuses the publication allowlist and ledger of migration 41.
5. **No new data source, vendor, paid call or sending path** (owner holds, 2026-10-11). v0 needs **no migration**.
6. **Explicit session.** Every query takes the session it describes; nothing infers "today" from the wall clock (CLAUDE.md invariant).

## 3. Inputs and their owners

| Fact | Owner (reused as is) | PIT status |
|---|---|---|
| What changed: event types, quarter, figures, SEC accession, acceptance time | `fundamental_inflection_event` / `fundamental_feature` (method `inflection_v2_sec_pit`) | PIT (acceptance-dated) |
| Business support: operating income / margin / revenue growth / cash flow agreement; non-operating divergence | `mechanism/inflection/update_select.py` (`SUPPORT_SIGNALS`, `non_operating_divergence`, `decisions`) | PIT (same figures) |
| Selected as a Fundamental Update, or why not | `update_select.decisions()`; `fundamental_update_publication` once migration 41 is applied | PIT (decision record) |
| Earnings surprise around the filing | `backend/services/earnings_read_model.py` over `earnings_calendar` | **Not PIT** (estimates overwritten): labelled |
| Price reaction t0 → t0+2 vs S&P 500 | `stock_prices` with `fwd_v1` session conventions (`mechanism/research/labels/`) | PIT (t0 = first session after acceptance) |
| Strength Score and its components | `backend/services/discovery_service.py` (descriptive composite, "not a model") | Session-dated, recomputed from stored bars |
| Relative strength, sector context | `stock_relative_strength` (`rs_v1`), `sector_observation` (forward collection) | PIT (captured nightly) |
| Breakout context | `signal_ledger` (Donchian; near-universal, shown as context only) | PIT |
| What happened next (v1) | Evidence Ledger `event_observation` / `event_outcome_label` (audit §6, migration 44) | PIT, blind interim rules |

## 4. Components

```
                ┌──────────── mechanism/opportunity/ (NEW, pure + read-only) ────────────┐
 v2 events ───> │ repository.py   read-only SQL per input, all taking (session, symbols)   │
 features  ───> │ brief.py        PURE: inputs -> CompanyChangeBrief (facts + provenance)  │
 decisions ───> │ ordering.py     PURE: transparent sort keys (§5), no score               │
 earnings  ───> │ wording.py      PURE: plain-language lines; advice-word guard            │
 prices/RS ───> └──────────────────────────────────────────────────────────────────────────┘
                                   │ (imported by)
                backend/services/opportunity_service.py  (read-only transaction)
                backend/routers/opportunity.py           GET /api/opportunity/briefs?session=&days=&type=&sector=
                                                         GET /api/opportunity/briefs/{symbol}?session=
                                                         owner-only (require_owner), never public in v0/v1
                frontend/src/app/opportunities/          owner page: list + company drill-down
```

- **Placement follows CLAUDE.md "where should new work go":** facts are computed in `mechanism/` (like `strategy_analytics/`), never in a
  router, the frontend or the bot. The backend only calls the pure assembler with rows it read.
- **Data contract** (`CompanyChangeBrief`, JSON-serialisable, versioned `brief_v0`):
  `symbol, company_name, sector, session, quarter_end, public_at, sec_accession, changes[] {type, from, to, period}, support {signals_agreeing[],
  divergence, held_reason}, selection {selected | outranked | excluded, reason}, earnings {surprise_pct, report_date, pit:false}, reaction
  {t0, excess_2d, pit:true}, market {strength_score, rs_20, rs_60, sector_rs, as_of}, breakout {recent: bool, as_of}, provenance[] {field,
  source, as_of, pit, reason}, evidence (v1) {observation_id, labels or "collecting n/150"}`.
- **No storage in v0.** Briefs are assembled on request from stored rows; every input is already persisted and dated, so the same request for the
  same session returns the same bytes (determinism test). Freezing for the record happens in v1 through the Evidence Ledger (`event_observation`
  with `source = 'change_brief'`, audit §6.3 decision 8), not a second table.

## 5. v1 rules (owner decisions 2026-11-11 and 2026-11-12)

Three **separate** dimensions, never merged into one number:

| Dimension | Question | Verdicts | Counted when |
|---|---|---|---|
| **Business** | Did the reported business improve? SEC figures vs a year earlier, one verdict per accounting fact (revenue; operating; net income; cash). Derived metrics of one fact count once (`update_select.FAMILY`). Net-income gains mostly unexplained by operating income are *held*. | improved (≥ 2 facts up, none down) / mixed / worsened / no clear change / not available | always |
| **Expectations** | Did EPS beat what was expected **before** the release? | above / in line / below (±2%), or **not verified** | only with a verified **pre-release** consensus snapshot (observed before 00:00 New York on the report date). `earnings_calendar` is overwritten on every fetch, so the row holding the reported EPS was always written after the release: **0 verified today**; vendor figures are shown labelled unverified and never counted. Enabling it needs a first-seen consensus store (Evidence Ledger work), no code change here. |
| **Market response** | How did the price respond vs the S&P 500 (close before the first public day → close of the 3rd session)? A response, **not** evidence of business quality (separate colour in the UI). | rose / in line / fell (±1 pp), **unreliable**, not available | not when **unreliable**: a `price_discontinuities` row known by the cutoff inside the window, or a one-day move matching a 2x–20x split ratio. `adj_close` equals `close` in this database, so returns are unadjusted; 3:2 / 5:4 splits cannot be told from a large earnings move without corporate-action data (on 2026-11-10 those ratios matched 38 genuine earnings-day moves, so they are not used). Missing bar → not available. |

**Categories** (business first; the market only describes the response): *Business improved; market response positive* · *Business improved;
market response flat or negative* (the **discovery view**: a research list, not a buy signal) · *Business improved; market response unreliable or
not yet available* · *Business evidence mixed* · *Business did not clearly improve*. "Agreeing" = the first category with nothing disagreeing.

**Order** (unchanged, deterministic, `evidence.order_key`): agreeing first → business improved → more agreeing dimensions → fewer disagreeing →
more improved facts → fewer worsened → newest first public day → symbol. Counts are shown as the facts they are; nothing is a score.

**Knowledge cutoff** for session S = (S + 1 day) 12:00 UTC. Excluded and counted: events first detected or revised after it, figures recomputed
after it, earnings rows re-fetched after it, discontinuities detected after it. Prices: sessions ≤ S only.

**Production read-only run, session 2026-11-10, 14 days:** 625 company-quarters; business improved & market rose 176; business improved & market
flat/fell 139; business improved & market unreadable 20; mixed 243; not improved 47; expectations verified 0; unreliable windows 0 (none in 60 days).

## 6. Versions

| Version | Delivers | Needs | Production approvals |
|---|---|---|---|
| **v0: owner brief** | `mechanism/opportunity/` (pure assembler, ordering, wording, read-only repository), owner API, owner page; every v2 company-quarter of the last 30 days | Nothing new: existing tables only | Backend deploy, frontend deploy |
| **v1: with outcomes** | Brief links to the Evidence Ledger; frozen nightly as `event_observation(source='change_brief')`; "what happened next" per horizon; registered hypotheses shown as `COLLECTING n/150` (blind) | Migration 44 + ledger steps (audit §6.4) | Migration 44, forward-collection step arming, deploys |
| **v2: public change brief** | Public page and Fundamental Updates posts link to the brief: facts and SEC links only, allowlisted fields, corrections via the publication ledger | Migration 41 (being applied in the FU release), news licence still open (news stays off) | Public API/frontend deploy; sending stays a separate approval |

## 7. Acceptance criteria (v0)

1. **Coverage:** every v2 company-quarter public in the requested window appears exactly once; a reconciliation query prints 0 missing.
2. **Provenance:** every non-null field has a provenance entry; earnings estimate fields carry `pit:false` with the reason.
3. **PIT:** reaction t0 is the first session strictly after `public_at` (16:00 New York rule), matching `fwd_v1`; a fixture proves no price after
   the requested session is read.
4. **Determinism:** the same session request twice returns byte-identical JSON.
5. **Wording:** every generated line passes the advice-word guard (`BANNED` list) and the "no internals in copy" rule (no universe size or method).
6. **Safety:** API owner-only (test: non-owner 403), read-only transaction (test: a write raises), no import of any sending module.
7. **CI:** the six required checks green; real-Postgres tests for the repository queries.

## 8. Build order and estimate

1. Pure core (`brief.py`, `ordering.py`, `wording.py`) + fixtures — 2 days.
2. Read-only repository + real-Postgres tests — 1 day.
3. Owner API + tests — 1 day.
4. Owner page — 1–2 days.
5. Read-only dry run against production data (owner preview), then the deploy request.

Runs in parallel with the Fundamental Updates record-only observation (it touches no production component until its deploy is approved) and
before the Evidence Ledger, which v1 then plugs into.

## 9. v1: operational vs incomplete (2026-11-12)

| Operational on this PR | Incomplete |
|---|---|
| `mechanism/opportunity/` (pure evidence + ordering, read-only repository, CLI `brief.py --session`) | Not deployed (backend + frontend release need approval) |
| Owner-only read-only API `/api/opportunity/briefs[/{symbol}]`; owner page `/opportunities` | No outcomes yet: needs the Evidence Ledger (migration 44) |
| Tests: 26 pure (dimensions, pre-release rule, split/discontinuity flags, categories, PIT, ordering, wording, no writes), 3 real-schema DB tests (CI db job), 6 API tests, frontend format tests | Unadjusted closes: 3:2 / 5:4 splits are not detectable without corporate-action data |
| Read-only production run for 2026-11-10 (section 5); wording guard clean | Expectations dimension inactive: no verified pre-release consensus is stored |
| | No sector / size / strength context; no public version |

## 10. Open questions for the owner (none block v0)

1. Should v0 also include companies **without** a v2 event but with an earnings surprise and a large reaction? Default: no (change-led).
2. Retire overlapping "who is moving" views once the brief exists (audit §2.3)? Default: no change until the owner reviews usage.
