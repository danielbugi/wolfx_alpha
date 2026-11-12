# Evidence Ledger v0: prospective outcomes for Opportunity Intelligence

Status 2026-11-12: **v0 built (draft PR), first real capture taken manually; no schedule, no migration, nothing deployed.**
Question it answers, by measurement and not by assumption: *after the business improved, do companies whose market response was positive and
companies whose market response was not positive behave differently over the next 30, 90 and 180 days?*

## What is captured (append-only, hash-chained JSON lines; `mechanism/evidence_ledger/store.py`)
| Stream | Record | Rule |
|---|---|---|
| `observation` | Every Opportunity Intelligence company-quarter of the captured session's window (the whole research universe, all categories): evidence as seen (dimensions, category, accounting facts, market excess, contradictions, filing), its sha256, the **point-in-time sector** (`sector_observation`, captured at or before the capture), the **actual capture time**. | First capture wins. The same evidence later is a no-op; changed evidence is a `observation_revision` that never replaces the discovery. |
| `consensus` (`consensus_pre_release_v0`) | Each EPS estimate on file for a report in the next 60 days, with the vendor fetch time and the actual capture time. | Kept only if BOTH times precede 00:00 New York on the report date. A past report is never captured; nothing is verified retroactively. A changed estimate is a revision. |

**Basis.** *prospective* = first public on the captured session or the one before. *late_first_capture* = first seen later (e.g. the 597-row backlog
present when the ledger started on 2026-11-12). Reported separately, never pooled. Nothing is reconstructed backwards.

## Outcome conventions (`mechanism/evidence_ledger/model.py`)
- **Group** (two compared groups + the rest): *business improved, market positive* · *business improved, market not positive* (flat or fell) ·
  *market unreadable* (distorted window) · *business not clearly improved* · *awaiting market window*. A timely discovery is captured before its
  3-session market window closes, so its group comes from the **first later capture with the window complete** (a ledger revision).
- **Entry** = close of the first session whose 16:00 New York close is after the classifying capture. **Exit** for N in 30/90/180 = close of the
  last session on or before entry + N calendar days; complete only once the data reaches entry + N.
- **Benchmarks**: S&P 500 (`^GSPC`) over the same entry → exit; **sector peer median** (stocks with the same point-in-time sector and closes on
  both days; built from today's universe, so it carries its own survivorship).
- **Statuses, all in the denominator**: pending · complete · unreliable (discontinuity or 2x–20x split-like day: counted, no return) ·
  ended_early (no close at exit though the window is complete: delisting, halt or gap; return to the last close shown apart) · no_entry.
- Summaries: per group and horizon, counts of every status, median excess vs S&P 500 and vs sector, share above the S&P 500, over complete
  horizons only, with the denominator shown beside them.

## First real capture (2026-11-12 08:14:42 UTC, session 2026-11-11, production read-only)
611 discoveries (14 prospective, 597 late backlog) · 610 with a point-in-time sector · groups: market positive 179, market not positive 139,
not clearly improved 284, awaiting market window 9 · 398 pre-release consensus snapshots · 1,009 ledger lines. A second capture minutes later
added nothing (same chain head). Every horizon is pending: the first 30-day results mature from 2026-12-14.

## Data gaps
1. **No schedule**: v0 captured once by hand. Without a nightly capture there are no new prospective discoveries and no classifying revisions.
2. **Storage**: a file in the deploy user's home on the VPS, not in the nightly database backup. A database table (migration) or including the file
   in the backup is needed before it is relied on.
3. **Sector benchmark**: no sector ETFs in the database; the peer median is a proxy with survivorship.
4. **Splits**: `adj_close` equals `close`; distorted windows are excluded (unreliable), not corrected. 3:2 / 5:4 splits are not detectable.
5. **Delistings**: the universe is today's listed names; a delisting shows as ended_early only after the window is complete.
6. **Consensus history before 2026-11-12 does not exist** and will never be claimed.

## Smallest next production action (needs approval)
One nightly capture after the pipeline (session explicit), e.g. an optional pipeline step or a small timer running
`cli.py capture --session <gate session> --store /opt/donchian/evidence_ledger/ledger.jsonl`, plus adding that file to the nightly backup.
