# Forward research activation — the exact runbook (NOT EXECUTED)

Status: prepared on `lab/first-light-algo` for the owner's review. **Nothing in this document has been run.** Every command is a proposal for one explicit approval.
Written for the person who will execute it after the owner approves; it states what changes, how to check it, and how to undo it.

**The activation candidate** is one commit, named in the owner review that accompanies this file (`CAND`, 40 hex). Production runs the image whose tag is its first 12 hex (`TAG`),
built by the existing manual CD workflow from exactly that commit. The commit enables nothing: every feature is behind a flag, a boundary row, an admin-installed unit or an arming file.

## 0. Variables and approvals

```bash
CAND=<the 40-hex activation candidate>          TAG=${CAND:0:12}
KEY=~/.ssh/donchian_deploy                      VPS=116.203.220.219
D="ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes deploy@$VPS"     # the deploy user (docker, no sudo)
R="ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes root@$VPS"       # root: only for /opt/donchian/CURRENT_MECHANISM_SHA, /opt/donchian/scripts, /etc/systemd/system
IMG=ghcr.io/danielbugi/wolfx_alpha-mechanism:$TAG        PG=donchian-screener-postgres-1        ENVF=/opt/donchian/env/.env
```

Owner-controlled gates (the preflight reports each as NO until the owner states `yes`; this runbook never sets one): `s11_passed`, `model_version_fix_applied` (applied = shipped in the pinned image,
step 10), `owner_activation_approved`, `backup_verified` (steps 3-4), `authoritative_vendor_verified_live` (step 6b), `mechanism_image_pinned` (step 10). Plus the automatic `scheduler_units_committed` (true in the candidate).

**Deliberate differences from the owner's outline, and why.** (1) The image is built and pulled *before* the migrations (steps 5-6): the migration SQL is then read out of the very image that will run
(so it is byte-identical to the approved commit, no staged copy), and the read-only preflight can run from the image before anything is pinned. Building an image changes no production state. (2) A
**pin-only observation cycle** (step 11) sits between pinning and enabling anything: one full pipeline session on the new image with every flag still off, so the image alone is proven behaviour-neutral
before any evidence is written. (3) Step 13 includes creating the research-admin person's login (S9b), because the group has no members today and capture's boundary row can only be written by one.

| Owner outline | Here | | Owner outline | Here |
|---|---|---|---|---|
| 1 verify S11 PASS | 1 | | 11 deploy/restart only the required | 11 (none needed) |
| 2 verify candidate SHA + CI | 2 | | 12 enable sector-history recorder | **12 (first append-only write)** |
| 3-4 backup, verify | 3-4 | | 13 candidate capture + boundary | 13 |
| 5-6 migrations, schema | 7-8 | | 14 collector units | 14 |
| 7-8 roles, least privilege | 9 | | 15-20 verification and observation | 15-20 |
| 9-10 build/publish image, pin | 5-6 and 10 | | | |

## 1. What changes in production, and what does not

| Surface | Change | When |
|---|---|---|
| Disk | a labelled dump (~640 MB) under `/opt/donchian/backups/pre-release-b/`; the new image layers | steps 3, 6 |
| Database schema | 16 new tables, 20 functions, 47 `ENABLE ALWAYS` triggers (migrations 24-31); ownership/grants for them by the roles script | steps 7, 9 |
| Mechanism pin | `/opt/donchian/CURRENT_MECHANISM_SHA`: `cfd83f72f960` → `TAG`. Read by the pipeline, retry, channel-sender and evaluator wrappers at each run | step 10 |
| Behaviour at the pin, flags off | new ledger rows carry `model_version = NULL` instead of `'unknown'`; `BF.B`, `BRK/A` gain fundamentals rows and `BRK.B` a sector (vendor-symbol translation); nothing else | step 11 observes it |
| Env file | `SECTOR_HISTORY_RECORDER_ENABLED=1`, later `RESEARCH_CAPTURE_ENABLED=1` | steps 12, 13 |
| Database rows | the first sector polls/observations; the capture boundary row; then capture rows and market/sector/RS snapshots | steps 12, 13, 16 |
| systemd | 3 unit files + the wrapper installed, the timer enabled, an arming file created | step 14 |
| **Unchanged** | the backend (`963ab19318b7`), the bot (`e3feb64d3e86`, its own pin file), Postgres, Caddy, the frontend, `GUARDS_EFFECTIVE_FROM`, `PROD_SENDING_ENABLED`, every existing timer, DB credentials and roles | never touched |

The first **irreversible / append-only evidence write** is marked **[FIRST APPEND-ONLY WRITE]** (step 12). Everything before it is additive and undoable; steps 7-9 are undoable only while the new tables are empty.

## 2. The sequence

Each step: **Action**, **Expected**, **Verify**, **Rollback**, **Reversible**. `RO` = read-only.

### 1. Verify S11 PASS (RO)
**Action:** confirm the owner accepted `docs/research/evidence/s11_final_2026-10-07/S11_REPORT.md`, then `$D 'cd /home/deploy/release-b-s11 && bash s11_ro.sh snap' </dev/null`.
**Expected:** `CAPTURE|run/obs/snap/registry/activation|0/0/0/0/0`, `SCHEMA|migration24_25_tables|none`, `PIN|… CURRENT_MECHANISM_SHA=cfd83f72f960`, all `CONN|… user=donchian_app`.
**Verify:** the lines above. **Rollback:** none. **Reversible:** n/a. Gate: `s11_passed`.

### 2. Verify the clean candidate SHA and CI (RO)
**Action (operator checkout):**
```bash
git fetch origin && test "$(git rev-parse origin/lab/first-light-algo)" = "$CAND" && echo "branch tip is the candidate"
gh run list --branch lab/first-light-algo --limit 10 --json databaseId,headSha,status,conclusion -q ".[]|select(.headSha==\"$CAND\")"
git diff --quiet "$CAND" -- docs/operations deploy mechanism && echo "no drift"        # in a clean checkout of CAND
while read -r h f; do [ "$(git show "$CAND:$f" | sha256sum | cut -c1-64)" = "$h" ] || echo "MISMATCH $f"; done < <(sed -n '/^```manifest/,/^```$/p' docs/operations/FORWARD_RESEARCH_ACTIVATION_RUNBOOK.md | grep -E '^[0-9a-f]{64}  ')
```
**Expected:** the tip equals `CAND`; a completed, successful CI run for exactly `CAND` (all 6 jobs); no `MISMATCH`.
**Rollback:** none. **Reversible:** n/a.

### 3. Production backup / dump (writes files only)
**Action:** `$D "bash -s -- forward-research" < deploy/db/pre_activation_backup.sh`
**Expected:** `OK label=forward-research dir=/opt/donchian/backups/pre-release-b/forward-research-<UTC> tables=<N> bytes=… sha256=…` and four `COUNT|…` lines (ledger ≥ 1247, delivery ≥ 36, `daily_fundamentals`, `candidate_observation|0`).
**Verify:** step 4. **Rollback:** `$D 'rm -r /opt/donchian/backups/pre-release-b/forward-research-<UTC>'`. **Reversible:** yes (it only adds files; disk was 21% used). `globals.sql` is secret: never copied off the box.

### 4. Verify the backup (RO, plus an optional scratch restore)
**Action:** `$D 'BK=$(ls -dt /opt/donchian/backups/pre-release-b/forward-research-* | head -1); cd $BK && sha256sum -c production.dump.sha256 && wc -l restore_list.txt rowcounts.txt && grep -c " TABLE DATA " restore_list.txt && stat -c "%a %n" . *'`
**Expected:** `OK`; the TABLE DATA count equals the rowcounts line count; modes 700 for the directory and 600 for the files.
**Optional full restore rehearsal (recommended; the procedure proven before S10), all on a private internal Docker network, nothing touching production data:**
```bash
$D 'set -e; BK=$(ls -dt /opt/donchian/backups/pre-release-b/forward-research-* | head -1); W=$HOME/fr-rehearsal; mkdir -p $W; chmod 700 $W
python3 -c "import secrets,sys;sys.stdout.write(secrets.token_urlsafe(24))" > $W/pw; chmod 644 $W/pw
docker network create --internal --label fr-rehearsal=1 fr-net >/dev/null; docker volume create --label fr-rehearsal=1 fr-data >/dev/null
docker run -d --name fr-pg --pull never --network fr-net --label fr-rehearsal=1 --memory 3g -e POSTGRES_USER=trading_user -e POSTGRES_DB=trading_production \
  -e POSTGRES_PASSWORD_FILE=/run/fr/pw -v $W/pw:/run/fr/pw:ro -v $BK/production.dump:/b/production.dump:ro -v fr-data:/var/lib/postgresql/data postgres:16-alpine \
  postgres -c fsync=off -c synchronous_commit=off >/dev/null
for i in $(seq 1 60); do docker exec fr-pg pg_isready -U trading_user -d trading_production >/dev/null 2>&1 && break; sleep 2; done
docker exec fr-pg pg_restore -U trading_user -d trading_production -j 2 --exit-on-error /b/production.dump && echo RESTORE_OK
while IFS="|" read -r t c; do a=$(docker exec fr-pg psql -U trading_user -d trading_production -X -At -c "select count(*) from public.\"$t\""); [ "$a" = "$c" ] || echo "MISMATCH $t $c $a"; done < $BK/rowcounts.txt; echo COMPARED' </dev/null
$D 'docker rm -f fr-pg; docker volume rm fr-data; docker network rm fr-net; rm -rf $HOME/fr-rehearsal' </dev/null          # always, then confirm: docker ps -a | grep fr-
```
**Expected:** `RESTORE_OK` and no `MISMATCH`. **Rollback:** the removal line above. **Reversible:** yes. Gate: `backup_verified`.

### 5. Build and publish the image from the exact commit (external artifact, not production)
**Action:**
```bash
git tag -a "activation-candidate-$TAG" "$CAND" -m "forward research activation candidate" && git push origin "activation-candidate-$TAG"
gh workflow run cd.yml --ref "activation-candidate-$TAG" -f service=mechanism -f confirm_ci_passed=yes
gh run list --workflow cd.yml --limit 1 --json databaseId,status -q '.[0].databaseId'   # then: gh run watch <id> --exit-status
```
**Expected:** `verify-ci-precondition` and `build-and-push` succeed; the image `ghcr.io/danielbugi/wolfx_alpha-mechanism:$TAG` exists; the `deploy` job only prints that mechanism is not live-deployed. (If the `production` Environment
asks for a reviewer, that is the existing gate.) **Verify:** step 6 pulls it. **Rollback:** none needed (an unpinned image is inert); delete the tag if abandoned. **Reversible:** yes. (Merging to `main` is a separate owner decision and
not required: recommended as `git merge --ff-only` so `main` equals `CAND`, never a squash, which would change the SHA.)

### 6. Pull the image on the VPS; verify it; first read-only preflight
**6a Action:**
```bash
$D "docker pull $IMG && docker image inspect $IMG --format '{{.Id}} {{.Created}}'"
$D "docker run --rm --entrypoint sh $IMG -c 'cd /app && sha256sum -c -'" < <(sed -n '/^```manifest/,/^```$/p' docs/operations/FORWARD_RESEARCH_ACTIVATION_RUNBOOK.md | grep -E '^[0-9a-f]{64}  mechanism/')
$D "docker run --rm -e PYTHONPATH=/app/mechanism --entrypoint python $IMG -m forward_collection --help" | head -3
```
**Expected:** the pull succeeds; every `mechanism/*.sql` line prints `OK` (the image holds exactly the approved migrations); the CLI help prints.
**6b Action (read-only preflight, expected NO for environment and activation at this point):**
```bash
$D "docker run --rm -i --network donchian-screener_app_net --env-file $ENVF -e DB_HOST=postgres -e PGOPTIONS='-c default_transaction_read_only=on' -e PYTHONPATH=/app/mechanism --entrypoint python $IMG \
    -m forward_collection activation --spec /dev/stdin --runtime-role donchian_app" < docs/operations/forward_research_preflight_spec.json
```
**Expected:** `architecture_ready: YES`; `environment_ready: NO` (migrations 24-31 missing, grants missing); `activation_ready: NO`; exit 2. The same command repeated right before step 12 is the "authoritative vendor verified live" evidence:
`$D "docker run --rm -e PYTHONPATH=/app/mechanism --entrypoint python $IMG -m data_updaters.sector_history_recorder --live-yfinance AAPL,SPY,VFIAX,BRK.B,BF.B,BRK/A,ZZZZXQ"` must show sector / `no_sector` (ETF, MUTUALFUND) / sector for the three share classes / `invalid_response`.
**Rollback:** `$D "docker rmi $IMG"`. **Reversible:** yes. Gate: `authoritative_vendor_verified_live`.

### 7. Apply migrations 24-31, in this order (the first schema change)
**Action (one file per command, stop at the first error; each is atomic with `-1`; run as the bootstrap identity from the image's own copy):**
```bash
for f in add_market_snapshot_tables add_market_event_tables add_forward_return_label_table add_source_observation_tables \
         add_catalyst_classification_table add_stock_relative_strength_table add_dataset_experiment_registry_tables add_sector_history_tables; do
  echo "== $f"; $D "docker run --rm --entrypoint cat $IMG /app/mechanism/$f.sql | docker exec -i $PG psql -U trading_user -d trading_production -X -v ON_ERROR_STOP=1 -1" </dev/null || break
done
```
**Expected:** each prints `CREATE TABLE`/`CREATE FUNCTION`/`CREATE TRIGGER`… and no `ERROR`. (24 and 25 are the Market Intelligence migrations; they need the owner's explicit approval as part of this step. 28 references 25, 26 references migration 22.)
**Rollback:** `$D "docker exec -i -e PGOPTIONS='-c search_path=public' $PG psql -U trading_user -d trading_production -X -v ON_ERROR_STOP=1 -1 -f -" < deploy/db/rollback_24_31.sql` (refuses, without an approval setting, if any table holds a row). **Reversible:** yes while the tables are empty; after evidence exists only the quarantine route (section 3).

### 8. Verify the schema (RO)
**Action:**
```bash
$D "docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' $PG psql -U trading_user -d trading_production -X -At -F'|'" <<'SQL'
select 'tables_expect_16', count(*) from pg_class where relnamespace='public'::regnamespace and relkind='r' and relname in ('market_snapshot','sector_snapshot','universe_snapshot','market_event','market_event_revision','forward_return_label','source_observation','source_poll','catalyst_classification','stock_relative_strength','dataset_manifest','experiment_registration','experiment_result','sector_observation','sector_poll','sector_reconstruction');
select 'triggers_always_expect_47', count(*) filter (where t.tgenabled='A'), count(*) from pg_trigger t join pg_class c on c.oid=t.tgrelid where not t.tgisinternal and c.relnamespace='public'::regnamespace and c.relname in ('market_snapshot','sector_snapshot','universe_snapshot','market_event','market_event_revision','forward_return_label','source_observation','source_poll','catalyst_classification','stock_relative_strength','dataset_manifest','experiment_registration','experiment_result','sector_observation','sector_poll','sector_reconstruction');
select 'functions_expect_20', count(*) from pg_proc where pronamespace='public'::regnamespace and proname ~ '^research_(market|label|observation|poll|classification|rs|registry|sector)';
select 'ledger', count(*) from signal_ledger;
select 'sector_rows_expect_0', (select count(*) from sector_poll)+(select count(*) from sector_observation)+(select count(*) from market_snapshot)+(select count(*) from stock_relative_strength);
SQL
```
**Expected:** `16`, `47|47`, `20`, the ledger count equal to the backup's `rowcounts.txt`, `0`. Then repeat 6b: `migrations_22_and_24_to_31_applied` and `research_tables_guarded_by_enable_always_triggers` now OK.
**Rollback:** as step 7. **Reversible:** n/a (read-only).

### 9. Re-run the roles/grants and verify least privilege (changes ownership/grants of the new tables only)
**Action:**
```bash
$D "docker exec -i -e PGOPTIONS='-c search_path=public' $PG psql -U trading_user -d trading_production -X -v ON_ERROR_STOP=1 -1 -f -" < deploy/db/research_roles.sql
$D "docker exec -i -e PGOPTIONS='-c search_path=public' $PG psql -U trading_user -d trading_production -X -v ON_ERROR_STOP=1 -f -" < deploy/db/research_roles_verify.sql
```
**Expected:** both succeed; the verify script prints only `OK` lines (it also proves behaviour with `SET LOCAL ROLE donchian_app` in a rolled-back transaction). The script is idempotent and does not touch any role's credentials, so `donchian_app` keeps working throughout.
**Verify:** the 6b preflight now reports `environment_ready: YES` (`runtime_role_is_least_privilege_on_research_tables`: SELECT+INSERT only on the 24-31 tables; no UPDATE/DELETE/TRUNCATE) and `activation_ready: NO`.
**Rollback:** the step 7 rollback (the grants vanish with the tables). **Do NOT run `research_roles_rollback.sql`**: it would also undo S9/S10 (the runtime identity). **Reversible:** only while the tables are empty.

### 10. Deliberately pin the image
**Action (run in a quiet window, not 20:30Z-00:00Z; the pipeline reads the pin at every run):**
```bash
$R "set -e; cd /opt/donchian; cp CURRENT_MECHANISM_SHA PREVIOUS_MECHANISM_SHA.forward-research-\$(date -u +%Y%m%dT%H%M%SZ); printf '%s\n' $TAG > CURRENT_MECHANISM_SHA.new; mv CURRENT_MECHANISM_SHA.new CURRENT_MECHANISM_SHA; stat -c '%U %a' CURRENT_MECHANISM_SHA; cat CURRENT_MECHANISM_SHA"
```
**Expected:** `root 644` and `TAG`. The bot's separate pin (`/opt/donchian/env/mechanism_image_tag.env`, `e3feb64d3e86`) is deliberately **not** changed. Gates: `mechanism_image_pinned`, `model_version_fix_applied`.
**Verify:** `$D 'cd /home/deploy/release-b-s11 && bash s11_ro.sh snap' </dev/null | grep '^PIN'` shows `CURRENT_MECHANISM_SHA=$TAG`.
**Rollback:** `$R 'cp /opt/donchian/PREVIOUS_MECHANISM_SHA.forward-research-<UTC> /opt/donchian/CURRENT_MECHANISM_SHA'` (or write `cfd83f72f960`). **Reversible:** yes; the next one-shot run uses the old image again.

### 11. Restart / deploy: nothing; then observe one pipeline cycle with every flag still off
**Action:** none to run. No restart is needed or performed: the pipeline, retry, channel-sender and evaluator are one-shot `docker compose run --rm` containers that read the pin per run; the backend and bot are not touched.
**Observe** the next scheduled pipeline success on the new pin (20:45Z attempt fails the freshness gate by design; the 22:00Z attempt completes, ~23:20Z), then, read-only:
```bash
S=<that session>
$D 'cd /home/deploy/release-b-s11 && for m in "recon '$S'" capture backup; do bash s11_ro.sh $m; done' </dev/null
$D "docker run --rm --network donchian-screener_app_net --env-file $ENVF -e DB_HOST=postgres -e PGOPTIONS='-c default_transaction_read_only=on' -e PYTHONPATH=/app --entrypoint python $IMG mechanism/validate_release_b.py connections --forbid-user trading_user"
$D "docker run --rm --network donchian-screener_app_net --env-file $ENVF -e DB_HOST=postgres -e PGOPTIONS='-c default_transaction_read_only=on' -e PYTHONPATH=/app --entrypoint python $IMG mechanism/validate_release_b.py delivery --session $S"
```
**Expected:** `RECON|model_version|<NULL>=<all rows>` (no `unknown`); `RECON|duplicate_…|0`; `CAPTURE|…|0/0/0/0/0`; `connections` and `delivery` PASS; `sector_poll`/`sector_observation` still 0 rows; the baseline ledger immutable fingerprint is still `7db4e45e68952746686e7f986f3b73e9` for `signal_date <= 2026-10-02`; the pipeline journal shows `BF.B`/`BRK/A` fetched without the old "Failed to parse json response" error.
**Rollback:** step 10's rollback. **Reversible:** yes. Anything unexpected here stops the activation.

### 12. Enable the sector-history recorder — **[FIRST APPEND-ONLY WRITE]**
**Action (before the 22:00Z run; read per run):**
```bash
$D 'set -e; E=/opt/donchian/env/.env; cp -p $E $E.bak.pre-recorder-$(date -u +%Y%m%dT%H%M%SZ)
grep -q "^SECTOR_HISTORY_RECORDER_ENABLED=" $E && { echo "already present"; exit 1; }
[ -z "$(tail -c1 $E)" ] || echo >> $E; echo SECTOR_HISTORY_RECORDER_ENABLED=1 >> $E; grep -c "^SECTOR_HISTORY_RECORDER_ENABLED=1$" $E'
```
**Expected:** `1`. Nothing is written now. **The first immutable evidence row is written by the next nightly fundamentals step (about 22:13Z): the first `sector_poll` and `sector_observation` insert, provenance `observed_forward`.** Those rows can never be updated or deleted (triggers, plus no UPDATE/DELETE grant) except through a two-person maintenance ticket.
**Verify (after that night):**
```sql
select source, response_state, count(*) from sector_poll group by 1,2 order by 1,2;      -- yfinance_info: sector ~3044, no_sector ~1 (PINC), invalid_response ~21; tiingo_meta (Dow-30 only): diagnostic
select count(*) from sector_observation where change_kind='first';                         -- ~3045, one per symbol with a sector or inferred no-sector
select count(*) from sector_poll where failure_reason='unsupported_symbol_format';          -- 0
```
and `$D "docker run --rm --network donchian-screener_app_net --env-file $ENVF -e DB_HOST=postgres -e PGOPTIONS='-c default_transaction_read_only=on' -e PYTHONPATH=/app/mechanism --entrypoint python $IMG -m forward_collection verify-sector-history --latest-completed"` (read-only; exit 0, accounted share ≥ 0.99), the ledger/delivery fingerprints unchanged, and no `sector history NOT recorded` line in the pipeline journal.
**Rollback / disable:** `$D "sed -i '/^SECTOR_HISTORY_RECORDER_ENABLED=/d' $ENVF"` (future polls stop). **Reversible:** the flag, yes; **rows already written, no** (immutable; quarantine only, section 3).

### 13. Candidate capture: admin login, boundary row, flag
**13a (S9b) Action, owner supplies `<person>`:** `$D "docker exec -it $PG sh -c 'psql -U \"\$POSTGRES_USER\" -d trading_production'"`, then `CREATE ROLE <person> LOGIN IN ROLE donchian_research_admin;` and `\password <person>` (typed by the owner; never logged). **Verify:** `roles.admin_group_members` lists `<person>`. **Rollback:** `DROP ROLE <person>;`. **Reversible:** yes.
**13b Action:** choose `E` = an explicit trading date strictly after the session whose pipeline run first saw the recorder, never before `GUARDS_EFFECTIVE_FROM` (2026-10-02). Look up the strategy id (RO): `select id from strategies where strategy_key='donchian_breakout' and strategy_version='v1';`. As `<person>`:
`$D "docker exec -it $PG psql -U <person> -d trading_production -c \"select research_capture_set_state(<id>, 'enabled', DATE '<E>', 'forward research activation $TAG')\""`.
**Expected:** one `enabled` row in `research_capture_activation` (append-only). **Verify:** `select * from research_capture_activation order by id;` **Rollback:** a later `research_capture_set_state(<id>, 'disabled', <next session>, '<note>')` (appends; history stays). **Reversible:** by appending a disable, not by deleting.
**13c Action:** append `RESEARCH_CAPTURE_ENABLED=1` to `$ENVF` exactly like step 12 (back up first). The three keys (image, flag, boundary) are now all present; capture begins at session `E`. **Rollback:** delete the flag line and/or append a `disabled` row (either key alone stops it).

### 14. Install the collector units, validate, then arm and enable
**14a Action (installs files; enables nothing):**
```bash
$R 'install -m 755 -o root -g root /dev/stdin /opt/donchian/scripts/run_forward_collection.sh' < deploy/vps/run_forward_collection.sh
for u in donchian-forward-collection.service donchian-forward-collection.timer donchian-forward-collection-alert.service; do
  $R "install -m 644 -o root -g root /dev/stdin /etc/systemd/system/$u" < deploy/vps/$u; done
$R 'systemd-analyze verify /etc/systemd/system/donchian-forward-collection.service /etc/systemd/system/donchian-forward-collection.timer && systemctl daemon-reload'
```
**Expected:** no output from `verify`. **Verify:** `$R 'systemctl is-enabled donchian-forward-collection.timer; systemctl list-timers --all | grep -c forward'` → `disabled` and `0`. **Rollback:** `$R 'rm -f /etc/systemd/system/donchian-forward-collection* /opt/donchian/scripts/run_forward_collection.sh && systemctl daemon-reload'`.
**14b Action (read-only dry run through the production wrapper path, identity `donchian_app`):**
`$D "cd /opt/donchian/compose && IMAGE_TAG=$TAG docker compose --project-directory /opt/donchian/compose -f docker-compose.yml -f docker-compose.prod.yml --env-file $ENVF run --rm -e PYTHONPATH=/app/mechanism channel-sender -m forward_collection run --latest-completed --with-sector-history-check" </dev/null`
**Expected:** verdict `DRY_RUN`, exit 0, no failed step (without `--apply` nothing is written). Also confirm the refusal: `$R /opt/donchian/scripts/run_forward_collection.sh; echo $?` → `5` ("not armed").
**14c Action (arm and enable):** `$R "printf '%s' $TAG > /opt/donchian/FORWARD_COLLECTION_ARMED && systemctl enable --now donchian-forward-collection.timer"`
**Expected:** a `timers.target.wants` symlink is created; the next fire is 03:15 or 08:15 Asia/Jerusalem. **Rollback / disable:** `$R 'systemctl disable --now donchian-forward-collection.timer; rm -f /opt/donchian/FORWARD_COLLECTION_ARMED'`. **Reversible:** yes (evidence already written is not).

### 15. Verify service and timer state (RO)
`$R 'systemctl list-timers --all --no-pager | grep -E "forward|pipeline"; systemctl is-enabled donchian-forward-collection.timer; systemctl --failed --no-pager'` → the forward timer `enabled` with a next elapse; no failed unit; every existing timer unchanged (compare with the step 1 snapshot).
**Rollback:** none (read-only observation; it changes nothing). **Reversible:** n/a. Any unexpected result stops the activation: apply section 3-A.

### 16. Observe the first real forward session
The first forward-observed session is the first session `S ≥ E` whose pipeline run happened with the recorder on, capture armed, then collected by the timer's next fire (03:15 Asia/Jerusalem the day after). Do not run anything by hand. After the 22:00Z pipeline success:
`$D 'cd /home/deploy/release-b-s11 && bash s11_ro.sh snap && bash s11_ro.sh recon <S>' </dev/null` (ledger rows written with lineage ids for the session; `observation_id_not_null` equals the ledger rows). After the 03:15 fire:
`$D 'tail -n 6 /opt/donchian/logs/forward_collection_runs.log'` → `forward-collection finished: exit 0`.
**Rollback:** none (read-only observation; it changes nothing). **Reversible:** n/a. Any unexpected result stops the activation: apply section 3-A.

### 17. Verify candidate / market / sector / RS / history / labels (RO)
```sql
select session_date, status from candidate_capture_run order by id desc limit 3;                      -- S: complete
select count(*) from candidate_observation where session_date = DATE '<S>';                              -- = candidates that entered the guards (the M in the pipeline log)
select count(*) from signal_ledger where signal_date = DATE '<S>' and observation_id is not null;         -- = the ledger rows written for S
select count(*) from market_snapshot where session_date = DATE '<S>' and provenance='observed_forward';   -- 1 per feature set
select count(*) from sector_snapshot where session_date = DATE '<S>';  select count(*) from stock_relative_strength where session_date = DATE '<S>';
select count(*) from sector_observation; select count(*) from sector_poll where (attempted_at at time zone 'UTC')::date >= DATE '<S>';
select count(*) from forward_return_label;                                                                -- 0 until a horizon (5/20/60 sessions) matures: expected, not a failure
```
**Rollback:** none (read-only observation; it changes nothing). **Reversible:** n/a. Any unexpected result stops the activation: apply section 3-A.

### 18. Run the research status / preflight (RO)
`$D "docker run --rm -i --network donchian-screener_app_net --env-file $ENVF -e DB_HOST=postgres -e PGOPTIONS='-c default_transaction_read_only=on' -e PYTHONPATH=/app/mechanism --entrypoint python $IMG -m forward_collection activation --spec /dev/stdin --runtime-role donchian_app <the owner's --gate NAME=yes statements>" < docs/operations/forward_research_preflight_spec.json`
→ architecture, environment and activation `YES`. The `--gate` flags are the owner's own statements; this runbook never supplies one for them.
**Rollback:** none (read-only observation; it changes nothing). **Reversible:** n/a. Any unexpected result stops the activation: apply section 3-A.

### 19. Verify the session COMPLETE (RO)
`$D 'grep -o "\"verdict\": *\"[A-Z_]*\"" /opt/donchian/logs/forward_collection_runs.log | tail -3'` → `COMPLETE`; `verify-sector-history --latest-completed` exit 0; the delivery validator PASS for `S`; the baseline fingerprints unchanged.
**Rollback:** none (read-only observation; it changes nothing). **Reversible:** n/a. Any unexpected result stops the activation: apply section 3-A.

### 20. Monitor the next scheduled cycle
Expect, on the following days: the 20:45Z pipeline attempt fails the freshness gate and 22:00Z succeeds; the collector's second fire (08:15) finds the session already complete (exit 0 or 3, never an alert); weekend fires find no new session and exit 0.
Watch items: an alert on a **missed fundamentals run** (two in a row break the 4-day window; 09-29 and 09-30 were missed once); **Israel DST ends 2026-10-25** (fundamentals then straddles 00:00Z: same-day PIT eligibility is lost for the last symbols, convergence is unaffected); `PINC`-style ticker reuse (a standing source of `sector_no_sector_conflict`); the arming file is **disarmed by any re-pin** (re-create it deliberately); disk growth (about 640 MB per nightly dump, 14-day retention).
**Rollback:** none (read-only observation; it changes nothing). **Reversible:** n/a. Any unexpected result stops the activation: apply section 3-A.


## 3. Rollback

**A. Stop future collection (always reversible, touches no data):**
```bash
$R 'systemctl disable --now donchian-forward-collection.timer; rm -f /opt/donchian/FORWARD_COLLECTION_ARMED'
$D "sed -i '/^SECTOR_HISTORY_RECORDER_ENABLED=/d;/^RESEARCH_CAPTURE_ENABLED=/d' $ENVF"
# as <person>: select research_capture_set_state(<id>, 'disabled', DATE '<next session>', 'stop');   -- appends; history stays
$R 'cp /opt/donchian/PREVIOUS_MECHANISM_SHA.forward-research-<UTC> /opt/donchian/CURRENT_MECHANISM_SHA'      # only if the image itself is the problem
```
Any ONE of the three keys, the timer, the arming file, or the pin stops its part. The backend, bot, Telegram and every pre-existing job are unaffected.

**B. Undo the schema (only while the 24-31 tables are empty):** step 7's `rollback_24_31.sql` (it refuses, without an explicit written approval setting, when any table holds a row). A data-loss approval is the owner's alone and requires the step 3 backup.

**C. Undo collected research history: normally do NOT delete.** Observations, polls, snapshots and capture rows are immutable append-only evidence. If some are wrong: record the affected sessions/symbols as invalidated through the existing
provenance architecture (a new provenance row under a two-person maintenance ticket: `research_maintenance_open` → `approve` by a different admin → `begin` → `close`), so the dataset assembler and readiness checks exclude them while the raw rows
stay as evidence; physical deletion is a research-admin maintenance action of last resort (approved, begun ticket) and is never part of an automatic rollback.

**What is NOT a rollback:** `research_roles_rollback.sql` (it would undo S9/S10, the runtime role switch), `rollback22.sql` (migration 22 and capture's tables), and re-running the backup.

## 4. The first action to approve

**Step 3**, the labelled production backup: `$D "bash -s -- forward-research" < deploy/db/pre_activation_backup.sh`. It is the first command that changes anything (it only adds files). Steps 1-2 are read-only checks. The **first append-only evidence write** is the effect of step 12.

## 5. Manifest: the exact bytes this runbook refers to

`sha256` of each file's content as stored in git (LF), at the activation candidate. Step 2 checks the checkout and step 6a checks the image. `test_activation_artifacts.py` fails CI if any line drifts from the file.

```manifest
d61a7a33111d383b799fb1e07a1d447717708835c86ba6f9b76bbd777db72f5d  mechanism/add_market_snapshot_tables.sql
c545bdbf64b6ffa6490a4453fced99c5ec7131977908db01dc265152adfe2d48  mechanism/add_market_event_tables.sql
d1291331e76c12f7d82c0a66a75fefde9ff859e5251f11bfd87d7edca6735cb9  mechanism/add_forward_return_label_table.sql
2f4152a933e5bf3ce5b6fb00dfa5c7c2d1e0cddd2b667bdd849d167834dea7a1  mechanism/add_source_observation_tables.sql
02f2057c067977c03b37bae632e45dc622f369d35c665b6c48930db520e7011f  mechanism/add_catalyst_classification_table.sql
a3da3cd6761e4224a46dea3ba83a52746f246d1dc9b39c25c8dc7bda0f22a6ea  mechanism/add_stock_relative_strength_table.sql
13b48ef0389874872a18f6b5a624100f356db1b0603a08f6e56388df2e2dad69  mechanism/add_dataset_experiment_registry_tables.sql
6f2ac75347d04132c8f47891f27fc393d7b6d0ebcda244cd55570f7f2a0f364b  mechanism/add_sector_history_tables.sql
1524b55e3fb588ba11d07220cc018e2c3a3c0229213ed2506e54ddd67f9fb0c9  deploy/db/research_roles.sql
ea141858ef105b20800adc665dd3bcc1fa3bcfcf602add78472692061c7c66e9  deploy/db/research_roles_verify.sql
713202f3a3e7354a6bde42be90e4a4dc100df23981e3f11e4af1643855217796  deploy/db/research_roles_rollback.sql
0a1f3a27d525051d776e93dc4f700e2ef8e51164f0b53e9a59310c9aee1f72d5  deploy/db/rollback_24_31.sql
e147ae29a396264330cd5b1ccc5e4e8d664b8cdd5b9a8028c15121e9b9ce00d1  deploy/db/pre_activation_backup.sh
903d808fd377887599d0db28159fe133bd5bb2f5d906673c59ed809e1213cd35  deploy/vps/run_forward_collection.sh
d024b112623c9093fa41775ba707a2d9ddb5eb7c4184f8591a98dfea21fb50df  deploy/vps/donchian-forward-collection.service
c4f7415eb05f97ba002dec05e85660e0b94fb7114b466826ac88dfd413f2e368  deploy/vps/donchian-forward-collection.timer
39f2c9baeedda1eb7ca3a72aefc1f1b1bcf47cae04f07d832701983836667185  deploy/vps/donchian-forward-collection-alert.service
f579ff201446158552d175bdab73c94bf81a3943f3ab4146947d08ed22ce1f10  docs/operations/forward_research_preflight_spec.json
```

## 6. Known limitations

* The runbook is rehearsed in pieces (the roles script and the S9/S10 backup/restore procedure were run before; the rollback and the dormant wrapper are tested on throwaway databases and scratch directories) but this exact sequence has never run against production.
* The pipeline timer fires at 23:45 and 01:00 Asia/Jerusalem; step 10 and step 12 should be done well before 20:30Z.
* Step 13 needs the owner to choose `<person>` and `E`; step 14c needs the owner's go to enable the timer.
* The research-admin person's password and every database password are typed or generated out of band and never appear in this repository.
* `forward_research_preflight_spec.json` is a *preflight-only* spec: its windows and cutoff are inert placeholders from the test world; it only declares which sources are enabled and which versions the collector writes. A real research dataset spec is authored later with `dataset_cli author`.
