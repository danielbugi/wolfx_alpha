// Watchlist performance view: default selection (first 3), add/remove with a 6-symbol maximum, bounded and cached
// requests (period changes refetch nothing), the chart gets rebased series + a dashed S&P 500, ranked bars, cards, and
// honest states for partial history, discontinuities, unavailable data and an older server; the equal-weight summary,
// sector mix, last-close strip and the chart tooltip.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';

const api = vi.hoisted(() => ({ stock: vi.fn(), benchmark: vi.fn() }));
const chart = vi.hoisted(() => ({ data: [] as Record<string, unknown>[], lines: [] as { dataKey: string; dash?: string }[] }));
vi.mock('@/services/performanceApi', () => ({ performanceApi: api }));
vi.mock('recharts', () => {
  const Pass = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  return {
    ResponsiveContainer: Pass,
    LineChart: ({ data, children }: { data: Record<string, unknown>[]; children?: React.ReactNode }) => {
      chart.data = data;
      chart.lines = [];
      return <div data-testid="chart">{children}</div>;
    },
    Line: ({ dataKey, strokeDasharray }: { dataKey: string; strokeDasharray?: string }) => {
      chart.lines.push({ dataKey, dash: strokeDasharray });
      return null;
    },
    CartesianGrid: () => null, XAxis: () => null, YAxis: () => null, Tooltip: () => null, ReferenceLine: () => null,
  };
});

import WatchlistPerformance, { ChartTip } from '@/components/watchlist/WatchlistPerformance';
import { computePerformance } from '@/lib/performance';
import type { ScreenerRow } from '@/services/screenerV2Api';

function sessions(from: string, to: string): string[] {
  const out: string[] = [];
  for (let d = new Date(`${from}T00:00:00Z`); d <= new Date(`${to}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + 1)) {
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) out.push(d.toISOString().slice(0, 10));
  }
  return out;
}
const CAL = sessions('2025-09-01', '2026-10-16');
const bench = CAL.map((d, i) => ({ date: d, close: 100 + i * 0.05 }));
const hist = (symbol: string, f: (i: number, d: string) => number | null, opts: { from?: string; disc?: { date: string }[] | undefined; noDisc?: boolean } = {}) => ({
  symbol,
  bars: CAL.filter((d) => !opts.from || d >= opts.from).map((d, i) => ({ date: d, close: f(i, d) })),
  discontinuities: opts.disc ?? [],
  discontinuitiesChecked: !opts.noDisc,
});

const SAVED = ['AAA', 'BBB', 'CCC', 'DDD', 'EEE', 'FFF', 'GGG'];
const DATA: Record<string, ReturnType<typeof hist>> = {
  AAA: hist('AAA', (i) => 50 + i * 0.2),
  BBB: hist('BBB', (i) => 80 - i * 0.05),
  CCC: hist('CCC', (i) => 20 + i * 0.01),
  DDD: hist('DDD', (i) => 30 + i * 0.03),
  EEE: hist('EEE', (i) => 40 + i * 0.04),
  FFF: hist('FFF', (i) => 60 + i * 0.06),
  GGG: hist('GGG', (i) => 70 + i * 0.07),
};

beforeEach(() => {
  window.localStorage.clear();
  api.stock.mockReset();
  api.benchmark.mockReset();
  api.stock.mockImplementation(async (s: string) => DATA[s]);
  api.benchmark.mockResolvedValue(bench);
});

const chip = (s: string) => screen.getByRole('button', { name: s, pressed: undefined });

describe('WatchlistPerformance', () => {
  it('starts with the first 3 saved symbols, fetches only those plus the S&P 500, and charts them from 0% with a dashed benchmark', async () => {
    render(<WatchlistPerformance saved={SAVED} />);
    await waitFor(() => expect(screen.getAllByTestId('perf-card')).toHaveLength(3));
    expect(api.stock.mock.calls.map((c) => c[0])).toEqual(['AAA', 'BBB', 'CCC']);
    expect(api.benchmark).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(chart.lines.map((l) => l.dataKey)).toEqual(['SPX', 'AAA', 'BBB', 'CCC']));
    expect(chart.lines[0].dash).toBeTruthy();
    expect(chart.data[0]).toMatchObject({ AAA: 0, BBB: 0, CCC: 0, SPX: 0 });
    expect(screen.getByTestId('perf-window').textContent).toContain('2026-10-16');
    expect(chip('AAA').getAttribute('aria-pressed')).toBe('true');
    expect(chip('DDD').getAttribute('aria-pressed')).toBe('false');
  });

  it('changes the period without any new request, and the window follows (YTD from the last 2025 session)', async () => {
    render(<WatchlistPerformance saved={SAVED} />);
    await waitFor(() => expect(chart.lines).toHaveLength(4));
    const before = api.stock.mock.calls.length;
    fireEvent.click(screen.getByRole('radio', { name: 'YTD' }));
    await waitFor(() => expect(chart.data[0].date).toBe('2025-12-31'));
    fireEvent.click(screen.getByRole('radio', { name: '1M' }));
    await waitFor(() => expect(chart.data[0].date).toBe('2026-09-16'));
    expect(api.stock.mock.calls.length).toBe(before);
    expect(api.benchmark).toHaveBeenCalledTimes(1);
  });

  it('adds and removes chart symbols, refuses a 7th with a message, and remembers the selection', async () => {
    const { unmount } = render(<WatchlistPerformance saved={SAVED} />);
    await waitFor(() => expect(screen.getAllByTestId('perf-card')).toHaveLength(3));
    for (const s of ['DDD', 'EEE', 'FFF']) fireEvent.click(chip(s));
    await waitFor(() => expect(screen.getAllByTestId('perf-card')).toHaveLength(6));
    fireEvent.click(chip('GGG'));
    expect(screen.getByTestId('limit-note').textContent).toBe('Up to 6 stocks at once. Remove one to add another.');
    expect(chip('GGG').getAttribute('aria-pressed')).toBe('false');
    expect(api.stock.mock.calls.map((c) => c[0])).not.toContain('GGG');
    fireEvent.click(screen.getByRole('button', { name: 'Remove BBB from the chart' }));
    await waitFor(() => expect(screen.getAllByTestId('perf-card')).toHaveLength(5));
    expect(JSON.parse(window.localStorage.getItem('watchlist_chart_selection')!)).toEqual(['AAA', 'CCC', 'DDD', 'EEE', 'FFF']);
    unmount();
    render(<WatchlistPerformance saved={['AAA', 'DDD', 'ZZZ']} />); // a removed saved symbol drops out of the remembered selection
    await waitFor(() => expect(screen.getAllByTestId('perf-card').map((c) => within(c).getAllByRole('link')[0].textContent)).toEqual(['AAA', 'DDD']));
  });

  it('ranks the selected stocks by period return with the S&P 500 as a reference row', async () => {
    render(<WatchlistPerformance saved={SAVED} />);
    await waitFor(() => expect(screen.getAllByTestId('rank-row')).toHaveLength(3));
    await waitFor(() => expect(screen.getAllByTestId('rank-row').map((r) => r.textContent?.slice(0, 3))).toEqual(['AAA', 'CCC', 'BBB']));
    expect(screen.getByTestId('rank-bench').textContent).toContain('S&P 500');
    const card = screen.getAllByTestId('perf-card')[0];
    expect(card.textContent).toMatch(/Return\+\d+\.\d%/);
    expect(card.textContent).toMatch(/vs S&P 500[+-]\d+\.\d pp/);
    expect(within(card).getByTestId('sparkline')).toBeTruthy();
  });

  it('flags a stock that listed after the period start, and withholds a return across a recorded discontinuity', async () => {
    DATA.AAA = hist('AAA', (i) => 10 + i, { from: '2026-08-03' });
    DATA.BBB = hist('BBB', (i) => 80 + i, { disc: [{ date: '2026-07-01' }] });
    render(<WatchlistPerformance saved={SAVED} />);
    await waitFor(() => expect(screen.getAllByTestId('coverage-note')).toHaveLength(2));
    const notes = screen.getAllByTestId('coverage-note').map((n) => n.textContent);
    expect(notes[0]).toBe('AAA: history starts 2026-08-03, after the period start 2026-04-16 (not directly comparable over the full period)');
    expect(notes[1]).toBe('BBB: a price discontinuity is recorded on 2026-07-01; no return shown');
    expect(screen.getByText('(not drawn)').parentElement?.textContent).toContain('BBB');
    expect(screen.getByTestId('perf-window').textContent).toBe('% change from the close of 2026-04-16 to 2026-10-16 · S&P 500 dashed');
    await waitFor(() => expect(chart.lines.map((l) => l.dataKey)).toEqual(['SPX', 'AAA', 'CCC'])); // BBB not drawn
    const rows = screen.getAllByTestId('rank-row');
    expect(rows[rows.length - 1].textContent).toContain('BBB');
    expect(rows[rows.length - 1].textContent).toContain('—');
    expect(screen.getAllByTestId('perf-card')[0].textContent).toContain('shorter window');
    DATA.AAA = hist('AAA', (i) => 50 + i * 0.2);
    DATA.BBB = hist('BBB', (i) => 80 - i * 0.05);
  });

  it('keeps working when the S&P 500 or a stock history is unavailable, without inventing relative figures', async () => {
    api.benchmark.mockRejectedValue(new Error('down'));
    api.stock.mockImplementation(async (s: string) => {
      if (s === 'BBB') throw new Error('down');
      return DATA[s];
    });
    render(<WatchlistPerformance saved={SAVED} />);
    await waitFor(() => expect(screen.getByText(/S&P 500 history is unavailable right now/)).toBeTruthy());
    await waitFor(() => expect(chart.lines.map((l) => l.dataKey)).toEqual(['AAA', 'CCC']));
    expect(screen.getAllByTestId('perf-card')[0].textContent).toContain('vs S&P 500—');
    expect(screen.getAllByTestId('perf-card')[1].textContent).toContain('Price history unavailable right now.');
    expect(screen.queryByTestId('rank-bench')).toBeNull();
  });

  it('says when an older server did not report discontinuities, and states the price-return basis', async () => {
    DATA.CCC = hist('CCC', (i) => 20 + i * 0.01, { noDisc: true });
    const { container } = render(<WatchlistPerformance saved={SAVED} />);
    await waitFor(() => expect(container.textContent).toContain('Price discontinuities were not checked for CCC'));
    expect(container.textContent).toContain('not a total return');
    expect(container.textContent).toContain('price index without dividends');
    DATA.CCC = hist('CCC', (i) => 20 + i * 0.01);
  });

  it('asks for a selection when nothing is selected', async () => {
    render(<WatchlistPerformance saved={SAVED} />);
    await waitFor(() => expect(screen.getAllByTestId('perf-card')).toHaveLength(3));
    for (const s of ['AAA', 'BBB', 'CCC']) fireEvent.click(chip(s));
    expect(screen.getByText('Pick at least one stock above to chart it.')).toBeTruthy();
    expect(screen.queryByTestId('ranked')).toBeNull();
  });

  it('summarises the equal-weight basket of the comparable stocks against the S&P 500, with best and worst', async () => {
    render(<WatchlistPerformance saved={SAVED} />);
    await waitFor(() => expect(screen.getByTestId('beat').textContent).toBe('2 of 3'));
    const r = computePerformance('6m', bench, ['AAA', 'BBB', 'CCC'].map((s) => DATA[s]));
    const mean = (r.series.reduce((a, s) => a + (s.ret as number), 0) / 3).toFixed(2);
    expect(screen.getByTestId('basket-ret').textContent).toBe(`+${mean}%`);
    expect(screen.getByTestId('basket-vs').textContent).toMatch(/^[+-]\d+\.\d pp$/);
    expect(screen.getByTestId('best-worst').textContent).toMatch(/^AAA \+.*BBB -/);
    const note = screen.getByTestId('basket-note').textContent;
    expect(note).toContain('The plain average of 3 stocks with a close on both 2026-04-16 and 2026-10-16 (AAA, BBB, CCC)');
    expect(note).toContain('A reference for the list, not a portfolio.');
    expect(note).toContain("measures today's list backwards in time");
    expect(note).not.toContain('Not included');
  });

  it('leaves a late starter or a discontinuity out of the basket and says so', async () => {
    DATA.AAA = hist('AAA', (i) => 10 + i, { from: '2026-08-03' });
    DATA.BBB = hist('BBB', (i) => 80 + i, { disc: [{ date: '2026-07-01' }] });
    render(<WatchlistPerformance saved={SAVED} />);
    await waitFor(() => expect(screen.getByTestId('basket-note').textContent).toContain('Not included: AAA (no full-period return), BBB (no full-period return).'));
    expect(screen.getByTestId('basket-note').textContent).toContain('The plain average of 1 stock with a close on both');
    const ccc = computePerformance('6m', bench, [DATA.CCC]).series[0];
    expect(screen.getByTestId('beat').textContent).toBe(`${(ccc.vsBench as number) > 0 ? 1 : 0} of 1`); // only CCC is a member
    DATA.AAA = hist('AAA', (i) => 50 + i * 0.2);
    DATA.BBB = hist('BBB', (i) => 80 - i * 0.05);
  });

  it('names a stock whose history failed to load as excluded, instead of dropping it silently', async () => {
    api.stock.mockImplementation(async (s: string) => {
      if (s === 'BBB') throw new Error('down');
      return DATA[s];
    });
    render(<WatchlistPerformance saved={SAVED} />);
    await waitFor(() => expect(screen.getByTestId('basket-note').textContent).toContain('Not included: BBB (history unavailable).'));
    expect(screen.getByTestId('basket-note').textContent).toContain('(AAA, CCC)');
  });

  it('shows no partial average while a selected history is still loading', async () => {
    let release: (v: unknown) => void = () => undefined;
    api.stock.mockImplementation((s: string) => (s === 'CCC' ? new Promise((r) => { release = r; }) : Promise.resolve(DATA[s])));
    render(<WatchlistPerformance saved={SAVED} />);
    await waitFor(() => expect(chart.lines.map((l) => l.dataKey)).toEqual(['SPX', 'AAA', 'BBB']));
    expect(screen.getByTestId('basket-ret').textContent).toBe('…');
    expect(screen.getByTestId('beat').textContent).toBe('…');
    expect(screen.getByTestId('basket-note').textContent).toBe('Waiting for every selected stock before averaging.');
    release(DATA.CCC);
    await waitFor(() => expect(screen.getByTestId('basket-note').textContent).toContain('(AAA, BBB, CCC)'));
  });

  it('shows no relative summary without the S&P 500', async () => {
    api.benchmark.mockRejectedValue(new Error('down'));
    render(<WatchlistPerformance saved={SAVED} />);
    await waitFor(() => expect(screen.getAllByTestId('rank-row')).toHaveLength(3));
    expect(screen.getByTestId('basket-vs').textContent).toBe('—');
    expect(screen.getByTestId('beat').textContent).toBe('—');
  });

  it('with the Screener rows: a last-close strip for every saved symbol, the sector mix of the whole list, and closes on the cards', async () => {
    const rows: Record<string, ScreenerRow> = {
      AAA: { symbol: 'AAA', sector: 'Technology', close: 12.5, change_1d: 1.25 } as ScreenerRow,
      BBB: { symbol: 'BBB', sector: 'Energy', close: 80, change_1d: -0.5 } as ScreenerRow,
      CCC: { symbol: 'CCC', sector: 'Technology', close: 20, change_1d: null } as ScreenerRow,
      DDD: { symbol: 'DDD', sector: null, close: 30, change_1d: 0 } as ScreenerRow,
    };
    render(<WatchlistPerformance saved={['AAA', 'BBB', 'CCC', 'DDD']} rows={rows} session="2026-10-16" />);
    await waitFor(() => expect(screen.getAllByTestId('perf-card')).toHaveLength(3));
    const items = screen.getAllByTestId('ticker-item');
    expect(items.map((i) => i.textContent)).toEqual(['AAA$12.50+1.25%', 'BBB$80.00-0.50%', 'CCC$20.00', 'DDD$30.000.00%']);
    expect(screen.getByText('Closes and day change for the 2026-10-16 session.')).toBeTruthy();
    expect(screen.getAllByTestId('sector-row').map((r) => r.textContent)).toEqual(['Technology2 · 50%', 'Energy1 · 25%', 'Unclassified1 · 25%']);
    expect(screen.getByTestId('sector-mix').textContent).toContain("today's sector classification (not as of past dates)");
    expect(screen.getAllByTestId('card-close').map((c) => c.textContent)).toEqual(['$12.50', '$80.00', '$20.00']);
    expect(screen.getAllByTestId('rank-row')[0].textContent).toContain('+1.25%');
  });

  it('without Screener rows there is no strip, no sector mix and no invented close', async () => {
    render(<WatchlistPerformance saved={SAVED} />);
    await waitFor(() => expect(screen.getAllByTestId('perf-card')).toHaveLength(3));
    expect(screen.queryByTestId('ticker-item')).toBeNull();
    expect(screen.queryByTestId('sector-mix')).toBeNull();
    expect(screen.queryByTestId('card-close')).toBeNull();
  });

  it('states the baseline rule for the chosen period', async () => {
    render(<WatchlistPerformance saved={SAVED} />);
    await waitFor(() => expect(screen.getByTestId('baseline-rule').textContent).toContain('the same date 6M earlier'));
    fireEvent.click(screen.getByRole('radio', { name: 'YTD' }));
    expect(screen.getByTestId('baseline-rule').textContent).toContain('the same date at the end of last year');
    expect(screen.getByTestId('baseline-rule').textContent).toContain('A missing close is a gap, never filled.');
  });
});

describe('ChartTip', () => {
  const r = computePerformance('1m', bench, [
    { symbol: 'AAA', bars: DATA.AAA.bars },
    { symbol: 'BBB', bars: DATA.BBB.bars },
  ]);
  const theme = { tipBg: '#fff', tipText: '#000', spx: '#888' };
  const last = CAL[CAL.length - 1];

  it('lists each line highest first, the S&P 500, and the biggest move that session', () => {
    render(<ChartTip active label={last} result={r} symbols={['BBB', 'AAA']} colorOf={() => '#000'} theme={theme} />);
    const tip = screen.getByTestId('chart-tip');
    expect(tip.textContent).toMatch(/^Fri, Oct 16, 2026AAA\+.*BBB-.*S&P 500\+/);
    expect(screen.getByTestId('chart-tip-mover').textContent).toMatch(/^Biggest move that session: AAA \+0\.\d\d%$/);
  });

  it('has no mover on the first session of the window, and renders nothing when inactive', () => {
    const { container } = render(<ChartTip active label={r.baseline!} result={r} symbols={['AAA', 'BBB']} colorOf={() => '#000'} theme={theme} />);
    expect(screen.queryByTestId('chart-tip-mover')).toBeNull();
    expect(container.textContent).toContain('AAA0.0%');
    const off = render(<ChartTip active={false} label={last} result={r} symbols={['AAA']} colorOf={() => '#000'} theme={theme} />);
    expect(off.container.innerHTML).toBe('');
  });
});
