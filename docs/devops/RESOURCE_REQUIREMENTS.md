# Resource Requirements & Isolation Analysis

> Companion to [SERVICE_INVENTORY.md](SERVICE_INVENTORY.md) and [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md).
> Analysis only — nothing deployed or changed. Where possible, figures below are **measured against
> the live local database and filesystem in this session** (read-only queries), not guessed — each
> measured figure says so explicitly; everything else is a reasoned estimate from the code, clearly
> labeled as such.

## 0. Ground-truth numbers pulled from the live system

| Fact | Value | How it was obtained |
|---|---|---|
| Total Postgres database size | **4,511 MB (~4.5 GB)** | `pg_size_pretty(pg_database_size(current_database()))` |
| `technical_indicators` | 1,883 MB, 6,384,301 rows | `pg_total_relation_size` + `COUNT(*)` |
| `stock_prices` | 1,433 MB, 6,430,835 rows | same |
| `ml_breakout_dataset_v2` | 799 MB, 594,888 rows | same (this table is **rebuilt with `--replace` nightly, not appended** — it does not grow unbounded like the two above) |
| `daily_fundamentals` | 130 MB, 514,086 rows | same |
| `enhanced_ml_training_data` | 108 MB | same (legacy/stale table per CLAUDE.md — candidate to drop, see §6) |
| `digest_stocks` | 34 MB, 173,928 rows | same |
| Everything else (25+ smaller tables: auth, Telegram, access, audit) | < 20 MB combined | same |
| `logs/` directory | **444 MB** | `du -sh` |
| `breakout_results/` directory | **177 MB** | `du -sh` (timestamped JSON snapshot per pipeline run, never pruned) |
| `frontend_data/` directory | 12 MB | `du -sh` |
| `ml_training/models/` directory | 70 MB | `du -sh` (versioned `.joblib` model files, never pruned) |
| `reports/` directory | 3.8 MB | `du -sh` |
| Dev machine CPU/RAM (context only — not a sizing target) | 12 logical cores, 51.5 GB RAM | `os.cpu_count()` / `psutil` |

**Two disk-growth risks found by this measurement that weren't visible from reading code alone:**
`logs/` (444 MB) and `breakout_results/` (177 MB) both accumulate one new timestamped file per
pipeline run with **no retention/rotation policy anywhere in the code** — left unmanaged, these grow
without bound on any host, including a production VPS with a fixed disk size. This is a concrete
input into §3's disk sizing and belongs in the go-live checklist (see
[PRODUCTION_READINESS_CHECKLIST.md](PRODUCTION_READINESS_CHECKLIST.md)).

**Estimated database growth rate:** `stock_prices` + `technical_indicators` are the only two tables
that grow unbounded with time (one row per symbol per trading day, forever). At current row/size
ratios (~233 bytes/row and ~309 bytes/row respectively, including indexes) and the current
~1,000-2,500 symbol universe, that's roughly **400-600 MB/year** combined, before accounting for the
planned Russell-3000 universe expansion (CLAUDE.md §7.2, still open) which would raise this
proportionally (~2-3x at 3,000 symbols). Everything else in the database either doesn't grow
(`ml_breakout_dataset_v2` is replaced, not appended) or grows slowly (Telegram/auth tables, bytes per
user action, negligible at 2 dashboard users + the current bot userbase).

## 1. Per-service resource requirements

| Service | CPU | RAM | Disk | Network | Persistent storage | Usage pattern | Can it interfere with co-located services? |
|---|---|---|---|---|---|---|---|
| **Frontend** (Next.js) | Low — 0.25-0.5 vCPU steady state (SSR for 2 users); a `next build` briefly wants more (1-2 vCPU for ~1-2 min) | 150-300 MB steady; build process can spike to 500 MB-1 GB briefly | Image ~150-300 MB (Node + built app); no persistent data | Low — a handful of small JSON API calls per page view, ~2 concurrent users | None (stateless) | **Bursty**, and rare (mostly idle between the 2 users' sessions); if self-hosted, `next build` is the only real spike | Only during a build/deploy, and only if self-hosted alongside other CPU-bound work — **irrelevant under the recommended Vercel option**, since it runs off-VPS entirely |
| **Backend API** (FastAPI/Uvicorn) | Low-moderate — 0.5-1 vCPU steady for 2 concurrent dashboard users; the `/ml-stats` route briefly loads a ~10s-scale XGBoost `joblib.load()` on a cache miss | 300-600 MB steady (connection pool + in-memory TTL caches of the `frontend_data/*.json` files + occasionally a loaded model, ~50-150 MB depending on model size) | Image ~300-500 MB; no meaningful persistent disk beyond logs | Low-moderate — proxies/queries Postgres per request (same host, so effectively free), serves small JSON payloads to 2 users | None beyond its log files (state lives in Postgres) | **Mostly constant-low**, with request-driven micro-bursts; no scheduled heavy work of its own | **Yes, moderately** — it needs Postgres query latency to stay low; if Postgres is starved by the pipeline/ML training running on the same host, API response times degrade (this is the concrete, measured concern already fought once in this project — see CLAUDE.md's `/api/performance/` work) |
| **Telegram bot** (`run_bot.py`) | Low steady (idle long-poll); spikes to 1+ vCPU briefly when rendering a chart (`mplfinance`) or answering `/scan`/`/screen` (a full-universe query) | 200-400 MB steady; chart rendering (Pillow/matplotlib in-process) can spike to 500 MB-1 GB transiently per request | Image ~400-600 MB (matplotlib/Pillow/mplfinance are not small); a small local chart-cache concept exists but is tracked in Postgres (`bot_chart_cache`), not disk — no meaningful persistent local disk | Low — mostly idle long-poll to Telegram, occasional Alpaca news API calls, occasional outbound photo uploads to Telegram | None (all durable state in Postgres) | **Bursty, low-frequency** — idle the vast majority of the time, brief CPU/RAM spikes per user interaction | **Mild** — a burst (e.g., someone requesting `/scan` across the full universe, or several chart renders in quick succession) could transiently compete with the API for CPU, but is short-lived and low-probability at current user counts |
| **Channel senders** (`send_daily_digest.py` etc., one-shot) | Moderate for their brief runtime — building the market card image (`market_card.py`, Pillow) and the momentum board (candlestick chart) are the heaviest single operations here, roughly comparable to a bot chart render but for the whole universe's data | 300-600 MB transient, for the duration of the script (typically well under 1-2 min) | Writes a promo/market-card PNG to `reports/first_light/` (a few MB per run — already small per §0) | Low — reads Postgres, a handful of outbound Telegram Bot API calls (text + photo) | None beyond the PNG output (small, already measured at 3.8 MB total for `reports/`) | **Scheduled, one-shot, predictable timing** (six fixed times/day) | Low — runs briefly, at scheduled times that can be chosen to avoid overlapping the heaviest pipeline steps |
| **Mechanism pipeline** (`automation_pipeline.sh`, 12 steps) | **The heaviest sustained CPU consumer in the system** — threaded fetch across ~1,000-2,500 symbols (5 updaters), `fundamentals_updater.py` alone documented at ~25 min even after tuning; realistically wants 1-2 vCPU sustained for its multi-hour run, more helps but is rate-limited by vendor APIs more than local CPU for the fetch steps specifically | 500 MB-1.5 GB during the fetch/screening steps (pandas DataFrames over thousands of symbols); the screener's multi-timeframe scoring is the heavier in-memory step | Ephemeral — writes to Postgres + `frontend_data/*.json` (12 MB, small) + `breakout_results/` (**already 177 MB and growing — needs retention**) + `logs/` (**already 444 MB and growing — needs retention**) | Moderate-high — the actual bottleneck for the fetch steps is *external* vendor API rate limits (Tiingo/Alpaca/yfinance), not local network capacity | The JSON snapshot files above; no independent database of its own (writes into the shared Postgres instance) | **Scheduled, single daily run, multi-hour duration (historically 1-4h, now longer with ML steps appended)** — the textbook definition of a bursty, off-peak batch job | **Yes, significantly** — this is the process most likely to degrade the API/bot if co-located without limits, both via CPU contention and via Postgres write-lock/I/O contention during its bulk upserts |
| **ML training** (dataset rebuild + 2x XGBoost train, pipeline steps 10-12) | **The single heaviest CPU/RAM spike in the whole system** — training XGBoost with bootstrap CI + walk-forward folds over ~595k rows, twice (two targets), is meaningfully more demanding than anything else here; wants 2+ vCPU for a bounded window to avoid a very long training time | **The RAM ceiling for VPS sizing** — assembling a ~595k-row feature DataFrame plus XGBoost's own in-memory structures plausibly wants 1.5-3 GB peak, depending on feature count; this is an estimate (not measured in this session — no live training was run), and should be **confirmed with a real run and `docker stats` before finalizing the VPS RAM tier** | Writes `ml_training/models/*.joblib` (already measured: 70 MB total across all versions kept — **also unbounded without a retention policy** on old model files) | Low — pure local computation once `stock_prices` is read; no external calls | Model artifact files (disk) — see §0 note on this being a soft single point of failure worth including in backups | **Scheduled, once nightly, bounded duration** (part of the same 02:00 pipeline run) | **Yes, the most of any service** — this is the one workload where a VPS sized only for "API + bot steady state" would visibly stall everything else sharing its CPU/RAM for the duration of the training window |
| **PostgreSQL** | Low-moderate steady (serving 2 dashboard users + the bot); **high burst during the nightly pipeline's bulk upserts** (thousands of symbols × multiple tables) | Postgres itself: modest for this data volume — `shared_buffers` in the 256-512 MB range is reasonable at 4.5 GB total data; more helps cache hit ratio but isn't required at this scale | **Persistent, measured: 4.5 GB today, growing ~0.4-0.6 GB/year** at the current universe size (§0) — size the volume with real headroom, not just today's footprint | Low if co-located with the API (localhost/Docker-bridge traffic is effectively free); would become a real, measurable latency factor if ever split onto a separate host (see §4) | **The one service with real persistent storage requirements** — needs a Docker named volume (or host bind mount) with its own backup strategy (DISASTER_RECOVERY.md) | **Mostly steady low load, with a predictable nightly burst** matching the pipeline's schedule | It is the one service everything else *depends on* rather than competes with — but the pipeline's write burst can slow down concurrent API reads if the disk I/O is shared and not provisioned with headroom |

## 2. Isolation recommendation per service

The instruction is explicit: **no VPS-level split without a concrete, resource-driven reason.**
Applying that standard to the measurements above:

| Service | Recommended isolation level | Reason |
|---|---|---|
| Frontend | **Managed external service (Vercel)** | Not a resource-contention argument — a cost/simplicity one (TARGET_ARCHITECTURE.md §1, Option C): it's free, removes a Node process from the VPS entirely, and Vercel's own infrastructure isolates it far better than anything self-hosted would for zero extra cost. If Vercel is ever rejected, the fallback is **Docker container level** on the same VPS (it's stateless, low-resource, and shares no data with anything else — no reason it would ever need its own host). |
| Backend API | **Docker container level**, same Compose stack as Postgres/bot | Low, mostly-constant resource use (§1); its only real requirement is *low-latency Postgres access*, which argues **for** co-location, not against it. A container boundary is sufficient to isolate its crashes/restarts from other services without paying for a second host. |
| Telegram bot | **Docker container level**, same Compose stack | Same reasoning as the API — low steady resource use, occasional short bursts, no data of its own outside Postgres. Its only real isolation need (an independent restart/deploy lifecycle from the API — SERVICE_INVENTORY.md §3) is already satisfied by being a **separate container**, not a separate host. |
| Channel senders | **Docker container level** (run via `docker compose run`, not a long-lived service) | One-shot, scheduled, low resource footprint (§1). No concrete reason to isolate further — they already don't run continuously, so they can't "starve" anything except during their own brief execution window, which can be scheduled to avoid the pipeline's heaviest steps. |
| Mechanism pipeline | **Docker container level, but with an explicit Compose resource limit (`cpus:`, `mem_limit:`)** — not a separate VPS | This is the service with the strongest case for *some* isolation (§1: heaviest sustained CPU/network user), but the concrete fix is a **resource cap on one container**, which is free and simple, before reaching for a second host, which costs money and adds operational surface (TARGET_ARCHITECTURE.md's Option B critique). Revisit only if a capped container still measurably degrades API latency during its run — see §3's scaling trigger. |
| ML training | **Docker container level, with the same resource cap approach, scheduled for the lowest-traffic window** (already true — 02:00 Israel time, off-hours for a US-market audience) | This is the single heaviest spike in the system (§1), but it is also the **shortest-lived and most schedulable** — a resource cap plus deliberate off-peak timing addresses the actual risk (API/bot degradation during the spike) without needing dedicated hardware. A separate VPS would sit >95% idle the rest of the day, which is the opposite of this project's stated cost priority. |
| PostgreSQL | **Docker container level, same Compose stack, on a named volume** (not a separate VPS; not managed, at this scale — see §4) | The measured data volume (4.5 GB, growing slowly) and query load (2 dashboard users + one bot) do not justify a dedicated host. The one thing Postgres *does* need — protection from the pipeline's write burst starving API read latency — is addressed by resource limits on the *pipeline* container, not by moving Postgres itself anywhere. |

**No service in this system currently has a concrete, measured resource requirement that justifies a
separate VPS.** The closest candidate (ML training's CPU/RAM spike) is bounded, predictable, already
schedulable to off-peak hours, and solvable with a free Docker resource limit — exactly the kind of
case the "try the free lever before paying for a second VPS" principle in TARGET_ARCHITECTURE.md §2
was written for.

## 3. Single-VPS feasibility and recommended specification

**Yes — the entire system (backend, bot, pipeline, ML training, Postgres, reverse proxy) can
reasonably run on one Linux VPS.** Summing §1's steady-state figures: roughly 1-2 GB RAM and well
under 1 vCPU for the *always-on* services (API + bot) combined; the pipeline/ML training's nightly
peak is the number that actually sizes the box, since it must not starve the always-on services during
its ~1-4h window.

### Recommended initial specification

| Resource | Recommendation | Reasoning |
|---|---|---|
| **vCPU** | **4 vCPU** | 1 vCPU covers the always-on API+bot with real headroom; the remaining 3 give the pipeline/ML training room to run without visibly starving them, based on §1's "2+ vCPU wanted for training" estimate plus the pipeline's own threaded fetch |
| **RAM** | **8 GB** | Always-on services want ~1-2 GB combined (§1); Postgres wants some cache headroom (~1 GB is generous at 4.5 GB data); ML training's peak is estimated 1.5-3 GB (§1, **unverified — confirm with a real run**); 8 GB leaves comfortable slack above the sum of all of this running concurrently, rather than sizing to the bare minimum |
| **SSD** | **80-100 GB** | Today's actual footprint: ~4.5 GB DB + ~0.7 GB app output dirs (§0) ≈ 5.2 GB. Budget for: DB growth (~0.5-0.6 GB/year, more if the universe expands to Russell 3000 per CLAUDE.md §7.2 — budget 2-3x that rate to be safe, ~1.5 GB/year), Docker images (4 images × ~0.3-0.6 GB each ≈ 1.5-2.5 GB), OS + Docker overhead (~10-15 GB), backup staging space before off-box upload (§ DISASTER_RECOVERY.md — at least one full DB dump's worth, ~5 GB, held locally before/after the off-box sync), and multi-year headroom without resizing. 80 GB gives years of runway at the current growth rate; 100 GB if the Russell-3000 expansion is imminent. |
| **Expected headroom at this spec** | ~50-65% RAM and ~50% CPU unused outside the nightly pipeline/ML window; ~90%+ disk unused initially | This is intentionally generous relative to bare measured needs — see the "SIMPLE > CHEAP > RELIABLE" priority ordering: a right-sized-with-headroom single box is cheaper and simpler than optimizing down to the minimum and then needing to resize under time pressure |

**Cost context:** a 4 vCPU / 8 GB / 80-100 GB SSD VPS is a mid-tier offering at essentially every major
provider (e.g., in the range of $40-48/mo on DigitalOcean/Linode/Hetzner-equivalent pricing as of
late 2025/2026 — confirm current pricing with the specific provider chosen, this is not a quoted
price). This is **higher than the 2 vCPU/4 GB figure floated in TARGET_ARCHITECTURE.md's Option A
write-up** — that earlier estimate was made before this session's real ML-training resource estimate
was worked out in this level of detail; **treat this document's 4 vCPU/8 GB figure as the current,
more-informed recommendation**, superseding that earlier rough number.

### What would trigger 1 VPS → 2 VPS → multiple servers

| Trigger | What it means | What moves first |
|---|---|---|
| **Sustained, measured Postgres query latency degradation during the nightly pipeline/ML window** (e.g., `/api/performance/`'s own p95 tracker — already built into this codebase — showing API latency regularly spiking above its healthy threshold specifically during the 02:00-06:00 window, even after Docker resource limits are tuned) | The single-host resource-sharing model has hit its ceiling for this workload | **Postgres moves to its own VPS first** — not the pipeline. Rationale: Postgres is the one service every other service depends on for correctness (not just performance), so isolating *it* protects the most critical path; the pipeline/ML training remain flexible to schedule or cap further first, since they're the actual source of the contention and cheaper to constrain than to relocate |
| **The symbol universe grows enough (Russell 3000, ~3,000 symbols per CLAUDE.md §7.2) that ML training's memory estimate (§1, currently 1.5-3 GB *unverified*) is confirmed to exceed ~50% of the box's RAM even with other services quiesced** | The batch workload has genuinely outgrown "a spike the rest of the system tolerates" | **ML training moves to its own VPS** (or, cheaper first: a bigger single VPS — re-run the §3 sizing math with real, measured numbers from a production run before assuming a second host is needed) |
| **A second developer/on-call person needs independent access to run the pipeline/bot without touching the API's host** (an organizational trigger, not a resource one) | Operational separation of concerns becomes valuable for its own sake | **The bot + channel senders** are the natural second-host candidate (SERVICE_INVENTORY.md notes they're already the most independent service pairing) |
| **Real concurrent user count grows past a handful** (e.g., the dashboard is opened to a paying user base beyond Owner/Collaborator) | The API's own resource needs, not just the batch jobs', start mattering | **Scale the API vertically first** (bigger single VPS, or a second API container behind the reverse proxy on the *same* box) before splitting hosts — this project has no evidence yet that horizontal API scaling is needed, and a bigger single box is simpler and cheaper up to a fairly high ceiling |

None of these triggers are currently met — they're listed so a future resource crunch has a
predefined, measurement-based answer instead of a guess made under pressure.

## 4. Database (and Redis) hosting decision

### PostgreSQL: Option A (Docker on the same VPS) vs. B (separate VPS) vs. C (managed)

| | A: Docker, same VPS | B: Separate VPS | C: Managed Postgres (RDS/Supabase/DO Managed DB) |
|---|---|---|---|
| **Cost** | **$0 marginal** — already included in the single VPS's price | Full second VPS cost (~$20-40/mo minimum) for a database that, per §0, is 4.5 GB and growing slowly — most of a second small VPS would sit idle | Meaningfully more than self-hosting at this data size — e.g., a managed Postgres tier with daily backups/PITR typically starts around $15-25/mo for a small instance and rises with storage/compute; `PLATFORM_ARCHITECTURE.md`'s own earlier analysis flagged Supabase specifically (~$25/mo) as "pure added cost for no capability this project is currently missing" at this scale — this analysis reaches the same conclusion independently from the resource numbers |
| **Backup complexity** | Manual but simple to automate (a `pg_dump` cron job — already designed in DISASTER_RECOVERY.md); the operator is fully responsible for remembering to wire it up | Same complexity as A, plus the operational overhead of a second host to patch/secure for no backup-specific benefit | **Lowest complexity** — automated backups/point-in-time recovery are the core value proposition of a managed offering, no cron job to maintain |
| **Recovery** | Manual restore from the `pg_dump` (documented, testable — DISASTER_RECOVERY.md §2) | Same as A | Push-button restore/PITR through the provider's console — meaningfully faster and lower-risk in an actual incident |
| **Maintenance** | Self-managed: Postgres version upgrades, tuning, monitoring are the operator's job (though Docker makes version upgrades a straightforward image-tag bump) | Same as A | Provider-managed — patching, minor version upgrades, and failover (if enabled) are handled automatically |
| **Failure risk** | Tied to the single VPS's disk/host — mitigated by off-box backups (DISASTER_RECOVERY.md), but there's no automatic failover | Same single-point-of-failure risk as A, just on a different box — **isolating Postgres onto its own VPS does not, by itself, add redundancy**, only separates *which* host's failure takes down which services | **Lowest failure risk** — managed offerings typically include multi-AZ replication/automatic failover as an add-on or default, something self-hosting on one VPS structurally cannot match without a second Postgres instance and real replication setup |
| **Network latency to the API** | **Effectively zero** (localhost/Docker bridge) | A real, non-zero cross-host hop — directly re-introduces the class of problem this project's own `/api/performance/` work already fought hard to eliminate (CLAUDE.md documents cutting a 43-53ms *local* connection cost; a cross-host link would likely cost more per request, not less) | Depends on region/network path to the provider; typically low but non-zero, and outside the operator's control |

**Recommendation: Option A — Postgres in Docker on the same VPS**, matching
`PLATFORM_ARCHITECTURE.md`'s earlier independent conclusion and this session's resource measurements:
at 4.5 GB and ~0.5 GB/year growth, neither B's isolation nor C's managed-service premium is justified
by anything measured in §0-§3. **Revisit Option C specifically if:** point-in-time recovery becomes a
real requirement (not just nightly-granularity backups), the data volume grows enough that self-managed
tuning becomes genuinely difficult for a solo operator, or uptime requirements grow to need automatic
failover — none of which are true today.

### Redis

**Not applicable — this system has no Redis, and none is recommended.** A full-text search of
`mechanism/`, `backend/`, and `frontend/src/` for `redis` returned zero matches (ARCHITECTURE_AUDIT.md
§2 confirmed this already). The backend's caching need is already met by simple in-process, in-memory
TTL caches (2-15 min, per CURRENT_ARCHITECTURE.md §3) appropriate for a single backend instance
serving 2 users — introducing Redis would add a service, a container, a failure mode, and an
operational dependency with no corresponding requirement this system actually has (there is no
multi-instance cache-sharing need, no session store beyond the already-working JWT/refresh-token
design, and no queue/pub-sub usage anywhere in the code). **Revisit only if the API is ever scaled to
multiple concurrent instances** (§3's last scaling trigger) and those instances need a shared cache —
not before.
