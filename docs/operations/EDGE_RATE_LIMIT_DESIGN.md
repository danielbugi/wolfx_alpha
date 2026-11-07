# Edge rate limiting for the public API: technical design

2026-11-07. **Design for review only.** No image has been built, nothing on the VPS was changed, and no Caddyfile was edited. The owner approved
the design work, and building or deploying a custom Caddy image needs a separate approval after this review.

## 1. Current state (verified read-only on the VPS, 2026-11-07)
| Item | Value |
|---|---|
| Container | `donchian-screener-reverse-proxy-1`, `restart: unless-stopped`, up 6 weeks |
| Image as written in compose | `caddy:2-alpine`, a **floating tag** |
| Image actually running | `sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b` = **`caddy:2.11.4-alpine`**, Caddy v2.11.4 |
| Rate-limit module | none (`caddy list-modules` shows no rate module) |
| Config | `/opt/donchian/compose/docker/Caddyfile.prod`: `encode gzip`, 3 security headers, `reverse_proxy backend:8000` |
| State | `caddy_data` volume (ACME certificates, must survive recreation), `caddy_config` |
| DNS | `api.first-light.finance` → `116.203.220.219`, the VPS itself, with no CDN in front, so `{remote_host}` is the real client address |
| Backend | 1 uvicorn process; in-app token bucket per client IP: feed 60/min, detail 120/min |
| Memory | Caddy uses about 50 MiB |

**Finding, independent of rate limiting:** the floating tag `caddy:2-alpine` moved to a **newer** image on 2026-11-06 (`sha256:78bd3502…`).
- **Risk:** a `docker compose pull` or a lost local image followed by a recreate would silently upgrade production Caddy.
- **Recommended first step on its own:** pin the image in the VPS compose overlay to the image already running, `caddy:2.11.4-alpine@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b`.
  - This changes nothing at runtime: same image, same config.
  - It is a compose edit, so it needs owner approval like any production change.

## 2. Design
### 2.1 Module
- **Module:** `github.com/mholt/caddy-ratelimit`. Apache-2.0, about 500 stars, active; maintained by Caddy's author but **not** part of the official Caddy organization.
- **Pin:** commit `5625512f24f6f59d6f64fb3aafe5eecff0b286db` (2026-06-12, the latest on master today). It declares Caddy v2.10.0 as its minimum, so it builds against v2.11.4.
- **What it provides:**
  - a sliding window per key;
  - HTTP 429 with `Retry-After`;
  - state kept across config reloads, but **not** across restarts;
  - IPv6 grouping by prefix (`ipv6_prefix`).
- **Distributed mode:** not needed for one VPS; not enabled.
- **Placement:** the `rate_limit` directive orders itself before `basic_auth` by default, so no `order` global option is needed. (The integration review's snippet included one; drop it.)

### 2.2 Image (a new build input, reviewed like the others)
`deploy/caddy/Dockerfile`, every input pinned:
```dockerfile
FROM caddy:2.11.4-builder@sha256:369218c81ca6d6af249981221b3a5c764d886dd5b058f51d144066de13f2418d AS build
RUN xcaddy build v2.11.4 --with github.com/mholt/caddy-ratelimit@5625512f24f6f59d6f64fb3aafe5eecff0b286db
FROM caddy:2.11.4-alpine@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b
COPY --from=build /usr/bin/caddy /usr/bin/caddy
```
- **Base:** the runtime base is the exact image production runs today. Only the binary changes: the same Caddy version plus one module.
- **Build:** built by a new `caddy` option in the manual `cd.yml` (workflow_dispatch, like backend and mechanism) and pushed as `ghcr.io/danielbugi/wolfx_alpha-caddy:<sha>`. No `latest` tag is used in production.
- **CI gates before any deploy:**
  1. `caddy version` must print v2.11.4.
  2. `caddy list-modules` must contain `http.handlers.rate_limit`.
  3. `caddy validate --config Caddyfile.prod --adapter caddyfile` must pass, run with `API_SITE_ADDRESS=:80`.
  4. A container test sends 130 requests to a stub backend and expects 429 with `Retry-After` after the limit, and **200 on every other path**.

### 2.3 Caddyfile change
Inside the existing site block, before `reverse_proxy`; nothing else changes:
```caddyfile
{$API_SITE_ADDRESS} {
	encode gzip

	header {
		-Server
		X-Content-Type-Options nosniff
		Referrer-Policy strict-origin-when-cross-origin
	}

	# Public, unauthenticated Fundamental Updates API only. Twice the in-app limits, so the backend stays the precise limiter for normal
	# clients and the edge only stops floods before Python runs. Nothing outside /api/public/ is touched.
	rate_limit {
		zone public_feed {
			match {
				path /api/public/signals
			}
			key {remote_host}
			window 1m
			events 120
			ipv6_prefix 64
		}
		zone public_detail {
			match {
				path /api/public/signals/*
			}
			key {remote_host}
			window 1m
			events 240
			ipv6_prefix 64
		}
		zone public_all_clients {
			match {
				path /api/public/*
			}
			key public_all
			window 1m
			events 3000
		}
		jitter 0.2
	}

	reverse_proxy backend:8000
}
```
- **Per-client zones** stop one address, or one IPv6 /64, from flooding.
- **The all-clients zone** caps total public traffic at 3,000/min (50/s), protecting Postgres from a distributed flood. Authenticated dashboard traffic is never counted.
- **`log_key` is off,** so client IPs are not written to the Caddy log for each rejection.
- **The in-app limiter stays** as the second layer. It also covers any path that reaches the backend without Caddy, such as internal callers.

## 3. Operational impact
| Area | Impact |
|---|---|
| Every request | One extra handler. Non-public paths don't match any zone; the cost is a path match, measured in microseconds |
| Memory | `O(events × keys)`: at most 120–240 timestamps per active IP, swept every minute. Expected under 10 MiB even at 10,000 distinct IPs/min |
| Restarts | Counters reset on a Caddy restart (as do the in-app counters on a backend restart). Acceptable, since limits are per minute |
| Certificates | Unchanged: the same `caddy_data` volume is mounted, so recreating the container keeps certificates and needs no ACME reissue |
| Deploy downtime | A container recreate takes a few seconds, during which HTTPS to the API drops. The dashboard (Vercel) shows its error state for those seconds. Do it outside US market hours and outside the 22:45 UTC pipeline window (the pipeline doesn't use Caddy, but keep changes apart) |
| Failure modes | A bad Caddyfile makes Caddy refuse to start, so the API goes down. Mitigated by CI `caddy validate` with the exact file, and by keeping the old image and Caddyfile ready for a one-command rollback |
| False positives | Shared NAT (offices, mobile carriers) shares one IPv4 key. 120/240 per minute is far above a human reader (a page view = 1–2 requests) |
| Supply chain | One third-party Go module and its dependencies, pinned by commit and image digest. Reviewed at each Caddy upgrade |

## 4. Rollout steps (when approved)
1. **Pin today's image** (section 1), as a separate approval. No functional change.
2. **Build the image** via manual CD and record its digest. The CI gates in 2.2 must pass.
3. **Back up** `docker-compose.prod.yml` and `docker/Caddyfile.prod` on the VPS with a UTC timestamp, the same convention as the existing `.bak.20260924T183421Z` files.
4. **Update the VPS files:**
   - set the reverse-proxy `image:` to `ghcr.io/danielbugi/wolfx_alpha-caddy:<sha>@<digest>`;
   - add the `rate_limit` block to `Caddyfile.prod`;
   - validate first, inside the new image: `docker run --rm -e API_SITE_ADDRESS=api.first-light.finance -v ./docker/Caddyfile.prod:/etc/caddy/Caddyfile:ro <image> caddy validate --config /etc/caddy/Caddyfile`.
5. **Recreate only the proxy:** `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps reverse-proxy`.
6. **Verify:**
   - `curl -sI https://api.first-light.finance/api/health` returns 200;
   - the dashboard's authenticated calls return 200;
   - `/api/public/signals` answers as before (503 until migration 41);
   - one burst of 130 requests to `/api/public/signals` from the VPS gets 429 after 120. Done from inside the Docker network, this uses the same key path;
   - `caddy list-modules` in the container shows `http.handlers.rate_limit`.

## 5. Rollback (about 1 minute, no data involved)
1. Restore the two backed-up files: `docker-compose.prod.yml` (image back to `caddy:2.11.4-alpine@sha256:6aeddd44…`) and `Caddyfile.prod`.
2. Run `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps reverse-proxy`.
3. Verify `/api/health` returns 200 and `caddy list-modules` shows no rate module.

The previous image stays on the VPS (`docker images` shows `6aeddd44c307`), so rollback needs no registry pull. Certificates live in `caddy_data`, which neither direction touches.
**Partial rollback (keep the image, disable limiting):** remove the `rate_limit` block and run `docker exec donchian-screener-reverse-proxy-1 caddy reload --config /etc/caddy/Caddyfile` for a zero-downtime config reload.

## 6. Alternatives (not recommended)
- **Redis-backed limiter in the app:** a new container and client library, more moving parts than one VPS needs.
- **PostgreSQL counter:** every public read becomes a write.
- **In-app only:** correct today with one process, wrong as soon as workers are added, and the work still reaches Python.
- **A CDN or WAF in front** (for example Cloudflare): moves TLS termination off the VPS and changes DNS. Larger scope than this need.
