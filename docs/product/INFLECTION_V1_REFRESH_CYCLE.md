# inflection_v1: refresh-cycle limitation and what still depends on it

2026-11-07. **Documentation only.** The owner decided to leave V1 unchanged for now. No code, schedule or data was changed.

## The limitation
`inflection_v1` is computed from `quarterly_fundamentals`, the yfinance statements written by pipeline step 8, `quarterly_fundamentals_updater.py --time-budget-seconds 2700`. That updater re-checks a symbol only when its newest row's `updated_at` is **more than 25 days old** (`get_symbols_to_update(recheck_after_days=25)`). There is no earnings-date trigger.

**Consequences** (verified read-only, see `FUNDAMENTAL_UPDATES_INTEGRATION_REVIEW.md` §3):
- **The universe moves as one batch.** 3,055 symbols were all due and refreshed on 2026-10-13/14, so all of them come due together again 25 days later (753 on Nov 7 and 2,186 on Nov 8).
- **Long silent gaps.** Between batches, only about 115 never-succeeding listings (preferreds and notes) are attempted each night. A company that reports the day after its batch check is not fetched for up to **25 days, plus the catch-up time**. Example: September quarters reported Oct 14 – Nov 7 reach V1 only from Nov 9.
- **Slow catch-up.** The 2,700 s budget covers about 730 symbols a night (about 3.7 s per symbol), so a 2,900-symbol batch takes **about 4 weeknights** (Nov 9–12 this cycle).
- **Late events.** V1 events for reports in a gap are detected days or weeks after they were public. The owner-alert freshness rule (public within 3 days) then suppresses most of them. That is correct behavior, but it means **V1 misses most of a reporting season**. In the V1/V2 comparison, V1 had 0 current-season events against V2's 921.
- **Recurring pattern.** The batch keeps its alignment: after each catch-up, rows are stamped within a few nights of each other, so the next cycle bunches again.

**Documentation drift:** the docstring of `mechanism/data_updaters/earnings_calendar_updater.py` says the quarterly updater uses `earnings_calendar` as "an event-driven staleness gate". It does not. `get_symbols_to_update()` uses only the 25-day `updated_at` timer, and its own docstring says no earnings-calendar source is needed. Fix the docstring if V1 is kept.

## What still depends on V1
| Dependency | Where | Effect of the cycle |
|---|---|---|
| **Owner Telegram inflection alerts** | `mechanism/inflection/alerts.py`, `ALERT_METHOD_VERSIONS = (inflection_v1,)` | Alerts arrive in bursts after each batch, and most are suppressed as not fresh. Genuine fresh alerts are possible from Mon Nov 9 during this catch-up |
| **Internal inflection API and page** | `backend/services/inflection_service.py` (`METHOD_VERSION = "inflection_v1"`), `/api/inflection/*`, the `/inflection` page | Shows V1 events only, so the newest quarters lag by up to about 4 weeks |
| **Data-quality page reconciliation** | `backend/services/data_quality_service.py` (V1 vs `inflection_v1_sec`, SEC latest values vs V2) | Reconciliation counts depend on where V1 is in its cycle |

**Not V1-specific but on the same table:** the following read `quarterly_fundamentals` directly and inherit the same lag for the newest quarter:
- the stock page fundamentals (`stock_service.py`);
- Deep Value checks (`deep_value_service.py`);
- system-health coverage (`system_health_service.py`);
- research extracts (`mechanism/research/strategy_lab/extract_fundamentals.py`);
- ML experiment scripts (`ml_training/experiments/*`).

**Does not depend on V1:**
- **Fundamental Updates** (`update_*` modules, `/signals`, `/api/public/signals`): built for `inflection_v2_sec_pit` and its SEC accession. Not wired to any schedule.
- **inflection_v2_sec_pit:** computed from SEC filings, not from `quarterly_fundamentals`.

## Options, for when V1 is revisited (none approved)
1. **Retire V1 for alerts** by switching `ALERT_METHOD_VERSIONS` to V2. This is the planned source switch and needs its own approval.
2. **Stagger the recheck,** for example `updated_at` plus a per-symbol offset from a symbol hash, so about 1/25 of the universe is due each night.
3. **Check around earnings dates:** re-check a symbol on the 1–3 nights after an `earnings_calendar` report date, in addition to the timer.
4. **Park never-succeeding listings:** stop re-trying symbols that have never produced a quarterly row (for example via `inactive_symbols`), recovering about 115 × 3.7 s ≈ 7 min a night.
