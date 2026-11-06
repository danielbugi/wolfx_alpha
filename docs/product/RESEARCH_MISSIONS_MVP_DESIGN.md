# Research Missions MVP: design (2026-11-05)

This implements the owner-approved Global Intelligence MVP (decision of 2026-11-05; architecture in `GLOBAL_INTELLIGENCE_RESEARCH_MISSIONS.md`).

**Constraints:**
- Development branch only: no deploy, no production migration, no schedule, no notification.
- Hard API budget: **$150 per month**, enforced in code and tested.
- First thematic domain: **AI & Semiconductors**; first test case: **HBM and advanced packaging**.

## 1. Architecture and integration points
```
CLI (python -m research_missions ...)                      FastAPI /api/research/* (owner only)          Next.js /research (owner only)
   create / run / status / budget                          list, detail, create-draft, budget           list, create, mission detail
           │                                                          │ reads
           ▼                                                          ▼
 research_missions.pipeline ── plan → collect → analyze → verify → report ──► Postgres (migration 41, additive)
   │            │                        │                    │
   │            ▼                        ▼                    ▼
   │     router (domain → sources)   llm (ONE paid gateway: budget reserve → call → settle)   report (deterministic, verified claims only)
   │            │
   ▼            ▼
 store     sources: first_light_db (prices, sectors/industries, SEC point-in-time facts, inflection events, news_items headlines),
                    gdelt (DOC 2.0 API), tiingo_news (internal use only)
```

**Reused, not rebuilt:**
- `shared.db` for connections;
- the price, sector, industry and SEC tables, and `fundamental_inflection_event`;
- `news_items` headlines;
- `backend/auth` `require_owner`;
- the migration and grant pattern of migration 40;
- the frontend layout, auth context and navigation list.

**No new framework.** The SDK is the plain `anthropic` client with JSON-schema structured outputs. There is no agent framework or vector database.

Package: `mechanism/research_missions/`, plus `backend/routers/research_missions.py` / `backend/services/research_missions_service.py` and `frontend/src/app/research/`.

## 2. Database schema (migration 41, `mechanism/add_research_mission_tables.sql`; additive, `IF NOT EXISTS`)
| Table | Purpose | Key columns |
|---|---|---|
| `research_mission` | Registry and execution state | `kind` ('company' / 'thematic'), `title`, `question`, `domain`, `params` jsonb (ticker, event_id, theme), `status` (draft, queued, running, completed, failed, stopped_budget), `budget_usd`, `spent_usd`, `error`, timestamps |
| `research_step` | One row per pipeline step and attempt | `mission_id`, `step` (plan, collect, analyze, verify, report), `status`, `detail` jsonb, `started_at`, `finished_at` |
| `research_document` | **Append-only** source documents (provenance) | `source`, `url`, `title`, `publisher`, `published_at`, `retrieved_at`, `license` ('public' / 'internal_only'), `content_sha256` (unique with source and url), `excerpt` (short), `meta` jsonb |
| `research_evidence` | **Append-only** mission-scoped evidence item | `mission_id`, `document_id`, `label` (E1, E2, ...), `quote`, `locator` |
| `research_claim` | Claims produced by the analyst | `mission_id`, `label` (C1, ...), `kind` (fact / inference / forecast), `text`, `confidence`, `status` (unverified / verified / partial / unsupported), `verifier_note` |
| `research_claim_evidence` | Claim ↔ evidence links | `claim_id`, `evidence_id` |
| `research_company` | Companies identified by a mission | `mission_id`, `symbol`, `name`, `in_universe`, `role` (value-chain role), `exposure`, `thesis`, `risks` jsonb, `claim_labels` jsonb |
| `research_relation` | Industry and supply-chain edges | `mission_id`, `from_entity`, `to_entity`, `relation` (supplier_of, customer_of, competitor_of, produces, uses), `claim_labels` jsonb |
| `research_report` | Versioned report | `mission_id`, `version`, `markdown`, `created_at` |
| `research_llm_usage` | **Budget ledger**: one row per paid call | `mission_id`, `role`, `model`, `status` (reserved / settled / refused / failed), `reserved_usd`, `cost_usd`, token counts, `web_searches`, `created_at`, `settled_at` |

**Append-only enforcement** (documents, evidence, usage settled rows): a `BEFORE UPDATE OR DELETE` trigger refuses changes. The exception is the usage row's single `reserved → settled/failed` transition. Grants to `donchian_app` follow migration 40: SELECT/INSERT/UPDATE, and no DELETE.

## 3. API contracts (owner only, `require_owner`)
| Method | Path | Returns |
|---|---|---|
| GET | `/api/research/missions?status=&kind=` | `[{id, kind, title, domain, status, spent_usd, budget_usd, created_at, updated_at}]` |
| GET | `/api/research/missions/{id}` | `{mission, steps[], report_markdown, claims[{label, kind, text, confidence, status, evidence[]}], companies[], relations[], evidence[{label, source, title, url, published_at, license}], usage{calls, cost_usd}}` |
| POST | `/api/research/missions` | Body `{kind, title, question, domain, params}` → `{id, status: "draft"}`. It **does not run** anything: running is a separate, manual CLI step in the MVP. |
| GET | `/api/research/budget` | `{month, cap_usd, spent_usd, reserved_usd, remaining_usd, paid_calls_enabled}` |

## 4. Agents and workflow
Each paid role uses one structured-output (JSON-schema) call through `llm.Gateway`.

| Role | Model (configurable) | Input | Output (schema) |
|---|---|---|---|
| **Planner** (thematic only) | `claude-opus-5-5`, effort medium | Theme, domain, candidate universe (symbols, names, industries from our database), allowed sources | `sub_questions[]`, `gdelt_queries[]` (≤ 5), `candidate_symbols[]` (≤ 20, must come from the universe), `outside_universe[]` (named, not scored), `value_chain_hypotheses[]` |
| **Collectors** | Plain code, no LLM | Plan or ticker | `research_document` and `research_evidence` rows with provenance |
| **Analyst** | `claude-sonnet-5-5` | Evidence pack (labelled E1…En, with dates and licence) plus our point-in-time numbers | `claims[]` each citing evidence labels; `companies[]` (role, exposure, thesis, risks, claim labels); `relations[]`; `open_questions[]` |
| **Verifier** | `claude-sonnet-5-5`, fresh context, sees **only** each claim and its cited excerpts | Claims with cited evidence | `verdicts[{claim, verdict: supported / partial / unsupported, note}]` |
| **Report** | Deterministic code | Verified and partial claims only | Markdown report; unsupported claims listed as dropped |

**Deterministic guards around the LLM:**
- A claim citing an unknown or other-mission evidence label is marked `unsupported` **before** the verifier sees it.
- A forecast must be `kind = forecast`.
- Companies outside our universe are listed but never scored.
- The report never contains unverified text.

**Workflow:** `draft → queued` (CLI `run`) `→ running`, then the steps plan → collect → analyze → verify → report, then `completed`. Each step is recorded in `research_step`, and a failure sets `failed` with the error. A budget refusal sets `stopped_budget`, and the mission keeps everything collected so far. Re-running resumes from the first incomplete step.

**Company deep-dive** (ticker or `event_id`):
- The plan is deterministic: the company's sector peers and its inflection events.
- The collectors gather our database facts, SEC point-in-time quarters, inflection events, `news_items` headlines, Tiingo news and GDELT.
- The analyst and verifier then run as above.

## 5. Sources and licensing
| Source | Access | Licence and handling | Use in MVP |
|---|---|---|---|
| First Light database (prices, sectors, industries, SEC facts and filings, inflection events) | Existing | Our own derived data (SEC data is public) | Yes |
| `news_items` (Benzinga headlines via the existing news feed) | Existing table | **Internal only.** Headline, URL and date stored; no article text; never redistributed | Yes |
| GDELT DOC 2.0 API | Free, no key (owner approved 2026-11-05) | Article metadata only (title, URL, date, domain); no article text | Yes |
| Tiingo News | Existing key (owner approved, internal research only) | **Internal only.** Title, URL, date, tickers and a short description excerpt (≤ 300 characters); `license = internal_only`; never redistributed | Yes, when the key is configured |
| SEC EDGAR full text / filing documents | Free but **requires a declared User-Agent contact** | Public | **Pending:** the owner must configure `SEC_USER_AGENT` |
| Claude `web_search` | Paid ($10 per 1,000) | n/a | **Off** (`max_uses = 0`; not declared) |

## 6. Cost control (enforcement, not just a cap)
1. **One paid gateway.** Only `research_missions/llm.py` imports `anthropic`; a test fails if any other module does.
2. **Kill switch.** No paid call happens unless `RESEARCH_PAID_CALLS_ENABLED=1`. Otherwise the gateway raises `PaidCallsDisabled` **before** building a client.
3. **Reservation before every call.** The worst-case cost is computed as conservatively estimated input tokens (characters ÷ 3) × input price, plus `max_tokens` × output price. It is doubled when a server-side fallback is enabled, priced at the highest price among the model and its possible fallback.
   - Under a Postgres advisory transaction lock, the gateway sums this month's settled and reserved spend, plus the mission's spend.
   - It inserts a `reserved` row **only if** the month stays at or under `RESEARCH_MONTHLY_BUDGET_USD` (default **150**), the mission stays at or under its `budget_usd` (default **5**), and the call stays at or under `RESEARCH_MAX_CALL_USD` (default **2**).
   - Otherwise it inserts a `refused` row and raises `BudgetExceeded`, and **no request is sent**.
4. **Settlement.** After the call, the row becomes `settled` with the actual cost from `response.usage` at the served model's price; an unknown model is priced at the ceiling. A failed call still settles at the reservation unless the API proves nothing was billed.
5. **Inputs are bounded by design.** The evidence pack is capped at a fixed number of items and characters, and the cap is stated in the prompt and the report. `max_tokens` is fixed per role.
6. **Outer guard (owner action).** Set a matching spend limit on the Anthropic Console workspace. The code does not assume it exists.
7. **Tests:**
   - a refusal makes no client call;
   - concurrent reservations cannot both pass at the edge (database test);
   - settlement maths;
   - the kill switch;
   - the gateway-only import rule;
   - a mission stops cleanly with `stopped_budget`.

**Expected cost per mission** (estimate; measured on the first live run): planner about 3k in / 3k out on Opus ($0.07); analyst about 12k / 8k on Sonnet ($0.10); verifier about 10k / 3k ($0.05). About **$0.25–0.50 per mission**.

## 7. Phased plan and acceptance tests
| Phase | Scope | Acceptance |
|---|---|---|
| **1 (this PR): vertical slice** | Migration 41; budget ledger and gateway; router; collectors (database, GDELT, Tiingo, `news_items`); planner, analyst and verifier; deterministic report; CLI; owner-only API; `/research` page | Unit tests with a fake LLM and recorded source fixtures pass. HBM thematic and company deep-dive missions run end to end with the fake LLM. Budget enforcement tests pass. CI migration bootstrap is green. |
| 2: first live runs (after owner configuration) | `ANTHROPIC_API_KEY`, `RESEARCH_PAID_CALLS_ENABLED=1` on a dev machine; run the HBM mission and one company deep-dive | Measured cost per mission recorded; owner spot-check of citation precision ≥ 95%; zero unverified claims in reports |
| 3: SEC filing text | `SEC_USER_AGENT` configured; MD&A, risk-factor and 8-K excerpts as evidence | Evidence cites accession and section |
| 4: thesis monitoring | Indicators and kill criteria per thesis, re-evaluated after the pipeline (needs schedule approval) | No LLM call unless an indicator moves; owner-only output |
| 5: more domains | Healthcare (openFDA, ClinicalTrials.gov), energy (EIA) and others | Same tests per source |

**Holdout and research rules unchanged.** Missions are evaluated forward only: model knowledge of later events makes historical LLM judgements unbacktestable.
