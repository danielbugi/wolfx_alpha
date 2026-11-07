# Fundamental Updates: inflection_v1 vs inflection_v2_sec_pit

Read-only comparison on production, 2026-11-07. Queries: [fu_source_comparison.sql](fu_source_comparison.sql) (re-runnable in a forced read-only session). **Nothing was switched.**

## Recommendation
**Use `inflection_v2_sec_pit` as the source of public Fundamental Updates**, with the Phase 1 publishability rules (verified SEC date, at most 104 days from quarter end to public date, detected on or after the public date, at least two figures).

On every measure that matters for a public, filing-linked post, v2 is equal or better:
- it is the only version producing events this reporting season;
- it has no point-in-time failures;
- every event is traceable to its SEC filing;
- it detects new filings within 1–3 days.

Its weaknesses are manageable:
- **Volume:** 551 fresh events in five days of peak season. Posts need a nightly cap.
- **Coverage gap:** companies with no SEC financial data (55 v1 events in the overlap window) get no updates.

Switching the owner alerts (`ALERT_METHOD_VERSIONS`) is a separate decision and is not part of this.

## 1. Coverage
| | v1 | v2 |
|---|---|---|
| Source | `quarterly_fundamentals` (provider data) | `sec_edgar_pit` (SEC XBRL, point-in-time) |
| Events | 1,322 | 3,609 |
| Companies with an event | 732 | 1,342 |
| Company-quarters computed (feature rows) | 15,896 rows, 3,001 companies | 7,679 rows, 2,834 companies |
| Quarter ends covered | 2024-09-30 … 2026-08-31 | 2025-03-29 … 2026-10-04 |
| Current season (quarter end on or after 2026-09-01) | **0 events** | **921 events, 573 companies** (1,844 quarters computed) |
| Detection runs | one run on 2026-10-31; **nothing detected since** | first run 2026-11-03; detecting nightly |

**Why v1 has stopped:**
- Its source table `quarterly_fundamentals` was last updated on **2026-10-26**, and no company in it has a September quarter.
- So v1 cannot produce current-season events, and the owner alerts, which read only v1, have nothing new to send.
- This is a separate operational issue worth its own look.

## 2. Event types
| Type | v1 | v2 |
|---|---|---|
| Revenue acceleration | 28 | 935 |
| Margin expansion | 475 | 998 |
| Operating leverage | 328 | 686 |
| Net income turnaround | 207 | 406 |
| FCF turnaround | 197 | 368 |
| Limited repricing (not publishable) | 87 | 216 |

v1 rarely detects revenue acceleration (28 events). It needs a previous quarter's year-over-year growth, and that history is thin in its source.

## 3. Overlap and missed events
Window: quarter ends from 2026-03-01 to 2026-08-31, which both versions computed. The five public types only. A match means the same symbol and type, with quarter ends within 10 days (fiscal calendars differ).

| | Count |
|---|---|
| v1 events | 1,174 |
| v2 events | 2,463 |
| Matched pairs | **898**: 76% of v1, 36% of v2 |

**v1 events with no v2 match (276):**

| Reason | Events |
|---|---|
| v2 computed that quarter but found no event (different thresholds or figures) | 108 |
| v2 has other event types for that company-quarter | 87 |
| The company has no SEC financial data in v2 | 55 |
| v2 did not compute that quarter | 26 |

By type, where v2 computed the quarter: margin 61, operating leverage 55, FCF 37, net income 36, revenue 6.

**v2 events with no v1 match (1,565):**

| Reason | Events |
|---|---|
| v1 computed that quarter but found no event | 1,292 |
| v1 has other event types for that company-quarter | 208 |
| v1 did not compute that quarter | 65 |

By type, where v1 computed the quarter: revenue acceleration 666, margin 332, operating leverage 216, net income 148, FCF 138.

**Do the figures agree?** For matched pairs, here is how often the headline metric is within 1% and 5% between versions:

| Type | Pairs | Within 1% | Within 5% |
|---|---|---|---|
| FCF turnaround | 143 | 85.3% | 95.1% |
| Margin expansion | 358 | 75.7% | 87.2% |
| Operating leverage (revenue growth) | 240 | 98.8% | 98.8% |
| Net income turnaround | 145 | 99.3% | 99.3% |
| Revenue acceleration | 12 | 83.3% | 91.7% |

- Most differences are in operating margin, where providers and the SEC filing define operating income differently.
- **For a post that links to the SEC filing, the filing's own figure (v2) is the one to show.**

## 4. Point-in-time correctness
| Check | v1 | v2 |
|---|---|---|
| Detected **before** its public date (re-dated by a later revision) | **19** | **0** |
| Public more than 104 days after quarter end | 27 | 7 (late filers; Phase 1 now rejects these) |
| Public date not verified (assumed lag or first seen) | 109 | 0 |
| Public before the quarter ended | 0 | 0 |
| Median days from quarter end to public | 36 | 36 |

**Public dates on matched pairs (v2 minus v1):**
- Where v1 used the earnings calendar (833 pairs), the median gap is **0 days**. The range is −91 to +26.
- In 16 of those pairs, the SEC date is more than 7 days *earlier* than v1's, which means v1's calendar date is suspect. That is the misdating family behind the 16 misleading owner alerts.
- Where v1 assumed a 75-day lag, the real SEC date was **30 to 53 days earlier**: v1 was late, but never early.

## 5. Detection speed (live)
| | Events public since the version's first run | Detected on or after the public date | Days from public to detection |
|---|---|---|---|
| v1 (since 2026-10-31) | 19 | **0** (all 19 are re-dated old quarters) | — |
| v2 (since 2026-11-03) | 551 | **551** | median 1, max 3 |

## 6. Filing traceability
| | v1 | v2 |
|---|---|---|
| Events with the exact SEC filing (accession) of their quarter | 0 (its source holds no filing ids) | **3,609 of 3,609** |
| Feature rows with an accession | 0 | 7,679 of 7,679 |

The exact-filing SEC button is therefore possible for every v2 update. For v1 it could only be the company's filing list.

## Conditions attached to the recommendation
1. **Gating rules:** Phase 1's rules apply in full: SEC-verified date, the 104-day cap, detection on or after the public date, at least two figures.
2. **Volume:**
   - a nightly cap on posts (3 per night planned for the dev channel);
   - a deterministic choice of which ones;
   - the website lists all publishable updates.
3. **Not covered:** companies without SEC financial data are not covered. That is acceptable for a filing-linked product.
4. **Separate decisions, not taken here:**
   - whether to repair or retire v1's source (stale since 2026-10-26);
   - whether to move the owner alerts to v2.
