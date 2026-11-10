# Fundamental Updates: deployment runbook (owner chat first)

Status 2026-11-10: **prepared, nothing deployed, applied or sent.** Each numbered step below needs the owner's explicit approval, in order.
Scope: replacing the legacy owner alerts (`mechanism/inflection/alerts.py`) with Fundamental Updates **in the owner's private chat only**
(rollout gate 4 in [../product/FUNDAMENTAL_UPDATES.md](../product/FUNDAMENTAL_UPDATES.md)). Dev channel, public channel, the public
`/signals` pages and the correction job are later, separate approvals.

## 0. Where things stand (verified read-only against production, 2026-11-10)
| Item | State |
|---|---|
| Legacy owner alerts | **Off since 2026-11-10 08:11 UTC** (`INFLECTION_ALERTS_ENABLED=0` in `/opt/donchian/env/.env`, owner-approved; backup `.env.bak-inflection-alerts-off-20261110T081143Z`). Detection and storage still run nightly. |
| Migration 41 objects | None exist (`fundamental_update_publication`, `fundamental_update_current`, both functions: absent). Additive, no name clash. |
| Runtime role | `donchian_app` exists. PostgreSQL 16.15. |
| Mechanism image | `1fc1753cdae0` (derived). It has `inflection/alerts.py` (`DbDelivery`), `alerts/telegram_client.py` (`send_photo`, `CAPTION_MAX`, `TEXT_MAX`, `text_length`) and the card fonts, but **none** of the `inflection/update_*.py` modules. |
| Source events | `inflection_v2_sec_pit` (SEC acceptance dates and accession per quarter): 2026-11-09 run ok, 0 errors. |
| Selection dry run, session 2026-11-09 | 128 candidate events → GTN, HNGE, KGS (all `profitability_turnaround`). **On `main` there is no per-type cap**; the owner-approved cap of 2 per lead type and the decision record are in PR #103 (draft). |

## 1. Prerequisites (code; each a separate approval)
1. **Merge PR #103** (selection policy: 3 per night, 1 per company, 2 per lead type, empty slots allowed; decision record). Without it the first night above would post three profitability turnarounds.
2. **Merge the publisher PR** (`mechanism/inflection/update_publish.py` + tests + this runbook). It is dormant: nothing calls it.
3. Optional before or after: PR #108 (earnings-aware quarterly refresh) affects v1 only; Fundamental Updates read v2.

## 2. Duplicate-notification guards
| Layer | Guard | Effect |
|---|---|---|
| Old vs new | `update_publish.py --send` **refuses while `INFLECTION_ALERTS_ENABLED=1`** | One event can never reach the owner both as an old alert and as an update. |
| Same update twice | Delivery claim in `inflection_alert_delivery` (`UNIQUE dedupe_key`, key `fu_owner:<update_key>:v1`) | Re-runs, the 00:15 pipeline retry or a second process cannot send it again. A *failed* send is retried (bounded by `MAX_ATTEMPTS`); a claimed or sent one never. |
| Same update recorded twice | Ledger `UNIQUE (update_key, version)`, insert `ON CONFLICT DO NOTHING` | Publication is idempotent; a re-run reports `exists`. Only recorded updates are ever sent. |
| Same company again | 30-day cooldown read from the ledger | No second post for the same company within 30 days. |
| Backlog flood | `backlog_not_fresh` (public more than 3 days before the session) | A coverage expansion or first run (like SUPN's history on 11-10) never floods the chat. |
| Volume | At most 3 per night | Empty slots allowed. |
| Unpublished internals | Writer `validate_public_dict` + DB `public_keys_chk` / `public_nested_chk` | Internal scores and fields cannot be stored or served. |

## 3. Steps (in order; stop at the first unexpected result)
`$R` = a root shell on the VPS; `$PG` = `donchian-screener-postgres-1`. Every read-only check can use `default_transaction_read_only=on`.

### Step A. Backup, then apply migration 41 (production database)
```bash
$R '/opt/donchian/scripts/nightly_backup.sh'                         # or confirm last night's backup in logs/nightly_backup.log
docker exec -i $PG sh -c 'psql -v ON_ERROR_STOP=1 -1 -U "$POSTGRES_USER" -d trading_production' < mechanism/add_fundamental_update_publication.sql
docker exec -i $PG sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d trading_production' < deploy/db/fundamental_update_publication_verify.sql   # must print: migration 41 verified
```
Expected: the verify script prints `migration 41 verified`. Nothing writes to the table yet.
**Rollback:** `deploy/db/fundamental_update_publication_rollback.sql` (one transaction; refuses if rows exist unless `SET fup.confirm_drop_with_rows = 'yes'` after a backup). Reversible: yes, while empty.

### Step B. Add the modules to the derived image and re-pin (the existing Stage 2 procedure)
Add to `mechanism/Dockerfile.stage2` on the release branch, then build and re-pin with the usual runbook ([STAGE2_MECHANISM_REPIN_RUNBOOK.md](STAGE2_MECHANISM_REPIN_RUNBOOK.md)):
```dockerfile
# Fundamental Updates, owner chat only (dormant until FU_OWNER_SEND_ENABLED=1): pure modules + the publisher. Imported by nothing else in the image.
COPY mechanism/inflection/update_model.py   /app/mechanism/inflection/update_model.py
COPY mechanism/inflection/update_select.py  /app/mechanism/inflection/update_select.py
COPY mechanism/inflection/update_ledger.py  /app/mechanism/inflection/update_ledger.py
COPY mechanism/inflection/update_format.py  /app/mechanism/inflection/update_format.py
COPY mechanism/inflection/update_card.py    /app/mechanism/inflection/update_card.py
COPY mechanism/inflection/update_news.py    /app/mechanism/inflection/update_news.py
COPY mechanism/inflection/update_publish.py /app/mechanism/inflection/update_publish.py
```
**The re-pin disarms forward collection** (by design). Re-create `/opt/donchian/FORWARD_COLLECTION_ARMED` with the new tag **in the same session** (root), or the collector refuses every night (2026-10-31..11-09 lost five sessions this way).
Verify: `docker run --rm --entrypoint python <image> -c "import sys; sys.path.insert(0,'/app/mechanism'); import inflection.update_publish"` exits 0.
**Rollback:** re-pin the previous tag (`1fc1753cdae0`) and re-arm for it.

### Step C. Record-only validation (at least 3 nights; no sending)
Wire ONE pipeline step (or timer) after the inflection step that runs, for the **explicit** session just processed:
```bash
/opt/donchian/scripts/run_channel_sender.sh mechanism/inflection/update_publish.py --session <session> --record
/opt/donchian/scripts/run_channel_sender.sh mechanism/inflection/update_publish.py --session <session> --revise     # report-only while FU_CORRECTIONS_ENABLED is unset
```
Before wiring it, run both once by hand for the last completed session. Each night, check (read-only):
- `FU_PUBLISH {...}`: `published` ≤ 3, `refused` null; the picks match the decision record (PR #103) for that night;
- `SELECT update_key, version, kind, public_date FROM fundamental_update_publication ORDER BY id DESC LIMIT 10;`: version 1 rows only, public dates within 3 days of the session;
- `deploy/db/fundamental_update_publication_verify.sql` still prints `migration 41 verified`;
- `FU_REVISE {...}`: `would_change` empty or explained (a real 10-Q/A restatement);
- `SELECT count(*) FROM inflection_alert_delivery WHERE claimed_at > <step start>` = 0 (nothing claimed, nothing sent).
**Rollback:** remove the step. Recorded rows are append-only and read by nothing public yet (the `/signals` API is not deployed); they can stay, or the whole table can be removed with step A's rollback after a backup.

### Step D. Enable owner-chat sending (**explicit owner approval required**)
1. Back up `.env`; append `FU_OWNER_SEND_ENABLED=1`. Confirm `INFLECTION_ALERTS_ENABLED=0` (the publisher refuses to send otherwise).
2. Change the step to `--record --send`.
3. First night: watch `inflection_alert_delivery WHERE channel = 'fu_owner'`: at most 3 rows, all `sent`; the messages in the owner chat match the ledger rows.
**Rollback / stop:** set `FU_OWNER_SEND_ENABLED=0` (takes effect at the next run; recording continues). A message already in the chat stays; nothing is deleted.

### Step E. Corrections (separate approval, after a clean week)
`--revise` rebuilds updates first published in the last 120 days from their current event and records `restated` / `redated` / `withdrawn` versions.
- **Never sends, edits or deletes** a Telegram message; the delivery key is per update and version 1, so a correction is never re-sent as a new update.
- Records only with `FU_CORRECTIONS_ENABLED=1`; otherwise reports what it would record.
- **Mass-change breaker:** more than 10 changes in one run → nothing recorded, exit 2, review the source data (a re-dating bug, a vendor restatement batch).
- A `withdrawn` update is never revived automatically.
**Rollback:** unset `FU_CORRECTIONS_ENABLED` (stops recording). Versions already recorded are history by design (append-only).

## 3a. Full rollback, newest step first
1. `FU_OWNER_SEND_ENABLED=0` → no more owner messages.
2. Remove the publisher step → no more ledger writes.
3. Re-pin the previous image (`1fc1753cdae0`) and **re-arm forward collection for it**.
4. Optional, after a backup: `deploy/db/fundamental_update_publication_rollback.sql` with `SET fup.confirm_drop_with_rows = 'yes'` → migration 41 removed.
5. Optional: restore the legacy alerts with `INFLECTION_ALERTS_ENABLED=1` (only after step 1, never both on).

## 4. Not in this deployment
- Dev and public channels (gates 5 and 6: `post_delivery` claim, `PROD_SENDING_ENABLED`, two clean weeks, explicit approval).
- Public `/signals` pages and `/api/public/signals` (backend and frontend deploys; the API answers 503 until migration 41 exists).
- The correction job (`Ledger.revise`, `FU_CORRECTIONS_ENABLED`).
- News in updates (off until Alpaca redistribution rights are verified).
- Stale-event cleanup of the old alerts (its own owner-approved step).
