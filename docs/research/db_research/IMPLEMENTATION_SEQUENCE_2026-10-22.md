# Recommended implementation sequence — earnings correctness, ledger data, strategy contract

Status: proposal for owner review, 2026-10-22.
- **Done (draft, not deployed):** PR #50, Earnings Release A.
- **Not started:** everything else, which waits for approval.

| # | Step | Scope | Production impact | Depends on | Main risks | Rollback |
|---|---|---|---|---|---|---|
| 1 | **Merge + release Earnings Release A** (PR #50) | Backend only: board, earnings page/dashboard card (via the board), Discovery, stock context | Manual backend release | CI green; owner approval | 8 residual symbols keep two dates (low-confidence by design). The rule's ~0.12 s cost on uncached board loads. | Previous backend image |
| 2 | **Decide the earnings-today post** until the channel fix ships | Ops only: pause `donchian-earnings-today.timer`, or accept about 3 stale mentions per post | Timer change (needs explicit approval) | — | `Persistent=true` catch-up on re-enable (see the B/C plan §1) | `systemctl enable --now …timer` |
| 3 | **Ledger read-time metrics (DERIVED)**: post-exit trajectory, MFE, portfolio drawdown (rule `independent_1R`), VIX-tercile and observed-only regime/RS breakdowns with coverage; fix the stale capability flags and the outdated `UNAVAILABLE_DIMENSIONS` text | `mechanism/strategy_analytics` + `/api/strategies` (backend release; the mechanism image is not needed because the backend imports the module by path) | Backend release | — | Must replay the evaluator's exact bar walk (the self-check pattern). Label "after exit" values as outside the strategy's result. | Previous backend image |
| 4 | **Strategy contract, option A** (code registry + drift tests + contract endpoint; contracts c1/c2 documented; research contracts named separately) | `mechanism/shared`, `strategy_analytics`, backend | Backend release | Read the production `GUARDS_EFFECTIVE_FROM` value (name only, read-only) | Code remains the source of truth; mitigated by drift tests | Previous backend image |
| 5 | **Earnings Release C** (unified read model; Screener, Stock Intelligence, Watchlist, Earnings page) | Backend + frontend | Backend release + Vercel | Step 1 | Retiring the live per-symbol vendor call changes the stock page's source (keep it as a labelled fallback) | Previous backend image + Vercel deployment |
| 6 | **Earnings Release B code** (adapter, kept time, gated writer; flag OFF) | Mechanism (updater) | **None until a mechanism re-pin** | Stage 2 done; merge approval | Writer on a separate connection; the poll covers subjects across symbols | Flag stays off |
| 7 | **Mechanism release after Stage 2**: Release A channel fix (`send_earnings_today` uses the current-row rule) + Release B code | Mechanism image re-pin (`CURRENT_MECHANISM_SHA`; collector arming must match) | Re-pin window | Stage 2 complete; steps 1 and 6 | Coupled to the pin/arming invariant | Previous pin (documented re-pin rollback) |
| 8 | **Activate Release B** (flag on after a dry run and the role check) | Production configuration | Writer activation | Step 7 | Rows cannot be deleted (append-only by design) | Unset the flag |
| 9 | **Strategy contract, option B** (append-only `strategy_contract` table) | Additive migration | Migration window | Step 4 has settled | Standard migration process: backup, preflight, verify | Guarded rollback refuses when rows exist |
| 10 | **Catalyst / ML dimensions** | — | — | Separate approvals (Catalyst Phase 1A activation; a model that passes the gate) | — | — |

**Why this order:**
- Steps 1–5 are read-side, backend/frontend only, and can each be rolled back by redeploying.
- Steps 6–8 are the only writer work. They wait for the mechanism re-pin that Stage 2 controls anyway.
- The contract (step 4) comes before any new strategy or exit variant, so every later number cites its rules.

**Explicitly out of scope:**
- Live trading-rule changes.
- Changes to existing strategy identities or rows.
- Deleting or rewriting `earnings_calendar` rows.
- ML training.
- Stage 2 itself.
