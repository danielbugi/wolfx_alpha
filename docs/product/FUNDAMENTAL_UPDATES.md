# Fundamental Updates: Phase 1 (built) and Phase 2 (architecture)

Status 2026-11-07. Branch `feat/fundamental-updates-v2`. Phase 1 and Phase 2 are built on the branch. **Nothing is sent, deployed, merged or migrated** (migration 41 is written and tested, not applied). The current owner alert (`mechanism/inflection/alerts.py`, `telegram_text`) is unchanged and remains the default.

## Owner decisions (2026-11-07)
| # | Decision |
|---|---|
| 1 | One public channel eventually, rolled out private owner chat → dev channel → public channel. Public sending needs **two weeks of clean production runs on the corrected image (`1fc1753cdae0`) and explicit owner approval**. |
| 2 | `/signals` is the technical route; public wording is **"Fundamental Updates"**. Factual developments, not recommendations, not proven alpha. |
| 3 | Option A: **public, read-only event pages** behind a narrow public API. `/inflection` stays the internal research interface. No detection logic, internal scores, private research data or unpublished fields are exposed. |

## Findings that shape the design
1. **The alerting version has no fresh events this season.**
   - Owner alerts read only `inflection_v1` (`ALERT_METHOD_VERSIONS`). That version has no event for any quarter ending after 2026-08-31.
   - Its November-dated events are the June-quarter events re-dated by the next report (the 16 misleading alerts).
   - Every on-time September-quarter event is `inflection_v2_sec_pit`, which is not alert-enabled; its first full run wrote 2,116 events in one night.
   - Fundamental Updates will need an owner decision on which version feeds it. v2 is the better source: SEC acceptance dates and an exact filing (accession) per quarter.
2. **News is fetched on demand only.**
   - `news_items` holds headlines for stocks someone opened in the bot or on the dashboard. None of the six preview companies had any.
   - Updates must fetch news at preparation time; the `news_items` cache stays as it is.
3. **Ticker tags are unreliable.** Real Alpaca items for 2026-11-04/05 included:
   - other exchanges' companies under our tickers (TKO → Taseko Mines, Tikehau Capital, Trekor Metals; FTK → flatexDEGIRO);
   - market roundups;
   - analyst rating notes.
4. **Alpaca news licensing is NOT verified.**
   - Alpaca's news is Benzinga content.
   - Its Terms & Conditions PDF could not be read here, and no primary source confirming public display rights was found.
   - **Public posts carry no news until the owner verifies the terms** (owner previews may).
5. **CI never ran `mechanism/inflection/tests`.** This includes the 2026-11-03 late-dating regression tests. This branch adds the folder to the mechanism CI job.
6. **The frontend has no "open" route category.**
   - `AuthGate` knows only signed-in pages and `/login`, and it redirects signed-in users *away* from public paths.
   - Public `/signals` pages need a third category (see Phase 2).

## Phase 1: what is built (previews only)
| Module | Role |
|---|---|
| `mechanism/inflection/update_model.py` | `build_update()` → `PublicUpdate`: allowlisted fields only (`PUBLIC_FIELDS`), eligibility, 2–3 figures, the deterministic "why it matters", links, natural key, content hash, `classify_revision()` |
| `mechanism/inflection/update_news.py` | `select_news()`: point-in-time cutoff, window, company-name match, roundup, rating and advice filters, de-duplication, ranking; at most 2 |
| `mechanism/inflection/update_format.py` | Telegram v2 caption (≤1,024 characters after parsing, fixed drop order), inline buttons (SEC and Finviz only; no First Light link in the post, owner decision 2026-11-07), text fallback |
| `mechanism/inflection/update_card.py` | 1200×675 PNG card; its own identity (teal-graphite surface, one amber accent), contrast-checked |
| `mechanism/inflection/update_preview.py` | CLI: fixture JSON → `card.png`, `caption.html/txt`, `buttons.json`, `update.json`, `v1.txt`, `index.html`. No database, no network, no send. |
| `mechanism/inflection/tests/test_fundamental_updates.py` | 49 tests: allowlist and no-internals, advice guard (pinned to the channel test's `BANNED`), snapshots, point-in-time, news matching on the real collision headlines, Telegram limits and escaping, card, corrections, backward compatibility, the preview CLI with sockets blocked |

**Publishability rules:**
- **Type:** five public types. `improvement_with_limited_repricing` is excluded: it is a price judgement on an internal score.
- **Date source:** the public date must come from an SEC filing or the company's report date. Assumed lags, first-seen dates and repricing windows are excluded.
- **Freshness:** the event must be first detected on or after its public date (the BILL/ETON shape is rejected).
- **Content:** at least two verified figures, and the explanation's own inputs present.

**Point-in-time rules:**
- Figures are the event's reported metrics.
- Market cap is used only from `valuation_at_publication`, and only when its snapshot date is on or before the public date. Sector and industry come from the last fundamentals row on or before the public date.
- Price reactions, "since publication" returns and latest valuations are never read.
- News must have `published_at`, `updated_at` and `fetched_at` all at or before the cutoff. It must also fall within [public date − 1 day, public date + 3 days].
- The chosen news is frozen with the publication and is never re-selected later.

## Phase 2: public `/signals` architecture (design; see "Phase 2: as built" below)

### Source of truth: a publication ledger, never live event rows
```
fundamental_update_publication   (new migration, Phase 3; additive, IF NOT EXISTS)
  id               BIGSERIAL PK
  update_key       TEXT        -- natural key SYMBOL/quarter_end/slug
  version          INT         -- 1 = first publication, +1 per correction
  status           TEXT        -- published | corrected | withdrawn
  public_json      JSONB       -- PublicUpdate.to_public_dict() exactly as published (figures, news, links frozen)
  content_hash     CHAR(64)
  event_id         BIGINT      -- internal; never returned by the public API
  method_version   TEXT        -- internal; never returned
  published_at     TIMESTAMPTZ
  telegram_refs    JSONB       -- {channel: message_id}; internal
  correction       JSONB       -- {kind, changed[], at} for version > 1
  UNIQUE (update_key, version)
```
- The public API serves **only** `public_json` from this ledger. An event that was never published is not public: 404, even if it exists.
- Because the stored JSON is the allowlisted dict, a later column added to the event table can never leak.
- Figures stay as published. Revisions appear only as explicit corrections.

### Public API (new router, unauthenticated, read-only)
| Endpoint | Returns |
|---|---|
| `GET /public/signals?limit=20&cursor=…&type=…&sector=…` | `{items: [public_json summary], next_cursor}` |
| `GET /public/signals/{symbol}/{quarter_end}/{slug}` | `{update: public_json, status, published_at, corrections: [{version, kind, changed, at}]}` |
| `GET /public/signals/{symbol}/{quarter_end}/{slug}/card.png` | the card rendered from the stored `public_json`, cached; also used as the page's OG image |

- **Field allowlist:**
  - Pydantic response models are generated from `PUBLIC_FIELDS` with `extra="forbid"`.
  - A test asserts that the OpenAPI schema keys equal `PUBLIC_FIELDS` plus `status`, `published_at` and `corrections`.
  - A second test runs the responses through the no-internals check (no strength, score, severity, method or price fields).
- **Rate limiting:**
  - an in-app token bucket per client IP, read from Caddy's `X-Forwarded-For` (only trusted from Caddy's address): 60 requests per minute for the feed, 120 for detail pages, 30 for `card.png`;
  - a `429` with `Retry-After`;
  - `Cache-Control: public, max-age=300`;
  - an `ETag` equal to `content_hash`, so repeat visits cost nothing.
- **Pagination:**
  - an opaque keyset cursor (base64 of `published_at`, `update_key`), newest first;
  - `limit` from 1 to 50;
  - no offsets, so cost is constant at any depth.
- **Input validation:**
  - `symbol` must match `^[A-Z][A-Z0-9.\-]{0,9}$`;
  - `quarter_end` must be an ISO date between 2000-01-01 and today;
  - `slug` must be in `SLUG_TO_TYPE`;
  - filter values must come from closed lists;
  - a cursor that fails to decode is a 422;
  - errors never echo the input.
- **Isolation:** a separate router module with no `require_authenticated_user` and no imports of the authenticated services. The database read uses a single parameterised SELECT on the ledger.

### Stable natural-key URLs
- The address is `/signals/{SYMBOL}/{quarter_end}/{slug}`, for example `/signals/POWI/2026-09-30/margin-expansion`.
- It is independent of database IDs, which have large gaps from upserts, and of method versions.
- Corrections keep the address: the page shows the latest version and its correction history.
- A withdrawn update keeps its page, with a "withdrawn" banner and the reason category.

### Frontend
| Route | Content | Reuse |
|---|---|---|
| `/signals` | feed grouped by public date; type and sector filters; "Load more" via cursor | `EventCard` layout as a public variant, fed by `public_json` |
| `/signals/[symbol]/[quarter]/[type]` | card image, explanation, figures, news with times, date source, SEC/Finviz links, correction history, a "what a Fundamental Update is" note | `InflectionPanel` layout pieces; `TrendCharts` only if its data is added to the allowlist later |

- `AuthGate` gains an **open path** category (`/signals` and `/signals/*`): rendered for everyone, with the app chrome when signed in and a minimal public header when not. No redirect either way.
- Signed-in users keep `/inflection` for research.
- The detail page is server-rendered, so `generateMetadata` sets the title and description and uses `card.png` as the OG image.

## Correction workflow (already-published updates later revised)
A daily job compares every published update (last 120 days) with the event's current state. It rebuilds the update with `build_update(..., check_freshness=False)` and calls `classify_revision(stored_public_json, current)`.

| Result | Action |
|---|---|
| `unchanged` | Nothing. A revision below display precision is not a correction. |
| `restated` (a figure, the label or the explanation changed) | 1. Write a new ledger version: the old row becomes `corrected`, and the new one carries `correction.changed`.<br>2. Edit the original Telegram caption, appending one line: "Corrected Nov 9: operating margin 12.0% (was 15.2%)".<br>3. Post **one** reply to the original post, claimed once through `post_delivery` with key `fu_correction:{update_key}:v{n}`.<br>4. The web page shows the history. |
| `redated` (the public date or its source changed, same figures) | Ledger version only, and the page shows the history. Telegram: the caption edit only, no new post. |
| `withdrawn` (the event is gone or no longer publishable) | Ledger status `withdrawn`. Edit the caption to a "Withdrawn" line and post one reply, claimed once. The page keeps a "withdrawn" banner. **Nothing is deleted.** |

Guards:
- **Once per change:** at most one correction post per update per day. The claim key includes the new version, so a re-run cannot post twice.
- **Not a new update:** a correction is never a fresh "Fundamental Update" post.
- **Same gates as publishing:** owner chat → dev channel → public; `PROD_SENDING_ENABLED`; an off switch `FU_CORRECTIONS_ENABLED`.
- **Kept apart from the 16 old alerts:** the stale-event cleanup stays its own owner-approved step. Those alerts went only to the owner chat and are not in this ledger.

## Rollout gates (in order; each needs the previous one)
1. Phase 1 previews reviewed by the owner (this report).
2. Owner decisions:
   - the source version (`v2_sec_pit` recommended);
   - the Alpaca news terms;
   - the Phase 2 architecture.
3. Phase 2 build. Code and tests only; the ledger migration is reviewed before it is applied.
4. Owner chat on v2, behind `INFLECTION_FORMAT=v2`. The current text stays the default until it is switched.
5. Dev channel, claimed through `post_delivery`, at most 3 per night.
6. Public channel, after **two clean weeks on `1fc1753cdae0` and explicit owner approval**. Gated by `PROD_SENDING_ENABLED`.

## Phase 2 test plan
- **API:**
  - the response schema equals the allowlist;
  - the no-internals check passes on real ledger rows;
  - a never-published key returns 404;
  - malformed symbol, date, slug or cursor return 422, without echoing the input;
  - pagination is stable under inserts;
  - `ETag` and 304 work;
  - the rate limit returns 429 with `Retry-After`;
  - the router imports nothing authenticated.
- **Ledger:**
  - versions are append-only;
  - `UNIQUE (update_key, version)` holds;
  - the correction path writes exactly one new version per change;
  - the public JSON round-trips byte for byte.
- **Frontend:**
  - open paths render signed in and signed out without a redirect;
  - an unknown key shows not-found;
  - the address is encoded and decoded correctly (including `BRK.B`);
  - the layout works at phone width;
  - a withdrawn banner appears for withdrawn updates.
- **End to end with no sending:** fixture events → ledger (test database) → API → page. CI uses a fake Telegram client, and any socket fails the test.

## Phase 2: as built (2026-11-07, branch only)
Owner decisions applied:
- The source of public updates will be `inflection_v2_sec_pit`, but nothing is switched yet; the evidence is in [FUNDAMENTAL_UPDATES_SOURCE_COMPARISON.md](FUNDAMENTAL_UPDATES_SOURCE_COMPARISON.md).
- Public news stays off.
- Telegram posts carry SEC and Finviz links only.

| Piece | Where |
|---|---|
| Publication record | `mechanism/add_fundamental_update_publication.sql` (migration 41):<br>• append-only table, one row per version (initial / restated / redated / withdrawn);<br>• `UNIQUE (update_key, version)`;<br>• key and hash checks;<br>• UPDATE, DELETE and TRUNCATE refused by `ENABLE ALWAYS` triggers;<br>• the `fundamental_update_current` view;<br>• SELECT + INSERT for `donchian_app`.<br>Mounted as init 41 (CI applies it); **not applied to production**. |
| Writer | `mechanism/inflection/update_ledger.py`:<br>• `publish()` is idempotent (`exists` on repeat);<br>• `revise()` writes one version per real change, keeps the first publication's news, never revives a withdrawn update, and lets a racing loser write nothing.<br>No scheduled caller. |
| Eligibility | `update_model.py` adds a cap of at most 104 days from quarter end to public date. It drops late filers and quarters first seen in a later registrant's filings. |
| Public API | `backend/routers/public_signals.py` + `backend/services/public_signals_service.py`; `GET /api/public/signals` and `GET /api/public/signals/{symbol}/{quarter_end}/{slug}`:<br>• no authentication;<br>• reads only the publication record;<br>• response models are exactly `PUBLIC_FIELDS` with `extra="forbid"` (unexpected nested data fails closed with a 503);<br>• keyset cursor; closed-list filters; errors that never echo the input;<br>• token bucket per client IP (60 per minute on the feed, 120 on detail pages) with `Retry-After`;<br>• `X-Forwarded-For` trusted only from the proxy network;<br>• `Cache-Control: public, max-age=300`;<br>• `ETag`/304 on detail pages;<br>• 503 until migration 41 exists. |
| Frontend | `/signals` (feed: type chips, sector, "show older") and `/signals/[symbol]/[quarter]/[type]` (detail: figures, date source, news when present, SEC and Finviz, correction history, withdrawn banner).<br>`AuthGate` gains open paths (`lib/signals.ts`): public header when signed out, app frame when signed in, no redirect.<br>"Fundamental Updates" is added to the Product navigation. |
| Tests | `test_update_ledger.py` and `test_public_signals_db.py` run on real PostgreSQL in the CI database job. `test_public_signals_api.py` covers the allowlist, no internals, validation, cursor, 304, 429, client IP and no authentication. Frontend: `signals.test.ts`, `UpdateView.test.tsx`, `AuthGate.test.tsx`. |

**Deferred from the design (each is a later decision):**
- **Card image endpoint:** `card.png`, also usable as the OG image. The backend image would need Pillow.
- **Server-rendered metadata:** the pages render client-side, like the rest of the dashboard.
- **Per-worker rate limit:** the bucket lives in memory per worker, so with N workers the effective limit is N times the stated one. A shared limit would need Redis or Caddy, which is new infrastructure.
- **Correction job and Telegram correction posts:** they belong to the sending phases, which need separate approval.
