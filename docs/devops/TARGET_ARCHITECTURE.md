# Target Architecture

> Companion to [SERVICE_INVENTORY.md](SERVICE_INVENTORY.md). Priorities given by the user: reduce
> monthly cost, keep the system simple, reliable independent deploys, easy for **one developer** to
> maintain, avoid unnecessary DevOps complexity. Kubernetes is not recommended — nothing about this
> system's scale (2 dashboard users, ~1,000-2,500 symbols, one Postgres instance, six lightweight
> processes) presents a concrete technical reason for orchestration complexity beyond Docker Compose.

## 1. Option comparison

### Option A — Everything on one VPS, Docker Compose

Frontend, backend API, Telegram bot, mechanism pipeline (as scheduled containers/cron), Postgres, and
a reverse proxy (Caddy/Traefik/nginx) all run as Compose services on a single VPS.

| | |
|---|---|
| **Advantages** | Cheapest possible (one bill); simplest mental model (one `docker-compose.yml`, one `ssh`, one place to look at logs); zero network latency between services (all on localhost/Docker bridge network); matches the system's actual current coupling (everything already assumes `localhost` Postgres); fastest to build from scratch (this project has zero existing containers, so Option A is the shortest path to *any* real CI/CD) |
| **Disadvantages** | The nightly ML retrain / mechanism pipeline's CPU burst can degrade API/bot latency during the run, since everything shares one host's CPU/RAM/disk I/O; a single host is a single point of failure for the *entire* system, not just the database (mitigated by the DB being the real irreplaceable state — see below); vertical scaling (bigger VPS) is the only scaling lever |
| **Operational complexity** | Low. One `docker compose up -d`, one `docker compose logs -f <service>`, standard Docker restart policies |
| **Failure impact** | If the host goes down, everything goes down simultaneously — but this is already true today (single Windows machine), so Option A is a strict reliability *improvement*, not a new risk |
| **Scaling characteristics** | Vertical only within Compose; adequate for this project's actual traffic (2 dashboard users, ~2,500-symbol batch job, one Telegram bot) for the foreseeable future |
| **Approximate resource requirements** | 2 vCPU / 4GB RAM is workable if the nightly pipeline/ML retrain is scheduled off-peak (already the case, 02:00) and Postgres gets its own resource reservation; 4 vCPU / 8GB RAM gives real headroom and lets the ML retrain run without visibly affecting API latency |
| **Migration difficulty** | Low — this is the "first real deployment" path; no existing infrastructure to unwind |
| **Cost** | Cheapest option: one VPS, e.g. $20-40/mo (4 vCPU/8GB tier at most providers) covers this comfortably |

### Option B — Multiple VPSs grouped logically (as sketched in the prompt: frontend+API+proxy /
workers+automation / database)

| | |
|---|---|
| **Advantages** | Isolates the nightly pipeline/ML CPU burst from API-serving latency entirely (separate hosts, separate resource pools); database isolated from application bugs/crashes; each group can be resized independently |
| **Disadvantages** | 2-3x the monthly cost for a workload that, per SERVICE_INVENTORY.md, totals maybe 4-6 vCPU / 8-12GB RAM *combined* — splitting that across 3 small VPSs is typically *more* expensive than one right-sized VPS, not less; adds real operational surface for a one-person team: 3 sets of OS patches, 3 SSH targets, network security between hosts (the API↔DB link now crosses a network boundary and needs either a VPC/private network or a hardened public Postgres — meaningfully more to get wrong); the database being remote from the API adds latency to *every* request, worth measuring against this app's own perf-tuning history (CLAUDE.md's `/api/performance/` work fought hard to cut a 43-53ms *local* connection-acquire cost — a cross-host Postgres link would very likely re-introduce a bigger version of the exact problem that work fixed) |
| **Operational complexity** | Medium-high for one developer: 3 hosts to patch/monitor/secure, a private network or VPN between them, more moving parts in the CI/CD pipeline (deploy to the right host per service) |
| **Failure impact** | Better isolation — the DB VPS going down still takes everything down (nothing works without it), but the workers VPS going down only stops scheduled jobs, leaving the live dashboard/bot up on stale data. This is a real advantage over Option A. |
| **Scaling characteristics** | Each group scales independently — the most flexible option, but this project has no demonstrated need for that flexibility yet (2 users, no burst traffic pattern) |
| **Approximate resource requirements** | VPS1 (frontend+API+proxy): 1-2 vCPU/2GB; VPS2 (workers/pipeline/ML): 2-4 vCPU/4-8GB (needs the most RAM, for XGBoost training over 595k rows); VPS3 (Postgres): 1-2 vCPU/2-4GB + persistent block storage |
| **Migration difficulty** | Medium — same containerization work as Option A, plus inter-host networking/DNS/firewall setup |
| **Cost** | Roughly $45-90/mo (three small-to-medium VPS instances) — 2-3x Option A for a workload this size |

### Option C — One primary VPS + managed external services where appropriate

Frontend on a managed static/edge host (e.g. Vercel), backend API + Telegram bot + mechanism pipeline
on one VPS, database either kept on that VPS or moved to a managed Postgres provider later if
backup/HA needs grow.

| | |
|---|---|
| **Advantages** | Removes the one component (Next.js frontend) that a managed platform genuinely does better and cheaper than self-hosting: zero-config deploys from the existing repo, free TLS, a global CDN, and *zero added monthly cost* on Vercel's free tier for a 2-user internal tool; keeps everything with real state (Postgres) and everything with real secrets (API keys, bot token) under the developer's own control on the VPS, where it already lives; smallest possible incremental change from Option A — same VPS, same Compose file, just minus the frontend container |
| **Disadvantages** | Introduces one more vendor relationship to track (Vercel) and one more place secrets could theoretically be exposed (though only `NEXT_PUBLIC_API_BASE_URL` — a public URL, not a secret — needs to reach it); the VPS backend must be reachable over HTTPS with a real domain for Vercel's HTTPS frontend to call it without a mixed-content browser block — this is a real prerequisite, not optional |
| **Operational complexity** | Lowest of the three for a solo developer: one VPS to patch/monitor, one `git push` to redeploy the frontend (Vercel handles the rest), no reverse-proxy config needed for the frontend at all |
| **Failure impact** | Frontend outages become extremely unlikely (Vercel's uptime SLA far exceeds anything self-hosted) and are now decoupled from the VPS — a VPS reboot no longer risks the UI being unreachable, only the API/data behind it |
| **Scaling characteristics** | Frontend scales automatically and for free under Vercel; backend/DB scaling is identical to Option A (vertical, on the one VPS) |
| **Approximate resource requirements** | VPS: 2-4 vCPU/4-8GB (same math as Option A minus the frontend's small footprint) |
| **Migration difficulty** | Low — same containerization/CI work as Option A for the backend/bot/pipeline, plus a one-time Vercel project import for the frontend (no code changes beyond what's already true: `NEXT_PUBLIC_API_BASE_URL` must point at a real HTTPS backend URL) |
| **Cost** | **Lowest realistic option**: VPS ($20-40/mo) + Vercel free tier ($0) = same or less than Option A, because the VPS no longer needs to reserve resources for a Node build/serve process |

## 2. Recommendation

**Option C**, with the VPS portion built exactly like Option A (Docker Compose, one host, Postgres
co-located) so that if Vercel is ever rejected (e.g., a future requirement to keep 100% of the stack
under one roof) the fallback is a one-line change (self-host the frontend container that's already
built and tested for Option A) rather than a redesign. This matches every stated priority:

- **Cost:** cheapest of the three options.
- **Simplicity:** one Docker Compose file governs the entire self-hosted surface; the frontend needs
  no server-side config once on Vercel.
- **Independent deploys:** already a hard requirement (Phase 5) — satisfied identically well by
  Options A and C for the backend/bot/pipeline; Vercel adds *automatic* independent frontend deploys
  on every push for free, which is strictly better than self-hosting it.
- **Solo-developer maintainability:** fewer hosts to patch (one VPS instead of three), no inter-host
  networking to secure, no VPC to configure.
- **No unnecessary complexity:** explicitly rejects Option B's multi-host networking overhead, which
  this project's actual resource needs (SERVICE_INVENTORY.md — a few vCPUs, a few GB RAM combined) do
  not justify. Also explicitly rejects Kubernetes — no service here needs autoscaling, no team needs
  a shared multi-tenant cluster, and the operational cost of learning/running k8s for a single
  developer vastly exceeds any benefit at this scale.

**One exception to flag for revisit, not built now:** if the nightly ML retrain (pipeline steps 10-12)
turns out, once measured under the new schedule, to meaningfully degrade API/bot responsiveness on a
shared VPS, the cheapest fix is a Docker resource limit (`--cpus`, `--memory` in Compose) on that one
container *before* reaching for Option B's multi-host split — try the free lever before paying for a
second VPS.

## 3. Target service map

```
                        Vercel (frontend, free tier, auto-deploy on push)
                                     │  HTTPS
                                     ▼
                     ┌─────────────────────────────────────┐
                     │            One VPS (Docker Compose)     │
                     │                                          │
                     │  ┌───────────────┐   ┌────────────────┐ │
   HTTPS ────────────┼─►│ Traefik/Caddy   │──►│ Backend API      │ │
   (api.example.com) │  │ (reverse proxy, │   │ container        │ │
                     │  │ auto TLS)       │   │ (uvicorn, no      │ │
                     │  └───────────────┘   │  --reload)        │ │
                     │                        └────────┬────────┘ │
                     │  ┌────────────────┐              │          │
                     │  │ Telegram bot     │              ▼          │
                     │  │ container         │   ┌────────────────┐ │
                     │  │ (long polling,    │──►│ Postgres         │ │
                     │  │ restart:always)   │   │ container        │ │
                     │  └────────────────┘   │  + named volume   │ │
                     │                        └────────┬────────┘ │
                     │  ┌────────────────┐              ▲          │
                     │  │ Pipeline runner  │──────────────┘          │
                     │  │ container         │  (host cron / Compose  │
                     │  │ (scheduled, not   │   `run` invocation,     │
                     │  │ long-running)     │   not long-running)     │
                     │  └────────────────┘                         │
                     │  ┌────────────────┐                         │
                     │  │ Channel-sender   │  (same pattern:         │
                     │  │ scheduled jobs    │  scheduled, one-shot)  │
                     │  └────────────────┘                         │
                     └─────────────────────────────────────┘
```

Key differences from today: the pipeline and channel senders become *scheduled container runs*
(`docker compose run --rm pipeline`) triggered by host `cron`, replacing Windows Task Scheduler
1:1 in concept; a reverse proxy terminates TLS and is the only inbound-facing piece besides SSH;
Postgres gets a named Docker volume with an actual backup job (see DISASTER_RECOVERY.md); the backend
runs `uvicorn main:app` (no `--reload`) under Docker's `restart: unless-stopped` policy instead of a
manually-started terminal.

## 4. Deferred / explicitly not recommended now

- **Kubernetes** — no concrete need identified; revisit only if the project grows to multiple
  independently-scaling backend replicas or a multi-region requirement, neither of which is on the
  roadmap.
- **Managed Postgres (Supabase, RDS, etc.)** — matches the conclusion already reached in
  `PLATFORM_ARCHITECTURE.md`: keep Postgres self-hosted on the VPS until managed backups/PITR/failover
  become a real priority; revisit if data-loss risk tolerance changes or the database outgrows one
  VPS's disk.
- **Splitting the backend into microservices beyond the four that already exist** (frontend, API,
  bot, pipeline) — the routers inside `backend/` (screener, alpha, strategy, deep-value, etc.) are
  cohesive, share one Postgres connection pool, and have no independent scaling or deployment need
  that would justify splitting them into separate containers.
