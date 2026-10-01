// Candidate Explorer + Candidate Detail + lineage against a mocked strategyApi and synthetic Release B payloads.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { SignalDetail } from '@/services/strategyApi';
import prodDetailJson from './fixtures/prod_detail_463.json';
import {
  candidate, candidateDetail, candidatePage, candidatePageNoData, candidatePageUnavailable, snapshotDetail,
} from './fixtures/research';

const api = vi.hoisted(() => ({ candidates: vi.fn(), candidate: vi.fn(), snapshot: vi.fn(), signal: vi.fn() }));
vi.mock('@/services/strategyApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/strategyApi')>();
  return { ...actual, strategyApi: api };
});
vi.mock('@/components/dashboard/SymbolHoverLink', () => ({ default: ({ symbol }: { symbol: string }) => <span>{symbol}</span> }));

import CandidatesTab, { CANDIDATE_PAGE_SIZE } from '@/components/strategies/CandidatesTab';
import CandidateDetailDialog from '@/components/strategies/CandidateDetailDialog';

const axiosError = (status: number, detail: unknown) =>
  Object.assign(new Error(`HTTP ${status}`), { isAxiosError: true, response: { status, data: { detail } } });

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  api.snapshot.mockResolvedValue(snapshotDetail());
});

function Harness({ initial = null, strategyKey = 'donchian_breakout', version = 'v1', name = 'Donchian Breakout' }: { initial?: number | null; strategyKey?: string; version?: string; name?: string }) {
  const [id, setId] = React.useState<number | null>(initial);
  return <CandidatesTab strategyKey={strategyKey} version={version} strategyName={name} candidateId={id} onCandidateChange={setId} />;
}

describe('Candidate explorer', () => {
  it('asks the server for one page (default session = latest captured) and renders the requested columns from stored fields', async () => {
    api.candidates.mockResolvedValue(candidatePage({ total: 120, has_more: true }));
    render(<Harness />);
    await screen.findByText(/1–3 of 120/);
    expect(api.candidates).toHaveBeenCalledWith('donchian_breakout', 'v1', expect.objectContaining({ limit: CANDIDATE_PAGE_SIZE, offset: 0, sort: 'rank', session_date: undefined }));
    const table = screen.getByRole('table', { name: 'Candidates' });
    const headers = within(table).getAllByRole('columnheader').map((h) => h.textContent);
    expect(headers).toEqual(['Symbol', 'Session', 'Direction', 'Class', 'Guard', 'Guard reason', 'Alignment', 'Grade', 'Combined', 'Selected', 'Snapshot', 'Ledger signal']);
    const row = within(table).getAllByRole('row')[1];
    const cells = within(row).getAllByRole('cell').map((c) => c.textContent);
    expect(cells[0]).toContain('SYM0');
    expect(cells[3]).toBe('Bullish breakout');
    expect(cells[4]).toBe('Passed');
    expect(cells[6]).toBe('70');
    expect(cells[7]).toBe('A');
    expect(cells[8]).toBe('80.5');
    expect(cells[9]).toBe('Selected');
    expect(cells[10]).toBe('Complete');
    expect(cells[11]).toBe('#463');
    expect(screen.getByTestId('candidates-strategy').textContent).toBe('Donchian Breakout v1');
  });

  it('shows rejected guard reasons and missing values as a dash, never 0', async () => {
    api.candidates.mockResolvedValue(candidatePage({
      items: [candidate(1, { passed_guard: false, guard_status: 'rejected', guard_reasons: ['illiquid_dollar_volume'], alignment_score: null, combined_score: null, quality_grade: null, snapshot_status: 'partial', missing_count: 4 })],
    }));
    render(<Harness />);
    const row = (await screen.findAllByRole('row'))[1];
    const cells = within(row).getAllByRole('cell').map((c) => c.textContent);
    expect(cells[4]).toBe('Rejected');
    expect(cells[5]).toBe('Illiquid (dollar volume)');
    expect(cells[6]).toBe('—');
    expect(cells[7]).toBe('—');
    expect(cells[8]).toBe('—');
    expect(cells[10]).toBe('Partial · 4 missing');
    expect(cells[11]).toBe('—');
  });

  it('next page requests offset 50; any filter change resets to offset 0 and is sent to the server', async () => {
    api.candidates.mockResolvedValue(candidatePage({ total: 400, has_more: true }));
    render(<Harness />);
    await screen.findByText(/1–3 of 400/);
    fireEvent.click(screen.getAllByRole('button', { name: 'Next page' })[0]);
    await waitFor(() => expect(api.candidates).toHaveBeenLastCalledWith('donchian_breakout', 'v1', expect.objectContaining({ offset: 50 })));

    const last = () => api.candidates.mock.calls.at(-1)![2];
    fireEvent.change(screen.getByLabelText('Direction'), { target: { value: 'bearish' } });
    await waitFor(() => expect(last()).toMatchObject({ offset: 0, direction: 'bearish' }));
    fireEvent.change(screen.getByLabelText('Guard status'), { target: { value: 'rejected' } });
    await waitFor(() => expect(last()).toMatchObject({ offset: 0, direction: 'bearish', guard: 'rejected' }));
    fireEvent.change(screen.getByLabelText('Selected'), { target: { value: 'yes' } });
    await waitFor(() => expect(last()).toMatchObject({ selected: true }));
    fireEvent.change(screen.getByLabelText('Selected'), { target: { value: 'no' } });
    await waitFor(() => expect(last()).toMatchObject({ selected: false }));
    fireEvent.change(screen.getByLabelText('Candidate class'), { target: { value: 'near_bearish' } });
    await waitFor(() => expect(last()).toMatchObject({ candidate_class: 'near_bearish' }));
    fireEvent.change(screen.getByLabelText('Grade'), { target: { value: 'A' } });
    await waitFor(() => expect(last()).toMatchObject({ grade: 'A' }));
    fireEvent.change(screen.getByLabelText('Session'), { target: { value: '2026-10-01' } });
    await waitFor(() => expect(last()).toMatchObject({ session_date: '2026-10-01', offset: 0 }));
    fireEvent.change(screen.getByLabelText('Sort'), { target: { value: 'symbol' } });
    await waitFor(() => expect(last()).toMatchObject({ sort: 'symbol' }));
  });

  it('class options come from the response facets, labelled through the strategy extension', async () => {
    api.candidates.mockResolvedValue(candidatePage());
    render(<Harness />);
    await screen.findByText(/1–3 of 3/);
    const options = within(screen.getByLabelText('Candidate class')).getAllByRole('option').map((o) => o.textContent);
    expect(options).toEqual(['Any', 'Bullish breakout (2)', 'Near bearish breakout (1)']);
  });

  it('symbol search is debounced, upper-cased and server-side', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      api.candidates.mockResolvedValue(candidatePage());
      render(<Harness />);
      await screen.findByText(/1–3 of 3/);
      const calls = api.candidates.mock.calls.length;
      fireEvent.change(screen.getByLabelText('Filter candidates by symbol'), { target: { value: 'aa' } });
      expect(api.candidates.mock.calls.length).toBe(calls);
      await act(async () => { await vi.advanceTimersByTimeAsync(350); });
      await waitFor(() => expect(api.candidates).toHaveBeenLastCalledWith('donchian_breakout', 'v1', expect.objectContaining({ symbol: 'AA', offset: 0 })));
    } finally {
      vi.useRealTimers();
    }
  });

  it('"Clear filters" restores the defaults', async () => {
    api.candidates.mockResolvedValue(candidatePage());
    render(<Harness />);
    await screen.findByText(/1–3 of 3/);
    const clear = screen.getByRole('button', { name: 'Clear filters' }) as HTMLButtonElement;
    expect(clear.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Direction'), { target: { value: 'bullish' } });
    await waitFor(() => expect((screen.getByRole('button', { name: 'Clear filters' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    await waitFor(() => expect(api.candidates.mock.calls.at(-1)![2].direction).toBeUndefined());
  });

  it('before migration 22: "Not collected — Release B", no "0 candidates", no pager totals', async () => {
    api.candidates.mockResolvedValue(candidatePageUnavailable());
    render(<Harness />);
    expect(await screen.findByText('Not collected — Release B')).toBeTruthy();
    expect(screen.queryByText(/No candidates/)).toBeNull();
    expect(screen.queryByRole('table', { name: 'Candidates' })).toBeNull();
    expect(screen.queryByText(/of 0/)).toBeNull();
  });

  it('tables exist but capture never ran → says capture has not run', async () => {
    api.candidates.mockResolvedValue(candidatePageNoData());
    render(<Harness />);
    expect(await screen.findByText('Capture has not run for this strategy')).toBeTruthy();
    expect(screen.queryByRole('table', { name: 'Candidates' })).toBeNull();
  });

  it('empty results distinguish "no candidates" from "no match for the filters"', async () => {
    api.candidates.mockResolvedValue(candidatePage({ items: [], total: 0 }));
    render(<Harness />);
    expect(await screen.findByText('No candidates in this session')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Direction'), { target: { value: 'bullish' } });
    expect(await screen.findByText('No candidate matches these filters')).toBeTruthy();
  });

  it('an API failure shows an error with retry', async () => {
    api.candidates.mockRejectedValueOnce(axiosError(500, { code: 'x', message: 'Database unavailable.' }));
    render(<Harness />);
    expect(await screen.findByText('Could not load candidates')).toBeTruthy();
    expect(screen.getByText('Database unavailable.')).toBeTruthy();
    api.candidates.mockResolvedValue(candidatePage());
    fireEvent.click(screen.getByRole('button', { name: /retry|try again/i }));
    await screen.findByRole('table', { name: 'Candidates' });
  });

  it('is strategy-agnostic: a second strategy is queried by its own key/version and labelled by its own name', async () => {
    api.candidates.mockResolvedValue(candidatePage({ items: [candidate(0, { candidate_class: 'oversold_bounce' })] }));
    render(<Harness strategyKey="mean_reversion_demo" version="v2" name="Mean Reversion Demo" />);
    await screen.findByRole('table', { name: 'Candidates' });
    expect(api.candidates).toHaveBeenCalledWith('mean_reversion_demo', 'v2', expect.anything());
    expect(screen.getByTestId('candidates-strategy').textContent).toBe('Mean Reversion Demo v2');
    expect(screen.getByText('Oversold bounce')).toBeTruthy(); // no registry entry → humanised stored value
  });
});

describe('Candidate detail', () => {
  it('row click opens the detail; Strategy context and T0 snapshot are separate, loaded from separate endpoints', async () => {
    api.candidates.mockResolvedValue(candidatePage());
    api.candidate.mockResolvedValue(candidateDetail());
    render(<Harness />);
    fireEvent.click(await screen.findByRole('button', { name: 'Open SYM0 candidate details' }));
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(api.candidate).toHaveBeenCalledWith('donchian_breakout', 'v1', 1000));
    await within(dialog).findByTestId('t0-snapshot');
    await waitFor(() => expect(api.snapshot).toHaveBeenCalledWith('donchian_breakout', 'v1', 2000));

    const ctx = within(await within(dialog).findByTestId('strategy-context'));
    expect(ctx.getByText('Candidate class').nextElementSibling?.textContent).toBe('Bullish breakout');
    expect(ctx.getByText('Entry close').nextElementSibling?.textContent).toBe('$50.00');
    expect(ctx.getByText('Alignment score').nextElementSibling?.textContent).toBe('70');
    expect(ctx.getByText('Combined score').nextElementSibling?.textContent).toBe('80.5');
    expect(ctx.getByText('Model').nextElementSibling?.textContent).toBe('Not scored (no validated model)');
    // Donchian-specific fields arrive via the extension layer; an undeclared field is kept, humanised
    expect(ctx.getByText('Urgency').nextElementSibling?.textContent).toBe('HIGH');
    expect(ctx.getByText('Donchian high (20)').nextElementSibling?.textContent).toBe('$48.50');
    expect(ctx.getByText('Mystery field').nextElementSibling?.textContent).toBe('kept');
    // the market snapshot is NOT inside the strategy block
    expect(ctx.queryByText('Close')).toBeNull();
    expect(ctx.queryByText('Rsi 14')).toBeNull();
  });

  it('shows feature_set_version, session date, bar date and missing-feature count prominently', async () => {
    api.candidate.mockResolvedValue(candidateDetail());
    render(<CandidateDetailDialog strategyKey="donchian_breakout" version="v1" candidateId={1000} onClose={() => {}} onOpenSignal={() => {}} />);
    const banner = within(await screen.findByTestId('candidate-banner'));
    expect(banner.getByText('t0_v1')).toBeTruthy();
    expect(banner.getByText('Session date').nextElementSibling?.textContent).toMatch(/Oct 2, 2026|2 Oct 2026|10\/2\/2026|2026/);
    expect(banner.getByText('Bar date').nextElementSibling?.textContent).toBe(banner.getByText('Session date').nextElementSibling?.textContent);
    expect(screen.getByTestId('missing-features').textContent).toBe('0');
  });

  it('T0 snapshot fields are grouped Price / Trend / Momentum / Volatility / Volume-Liquidity / 52-week / Market metadata / Data quality', async () => {
    api.candidate.mockResolvedValue(candidateDetail());
    render(<CandidateDetailDialog strategyKey="donchian_breakout" version="v1" candidateId={1000} onClose={() => {}} onOpenSignal={() => {}} />);
    const snap = await screen.findByTestId('t0-snapshot');
    await within(snap).findByText('Price');
    const groups = Array.from(snap.querySelectorAll('[data-group]')).map((g) => g.getAttribute('data-group'));
    expect(groups).toEqual(['price', 'trend', 'momentum', 'volatility', 'volume_liquidity', 'week52', 'market_metadata', 'data_quality']);
    const vol = within(snap.querySelector('[data-group="volume_liquidity"]') as HTMLElement);
    expect(vol.getByText('Rvol 20').nextElementSibling?.textContent).toBe('1.62×');
    expect(vol.getByText('Dollar volume 20').nextElementSibling?.textContent).toBe('$25,300,000');
    expect(within(snap.querySelector('[data-group="trend"]') as HTMLElement).getByText('Above sma 200').nextElementSibling?.textContent).toBe('Yes');
    expect(snap.textContent).toContain('t0_v1 · manifest abcdef012345 · hash 0123456789ab · code abc123def456');
  });

  it('missing / null features read "Missing" (never null, never 0) and the PARTIAL state is called out', async () => {
    const detail = candidateDetail({ snapshot: { id: 2000, feature_set_version: 't0_v1', snapshot_status: 'partial', missing_count: 2 } });
    api.candidate.mockResolvedValue(detail);
    const s = snapshotDetail({ snapshot_status: 'partial', missing_features: ['rsi_14', 'rvol_20'] });
    s.groups[2].items[0] = { ...s.groups[2].items[0], value: null, missing: true };
    s.groups[4].items[0] = { ...s.groups[4].items[0], value: null, missing: true };
    api.snapshot.mockResolvedValue(s);
    render(<CandidateDetailDialog strategyKey="donchian_breakout" version="v1" candidateId={1000} onClose={() => {}} onOpenSignal={() => {}} />);
    const snap = await screen.findByTestId('t0-snapshot');
    const list = await within(snap).findByTestId('missing-feature-list');
    expect(list.textContent).toContain('2 missing: rsi_14, rvol_20');
    expect(within(snap.querySelector('[data-group="momentum"]') as HTMLElement).getByText('Rsi 14').nextElementSibling?.textContent).toBe('Missing');
    expect(within(snap.querySelector('[data-group="volume_liquidity"]') as HTMLElement).getByText('Rvol 20').nextElementSibling?.textContent).toBe('Missing');
    expect(snap.textContent).not.toMatch(/\bnull\b|undefined|NaN/);
    expect(screen.getByTestId('missing-features').textContent).toContain('2');
    expect(screen.getByTestId('missing-features').textContent).toContain('PARTIAL');
  });

  it('null strategy-context values read "Not recorded", not 0', async () => {
    const d = candidateDetail();
    Object.assign(d.strategy_context, { alignment_score: null, combined_score: null, quality_grade: null, session_rank: null, breakout_dist_atr: null });
    d.strategy_context.levels.entry_close = null;
    api.candidate.mockResolvedValue(d);
    render(<CandidateDetailDialog strategyKey="donchian_breakout" version="v1" candidateId={1000} onClose={() => {}} onOpenSignal={() => {}} />);
    const ctx = within(await screen.findByTestId('strategy-context'));
    for (const label of ['Alignment score', 'Combined score', 'Grade', 'Entry close', 'Breakout distance']) {
      expect(ctx.getByText(label).nextElementSibling?.textContent).toBe('Not recorded');
    }
    expect(ctx.getByText('Session rank').nextElementSibling?.textContent).toBe('Not ranked');
  });

  it('a snapshot failure is reported inside the snapshot panel; the strategy context still renders', async () => {
    api.candidate.mockResolvedValue(candidateDetail());
    api.snapshot.mockRejectedValue(axiosError(404, { code: 'snapshot_not_found', message: 'Snapshot not found.' }));
    render(<CandidateDetailDialog strategyKey="donchian_breakout" version="v1" candidateId={1000} onClose={() => {}} onOpenSignal={() => {}} />);
    expect(await screen.findByText('Could not load the snapshot')).toBeTruthy();
    expect(screen.getByTestId('strategy-context')).toBeTruthy();
  });

  it('a candidate that cannot be loaded (e.g. 404 research_not_available) shows an error, not an empty dialog', async () => {
    api.candidate.mockRejectedValue(axiosError(404, { code: 'research_not_available', message: 'Candidate capture is not installed yet.' }));
    render(<CandidateDetailDialog strategyKey="donchian_breakout" version="v1" candidateId={1000} onClose={() => {}} onOpenSignal={() => {}} />);
    expect(await screen.findByText('Could not load this candidate')).toBeTruthy();
    expect(screen.getByText('Candidate capture is not installed yet.')).toBeTruthy();
    expect(api.snapshot).not.toHaveBeenCalled();
  });

  it('does not fetch while closed', () => {
    render(<CandidateDetailDialog strategyKey="donchian_breakout" version="v1" candidateId={null} onClose={() => {}} onOpenSignal={() => {}} />);
    expect(api.candidate).not.toHaveBeenCalled();
  });
});

describe('Ledger lineage', () => {
  it('Candidate → Feature snapshot → Ledger signal; the signal link opens the existing signal detail', async () => {
    api.candidates.mockResolvedValue(candidatePage());
    api.candidate.mockResolvedValue(candidateDetail());
    api.signal.mockResolvedValue(structuredClone(prodDetailJson) as unknown as SignalDetail);
    render(<Harness initial={1000} />);
    const lineage = within(await screen.findByTestId('candidate-lineage'));
    expect(lineage.getByText('#1000')).toBeTruthy();
    expect(lineage.getByText('#2000')).toBeTruthy();
    const link = lineage.getByRole('button', { name: /#463/ });
    fireEvent.click(link);
    await waitFor(() => expect(api.signal).toHaveBeenCalledWith('donchian_breakout', 'v1', 463));
    await waitFor(() => expect(screen.getAllByRole('dialog').length).toBeGreaterThan(0));
  });

  it('a candidate that never became a signal says so; no link, no invented ledger row', async () => {
    api.candidate.mockResolvedValue(candidateDetail({ lineage: { observation_id: 1001, snapshot_id: 2001, signal: null } }));
    render(<CandidateDetailDialog strategyKey="donchian_breakout" version="v1" candidateId={1001} onClose={() => {}} onOpenSignal={() => {}} />);
    const lineage = within(await screen.findByTestId('candidate-lineage'));
    expect(lineage.getByText('Not in the signal ledger')).toBeTruthy();
    expect(lineage.queryByRole('button')).toBeNull();
  });

  it('the explorer row links straight to the ledger signal without opening the candidate', async () => {
    api.candidates.mockResolvedValue(candidatePage());
    api.signal.mockResolvedValue(structuredClone(prodDetailJson) as unknown as SignalDetail);
    render(<Harness />);
    fireEvent.click(await screen.findByRole('button', { name: 'Open ledger signal 463 for SYM0' }));
    await waitFor(() => expect(api.signal).toHaveBeenCalledWith('donchian_breakout', 'v1', 463));
    expect(api.candidate).not.toHaveBeenCalled();
  });
});
