// The product stock page against an API response that DOES carry internal research output (a trade plan, an ML score,
// a breakout track record with outcomes): none of it may reach the screen or the chart.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

const chartProps = vi.hoisted(() => ({ last: null as null | Record<string, unknown> }));

vi.mock('next/navigation', () => ({
  useParams: () => ({ symbol: 'acme' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/stock/ACME',
}));
vi.mock('@/components/charts/StockChart', () => ({
  default: (props: Record<string, unknown>) => {
    chartProps.last = props;
    return <div data-testid="chart" />;
  },
}));
vi.mock('@/components/stock/NewsPanel', () => ({ default: () => <div>news panel</div> }));
vi.mock('@/components/stock/PeersPanel', () => ({ default: () => <div>peers panel</div> }));

const detail = {
  symbol: 'ACME',
  data_source: 'active_signal',
  snapshot: {
    current_price: 50.25, price_change_pct: 1.2, sector: 'Industrials', industry: 'Machinery', quality_grade: 'B',
    market_cap: 2e9, market_cap_formatted: '$2.0B', pe_ratio: null, beta: 1.1, dividend_yield: null,
    rsi_14: 61, donchian_high: 51, donchian_low: 44, price_position_in_channel: 80, sma_20: 48, sma_50: 46, atr_14: 1.3,
    weekly_context: null, monthly_context: null,
  },
  ai_rating: {
    signal_type: 'bullish_breakout', alignment_grade: 'A', alignment_score: 90, reasoning: 'internal reasoning text',
    ml_momentum_probability: 72, ml_confidence: 'high',
    plan: {
      direction: 'long', entry_price: 50, stop_loss_price: 47, tp1: 53, tp2: 56, tp3: 60, risk_pct: 6, reward_pct_tp1: 6,
      reward_pct_tp2: 12, reward_pct_tp3: 20, strategy_score: 81, momentum_score: 80, fundamentals_score: 70, alignment_score: 90, risk_bonus: 5,
    },
  },
  breakout_track_record: {
    total_breakouts: 2, win_rate: 50, avg_gain_10d: 4, avg_loss_10d: -3,
    entries: [
      { date: '2026-08-03', breakout_type: 'bullish', entry_price: 40, success: true, max_gain_10d: 9, max_loss_10d: -1, days_to_peak: 4 },
      { date: '2026-09-14', breakout_type: 'bullish', entry_price: 45, success: false, max_gain_10d: 1, max_loss_10d: -6, days_to_peak: 1 },
    ],
  },
  quarterly_financials: [],
};

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return {
    ...actual,
    stockApi: {
      getDetail: vi.fn(async () => detail),
      getPriceHistory: vi.fn(async () => ({ bars: [] })),
      getEarnings: vi.fn(async () => ({ earnings_dates: [] })),
    },
    marketApi: { ...actual.marketApi, getSectorHistory: vi.fn(async () => ({ sectors: [] })) },
  };
});

import StockDetailPage from '@/app/stock/[symbol]/page';

const FORBIDDEN = [
  /trade plan/i, /\bEntry\b/, /\bStop\b/, /\bTP[123]\b/, /position size/i, /account risk/i, /model score/i, /experimental/i,
  /win rate/i, /track record/i, /strategy score/i, /active breakout signal/i, /no active signal/i, /internal reasoning text/,
  /\b72%/, /Timeframe alignment/i,
];

beforeEach(() => {
  chartProps.last = null;
  window.localStorage.clear();
});

describe('stock page (product view)', () => {
  it('shows no trade plan, sizing, ML score, signal chip or track record even when the API sends them', async () => {
    const { container } = render(<StockDetailPage />);
    expect(await screen.findByText('ACME')).toBeTruthy();
    for (const re of FORBIDDEN) expect(container.textContent).not.toMatch(re);
    expect(screen.queryByRole('radio', { name: /strategy/i })).toBeNull();
    expect(container.textContent).toMatch(/Technicals/);
  });

  it('gives the chart no plan and only neutral breakout markers (dates kept, outcomes removed)', async () => {
    render(<StockDetailPage />);
    await screen.findByText('ACME');
    expect(chartProps.last?.plan).toBeNull();
    const markers = chartProps.last?.breakouts as Array<Record<string, unknown>>;
    expect(markers.map((m) => m.date)).toEqual(['2026-08-03', '2026-09-14']);
    for (const m of markers) {
      expect(m.success).toBeNull();
      expect(m.max_gain_10d).toBeNull();
      expect(m.max_loss_10d).toBeNull();
      expect(m.entry_price).toBeNull();
    }
  });

  it('falls back to News when the browser remembers the removed Strategy tab, and Technicals shows key levels', async () => {
    window.localStorage.setItem('stock_detail_tab', 'strategy');
    render(<StockDetailPage />);
    expect(await screen.findByText('news panel')).toBeTruthy();
    fireEvent.click(screen.getByText('Technicals'));
    expect(await screen.findByText('Key levels')).toBeTruthy();
  });

  it('keeps missing values unavailable instead of inventing them', async () => {
    render(<StockDetailPage />);
    await screen.findByText('ACME');
    const pe = screen.getByText('P/E').parentElement!;
    expect(pe.textContent).toContain('—');
  });

  it('links the sector name to its sector page', async () => {
    render(<StockDetailPage />);
    expect((await screen.findByRole('link', { name: 'Industrials' })).getAttribute('href')).toBe('/sectors/industrials');
  });
});
