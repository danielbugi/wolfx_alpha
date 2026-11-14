# Evidence Ledger v0: prospective outcomes for Opportunity Intelligence

Status 2026-11-12: **v0 built (draft PR), first real capture taken manually; nightly capture + backup leg built and committed DORMANT; no migration, nothing deployed or scheduled.**
Question it answers, by measurement and not by assumption: *after the business improved, do companies whose market response was positive and
companies whose market response was not positive behave differently over the next 30, 90 and 180 days?*

## What is captured (append-only, hash-chained JSON lines; `mechanism/evidence_ledger/store.py`)
| Stream | Record | Rule |
|---|---|---|
| `observation` | Every Opportunity Intelligence company-quarter of the captured session's window (the whole research universe, all categories): evidence as seen (dimensions, category, accounting facts, market excess, contradictions, filing), its sha256, the **point-in-time sector** (`sector_observation`, captured at or before the capture), the **actual capture time**. | First capture wins. The same evidence later is a no-op; changed evidence is a `observation_revision` that never replaces the discovery. |
| `consensus` (`consensus_pre_release_v0`) | Each EPS estimate on file for a report in the next 60 days, with the vendor fetch time and the actual capture time. | Kept only if BOTH times precede 00:00 New York on the report date. A past report is never captured; nothing is verified retroactively. A changed estimate is a revision. |

**Basis.** *prospective* = first public on the captured session or the one before, captured on that session's own night. *delayed_processing* = the
same, but the ledger's night was missed and the session was processed on a later run (below). *late_first_capture* = first seen later (e.g. the
597-row backlog present when the ledger started on 2026-11-12). The three are reported separately, never pooled. Nothing is reconstructed backwards.

**Missed nights.** Every session the ledger deals with gets one `capture_run` record: `on_time`, `delayed_processing` or `missed`.
- On its next run the ledger processes the newest 5 sessions it missed, oldest first, before the latest session. Each is built from the same
  point-in-time brief that night would have produced: every event in it was detected and last changed by the system, in database-stamped
  timestamps, before that session's knowledge cutoff. So the evidence is what was already recorded at the time, not a reconstruction.
- Such records carry `basis=delayed_processing`, `processing=delayed_processing`, `evidence_cutoff` and `original_available_at` (the filing's
  public time), and their **actual** `captured_at`. Nothing is back-dated: the outcome clock starts after the actual capture, like every record.
  Their sector is the one known by the cutoff. No consensus is captured for them: estimates are only ever captured live.
- Sessions further back, or whose prices never arrived, are recorded as `missed`, a genuine gap with no observations.
- `report` shows `session_coverage` (on_time / delayed / missed / no_record) and a separate `delayed_processing` summary.

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
1. **Not scheduled yet**: the nightly unit is built but dormant (see below). Until it is armed there are no new prospective discoveries and no classifying revisions.
2. **Storage**: until activation, a file in the deploy user's home on the VPS, not backed up. The backup leg (below) covers `/opt/donchian/evidence_ledger`; a
   database table is a later option (migration), not needed for v0.
3. **Sector benchmark**: no sector ETFs in the database; the peer median is a proxy with survivorship.
4. **Splits**: `adj_close` equals `close`; distorted windows are excluded (unreliable), not corrected. 3:2 / 5:4 splits are not detectable.
5. **Delistings**: the universe is today's listed names; a delisting shows as ended_early only after the window is complete.
6. **Consensus history before 2026-11-12 does not exist** and will never be claimed.

## Nightly capture + backup (built, committed DORMANT; activation needs owner approval)
| Piece | What it does |
|---|---|
| `cli.py capture --latest-completed` | Session = latest COMPLETED session on the market calendar (`shared.market_calendar`, close + 120 min), never the wall clock; the weekday fallback is refused (exit 5). The session's `^GSPC` bar and ≥1,000 stock bars must be stored, else exit 2 (the later fire retries). Exclusive lock `<store>.lock` (exit 3). A corrupt or half-written ledger is refused (exit 9), nothing appended. |
| `deploy/vps/run_evidence_ledger.sh` | One-off `docker compose run --rm channel-sender` with a read-only database session, the ledger dir mounted at `/ledger`. **Its own pin and arming file** (`CURRENT_EVIDENCE_LEDGER_SHA`, `EVIDENCE_LEDGER_ARMED`): never reads or moves the mechanism pin, so arming it cannot change the pipeline image. |
| `donchian-evidence-ledger.{service,timer}` | 07:30 and 10:15 Asia/Jerusalem (after the pipeline, final post-market retry, discontinuity scan and collector; before the 11:45 backup). `Persistent=false` (no catch-up on boot); a missed night is handled by the next run (delayed processing or missed, above). Failures (exit 2/5/8/9) fire `donchian-evidence-ledger-alert.service`. |
| `deploy/db/nightly_backup.sh` | Copies `/opt/donchian/evidence_ledger/ledger.jsonl` into the night's backup dir under the ledger lock, with its sha256. Two independent status lines: `OK db=…` and `LEDGER OK|NOT_ACTIVATED|FAILED …`. A ledger failure (file missing once activated, copy/checksum failure, lock timeout) keeps the good database dump and exits **4**; a dump failure exits 1. Both fire `donchian-nightly-backup-alert.service` → `run_ops_alert.sh` (logs/ops_alerts.log + one owner-only private message, with a per-status explanation). `pull_nightly_backup.ps1` pulls and checks the ledger copy off-box when present. |

Verified 2026-11-12: 63 tests on Linux (wrapper refusals and the real backup script against a stub docker: not activated / OK / missing file /
held lock / failed dump); an end-to-end `--latest-completed` run on production (read-only, against a copy of the ledger) resolved session
2026-11-11 at 20:39Z and appended nothing (611 unchanged).

### Activation (owner approval; root steps, outside any Fundamental Updates observation night)
1. Merge this PR; build the mechanism image at the merge commit (CD build-and-push only; deploys nothing). Its tag `E` is the ledger's pin; the
   mechanism pin `CURRENT_MECHANISM_SHA` is NOT changed.
2. Root: install the new `nightly_backup.sh`, `run_evidence_ledger.sh` and `run_ops_alert.sh` (mode 755; the alert wrapper is not on the VPS yet)
   into `/opt/donchian/scripts`; install the units, timer and both alert units into `/etc/systemd/system`. The backup unit now has
   `OnFailure=`. Owner alerts are only delivered once `OPS_ALERTS_ENABLED=1` is in `.env`; until then they are logged only, and that is a
   separate owner decision. `mkdir -m 700 /opt/donchian/evidence_ledger`; move the existing manual ledger in (`cp -p ~deploy/evidence_ledger/ledger.jsonl`,
   check its sha256 `06503be3…`); `echo E > CURRENT_EVIDENCE_LEDGER_SHA`; `echo E > EVIDENCE_LEDGER_ARMED`.
3. `run_evidence_ledger.sh report` (read-only) must load the chain (head `4edf18e8…`); then `systemctl daemon-reload && systemctl enable --now donchian-evidence-ledger.timer`.
4. Next morning: the 07:30 capture log line, then the 11:45 backup's `OK db=` and `LEDGER OK … lines=` lines.
Rollback: `systemctl disable --now donchian-evidence-ledger.timer`; remove the arming file. The ledger file stays (append-only evidence).
