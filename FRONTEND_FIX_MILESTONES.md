# FRONTEND_FIX_MILESTONES.md — App Fix Plan (frontend + the data it shows)

> **Scope note (added 2026-09-25):** kept as a live tracker, not moved to history — but its items
> were last verified before the Vercel deployment and the VPS migration; re-verify a given item
> against current reality before assuming its status line still holds. For current frontend
> architecture see [docs/architecture/SYSTEM_OVERVIEW.md](docs/architecture/SYSTEM_OVERVIEW.md).

> **Living tracker.** Created 2026-09-19 from the frontend audit (tsc + `next lint` +
> headless-Chrome renders of `/`, `/screener`, `/strategy`, `/alerts` + full code read).
> We work through this file top to bottom until every item is closed. Update the status
> of an item **in the same session you touch it**; never mark ✅ without the evidence
> listed in its acceptance criteria. Broader project roadmap: [MILESTONES.md](docs/history/MILESTONES.md).
> Architecture/mechanism detail: [CLAUDE.md](CLAUDE.md).

Status legend: ✅ done · 🔄 in progress · ⬜ not started · ⚠️ blocked / needs a decision · ❓ hypothesis, not yet proven

Severity: **C** critical (blocks build/deploy) · **H** high · **M** medium · **L** low

---

## 0. Ground rules (binding for every milestone)

These are the user's rules, expanded into things we can actually check.

**R1 — No values swapped in just to make something pass.**
Not allowed, ever, as a way to get a green build or an empty console:
- `as any`, `// @ts-ignore`, `// eslint-disable`, `ignoreBuildErrors`, `ignoreDuringBuilds`.
  A lint/type error means a type or a contract is wrong — fix the type or the contract.
- Fabricated defaults that make missing data look real: `?? 0`, `|| 0`, `|| 1.0`,
  `COALESCE(x, 1.0)`, `positionPct() → 50`, an empty bar drawn as a 0-length red bar,
  "sideways" when the trend is simply unknown. **Missing data is `null`, and the UI shows
  "—" / "N/A".** A default is only legitimate when it is a real, documented domain value.
- Loosening a threshold, widening a type to `unknown`, or deleting a test/feature to make
  the error go away.

**R2 — Root cause first, at the layer that owns it.**
Every item has a *Root cause* line. Before writing a fix, write down the cause **with
evidence** (query output, code line, repro). Ownership order: data pipeline → backend →
API contract → frontend. A frontend patch over a backend/data defect **does not close the
item** — either the source is fixed, or the owner-layer defect is split into its own item
and this one stays open/⚠️. Items marked ❓ start with an *investigation* step whose output
is the root cause; we do not skip to a fix.

**R3 — Use the relevant skills and design deliberately.**
- `dataviz` — **must be loaded before touching any chart** (treemap, scatter, sparkline,
  sector trend, candlestick, tooltips, legends, color scales).
- `run` — to launch the real app and confirm a change actually works in it.
- `code-review` and `simplify` — at the end of each milestone, before it is marked ✅.
- `security-review` — for anything touching CORS, env/config, rewrites, deploy settings.
- Architecture/system design (no dedicated skill is installed — this is convention):
  contract-first (typed, nullable-honest API types are the source of truth), one owner per
  metric definition, one shared formatter module, one data-fetch pattern, no duplicated
  business constants between pages (see 3.8).

**R4 — Financial accuracy is a feature, not a polish item.**
Written from the seat of a professional trader:
- Every number on screen must trace to a definition and a query. Definitions (relative
  volume, "unusual", "active signal", "position size", "% change") are written down next to
  the code that computes them and named the same in the UI.
- Show the **as-of date** of every dataset. Never label something "Today" if it is the last
  session's close. Stale data is shown as stale.
- **Missing ≠ zero ≠ normal.** Units are explicit (percent vs fraction; 0–100 vs 0–1).
- Shorts, gaps, and illiquid tickers are real trading concerns — don't present an
  illiquid $4 name +32% on 1.6M shares as an equal of a liquid mover without saying so.
- When a data change is made, spot-check ≥ 5–10 symbols against an independent second
  source and record the comparison in the item's evidence.

**R5 — Verification is evidence, not a feeling.**
An item is done only when its acceptance criteria are demonstrated: commands run, real
renders inspected, before/after numbers. Report honestly what was **not** verified.
"tsc passes" alone never closes a UI item.

**R6 — Process.**
- One milestone at a time, in order, unless a dependency says otherwise.
- New problems found mid-fix get a new item ID appended in §11 — they are not silently
  fixed inside another item (no scope creep, no hidden changes).
- Do not commit unless the user asks. Update CLAUDE.md (architecture + changelog) when a
  milestone changes how the system works.

### Definition of Done (copy into each item's PR/notes)
- [ ] Root cause written down with evidence (R2)
- [ ] Fix is at the owning layer; no R1 violations
- [ ] `npx tsc --noEmit` clean, `next lint` clean (no disables), `npm run build` passes (from FM0.2 onward)
- [ ] Rendered in the real app (loading / empty / error / data states as applicable) and inspected
- [ ] Numbers touched were cross-checked against an independent source (R4), if data-related
- [ ] Docs updated (this file, CLAUDE.md where architecture changed)

---

## 1. Status board

| Milestone | Theme | Items | Status |
|---|---|---|---|
| FM0 | Verification harness & dev environment | 3 | ✅ |
| FM1 | Build health (Critical) | 5 | ✅ |
| FM2 | UI foundation: NextUI/Tailwind + broken layouts (High) | 3 | ⬜ |
| FM3 | **Data accuracy & trust** (High/Medium) | 10 | ⬜ |
| FM4 | Robustness, state & contracts | 7 | ⬜ |
| FM5 | Charts & visualization (`dataviz`) | 5 | ⬜ |
| FM6 | Responsive & accessibility | 4 | ⬜ |
| FM7 | Cleanup & consistency | 6 | ⬜ |
| FM8 | Final verification & regression net | 4 | ⬜ |

Recommended order: **FM0 → FM1 → FM2 → FM3 → FM4 → FM5 → FM6 → FM7 → FM8.**
FM3 is the milestone that matters most for a trading tool; FM1/FM2 come first only because
they unblock building and *seeing* the app correctly, which FM3 verification depends on.

---

## 2. Decisions needed from the user

| # | Decision | Recommendation | Blocks |
|---|---|---|---|
| D1 | UI kit path: (A) wire the NextUI theme plugin into Tailwind v4, or (B) migrate to HeroUI (NextUI's renamed successor, documents Tailwind v4 support) | Verify HeroUI/Tailwind-v4 docs first; (B) if the migration is mechanical, else (A) as a bridge | FM2.1 |
| D2 | Liquidity floor for Top Gainers/Losers/Unusual Volume (min price, min avg daily dollar volume) | Explicit, visible, user-adjustable filter with a sane default — never a silent drop | FM3.7 |
| D3 | Adopt a data-fetching library (TanStack Query / SWR) vs a small in-repo hook | Decide after FM4.3 evaluation; avoid a dependency if a ~40-line hook covers abort/dedupe/stale | FM4.3 |
| D4 | Alpha Finder default: show both directions, or long-only default with a Long/Short toggle | Direction toggle, default = both, with an explicit direction column | FM3.3 |
| D5 | Relative-volume definition: N-day window for the baseline | Today ÷ average of the *prior* 20 sessions (excluding today); optionally show 50-day | FM3.2 |

---

## FM0 — Verification harness & dev environment

*Why first:* our audit hit two environment problems that would make later verification
unreliable, and there is no way to check a change in a real browser reproducibly.

- [x] **FM0.1 (M) Dev server is unhealthy — diagnose before trusting any render.** ✅ 2026-09-19
  *Evidence:* the `:3001` dev server returns 500 for every `/stock/*` route with
  `write EPIPE` / "Jest worker encountered 2 child process exceptions"; `:3000` is a stale
  server returning 404 for `/stock/AAPL` (same failure class as the 2026-09-19 corrupted
  webpack cache in CLAUDE.md §3.3).
  *Root cause (confirmed):* `frontend/_next_out.log` was full of `write EPIPE` — the dev
  server had been started with its stdout piped to a reader that later went away, so any
  worker write to stdout threw. **Correction to the original evidence:** `:3000` was *not* a
  stale server of this project — it is an unrelated app ("Ollama Chat", `node server.js`
  from `C:\Users\Daniel\Desktop\agent`). It was left running and untouched. Only the `:3001`
  Next dev server belongs to this project.
  *Fix:* stopped the `:3001` process tree, deleted `frontend/.next` and the stale
  `_next_out.log`, restarted `npm run dev -- -p 3001` with output going to a real file
  (harness background task), not a closable pipe. Document the exact start procedure in
  CLAUDE.md §8 (done, FM7.6 will re-check).
  *Accept (met):* `/`, `/screener`, `/strategy`, `/alerts`, `/stock/AAPL`, `/stock/BRK.B`,
  `/system-health`, `/ml-stats`, `/performance` all 200 **and** screenshots inspected for
  `/`, `/stock/AAPL`, `/stock/BRK.B`, `/system-health`, `/ml-stats`, `/performance` — all
  render real data; one Next dev server for this project; no EPIPE in the new log.

- [x] **FM0.2 (M) Let verification builds run without corrupting the dev cache.** ✅ 2026-09-19
  *Root cause:* `next build` writes to the same `.next` the dev server uses; that is how the
  corrupted-cache incident happened. Building a copy in a scratch dir also fails on this
  machine (non-ASCII repo path breaks webpack resolution through junctions).
  *Fix:* `distDir: process.env.NEXT_DIST_DIR || '.next'` in `next.config.ts`, add
  `.next-build/` to `.gitignore`; verification runs `NEXT_DIST_DIR=.next-build npm run build`.
  *Accept (met):* `NEXT_DIST_DIR=.next-build npm run build` ran while `next dev` was up;
  output went to `.next-build/`; `.next` file count 32 before/after; dev routes still 200.
  The scratch-copy failure was path-related, **not** an in-place problem: webpack compiles
  fine in place on the non-ASCII repo path ("✓ Compiled successfully in 28.0s"), so FM1.5
  can build in place. Side effect: Next auto-appended `.next-build/types/**/*.ts` to
  `frontend/tsconfig.json` `include` (idempotent, its own behaviour) — kept.
  *Changed:* `frontend/next.config.ts` (`distDir`), `.gitignore` (`.next-build/`).

- [x] **FM0.3 (M) Reproducible visual check.** ✅ 2026-09-19
  *Fix:* [frontend/scripts/screenshots.sh](frontend/scripts/screenshots.sh) —
  `bash frontend/scripts/screenshots.sh OUT_DIR [route ...]` (defaults: `/ /screener /strategy
  /alerts /stock/AAPL`; env `WIDTHS`, `BASE_URL`, `BUDGET_MS`, `CHROME`). Headless Chrome with
  a throwaway profile, `localhost` origin, virtual-time budget so data fetches settle.
  *Accept (met for `/` and `/stock/AAPL` at 1440/820/390 — 6 PNGs, inspected; the other
  routes at 1440 only).* The full 5-route × 3-width sweep is FM8.1's job.

---

## FM1 — Build health (Critical)

*Goal:* `npm run build` (with lint **enabled**) exits 0 — and does so because the code is
correct, not because checks were relaxed.

- [x] **FM1.1 (C) 23 ESLint errors — fix the types, not the linter.** ✅ 2026-09-19
  *Outcome:* the dashboard's `MainPageData` / `StockItem` / `AIPickItem` types **already existed**
  in `api.ts` — `page.tsx` bypassed them with `useState<any>` — so the fix there was to use the
  contract, not invent one. New in `api.ts`: `ScreenerFacets`, `Partial<ScreenerFilterRequest>`
  for `filters_applied`, `SectorBreakdownResponse`, `TechnicalDistributionResponse`, `isHttpStatus()`,
  and `StockDetail` as a **discriminated union** on `data_source` (`ActiveSignalSnapshot` |
  `NoSignalSnapshot`) with honest `Maybe<T>` fields, shapes taken from live payloads (AAPL, MMM,
  BRK.B), not guessed. Recharts callbacks are typed by contextual typing / a `ScatterClickPoint`
  (recharts 3.2 passes `{...datum, cx, cy, payload: datum}`, confirmed in its source). Two real
  defects fell out of honest typing and were fixed at the source: the Tooltip `Number(v)` turned a
  null quarter into `$0.00`; `weekly.price_position_weekly > 60 … : 'sideways'` (a null compare) now
  yields `null` trend when the position is unknown. Escaped entities ×6.
  *Evidence:* `next lint` → "No ESLint warnings or errors"; `grep -rnE
  "eslint-disable|as any|@ts-ignore|@ts-expect-error|: any\b|<any>|ignore(Build|During)"` → none.
  *Original item text follows:*
  *Evidence:* `next lint`: `no-explicit-any` ×17 ([page.tsx](frontend/src/app/page.tsx)
  ×8 at 98/447/603/611/619/721/778/841, [api.ts](frontend/src/services/api.ts) ×5,
  [screener/page.tsx](frontend/src/app/screener/page.tsx) ×2,
  [stock/[symbol]/page.tsx](frontend/src/app/stock/[symbol]/page.tsx) ×2,
  [DevQAPanel.tsx:27](frontend/src/components/dev/DevQAPanel.tsx#L27)), plus
  `no-unescaped-entities` ×6 (alerts, performance, DevQAPanel).
  *Root cause:* the dashboard's `main-page-data` payload has no type at all
  (`useState<any>`), so gainers/losers/unusual-volume rows are `any`; API error handling
  is untyped; chart callbacks are untyped.
  *Fix:* define `MainPageData` (+ row types, with honest nullability — see FM4.2) in
  `api.ts` and use it; use `axios.isAxiosError`; use recharts' exported prop types for
  Treemap `content` / Scatter `onClick` / Tooltip `formatter`; escape entities properly
  (`&apos;`/`&quot;`). *No* `eslint-disable`.
  *Accept:* `next lint` reports 0 errors; `grep -rn "eslint-disable\|as any\|@ts-ignore" src`
  finds nothing new (the two pre-existing `eslint-disable react-hooks/exhaustive-deps` lines
  are removed under FM1.4).

- [x] **FM1.2 (C) `useSearchParams()` without a Suspense boundary in `/screener`.** ✅ 2026-09-19
  *Outcome:* **confirmed by execution first** (was "not executed" in the audit): with every lint
  error already fixed, `next build` failed with `useSearchParams() should be wrapped in a suspense
  boundary at page "/screener"` / `Error occurred prerendering page "/screener"`. Fixed by
  splitting into a default-export `Suspense` shell + `ScreenerPageContent`. *Not done here:* making
  the params reactive (still read once) — that stays in FM4.5.
  *Evidence:* build passes; `/screener?filter=gainers` rendered in headless Chrome still applies the
  preset (results sorted by change desc, top row USDE +32.17%).
  *Original item text follows:*
  *Evidence:* [screener/page.tsx:77](frontend/src/app/screener/page.tsx#L77); no `Suspense`
  anywhere in `src/`. Next 15 fails the production build for this (documented framework
  behaviour — **not executed** during the audit; confirm here).
  *Fix:* split the page into a server-safe shell + a client child wrapped in `<Suspense
  fallback=…>`; the child reads the params. (Also resolves part of FM4.5: params should be
  reactive, not read once.)
  *Accept:* production build passes; `/screener?filter=gainers` still applies the preset.

- [x] **FM1.3 (C) `next.config.ts` would break or mislead a deploy.** ✅ 2026-09-19
  *Outcome:* chose **no rewrites** — grep confirmed nothing uses a same-origin `/api` path (all
  calls go through `API_BASE_URL`) and nothing uses `next/image`, so the rewrite and
  `images.domains` were dead config; both removed. `next.config.ts` is now a proper ESM function
  config that **throws in `PHASE_PRODUCTION_BUILD` when `NEXT_PUBLIC_API_BASE_URL` is unset**;
  `api.ts` `resolveApiBaseUrl()` keeps the `127.0.0.1:8000` default for `NODE_ENV=development`
  only and throws otherwise.
  *Evidence (all three acceptance cases run):* (1) build with the var set → exit 0; (2) build with
  `NEXT_PUBLIC_API_BASE_URL=` → exit 1, "NEXT_PUBLIC_API_BASE_URL is not set. Production builds need
  the public URL of the FastAPI backend…"; (3) dev server restarted with `.env.local` moved away
  (startup log no longer lists `Environments: .env.local`) → `/` and `/screener?filter=gainers`
  rendered live data; `.env.local` restored afterwards.
  *Security review:* the `security-review` skill diffs tracked changes and `frontend/` is untracked,
  so it could not see these files; reviewed by hand instead. Surface change is a reduction (an
  env-driven proxy rule removed, no new endpoints/headers/secrets; `NEXT_PUBLIC_*` is public by
  design). **Not covered:** CORS on the backend — untouched, still FM4.7. Residual note:
  `compiler.removeConsole` is still on in production and strips `console.error` too, so production
  API failures log nothing client-side (not in scope; raise with FM4.1 if error reporting matters).
  *Original item text follows:*
  *Evidence:* `rewrites()` destination is `${process.env.NEXT_PUBLIC_API_BASE_URL}/api/...`
  → `undefined/api/...` if the var is unset, which is a build error; and nothing uses the
  rewrite (the app calls `API_BASE_URL` directly). `images.domains` is deprecated.
  `api.ts:13` silently falls back to `127.0.0.1:8000`, which in a production build would
  quietly point every user's browser at *their own* localhost.
  *Fix:* decide one path — either a real same-origin proxy via rewrites (and the client
  uses relative `/api`), or no rewrites. Remove `images.domains`. In production, **require**
  `NEXT_PUBLIC_API_BASE_URL` (fail the build/boot loudly); keep the localhost default for
  `NODE_ENV=development` only. Run `security-review` on the result.
  *Accept:* build passes with the var set; build/boot fails with a clear message when it is
  unset in production; dev still works with no `.env.local`.

- [x] **FM1.4 (M) 6 lint warnings are real defects.** ✅ 2026-09-19
  *Outcome:* dashboard fetchers are `useCallback`s (hook-provided `start`/`done`/`markLoaded` are
  stable, so no refetch loop) and the mount effect declares them; stock page reads the selected
  range through a ref, so Retry always uses the range chosen *now* (it used the one captured when
  `symbol` last changed) and a range change doesn't re-run the loader; screener's mount search reads
  the initial filters from a ref; dead `router`, `lastDate`, `confidenceChipColor` deleted
  (grep-confirmed unused; `uiColors.confidenceColor` is what everything else uses); Sector legend is
  now `<ul><li><button aria-pressed>` (valid ARIA). **Found and fixed a third `eslint-disable` the
  audit missed** — `LightweightCandlestickChart.tsx:147`: the mount-only effect read `isMini` and
  `resolvedHeight` and its comment promised variant changes were handled but only height was;
  chart options now come from one `variantOptions(isMini)` used at creation and on change.
  *Evidence:* lint 0/0, grep for `eslint-disable` empty; MMM (no-signal branch) and AAPL stock pages
  and the dashboard rendered after the change; chart draws identically. *Not verified:* the
  Retry-uses-current-range behaviour and the chart's variant switch at runtime (no clicking in the
  headless harness; neither is reachable from the UI today).
  *Original item text follows:*
  *Evidence/Root cause:* `useEffect` missing `fetchData` ([page.tsx:224]) and stale
  `rangeDays` in `useCallback` ([stock page:238]) — genuine stale-closure risks, currently
  hidden by an `eslint-disable`; unused `router` (screener), `lastDate`
  (SectorTrendChart), `confidenceChipColor` (strategy — duplicate of `uiColors`);
  `aria-pressed` on `role="listitem"` (SectorTrendChart — invalid ARIA).
  *Fix:* make handlers stable with `useCallback` and correct deps; delete dead code; fix the
  ARIA role (use `role="list"` container of real `<li><button aria-pressed>` items).
  *Accept:* `next lint` 0 errors, 0 warnings; no `eslint-disable` left in `src/`.

- [x] **FM1.5 (C) Build gate.** ✅ 2026-09-19
  *Accept (met):* `NEXT_DIST_DIR=.next-build npm run build` exits 0 **with lint on** (no
  `ignoreDuringBuilds`/`ignoreBuildErrors`); output in the Progress Log (§10).

---

## FM2 — UI foundation (High)

- [ ] **FM2.1 (H) NextUI is running without its theme plugin — components are half-styled.**
  *Evidence (rendered):* screener Inputs/Selects have no visible field boundary; the
  "Sort By" label overprints its value ("Brice Bhange"); `bordered` preset buttons render
  as plain text; the existing `bg-content1` popover workaround in
  [SymbolHoverLink.tsx](frontend/src/components/dashboard/SymbolHoverLink.tsx) documents
  the same missing tokens. `globals.css` is only `@import "tailwindcss"`; there is no
  Tailwind/hero config; Tailwind v4 does not scan `node_modules` for NextUI's classes.
  *Root cause:* NextUI 2.x needs its Tailwind plugin (theme tokens: `bg-content1`,
  `text-default-*`, `rounded-medium`, spacing units) and content sources; the project was
  migrated to Tailwind v4 without providing either. Scattered `className="bg-slate-700
  text-white"` overrides are symptoms of working around it.
  *Fix:* per decision **D1**. After the kit is correct, remove the workaround classes that
  only existed to compensate (e.g. the manual popover `bg-white`), keeping only real brand
  choices.
  *Accept:* each of Button (solid/bordered/light/ghost), Input, Select (open dropdown,
  multi-select), Chip, Table, Card, Tabs/Progress, Popover, Tooltip, Spinner renders with
  correct theme tokens — **including open dropdown/tooltip/popover states**, which the audit
  could not exercise. No overlapping label/value text anywhere.

- [ ] **FM2.2 (H) Strategy & Alerts control bars collapse into a right-aligned column.**
  *Evidence (rendered):* [strategy/page.tsx:93](frontend/src/app/strategy/page.tsx#L93),
  [alerts/page.tsx:92](frontend/src/app/alerts/page.tsx#L92): direction buttons, "High
  Confidence Only", and the number inputs stack vertically at the far right with a large
  empty area at left; input values overlap their labels.
  *Root cause:* `className="flex flex-wrap …"` on NextUI `CardBody` loses to the
  component's own `flex-col` (both classes apply); it is not a spacing issue.
  *Fix:* put the controls in a plain wrapper `<div className="flex flex-wrap gap-4 items-end">`
  inside `CardBody`. Grep every other page for `flex-*` layout classes placed directly on
  NextUI `Card*`/`Table*` slots and fix the same way.
  *Accept:* controls sit in a wrapping row at 1440/820/390; screenshot compared.

- [ ] **FM2.3 (L) Card header alignment.** "View All" sits next to the title instead of the
  right edge (CardHeader's inner flex lacks `w-full`; see dashboard Top Gainers/Losers/
  Unusual Volume). Same root cause family as FM2.2. *Accept:* right-aligned, rendered.

---

## FM3 — Data accuracy & trust  *(most important milestone)*

*Principle:* a screen that looks polished but shows a wrong number is worse than one that is
ugly. Each item below is a number a trader would act on.

- [ ] **FM3.1 (H) "Active Signals" KPI is not a signal count.**
  *Evidence:* [page.tsx:327](frontend/src/app/page.tsx#L327) adds
  `top_gainers + top_losers + unusual_volume + top_ai_picks` **list lengths** → renders 20
  (= the number of rows on screen); becomes `NaN → 0` if any list is undefined. The backend
  already returns `market_summary.active_signals`, but [main.py:517](backend/main.py#L517)
  sets it to `len(top_ai_picks)` where `top_ai_picks` is fetched with `limit=5`
  (main.py:482) — so even the backend value is capped by a display limit, not a real count.
  *Root cause:* two layers: (backend) the metric is defined as "length of a truncated
  list"; (frontend) it ignores the backend field and invents its own sum.
  *Fix:* backend computes the true count from the screener output (per `signal_type`:
  bullish/bearish breakout, near-bullish/near-bearish) plus the screener run's as-of
  timestamp, from the same cached `ml_data` (`load_ml_enhanced_data_cached`). Frontend
  renders that value with the breakdown and "—" if unavailable. Document the definition.
  *Accept:* the card equals the count in `frontend_data/latest_multi_timeframe_ml_enhanced.json`
  (verified by script), and the breakdown sums to it.

- [ ] **FM3.2 (H ❓) Relative-volume numbers are implausible → "Unusual Volume" is noise.**
  *Evidence:* dashboard shows **avg volume ratio 2.01x** across the universe and
  **1,336 of 3,067 stocks (≈44%) "unusual"** (`volume_ratio ≥ 2.0`). On a normal session the
  universe average relative volume should sit near ~1.0 and unusual volume is a small
  minority.
  *Known facts:* `volume_ratio` = today ÷ **10-day SMA that includes today**
  ([daily_data_updater.py:239-252](mechanism/data_updaters/daily_data_updater.py#L239));
  standard RVOL uses the *prior* N sessions (typically 20–50) excluding today. That
  difference alone dampens the ratio, so it does **not** explain a 2.0 average.
  *Hypotheses to test (❓ — do not fix before one is proven):*
  (a) mixed volume units across providers in the same 10-day window (Alpaca free = IEX-only
  volume; Tiingo/yfinance = consolidated; `repopulate_from_tiingo.py` backfills) so the latest
  bar and the history disagree; (b) the latest bar is a partial/intraday or duplicated row;
  (c) split/adjustment artifacts; (d) a handful of extreme ratios skewing the **mean**
  (report the median and percentiles — a mean is the wrong summary here).
  *Investigation output required:* distribution (median, p90, p99) of `volume_ratio` for the
  latest date; per-provider source of the last 11 bars for 20 random symbols; 10 symbols
  compared against an independent volume source.
  *Fix (after root cause):* correct the data/units and/or definition per **D5**
  (prior-N-session RVOL); rerun indicators; then re-evaluate the `≥ 2.0` threshold against
  the corrected distribution. Show **median** RVOL in the UI, not a skew-prone mean.
  *Accept:* corrected universe median ≈ 1.0 and "unusual" is a plausible minority;
  spot-check table (≥ 10 symbols vs independent source) recorded in §10.

- [ ] **FM3.3 (H ❓) Alpha Finder ranks bearish, low-confidence signals at the top.**
  *Evidence (rendered):* top 25 are all `bearish breakout`, `ml_confidence` = `low` /
  `very low`, yet combined score 78–83 (#1 LBTYA −4.08%, score 83, "low").
  *Known facts:* `combined_score = 0.6 × alignment + 0.4 × ml_probability`
  ([multi_timeframe_screener.py:279](mechanism/screeners/multi_timeframe_screener.py#L279)),
  sorted descending ([alpha.py:141](backend/routers/alpha.py#L141)); the panel is titled
  "Alpha Finder — ranked by combined confidence + alignment + quality".
  *Root cause (❓):* alignment (60% weight) dominates; the ML model was trained on a "high
  momentum" label, so its probability may not be direction-aware for bearish setups —
  meaning "low ML confidence" on a short may not mean what a reader assumes. Must be
  determined from the labeler/feature code (`ml_training/data_preparation/momentum_labeler.py`)
  and the score distribution, not guessed.
  *Fix (after root cause):* direction-aware scoring/labeling in the screener/ML layer (not
  by hiding bearish rows in the UI); per **D4** an explicit direction column/toggle; the
  panel title/subtitle must describe what the score really is; show the "25 of N" cap.
  *Accept:* a written explanation of how score and confidence relate for long vs short;
  top of the list is consistent with its own confidence column or the discrepancy is
  explained on screen.

- [ ] **FM3.4 (H) Fabricated fallbacks that present missing data as real.** (R1)
  *Evidence:* backend `COALESCE(ti.volume_ratio, 1.0)` ([main.py:649,761](backend/main.py#L649),
  [market_service.py:313](backend/services/market_service.py#L313)) and `… else 1.0`
  (main.py:516, market_service:170) turn "no volume data" into "exactly normal volume";
  frontend: `positionPct()` returns **50** for missing inputs
  ([priceMath.ts:11](frontend/src/lib/priceMath.ts#L11)) so "Position in channel: 50%" is
  shown with no data; `ScoreBar` `value ?? 0` draws an empty red bar for missing scores;
  treemap `avg_performance ?? 0` colors an unknown sector as flat; weekly trend defaults to
  `'sideways'` when `price_position_weekly` is undefined; `Number(x) || 0` in threshold
  inputs; `daily_fundamentals` gaps rendered as `0`/`N/A%`.
  *Root cause:* defaults were chosen to keep the UI from crashing instead of modelling
  "unknown" in the types.
  *Fix:* audit every `|| n`, `?? n`, `COALESCE(...,n)`, `else n` in the backend endpoints
  and frontend that feed the UI; keep `null`; make formatters/components accept `null` and
  render "—"; `positionPct` returns `number | null`. Keep a default **only** where it is a
  real domain constant, with a comment saying why.
  *Accept:* grep list of every removed/kept default with justification; missing-data render
  test for each component (props with `null`) shows "—", never a plausible number.

- [ ] **FM3.5 (H) As-of dates and the "Today" label.**
  *Evidence:* "Market Overview — **Today**" is computed from the latest available bars
  (the render showed data dated 2026-09-18 on 2026-09-19); "Data Updated" is the only date
  on the page and it belongs to one dataset. The screener JSON, price bars, fundamentals,
  sector snapshot, and index strip each have different as-of times and none is shown.
  *Root cause:* no per-dataset as-of metadata in the API contract.
  *Fix:* every endpoint that feeds a panel returns its own `as_of` (trading date and, for
  the screener, run timestamp); each panel shows it. "Today" is replaced by the actual date.
  A stale banner appears when the latest bar is older than the most recent trading session —
  derived from a real exchange calendar (not hardcoded weekends).
  *Accept:* each panel displays its as-of; on a weekend/holiday the UI says "as of Fri
  2026-09-18 close", not "Today".

- [ ] **FM3.6 (H) "Unknown" sector is ~2/3 of the universe and dominates the heatmap.**
  *Evidence:* treemap: "Unknown" is by far the largest box; Alpha/Gainers/Screener tables are
  mostly "Unknown". CLAUDE.md §7.4 / 2026-09-19 entries: `daily_fundamentals` covered ~32% of
  symbols before the step was wired into the pipeline.
  *Root cause:* data — sector tags missing for symbols not yet covered by the fundamentals
  step. **Not a frontend defect.**
  *Fix:* complete the fundamentals backfill (full-universe pipeline run), verify
  `daily_fundamentals` coverage ≥ agreed % and `sector` non-null for covered names, then
  re-snapshot `sector_performance_daily`. Frontend: never present "Unknown" as if it were a
  sector — show it as "Unclassified (N)" separately, excluded from sector ranking, so it
  can't distort the picture while coverage is incomplete. Track coverage on `/system-health`.
  *Accept:* coverage numbers before/after; heatmap dominated by real sectors.

- [ ] **FM3.7 (M ⚠️ D2) Movers are dominated by illiquid micro-priced names.**
  *Evidence:* Top Gainers: USDE $10.19 +32%, GEMI, FWDI, SECZ, QMLS … (sub-$15, 1.6–21M
  shares — largely leveraged/inverse ETPs and thin names). A trader can't act on these the
  way the panel implies.
  *Root cause:* no liquidity/price floor or instrument-type filter in
  `get_top_gainers/losers` and the screener default.
  *Fix:* explicit, visible filters (min price, min average dollar volume, optionally
  exclude ETPs) with defaults chosen in **D2**, shown as chips ("Price ≥ $5 · Avg $ vol ≥
  $X — change"). Not a silent drop.
  *Accept:* panels state their floor; results are tradeable names by that definition.

- [ ] **FM3.8 (M) Position-size column carries no information; constants duplicated.**
  *Evidence (rendered):* every row = `10.0%`: `min(maxPos, accountRisk ÷ stopRisk)` with
  defaults 1% / 10% and 3–6% stops → 17–33% → the cap always binds. The stock page
  hard-codes the same 1%/10% ([stock page:521](frontend/src/app/stock/[symbol]/page.tsx#L521))
  independently of the Strategy page inputs.
  *Root cause:* display doesn't tell the trader when the cap binds or what risk they are
  actually taking (10% notional × 4% stop = 0.4% account risk, not 1%).
  *Fix:* show *effective account risk* and a "capped" marker; single source for the two
  defaults (shared constants or a backend-provided plan); verify stop/target math in
  `services/strategy_calc.py` (ATR basis, direction handling for shorts) and note that
  short sizing ignores borrow availability/cost. Confirm short stops/targets are mirrored
  correctly (the top-50 list is all SHORT, so this is the common path).
  *Accept:* sizes vary meaningfully or are visibly capped with effective risk shown;
  hand-check 3 long + 3 short plans against manual math.

- [ ] **FM3.9 (M) Units & formatting audit.**
  *Evidence/candidates:* `Div Yield {fmtNum(x)}%` prints `N/A%`; `win_rate` printed raw
  (`{track.win_rate}%`); `ml_momentum_probability` 0–100 vs any 0–1 fields; margins/ROE as
  percent vs fraction; `fmtBig` negative → `$-1.2B`; `stock.volume / 1e6` on null → `0.0M`.
  *Root cause:* no shared formatter module or documented units in the API types; every page
  re-implements `fmtPct/fmtNum/fmtBig/fmtMoney`.
  *Fix:* one `lib/format.ts` (null-safe, sign-correct, unit-explicit) used everywhere;
  API types annotate units (`/** percent, 0–100 */`); backend confirms each field's unit.
  *Accept:* one formatter set; a table of every displayed metric → unit → source.

- [ ] **FM3.10 (M) Market-strip semantics and cross-check.**
  *Evidence:* VIX −4.08% is colored **red** and 10Y yield +1.03% **green** by sign alone;
  for VIX/yields/DXY, "up = good" coloring misleads. Index closes/changes need a spot-check.
  *Fix:* neutral (or inverse-risk) coloring for VIX, yields, DXY, crude per `dataviz`;
  verify last-session close and % change for all 10 symbols against a second source and
  confirm the `^TNX` scaling (Yahoo quotes yield ×1 vs ×10 historically) is right.
  *Accept:* comparison table in §10; coloring rule documented in the component.

---

## FM4 — Robustness, state & contracts

- [ ] **FM4.1 (H) No error boundaries.**
  *Evidence:* no `error.tsx`, `global-error.tsx`, `not-found.tsx`, `loading.tsx`. Unguarded
  `.toFixed()` on possibly-null values across tables can white-screen the whole app
  (page.tsx 727/731/784/788/847/850/857/864; screener 360–367; strategy 207–230; alerts 177).
  *Fix:* route-level `error.tsx` (message, retry, error id), `global-error.tsx`,
  `not-found.tsx` (also for unknown symbols), `loading.tsx` skeletons. Boundaries are a
  **safety net** — FM4.2 is the actual fix for the crashes.
  *Accept:* throwing in a component shows the boundary with a working retry, nav stays.

- [ ] **FM4.2 (H) Make API types honest so the compiler prevents null crashes.**
  *Root cause:* types in `api.ts` declare fields non-nullable (`current_price: number`,
  `price_change_pct: number`, …) that the backend can return null; that is *why*
  `.toFixed()` is called unguarded and why `any` crept in.
  *Fix:* audit each response type against the real backend payload (sample every endpoint),
  mark nullable fields `number | null`, and let `tsc` flag every unsafe use; fix each use via
  the shared formatters (FM3.9) — no sprinkling of `?.`/`?? 0` (R1).
  *Accept:* `tsc` clean with the corrected nullable types; recorded list of fields changed.

- [ ] **FM4.3 (M) One request-lifecycle pattern; no stale/out-of-order results.**
  *Evidence:* screener `runSearch`/`applyPreset`, alerts `fetchScan`, strategy `fetchResults`,
  stock `changeRange` all race (last response wins, not last request); `changeRange` has no
  `catch` (button shows the new range while the chart keeps the old data — unhandled
  rejection); alerts refetches on **every keystroke** (no debounce; `setLoading(true)` swaps
  the results for a spinner; clearing a field snaps to 0 via `Number(x) || 0`; `min/max`
  attributes not enforced).
  *Fix:* AbortController/request-id per fetch; keep previous data visible while refetching;
  commit-based number inputs (blur/Enter/debounce) with real validation and clamping to the
  documented range; per **D3**, shared hook or library. Remove the redundant "Rescan" or
  make it the commit action.
  *Accept:* rapid filter changes always end showing the last request's data; a failed range
  change reverts the button and shows an error; alerts fire ≤ 1 request per commit.

- [ ] **FM4.4 (M) Dashboard refresh semantics.**
  *Evidence:* "Refresh" calls only `fetchData` ([page.tsx:293](frontend/src/app/page.tsx#L293)),
  not indices/overview/sector history, while "Last updated" implies the whole page; the 5-min
  interval keeps polling in hidden tabs; a failed background refresh of the main data shows
  no stale marker (only market overview has one).
  *Fix:* one `refreshAll`; per-panel as-of + stale chip (ties to FM3.5); pause polling on
  `visibilitychange`; resume with an immediate refresh.

- [ ] **FM4.5 (M) Screener correctness & UX.**
  *Evidence:* no sort-order control (quick links set `asc` for losers and it stays), no
  reset, Enter doesn't apply; a preset **merges** into the sidebar filters but the request
  sends only the preset *name* (sidebar can disagree with the results shown); preset slug
  derived by lower-casing the label; `?filter=` read once; no URL sync (can't share a
  screen).
  *Fix:* backend returns each preset's concrete filters; frontend applies them visibly and
  searches with exactly those; slug/id comes from the API, not string munging; sort order
  control; reset; Enter-to-apply; URL ⇄ filters sync; sortable column headers.

- [ ] **FM4.6 (M) Stock detail page (`/stock/[symbol]`).**
  *Precondition:* FM0.1 (page could not be rendered during the audit — reviewed from code
  only). *Then:* render and verify all cards; fix weekly-trend inference when data is
  missing (FM3.4), retry using stale `rangeDays`, `useCallback` deps (FM1.4), earnings
  marker legend, symbols with dots/dashes (`BRK.B`, `BF-B`) round-trip through the router
  and API without encoding bugs (add `encodeURIComponent` at the API layer if needed).

- [ ] **FM4.7 (M) CORS/config drift.**
  *Evidence:* [main.py:183](backend/main.py#L183) hard-codes `http://localhost:{3000,3001,5173,8080}`;
  the frontend defaults to `127.0.0.1:8000`. Opening the app at `http://127.0.0.1:3001`
  produced "Failed to fetch" on every page (reproduced during the audit) and a
  "Connection Error" card that says `http://localhost:8000` regardless of the real URL
  ([page.tsx:253](frontend/src/app/page.tsx#L253)). `ALLOWED_ORIGINS` exists in `.env` but is
  not read (CLAUDE.md §6).
  *Fix:* backend reads `ALLOWED_ORIGINS`; dev default includes both `localhost` and
  `127.0.0.1` forms; frontend error text uses `API_BASE_URL` and distinguishes network
  failure from CORS from HTTP error where detectable. `security-review` (no wildcard with
  credentials).

---

## FM5 — Charts & visualization  *(load the `dataviz` skill first)*

- [ ] **FM5.1 (M) Sector treemap.**
  *Evidence (rendered crop):* labels render white with a thick white halo — illegible on the
  light-green/pink cells. *Root cause:* `stroke="#fff"` on `<Treemap>` is inherited by the
  `<text>` inside the custom `content` (fill is set to slate-900 but the stroke isn't
  cleared). *Also:* 4 coarse color buckets (±1%); small-box labels truncate to "Cons S…";
  color is the only carrier of direction on the cells' fill (labels carry sign — ok); the
  treemap is not keyboard-reachable; "Unknown" issue is FM3.6.
  *Fix:* `stroke="none"` on the text (or `paintOrder`), proper diverging scale with a
  legend and stated thresholds/clamp, colorblind-safe, AA contrast for labels on every
  bucket, keyboard/aria alternative (the legend list can be the accessible path).

- [ ] **FM5.2 (M) Alpha Finder scatter.** Tooltip hides the symbol (`labelFormatter={()=>''}`)
  — you must click to learn what a point is; no legend for green/red; nulls silently dropped;
  axes should be labelled with what they mean (and follow FM3.3's final definitions).

- [ ] **FM5.3 (L→M) Candlestick chart.** Donchian high & low are the same pale-gray dashed
  line (~2.3:1 on the `#f1f5f9` plot) so upper/lower are indistinguishable and low-contrast;
  no OHLC/crosshair readout; "E" earnings markers unexplained; `fitContent()` on every
  toggle resets the user's zoom/pan; verify time-axis dates around DST/UTC edges.

- [ ] **FM5.4 (L) Index strip & sparklines.** Coloring semantics per FM3.10; per-instrument
  formatting (`^TNX` %, VIX, BTC); as-of per FM3.5; sparkline min/max not zero-based.

- [ ] **FM5.5 (L) Sector Trend chart.** Lint/ARIA items in FM1.4; confirm tooltip/hover
  interactions (never exercised headlessly); 3 sectors share hues by dash only — verify
  distinguishability at real size.

---

## FM6 — Responsive & accessibility

- [x] **FM6.1 (M) Navigation doesn't fit narrow screens. — FIXED 2026-09-22.** *Fix:* the single horizontal
  `TopNav` bar was replaced with a persistent, collapsible left `Sidebar` (>= 1024px) and a `MobileNav` slim top
  bar + slide-over drawer (below it), grouped exactly as anticipated here ("Markets": Dashboard/Screener/
  Strategy/Alerts/Telegram; "Ops": System Health/ML Stats/Performance). One shared route list
  (`lib/navigation.ts`) and one shared search implementation (`hooks/useSymbolSearch.ts`,
  `components/layout/SearchBox.tsx`) back both. *Accept (real-Chrome, 27 checks):* zero horizontal overflow at
  1440/820/390px on every route including `/telegram`; all 8 routes reachable and titled at every width, incl.
  collapsed to an icon rail; active-route highlighting; collapse state survives navigation and reload; search
  works in the sidebar and in the mobile drawer; the mobile drawer is a real top-layer dialog (focus trap,
  Escape/backdrop close); zero console errors.
- [ ] **FM6.2 (M) Tables & layout overflow.** `removeWrapper` removed NextUI's horizontal
  scroller: Strategy (8 cols + 180px plan bar), Screener, Alpha Finder overflow the page at
  390px (the screener filter card is cut off). *Fix:* scroll containers with sticky first
  column, or column priority; stack the screener filter panel above results on mobile.
- [ ] **FM6.3 (M) Accessibility.** NextUI `Table`s have no `aria-label` (console warnings +
  screen-reader noise); filter toggles need `aria-pressed`; hover mini-chart is mouse-only
  (needs focus/keyboard equivalent or a plain link fallback — and verify clicking the
  trigger doesn't both toggle the popover and navigate); contrast audit (AA) for chip
  colors and treemap; don't rely on red/green alone.
- [ ] **FM6.4 (L) Page chrome.** `min-h-screen` under the sticky nav forces a permanent 56px
  scroll and off-centers loader/error cards (use `min-h-[calc(100vh-3.5rem)]`); per-page
  `<title>` (all tabs currently identical); remove unused default `public/*.svg`.

---

## FM7 — Cleanup & consistency

- [ ] **FM7.1** Delete dead code after grep-confirming no importers: `MainLayout.tsx`
  (hard-codes "Market Open" and "S&P: +0.8%" — would be a lie if ever mounted),
  `AIPicksWidget`, `TopGainersTable`, `TopLosersTable`, `UnusualVolumeTable`,
  `MarketSummaryCards`. (The dashboard re-implements all of them inline — decide which
  version survives and delete the other.)
- [ ] **FM7.2** Consolidate `gradeColor`/`confidenceChipColor` (duplicated in screener,
  alerts, strategy vs [uiColors.ts](frontend/src/lib/uiColors.ts)); one formatter module (FM3.9).
- [ ] **FM7.3** NextUI `onClick` → `onPress` (deprecation warning on every Button; the
  library says it will stop working).
- [ ] **FM7.4** "View All" buttons → real `<Link href>` (currently `window.location.href` =
  full reload, no middle-click/keyboard/prefetch).
- [ ] **FM7.5** Break up oversized pages (`page.tsx` 886 lines, stock page 683) into
  components once types/formatters are stable; no behavior change.
- [ ] **FM7.6** Update CLAUDE.md §3.1/§3.3/§8/§9 (frontend section, verification procedure,
  changelog) and mark this file's status board.

---

## FM8 — Final verification & regression net

- [ ] **FM8.1** Full matrix: routes (`/`, `/screener`, `/strategy`, `/alerts`,
  `/stock/AAPL`, `/stock/BRK.B`, `/system-health` ×3 views, `/ml-stats`, `/performance`) ×
  widths (1440/820/390) × states (loading, empty, error, data), with the backend stopped
  for the error state. Screenshots inspected, not just captured.
- [ ] **FM8.2** Automated tests where they pay for themselves (CLAUDE.md §8: none exist):
  unit tests for pure logic — formatters, `positionPct`, `nearestBarDate`, position-size
  math, cumulative sector series, RVOL calculation (backend); a smoke test that each route
  renders without console errors. Decide scope with the user; no test theatre.
- [ ] **FM8.3** `code-review` (high) + `simplify` over the whole diff; `security-review`
  over config/CORS changes.
- [ ] **FM8.4** Independent data re-audit: re-run the R4 spot-check on ≥ 10 symbols for
  price, %change, RVOL, sector, signal counts; record in §10.

---

## 9. Verified facts from the audit (so we don't re-derive them)

- `npx tsc --noEmit` — clean.
- `next lint` — 23 errors, 6 warnings (listed in FM1.1 / FM1.4) — **now 0 / 0 (FM1 done)**;
  the audit's "two pre-existing `eslint-disable` lines" was actually three (the third was in
  `LightweightCandlestickChart.tsx`); all removed.
- Renders inspected (headless Chrome, `localhost:3001`): `/`, `/screener`, `/strategy`,
  `/alerts` at 1440px; `/` at 820px; `/screener` at 390px.
- **Not verified** (must be done inside the milestones above): `/stock/[symbol]`,
  `/system-health`, `/ml-stats`, `/performance` renders; open Select dropdowns, tooltips,
  hover popovers, chart hover states; production `next build` (a scratch-copy attempt failed
  on the non-ASCII path — FM0.2); the Suspense build failure (framework behaviour, not run).
- Dev-server anomalies that are environment, not app code: FM0.1.

## 10. Progress log

*(Append newest first. Each entry: date · item IDs · what changed · evidence (commands,
numbers, screenshots) · what was NOT verified.)*

- 2026-09-20 · **ML-related frontend changes (outside the FM items; recorded here because they touch numbers the UI
  shows)** · Made every ML-derived field honestly nullable end to end. Files: `services/api.ts` (`AIPickItem.ml_score` /
  `confidence`, both plan types' `momentum_score` / `fundamentals_score` / `alignment_score` → `number | null`; new
  `ModelEvaluation`, `CurrentModel.reason`, `ModelHistoryEntry.served`, dataset fields), `components/dashboard/AIPicksWidget.tsx`
  ("—" for a null score), `app/strategy/page.tsx` and `app/stock/[symbol]/page.tsx` (score tooltips), `components/health/MLStatsPanel.tsx`
  ("none served" state with the reason, new "Latest honest evaluation" card with gate checks, legacy models tagged
  "not served" with their old metrics hidden, plan-profit share + discontinuity count in the dataset card).
  Backend counterparts: `strategy_calc.py` (renormalised weights, `momentum_score: null`), `main.py` top-ai-picks (`ml_score: null`),
  `ml_stats_service.py`, `system_health_service.py`. *Evidence:* `npx tsc --noEmit` → clean; `npx next lint` → "✔ No ESLint
  warnings or errors"; API checked on temporary uvicorn instances (ports 8001/8002, stopped afterwards); **headless-Chrome renders at 1440px of
  `/ml-stats`, `/strategy` and `/` against the restarted backend** confirmed the new states (no fabricated percentages; `no model`
  chips; legacy models flagged). *Not verified:* 820px / 390px widths, keyboard/a11y, hover/tooltip interactions.
  Rule check (§0): no `as any`, no default values introduced — missing data renders as "—".

- 2026-09-20 · — · **Dev server exited on its own** (`next dev -p 3001`, exit code 1) some hours
  after the FM1 work, while a browser tab was still polling it. Its log ends in ordinary `200`s —
  no EPIPE / OOM / uncaught exception / port error — so this is *not* the FM0.1 failure mode.
  Cause **not established** (likely the harness tearing down the idle background task, but
  unproven). Restarted; all 10 routes 200, backend `/api/health` 200, no errors in the new log.
  If it recurs, capture its stdout to a file instead of the harness task and check whether the
  exit coincides with the session going idle. No code changed.
- 2026-09-19 · **FM1.1–FM1.5 ✅ (FM1 complete)** · Build health. Files changed: `next.config.ts`,
  `services/api.ts`, `app/page.tsx`, `app/screener/page.tsx`, `app/stock/[symbol]/page.tsx`,
  `app/alerts/page.tsx`, `app/performance/page.tsx`, `app/strategy/page.tsx`,
  `components/dev/DevQAPanel.tsx`, `components/dashboard/SectorTrendChart.tsx`,
  `components/charts/LightweightCandlestickChart.tsx`. Details per item above.
  *Gate output (final run):* `npx tsc --noEmit` → exit 0; `npx next lint` → "✔ No ESLint warnings
  or errors"; escape-hatch grep → none; `NEXT_DIST_DIR=.next-build npm run build` → exit 0,
  "✓ Compiled successfully", "✓ Generating static pages (11/11)", routes `/ /alerts /ml-stats
  /performance /screener /strategy /system-health` static + `/stock/[symbol]` dynamic (first-load
  JS: `/` 415 kB, `/stock/[symbol]` 401 kB). Before the Suspense fix the same build exited 1 on
  `/screener` (evidence for FM1.2). Unset-var build exited 1 with the intended message (FM1.3).
  *Reviews:* `code-review` (medium, explicit paths — the frontend is untracked, so it read the files
  rather than a diff) returned 8 findings; **none are regressions from FM1** — six are already
  tracked (FM3.1, FM3.4 ×2, FM4.3, FM4.5, FM4.7), two are new and logged in §11 (FM-N6, FM-N7).
  `simplify`'s git-diff mode can't see untracked files either; did the equivalent by hand and
  consolidated the duplicated `Maybe<T>` (now exported from `api.ts`). `dataviz` was loaded before the
  chart-lifecycle edit; earlier type-only/markup edits to the treemap, scatter and sector-trend
  code happened *before* it was loaded (no visual/colour/mark change — flagged for honesty, R3).
  *Scope kept:* the wire-format types describe what the backend really sends today, including
  fabricated defaults (`volume_ratio` 1.0, `price_change_pct` 0, `market_cap` 0) — those are FM3.4's
  to remove at the backend, after which the types get `| null` (per R2, not papered over here).
  *Not verified:* interactive states (Select dropdowns, hover cards, chart tooltips, the Trend
  toggle's legend hover/click) — the headless harness can't click; production-mode *runtime* render
  (`next start`) — only the build was exercised; `/strategy` and `/alerts` renders after the edits
  (they only got string/dead-code changes, but were not re-screenshotted).
- 2026-09-19 · **FM0.1, FM0.2, FM0.3 ✅** · Restarted the `:3001` dev server cleanly (cache
  wiped, no piped stdout); added `distDir: process.env.NEXT_DIST_DIR || '.next'` +
  `.next-build/` gitignore; added `frontend/scripts/screenshots.sh`.
  *Evidence:* all 9 routes 200; `/stock/AAPL` (was 500) renders chart + fundamentals +
  track record; `/stock/BRK.B` renders (with visible data gaps — see §11);
  `/system-health`, `/ml-stats`, `/performance` render real data; isolated build produced
  `.next-build/`, left `.next` at 32 files, dev still 200; build's lint failure is exactly
  the FM1 list (23 errors / 6 warnings, matches the audit).
  The screenshots also reproduce the audit's FM3 numbers on the live data (Active Signals
  20, Volume Ratio 2.01x, 1336 "unusual", "Unknown" sector box dominant, treemap label
  halo) — so those findings are real, not test-harness artifacts.
  *Not verified:* `/screener`, `/strategy`, `/alerts` were only checked for HTTP 200 this
  session (already screenshotted in the original audit); 820/390px renders of the routes
  other than `/` and `/stock/AAPL`; any interactive state (dropdowns, hover, tooltips).
  *Correction:* `:3000` is an unrelated app, not a stale server of this project (see FM0.1).
- 2026-09-19 · — · File created from the frontend audit. No code changed yet.

## 11. Findings intake (new items discovered while fixing)

*(ID · severity · one-line evidence · owner layer. Do not fix here silently — promote to a
milestone item first.)*

- **FM-N1 · H · frontend (+ data)** — `/stock/BRK.B` shows the literal text **"AI: undefined"**
  ([stock page:329](frontend/src/app/stock/[symbol]/page.tsx#L329) — `hasSignal` is true but
  `ml_confidence` is undefined, so the template prints `undefined`), plus "Mkt Cap **$0B**",
  "Div Yield **N/A%**", weekly/monthly "Position in channel: **50%**" beside "Low N/A · High
  N/A", "Alignment 25% (F)" and "Overall Quality 50" with every sub-score N/A, and a "Win rate
  **0%**" rendered in green. All are R1/R4 fabricated-value cases → fold into **FM3.4 / FM3.9**
  as concrete acceptance cases (BRK.B is the repro). Underlying data gap: BRK.B has no
  fundamentals/weekly/monthly rows — owner = pipeline (see FM3.6).
- **FM-N2 · M · backend** — the request-latency tracker records **unmatched (404) requests by
  raw URL**: [main.py:200-201](backend/main.py#L200) does
  `path_template = route.path if route is not None else request.url.path`. Confirmed effect on
  `/performance`: rows like `/api/stock/signal/NVDA` and `/api/stock/price-history/AAPL`
  (wrong path order — nothing in the repo calls these; earlier manual probes) with 100%
  errors, one row per distinct URL. Any scanner or typo creates unbounded new keys in
  `performance_tracker._routes` (per-key samples are bounded, the key count is not) and
  pollutes the report. Fix: bucket unmatched requests under one label (e.g. `"<unmatched>"`).
  Owner layer: backend. Does not belong to any FM item.
- **FM-N3 · L · frontend** — Chart time-axis month labels render in **Hebrew** ("פבר", "מרץ",
  "אפר"…) on the AAPL/BRK.B charts (the machine's browser locale), while everything else is
  English/US. Decide a fixed locale for the chart's `timeScale` formatter. → **FM5.3**.
- **FM-N4 · L ❓ · frontend/data** — AAPL header shows "Grade **D**" next to "Overall Quality
  **63**" and sub-scores 15/20/18/10 (see the AAPL screenshot). Not yet established whether the
  grade cut-offs are consistent with the score (63 → D looks harsh) — check the grade mapping in
  `fundamentals_updater.py` against what is displayed before touching anything. → **FM3.9**.
- **FM-N5 · L · docs** — ~~CLAUDE.md §8 has no frontend dev-server start/verify procedure~~ —
  **done 2026-09-19** (§8 + changelog updated with FM0). FM7.6 only needs a final re-check.
- **FM-N1 root cause (update)** — the `"$0B"` / `market_cap: 0` / `shares_outstanding: 0` on
  `/stock/BRK.B` is written by the **screener**, not the UI:
  [multi_timeframe_screener.py:649](mechanism/screeners/multi_timeframe_screener.py#L649)
  `self.format_market_cap(row['market_cap']) if row['market_cap'] else "$0B"`, and the enhancer's
  `fundamentals.get('market_cap', 0)`
  ([ml_signal_enhancer.py:859](mechanism/ml_enhancement/ml_signal_enhancer.py#L859)). Fabricated-zero
  defaults at the **pipeline** layer → add to FM3.4's audit scope (it currently lists only
  backend endpoints + frontend). Also seen in the same audit: `stock_service.py:110`
  (`price_change_pct … else 0.0`) and `main.py:674/786/897/948-949` (`volume_ratio` 1.0 /
  `price_change_pct` 0).
- **FM-N6 · L · frontend (dev tool)** — `DevQAPanel.runCheck`
  ([DevQAPanel.tsx:87](frontend/src/components/dev/DevQAPanel.tsx#L87)) calls `clearTimeout` as soon
  as headers arrive, so the 15 s abort doesn't cover `await res.json()` and reported latency is
  time-to-headers. A stalled body leaves the row "pending" and "Run tests" disabled until reload.
  Dev-only (NODE_ENV-gated), but it is the tool FM8.1 leans on. Found by the FM1 `code-review`.
- **FM-N7 · M · frontend** — the dashboard's `fetchAlpha`
  ([page.tsx:194](frontend/src/app/page.tsx#L194)) has the same last-response-wins race FM4.3 lists
  for screener/alerts/strategy/stock: click "Bullish Breakout" then "Near Bearish" quickly and a
  slower first response can overwrite the newer one, leaving the table out of sync with the
  highlighted chips and the "{n} results" count. → extend **FM4.3**'s scope to the dashboard.
- **FM-N8 · M · backend/pipeline** — active-signal snapshots have **no `peg_ratio` key** (the
  screener JSON doesn't carry it), so the stock page's "PEG" is always N/A for symbols with an
  active signal, while no-signal symbols show it (MMM: PEG 1.56). A field silently missing on one
  of two code paths for the same panel. Owner: backend `stock_service`/screener output → **FM3.9**
  (units & field audit) or FM4.6.
- **FM-N9 · L · frontend** — with an unknown symbol, dev shows a Next "4 Issues" overlay badge:
  the axios response interceptor `console.error`s every failed request, which Next dev treats as
  issues. Expected noise for a 404, but it means the dev overlay can't be used as an "any real
  problem?" signal on error paths. → note for **FM4.1/FM4.3** (error handling pass).

### Findings from the 2026-09-20 renders (ML switched off in the app)

- **FM-N10 · H · frontend/product** — with no served ML model every Alpha Finder row has `combined_score` 60 (alignment 100
  × 0.6), so the list is an alphabetical wall of ties and its subtitle "Ranked by combined confidence + alignment + quality score"
  ([page.tsx](frontend/src/app/page.tsx)) is false. The "High Confidence Only" chip (dashboard) and button
  ([strategy/page.tsx](frontend/src/app/strategy/page.tsx)) can only return empty (`min_confidence=high` needs a score ≥ 70).
  → **FM3.3** (Alpha Finder semantics) + a product decision: tie-break by a real, disclosed measure (e.g. lower ATR%), retitle,
  hide/disable the confidence controls while no model is served.
- **FM-N11 · M · frontend** — Strategy page: the POSITION SIZE column reads 10.0% on every row (defaults account-risk 1% /
  max-position 10%: 1% ÷ stop-risk 3-6% > 10%, so the cap always binds) → the column carries no information at defaults. Also the
  controls card renders garbled at 1440px (labels overlap the input values, large empty area) — not investigated; likely **FM2** (NextUI
  plugin/Tailwind) rather than this change, unverified. → **FM2** / **FM3.10**.
- **FM-N12 · M · backend/frontend** — `/ml-stats` "Live Prediction Track Record" still counts the 1,537 predictions logged by the
  discredited legacy models (e.g. "Very Low 1370") next to "none served". → scope `get_prediction_track_record()` to the served model
  version (or label it legacy). Owner: `ml_stats_service.py`.
- **FM-N13 · M · frontend/product** — the dashboard "AI-Enhanced Picks" widget keeps its "AI" title while it is only
  alignment-ranked (its ML column shows "—"). → rename/hide while ML is unavailable (decision pending with the user).
- **FM-N14 · L · backend** — strategy scores are now on a higher scale while ML is absent (weights renormalised over the remaining
  components: e.g. 73 → 92 for the same signal). Comparable only among unscored signals; note it wherever the score is described.

### Findings from the 2026-09-22 Telegram Control Center work (found, not fixed here — R6)

- **FM-N15 · M · frontend — FIXED 2026-09-22.** *Root cause:* the top-progress bar's sweep animation (`@keyframes top-progress-sweep`, `translateX(350%)`)
  overshoots its own `overflow: hidden` fixed ancestor; Chromium still counts the transformed descendant's post-transform box toward
  `document.documentElement.scrollWidth` even though it is visually clipped. *Fix:* `style={{ contain: 'layout' }}` on the fixed wrapper
  (`lib/topProgress.tsx`) makes it the containing block for overflow purposes, so the transform can no longer escape upward. *Evidence:*
  `document.documentElement.scrollWidth` measured 905px on every route before the fix (identical regardless of page content, confirming it
  was this one global element) and matches the viewport after it, at 390/820/1440px, across 8 routes.
- **FM-N16 · M · frontend — FIXED 2026-09-22** by the FM6.1 sidebar/mobile-nav rewrite (a vertical list has no fixed-width budget to run out of).
- **FM-N17 · H · dev-infra/backend** — `uvicorn main:app --reload` **hangs in "Waiting for application shutdown"** after a file change while a keep-alive connection is open:
  the port keeps LISTENING, every request hangs, `CLOSE_WAIT` piles up (the wedge CLAUDE.md §8 describes; reproduced on 2026-09-22 right after editing `routers/telegram_control.py`).
  Root cause not investigated (suspects: the lifespan shutdown or the `main-page-data` background rebuild thread). Workaround: `taskkill /PID <listener> /T /F`, restart; or run
  without `--reload` while editing backend files.
- **FM-N18 · M · frontend** — the app does not load NextUI's Tailwind plugin, so NextUI's `Modal` renders unstyled (no backdrop/positioning) and `Button` variants `bordered` / `flat` /
  `isLoading` lose their border/fill (already known for `bg-content1`). The Telegram Control Center avoids them (`components/common/Dialog.tsx` on native `<dialog>`,
  `components/telegram/ui.tsx`); pages that still use those variants (e.g. `/system-health` bordered buttons) show the defect. → **FM2**.
