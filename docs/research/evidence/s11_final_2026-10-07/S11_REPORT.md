# RELEASE B S11 PASS — PROVEN UNDER LEAST PRIVILEGE

Final report of the passive production validation authorised on 2026-10-03 (S11), produced 2026-10-07 04:07Z–04:20Z from the persisted production
evidence. Every production access was read-only: the observer `s11_ro.sh` (read-only SQL, logs), the sha-pinned journal wrapper (`43b9ecea…`, only the
fixed unit allowlist and presets), the corrected validator (commit `d4eab95`) streamed into a throw-away container, and read-only SQL. Nothing was
triggered, restarted, changed or written. Raw outputs: this directory (`01`–`08`, `valrun.sh`, `reconrun.sh`).

**How this report was completed.** The in-session cron checkpoints (21:12Z, 23:52Z, 00:52Z) did not survive a session restart, so no live
`pg_stat_activity` sampling was taken during the run windows. The final checkpoint was therefore run from persisted evidence (logs, journal, ledger).
Identity of the one-shot services is shown by execution/log evidence as S11.10 allows ("do not require persistent connections from one-shot services"):
see item 4.

## FINAL REPORT

**1. Scheduled execution timeline (window: S10 end 2026-10-03 09:48Z → 2026-10-07 04:07Z, all normal timer fires).**
| Unit | Result |
|---|---|
| `donchian-pipeline` | Weekend fires 10-03/10-04 returned OK in about a second (no session). **10-05:** 20:45:28Z start → 20:59:59Z FAILED exit 1 (`price freshness check`: "the 2026-10-05 bar is not in yet - the session stays unmarked and the next attempt retries") → 22:00:33Z start → **23:21:51Z OK**, "marked processed through 2026-10-05". **10-06:** 20:45:53Z → 20:59:59Z FAILED (same gate) → 22:00:54Z → **23:24:55Z OK**. Mechanism sha `cfd83f72f960` throughout |
| channel-sender family | notice-midday 09:00Z, earnings-today 07:00Z, notice-evening 17:00Z, evaluator 00:30Z: every fire OK (10-04, 10-05, 10-06, 10-07). `market_calendar gate` exits 3 at 02:00Z daily ("no new session for digest:prod", the designed skip) |
| `donchian-postmarket-retry` | every 20 min 22:20Z–03:00Z, all OK |
| `donchian-nightly-backup` | 10-03, 10-04, 10-05, 10-06 23:3xZ, all OK |

**2. Sender result.** Normal scheduled sends succeeded: e.g. 10-05 17:00Z `SENT 1 notices [assistant] to PROD (slot 2)`; no manual invocation. No permission or
authentication error in any sender unit (journal `errors` preset, three 36-hour windows, and the Postgres log).

**3. Pipeline result.** Both weekday sessions completed under the approved retry design; no `--force`, no session or threshold change.

**4. Runtime DB identities.** The wiring is `donchian_app` for every runtime service: `BACKEND_/BOT_/PIPELINE_/SENDER_DB_USER=donchian_app` (passwords present,
never read), `APP_DB_*` unset; `docker-compose.yml` maps `DB_USER: ${PIPELINE_DB_USER:-…}` and `${SENDER_DB_USER:-…}`; base `DB_USER=trading_user` is
the bootstrap/backup identity. Validator `connections --forbid-user trading_user`: **PASS** (12 connections, all `donchian_app`: backend 10, bot 2). Across
~4 days of real writes, Postgres logged **zero** `permission denied`, `password authentication failed`, `must be owner`, `does not exist`, `deadlock`,
`duplicate key`, `violates`, or `FATAL`. Limit: no live sampling of the one-shot pipeline/sender connections was taken (see above).

**5. Freshness / session.** Explicit session resolved (`new US session 2026-10-05 (last processed: 2026-10-02)`); the first attempt correctly refused a missing bar;
`stock_prices` rows for the session: 3066/3066 (10-05), 3065/3065 (10-06); duplicate bars 0; session marker set after the successful run.

**6. Guards.** `Universe guards mode: ACTIVE (session >= GUARDS_EFFECTIVE_FROM=2026-10-02)`, behaviour `NEW`; dropped **81/1397 = 5.8%** (10-05) and **78/1366 = 5.7%**
(10-06), both ≤ 15%; `guards.db.no_discontinuity_in_ledger` PASS; `GUARDS_EFFECTIVE_FROM` unchanged (2026-10-02).

**7. Screener funnel (10-05 / 10-06).** Candidate signals entering the guards 1397 / 1366 (bullish_breakout 103 / 102, bearish_breakout 155 / 123, near_bullish 442 / 434,
near_bearish 697 / 707); guard rejected 81 / 78; screened 1316 / 1288; post-guard bullish 101 / 101, bearish 142 / 117, near bullish 417 / 401, near bearish
656 / 669; ML "processed" 888 / 812 but no validated model exists (`ML: N/A`); tracked breakouts 243 / 218.

**8. Ledger reconciliation (exact).** 10-05: **243 breakouts = 126 written + 117 open-position skips** (98 still open + 19 open at write time) + 0 ineligible (ATR/price) + **0 unexplained**;
10-06: **218 = 99 + 119** (101 + 18) + 0 + 0. Every ledger row of the session is a breakout in the results file and no row exists outside it. The
historical rows are unchanged: the **1022-row immutable fingerprint is exactly the S11 baseline `7db4e45e68952746686e7f986f3b73e9`**; the mutable
fingerprint of that population changed, as expected, through the evaluator. Ledger 1022 → 1247 = +126 +99 (both explained). Two validator results need
explanation, neither a regression: `guards.db.candidate_range` (126 vs median 363, 99 vs 266) compares guarded sessions with earlier unguarded ones and a growing set
of open positions suppresses new entries (the exact skip counts above); `config.guards.boundary_in_future` is a pre-first-session check that is false by construction
once the boundary has passed.

**9. Delivery reconciliation.** Each of 10-05 and 10-06: 4 posts (`daily_digest`, `market_health`, `momentum_board`, `top_gainers`) to `prod`, `sent`, attempt 1;
validator `delivery`: PASS, no duplicates; the 28 baseline delivery rows keep the baseline hash **`9c73e87468854222fe5280dca473c373`**; 28 → 36 = 2 sessions × 4; `market_environment` not sent.

**10. Evaluator.** Fired naturally 10-05, 10-06, 10-07 at 00:30Z, all OK, no authorization errors; `pg_stat` shows 4129 cumulative `signal_ledger` row updates since statistics start (all to mutable columns: the historical immutable
fingerprint matches); reconciled apart from the pipeline writes (the evaluator never inserts).

**11. `model_version` defect measurement (observation only).** New rows since 10-02: 225 = **222 `'unknown'`**, 3 NULL, 0 real versions (10-05: 124 / 2; 10-06: 98 / 1). By grade: A 31, B 45, C 31, D 34, F 81 `unknown`;
the 3 NULL are B 1, F 2. All rows: 860 NULL, 387 `unknown` (165 from 10-02 + 222). The defect reproduces in 98.7% of new rows; historical rows untouched.

**12. Capture state.** OFF throughout: `RESEARCH_CAPTURE_ENABLED` key count 0; run/observation/snapshot/registry/activation counts 0/0/0/0/0; ledger rows with an observation or
snapshot id 0; validator `schema --capture inactive` PASS; migrations 24/25 objects absent; schema 22 EXACT, 14 triggers ENABLE ALWAYS, roles 20 PASS.

**13. Nightly backup.** 10-03 … 10-06: `OK`, ≈636.7–638.4 MB, sha256 recorded, unit `Result=success`, `ExecMainStatus=0`; the script's `pg_restore -l` verification gates the OK line.
Identity remains `trading_user` (the nightly unit runs `nightly_backup.sh trading_production`); never switched.

**14. System health.** postgres healthy; backend healthy (API 200, public API 200); bot and proxy up; restarts 0; containers unchanged since S10; disk 21%; kernel OOM lines 0;
pins unchanged (`CURRENT_SHA=963ab19318b7`, `CURRENT_MECHANISM_SHA=cfd83f72f960`, bot `e3feb64d3e86`, `PREVIOUS_GOOD_SHA=e0991c4dc19d`).

**15. Warnings / anomalies (none an S10 regression).** (a) The 20:45Z attempt fails the freshness gate every weekday; the 22:00Z retry does the work (the designed behaviour). (b) Known
pre-existing: `BRK/A` Yahoo fetch failure (the dotted-symbol defect, now fixed in the lab), `technical_indicators` `numeric field overflow` (MCHB, 2 in the window), "No ML models found".
(c) One Postgres ERROR `cannot execute CREATE TABLE AS in a read-only transaction` is **my own Slice 12 Check A first attempt** (2026-10-06 20:13Z): the read-only guard refused it; nothing was written.
(d) The Slice 12 read-only queries and the throw-away containers ran inside the window as the bootstrap role; they are not application connections. (e) Fundamentals produced no rows for 09-29/09-30 (before S10).

**16. S10 rollback required?** No.

**17. Ready for the isolated `model_version='unknown'` → NULL fix stage?** Yes.
