// Fixtures are real responses from backend/routers/strategy_performance.py, generated from the backend service against a disposable
// schema: "perf_alpha_*" is a strategy with winners/stops/expired/open signals, "perf_empty_*" one with no signals at all.
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import PerformanceBreakdownsPanel from '@/components/strategies/PerformanceBreakdowns';
import { strategyApi } from '@/services/strategyApi';
import type { PerformanceBreakdowns, PerformanceOverview } from '@/services/strategyApi';
import alphaOverviewJson from './fixtures/perf_alpha_overview.json';
import alphaBreakdownsJson from './fixtures/perf_alpha_breakdowns.json';
import emptyOverviewJson from './fixtures/perf_empty_overview.json';
import emptyBreakdownsJson from './fixtures/perf_empty_breakdowns.json';

const fx = <T,>(json: unknown): T => structuredClone(json) as T;

vi.mock('@/services/strategyApi', async (orig) => {
  const mod = await orig<typeof import('@/services/strategyApi')>();
  return { ...mod, strategyApi: { ...mod.strategyApi, performanceOverview: vi.fn(), performanceBreakdowns: vi.fn() } };
});

const overview = vi.mocked(strategyApi.performanceOverview);
const breakdowns = vi.mocked(strategyApi.performanceBreakdowns);

const serve = (o: unknown, b: unknown) => {
  overview.mockResolvedValue(fx<PerformanceOverview>(o));
  breakdowns.mockResolvedValue(fx<PerformanceBreakdowns>(b));
};
const renderPanel = (key = 'alpha_trend', version = '1') => render(<PerformanceBreakdownsPanel strategyKey={key} version={version} />);

beforeEach(() => { overview.mockReset(); breakdowns.mockReset(); });
afterEach(() => cleanup());

describe('PerformanceBreakdowns — values and N come from the API unchanged', () => {
  it('renders the first available dimension with counts, preliminary markers and N', async () => {
    serve(alphaOverviewJson, alphaBreakdownsJson);
    renderPanel();
    const row = (await screen.findByRole('rowheader', { name: 'bearish' })).closest('tr') as HTMLElement;
    expect(row.textContent).toContain('prelim · N=3');
    expect(screen.getByRole('tab', { name: 'Direction' }).getAttribute('aria-selected')).toBe('true');
    expect(overview).toHaveBeenCalledWith('alpha_trend', '1');
    expect(breakdowns).toHaveBeenCalledWith('alpha_trend', '1');
  });

  it('a genuine 0.0 win rate over a real sample shows 0.0%, a profit factor of 0 shows 0.00 (zero is a measurement here)', async () => {
    serve(alphaOverviewJson, alphaBreakdownsJson);
    renderPanel();
    const row = (await screen.findByRole('rowheader', { name: 'bearish' })).closest('tr') as HTMLElement;
    const win = row.querySelector('td[data-metric-state]') as HTMLElement;
    expect(win.textContent).toMatch(/^0(\.0)?%/);
    expect(row.textContent).toContain('0.00');
  });

  it('switching the dimension swaps the table without refetching', async () => {
    serve(alphaOverviewJson, alphaBreakdownsJson);
    renderPanel();
    await screen.findByRole('rowheader', { name: 'bearish' });
    fireEvent.click(screen.getByRole('tab', { name: /^Sector/ }));
    expect(screen.queryByRole('rowheader', { name: 'bearish' })).toBeNull();
    expect(screen.getByRole('tab', { name: /^Sector/ }).getAttribute('aria-selected')).toBe('true');
    expect(overview).toHaveBeenCalledTimes(1);
    expect(breakdowns).toHaveBeenCalledTimes(1);
  });
});

describe('PerformanceBreakdowns — missing information is never zero', () => {
  it('a not_available metric renders an em dash, never a number, and exposes its state', async () => {
    const b = fx<PerformanceBreakdowns>(alphaBreakdownsJson);
    const group = Object.values(b.dimensions).find((d) => d.groups.some((g) => g.profit_factor.state === 'not_available'));
    expect(group, 'fixture must contain a not_available profit factor').toBeTruthy();
    serve(alphaOverviewJson, alphaBreakdownsJson);
    renderPanel();
    await screen.findByRole('rowheader', { name: 'bearish' });
    fireEvent.click(screen.getByRole('tab', { name: group!.label }));
    await waitFor(() => expect(document.querySelector('td[data-metric-state="not_available"]')).toBeTruthy());
    document.querySelectorAll('td[data-metric-state="not_available"], td[data-metric-state="no_data"]').forEach((td) => {
      expect(td.textContent).toMatch(/^—/);
      expect(td.textContent).not.toMatch(/\d/);
    });
  });

  it('lists the dimensions and metrics the API declares unavailable, with what they require, and shows no table for them', async () => {
    serve(alphaOverviewJson, alphaBreakdownsJson);
    renderPanel();
    await screen.findByRole('rowheader', { name: 'bearish' });
    const dims = screen.getByTestId('unavailable-dimensions');
    for (const label of ['Market regime at signal', 'Relative strength at signal', 'Catalyst type']) expect(within(dims).getByText(label)).toBeTruthy();
    expect(dims.textContent).toContain('market_snapshot');
    expect(screen.queryByRole('tab', { name: /Market regime/ })).toBeNull();
    const metrics = screen.getByTestId('unavailable-metrics');
    expect(within(metrics).getAllByRole('listitem')).toHaveLength(4);
    expect(metrics.textContent).not.toMatch(/\b0(\.0+)?\b/);
  });

  it('exit rules the strategy does not declare are "Not available" with the API reason, not a default rule', async () => {
    serve(alphaOverviewJson, alphaBreakdownsJson);
    renderPanel();
    const el = await screen.findByText(/Not available — no exit rules are declared/);
    expect(el.getAttribute('data-exit-rules')).toBe('not_available');
    expect(document.querySelector('[data-exit-rules="declared"]')).toBeNull();
  });

  it('declared exit rules are shown verbatim', async () => {
    const o = fx<PerformanceOverview>(alphaOverviewJson);
    (o as { exit_rules: unknown }).exit_rules = { state: 'declared', stop_atr_mult: 2, target_atr_mults: [1, 2, 3], expiry_bars: 20, r_unit: 'entry − stop' };
    overview.mockResolvedValue(o);
    breakdowns.mockResolvedValue(fx<PerformanceBreakdowns>(alphaBreakdownsJson));
    renderPanel();
    const dl = await waitFor(() => {
      const e = document.querySelector('[data-exit-rules="declared"]');
      if (!e) throw new Error('not yet');
      return e as HTMLElement;
    });
    expect(dl.textContent).toContain('2 × ATR');
    expect(dl.textContent).toContain('1 × ATR · 2 × ATR · 3 × ATR');
    expect(dl.textContent).toContain('20 sessions');
  });

  it('a strategy with no signals gets honest empty states, not zeros', async () => {
    serve(emptyOverviewJson, emptyBreakdownsJson);
    renderPanel('gamma_empty');
    expect(await screen.findByText(/No signals to group by/)).toBeTruthy();
    expect(document.querySelectorAll('[data-metric-state]')).toHaveLength(0);
    expect(screen.queryByText(/%/)).toBeNull();
    expect(screen.getByTestId('unavailable-dimensions').textContent).toContain('Catalyst');
  });
});

describe('PerformanceBreakdowns — failure handling', () => {
  it('a failed request shows an error with retry and no numbers; retry refetches', async () => {
    overview.mockRejectedValueOnce(new Error('boom'));
    breakdowns.mockResolvedValueOnce(fx<PerformanceBreakdowns>(alphaBreakdownsJson));
    renderPanel();
    expect(await screen.findByText('Could not load the performance breakdowns')).toBeTruthy();
    expect(document.querySelector('[data-metric-state]')).toBeNull();
    serve(alphaOverviewJson, alphaBreakdownsJson);
    fireEvent.click(screen.getByRole('button', { name: 'Try Again' }));
    expect(await screen.findByRole('rowheader', { name: 'bearish' })).toBeTruthy();
    expect(overview).toHaveBeenCalledTimes(2);
  });

  it('switching strategy never shows the previous strategy\'s numbers under the new name', async () => {
    serve(alphaOverviewJson, alphaBreakdownsJson);
    const { rerender } = renderPanel();
    await screen.findByRole('rowheader', { name: 'bearish' });
    serve(emptyOverviewJson, emptyBreakdownsJson);
    rerender(<PerformanceBreakdownsPanel strategyKey="gamma_empty" version="1" />);
    expect(await screen.findByText(/No signals to group by/)).toBeTruthy();
    expect(screen.queryByRole('rowheader', { name: 'bearish' })).toBeNull();
  });
});
