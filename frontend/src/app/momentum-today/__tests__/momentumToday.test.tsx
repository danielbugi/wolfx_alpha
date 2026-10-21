// Momentum Today: two views of the session from the Screener's shared engine (paged until complete).
//   Today's Movers   — top 5 in 2+ of the session's lists first, then the 1-day gain (never with another session's ranks);
//                      includes stocks that made the lists without being a breakout or new high.
//   Strength Leaders — Strength Score 80+, highest first, with the five parts.
// Both: why it's moving (earnings), size, quality, filters, sortable columns, green strength shading, star, unavailable states.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { QueryPairs } from '@/services/screenerV2Api';
import { dayLists, has, LISTS, result, row, searchMock } from './fixtures';

const api = vi.hoisted(() => ({ search: vi.fn(), definitions: vi.fn(), exportCsv: vi.fn() }));
vi.mock('@/services/screenerV2Api', () => ({ screenerV2Api: api }));
const lists = vi.hoisted(() => ({ getDayLists: vi.fn() }));
vi.mock('@/services/dayListsApi', () => lists);

import MomentumTodayPage from '@/app/momentum-today/page';
import { ScoreBadge, ScoreBreakdown } from '@/components/screener/StrengthScore';
import { applyFilters, catalyst, DEFAULT_FILTERS } from '@/lib/momentumToday';

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, '', '/momentum-today');
  api.search.mockReset();
  api.search.mockImplementation(async (pairs: QueryPairs) => searchMock(pairs));
  lists.getDayLists.mockReset();
  lists.getDayLists.mockResolvedValue(LISTS());
});

const board = () => screen.getByTestId('momentum-board');
const symbols = () => within(board()).getAllByTestId('momentum-symbol').map((x) => x.textContent);
const cells = (id: string) => within(board()).getAllByTestId(id).map((x) => x.textContent);
const rowOf = (s: string) => within(board()).getAllByTestId('momentum-row').find((x) => within(x).getByTestId('momentum-symbol').textContent === s)!;

describe('Momentum Today', () => {
  it('asks the shared engine once per board (page 1) and shows both views with their counts', async () => {
    render(<MomentumTodayPage />);
    await waitFor(() => expect(screen.getByTestId('view-movers').textContent).toContain('142'));
    const calls = api.search.mock.calls.map((c) => c[0] as QueryPairs).filter((p) => has(p, 'page', '1') && !p.some(([k]) => k === 'symbol'));
    expect(calls).toHaveLength(3);
    expect(calls.some((p) => has(p, 'breakout_20', 'true'))).toBe(true);
    expect(calls.some((p) => has(p, 'new_high_20', 'true') && has(p, 'breakout_20', 'false'))).toBe(true);
    expect(calls.some((p) => has(p, 'min_strength_score', '80'))).toBe(true);
    await waitFor(() => expect(screen.getByTestId('view-movers').textContent).toContain('2 top on 2+ lists'));
    expect(screen.getByTestId('view-movers').textContent).toContain("Today's Movers");
    expect(screen.getByTestId('view-movers').textContent).toContain('57 new highs');
    expect(screen.getByTestId('view-strength').textContent).toContain('155 stocks with Strength 80+');
    expect(screen.getByTestId('view-strength').textContent).toContain('of 3,060 on 2026-10-20');
  });

  it("Today's Movers: top 5 in 2+ lists first, then the biggest 1-day gain, including stocks only on the lists", async () => {
    render(<MomentumTodayPage />);
    await screen.findByTestId('momentum-board');
    await waitFor(() => expect(symbols()).toEqual(['FFF', 'BBB', 'CCC', 'AAA', 'DDD']));
    // FFF was fetched from the same engine by symbol, for the same session
    const bySymbol = api.search.mock.calls.map((c) => c[0] as QueryPairs).find((p) => p.some(([k]) => k === 'symbol'))!;
    expect(bySymbol).toEqual(expect.arrayContaining([['session', '2026-10-20'], ['symbol', 'FFF']]));
    const ranks = within(board()).getAllByTestId('momentum-rank').map((x) => x.firstElementChild as HTMLElement);
    expect(ranks.map((x) => x.textContent)).toEqual(['1', '2', '3', '4', '5']);
    expect(ranks.map((x) => x.getAttribute('data-top-two'))).toEqual(['true', 'true', null, null, null]);
    expect(cells('momentum-lists')).toEqual(['Gain #1Range #1', 'Gain #2Vol #4', 'Range #9', '—', '—']);
    expect(cells('momentum-group')).toEqual(['On the lists', 'Breakout', 'Breakout', 'Breakout', 'New high']);
    expect(screen.queryByTestId('momentum-lists-note')).toBeNull();
  });

  it("shows why it's moving (earnings), size and a quality check, with the company name when stored", async () => {
    render(<MomentumTodayPage />);
    await screen.findByTestId('momentum-board');
    await waitFor(() => expect(symbols()).toHaveLength(5));
    expect(within(rowOf('AAA')).getByTestId('momentum-catalyst').textContent).toBe('Earnings 1d ago · EPS +12% vs est.');
    expect(within(rowOf('BBB')).getByTestId('momentum-catalyst').textContent).toBe('Earnings in 1d');
    expect(within(rowOf('DDD')).getByTestId('momentum-catalyst').textContent).toBe('No earnings report');
    expect(within(rowOf('AAA')).getByTestId('momentum-name').textContent).toBe('Acme Analytics Inc.');
    expect(within(rowOf('DDD')).getByTestId('momentum-name').textContent).toBe('Software'); // no stored name: the industry
    expect(within(rowOf('AAA')).getByTestId('momentum-size').textContent).toBe('$5.00Bfloat 120.0M');
    expect(within(rowOf('AAA')).getByTestId('momentum-quality').textContent).toBe('BP/E 20.0');
    expect(within(rowOf('BBB')).getByTestId('momentum-quality').textContent).toBe('DP/S 3.0'); // no P/E: P/S
    expect(within(rowOf('CCC')).getByTestId('momentum-quality').textContent).toBe('—P/E 20.0');
  });

  it('never mixes list ranks from another session, and says why Movers fall back to the 1-day gain', async () => {
    lists.getDayLists.mockResolvedValue(dayLists('2026-10-17', { BBB: [{ gainers: 1, volume: 1 }, true], FFF: [{ gainers: 2, atr: 2 }, true] }));
    render(<MomentumTodayPage />);
    const note = await screen.findByTestId('momentum-lists-note');
    expect(note.textContent).toBe('The list ranks on hand are for 2026-10-17, not 2026-10-20, so Movers are ordered by the 1-day gain only.');
    expect(symbols()).toEqual(['CCC', 'AAA', 'BBB', 'DDD']);
    expect(cells('momentum-lists')).toEqual(['—', '—', '—', '—']);
    expect(api.search.mock.calls.some((c) => (c[0] as QueryPairs).some(([k]) => k === 'symbol'))).toBe(false);
  });

  it('still shows Movers, ordered by the 1-day gain, when the list ranks are unavailable', async () => {
    lists.getDayLists.mockRejectedValue(new Error('down'));
    render(<MomentumTodayPage />);
    expect((await screen.findByTestId('momentum-lists-note')).textContent).toBe("The session's list ranks are unavailable right now, so Movers are ordered by the 1-day gain only.");
    expect(symbols()).toEqual(['CCC', 'AAA', 'BBB', 'DDD']);
  });

  it('filters by list, liquidity, size, quality and upcoming earnings, re-ranking what is left', async () => {
    render(<MomentumTodayPage />);
    await screen.findByTestId('momentum-board');
    await waitFor(() => expect(symbols()).toHaveLength(5));
    fireEvent.click(within(board()).getByRole('radio', { name: /^Broke out/ }));
    expect(symbols()).toEqual(['BBB', 'CCC', 'AAA']);
    fireEvent.click(within(board()).getByRole('radio', { name: /^On the lists/ }));
    expect(symbols()).toEqual(['FFF']);
    fireEvent.click(within(board()).getByRole('radio', { name: /^All/ }));
    fireEvent.change(within(board()).getByLabelText('Quality'), { target: { value: 'B' } });
    expect(symbols()).toEqual(['AAA', 'DDD']); // B or better; a missing grade is excluded
    expect(cells('momentum-rank')).toEqual(['1', '2']);
    fireEvent.click(within(board()).getByRole('button', { name: 'Reset filters' }));
    fireEvent.click(within(board()).getByLabelText(/Hide earnings in the next 2 days/));
    expect(symbols()).toEqual(['FFF', 'CCC', 'AAA', 'DDD']); // BBB reports tomorrow
    fireEvent.change(within(board()).getByLabelText('Size'), { target: { value: 'large' } });
    expect(within(board()).getByTestId('momentum-empty').textContent).toBe('No stocks match these filters.');
  });

  it('Strength Leaders: Strength Score first with its five parts, and the view is kept in the URL', async () => {
    render(<MomentumTodayPage />);
    await screen.findByTestId('momentum-board');
    fireEvent.click(screen.getByTestId('view-strength'));
    await waitFor(() => expect(symbols()).toEqual(['EEE', 'AAA']));
    expect(window.location.search).toBe('?view=strength');
    expect(within(board()).getByRole('heading', { name: 'Strength Leaders' })).toBeTruthy();
    expect(cells('momentum-score')).toEqual(['97', '91']);
    expect(within(board()).getAllByTestId('part-strength_trend').map((x) => x.textContent)).toEqual(['100', '100']);
    expect(within(board()).queryByTestId('momentum-lists')).toBeNull();
    expect(within(board()).getByRole('link', { name: 'Open in Screener →' }).getAttribute('href')).toBe('/screener?preset=strongest&view=cards');
  });

  it('opens Strength Leaders from ?view=strength', async () => {
    window.history.replaceState(null, '', '/momentum-today?view=strength');
    render(<MomentumTodayPage />);
    await screen.findByTestId('momentum-board');
    await waitFor(() => expect(symbols()).toEqual(['EEE', 'AAA']));
  });

  it('sorts by any column (blanks always last), shaded on one green scale with the reason when withheld', async () => {
    render(<MomentumTodayPage />);
    await screen.findByTestId('momentum-board');
    await waitFor(() => expect(symbols()).toHaveLength(5));
    fireEvent.click(within(board()).getByRole('button', { name: /^Strength/ }));
    expect(symbols()).toEqual(['AAA', 'FFF', 'DDD', 'BBB', 'CCC']);
    fireEvent.click(within(board()).getByRole('button', { name: /^Strength/ }));
    expect(symbols()).toEqual(['BBB', 'DDD', 'FFF', 'AAA', 'CCC']); // ascending, the withheld score still last
    const shade = (el: HTMLElement) =>
      Number(/rgba\(5, 150, 105, ([\d.]+)\)/.exec((within(el).getByTestId('momentum-score').closest('td') as HTMLElement).style.background)?.[1]);
    expect(shade(rowOf('AAA'))).toBeGreaterThan(shade(rowOf('BBB')));
    expect(within(rowOf('AAA')).getByTestId('strength-icon').getAttribute('data-filled')).toBe('5');
    expect(within(rowOf('CCC')).getByTestId('momentum-score').getAttribute('title')).toBe('No Strength Score: illiquid (under $1M a day)');
    fireEvent.click(within(board()).getByRole('button', { name: /^Symbol/ }));
    expect(symbols()).toEqual(['AAA', 'BBB', 'CCC', 'DDD', 'FFF']);
    expect(within(board()).getByTestId('momentum-table').parentElement!.className).toContain('overflow-x-auto');
  });

  it('pages through the shared engine until a board is complete', async () => {
    api.search.mockImplementation(async (pairs: QueryPairs) => {
      if (!has(pairs, 'breakout_20', 'true')) return result([], 0);
      if (has(pairs, 'page', '1')) return result(Array.from({ length: 200 }, (_, i) => row(`P${i}`)), 230);
      if (has(pairs, 'page', '2')) return result(Array.from({ length: 30 }, (_, i) => row(`Q${i}`)), 230);
      return result([], 230);
    });
    lists.getDayLists.mockResolvedValue(dayLists('2026-10-20', {}));
    render(<MomentumTodayPage />);
    await screen.findByTestId('momentum-board');
    await waitFor(() => expect(within(board()).getByTestId('momentum-showing').textContent).toBe('Showing 50 of 230'));
    fireEvent.click(within(board()).getByRole('button', { name: 'Show 50 more' }));
    expect(within(board()).getAllByTestId('momentum-row')).toHaveLength(100);
  });

  it('shows which sectors the breakouts come from, and the star adds to the active watchlist', async () => {
    render(<MomentumTodayPage />);
    const sectors = await screen.findByTestId('momentum-sectors');
    expect(sectors.textContent).toContain('Technology 2');
    expect(sectors.textContent).toContain('Energy 1');
    await waitFor(() => expect(symbols()).toHaveLength(5));
    fireEvent.click(screen.getAllByRole('button', { name: 'Add AAA to watchlist' })[0]);
    expect(JSON.parse(window.localStorage.getItem('watchlist_symbols')!)).toEqual(['AAA']);
  });

  it('says when the data is unavailable', async () => {
    api.search.mockRejectedValue(new Error('down'));
    render(<MomentumTodayPage />);
    expect((await screen.findByTestId('momentum-error')).textContent).toBe('Momentum data is unavailable right now.');
  });

  it('describes the session only (legend states the rules, the weights and that it is not a forecast)', () => {
    render(<MomentumTodayPage />);
    const legend = screen.getByTestId('momentum-legend').textContent!;
    expect(legend).toContain('top 5 in two or more of the session');
    expect(legend).toContain('relative strength 35%, trend 25%, momentum 20%, volume 10%, liquidity 10%');
    expect(legend).toContain('not verified point-in-time');
    expect(legend).toContain('not a forecast or a recommendation');
  });
});

describe('Momentum Today helpers', () => {
  it('labels earnings: reported in the last 3 days (surprise kept only when plausible), due soon, upcoming or none', () => {
    expect(catalyst({ days_since_earnings: 0, earnings_surprise_pct: -5.2 }).label).toBe('Earnings on the session · EPS -5% vs est.');
    expect(catalyst({ days_since_earnings: 2, earnings_surprise_pct: -3326 }).label).toBe('Earnings 2d ago');
    expect(catalyst({ days_since_earnings: 4, days_to_earnings: 2 })).toMatchObject({ tone: 'soon', label: 'Earnings in 2d' });
    expect(catalyst({ days_to_earnings: 9 }).tone).toBe('upcoming');
    expect(catalyst({ days_to_earnings: 30 })).toMatchObject({ tone: 'none', label: 'No earnings report' });
  });

  it('a filter that is set excludes rows whose value is missing', () => {
    const rows = [row('X', { dollar_vol_20: null }), row('Y', { dollar_vol_20: 6e6 })];
    expect(applyFilters(rows, { ...DEFAULT_FILTERS, liquidity: '5m' }).map((r) => r.symbol)).toEqual(['Y']);
    expect(applyFilters(rows, DEFAULT_FILTERS)).toHaveLength(2);
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
