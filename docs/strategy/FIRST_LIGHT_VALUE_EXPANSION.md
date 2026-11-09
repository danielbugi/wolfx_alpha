# First Light — Value Expansion Audit and Plan

**Status:** proposal, 2026-11-09. Written for the owner. Nothing here was deployed, applied or sent.
**Method:** every number below comes from the code on `main` (af8ba8b) or from a read-only, forced-read-only query of production
`trading_production` on 2026-11-09, or from the server's own timer and log state (read-only). Where a claim rests on an earlier research
record rather than a fresh query, it says so. No historical outcome statistic was computed for this audit: the SEC point-in-time holdout stays
closed (owner rule, 2026-11-04), and nothing here is a new look at a test period.

---

## 0. The short version

1. **First Light has unusually good research infrastructure and no demonstrated predictive edge yet.** Every predictive test so far came back
   negative or inconclusive: the ML score (honest AUC about 0.56 against a reported 0.70; audit 2026-09-20), the executed breakout strategy, the
   right-tail study (REJECT, PR #90), and the fundamental-inflection event study (`research_strategy_run` id 1: **NO_EDGE_DEMONSTRATED** at 5 and
   20 sessions, with a positive 60-session cell resting on only 3 time blocks). That is not a failure. It is what an honest system should find
   first, and few retail products could even measure it. But it decides the strategy: **the scarce, valuable thing is evidence, not more
   signals.**
2. **The Donchian signal is not selective.** In six weeks (2026-09-28 → 11-06) the signal ledger logged 5,697 signals on 2,874 symbols, essentially
   the whole ~3,000-stock universe. A breakout on its own is context, not a shortlist.
3. **The best assets are under-used:**
   - **10 years of SEC point-in-time fundamentals:** 2014–2026, every filing with its SEC acceptance timestamp, about 40 reporting seasons, next
     to 10 years of daily prices.
   - **Unused history in the evidence so far:** the inflection engine's lab verdict used **two** reporting seasons.
   - **A forward-outcome machine that only follows Donchian candidates:** the `fwd_v1` labels.
   - **Six hypotheses registered forward-only on 2026-10-31 that nothing evaluates.**
4. **An operational gap found during this audit (needs the owner today):**
   - **What stopped:** forward collection (market snapshot, sector recorder, relative strength, forward labels) has refused to run since the mechanism
     re-pin. `FORWARD_COLLECTION_ARMED` names image `dbadfa286266` while `CURRENT_MECHANISM_SHA` is `1fc1753cdae0`, so
     `run_forward_collection.sh` exits 5 by design.
   - **Last writes:** `market_snapshot`, `stock_relative_strength` and `forward_return_label` all last wrote on 2026-10-31 04:45 UTC.
   - **What is lost:** sessions past their decision deadline are a permanent gap by contract (`forward_collection/contract.py`), so those sessions'
     point-in-time captures are gone.
   - **The fix:** re-arming is one owner-approved file write. Section 8, day 0.
5. **Recommendation:** make the next product step an **Evidence Ledger**.
   - **What it does:** every SEC-dated fundamental change, and every Fundamental Updates selection decision, is frozen at the moment it became
     public. It is then followed forward with the existing label engine, and scored against the six pre-registered hypotheses.
   - **Effort:** small, because almost every part exists.
   - **Value:** it turns First Light from "tools that show things" into "a platform that knows which of the things it shows are worth anything",
     and it is the only path to a public track record a subscriber can trust.

---

## 1. Current capability inventory

Legend: **LIVE** = running in production now · **BUILT** = merged code, not running or not reachable · **DRAFT** = only on an unmerged PR ·
**ABSENT** = does not exist.

### 1.1 Data assets (production, 2026-11-09)

| Asset | Coverage | Writer, freshness | Status |
|---|---|---|---|
| `stock_prices` | 6.5M bars, 3,114 symbols, 2016-09-20 → 2026-11-06 | nightly pipeline | LIVE |
| `technical_indicators` (+ weekly / monthly) | 6.5M rows, 2016-10 → 2026-11-06 | nightly pipeline | LIVE |
| `sec_filing` | 121k filings, **every one since 2014 with an SEC acceptance timestamp**; 2,852 CIKs in 2026 | nightly (`sec_fundamentals_updater`) | LIVE |
| `sec_quarter` | 1.42M values, 12 metrics (revenue, operating income, net income, FCF, OCF, capex, equity, assets, …), basis and revision count; 1,078 revised values | nightly | LIVE |
| `sec_fact` | 4.0M raw XBRL facts, append-only | nightly | LIVE |
| `fundamental_feature` / `fundamental_inflection_event` | 23.6k features; 4,931 events (v1 1,322; **v2 SEC-PIT 3,609**), 1,449 symbols | nightly (inflection step) | LIVE |
| `earnings_calendar` | 68k rows, 1999 → 2027-03, EPS estimate / actual / surprise | nightly | LIVE (estimates not first-seen-dated: see 2.4) |
| `daily_fundamentals` | 609k rows, 2023-07 → 2026-11-06, market cap, P/E, P/S, quality scores, sector | nightly | LIVE |
| `quarterly_fundamentals` | 15.9k rows (yfinance), 2023-09 → 2026-08 | last write 2026-10-26 | LIVE, but a second fundamentals source beside SEC |
| `signal_ledger` | 5,697 Donchian signals since 2026-09-28; resolved: target1+ 2,273, stopped 1,937, expired 155, open 1,332 | nightly eval | LIVE |
| `candidate_observation` / `feature_snapshot` | 32.8k immutable point-in-time captures, 2026-10-09 → 11-06 | pipeline (research capture) | LIVE |
| `forward_return_label` (`fwd_v1`) | 73k labels (h1/3/5/10) for Donchian candidates | **stopped 2026-10-31** | STALLED (disarmed) |
| `market_snapshot` (`risk_regime_v1`) / `stock_relative_strength` (`rs_v1`) | 15 sessions; 138k RS rows, 2026-10-12 → 10-30 | **stopped 2026-10-31** | STALLED (disarmed) |
| `sector_observation` | 3,090 point-in-time sector rows, last 2026-11-03 | forward collection | STALLED |
| `paper_*` (forward paper tracker) | 5 exit variants, 18.4k observations, ~10.3k open | timer, last 2026-11-07 | LIVE |
| `research_strategy_spec` / `_run` | 7 runs: 1 NO_EDGE, 6 registered forward-only | manual (strategy lab) | BUILT, never re-evaluated |
| `news_items` | 1,393 headlines, **88 symbols**, last fetch 2026-10-31 | — | DORMANT (public news off pending licence) |
| `breakouts`, `momentum_scores`, `ml_predictions`, `ml_breakout_dataset_v2` (957 MB), `enhanced_ml_training_data` | legacy ML path | last write 2026-09-18 | DORMANT |
| `catalyst_classification`, `source_observation`, `market_event`, `experiment_registration`, `dataset_manifest`, `ml_prediction_outcomes`, `alerts`, `bot_watchlist` | 0 rows each | — | BUILT, empty |
| Audience | `bot_users` 7, `dashboard_users` 2; one public Telegram channel | — | LIVE, small |

### 1.2 Engines and pipelines

| Capability | Where | Status | Evidence |
|---|---|---|---|
| Donchian multi-timeframe screener + guard + quality grade | `mechanism/screeners/multi_timeframe_screener.py` | LIVE | 5.7k ledger rows in 6 weeks |
| Signal ledger outcome evaluation (R-multiples) | `mechanism/strategy_analytics/`, `donchian-signal-ledger-eval.timer` | LIVE | |
| Candidate capture (what the strategy saw, immutable) | `mechanism/research/observer.py` | LIVE | |
| Forward labels `fwd_v1` (pure; excess vs ^GSPC; MFE/MAE; void rules) | `mechanism/research/labels/` | BUILT; its scheduled run is the disarmed forward collection | 73k labels before 10-31 |
| Forward collection orchestrator (deadline, lock, retry, never fakes success) | `mechanism/forward_collection/` | LIVE but refusing (exit 5) | `/opt/donchian/logs/forward_collection_runs.log` |
| Forward paper tracker, 5 exit policies | `mechanism/research/paper_tracker.py`, `exit_policies.py` | LIVE | |
| Strategy lab: versioned spec → backtest → OOS → immutable run → report → research loop | `mechanism/research/strategy_lab/` | BUILT (manual) | 7 ledger runs |
| Fundamental Inflection Engine v1 (yfinance) and v2 (SEC acceptance-dated) | `mechanism/inflection/` | LIVE (both) | newly detected v2 events per night, Nov 2–6: 2,116 / 1,011 / 118 / 214 / 150 (the first nights include a backlog) |
| Inflection owner alerts (private) | `mechanism/inflection/alerts.py` | LIVE (owner chat) | 22 deliveries |
| Fundamental Updates: public model, card, caption, night selection, publication ledger, public API | `update_model.py`, `update_select.py` (PR #103), `update_ledger.py`, `backend/routers/public_signals.py` | BUILT; migration 41 not applied, so the API answers 503; selection is DRAFT | PRs #101 (merged), #103, #104 |
| Market Intelligence: regime, sector RS, breadth, provenance | `mechanism/market_intelligence/` | LIVE until 10-31 via forward collection | |
| SEC 8-K catalyst classification, EDGAR collector, financial anomaly (recurring vs one-time), first-seen observation store | `mechanism/market_intelligence/{catalyst_*,edgar_collector,financial_anomaly,first_seen}.py` | BUILT, **dry-run only, 0 rows** | collector "never writes" (docstring) |
| SEC PIT full-history reconstruction (99,527 company-quarters, reproduces production 1,968/1,968) | PR #94 | DRAFT, exploratory; gates: PIT PASS, coverage, survivorship pending | |
| Research Missions (plan → collect → verify → report; citation-verified claims; $150/month enforced budget gateway) | PR #98 | DRAFT; never run against a real model | |
| Discovery screener v2, Strength Score, Momentum Board / Leaders, Deep Value, Sectors, Markets, Earnings | `backend/services/*` | LIVE (dashboard) | 26 dashboard pages |
| Post-market package (digest, board, gainers, market health), idempotent | `mechanism/alerts/publish_post_market.py` | LIVE (channel) | |
| Private assistant bot | `mechanism/alerts/run_bot.py` | LIVE | 7 users |

### 1.3 What is notably ABSENT

- Forward outcomes for anything other than Donchian candidates: no fundamental event, selection decision or research claim is followed forward.
- An evaluator for registered hypotheses (the six forward-only rows were written once and never revisited).
- Any view joining a company's **fundamental change** with its **market state** (strength, breakout, relative strength, earnings reaction).
  The stock page shows them as separate owner panels.
- 8-K / event-timing data: `sec_filing` holds only 10-Q/10-K in the last 30 days (1,893 10-Q, 23 10-K, 15 amendments).
- Delisted companies: the universe is today's listed names, so every historical study has survivorship exposure.

---

## 2. Under-utilized assets, disconnections and duplication

### 2.1 Under-utilized

1. **Ten years of acceptance-timed SEC fundamentals:** 3,776 filings in 2014 rising to 11,049 in 2025, every one with `accepted_at`. The live lab
   verdict used `quarterly_fundamentals` (yfinance, two reporting seasons). PR #94 already reconstructs the history (99,527 company-quarters). The
   only data in the system with enough independent seasons to answer "does a fundamental inflection matter?" is sitting unused.
2. **The `fwd_v1` label engine:**
   - **Generic already:** `compute(obs: Observation(id, symbol, direction, t0_session, entry_close), horizon, sessions, bars, bench, as_of)` is pure
     and knows nothing about Donchian.
   - **Pointed at one source only:** it is fed only by `candidate_observation`, whose constraints (`signal_type` in the four breakout kinds,
     required channel fields) tie it to the breakout strategy.
3. **The six registered hypotheses** (`research_strategy_run` 2–7, 2026-10-31): FCF + valuation compression; margin + relative strength;
   profitability + revenue; revenue + volume; acceleration + leverage; delayed reaction.
   - **The registration is done properly:** forward-only after the registration date, confirmation needs at least 150 events, and the test period is
     explicitly not reused.
   - **But nothing counts the events as they arrive.**
4. **Earnings surprise:** 532 of the 591 SEC-dated inflection company-quarters public since 2026-09-28 (90%) have an EPS surprise within the 5 days
   before. No product view or study joins them.
5. **The financial-anomaly module (recurring vs one-time):**
   - **The rules exist:** `market_intelligence/financial_anomaly.py`, versioned and lineage-carrying.
   - **The gap they would fill:** they are exactly what the non-operating divergence heuristic in PR #103 calls its biggest limitation ("the
     source data does not itemize nonrecurring items").
   - **Not wired:** nothing connects the two.

### 2.2 Disconnected components that already describe the same companies

| Fundamental side | Market side | Overlap measured (2026-09-28 → 11-06) |
|---|---|---|
| 591 v2 inflection company-quarters | Donchian ledger signal within 10 days **after** public date | 279 (47%) |
| same | ledger signal in the 30 days **before** | 505 (85%) |
| 5,697 ledger signals | a v2 inflection public in the 90 days **before** the signal | 1,624 (28.5%) |
| same 591 | news within ±3 days | 8 (1%) |

The overlaps are large because breakouts are near-universal (section 0, point 2). The point is not that the combination works. It is that **nobody
can currently ask whether it works**, because the two halves never meet in one record.

### 2.3 Duplicated functionality (simplification candidates, none urgent)

- **Two fundamentals sources:** `quarterly_fundamentals` (yfinance; feeds v1, Deep Value and screener columns) and `sec_quarter` (SEC; feeds
  v2). v1 is kept deliberately (owner, 2026-11-04). Long term, one acceptance-dated source should feed everything.
- **Two inflection engines** running nightly (v1 and v2) with separate alerting paths.
- **Many "who is moving" views:** Momentum Board, Momentum Leaders, Today's Movers, Strength Leaders, the Discovery screener and the classic
  screener all rank the same universe by price behaviour. For 2 dashboard users this is breadth without depth.
- **The legacy ML path** (`breakouts`, `momentum_scores`, `ml_predictions`, dataset tables ≈ 1.1 GB) stopped writing on 2026-09-18 and is
  not on any live decision path ("ML score stays internal"). It is a cost and confusion candidate for archiving, after an owner decision.
- **Two idempotency mechanisms** (`session_state.json` and `telegram_post_delivery`): already listed in CLAUDE.md as known debt.

### 2.4 Validity hazards found while mapping

- **Mutable inputs without first-seen dates:** `earnings_calendar` estimates and `daily_fundamentals` ratios are overwritten in place. The
  first-seen store designed for exactly this (`first_seen.py`, `source_observation`) has 0 rows. Any study using consensus estimates is not
  point-in-time today.
- **Survivorship:** every historical study draws from currently listed symbols.
- **Fragile research capture:** arming is tied to an image SHA, and the re-pin procedure does not re-arm. Every deploy can silently pause
  research capture; it just did, for at least six sessions.

---

## 3. The three opportunities you named, assessed

Scores: 1 (low) to 5 (high). For effort, cost and risk, higher means **more** effort, cost or risk.

### 3.1 Opportunity Intelligence: "which companies are changing, and why"

- **What exists:**
  - **Fundamental change (LIVE):** the v2 engine detects it, SEC acceptance-dated, with figures and filing links.
  - **Selection and explanation (DRAFT):** support signals, the divergence heuristic and the night selection (#103); the publication model with
    allowlist and corrections (#101/#104).
  - **Market state (LIVE):** Strength Score, Discovery, the earnings read model and sector context.
- **What is missing:**
  - one company record joining fundamental change, market state and earnings reaction;
  - a nightly ranked shortlist across both (today's selection uses only fundamental facts);
  - outcome evidence for any of it.
- **Challenge:**
  - "Opportunity" implies the change predicts something, and nothing shown so far does.
  - Until the Evidence Ledger says otherwise, sell it as **change intelligence:** "what changed, verified from the filing, in plain language, the
    day it became public".
  - That is honest, defensible and valuable on its own: it saves an investor reading 1,900 10-Qs a month.

| Investor value | Commercial value | Data readiness | Effort | Op. cost | Leakage risk | Time to first result |
|---|---|---|---|---|---|---|
| 4 | 4 | 4 (SEC side strong; RS/regime stalled) | 2 | 1 (no paid API) | 2 if descriptive, 5 if marketed as predictive | 2–3 weeks |

### 3.2 Autonomous Research: source-grounded research with hypotheses, counterarguments and falsification

- **What exists:**
  - **Pipeline (DRAFT, PR #98):** plan → collect → analyze → verify → report.
  - **Verification:** deterministic rejection of uncited or advice-worded claims, and an independent verifier.
  - **Budget:** an enforced $150/month gateway.
  - **Never run:** no API key; cost per mission is estimated at $0.25–0.50 but unmeasured.
- **Challenge:**
  - LLM-written research on top of signals with no demonstrated edge produces fluent text about unvalidated ideas: expensive to make, easy to
    copy, and an advice-wording risk.
  - The model cannot create edge. It can (a) read what structured data cannot (10-Q text, to classify one-off items, the gap in section 2.1
    item 5), and (b) **write hypotheses in falsifiable form** that are registered into the ledger and scored by outcomes.
- **Better framing:** autonomous research as a **hypothesis generator and evidence reader that feeds the Evidence Ledger**, not as a report
  publisher.

| Investor value | Commercial value | Data readiness | Effort | Op. cost | Leakage risk | Time to first result |
|---|---|---|---|---|---|---|
| 3 now, 5 once outcomes exist | 3 | 3 (filing text needs `SEC_USER_AGENT`; news licence open) | 3 | 3 ($ per mission, unmeasured) | 3 (LLM knowledge of later events) | 3–5 weeks |

The LLM leakage risk deserves its own line. A model trained on data up to its cutoff "knows" how 2024–2025 stories ended. Any historical evaluation
of LLM-generated research is contaminated by construction. **LLM research can only be evaluated forward.** That is one more reason it belongs
behind the Evidence Ledger.

### 3.3 Research-to-Outcome: track point-in-time hypotheses and their outcomes

- **What exists:**
  - **Capture and labels (LIVE before 10-31):** immutable PIT capture, the `fwd_v1` labels, and their void, discontinuity and benchmark rules.
  - **Pre-registration (BUILT):** pre-registration with decision rules, the strategy lab and its report generator.
  - **Paper tracker (LIVE):** five exit variants.
  - **Registered hypotheses (BUILT):** six forward-only ones waiting.
- **What is missing:** an observation source for non-Donchian events, labels for them, and a scorecard that applies the registered rules as data
  arrives.
- **Assessment:** this is the cheapest of the three and the one every other opportunity depends on.

| Investor value | Commercial value | Data readiness | Effort | Op. cost | Leakage risk | Time to first result |
|---|---|---|---|---|---|---|
| 5 (it is the only honest answer to "does this work?") | 5 (a verifiable public record is the moat) | 5 | 2 | 1 | 1 (forward-only by construction) | 1 week to first labels; first confirmatory verdicts need 150 events (one or two earnings seasons) |

---

## 4. Opportunities you may have overlooked

1. **Unlock the decade of SEC history properly (close PR #94's gates).** Two gates block a confirmatory study:
   - **Coverage:** 85.1%, gate pending.
   - **Survivorship:** the current-listings universe.
   - **The fix:** a survivorship plan (delisted symbols' prices and filings: SEC has the filings, prices need a source), then ONE pre-registered
     study on the closed holdout.
   - **Why it matters:** 40 seasons instead of 2. The only way to learn in weeks, not years, whether fundamental change has any edge.
2. **Selection as an experiment.**
   - **The test:** every night PR #103 picks 3 companies and records why the rest were not picked (its decision record has one row per event:
     3,609 rows for Nov 2–6).
   - **What to compare:** labelled forward, "selected" vs "eligible but outranked" is a free, continuous A/B test of the editorial policy itself.
     Is the product choosing better than chance?
   - **Why it is unusual:** no competitor can show this.
3. **8-K catalyst timing.**
   - **Built but switched off:** classification rules, scope (8-K, 10-K/Q, NT filings) and the collector exist, dry-run only.
   - **What it would add:** NT 10-Q (late filing) and 8-K Item 2.02/4.02 are well-known event types, and sit directly in the SEC pipeline that
     already runs nightly.
   - **Needs:** a declared `SEC_USER_AGENT` and a schema-approved write path.
4. **Consolidate the surface.**
   - **The mismatch:** 26 dashboard pages, 2 dashboard users, 7 bot users. The bottleneck is distribution and trust, not features.
   - **The fix:** one company page (change brief + evidence) and one daily list beat six momentum views.
5. **Make research capture deploy-safe.**
   - **The change:** re-arming belongs in the re-pin procedure (`stage2_repin.sh`), and an unarmed collector should page the owner the first
     night, not silently exit 5.
   - **Why:** the research-to-outcome strategy is only as good as its unbroken capture.

---

## 5. Top five, ranked

| Rank | Opportunity | Status | Why this rank |
|---|---|---|---|
| **1** | **Evidence Ledger:** forward outcomes for SEC-dated fundamental changes, Fundamental Updates selections and registered hypotheses | Partial: labels, registration and capture exist | Cheapest, no leakage, everything else depends on it; starts paying off within a week |
| **2** | **Company Change Brief:** one explainable record per company joining fundamental change, support, market state, earnings reaction and filing links; descriptive | Partial: all inputs exist separately | Highest direct investor value, and the subscription product's core; only honest once rank 1 runs beside it |
| **3** | **Decade-scale PIT research:** close #94's gates, then one pre-registered confirmatory study | Draft | Answers the edge question in weeks; blocked on survivorship data |
| **4** | **Hypothesis generator:** research missions re-scoped to read filing text (one-off items) and emit falsifiable hypotheses into the ledger | Draft (#98) | Real leverage once rank 1 exists; paid calls need approval |
| **5** | **Trust and distribution layer:** append-only public record (migration 41), Fundamental Updates posts with SEC links, and later the outcome record | Built, awaiting approvals | Low effort, converts work into audience; the commercial moat |

Deliberately **not** in the top five:
- **More screeners:** nothing more is needed.
- **ML scoring:** no validated model, and the dataset is dormant.
- **New data vendors:** on hold per owner decision 2026-10-11, and nothing above needs one except rank 3's delisted prices.

---

## 6. Recommended vertical slice: **Evidence Ledger v0**

**One sentence:** every SEC-dated fundamental change is frozen the day it becomes public, followed for 1, 5, 20 and 60 sessions, and scored
nightly against the six pre-registered hypotheses and the Fundamental Updates selection policy. The owner sees it on one Lab page.

### 6.1 Why this slice

- **Reuse:**
  - the label engine (`fwd_v1.compute`, unchanged);
  - the session calendar (`labels/sessions.py`);
  - the hypothesis definitions (the predicates in `strategy_lab/inflection_study.py` lines 351–356, imported, not copied);
  - the selection and its decision record (`update_select.decisions`, PR #103);
  - the read-only Lab pattern (`backend/services/lab_reports_service.py`, `forward_tracker_service.py`).
- **New:**
  - one additive migration with two append-only tables;
  - an observer;
  - a label adapter;
  - a pure scorecard;
  - one read-only endpoint;
  - one Lab view.
- **No paid API, no sending, no change to signal detection, event dating or eligibility.**

### 6.2 Architecture

```
fundamental_inflection_event (v2, LIVE) ──┐
update_select.decisions() (PR #103) ───────┼─> observe (nightly, after the inflection step)
registered hypotheses (research_strategy_run 2-7) ┘        │  freezes: symbol, quarter, event types, figures, support, divergence,
                                                            │  selection outcome + reason, hypothesis memberships, SEC accession,
                                                            │  availability timestamp, t0 session, entry close, content hash
                                                            v
                                         event_observation (append-only, migration 44)
                                                            │
                         fwd_v1.compute (unchanged) <───────┤ label (horizons 1, 5, 20, 60; excess vs ^GSPC; MFE/MAE; void rules)
                                                            v
                                         event_outcome_label (append-only, migration 44)
                                                            │
                                        scorecard (pure) ───┤ per cohort: n observed, n labelled, progress to the registered n,
                                                            │ verdict by the REGISTERED rule only
                                                            v
                        GET /api/lab/evidence (owner, read-only) ──> Lab → "Evidence" view
```

### 6.3 Design decisions (made here, documented, reversible)

1. **Unit = company-quarter**, the unit the hypotheses were registered on (`inflection_study`). Every event type public for that quarter is stored
   as a set.
2. **t0 = the first session that opens after the SEC acceptance timestamp** (after 16:00 New York time → next session). Entry reference = that
   session's close, exactly `fwd_v1`'s convention. This removes the after-hours look-ahead.
3. **A hypothesis needing post-t0 information gets its own later decision time:**
   - delayed reaction uses the two-session reaction, so its t0 is the 3rd session;
   - membership is decided and frozen at that time, never before.
4. **Frozen at first sight.** `fundamental_inflection_event` rows can be revised later (`revision_count`). The observation stores the payload and
   its hash when first observed; a later revision is a new observation row with `supersedes`, never an edit.
5. **Backfill boundary.**
   - Events public on or after the registration date (2026-10-31) may be observed late from stored rows, flagged `observed_late = true`.
   - A late row counts for confirmation **only** if the event row was never revised after its t0 (`revision_count = 0` and
     `updated_at <= t0`); otherwise it is descriptive only.
   - Nothing before 2026-10-31 is observed: that period has been looked at.
6. **Blind interim results for registered hypotheses.**
   - Until a hypothesis reaches its registered n (150), the scorecard shows **counts and progress only, no returns**. This prevents the optional
     stopping that would otherwise contaminate the test.
   - Non-registered cohorts (event types, selected vs outranked) show descriptive numbers, labelled "descriptive, not confirmatory".
7. **Benchmark:** ^GSPC in v0 (`fwd_v1` already supports it). Sector-relative excess (the registered studies used a sector index) is v1: the
   scorecard reports both once `event_outcome_label` gains the sector benchmark. Until then the verdict for sector-defined hypotheses stays
   INSUFFICIENT_DATA, by rule.
8. **Generic tables** (`event_observation.source` = `inflection_v2`), so 8-K catalysts or research-mission hypotheses can later use the same
   ledger without a new schema.
9. **Migration number 44** (41 = publication, 42/43 reserved for PRs #98/#100). Additive, `IF NOT EXISTS`, append-only triggers, the same
   fail-closed pattern as the hardened migration 41.

### 6.4 Implementation plan

| Step | Deliverable | Files | Tests |
|---|---|---|---|
| 1 | Migration 44: `event_observation` (id, source, source_key, symbol, unit_quarter_end, availability_at, availability_basis, t0_session, entry_close, payload jsonb, payload_hash, observed_at, observed_late, supersedes_id, code_ref; UNIQUE (source, source_key, payload_hash)) and `event_outcome_label` (the `forward_return_label` columns, FK to `event_observation`); append-only triggers; verify + rollback scripts | `mechanism/add_event_evidence_ledger.sql`, `deploy/db/event_evidence_ledger_{verify,rollback}.sql` | real-Postgres tests like `test_publication_schema.py` |
| 2 | Observer (pure core + thin repository): company-quarter units from v2 events, t0 from acceptance time, hypothesis membership via the registered predicates, selection outcome via `decisions()` | `mechanism/research/evidence/observe.py`, `repository.py` | PIT tests: t0 strictly after availability; frozen payload; revision → new row; nothing before 2026-10-31 |
| 3 | Label adapter: `fwd_v1.compute` over `event_observation`; idempotent `INSERT … ON CONFLICT DO NOTHING` | `mechanism/research/evidence/labels.py` | parity test: same bars and observation give the same label as `fwd_v1` |
| 4 | Scorecard (pure): cohorts = 6 registered hypotheses, 5 event types, selection outcome; registered decision rule copied from the run row, never edited; blind interim | `mechanism/research/evidence/scorecard.py` | rule tests: blind below n, verdict only by the stored rule, deterministic bytes |
| 5 | Scheduling: one step in forward collection (already deadline-guarded, locked, owner-armed): `evidence_observe` then `evidence_labels`, dry-run by default | `mechanism/forward_collection/steps.py` | orchestrator tests; activation preflight lists the new tables |
| 6 | Read-only API `GET /api/lab/evidence` (+ `/{source_key}`), owner-only, `SET TRANSACTION READ ONLY` | `backend/routers/lab_reports.py` or new `evidence.py`, `backend/services/evidence_service.py` | API tests; owner-only enforced |
| 7 | Lab "Evidence" view: cohort table (n, progress bar to 150, status), selected vs outranked, drill-down to a frozen record with SEC link and labels | `frontend/src/app/lab/…`, `components/lab/EvidenceView.tsx` | component tests |
| 8 | Docs: `docs/research/EVIDENCE_LEDGER.md` (contract, decision rules, blind-interim rule) + migration registry row 44 | | |

**Estimate:** steps 1–4 in about 4 working days; steps 5–8 in about 3 more. Everything lands as draft PRs. Production needs four separate owner
approvals: migration 44, re-arming forward collection with the new step, the backend deploy, and the frontend deploy.

### 6.5 Acceptance criteria

1. **Coverage:** every v2 company-quarter with SEC acceptance on or after 2026-10-31 and a PUBLIC event type appears exactly once in
   `event_observation`, or more than once only via `supersedes`. Checked by a reconciliation query that prints 0 missing.
2. **PIT correctness:**
   - for 100% of rows, `t0_session` is the first session strictly after `availability_at` on the New York clock;
   - no payload field is dated after `observed_at`;
   - a test injects a revision and sees a new row, never an edit.
3. **Label parity:** for a fixture of 50 observations the adapter's labels equal `fwd_v1.compute` byte for byte. Void reasons (missing bar,
   discontinuity) behave identically.
4. **Registered rules respected:**
   - each of the six hypotheses shows `COLLECTING n/150` with no return figure until n ≥ 150;
   - its verdict is computed only by the rule stored on its `research_strategy_run` row;
   - a test proves an edited rule is refused.
5. **Selection evidence:** for each night since the policy's first simulated night, the scorecard reports selected vs eligible-not-selected
   cohorts with n and descriptive h1/h5 excess, labelled descriptive.
6. **Safety:**
   - no sending;
   - the API is owner-only and read-only;
   - the observer never writes `fundamental_inflection_event`, `signal_ledger` or anything else outside the two new tables (a privilege test as
     `donchian_app`);
   - migration verify passes; rollback drill passes.
7. **CI:** all six required checks green, with the real-Postgres job running the new tests and no skips.

### 6.6 Measurable user value

| Metric | Today | After the slice |
|---|---|---|
| SEC-dated fundamental changes with a frozen, PIT record and forward outcome | 0 | 100% of those public since 2026-10-31 (~600 company-quarters in the first week of observation) |
| Registered hypotheses with a live count toward their decision threshold | 0 of 6 | 6 of 6 |
| Time from horizon maturity to a label | n/a | ≤ 1 nightly run |
| Can the owner tell whether the 3 posts a night beat the ones not chosen? | No | Yes: a descriptive comparison every night, a decision-grade one at n ≥ 150 |
| Questions answerable without a new study | "what changed?" | "what changed, and what happened next, for every change we saw" |

### 6.7 Demonstration scenario (owner, about one week after activation)

1. The owner opens **Lab → Evidence**. The header reads, for example: *"Observed since 2026-10-31: 6xx company-quarters (SEC-dated). Labelled
   at 5 sessions: 4xx."*
2. **Registered hypotheses** table:
   - **Margin Expansion + Relative Strength:** `COLLECTING 41 / 150` (no returns shown: blind until 150).
   - The other five rows show the same kind of progress, each with an expected-date estimate from the current arrival rate.
3. **Fundamental Updates policy** table: *selected 15 vs eligible-not-selected 3xx*, with descriptive 1- and 5-session excess returns, clearly marked
   "descriptive, not confirmatory".
4. Clicking **LITE (2026-11-03)** opens:
   - the frozen record: figures, support signals, divergence, why it was selected, SEC filing link;
   - acceptance time → t0 → entry close;
   - each horizon's label as it matures: excess vs S&P 500, best and worst excursion.
5. The owner can now answer, from one screen and with every input traceable to a filing and a timestamp, the question every subscriber will
   eventually ask: **"how did the things you showed me turn out?"**

---

## 7. Measurable success criteria for the whole programme (30 days)

1. **Forward collection:** re-armed and running every session, with zero further silent gaps (an unarmed run alerts the owner the same night).
2. **Evidence Ledger v0:** live in production (after approvals), covering 100% of eligible events, labels current within one run.
3. **Company Change Brief v0:** draft PR with the descriptive joined record for every company with a v2 event in the last 30 days; zero
   predictive wording (advice guard test).
4. **PR #94 gates:**
   - a written survivorship plan with a cost estimate;
   - no holdout opened.
5. **Research missions:**
   - re-scoped design accepted or rejected by the owner;
   - if accepted, one measured live mission cost under the $2 per-mission and $10 total caps you set.
6. **Surface consolidation:** one documented proposal; no deletions without owner approval.

---

## 8. 30-day roadmap

| When | What | Owner action needed |
|---|---|---|
| **Day 0 (today)** | Re-arm forward collection for `1fc1753cdae0` (one root write of `/opt/donchian/FORWARD_COLLECTION_ARMED`), then confirm the next 04:45 UTC run exits 0. Sessions already past their deadline are a permanent gap by contract; record them in the data-health page. | **Yes:** production change |
| Days 1–4 | Evidence Ledger steps 1–4 on a branch (migration 44, observer, label adapter, scorecard), real-Postgres tests, a read-only dry-run against production data printing what would be observed | No (draft PR) |
| Days 5–7 | Steps 5–8 (forward-collection step, API, Lab view, docs); CI green; demo on a production-data copy | Review |
| Week 2 | Apply migration 44 (and 41 if approved); deploy the backend and frontend; enable the new collection step | **Yes:** migration, deploy, arming |
| Week 2 | Add "re-arm forward collection" to the re-pin procedure, and make an unarmed run alert the same night (tooling change, draft PR) | Review |
| Week 3 | Company Change Brief v0: one record per company joining v2 change, support/divergence, Strength Score, RS (once collection runs again), earnings surprise, SEC links; the owner page first | Review |
| Week 3 | Decide the Fundamental Updates launch (sending stays off until approved): the public posts show facts only, and the Evidence Ledger runs behind them | **Yes:** sending |
| Week 4 | PR #94 survivorship plan (delisted universe: SEC filings free; prices need a source → cost estimate, no purchase) | Decision on any data cost |
| Week 4 | Research missions re-scope: rebase #98 to migration 42; first use = read 10-Q text for the companies the divergence heuristic holds or flags, and write falsifiable hypotheses into the ledger; one measured live mission | **Yes:** paid calls, API key |
| Day 30 | Review: coverage, gaps, the first selected-vs-outranked readout (descriptive), progress of each registered hypothesis toward n = 150 | Review |

---

## 9. What this plan deliberately does not do

- It makes no claim that any signal works. The ledger's first job is to find out, by rules fixed in advance.
- It opens no holdout, re-uses no looked-at test period, and computes no new historical return statistic.
- It adds no data vendor, no paid call, no sending path and no change to signal detection, event dating or eligibility.
- It does not touch production. Every production step above is listed with the approval it needs.
