// Momentum Today: three boards from the Screener's shared engine (one request each), headline counts, tiles coloured by the
// Strength Score (grey "—" with the reason when withheld), where the breakouts come from, the star, and unavailable states.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { QueryPairs, ScreenerResult, ScreenerRow } from '@/services/screenerV2Api';

const api = vi.hoisted(() => ({ search: vi.fn(), definitions: vi.fn(), exportCsv: vi.fn() }));
vi.mock('@/services/screenerV2Api', () => ({ screenerV2Api: api }));

import MomentumTodayPage from '@/app/momentum-today/page';
import { ScoreBadge, ScoreBreakdown } from '@/components/screener/StrengthScore';

const row = (symbol: string, over: Partial<ScreenerRow> = {}) =>
  ({
    symbol, sector: 'Technology', industry: 'Software', change_1d: 2.1, strength_score: 91.3, strength_reason: null,
    strength_rs: 95, strength_trend: 100, strength_momentum: 88, strength_volume: 60, strength_liquidity: 80, ...over,
  }) as ScreenerRow;
const result = (rows: ScreenerRow[], total = rows.length) => ({ session: '2026-10-20', universe: 3060, total, rows }) as unknown as ScreenerResult;
const has = (pairs: QueryPairs, k: string, v: string) => pairs.some(([a, b]) => a === k && b === v);

beforeEach(() => {
  window.localStorage.clear();
  api.search.mockReset();
  api.search.mockImplementation(async (pairs: QueryPairs) => {
    if (has(pairs, 'breakout_20', 'true')) return result([row('AAA'), row('BBB', { sector: 'Energy', strength_score: 35.2 }), row('CCC', { strength_score: null, strength_reason: 'illiquid (under $1M a day)' })], 142);
    if (has(pairs, 'new_high_20', 'true')) return result([row('DDD', { strength_score: 64 })], 57);
    return result([row('EEE', { strength_score: 96.5 })], 155);
  });
});

describe('Momentum Today', () => {
  it('asks the shared engine once per board and shows the session counts', async () => {
    render(<MomentumTodayPage />);
    await waitFor(() => expect(screen.getByTestId('momentum-headline').textContent).toContain('142'));
    expect(api.search).toHaveBeenCalledTimes(3);
    const calls = api.search.mock.calls.map((c) => c[0] as QueryPairs);
    expect(calls.some((p) => has(p, 'breakout_20', 'true') && has(p, 'sort', 'strength_score'))).toBe(true);
    expect(calls.some((p) => has(p, 'new_high_20', 'true') && has(p, 'breakout_20', 'false'))).toBe(true);
    expect(calls.some((p) => has(p, 'min_strength_score', '80'))).toBe(true);
    const head = screen.getByTestId('momentum-headline').textContent!;
    expect(head).toContain('57');
    expect(head).toContain('155');
    expect(head).toContain('of 3,060 stocks on 2026-10-20');
  });

  it('colours each tile by its Strength Score and shows the reason when the score is withheld', async () => {
    render(<MomentumTodayPage />);
    const board = await screen.findByTestId('board-breakouts');
    await waitFor(() => expect(within(board).getAllByTestId('momentum-tile')).toHaveLength(3));
    const [a, b, c] = within(board).getAllByTestId('momentum-tile');
    expect(a.className).toContain('emerald');
    expect(within(a).getByTestId('score-badge').textContent).toBe('91');
    expect(b.className).toContain('orange');
    expect(within(c).getByTestId('score-badge').getAttribute('title')).toBe('No Strength Score: illiquid (under $1M a day)');
    expect(within(board).getByRole('link', { name: 'Open in Screener →' }).getAttribute('href')).toBe('/screener?breakout_20=true&sort=strength_score&order=desc&view=cards');
  });

  it('shows which sectors the breakouts come from, and the star adds to the active watchlist', async () => {
    render(<MomentumTodayPage />);
    const sectors = await screen.findByTestId('momentum-sectors');
    expect(sectors.textContent).toContain('Technology 2');
    expect(sectors.textContent).toContain('Energy 1');
    fireEvent.click(screen.getAllByRole('button', { name: 'Add AAA to watchlist' })[0]);
    expect(JSON.parse(window.localStorage.getItem('watchlist_symbols')!)).toEqual(['AAA']);
  });

  it('says when the data is unavailable', async () => {
    api.search.mockRejectedValue(new Error('down'));
    render(<MomentumTodayPage />);
    expect((await screen.findByTestId('momentum-error')).textContent).toBe('Momentum data is unavailable right now.');
  });

  it('describes the session only (legend states the weights and that it is not a forecast)', async () => {
    render(<MomentumTodayPage />);
    const legend = screen.getByTestId('momentum-legend').textContent!;
    expect(legend).toContain('relative strength 35%, trend 25%, momentum 20%, volume 10%, liquidity 10%');
    expect(legend).toContain('not a forecast or a recommendation');
  });
});

describe('Strength Score badge and breakdown', () => {
  it('shows the score with its band, the five weighted parts, and never a 0 when withheld', () => {
    const { rerender } = render(<ScoreBreakdown row={row('AAA')} />);
    const parts = screen.getAllByTestId('score-part').map((p) => p.textContent);
    expect(parts).toEqual(['Relative strength 35%95', 'Trend 25%100', 'Momentum 20%88', 'Volume 10%60', 'Liquidity 10%80']);
    rerender(<ScoreBreakdown row={row('BBB', { strength_score: null, strength_reason: 'fewer than 20 sessions of trading', strength_trend: null })} />);
    expect(screen.getByTestId('score-reason').textContent).toBe('No score: fewer than 20 sessions of trading');
    expect(screen.getAllByTestId('score-part')[1].textContent).toBe('Trend 25%—');
    render(<ScoreBadge row={row('CCC', { strength_score: 85.4 })} />);
    expect(screen.getAllByTestId('score-badge')[0].getAttribute('aria-label')).toBe('Strength Score 85 of 100 (very strong)');
  });
});
