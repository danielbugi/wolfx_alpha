// Static guard for the product-facing pages: no trade plans, position sizing, experimental model output, signal
// performance (win rate / outcomes) or "confirmed signal" framing may come back into them. Comments are stripped
// first, so explanations of what was removed do not trip it. Shared components that internal pages also use (the
// chart, which can draw a plan when given one) are covered by the stock-page render test instead.
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { NAV_GROUPS } from '@/lib/navigation';

const SRC = path.resolve(__dirname, '..');

// Product pages and the components only they render.
const PRODUCT_FILES = [
  'app/page.tsx',
  'app/screener/page.tsx',
  'app/markets/page.tsx',
  'app/markets/[symbol]/page.tsx',
  'app/earnings/page.tsx',
  'app/stock/[symbol]/page.tsx',
  'components/dashboard/SummaryTiles.tsx',
  'components/dashboard/MarketIndicesStrip.tsx',
  'components/dashboard/MarketMovers.tsx',
  'components/dashboard/CommoditiesCard.tsx',
  'components/dashboard/WatchlistCard.tsx',
  'components/dashboard/MarketChartCard.tsx',
  'components/dashboard/SectorTrendChart.tsx',
  'components/earnings/EarningsCard.tsx',
  'components/earnings/EarningsTable.tsx',
  'components/stock/KeyLevels.tsx',
  'components/stock/StockContextPanel.tsx',
  'components/screener/StrengthMap.tsx',
  'components/screener/StrengthCards.tsx',
  'components/screener/StrengthMeter.tsx',
  'components/screener/StrengthScore.tsx',
  'app/momentum-today/page.tsx',
  'lib/momentumToday.ts',
  'hooks/useMomentumToday.ts',
  'components/dashboard/MomentumTopCard.tsx',
  'components/stock/StockStrengthCard.tsx',
  'components/stock/LatestReportCard.tsx',
  'lib/latestReport.ts',
  'hooks/useStockContext.ts',
  'lib/strength.ts',
  'app/watchlist/page.tsx',
  'lib/watchlistView.ts',
  'lib/performance.ts',
  'components/watchlist/WatchlistPerformance.tsx',
  'components/watchlist/WatchlistPanel.tsx',
  'components/watchlist/SymbolChart.tsx',
  'components/charts/IndicatorsDialog.tsx',
  'lib/chartIndicators.ts',
  'components/watchlist/WatchlistDetails.tsx',
  'components/common/DataStamp.tsx',
  'components/common/ProductDisclaimer.tsx',
  'app/sectors/page.tsx',
  'app/sectors/[slug]/page.tsx',
  'components/sectors/MembershipNote.tsx',
  'components/sectors/ObservedSectorContext.tsx',
  'components/marketIntel/MarketIntelligencePanel.tsx',
  'components/marketIntel/RegimeCard.tsx',
  'components/marketIntel/BreadthCard.tsx',
  'components/marketIntel/ReturnsCard.tsx',
  'components/marketIntel/SectorLeadershipCard.tsx',
  'components/marketIntel/SectorHeatmap.tsx',
  'components/marketIntel/SectorRotation.tsx',
  'components/ui/InfoTip.tsx',
  'lib/earningsConfidence.ts',
];

// The one allowed use of the word: the earnings date confidence note (lib/earningsConfidence.ts, owner-approved wording
// 2026-10-26). Only this exact phrase is exempt; every other "confirmed" is still caught.
const ALLOWED = [/not confirmed by latest check/gi];
const allow = (code: string) => ALLOWED.reduce((c, re) => c.replace(re, ''), code);

// Each entry: what it catches, and the pattern (case-insensitive).
const BANNED: [string, RegExp][] = [
  ['trade plan', /trade[\s-]?plan/i],
  ['position sizing', /position[\s-]?siz|account risk|% of (your )?(portfolio|account)/i],
  ['stop / targets', /stop[\s-]?loss|take[\s-]?profit|\bTP[123]\b|stop_loss_price|entry_price/i],
  ['experimental ML output', /model score|ml_momentum_probability|ml_confidence|\bai_rating\b/i],
  ['signal performance', /win[\s_-]?rate|breakout track record|max_gain_10d|max_loss_10d/i],
  ['strategy scoring', /strategy[\s_]score|rating\.plan|PlanBar/i],
  ['signal framing', /confirmed|momentum board|MomentumBoard|MomentumLeaders|active breakout signal|buy signal|sell signal/i],
  ['performance claims', /will outperform|guaranteed|high[\s-]?conviction|proven edge|beats the market/i],
];

function stripComments(code: string): string {
  return code
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, '') // JSX comments
    .replace(/\/\*[\s\S]*?\*\//g, '') // block comments
    .replace(/(^|[^:'"`])\/\/.*$/gm, '$1'); // line comments (not URLs inside strings)
}

describe('product-facing pages', () => {
  it.each(PRODUCT_FILES)('%s carries no trade plan, sizing, experimental ML, signal performance or claims', (rel) => {
    const code = allow(stripComments(fs.readFileSync(path.join(SRC, rel), 'utf-8')));
    const hits = BANNED.filter(([, re]) => re.test(code)).map(([what]) => what);
    expect(hits).toEqual([]);
  });

  // Internal tools (Momentum Board, Position Plans, Deep Value, ...) stay reachable by URL, but a product page never
  // sends a user there: a route string literal such as '/momentum' or `/momentum?sector=` is a link into an internal tool.
  const INTERNAL_ROUTES = NAV_GROUPS.filter((g) => g.internal).flatMap((g) => g.items.map((i) => i.href));
  const internalLink = (code: string) =>
    INTERNAL_ROUTES.filter((r) => new RegExp(`['"\`]${r.replace(/[-/]/g, '\$&')}(?=['"\`?#/])`).test(code));

  it.each(PRODUCT_FILES)('%s links to no internal page', (rel) => {
    expect(internalLink(stripComments(fs.readFileSync(path.join(SRC, rel), 'utf-8')))).toEqual([]);
  });

  it('the internal-link guard catches a link and ignores product routes that share a prefix', () => {
    expect(INTERNAL_ROUTES).toContain('/momentum');
    expect(internalLink('<Link href={`/momentum?sector=${s}`}>')).toEqual(['/momentum']);
    expect(internalLink("href='/alerts'")).toEqual(['/alerts']);
    expect(internalLink("href='/momentum-today'")).toEqual([]);
    expect(internalLink("apiClient.post('/api/performance/client-metric')")).toEqual([]);
  });

  it('the guard itself catches each banned pattern (so a typo cannot silently disable it)', () => {
    const samples = ['Trade plan', 'Suggested position size', 'TP1', 'Model score', 'Win rate', 'Strategy score', 'Confirmed today', 'will outperform'];
    for (const s of samples) expect(BANNED.some(([, re]) => re.test(s))).toBe(true);
    expect(stripComments('// Trade plan removed\nconst a = 1; /* Model score */')).not.toMatch(/Trade plan|Model score/);
    // the earnings note is the only exemption, and it does not hide anything else
    expect(BANNED.some(([, re]) => re.test(allow('Previously reported: Oct 28 — not confirmed by latest check')))).toBe(false);
    expect(BANNED.some(([, re]) => re.test(allow('not confirmed by latest check · Confirmed signal')))).toBe(true);
  });
});
