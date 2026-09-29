// Signal explorer + page shell against a mocked strategyApi (the real contract lives in the fixtures).
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { SignalDetail, SignalPage, StrategyListItem, StrategySummary, DataHealth } from '@/services/strategyApi';
import prodPageJson from './fixtures/prod_signals_page.json';
import prodDetailJson from './fixtures/prod_detail_463.json';
import heldDetailJson from './fixtures/demo_detail_held.json';
import strategiesJson from './fixtures/strategies.json';
import prodSummaryJson from './fixtures/prod_summary.json';
import prodHealthJson from './fixtures/prod_health.json';

const fixture = <T,>(json: unknown): T => structuredClone(json) as T;

const api = vi.hoisted(() => ({
  list: vi.fn(),
  summary: vi.fn(),
  dataHealth: vi.fn(),
  signals: vi.fn(),
  signal: vi.fn(),
}));

vi.mock('@/services/strategyApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/strategyApi')>();
  return { ...actual, strategyApi: api };
});

const nav = vi.hoisted(() => ({ search: '' }));
vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
  usePathname: () => '/strategies',
}));
vi.mock('@/hooks/usePagePerf', () => ({ usePagePerf: () => ({ markLoaded: () => {} }) }));
vi.mock('@/components/dashboard/SymbolHoverLink', () => ({ default: ({ symbol }: { symbol: string }) => <span>{symbol}</span> }));

import SignalsTab, { SIGNAL_PAGE_SIZE } from '@/components/strategies/SignalsTab';
import StrategiesPage from '@/app/strategies/page';

const page = (over: Partial<SignalPage> = {}): SignalPage => ({ ...fixture<SignalPage>(prodPageJson), ...over });
const axiosError = (status: number, detail: unknown) =>
  Object.assign(new Error(`HTTP ${status}`), { isAxiosError: true, response: { status, data: { detail } } });

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  nav.search = '';
});

describe('Signal explorer', () => {
  it('asks the server for one page and shows "1–N of total"', async () => {
    api.signals.mockResolvedValue(page({ total: 490, limit: 50, offset: 0, has_more: true }));
    render(<SignalsTab strategyKey="donchian_breakout" version="v1" />);
    await screen.findAllByText('1–5 of 490');
    expect(api.signals).toHaveBeenCalledWith('donchian_breakout', 'v1', expect.objectContaining({ limit: SIGNAL_PAGE_SIZE, offset: 0, sort: 'newest' }));
    expect(screen.getAllByRole('row')).toHaveLength(6); // header + 5 rows from the fixture
  });

  it('next page requests offset 50; a filter change resets to offset 0 and sends the filter', async () => {
    api.signals.mockResolvedValue(page({ total: 490, has_more: true }));
    render(<SignalsTab strategyKey="donchian_breakout" version="v1" />);
    await screen.findAllByText('1–5 of 490');
    fireEvent.click(screen.getAllByRole('button', { name: 'Next page' })[0]);
    await waitFor(() => expect(api.signals).toHaveBeenLastCalledWith('donchian_breakout', 'v1', expect.objectContaining({ offset: 50 })));
    fireEvent.change(screen.getByLabelText('Direction'), { target: { value: 'bullish' } });
    await waitFor(() => expect(api.signals).toHaveBeenLastCalledWith('donchian_breakout', 'v1', expect.objectContaining({ offset: 0, direction: 'bullish' })));
    fireEvent.change(screen.getByLabelText('Flag'), { target: { value: 'split_suspect' } });
    await waitFor(() => expect(api.signals).toHaveBeenLastCalledWith('donchian_breakout', 'v1', expect.objectContaining({ evaluation_flag: 'split_suspect', resolution_flag: undefined })));
  });

  it('opens a signal detail with Release-B provenance and no raw nulls', async () => {
    api.signals.mockResolvedValue(page());
    api.signal.mockResolvedValue(fixture<SignalDetail>(prodDetailJson));
    render(<SignalsTab strategyKey="donchian_breakout" version="v1" />);
    const first = fixture<SignalPage>(prodPageJson).items[0];
    fireEvent.click(await screen.findByRole('button', { name: `Open ${first.symbol} signal details` }));
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByText('Trade plan');
    expect(dialog.textContent).toContain('$46.11');
    expect(within(dialog).getAllByText('Not collected — Release B')).toHaveLength(3);
    expect(dialog.textContent).toContain('Not scored (no validated model)');
    expect(dialog.textContent).not.toMatch(/\bnull\b/i);
  });

  it('a held signal detail names the hold in its timeline', async () => {
    api.signals.mockResolvedValue(page());
    api.signal.mockResolvedValue(fixture<SignalDetail>(heldDetailJson));
    render(<SignalsTab strategyKey="zz_demo_states" version="v1" />);
    const first = fixture<SignalPage>(prodPageJson).items[0];
    fireEvent.click(await screen.findByRole('button', { name: `Open ${first.symbol} signal details` }));
    const dialog = await screen.findByRole('dialog');
    expect((await within(dialog).findByText(/Held — Split suspect/)).textContent).toContain('Held — Split suspect');
  });

  it('empty results distinguish "no signals" from "no match"', async () => {
    api.signals.mockResolvedValue(page({ items: [], total: 0, has_more: false }));
    render(<SignalsTab strategyKey="donchian_breakout" version="v1" />);
    expect(await screen.findByText('No signals recorded yet')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Grade'), { target: { value: 'A' } });
    expect(await screen.findByText('No signal matches these filters')).toBeTruthy();
  });

  it('an API failure shows an error with retry', async () => {
    api.signals.mockRejectedValueOnce(axiosError(500, 'boom')).mockResolvedValue(page());
    render(<SignalsTab strategyKey="donchian_breakout" version="v1" />);
    expect(await screen.findByText('Could not load signals')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Try Again' }));
    await screen.findAllByText(/of \d+/);
  });
});

describe('Strategies page', () => {
  const ok = () => {
    api.list.mockResolvedValue(fixture<{ strategies: StrategyListItem[] }>(strategiesJson).strategies);
    api.summary.mockResolvedValue(fixture<StrategySummary>(prodSummaryJson));
    api.dataHealth.mockResolvedValue(fixture<DataHealth>(prodHealthJson));
  };

  it('renders the strategy from the API list, not a hard-coded one', async () => {
    ok();
    nav.search = 'strategy=donchian_breakout&version=v1';
    render(<StrategiesPage />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Donchian Breakout' })).toBeTruthy();
    expect(screen.getByText('Version v1')).toBeTruthy();
    expect(screen.getByText('LIVE')).toBeTruthy();
    await screen.findByText('Total signals');
    expect(api.summary).toHaveBeenCalledWith('donchian_breakout', 'v1');
    // two registered strategies in the fixture -> a selector is offered
    expect(within(screen.getByLabelText('Strategy')).getAllByRole('option')).toHaveLength(2);
  });

  it('no registered strategy → empty state', async () => {
    api.list.mockResolvedValue([]);
    render(<StrategiesPage />);
    expect(await screen.findByText('No strategy is registered yet')).toBeTruthy();
  });

  it('list failure → error with retry', async () => {
    api.list.mockRejectedValue(axiosError(503, { code: 'unavailable', message: 'Service unavailable.' }));
    render(<StrategiesPage />);
    expect(await screen.findByText('Could not load strategies')).toBeTruthy();
    expect(screen.getByText('Service unavailable.')).toBeTruthy();
  });

  it('an expired session surfaces the backend auth message (the shared client then signs the user out)', async () => {
    api.list.mockRejectedValue(axiosError(401, { code: 'not_authenticated', message: 'The session token is missing, expired or invalid.' }));
    render(<StrategiesPage />);
    expect(await screen.findByText('The session token is missing, expired or invalid.')).toBeTruthy();
  });

  it('summary failure does not hide the strategy header; data health failure is reported on its tab', async () => {
    ok();
    api.summary.mockRejectedValue(axiosError(500, 'x'));
    api.dataHealth.mockRejectedValue(axiosError(500, 'x'));
    nav.search = 'strategy=donchian_breakout&version=v1&tab=health';
    render(<StrategiesPage />);
    expect(await screen.findByText('Could not load data health')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: 'Donchian Breakout' })).toBeTruthy();
  });
});
