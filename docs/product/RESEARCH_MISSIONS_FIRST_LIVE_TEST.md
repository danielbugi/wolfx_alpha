# Research Missions: plan for the first real-model test (preparation approved 2026-11-06; execution NOT authorised)

**Status:** paid calls stay disabled (`RESEARCH_PAID_CALLS_ENABLED` unset) until the owner explicitly authorises this test. Nothing here touches production data, schedules or notifications.

## 1. Where it runs
- **Recommended:** the owner's development PC, with a **local** Postgres that has migration 41 applied. That database holds missions, evidence, claims, reports and the ledger.
- **Source data** (prices, industries, SEC point-in-time revenue, inflection events, headlines) is read from production over an SSH tunnel, in a **forced read-only** session.
- **Read-only source adapter (implemented, `mechanism/research_missions/source_db.py`):** with `RESEARCH_SOURCE_DSN` set, `FirstLightDb` reads through one session that is refused unless read-only is proven on the live connection: the server default `default_transaction_read_only=on`, the client `set_session(readonly=True)`, and a write probe (`CREATE TEMP TABLE`) that must fail with SQLSTATE 25006. Only a single SELECT / WITH statement is accepted; a 60-second statement timeout applies. `run` refuses when the store and the source are the same database (fingerprint: database, postmaster start time, port, version). The DSN is never printed or put in an error.
- **Source role (owner decision):** a superuser source role is refused by the code. Production has no read-only login role today (verified read-only 2026-11-06: the login roles are `donchian_app`, read-write, and `trading_user`, a superuser). Options: **(a, recommended)** a dedicated SELECT-only login role on the tables `FirstLightDb` reads (`daily_fundamentals`, `companies`, `inactive_symbols`, `fundamental_inflection_event`, prices, SEC PIT, `news_items`). This is a production role change and needs its own approval. **(b)** `donchian_app` under the three read-only layers above: the server refuses writes, but the role itself holds write grants.
- **Tunnel:** Postgres publishes no port on the VPS. Run `ssh -N -L 15432:<postgres container IP>:5432 deploy@<VPS>`, resolving the container IP at tunnel time (it is 172.18.0.4 today and can change on restart). Then set `RESEARCH_SOURCE_DSN=host=127.0.0.1 port=15432 dbname=trading_production user=<role> password=…` in the local shell only.
- **Preflight (no paid call):** `python -m research_missions preflight` prints the read-only proof, the store and source fingerprints, the effective limits, this month's ledger, the paid-calls switch and whether the keys are present (booleans only).
- **Alternative** (not recommended for the first test): apply migration 41 to production and run on the VPS. That is a production change and needs its own approval.

## 2. Model and API configuration (as in `mechanism/research_missions/config.py`)
| Role | Model | Effort | `max_tokens` | Output |
|---|---|---|---|---|
| Planner (thematic only) | `claude-opus-5-5` | medium | 8,000 | JSON schema `PLAN_SCHEMA` (strict) |
| Analyst | `claude-sonnet-5-5` | medium | 24,000 | `ANALYSIS_SCHEMA` (strict) |
| Verifier | `claude-sonnet-5-5` | low | 12,000 | `VERIFY_SCHEMA` (strict) |

- **Request shape:** Messages API via `client.beta.messages.create`, with `output_config = {effort, format: json_schema}`.
- **Server-side refusal fallback** is on: `fallbacks="default"` with beta `server-side-fallback-2026-07-01`. It is priced at the ceiling in reservations and in settlement if a fallback ran.
- **Not used:** no `thinking` parameter (the default adaptive mode), no sampling parameters, no forced `tool_choice`, no tools, no web search.
- **SDK:** the official `anthropic` Python package (`pip install anthropic`), imported lazily by `llm.py` only.

## 3. Spend limits for the test
| Limit | Value | Where enforced |
|---|---|---|
| Per call | $2.00 (code default; the analyst's worst-case reservation is about $1.45) | `budget.decide`, before the request |
| Per mission | **$2.00** for the thematic test, **$1.50** for each company test | `research_mission.budget_usd` |
| Monthly, during the test | **$10.00**, set as `RESEARCH_MONTHLY_BUDGET_USD=10` (the environment can only lower the reviewed $150) | Database ledger, advisory lock |
| Outer guard | A dedicated Anthropic Console workspace with a **$20** spend limit | Anthropic Console (owner) |

**Expected actual spend:** about $0.25–0.50 per mission; **under $2 for the whole test.**

## 4. Credentials (owner provides; never committed, logged or printed)
- `ANTHROPIC_API_KEY`: a **new key in a dedicated workspace** (for example "first-light-research") with the $20 limit, so it can be revoked without touching anything else.
- `TIINGO_API_KEY`: already in the local `.env` (internal research use only).
- The SSH deploy key for the read-only tunnel already exists.
- No SEC contact is needed for the MVP's sources.

## 5. Test missions
| # | Mission | Entry | Budget |
|---|---|---|---|
| T1 | **HBM and advanced packaging**: "Which US-listed companies are exposed to HBM and advanced semiconductor packaging, in which value-chain role, and what are the main risks?" | Thematic, AI & Semiconductors | $2.00 |
| T2 | Company deep-dive: MU | Company | $1.50 |
| T3 | Company deep-dive from a recent SEC point-in-time inflection event (owner picks the event ID) | Company (event) | $1.50 |

**Run order:** T1, then review, then T2 and T3. Each run is `python -m research_missions run <id>`, run by hand.

## 6. Expected outputs
- **Plan (T1):** ≤ 5 news queries and ≤ 20 candidates, all from our 70-odd AI & Semiconductors universe. Out-of-universe names (for example SK hynix, TSMC, Samsung) are listed but not researched.
- **Evidence:** 40–80 items (one database fact sheet per candidate, GDELT and Tiingo articles, headlines), each with source, date and licence.
- **Claims:** about 15–40, each citing evidence labels; companies with value-chain roles; supply-chain edges; open questions.
- **Report:** findings from verified and partial claims only; dropped claims listed; evidence table; cost from the ledger.

## 7. Source-verification criteria (owner review; pass required before any further use)
1. **Citation precision ≥ 95%:** for every verified or partial finding in T1 (all of them), the cited excerpts actually state it.
2. **Zero** findings that rest on uncited or unknown evidence. The code guarantees this; the review confirms it.
3. **Zero** advice wording in the report.
4. **Rejection check:** of up to 10 claims the verifier rejected, at least 8 were rightly rejected (a check that the verifier is not over-strict).
5. **Roles check:** each company's role and exposure are consistent with its cited evidence.
6. **Licences:** internal-only items are marked, and the report states it is not for redistribution.
7. **Cost:** the measured cost per mission is recorded from the ledger. Expectation ≤ $1; anything above $2 means the test stops.

## 8. Failure handling and rollback
| Event | Handling |
|---|---|
| Refusal (`stop_reason: refusal`, fallback also declined) | The call is paid and recorded; the mission fails with the category; review the inputs before any retry |
| Budget refusal | The mission stops as `stopped_budget`; everything collected is kept; no request was sent |
| Truncated or invalid JSON | Paid, recorded; the mission fails; review `max_tokens` / effort before a retry |
| GDELT or Tiingo error | The source contributes nothing (logged); the mission continues on the other sources |
| Anything unexpected | Unset `RESEARCH_PAID_CALLS_ENABLED` (the kill switch; no code change), and revoke the dedicated API key if needed |
| Rollback | Nothing to roll back in production: the local database can be dropped; the ledger and evidence stay append-only for the record |

## 9. Owner decisions needed to start
1. Authorise the test and the limits above.
2. Provide the dedicated API key (and set the Console limit).
3. Review the preflight output (read-only proof, isolation, limits) and explicitly authorise execution. The adapter is built and tested; nothing runs until then.
