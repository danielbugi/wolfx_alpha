# Pipeline reliability: the daily first-fire failure and the quarterly-fundamentals timeout

Status 2026-10-15: **PROPOSAL, nothing applied.** No timer, unit, pin or image on the VPS was changed. Evidence below is from read-only logs and queries.

## 1. What happens today

`donchian-pipeline.timer` fires at 23:45 and 01:00 Asia/Jerusalem (20:45Z and 22:00Z in summer). On every trading night since at least 2026-10-05 the **first** fire fails about 15 minutes in and the **second** succeeds:

| Night | First fire (20:45Z) | Bars present at the gate | Second fire (22:00Z) |
|---|---|---|---|
| 10-05 | FAILED | 212 of ~3,066 (6.9%) | OK 23:21Z |
| 10-06 | FAILED | 222 (7.2%) | OK 23:24Z |
| 10-07 | FAILED | 0 (0.0%) | OK 00:13Z (+1 day) |
| 10-08 | FAILED | 47 (1.5%) | OK 23:25Z |
| 10-09 | FAILED | 7 (0.2%) | OK 23:25Z |
| 10-12 | FAILED | 17 (0.6%) | OK 23:24Z |
| 10-13 | FAILED | 1 (0.0%) | **killed by the 4 h timeout** (section 3), rescued ~3 h later |
| 10-14 | FAILED | 227 (7.4%) | OK 23:25Z |

The failure is the pipeline's own freshness gate working as designed: `NOT FRESH: 227 rows for 2026-10-14 vs 3064 on 2026-10-13 = 7.4% coverage (need 90%)`. It leaves the session unmarked, writes nothing partial, and the next fire retries. Weekends skip cleanly. So the first fire is a **deterministic daily failure**, not a flaky one.

## 2. Vendor readiness (measured)

`stock_prices.created_at` (naive UTC) records when each bar was first inserted by any of our fetchers. For the VPS-era nights, percentiles of the insert time of each session's bars (UTC):

| Session | first | 5% | 50% | 90% |
|---|---|---|---|---|
| 10-05 | 20:57 | 20:59 | 21:13 | 21:27 |
| 10-06 | 20:56 | 20:59 | 21:14 | 21:28 |
| 10-07 | 21:03 | 21:10 | 21:26 | 21:41 |
| 10-08 | 20:47 | 21:03 | 21:21 | 21:29 |
| 10-09 | 20:45 | 21:08 | 21:27 | 21:43 |
| 10-12 | 20:46 | 21:05 | 21:20 | 21:29 |
| 10-13 | 20:46 | 21:12 | 21:28 | 21:43 |
| 10-14 | 20:47 | 20:59 | 21:14 | 21:28 |

Reading: 90% of the day's bars were obtained by **21:27-21:43Z** on every night, 1h27-1h43 after the 20:00Z close. This is an upper bound on vendor readiness, quantised by our own fetch cadence (the post-market retry probes at 20:45, 21:00, 21:20, 21:40Z); it measures when we *captured*, not when the vendor *published*. The daily updater needs ~14.5 minutes for its 3,075 symbols before the freshness gate is evaluated, so a pipeline that **starts at 21:45Z checks at ~22:00Z**, after all eight observed nights' 90% points.

The Jerusalem anchor also drifts against the US close: 20:45Z is 45 minutes after the close in US summer time, but 1h45 after it for the week between Israel's and the US's clock changes, and 45 minutes after the (21:00Z) close again in winter. Anchoring to New York time removes the drift (verified with `systemd-analyze calendar` on the VPS, systemd 255): **17:45 America/New_York = 21:45Z in summer and 22:45Z in winter, always 1h45 after the close.**

## 3. The 2026-10-13 incident (quarterly fundamentals)

* The 22:00Z retry reached step 8/13 (quarterly fundamentals) at 23:09Z and was still there when systemd's `TimeoutStartSec=4h` killed the unit at 02:00:37Z (`start operation timed out`). Steps 9-13 of that run never executed. The session's capture and ledger rows were produced ~3 h later by the post-market retry path (ledger rows written 02:29Z instead of ~23:19Z); the data is complete.
* Why step 8 took hours: `get_symbols_to_update` selects symbols never fetched or last checked more than 25 days ago, using `MAX(updated_at)`. After the previous full pass **the whole universe falls due together**: on 10-13 about 3,055 symbols were due. At ~4.9 s per symbol (3 s rate-limit sleep plus three yfinance calls) that is ~4 h. The data confirms it: 753 symbols were written on 10-13 (UTC) and 2,186 on 10-14 before the kill, 2,939 in all, with ~116 left.
* Why `ok=0 fail=116` on a healthy night: 117 symbols with prices have **never** produced a quarterly row (the script's own comment says closed-end funds and similar; I did not classify them one by one; 39 more are already in `inactive_symbols`). A failed symbol never has its `updated_at` refreshed, so these ~116 are due and fail **every night** (~7 minutes).
* Recurrence: the cohort is now synchronised again (753 symbols checked on 10-13, 2,186 on 10-14), so the next full cohort falls due on **2026-11-07 / 11-08**. Under the current code that night needs ~3 h in step 8 after ~1 h of earlier steps and would hit the 4 h limit again.
* Downstream dependency: nothing in the pipeline reads `quarterly_fundamentals` after step 8. Checked in the code: the screener, the ML dataset rebuild, both ML trainings, the signal ledger, the forward collector and the post-market package do not (the only "quarterly" matches in `mechanism/alerts` are options-expiry helpers). Its readers are the backend services (deep value, earnings, stock pages), the lab studies and step 7's calendar (which runs *before*). Step 8 can therefore be bounded and made non-blocking without breaking the chain.

## 4. The proposed correction (three independent parts)

**A. Timer (no image needed).** `deploy/vps/donchian-pipeline.timer`: `OnCalendar=*-*-* 17:45:00 America/New_York` and `OnCalendar=*-*-* 19:15:00 America/New_York` (retry 90 minutes later), `Persistent=true` and `RandomizedDelaySec=60` unchanged. Expected: the first fire passes the gate on the first attempt; completion ~23:10Z, about 15 minutes earlier than today's 23:25Z. The second fire stays a no-op once the session is marked done.

**B. Bounded quarterly updater.** `quarterly_fundamentals_updater.py`: `--time-budget-seconds` stops cleanly before starting a new symbol once the budget is spent; the due list is ordered stalest-successful first and never-succeeded symbols last, so the hopeless 116 can never starve real refreshes. Unreached symbols keep their old `updated_at` and are first in line next night: the 2026-11-07/08 cohort is worked off at ~550 symbols per 45-minute night over ~5-6 nights instead of one unbounded run. No schema change.

**C. Optional-step wrapper.** `automation_pipeline.sh`: `run_optional_step` runs step 8 under `timeout 3300` (+30 s SIGKILL) with `--time-budget-seconds 2700`; a failure, a timeout or a short run logs a WARNING and the pipeline continues to the screener, ML and publication. Every other step keeps aborting on failure.

### Expected behaviour

| Situation | Today | After A+B+C |
|---|---|---|
| Normal night | first fire fails, second succeeds ~23:25Z | first fire succeeds ~23:10Z, second is a no-op |
| Vendor late (bars < 90% at the gate) | run fails, next fire retries | same, but the retry is 90 min later with the same safety net |
| Step 8 normal (~116 hopeless symbols) | ~7 min, `ok=0 fail=116` | same symbols, now last in the queue and inside a 45 min budget |
| Whole universe due (next 2026-11-07/08) | ~3-4 h, unit killed, steps 9-13 skipped | 45 min budget, rest deferred, steps 9-13 run on time |
| Step 8 hangs or crashes | pipeline aborts (or 4 h timeout) | killed at 55 min, WARNING, pipeline continues |
| Both fires fail | post-market retry path rescues (as on 10-13) | unchanged |

### Failure handling that stays as is
The freshness gate (90%), the unmarked-session retry semantics, the pipeline lock shared with the post-market retry and discontinuity scan, and the post-market retry rescue are not changed. This proposal moves *when* the first attempt happens and bounds *one* optional step; it does not weaken a safety check.

## 5. Tests (all in the repo)

* `mechanism/data_updaters/tests/test_quarterly_budget.py`: budget stops before a new symbol and reports `symbols_remaining`; zero budget attempts nothing; a failing symbol does not stop the run; due list ordering in the SQL; CLI flag; **the shell wrapper** (`run_optional_step` extracted from the real script and run against a stub interpreter): success continues, failure warns and continues, a hang is killed at its limit and the pipeline continues; step 8 is the only optional step and carries both limits.
* `mechanism/forward_collection/tests/test_quarterly_ordering_db.py`: the real SQL on a throwaway production-shaped database: fresh and inactive symbols excluded; order `STALE60, STALE30, STALE26, NEVER_A, NEVER_B`; `limit` takes the stalest successful symbols.
* Existing suites (`test_scan_wrapper_and_timing`, the data-updater tests, the activation-neutral guards) pass unchanged.

## 6. Rollout (each stage needs its own approval; none performed)

1. **Stage 1, timer only (root, no image):** back up the unit (`cp /etc/systemd/system/donchian-pipeline.timer{,.bak-<ts>}`), install the reviewed file, `systemctl daemon-reload`, `systemctl restart donchian-pipeline.timer`, confirm `systemctl list-timers donchian-pipeline.timer` shows 21:45Z / 23:15Z. Verify the next trading night: first fire OK in `pipeline_runs.log`, `NOT FRESH` absent. **Rollback:** restore the `.bak` file, daemon-reload, restart the timer.
2. **Stage 2, image (B and C):** build the mechanism image from the merged commit (existing build-and-push, deploys nothing), then change `CURRENT_MECHANISM_SHA`, which the collector's arming file must equal, so re-arm the collector in the same window and re-verify the collector/bot pins. This touches the pins the owner has told me to leave alone, so it needs explicit approval. It must land **before 2026-11-07**. **Rollback:** restore the previous pin and arming file (documented in `docs/operations/ROLLBACK.md`).
3. The tracker (`CURRENT_PAPER_SHA`, its timer) is independent of both stages and is not touched; its 09:30/11:30 fires already run after the pipeline's new 23:10Z completion and the collector's 06:45Z-local rows.

## 7. Not proposed
Moving step 8 to the end of the script (a possible later simplification), changing the freshness threshold, and any change to the post-market retry timer.
