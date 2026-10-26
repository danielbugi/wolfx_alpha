# Overview metrics: consistency audit (2026-10-26)

**Scope:** every number on the Overview, compared with the Screener and Sector Intelligence. The fixes are in this PR (backend `MarketService.get_market_overview` → Screener engine, plus Overview labels); everything else is documented here only.

**Evidence:** code on `main@6e3d7b3` and production read-only measurements (session 2026-10-23).

## 1. Metric by metric

| Overview element | Population and filters | Session | Formula / source | Freshness shown | Goes to | Status |
|---|---|---|---|---|---|---|
| **Movers lists** (gainers, losers, unusual volume) | Bar on the session, close ≥ $1, 20-session dollar volume ≥ $1M; unusual: volume ratio ≥ 2 | Explicit (newest real session) | Screener v2 engine (`DiscoveryService.movers`, PR #65) | "session YYYY-MM-DD" on the card | The matching Screener query, pinned to the session | ✅ since PR #65 |
| **"Today's activity" tiles**: unusual volume, RSI > 70 / < 30, average RSI / volume ratio | **Before:** each symbol's *latest bar within 5 days*, close ≥ $1. **After:** bar **on** the session, close ≥ $1, **no liquidity floor** (labelled) | Before: implicit. **After: explicit**, shown in the card title | Before: legacy SQL with each symbol's *latest indicator row of any date*, and averages defaulting to 1.0 / 50.0. **After:** Screener base, indicators **of** the session, averages null when unknown, "known for N" when coverage is partial | **After:** "session YYYY-MM-DD" | **After:** the unusual-volume tile links to `/screener?min_close=1&min_volume_ratio=2&session=…`, whose total **equals** the tile (80 = 80 on 10-23). RSI has no Screener filter, so the RSI tiles have no link. | ✅ fixed here |
| **Sector heatmap** and **"Sectors today" tile** (best / weakest) | **Before:** latest bar within 5 days, close ≥ $1, each symbol's latest fundamentals sector *of any age*. **After:** bar on the session, close ≥ $1, the **Screener's sector** (observed RS sector, else the fundamentals sector ≤ 30 days old) | Before: implicit. **After: explicit** | **Mean** 1-day change of the members (box size = members with a change) | After: the card's session | `/sectors/<slug>` (tile names; heatmap selection → "Sector page" / "Screen this sector") | ✅ fixed here |
| **Sector Trend** view (90 days) | Per day: bar that day, close ≥ $1, sector = latest fundamentals sector (**current membership**, not point-in-time) | Each stored day | `sector_performance_daily`, written by `mechanism/data_updaters/sector_performance_snapshot.py`: the mean daily change, summed into a 90-day line | Implicit | Sector selection | ⚠ documented only (mechanism code; frozen during Stage 2). The membership is current, not historical, and that is now stated in the caption. |
| **Market snapshot panel** (regime, breadth, index vs typical stock, sector leadership) | The observed market-intelligence universe | **Explicit** (observed snapshot of the session) | `market_snapshot_*` tables (daily run) | Session, provenance, capture time; stale-vs-prices warning | Sector pages | ✅ already explicit |
| **VIX tile** | ^VIX index | Its own newest bar | `market_index_prices` | Last close | — | ✅ (a different instrument, not a stock count) |
| **Overview "Data as of"** | — | `market_summary.last_updated` = the newest `technical_indicators` date | Legacy | Shown | — | ✅ (equals the session on normal days; the cards carry their own session) |

## 2. Intentional differences, now labelled

- **Activity tiles vs movers lists:**
  - The tiles count **every stock priced $1+**, with no liquidity floor.
  - The lists show **liquid** stocks only ($1+ and $1M+ a day).
  - On 10-23 that is 80 vs 71 unusual-volume stocks. Both labels say so.
- **Overview sectors (mean) vs Sector pages (median):**
  - The heatmap uses the **mean** daily change of members; the Sector Intelligence pages use the **median** and the share up.
  - These measure different things; the outliers that move a mean are exactly what a median ignores. Both are labelled ("average change of members" / "median").
- **Sector Trend (current membership) vs sector pages (current membership):** they are consistent, and **neither is point-in-time**. The sector pages already say so; the Trend caption says so too.

## 3. Before → after on production (session 2026-10-23, read-only)

| Figure | Before (legacy) | After (Screener engine) |
|---|---|---|
| Stocks counted | 3,043 (3 symbols whose newest bar predates the session were counted) | **3,041** (bar on 10-23 only) |
| Gainers / losers / A-D | 1,056 / 1,932 / 0.55 | 1,056 / 1,932 / 0.55 (+ 53 unchanged stated) |
| Unusual volume | 80 | 80, **= its Screener view (80)** |
| RSI > 70 / < 30 | 202 / 254 | 202 / 254 (RSI known for 3,035) |
| Average RSI / volume ratio | 49.23 / 0.99 | 49.22 / 0.99 |
| Sector heatmap | 12 sectors | Identical on this session |

On a clean weekend snapshot the numbers barely move. The fix matters **at session rollover and on partial loads**: the old 5-day window mixed symbols from different days, and the latest-indicator lookup counted older days' RSI and volume ratios as today's (9 symbols currently have an indicator row older than the session). It also matters when no stock fell, where the old ratio was gainers ÷ 1, and when averages were missing, where it showed 1.0× and 50.

## 4. Performance

- **Cost:** the engine evaluation costs **0.51 s**, against 0.08 s for the legacy query.
- **Cache:** the endpoint now keeps one result per newest stored price date (`MAX(date)`, index-only, ~0.3 ms) and rebuilds as soon as a newer date exists, or after 15 min at most. So a normal Overview load pays only the 0.3 ms check.
- **Screener page:** unchanged at 0.51 s; the added RSI column isn't part of Screener rows.
