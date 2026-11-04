# First Light Global Intelligence Research Missions: architecture proposal

**Status: PROPOSAL, nothing implemented** (2026-11-04). This document asks for decisions: whether to build, which domain goes first, and the monthly budget.

**What it does not change:**
- It is not an exception to the 2026-10-11 "no new datasets" hold. Every new source below needs owner approval.
- The existing rules still apply: research output is private by default, nothing goes to a channel, no advice wording, and nothing about system internals appears in user copy.

## 1. What a "research mission" is
A mission is a **bounded, written question** about the world that the system researches, answers with cited evidence, and then **keeps monitoring**. Examples:
- "Which US-listed suppliers benefit if hyperscaler capex keeps growing?"
- "Is NSSC's margin expansion recurring or one-off?"

**The mission spec** (stored, versioned):
- domain, question, universe and horizon;
- the sources it is allowed to use;
- a budget, in dollars and tokens;
- the deliverables: a dossier, an industry map, and/or a thesis with kill criteria.

**Lifecycle:** draft → approved → researching → review → published to the owner (private) → monitoring → closed.

## 2. Architecture
```
Mission spec ──► Planner agent ──► Source router ──► Collectors (code + small model)
                     │                    │                 │
                     │                    ▼                 ▼
                     │              Source registry    Evidence store (append-only, point-in-time)
                     ▼                                       │
               Analyst agent ◄──────────── evidence ─────────┘
                     │  claims + citations
                     ▼
               Verifier agent (separate context; checks every claim against its cited evidence)
                     │  only verified claims
                     ▼
     Dossier / Industry map / Thesis ──► Thesis monitor (scheduled) ──► owner-only alerts + dashboard
```

| Component | Responsibility | Where it lives (proposed) |
|---|---|---|
| **Mission registry** | Specs, versions, status, budget, audit trail | New tables `research_mission`, `research_run` (additive migration) |
| **Source registry** | Every source with domain tags, licence (e.g. "internal use only"), cost, rate limit, and **point-in-time semantics**: does it give a published-at time, can history be revised? | Config plus table `research_source`; the router reads only approved sources |
| **Source router** | Maps the mission domain and question type to allowed sources and tools. Deterministic rules first; the planner may only pick from the allowed set. | `mechanism/research_missions/router.py` |
| **Collectors** | Fetch, normalise and deduplicate; extract passages. Mostly plain code, with Haiku 5.5 only for extraction and classification. | Runs on the VPS like other jobs, but on a separate timer and container from the pipeline |
| **Evidence store** | `evidence_item`: source, URL or accession, `published_at` (when the world could know it), `retrieved_at`, content hash, licence, excerpt, raw-document pointer. Append-only; a correction is a new row. | Postgres, plus raw documents on disk (in backups) |
| **Claims and citations** | `claim`: text, type (fact / inference / forecast), confidence, status (unverified, verified, refuted), links to evidence. A forecast is always marked as such. | Postgres |
| **Industry map** | A graph of company ↔ industry ↔ value-chain role ↔ company, built from SEC SIC codes, 10-K segment and major-customer disclosures, and supplier mentions. Every edge cites evidence and has an as-of date. | Tables `industry_node`, `industry_edge` |
| **Company analysis** | A dossier built on what we already own: SEC point-in-time facts, inflection events, prices, Strength Score, earnings calendar, plus filing text (MD&A, risk factors, 8-K items) and news | Reuses `mechanism/inflection`, the SEC tables and the strategy engine |
| **Thesis monitor** | A thesis is a set of claims, **indicators** (numbers we already compute, plus watch queries) and **kill criteria**. A scheduled job re-evaluates the indicators; an LLM is used only when an indicator moves or new evidence arrives. Every result is logged. | Separate systemd timer (when approved); alerts through the existing owner-only, exactly-once delivery pattern |
| **Agents** | Planner: Opus 5.5. Analyst: Sonnet 5.5, Opus 5.5 for hard questions. Collector extraction: Haiku 5.5. **Verifier**: Sonnet 5.5 in a fresh context. | Claude API with the SDK Tool Runner, **self-hosted** on the VPS: evidence stays in our database, our tools enforce source allow-lists, and per-turn hooks enforce budgets. Managed Agents is the alternative if we'd rather not host the agent loop. |

**Design rules:**
1. **Point-in-time evidence.** Every claim carries the latest `published_at` of its evidence, so any later analysis is limited to what was public then. This is the same discipline as the SEC point-in-time engine.
2. **Model knowledge contamination.** The models know events after any historical date, so **LLM judgements cannot be backtested honestly.** Missions are evaluated **forward only**: theses are registered with a date and scored later, like the six forward-only hypotheses.
3. **Verification is mandatory.** No claim reaches the owner without a verifier pass. Spot-checking citation precision is the primary quality metric.
4. **Budgets are hard.** There is a per-mission dollar cap (token task budgets plus a search-count cap), and a mission stops cleanly when it runs out.
5. **Safety invariants unchanged.** Agents never write outside the research schema and never send to a Telegram channel. Owner alerts reuse the claim-then-send ledger. Wording follows `CHANNEL_VOICE_AND_COPY.md` (no advice words).

## 3. Data sources by domain
| Domain | Free / already licensed | Paid (not approved) | Point-in-time quality |
|---|---|---|---|
| **Fundamentals, filings** | SEC EDGAR (have: facts, filings, acceptance times), full-text search, 8-K items | n/a | Excellent (acceptance timestamps) |
| **Macro** | FRED / ALFRED (in use in research), BLS, EIA, Census | n/a | ALFRED vintages are point-in-time |
| **News, general** | GDELT DOC API (free, no key; rate limit unverified); Tiingo News on our plan (**internal use only**) | Brave Search API about $5 per 1,000 queries; Claude web search $10 per 1,000 searches | Publication time present; reliability varies; needs deduplication |
| **Earnings calls** | Company IR pages (inconsistent) | FMP transcripts: plan level unclear (sources disagree, Premium to $139/mo Ultimate); free tier excludes transcripts | Good (call date) |
| **Healthcare / biotech** | openFDA (free key: 240 per minute, 120k per day), ClinicalTrials.gov, PubMed | n/a | Good; trial records are revised, so keep version history |
| **Patents / technology** | USPTO PatentsView (free, no key; limits to verify) | n/a | Grant and application dates |
| **Government / defence** | USAspending, SAM.gov, Federal Register | n/a | Good (award and publication dates) |
| **Market data** | Our prices, Strength engine, sectors, inflection events | Delisted history (see the survivorship plan) | As per the database |

## 4. Estimated operating cost (assumptions stated; to be measured in the vertical slice)
**Model prices** (list, per million tokens): Opus 5.5 $4 in / $20 out; Sonnet 5.5 $2 / $10; Haiku 5.5 $0.10 / $0.50. Cache reads $0.20 on Opus/Sonnet. **Batch API 50% off** for non-urgent work. Claude web search $10 per 1,000.

| Unit | Assumption | Estimate |
|---|---|---|
| Deep mission (one dossier or industry map) | Planner Opus 0.2M in / 20k out (about $1.2); Haiku extraction 2M / 0.1M (about $0.25); analyst Sonnet 1M / 60k (about $2.6; less with caching); verifier Sonnet 0.5M / 20k (about $1.2); 100 web searches ($1) | **about $5–7 per mission**; about $3–4 with caching and batched verification |
| Thesis re-evaluation | Sonnet 0.1M / 5k per thesis, only when an indicator moves or news arrives | about $0.25 each; about $0.13 batched |
| Monthly, small (owner research) | 20 missions, 30 theses checked weekly, free sources only | **about $120–170 per month** |
| Monthly, medium | 60 missions, 100 theses, Brave search, transcripts (FMP $29–139) | **about $500–650 per month** (60 × $6.25 + 100 × 4.3 × $0.25 + data $29–139) |
| Infrastructure | Existing VPS: Postgres, a timer and a container; raw documents in the GB range | about $0 extra at first; watch disk and backup size |

These are estimates, not measurements. The vertical slice reports the actual cost per mission (`usage` tokens plus searches) before any scale decision.

## 5. Minimal vertical slice (proposed first build, after approval)
**"Inflection deep-dive": one domain-agnostic mission type, built on data we already own and that is already approved.**

**Trigger:** the owner picks a company with a fresh SEC point-in-time inflection event (manual at first; no schedule).

**Steps:**
1. **Collect:**
   - SEC point-in-time facts and the inflection event (have);
   - the 10-Q/10-K MD&A, segment notes and 8-K items around the event (EDGAR, already used);
   - prices and Strength (have);
   - optionally Tiingo news (internal use, existing plan).
2. **Analyse:** Sonnet 5.5 writes a dossier covering:
   - what changed and why, in management's words, cited;
   - whether the change looks recurring or one-off (inventory, one-time items, acquisitions);
   - industry context: peers in the same SIC code with their own inflection status;
   - a **structured thesis** with 3–5 indicators and kill criteria.
3. **Verify:** a fresh-context verifier checks every claim against its cited excerpt; unverified claims are dropped and listed.
4. **Store:** evidence, claims and the thesis go into new additive tables, with point-in-time timestamps.
5. **Show:** an owner-only `/research` dashboard page. No Telegram until the owner approves.
6. **Monitor (phase 2 of the slice):** the indicators are re-checked after each nightly pipeline. An LLM runs only when an indicator crosses a threshold, and the result is an owner-only note.

**Success criteria:**
- Citation precision ≥ 95% on a 20-dossier spot check by the owner.
- Cost ≤ $4 per dossier, measured.
- Zero unverified claims shown.
- Theses registered forward-only, with their kill criteria, for later scoring.

**Scope:**
- Size: about 2–3 weeks of focused work (migration, router, collectors, three agent roles, page, tests). Manual trigger only, with no new schedule until reviewed.
- **Not in the slice:** paid sources, general web crawling, channel posts, automated triggers or trading signals.

## 6. Decisions needed from the owner
1. Approve building the vertical slice as described, with manual triggers and owner-only output.
2. A monthly API budget cap for missions (suggested starting cap: **$150**).
3. Whether Tiingo News (internal-use licence) and GDELT may be used as sources in the slice. Both are new datasets under the hold.
4. The first domain after the slice, for example semiconductors/AI supply chain (patents plus filings) or biotech catalysts (openFDA plus ClinicalTrials.gov).

## Sources (data-source and pricing checks, 2026-11-04)
- Claude model and tool prices: the Claude API reference bundled with Claude Code (cached 2026-10-06; verify on the Anthropic pricing page before budgeting).
- [Tiingo News API](https://api.tiingo.com/products/news-api): plans shown as internal use only; Commercial tier limits listed, price not shown.
- [FMP pricing](https://site.financialmodelingprep.com/pricing-plans?couponCode=paul) and [FMP transcript API](https://site.financialmodelingprep.com/developer/docs/earning-call-transcript-api); [third-party comparison](https://multiples.vc/compare/fmp-api-alternatives/md).
- Brave Search API: [CostBench](https://costbench.com/software/ai-search-apis/brave-search-api/), [ecorpit comparison](https://ecorpit.com/agent-web-search-api-cost-comparison-2026/). Not Brave's own page; verify there.
- openFDA limits: [api-evangelist openFDA rate limits](https://raw.githubusercontent.com/api-evangelist/open-fda/refs/heads/main/rate-limits/open-fda-rate-limits.yml).
- PatentsView: [rOpenSci](https://ropensci.org/blog/2017/09/19/patentsview/) (2017; current limits to verify on patentsview.org).
- GDELT: [GDELT Cloud comparison](https://gdeltcloud.com/compare/news-apis) (the free DOC API's rate limit is unverified).
