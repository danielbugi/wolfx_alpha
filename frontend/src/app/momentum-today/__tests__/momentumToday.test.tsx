// Momentum Today: three boards from the Screener's shared engine (paged until complete), ranked by the session's lists like
// the digest (top 5 in 2+ lists first, then the 1-day gain; never with another session's ranks), cells coloured by the
// Strength Score (grey "—" with the reason when withheld), where the breakouts come from, the star, and unavailable states.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { QueryPairs, ScreenerResult, ScreenerRow } from '@/services/screenerV2Api';

const api = vi.hoisted(() => ({ search: vi.fn(), definitions: vi.fn(), exportCsv: vi.fn() }));
vi.mock('@/services/screenerV2Api', () => ({ screenerV2Api: api }));
const lists = vi.hoisted(() => ({ getDayLists: vi.fn() }));
vi.mock('@/services/dayListsApi', () => lists);

import MomentumTodayPage from '@/app/momentum-today/page';
import { ScoreBadge, ScoreBreakdown } from '@/components/screener/StrengthScore';

const row = (symbol: string, over: Partial<ScreenerRow> = {}) =>
  ({
    symbol, sector: 'Technology', industry: 'Software', change_1d: 2.1, strength_score: 91.3, strength_reason: null,
    strength_rs: 95, strength_trend: 100, strength_momentum: 88, strength_volume: 60, strength_liquidity: 80, ...over,
  }) as ScreenerRow;
const result = (rows: ScreenerRow[], total = rows.length) => ({ session: '2026-10-20', universe: 3060, total, rows }) as unknown as ScreenerResult;
const has = (pairs: QueryPairs, k: string, v: string) => pairs.some(([a, b]) => a === k && b === v);
const dayLists = (session: string, entries: Record<string, [Record<string, number>, boolean]>) => ({
  session,
  bySymbol: new Map(Object.entries(entries).map(([s, [list_ranks, top_two]]) => [s, { list_ranks, top_two }])),
});

beforeEach(() => {
  window.localStorage.clear();
  api.search.mockReset();
  lists.getDayLists.mockReset();
  lists.getDayLists.mockResolvedValue(dayLists('2026-10-20', { BBB: [{ gainers: 2, volume: 4 }, true], CCC: [{ atr: 9 }, false] }));
  api.search.mockImplementation(async (pairs: QueryPairs) => {
    if (!has(pairs, 'page', '1')) return result([], 0); // later pages: nothing more in these fixtures
    if (has(pairs, 'breakout_20', 'true'))
      return result([row('AAA'), row('BBB', { sector: 'Energy', strength_score: 35.2, change_1d: 1.0 }), row('CCC', { strength_score: null, strength_reason: 'illiquid (under $1M a day)', change_1d: 5.0 })], 142);
    if (has(pairs, 'new_high_20', 'true')) return result([row('DDD', { strength_score: 64 })], 57);
    return result([row('EEE', { strength_score: 96.5 })], 155);
  });
});

describe('Momentum Today', () => {
  it('asks the shared engine once per board and shows the session counts', async () => {
    render(<MomentumTodayPage />);
    await waitFor(() => expect(screen.getByTestId('momentum-headline').textContent).toContain('142'));
    const calls = api.search.mock.calls.map((c) => c[0] as QueryPairs).filter((p) => has(p, 'page', '1'));
    expect(calls).toHaveLength(3);
    expect(calls.some((p) => has(p, 'breakout_20', 'true') && has(p, 'sort', 'strength_score'))).toBe(true);
    expect(calls.some((p) => has(p, 'new_high_20', 'true') && has(p, 'breakout_20', 'false'))).toBe(true);
    expect(calls.some((p) => has(p, 'min_strength_score', '80'))).toBe(true);
    const head = screen.getByTestId('momentum-headline').textContent!;
    expect(head).toContain('57');
    expect(head).toContain('155');
    expect(head).toContain('of 3,060 stocks on 2026-10-20');
  });

  it('ranks like the digest: top 5 in 2+ lists first, then the biggest 1-day gain, with each list rank shown', async () => {
    render(<MomentumTodayPage />);
    const board = await screen.findByTestId('momentum-board');
    await waitFor(() => expect(within(board).getAllByTestId('momentum-lists')[0].textContent).toBe('Gainers #2Volume #4'));
    const symbols = within(board).getAllByTestId('momentum-symbol').map((x) => x.textContent);
    expect(symbols).toEqual(['BBB', 'CCC', 'AAA']); // BBB is top 5 in two lists; then CCC +5.0% before AAA +2.1%
    const ranks = within(board).getAllByTestId('momentum-rank').map((x) => x.firstElementChild as HTMLElement);
    expect(ranks.map((x) => x.textContent)).toEqual(['1', '2', '3']);
    expect(ranks[0].getAttribute('data-top-two')).toBe('true');
    expect(ranks[1].getAttribute('data-top-two')).toBeNull();
    const cells = within(board).getAllByTestId('momentum-lists').map((x) => x.textContent);
    expect(cells).toEqual(['Gainers #2Volume #4', 'Range #9', '—']);
    expect(screen.queryByTestId('momentum-lists-note')).toBeNull();
  });

  it('never mixes list ranks from another session, and says why the order falls back to the 1-day gain', async () => {
    lists.getDayLists.mockResolvedValue(dayLists('2026-10-17', { BBB: [{ gainers: 1, volume: 1 }, true] }));
    render(<MomentumTodayPage />);
    const note = await screen.findByTestId('momentum-lists-note');
    expect(note.textContent).toBe('The list ranks on hand are for 2026-10-17, not 2026-10-20, so the order uses the 1-day gain only.');
    const board = screen.getByTestId('momentum-board');
    expect(within(board).getAllByTestId('momentum-symbol').map((x) => x.textContent)).toEqual(['CCC', 'AAA', 'BBB']);
    expect(within(board).getAllByTestId('momentum-lists').map((x) => x.textContent)).toEqual(['—', '—', '—']);
  });

  it('pages through the shared engine until a board is complete', async () => {
    api.search.mockImplementation(async (pairs: QueryPairs) => {
      if (!has(pairs, 'breakout_20', 'true')) return result([], 0);
      if (has(pairs, 'page', '1')) return result(Array.from({ length: 200 }, (_, i) => row(`P${i}`)), 230);
      if (has(pairs, 'page', '2')) return result(Array.from({ length: 30 }, (_, i) => row(`Q${i}`)), 230);
      return result([], 230);
    });
    render(<MomentumTodayPage />);
    const board = await screen.findByTestId('momentum-board');
    await waitFor(() => expect(within(board).getByTestId('momentum-showing').textContent).toBe('Showing 50 of 230'));
  });

  it('shows one table with tabs, shaded on one green scale with the strength icon and the reason when withheld', async () => {
    render(<MomentumTodayPage />);
    const board = await screen.findByTestId('momentum-board');
    await waitFor(() => expect(within(board).getAllByTestId('momentum-row')).toHaveLength(3));
    const byRow = (s: string) => within(board).getAllByTestId('momentum-row').find((r) => within(r).getByTestId('momentum-symbol').textContent === s)!;
    const [a, b, c] = [byRow('AAA'), byRow('BBB'), byRow('CCC')];
    const shade = (el: HTMLElement) =>
      Number(/rgba\(5, 150, 105, ([\d.]+)\)/.exec((within(el).getByTestId('momentum-score').closest('td') as HTMLElement).style.background)?.[1]);
    expect(within(a).getByTestId('momentum-score').textContent).toBe('91');
    expect(shade(a)).toBeGreaterThan(shade(b)); // 91.3 is a deeper green than 35.2
    expect(within(a).getByTestId('strength-icon').getAttribute('data-filled')).toBe('5');
    expect(within(b).getByTestId('strength-icon').getAttribute('data-filled')).toBe('2');
    expect(within(c).getByTestId('momentum-score').textContent).toBe('—');
    expect(within(c).getByTestId('momentum-score').getAttribute('title')).toBe('No Strength Score: illiquid (under $1M a day)');
    expect(within(a).getByTestId('momentum-group').textContent).toBe('Breakout');
    expect(within(board).getByRole('link', { name: 'Open in Screener →' }).getAttribute('href')).toBe('/screener?breakout_20=true&sort=strength_score&order=desc&view=cards');
    expect(within(board).getByRole('radio', { name: 'Broke out 142' })).toBeTruthy();
    expect(within(board).getByTestId('momentum-table').parentElement!.className).toContain('overflow-x-auto');
  });

  it('sorts by any column like the Momentum Board (blanks always last) and switches lists with the tabs or the headline cards', async () => {
    render(<MomentumTodayPage />);
    const board = await screen.findByTestId('momentum-board');
    await waitFor(() => expect(within(board).getAllByTestId('momentum-row')).toHaveLength(3));
    const symbols = () => within(board).getAllByTestId('momentum-symbol').map((x) => x.textContent);
    await waitFor(() => expect(symbols()).toEqual(['BBB', 'CCC', 'AAA'])); // default: ranked by the session's lists
    fireEvent.click(within(board).getByRole('button', { name: /^Strength/ }));
    expect(symbols()).toEqual(['AAA', 'BBB', 'CCC']); // strongest first, the withheld score last
    fireEvent.click(within(board).getByRole('button', { name: /^Strength/ }));
    expect(symbols()).toEqual(['BBB', 'AAA', 'CCC']); // ascending, the withheld score still last
    fireEvent.click(within(board).getByRole('button', { name: /^Symbol/ }));
    expect(symbols()).toEqual(['AAA', 'BBB', 'CCC']);
    fireEvent.click(within(board).getByRole('radio', { name: /^New highs/ }));
    await waitFor(() => expect(symbols()).toEqual(['DDD']));
    expect(within(board).getByTestId('momentum-group').textContent).toBe('New high');
    fireEvent.click(within(board).getByRole('radio', { name: /^All/ }));
    await waitFor(() => expect(symbols().sort()).toEqual(['AAA', 'BBB', 'CCC', 'DDD', 'EEE']));
    expect(within(board).getByRole('radio', { name: 'All 5' })).toBeTruthy();
    fireEvent.click(within(screen.getByTestId('momentum-headline')).getAllByRole('button')[2]); // "Strongest today" card
    await waitFor(() => expect(symbols()).toEqual(['EEE']));
  });

  it('shows 50 rows first, then more on request', async () => {
    api.search.mockImplementation(async (pairs: QueryPairs) =>
      has(pairs, 'breakout_20', 'true') ? result(Array.from({ length: 120 }, (_, i) => row(`S${i}`, { strength_score: 99 - i * 0.5 })), 120) : result([], 0));
    render(<MomentumTodayPage />);
    const board = await screen.findByTestId('momentum-board');
    await waitFor(() => expect(within(board).getAllByTestId('momentum-row')).toHaveLength(50));
    fireEvent.click(within(board).getByRole('button', { name: 'Show 50 more' }));
    expect(within(board).getAllByTestId('momentum-row')).toHaveLength(100);
    fireEvent.click(within(board).getByRole('button', { name: 'Show 20 more' }));
    expect(within(board).getAllByTestId('momentum-row')).toHaveLength(120);
  });

  it('still shows the table, ordered by the 1-day gain, when the list ranks are unavailable', async () => {
    lists.getDayLists.mockRejectedValue(new Error('down'));
    render(<MomentumTodayPage />);
    expect((await screen.findByTestId('momentum-lists-note')).textContent).toBe("The session's list ranks are unavailable right now, so the order uses the 1-day gain only.");
    expect(within(screen.getByTestId('momentum-board')).getAllByTestId('momentum-symbol').map((x) => x.textContent)).toEqual(['CCC', 'AAA', 'BBB']);
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
    expect(legend).toContain('pale (weak) to deep (strong)');
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
