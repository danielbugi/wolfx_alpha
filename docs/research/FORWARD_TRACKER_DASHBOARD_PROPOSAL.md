# Forward tracker dashboard (/lab): focused implementation proposal

Status: PROPOSAL, nothing built. Read-only slice over the three `paper_*` tables the tracker already writes. No new table, job, timer, pin or image. Production writes: none.

## Scope in one paragraph

A new authenticated, read-only backend endpoint set under `/api/lab/forward-tracker/*` and one new `/lab` view, "Forward paper tracker (live)". It reads only stored forward evidence (`paper_session`, `paper_observation`, `paper_event`, plus `stock_prices` for marking open positions and `candidate_capture_run` for completeness). The existing `/lab` reports are static, versioned research files; **the live view is a separate tab with its own banner and `source: "forward"` in every payload**, so a historical replay number can never appear beside a forward number without a label. Replay evidence stays in the static `strategy_experiments` report.

## Panels and exactly where each number comes from

| Panel | Source | Definition (stated in the UI) |
|---|---|---|
| Latest processed session | `paper_session` newest row | session date, scope, candidates, registered, entered, closed, not-enterable, skipped-open, open-after, snapshot hash prefix, code ref, how many sessions processed since activation (2026-10-14) |
| Open positions by variant | newest session's `open_state` + `paper_event` | per variant: pending entry (registered, entry at next open), open (entered, not closed), oldest open age in bars; one row per variant, five variants side by side |
| Closed positions | `closed` events | per variant: n, exit-reason mix, mean bars held, **realised net R at 0 / 10 (primary) / 25 bps** (recorded on the event, not recomputed), median, share positive; the standardised R (1R = 2 x ATR) and the variant's actual-risk R both shown |
| MFE / MAE | `closed` event payload (`mfe_r_std`, `mae_r_std`) and the open snapshot's running values | mean and distribution in R; open positions show running MFE/MAE |
| Unrealised paper performance | open positions + latest `stock_prices` close | mark-to-market at the last loaded close, gross and net-if-closed-at-10-bps, in R; labelled "marked at <date> close, not a result". Positions whose symbol has no loaded bar are counted and listed as unmarked, never zero-filled |
| Big winner candidates | open snapshot running MFE; `hindsight` events for closed ones | open positions whose running MFE reached >= 3R / 5R; for closed positions the `hindsight` event (max favourable excursion after the signal, capture ratio, premature exit). Everything from `hindsight` events carries the badge "hindsight, not known at decision time" (`decision_time_information: false` is already in the payload) |
| Variant comparison | the above, per variant | table of the five variants on identical candidates: positions, open, closed, net R at 10 bps with n, win rate, profit factor, MFE/MAE, capture ratio, premature-exit share. Interval shown only when closed n >= 30 (the strategies module's `min_sample_size` convention); below that: "n too small", no ranking arrows |
| Data completeness | `candidate_capture_run`, `paper_observation.context` | per session: capture status, candidates, observations with market regime attached, relative strength attached, catalysts available (today: none; shown as "no catalyst feed" with the stored reason), sector known, guard-rejected count, and the list of sessions with any gap |

## Honesty rules (enforced in code and tests)

* Every payload carries `source: "forward"`, `since: "2026-10-14"`, `sessions_processed`, and the denominators. The UI never shows a ratio without its n.
* No "profitable" / "edge" wording; status words come from fixed rules over counts and intervals, as in `research_insights`.
* Numbers are read back from stored events. The only computation is aggregation (counts, means, medians, intervals) and the mark-to-market of open positions from loaded prices.
* Replay or shadow data are never read by these endpoints (they live in static files). The `strategy_experiments` report keeps its own "historical replay" labels.
* A session that is missing, incomplete or refused shows as a gap row, not as zeros.

## Backend (small)

* `mechanism/research/paper_dashboard.py` (pure, no I/O): functions that take rows already fetched and return the payloads above. Same pattern as `strategy_analytics` and `market_intelligence.payload`; unit-testable without a database.
* `backend/services/forward_tracker_service.py`: fetches rows with read queries (read-only transaction), calls the pure module; answers `available: false, reason` if the tables are absent or unreadable, never a 500 (same contract as `market_intelligence_service`).
* `backend/routers/forward_tracker.py`: `GET /api/lab/forward-tracker/summary`, `/positions?variant=&status=`, `/closed?variant=`, `/completeness`. All behind `require_authenticated_user`, all GET, paginated where lists can grow (closed positions).
* Registered next to `lab_reports_router`. The runtime role already has SELECT on the three tables (roles verifier).

## Frontend (small)

* One component `ForwardTracker.tsx` plus a tab in `/lab`, reusing the existing `Table`, `fmtR`, `fmtInt` helpers and the "no NaN / undefined / null" test pattern.
* The page opens on a banner: "Forward paper observation since 2026-10-14. Paper only: no orders are placed. Not advice." followed by the completeness strip, then the variant comparison, then open / closed / big-winner panels.

## Tests

* Pure-module unit tests on hand-built rows (empty, one session, many sessions, unmarked positions, n below the interval threshold, hindsight badge present).
* DB test on the existing validation environment: replay a few sessions through the real tracker, call the service, and assert every payload number equals a direct SQL aggregate (so the dashboard can never drift from the stored events).
* Router test: unauthenticated 401, `available:false` when tables are absent.
* Frontend vitest with a fixture built from that DB test's output; asserts labels and that nothing renders without its n.

## Effort and order

1. Pure module + tests (about half a day of work, no infrastructure).
2. Service, router, DB test.
3. Frontend tab and fixture.
4. Release through the established process (backend CD, Vercel from a clean export, authenticated production check). No mechanism image, no pin, no timer, no migration.

## Explicitly out of scope

Any write path, backfill, new strategy or variant definition, broker or order concept, charts that mix replay with forward, alerts, and any change to the tracker, collector or bot.

## Open questions for the owner

1. Minimum closed n before showing an interval (proposed: 30).
2. Whether open-position marks should use the last loaded close only (proposed) or also an intraday quote (not proposed: no new data source).
3. Whether the big-winner thresholds on open positions should be 3R and 5R (proposed) to match the research definition.
