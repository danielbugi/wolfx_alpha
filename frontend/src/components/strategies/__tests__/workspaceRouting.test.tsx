// The Quant Lab workspace end to end against a mocked strategyApi: /strategies and /strategies/[strategyKey], multi-strategy
// identity, graceful Release B unavailability (the current production API has no /research routes), and tab wiring.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { DataHealth, StrategyListItem, StrategySummary } from '@/services/strategyApi';
import strategiesJson from './fixtures/strategies.json';
import prodSummaryJson from './fixtures/prod_summary.json';
import prodHealthJson from './fixtures/prod_health.json';
import demoSummaryJson from './fixtures/demo_summary.json';
import demoHealthJson from './fixtures/demo_health.json';
import {
  candidatePage, partialRun, researchNoData, researchOk, researchUnavailable, run, runsNotAvailable, runsOf,
} from './fixtures/research';

const fixture = <T,>(json: unknown): T => structuredClone(json) as T;

const api = vi.hoisted(() => ({
  list: vi.fn(), summary: vi.fn(), dataHealth: vi.fn(), signals: vi.fn(), signal: vi.fn(),
  researchSummary: vi.fn(), captureRuns: vi.fn(), candidates: vi.fn(), candidate: vi.fn(), snapshot: vi.fn(),
}));
vi.mock('@/services/strategyApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/strategyApi')>();
  return { ...actual, strategyApi: api };
});

const nav = vi.hoisted(() => ({ search: '', params: { strategyKey: 'donchian_breakout' } as Record<string, string>, push: vi.fn() }));
vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
  usePathname: () => '/strategies',
  useParams: () => nav.params,
  useRouter: () => ({ push: nav.push, replace: vi.fn() }),
}));
vi.mock('@/hooks/usePagePerf', () => ({ usePagePerf: () => ({ markLoaded: () => {} }) }));
vi.mock('@/components/dashboard/SymbolHoverLink', () => ({ default: ({ symbol }: { symbol: string }) => <span>{symbol}</span> }));

import StrategiesPage from '@/app/strategies/page';
import StrategyPage from '@/app/strategies/[strategyKey]/page';

const axiosError = (status: number, detail: unknown) =>
  Object.assign(new Error(`HTTP ${status}`), { isAxiosError: true, response: { status, data: { detail } } });

const DEMO = 'zz_demo_states';
const asDemo = <T extends { strategy: unknown }>(x: T): T => ({ ...x, strategy: fixture<StrategySummary>(demoSummaryJson).strategy });

function ledgerOk() {
  api.list.mockResolvedValue(fixture<{ strategies: StrategyListItem[] }>(strategiesJson).strategies);
  api.summary.mockImplementation(async (key: string) => (key === DEMO ? fixture<StrategySummary>(demoSummaryJson) : fixture<StrategySummary>(prodSummaryJson)));
  api.dataHealth.mockImplementation(async (key: string) => (key === DEMO ? fixture<DataHealth>(demoHealthJson) : fixture<DataHealth>(prodHealthJson)));
}

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  nav.search = '';
  nav.params = { strategyKey: 'donchian_breakout' };
  nav.push.mockReset();
  ledgerOk();
  api.researchSummary.mockResolvedValue(researchUnavailable());
  api.captureRuns.mockResolvedValue(runsNotAvailable());
  api.candidates.mockResolvedValue(candidatePage());
});

describe('/strategies against today\'s production API (Release B not installed)', () => {
  it('the funnel is present and honest: every stage "Not collected — Release B", no zeros, ledger views untouched', async () => {
    nav.search = 'strategy=donchian_breakout&version=v1';
    render(<StrategiesPage />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Donchian Breakout' })).toBeTruthy();
    const funnel = await screen.findByRole('list', { name: 'Candidate funnel' });
    expect(within(funnel).getAllByText('Not collected — Release B')).toHaveLength(6);
    expect(funnel.textContent).not.toMatch(/\b0\b/);
    expect(await screen.findByText('Total signals')).toBeTruthy();
    expect(screen.queryByTestId('header-capture')).toBeNull();
  });

  it('an older backend with no /research routes (404/500) never breaks the page; the failure is reported, not turned into zeros', async () => {
    api.researchSummary.mockRejectedValue(axiosError(404, { code: 'not_found', message: 'Not Found' }));
    nav.search = 'strategy=donchian_breakout&version=v1';
    render(<StrategiesPage />);
    expect(await screen.findByText('Research data could not be loaded')).toBeTruthy();
    expect(screen.getByText('Total signals')).toBeTruthy();
    expect(screen.queryByRole('list', { name: 'Candidate funnel' })).toBeNull();
  });

  it('Research tab: forward outcomes reserved, no fake charts; capability rows still say Release B', async () => {
    nav.search = 'strategy=donchian_breakout&version=v1&tab=research';
    render(<StrategiesPage />);
    expect(await screen.findByTestId('forward-outcomes')).toBeTruthy();
    expect(screen.getByText('Candidate observations', { selector: 'span' })).toBeTruthy();
  });

  it('Candidates tab: not collected, never "0 candidates"', async () => {
    api.candidates.mockResolvedValue({ ...candidatePage({ items: [], total: null }), availability: researchUnavailable().availability });
    nav.search = 'strategy=donchian_breakout&version=v1&tab=candidates';
    render(<StrategiesPage />);
    expect(await screen.findByText('Not collected — Release B')).toBeTruthy();
    expect(screen.queryByText(/No candidates/)).toBeNull();
  });

  it('Data health tab: capture panel says not collected; the existing data-health sections still render', async () => {
    nav.search = 'strategy=donchian_breakout&version=v1&tab=health';
    render(<StrategiesPage />);
    const panel = await screen.findByTestId('capture-panel');
    expect(panel.getAttribute('data-overall')).toBe('not_available');
    expect(api.captureRuns).toHaveBeenCalledWith('donchian_breakout', 'v1', 30);
    await waitFor(() => expect(screen.queryByText('Could not load data health')).toBeNull());
  });
});

describe('/strategies once capture exists', () => {
  it('overview funnel, header capture chip and an "Explore candidates" hand-off', async () => {
    api.researchSummary.mockResolvedValue(researchOk('complete'));
    nav.search = 'strategy=donchian_breakout&version=v1';
    render(<StrategiesPage />);
    const chip = await screen.findByTestId('header-capture');
    expect(within(chip).getByText('COMPLETE')).toBeTruthy();
    expect(within(await screen.findByRole('list', { name: 'Candidate funnel' })).getByText('1,843')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Explore candidates/ })).toBeTruthy();
  });

  it('a PARTIAL latest capture shows an amber PARTIAL chip in the header, never COMPLETE', async () => {
    api.researchSummary.mockResolvedValue(researchOk('partial'));
    nav.search = 'strategy=donchian_breakout&version=v1';
    render(<StrategiesPage />);
    const chip = await screen.findByTestId('header-capture');
    expect(within(chip).getByText('PARTIAL')).toBeTruthy();
    expect(within(chip).queryByText('COMPLETE')).toBeNull();
  });

  it('tables installed but no run yet → no_data funnel', async () => {
    api.researchSummary.mockResolvedValue(researchNoData());
    nav.search = 'strategy=donchian_breakout&version=v1';
    render(<StrategiesPage />);
    expect(await screen.findByText('Capture has not run for this strategy')).toBeTruthy();
    expect(screen.queryByTestId('header-capture')).toBeNull();
  });

  it('health tab: a PARTIAL latest run is rendered as partial with its history', async () => {
    api.captureRuns.mockResolvedValue(runsOf(partialRun(), [partialRun(), run({ id: 2, session_date: '2026-10-01' })]));
    nav.search = 'strategy=donchian_breakout&version=v1&tab=health';
    render(<StrategiesPage />);
    const panel = await screen.findByTestId('capture-panel');
    await waitFor(() => expect(panel.getAttribute('data-overall')).toBe('partial'));
    const rows = within(screen.getByRole('table', { name: 'Capture history' })).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.getAttribute('data-capture-status'))).toEqual(['partial', 'complete']);
  });

  it('candidate deep link: ?tab=candidates&candidate=1000 opens the detail and loads the snapshot', async () => {
    api.candidate.mockResolvedValue((await import('./fixtures/research')).candidateDetail());
    api.snapshot.mockResolvedValue((await import('./fixtures/research')).snapshotDetail());
    nav.search = 'strategy=donchian_breakout&version=v1&tab=candidates&candidate=1000';
    render(<StrategiesPage />);
    await screen.findByRole('dialog');
    await waitFor(() => expect(api.candidate).toHaveBeenCalledWith('donchian_breakout', 'v1', 1000));
    await waitFor(() => expect(api.snapshot).toHaveBeenCalledWith('donchian_breakout', 'v1', 2000));
  });

  it('a research failure does not hide a working capture panel or the ledger views', async () => {
    api.researchSummary.mockRejectedValue(axiosError(500, 'x'));
    api.captureRuns.mockResolvedValue(runsOf(run()));
    nav.search = 'strategy=donchian_breakout&version=v1&tab=health';
    render(<StrategiesPage />);
    expect((await screen.findByTestId('capture-panel')).getAttribute('data-overall')).toBe('complete');
  });
});

describe('Multi-strategy', () => {
  it('/strategies has no hard-coded strategy: ?strategy= selects any registered one and its own key/version reach every endpoint', async () => {
    api.researchSummary.mockResolvedValue(asDemo(researchOk()));
    nav.search = `strategy=${DEMO}&version=v1`;
    render(<StrategiesPage />);
    expect(await screen.findByRole('heading', { level: 1, name: DEMO })).toBeTruthy();
    await waitFor(() => expect(api.summary).toHaveBeenCalledWith(DEMO, 'v1'));
    expect(api.dataHealth).toHaveBeenCalledWith(DEMO, 'v1');
    expect(api.researchSummary).toHaveBeenCalledWith(DEMO, 'v1');
    expect(api.summary).not.toHaveBeenCalledWith('donchian_breakout', expect.anything());
  });

  it('the strategy selector lists every registered strategy', async () => {
    nav.search = 'strategy=donchian_breakout&version=v1';
    render(<StrategiesPage />);
    await screen.findByRole('heading', { level: 1, name: 'Donchian Breakout' });
    const labels = within(screen.getByLabelText('Strategy')).getAllByRole('option').map((o) => o.textContent);
    expect(labels).toEqual(['Donchian Breakout v1', 'zz_demo_states v1']);
  });

  it('the Candidates tab of a second strategy queries that strategy and reuses the same explorer', async () => {
    nav.search = `strategy=${DEMO}&version=v1&tab=candidates`;
    render(<StrategiesPage />);
    await screen.findByRole('table', { name: 'Candidates' });
    expect(api.candidates).toHaveBeenCalledWith(DEMO, 'v1', expect.anything());
    expect(screen.getByTestId('candidates-strategy').textContent).toBe(`${DEMO} v1`);
  });
});

describe('/strategies/[strategyKey]', () => {
  it('pins the route key; the version defaults to the tracking one and ?version= overrides', async () => {
    nav.params = { strategyKey: 'donchian_breakout' };
    const { unmount } = render(<StrategyPage />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Donchian Breakout' })).toBeTruthy();
    expect(screen.getByRole('link', { name: /All strategies/ }).getAttribute('href')).toBe('/strategies');
    await waitFor(() => expect(api.summary).toHaveBeenCalledWith('donchian_breakout', 'v1'));
    unmount();

    api.summary.mockClear();
    nav.params = { strategyKey: DEMO };
    nav.search = 'version=v1';
    render(<StrategyPage />);
    expect(await screen.findByRole('heading', { level: 1, name: DEMO })).toBeTruthy();
    await waitFor(() => expect(api.summary).toHaveBeenCalledWith(DEMO, 'v1'));
  });

  it('decodes an encoded key from the path', async () => {
    nav.params = { strategyKey: encodeURIComponent(DEMO) };
    render(<StrategyPage />);
    expect(await screen.findByRole('heading', { level: 1, name: DEMO })).toBeTruthy();
  });

  it('an unknown key is reported — it never falls back to another strategy', async () => {
    nav.params = { strategyKey: 'does_not_exist' };
    render(<StrategyPage />);
    expect(await screen.findByText('Unknown strategy')).toBeTruthy();
    expect(screen.getByText(/does_not_exist/)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'See all strategies' }).getAttribute('href')).toBe('/strategies');
    expect(api.summary).not.toHaveBeenCalled();
    expect(api.researchSummary).not.toHaveBeenCalled();
  });

  it('switching strategy from the selector navigates to that strategy\'s own route', async () => {
    nav.params = { strategyKey: 'donchian_breakout' };
    render(<StrategyPage />);
    await screen.findByRole('heading', { level: 1, name: 'Donchian Breakout' });
    fireEvent.change(screen.getByLabelText('Strategy'), { target: { value: `${DEMO}/v1` } });
    expect(nav.push).toHaveBeenCalledWith(`/strategies/${DEMO}?version=v1`);
  });
});
