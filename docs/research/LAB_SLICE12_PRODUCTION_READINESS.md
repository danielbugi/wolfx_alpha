# Lab Slice 12 — Production readiness verification (NO ACTIVATION)

Branch `lab/first-light-algo`, draft PR #1 (CI only, DO NOT MERGE). Four owner-approved **read-only** production checks, one lab fix (the dotted-symbol
defect, at the vendor boundary), and the evidence-updated activation runbook and gate. Nothing was applied, enabled, installed, restarted, deployed or
merged; no production data or configuration was modified; S11 was not touched; no Tiingo call was made; no model was trained.

Evidence classes: **LIVE_PRODUCTION_READ** (read-only queries on the production database / host, 2026-10-06), **LIVE_VENDOR** (yfinance from the production
VPS network), **FROZEN_DEV**, **SYNTHETIC**, **CODE_READ**. Raw outputs: `docs/research/evidence/production_readiness_2026-10-06/` (README there lists target,
time, identity). No secret appears in any of it.

---

## 1. The four production checks

All DB checks: host `ubuntu-8gb-nbg1-2` (116.203.220.219), database `trading_production`, session user `trading_user` (the bootstrap/backup identity; services
run as `donchian_app` since S10), `BEGIN READ ONLY` + `default_transaction_read_only=on`, ended with `ROLLBACK`. The first Check A attempt used a temporary
table and **was refused by the read-only guard** (`cannot execute CREATE TABLE AS in a read-only transaction`); it is kept as `check_a_part1_attempt.*`. The
retry used CTEs only.

### Check A — `daily_fundamentals` freshness (20:13:52Z, 20:14:18Z)
Definitions: *session universe* = symbols with a `stock_prices` row on the latest price session, minus `inactive_symbols`; *updater target set* = the
fundamentals updater's own query (every `stock_prices` symbol minus `inactive_symbols`).

| Measure | Result |
|---|---|
| Latest price session / latest fundamentals date | 2026-10-05 / 2026-10-05 |
| Latest `created_at` / `updated_at` | 2026-10-05 23:05:16 (naive timestamp, see "timestamp reading") |
| Rows on the latest date | 3,073 (3,050 with a sector) |
| Session universe / updater target set / inactive | 3,066 / 3,075 / 39 |
| Session universe with a row **on the latest session** | 3,064 (99.93%); 1 more within 30 d (`BF.B`, last row 2026-09-23); 1 with **no row** (`BRK/A`) |
| Age of the latest row, updater set | 0 d: 3,073 · 11–30 d: 1 · >30 d: **0** · no row: 1 |
| Latest-row sector, session universe | available **3,041** (99.18%) · NULL 24 · no row 1 |
| Sector **evidence** age (latest row with a real sector), session universe | ≤4 d: 3,041 · 5–30 d: 0 · >30 d: **1** (`PINC`, last real sector 2025-08-21) · never had one: 24 |
| Distinct sectors | 11, exactly the yfinance taxonomy |
| Unavailable (25) | 21 non-dotted: preferreds/notes/odd tickers (`AFGB/C/D/E`, `CCZ`, `DDT`, `DTB`, `DTG`, `DTW`, `DUKB`, `EAI`, `ELC`, `EMP`, `ENJ`, `ENO`, `FISV`, `GJS`, `KMPB`, `NIQ`, `RZC`, `SI`) + `PINC` + the 3 dotted/slashed (`BF.B`, `BRK.B`, `BRK/A`) |
| Run dates in the last 21 days | 10-05, 10-02, 10-01, 09-28, 09-26 (a Saturday run for the Friday session), 09-24, 09-23, 09-22, 09-21, 09-20. **No rows dated 09-29 and 09-30** although prices exist for both |
| Largest gap between consecutive run dates | 3 days (09-28 → 10-01), around the S8–S10 activation work (the ledger also lacks 09-29/09-30; accepted, never backfilled) |

*Timestamp reading.* `created_at` is `timestamp without time zone`. Rows for 2026-10-05 were created 22:13–23:05. Read as UTC they fall after both evening timer fires (20:45Z and 22:00Z); read as Asia/Jerusalem local they would
be 19:13–20:05Z, **before** the first fire of that evening (20:45Z), which the pipeline's own ordering rules out. The UTC reading is the only consistent one;
it is an inference, not an observed fact.

**Answer — can the actual process keep sector evidence inside the 30-day window?** Kept apart:
* **Table freshness: yes.** Every session-universe symbol but two has a row dated the latest session; nothing has a latest row older than 12 days.
* **Sector availability: 99.18% available**, 25 unavailable; 22 would remain after the dotted fix (the 3 dotted symbols are the fixable ones, see §2).
  The 21 + `PINC` are not a freshness problem: yfinance supplies no sector for them (Check C).
* **Scheduling/cadence: sufficient for 30 days, thin for the collector's 4-day window.** The nightly pipeline (`donchian-pipeline.timer`, 23:45 and 01:00
  Asia/Jerusalem) runs fundamentals as step 5 for the whole updater set. 30 days needs one refresh in 30; the observed maximum gap is 3 days. The collector's
  verification window (`MAX_REFRESH_GAP_DAYS = 4`) was met, but with one day of slack: **two consecutive missed runs would break it** (that almost happened:
  09-29 and 09-30 produced no fundamentals).
* The history that the 30-day rule applies to does not exist yet (the recorder is off). This shows the *process that will feed it* is capable, nothing more.

*Does it change activation readiness?* It satisfies the "fundamentals refresh is recent" environment check with production evidence; it adds a monitoring
requirement (alert on a missed fundamentals run before it becomes two).

### Check B — production `DATA_PROVIDER` (20:13:04Z, 20:19:15Z)
* `/opt/donchian/env/.env`: exactly one `DATA_PROVIDER=` line, value **`tiingo`** (only that key was read; the file was never printed).
* The running `donchian-screener-bot-1` container: effective `DATA_PROVIDER` = `tiingo`.
* Wiring: `docker-compose.prod.yml` uses `env_file: !override .env` (production services read the real `.env` only); the pipeline is the one-shot
  `/opt/donchian/scripts/run_pipeline.sh`. A pipeline container was not running, so its environment is **inferred from that wiring, not observed**.
* `mechanism_image_tag.env`: `IMAGE_TAG=e3feb64d3e86` (the bot pin) next to `CURRENT_MECHANISM_SHA=cfd83f72f960`: the known dual pin, unchanged.

*Interpretation.* Slice 11 treated the production value as unknown and made the design independent of it; it matches. With `tiingo`, `fetch_company_info`
asks Tiingo first (Power plan: the Dow-30 only) and falls back to yfinance for every other symbol, so the nightly run already makes ~3,000 yfinance
requests; the sector recorder adds none for those, and one extra authoritative probe for Tiingo-served symbols (already implemented in Slice 11).
*Readiness effect:* none negative. Tiingo stays diagnostic-only; no Tiingo call was made.

### Check C — non-mutating yfinance dry run from the VPS network (20:18:27Z – 20:18:40Z)
Run in a throw-away container of the pinned image (`--rm`, no env file, no volume, no database); the two lab files were streamed in over stdin, so nothing
was written to the host. Dry-run output keeps `writes: false`. Request latency 209–1,071 ms (the first call includes warm-up); no timeouts; the only HTTP errors were vendor 404s (the unknown symbol, and the untranslated forms in the contrast below).

| Canonical symbol | Vendor request | Outcome | Notes |
|---|---|---|---|
| `AAPL`, `MSFT`, `JPM` | unchanged | `sector` (Technology / Technology / Financial Services) | admissible |
| `SPY`, `QQQ` | unchanged | `no_sector`, quoteType `ETF` | basis `inferred_from_quote_type` |
| `VFIAX` | unchanged | `no_sector`, quoteType `MUTUALFUND` | same |
| `ZZZZXQ` (unknown) | unchanged | `invalid_response / response_too_sparse` (1-key body) | not admissible |
| **`BRK.B`** | **`BRK-B`** | `sector` Financial Services | translated |
| **`BF.B`** | **`BF-B`** | `sector` Consumer Defensive | translated |
| **`BRK/A`** | **`BRK-A`** | `sector` Financial Services | translated |
| `BRK/B` (inactive) | `BRK-B` | `sector` Financial Services | same vendor symbol as `BRK.B`, separate identity |
| `BRK-B` (already dashed) | unchanged | `sector` Financial Services | not re-translated |
| `DUKB`, `FISV`, `NIQ`, `SI`, `EAI` | unchanged | `invalid_response / operating_company_sector_absent` | EQUITY, no sector key: **not** a no-sector |
| `PINC` | unchanged | `no_sector`, quoteType **ETF** | see "ticker reuse" below |
| `ABC.WS` | none (refused) | `request_failed / unsupported_symbol_format` | no request made |

Contrast, the **untranslated** forms from the same network (`check_c_raw_untranslated.json`): `BRK.B` → 15 keys, quoteType EQUITY, **no sector key**
(an operating company without a sector: exactly the case the policy refuses to call no-sector); `BF.B` → a 1-key body; `BRK/A`, `BRK/B` → request error.
The three dashed forms return 166–187 keys with full sectors.

*Ticker reuse.* `PINC` was an operating company until 2025-08-21 (its last real sector) and now returns an ETF: the ticker was reused. It is in the production
universe today. If forward history records it as `no_sector` while a candidate carries the old sector, that is a live instance of the owner's rule
(`sector_no_sector_conflict` → the sector-relative feature is unavailable, neither source wins). Not investigated further (outside the four checks).

### Check D — dotted-symbol production universe (20:14:56Z)
Every `stock_prices` symbol that is not plain `[A-Z0-9]+` (3,075 in the updater set):

| Symbol | Active | In latest session | Last fundamentals row | Latest sector | Vendor form | Resolves |
|---|---|---|---|---|---|---|
| `BF.B` | yes | yes | 2026-09-23 | none | `BF-B` | **yes** |
| `BRK.B` | yes | yes | 2026-10-05 | none | `BRK-B` | **yes** |
| `BRK/A` | yes | yes | none ever | none | `BRK-A` | **yes** |
| `BRK/B` | no (inactive, last price 2026-09-21) | no | none | none | `BRK-B` | yes (duplicate of `BRK.B`) |

The only characters outside `[A-Z0-9]` in the whole table are `.` (2 symbols) and `/` (2). No dashed symbols, no lower case, no whitespace, no non-ASCII; lengths
1–5. `BRK.B` and `BRK/B` are two internal spellings of one company (`BRK/B` is inactive and not in the updater set).
*Readiness effect:* the defect is bounded and fully covered by the evidenced rule.

---

## 2. Symbol normalization (Part 2)

**Inventory of what exists.** `shared/tiingo_client._normalize_symbol` maps *every* `.` and `/` to `-` for **Tiingo only**. Every yfinance caller passes the
canonical symbol raw: `fundamentals_updater._fetch_yfinance_raw`, the dry-run probe, `earnings_calendar_updater` (61), `quarterly_fundamentals_updater`
(105, the non-Tiingo fallback), `symbol_scraper` (385), `daily_data_updater` (381), `alerts/news_links` (117); `market_index_updater` passes index tickers.
`alerts/channel_content.py:304` already dedupes `BRK.B`/`BRK/B` for display. The bot's `parse_symbols` keeps an inner dot.

**Placement.** At the vendor call, as a stdlib-only module `mechanism/data_updaters/vendor_symbols.py`; not the generic Tiingo replacement, not a change to stored
symbols, not in `shared/` (whose package import pulls the database layer, which would make the non-mutating dry run need a DB).

**Rule (only what the evidence supports; fail closed).**
* plain `[A-Z0-9]+` → unchanged · already `ROOT-X` (single class letter) → unchanged
* `ROOT.X` / `ROOT/X` with `ROOT = [A-Z]{1,5}`, `X` one letter other than **U, W, R** → `ROOT-X` (`BRK.B→BRK-B`, `BF.B→BF-B`, `BRK/A→BRK-A`)
* **refused** (`UnsupportedSymbolFormat`, coded reason, no request): U/W/R classes (units/warrants/rights: vendors spell them differently), a multi-letter
  suffix (`ABC.WS`), a second separator, a digit in a dotted root, lower case, whitespace, a trailing newline, `^`/`=`/`&`, empty/non-string.
A refusal is recorded as a failed poll `request_failed / unsupported_symbol_format` (no information was obtained), never as an answer.
A real bug the tests found while building it: `$` accepted `"BRK.B\n"`; all patterns now use `fullmatch`.

**Canonical identity is preserved** (proved by `test_vendor_symbol_identity_db.py` on real Postgres, through the real writer wiring): the vendor is asked for
`BRK-B`, yet every observation, poll, chain hash, fundamentals upsert and history read is keyed `BRK.B`; no row ever carries the vendor spelling; the hash
cannot be reproduced with it; the stored payload projection contains no symbol; `BRK.B` and `BRK/B` keep two separate chains; a later refresh confirms the
head of the canonical chain; a refused symbol makes no request and leaves a failed poll under its own identity; plain symbols are untouched.

**Dry-run parity.** One translation function, called by both vendor-call sites (`FundamentalsUpdater._fetch_yfinance_raw` and the dry-run probe
`fetch_yfinance_raw`); `dry_run()` reports it (`vendor_symbol`: canonical / request / status / rule, or `refused` + reason). Tests: both paths ask the
vendor for the same symbol on six canonical symbols; both refuse the same five unsupported ones before any request; a static test pins that every
`yf.Ticker(...)` takes `request_symbol` and that no inline dot→dash exists; and the architecture preflight now carries
`sector_vendor_requests_use_the_translation` (it discovers request sites rather than listing them, and fails if the translation is naive, wrong or bypassed).
The VPS evidence file is replayed in a test against today's code.

**Deploy-time side effect (must be known before the image bump).** The same fetch also feeds `daily_fundamentals`. After the fix `BRK.B` gains a sector,
`BF.B` and `BRK/A` gain fundamentals rows (all stored under their canonical symbols). Three symbols' fundamentals change; nothing else does.

**Not changed (reported):** the other yfinance callers listed above still pass dotted symbols raw. They are outside the sector-history path; a follow-up should
route them through the same function. `earnings_calendar_updater` and the non-Tiingo `quarterly_fundamentals_updater` fallback are the likeliest to be wrong for the
four symbols today.

### No-sector terminology
yfinance never asserts "no sector": it omits the key, for ETFs/mutual funds **and** for degraded operating-company answers (`BRK.B` raw above). `no_sector` is
therefore a state this recorder **infers** from an approved `quoteType` (ETF, MUTUALFUND); an EQUITY without a sector is `invalid_response /
operating_company_sector_absent`. The stored enum keeps its name (no migration 32); `no_sector_reason` (`vendor_null` / `vendor_blank` /
`vendor_unknown_label`) only describes what the sector field looked like. Code and docs now say *inferred*: the dry run reports `no_sector_basis:
inferred_from_quote_type`, the selector kind is `K_INFERRED_NO_SECTOR` / `inferred_no_sector`, and a test fails if the recorder says the vendor
"explicitly answered/asserted" a no-sector. Slice 8–11 documents keep their historical wording where a superseded banner points here.

### Source Policy A — unchanged
`yfinance_info` is the sole forward sector-identity authority; Tiingo is diagnostic-only; no fallback identity; schema 31 sufficient; no migration 32.
Pinned by `test_source_policy_a_is_unchanged` and the spec validator.

---

## 3. Readiness (Part 3) — three separate answers

### Architecture readiness — **YES**
The code and contracts support activation: sources all have implemented collector steps, `sector_history` has a real verification step, the scheduler command
verifies it, versions/design are consistent, the no-sector policy is resolved, and the new `sector_vendor_requests_use_the_translation` check passes. It is
lab code: it is not in any production image.

### Environment readiness (production, from read-only evidence) — **NO**
| Requirement | Evidence | Status |
|---|---|---|
| Fundamentals refresh recent, whole universe | Check A | **satisfied** (with the cadence caveat) |
| Provider configuration as assumed | Check B | **satisfied** (`tiingo`, handled) |
| Authoritative vendor reachable and answering as designed from the VPS | Check C | **satisfied** (re-run immediately before activation) |
| Universe symbol forms covered | Check D | **satisfied** (4 of 4 after the fix) |
| Migrations 24–31 applied | 22/23 applied; 24/25 deliberately not; 26–31 are lab-only (project records; **not re-queried**, no check authorised) | **not satisfied** |
| `donchian_app` SELECT+INSERT on the new tables | no default privileges; roles script must be re-run after the migrations | **not satisfied** |
| Image contains the Slice 1–12 code | pinned `cfd83f72f960` predates the lab branch | **not satisfied** |

### Activation readiness (owner-controlled gates) — **NO** (I did not set any gate)
| Gate | Status | Why |
|---|---|---|
| `s11_passed` | **BLOCKED** | S11 is an independent process; its final decision is not known to this slice. Not touched, not rerun |
| `model_version_fix_applied` | **BLOCKED** | the isolated `'unknown'` → NULL stage is not done (165 historical production rows keep `'unknown'`) |
| `owner_activation_approved` | **OWNER APPROVAL REQUIRED** | per step group |
| `backup_verified` | **NOT YET APPLICABLE** | a restore-verified pre-activation dump is runbook step 1 (the nightly backup ran 2026-10-05 23:34Z, observed via the timer list) |
| `authoritative_vendor_verified_live` | **VERIFIED** (evidence: Check C) | the owner still sets the gate; repeat the check right before activation |
| `mechanism_image_pinned` | **BLOCKED** | needs the lab branch merged (owner decision), CI, an image build and a deliberate single pin |
| `scheduler_units_committed` (automatic) | **NOT YET APPLICABLE** / owner approval | units are committed at step 12 together with the deliberate guard amendment |

---

## 4. Activation runbook — evidence-updated (NOT executed; no secrets)
Preconditions: every gate above open. Each step is a production mutation unless marked read-only. Status column = what Slice 12 changes.

| # | Action | Expected | Verification | Rollback | Slice 12 status |
|---|---|---|---|---|---|
| 1 | Pre-activation dump + checksum, restore-test into a scratch DB | file + sha256; counts match S11 baselines (ledger 1022, delivery 28) | scratch restore | delete the scratch DB | NOT YET APPLICABLE |
| 2 | Read-only preflight (`forward_collection activation` + `validate_release_b.py`) | architecture YES; environment lists exactly the NOs fixed below | the report | none | architecture VERIFIED; environment NO |
| 3 | Migrations **24–31** in order, `psql … -v ON_ERROR_STOP=1 -1 -f -`, one at a time (24/25 = Market Intelligence: own explicit approval) | additive, `IF NOT EXISTS` | `to_regclass`, triggers, 0 rows after each | additive: leave empty tables; drop only via the owner-approved rollback scripts | OWNER APPROVAL REQUIRED |
| 4 | Re-run `research_roles.sql` then `research_roles_verify.sql` | `donchian_app` SELECT+INSERT on 24–31 tables only | verify script + preflight role check | `research_roles_rollback.sql` | NOT YET APPLICABLE |
| 5 | Schema verification | table/trigger/function set = derived catalog | preflight migration + guard checks | as 3 | NOT YET APPLICABLE |
| 6 | Mechanism image bump to a build containing Slice 1–12 **and the `model_version` fix**; one consistent pin for pipeline, bot and collector | `docker inspect` of every consumer shows the tag | no dual pin | repin to `cfd83f72f960` | BLOCKED. **Side effect to expect:** `BF.B`/`BRK/A` gain fundamentals rows, `BRK.B` a sector |
| 7 | `SECTOR_HISTORY_RECORDER_ENABLED=1` in the VPS env file (fundamentals updater only) | the next nightly run writes polls/observations: ≈3,044 `sector` first observations, 1 `no_sector` (`PINC`, an ETF now), ≈21 `invalid_response` (accounted; 5 of them probed in Check C); no `unsupported_symbol_format` for any current symbol | chains verify; ledger/delivery fingerprints unchanged | unset the flag (rows stay: immutable) | NOT YET APPLICABLE. **First append-only action** |
| 8 | Vendor verification from the VPS, live dry run (read-only): `docker run --rm --entrypoint sh <pinned image> -c 'cd /app/mechanism && PYTHONPATH=/app/mechanism python -m data_updaters.sector_history_recorder --live-yfinance "AAPL,SPY,VFIAX,BRK.B,BF.B,BRK/A,ZZZZXQ"'` | the Check C tally | the JSON; gate `authoritative_vendor_verified_live` | none | **VERIFIED 2026-10-06**; repeat before activation |
| 9 | Candidate capture, three keys: the image (6), `RESEARCH_CAPTURE_ENABLED=1`, an `enabled` boundary row via `research_capture_set_state` by a research-admin person | capture rows for the next session only | row counts; ledger unchanged | set state `disabled`; unset the flag | BLOCKED (`model_version` fix first) |
| 10 | Market/sector/RS collection | snapshots written by the collector | collector status | disable the timer | NOT YET APPLICABLE |
| 11 | Forward-return labels | labels only after maturity | collector status | disable the timer | NOT YET APPLICABLE |
| 12 | Commit and install `donchian-forward-collection{,-alert}.{service,timer}` + wrapper, **amending `test_guards.py` and `test_activation_neutral.py` in the same commit**; `systemctl enable --now …timer` | 03:15 / 08:15 Asia/Jerusalem, Tue–Sat | `systemctl list-timers`, `systemd-analyze verify`, first fire's log | `systemctl disable --now …timer` | OWNER APPROVAL REQUIRED |
| 13 | First-session verification | collector COMPLETE; sector verification OK; capture reconciles with the ledger | `forward_collection status`; fingerprints | stop collection | NOT YET APPLICABLE |
| 14 | Status/readiness verification | the session shows as forward-observed with provenance | status report | quarantine | NOT YET APPLICABLE |
| 15 | Rollback criteria (any ⇒ stop) | — | a fingerprint changes; a capture error reaches the screener; a chain fails verification; the runtime role holds UPDATE/DELETE; **two consecutive missed fundamentals runs**; S11 evidence disturbed | — | updated with the missed-run alert |

New watch items from the evidence: (a) alert on a missed fundamentals run (Check A); (b) **Israel DST ends 2026-10-25**: the 23:45 local fire moves to 21:45Z, so
fundamentals (today it starts about 22:13Z and runs ~52 min; an hour later it would run ~23:13Z–00:05Z) would straddle 00:00Z; polls stamped after midnight UTC are *accounted* but not PIT-eligible for that session
(the Slice 11 limitation) — freshness comes from earlier days, so convergence is unaffected, but the same-day confirmation is lost for the last symbols;
(c) `PINC`-style ticker reuse is a standing source of `sector_no_sector_conflict`.

Rollback runbook unchanged (Slice 11 §8): stop future collection (timers, flags, capture state, image pin) is reversible; collected immutable history is
quarantined through the provenance architecture, never deleted.

---

# ACTIVATION GATE — **NO-GO**
Recommendation for owner review only. A `GO` would not trigger anything.

**Exact blockers**
1. **S11** has not been declared passed to this slice (independent gate; not touched).
2. **`model_version='unknown'` → NULL** isolated stage not done (blocks capture, step 9).
3. **Migrations 24–31 not applied** in production per the project records (24/25 need their own explicit approval; not re-queried this slice) and the roles script not re-run.
4. **No production image contains this code**: PR #1 unmerged, no build, no pin change (the translation fix only reaches production through step 6).
5. **Scheduler units not committed** and the two guards not yet deliberately amended.
6. **No pre-activation backup** and no explicit owner activation approval.

**What would make it GO.** All six cleared, plus: the Check C dry run repeated from the VPS immediately before activation; the fundamentals run confirmed
nightly for the days leading in (no gap); a decision on whether `PINC` and the 21 vendor-sector-less symbols (22 = 0.7% of the universe) are acceptable as Option B unavailables
(recommended: yes, availability stays ~99.3%, far above the 90% floor).

**Mutating actions that would then each need separate approval, in order:** pre-activation dump (writes backup files) → migrations 24–31 (new tables,
functions, triggers) → roles-script re-run (grants) → image bump + single pin → `SECTOR_HISTORY_RECORDER_ENABLED=1` → `RESEARCH_CAPTURE_ENABLED=1` and the
`enabled` boundary row → guard amendment + timer install.

---

## 5. The five questions
1. **Is production `daily_fundamentals` fresh enough for the current sector-history policy?** Yes. 3,064 of 3,066 session-universe symbols have a row dated the
   latest session, none has a latest row older than 12 days, and the largest gap between runs in three weeks was 3 days against a 30-day window. Caveats: 25
   symbols (0.8%) have no sector, and two consecutive sessions (09-29, 09-30) produced no fundamentals, so the collector's 4-day window has one day of slack.
2. **Does production use the provider assumed by Slice 11?** Slice 11 assumed nothing and handled both; production is `tiingo` (configured and effective in the
   running bot; the one-shot pipeline container is inferred from the same env-file wiring). The design already covers it.
3. **Can every currently known dotted production symbol obtain the expected yfinance response after translation, preserving identity?** Yes: `BRK.B→BRK-B`,
   `BF.B→BF-B`, `BRK/A→BRK-A` (and the inactive `BRK/B→BRK-B`) all returned full sectors from the VPS network; identity preservation is proved by tests on real
   Postgres. The raw forms returned no sector, a 1-key body, or an error.
4. **Does anything from the real environment invalidate the Slice 7–11 convergence argument?** No. It qualifies it: the DST shift (same-day PIT eligibility
   for the last symbols), the thin cadence margin, the 22 structurally sector-less symbols plus 3 now fixable, and ticker reuse (`PINC`) are real-world
   conditions the design already treats as accounted/unavailable, never as failure. The dotted defect was real and is fixed in the lab.
5. **If activation is authorised after this slice, what changes, in what order, and what is the first irreversible/append-only action?** The runbook above,
   after the six blockers clear. Schema creation (24–31) and the grants are additive and reversible while the tables are empty; the image bump and flag are
   reversible. **The first append-only action is the first `sector_poll` / `sector_observation` insert at the first nightly fundamentals run after
   `SECTOR_HISTORY_RECORDER_ENABLED=1`** (immutable by trigger; removable only through a two-person maintenance ticket), then the capture boundary row at step 9.

## 6. Known limitations and technical debt
* The `created_at` time zone is inferred (UTC), not observed. The pipeline container's environment is inferred. The share of Tiingo-served symbols was not
  measured (no Tiingo call).
* Check A read the fundamentals table; it did not re-verify the migration state or the roles in production (not authorised). Environment NOs for migrations
  and grants rest on the project's own records.
* yfinance behaviour can drift (a quoteType could change; ticker reuse): re-run Check C before activation.
* The translation is evidence-bound: new share classes with letters other than A/B work by rule, but nothing beyond the four production symbols was probed
  from the VPS. Other yfinance callers still pass dotted symbols raw (follow-up).
* `BRK.B` and `BRK/B` map to one vendor symbol by design; only `BRK.B` is active.
