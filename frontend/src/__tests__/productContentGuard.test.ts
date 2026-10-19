// Static guard for the product-facing pages: no trade plans, position sizing, experimental model output, signal
// performance (win rate / outcomes) or "confirmed signal" framing may come back into them. Comments are stripped
// first, so explanations of what was removed do not trip it. Shared components that internal pages also use (the
// chart, which can draw a plan when given one) are covered by the stock-page render test instead.
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = path.resolve(__dirname, '..');

// Product pages and the components only they render.
const PRODUCT_FILES = [
  'app/page.tsx',
  'app/screener/page.tsx',
  'app/alerts/page.tsx',
  'app/markets/page.tsx',
  'app/markets/[symbol]/page.tsx',
  'app/earnings/page.tsx',
  'app/stock/[symbol]/page.tsx',
  'components/dashboard/SummaryTiles.tsx',
  'components/dashboard/MarketIndicesStrip.tsx',
  'components/dashboard/MarketMovers.tsx',
  'components/dashboard/CommoditiesCard.tsx',
  'components/dashboard/WatchlistCard.tsx',
  'components/dashboard/SectorTrendChart.tsx',
  'components/earnings/EarningsCard.tsx',
  'components/earnings/EarningsTable.tsx',
  'components/stock/KeyLevels.tsx',
  'components/stock/StockContextPanel.tsx',
  'components/screener/StrengthMap.tsx',
  'components/screener/StrengthCards.tsx',
  'components/screener/StrengthMeter.tsx',
  'lib/strength.ts',
  'app/watchlist/page.tsx',
  'lib/deepValueView.ts',
  'lib/watchlistView.ts',
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
];

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
    const code = stripComments(fs.readFileSync(path.join(SRC, rel), 'utf-8'));
    const hits = BANNED.filter(([, re]) => re.test(code)).map(([what]) => what);
    expect(hits).toEqual([]);
  });

  it('the guard itself catches each banned pattern (so a typo cannot silently disable it)', () => {
    const samples = ['Trade plan', 'Suggested position size', 'TP1', 'Model score', 'Win rate', 'Strategy score', 'Confirmed today', 'will outperform'];
    for (const s of samples) expect(BANNED.some(([, re]) => re.test(s))).toBe(true);
    expect(stripComments('// Trade plan removed\nconst a = 1; /* Model score */')).not.toMatch(/Trade plan|Model score/);
  });
});
