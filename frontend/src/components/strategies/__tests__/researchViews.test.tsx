// Release B research views (funnel, research tab, capture health, extension registry) driven by synthetic API payloads.
// The fixtures in ./fixtures/research.ts follow the backend contract; they are not production data.
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import type { DataHealth, StrategySummary } from '@/services/strategyApi';
import prodSummaryJson from './fixtures/prod_summary.json';
import prodHealthJson from './fixtures/prod_health.json';
import {
  disabledRun, failedRun, partialRun, researchNoData, researchOk, researchUnavailable, run, runsNotAvailable, runsOf,
} from './fixtures/research';

vi.mock('@/hooks/usePagePerf', () => ({ usePagePerf: () => ({ markLoaded: () => {} }) }));

import ResearchFunnel from '@/components/strategies/ResearchFunnel';
import ResearchTab from '@/components/strategies/ResearchTab';
import OverviewTab from '@/components/strategies/OverviewTab';
import CaptureHealthPanel from '@/components/strategies/CaptureHealthPanel';
import { candidateClassLabel, extensionRows, getExtension } from '@/lib/strategyExtensions';
import { describeCount, formatFeatureValue } from '@/lib/strategyFormat';

const fixture = <T,>(json: unknown): T => structuredClone(json) as T;
const summary = () => fixture<StrategySummary>(prodSummaryJson);
const health = () => fixture<DataHealth>(prodHealthJson);

const stage = (key: string) => screen.getByRole('list', { name: 'Candidate funnel' }).querySelector(`[data-stage="${key}"]`) as HTMLElement;

describe('Overview funnel', () => {
  it('before migration 22 every stage is "Not collected — Release B" and nothing reads 0', () => {
    render(<ResearchFunnel research={researchUnavailable()} />);
    const stages = ['evaluated', 'candidates', 'guard_passed', 'ranked', 'selected', 'ledger_signals'];
    for (const key of stages) {
      const el = stage(key);
      expect(el.getAttribute('data-count-state')).toBe('not_available');
      expect(within(el).getByText('—')).toBeTruthy();
      expect(within(el).getByText('Not collected — Release B')).toBeTruthy();
      expect(el.textContent).not.toMatch(/\b0\b/);
    }
    const line = screen.getByTestId('capture-status-line');
    expect(within(line).getByText('not collected')).toBeTruthy();
    expect(screen.queryByText('0%')).toBeNull();
    expect(screen.queryByRole('button', { name: /Explore candidates/ })).toBeNull();
  });

  it('tables installed but capture never ran → no_data, still no zeros', () => {
    render(<ResearchFunnel research={researchNoData()} />);
    expect(screen.getByText('Capture has not run for this strategy')).toBeTruthy();
    for (const key of ['candidates', 'selected', 'ledger_signals']) {
      expect(stage(key).getAttribute('data-count-state')).toBe('no_data');
      expect(stage(key).textContent).toContain('—');
    }
    expect(within(screen.getByTestId('capture-status-line')).getByText('not run')).toBeTruthy();
  });

  it('a complete capture shows the funnel counts, the unrecorded universe size and the capture status line', () => {
    const onOpenTab = vi.fn();
    render(<ResearchFunnel research={researchOk('complete')} onOpenTab={onOpenTab} />);
    expect(within(stage('candidates')).getByText('1,843')).toBeTruthy();
    expect(within(stage('guard_passed')).getByText('1,700')).toBeTruthy();
    expect(within(stage('ranked')).getByText('412')).toBeTruthy();
    expect(within(stage('selected')).getByText('25')).toBeTruthy();
    expect(within(stage('ledger_signals')).getByText('25 linked to a candidate')).toBeTruthy();
    // evaluated is genuinely not recorded: the backend's own reason, never 0
    expect(within(stage('evaluated')).getByText('Universe size is not recorded by the screener.')).toBeTruthy();
    const line = screen.getByTestId('capture-status-line');
    expect(within(line).getByText('COMPLETE')).toBeTruthy();
    expect(within(line).getByText('t0_v1')).toBeTruthy();
    expect(within(line).getByText('5,400 / 5,400')).toBeTruthy();
    expect(screen.getByText('92.3%')).toBeTruthy(); // guard pass rate
    fireEvent.click(screen.getByRole('button', { name: /Explore candidates/ }));
    expect(onOpenTab).toHaveBeenCalledWith('candidates');
  });

  it('a partial latest capture is labelled PARTIAL, not healthy', () => {
    render(<ResearchFunnel research={researchOk('partial')} />);
    const badge = within(screen.getByTestId('capture-status-line')).getByText('PARTIAL');
    expect(badge.closest('[data-capture-status]')?.getAttribute('data-capture-status')).toBe('partial');
    expect(screen.queryByText('COMPLETE')).toBeNull();
  });

  it('Overview tab renders the funnel only when research data was supplied, and reports a research failure without hiding the ledger', () => {
    const { rerender } = render(<OverviewTab summary={summary()} health={health()} onOpenTab={() => {}} />);
    expect(screen.queryByRole('list', { name: 'Candidate funnel' })).toBeNull();
    expect(screen.getByText('Total signals')).toBeTruthy();
    rerender(<OverviewTab summary={summary()} health={health()} research={researchUnavailable()} onOpenTab={() => {}} />);
    expect(screen.getByRole('list', { name: 'Candidate funnel' })).toBeTruthy();
    rerender(<OverviewTab summary={summary()} health={health()} researchError="Request timed out." onOpenTab={() => {}} />);
    expect(screen.getByText('Research data could not be loaded')).toBeTruthy();
    expect(screen.getByText(/Request timed out\./)).toBeTruthy();
    expect(screen.getByText('Total signals')).toBeTruthy();
  });
});

describe('Research tab', () => {
  const caps = () => summary().capabilities;
  const coverage = () => health().coverage;

  it('without a research summary it keeps the pre-Release-B behaviour', () => {
    render(<ResearchTab capabilities={caps()} coverage={coverage()} />);
    expect(screen.getByText('Conditional analysis')).toBeTruthy();
    expect(screen.queryByTestId('forward-outcomes')).toBeNull();
  });

  it('unavailable research: cards read "Not collected — Release B", no zeros or percentages, forward outcomes reserved', () => {
    render(<ResearchTab capabilities={caps()} coverage={coverage()} research={researchUnavailable()} />);
    const cards = ['Candidate observations', 'Feature snapshots', 'Bullish', 'Bearish', 'Guard passed', 'Guard rejected'];
    for (const label of cards) {
      const card = screen.getByText(label, { selector: 'p' }).closest('[data-count-state]') as HTMLElement;
      expect(card.getAttribute('data-count-state')).toBe('not_available');
      expect(within(card).getByText('—')).toBeTruthy();
    }
    for (const label of ['Missing feature rate', 'Snapshot coverage', 'Capture coverage']) {
      const card = screen.getByText(label, { selector: 'p' }).closest('[data-metric-state]') as HTMLElement;
      expect(card.getAttribute('data-metric-state')).toBe('not_available');
      expect(card.textContent).not.toContain('0%');
    }
    expect(screen.getByTestId('feature-set').textContent).toBe('Not collected — Release B');
    expect(screen.getByTestId('forward-outcomes').textContent).toContain('Forward Outcomes — Not collected yet');
    expect(screen.getByTestId('forward-outcomes').getAttribute('data-state')).toBe('not_available');
    expect(screen.queryByRole('img')).toBeNull(); // no fake charts
  });

  it('captured research shows the measured counts, the t0_v1 provenance and replaces the stale static capability rows', () => {
    render(<ResearchTab capabilities={caps()} coverage={coverage()} research={researchOk()} />);
    const obs = screen.getByText('Candidate observations', { selector: 'p' }).closest('[data-count-state]') as HTMLElement;
    expect(within(obs).getByText('5,400')).toBeTruthy();
    const bullish = screen.getByText('Bullish', { selector: 'p' }).closest('[data-count-state]') as HTMLElement;
    expect(within(bullish).getByText('3,100')).toBeTruthy();
    const fs = screen.getByTestId('feature-set');
    expect(within(fs).getByText('t0_v1')).toBeTruthy();
    expect(within(fs).getByText('abcdef012345')).toBeTruthy();
    expect(screen.getByText('2.1%')).toBeTruthy();
    // the static "Not collected" capability + coverage rows for the now-collected items are gone
    expect(screen.queryByText('Candidate observations', { selector: 'span' })).toBeNull();
    expect(screen.queryByText('Candidate observation link')).toBeNull();
    expect(screen.getByTestId('lineage-linked').textContent).toContain('25');
    // and no performance metric is invented from candidate data
    expect(screen.getByTestId('forward-outcomes')).toBeTruthy();
    expect(screen.queryByText(/win rate/i)).toBeNull();
  });

  it('reports a research load failure without breaking the ledger sections', () => {
    render(<ResearchTab capabilities={caps()} coverage={coverage()} researchError="boom" />);
    expect(screen.getByText('Research data could not be loaded')).toBeTruthy();
    expect(screen.getByText('Lineage coverage')).toBeTruthy();
  });
});

describe('Capture health panel', () => {
  const statusOf = (el: Element) => el.closest('[data-capture-status]')?.getAttribute('data-capture-status');

  it('not installed → one honest notice, no counters', () => {
    render(<CaptureHealthPanel data={runsNotAvailable()} />);
    expect(screen.getByTestId('capture-panel').getAttribute('data-overall')).toBe('not_available');
    expect(screen.getAllByText('Not collected — Release B').length).toBeGreaterThan(0);
    expect(screen.queryByTestId('capture-counters')).toBeNull();
    expect(screen.queryByRole('table', { name: 'Capture history' })).toBeNull();
  });

  it('loading and error states', () => {
    const { rerender } = render(<CaptureHealthPanel data={null} />);
    expect(screen.getByText('Loading capture health…')).toBeTruthy();
    const retry = vi.fn();
    rerender(<CaptureHealthPanel data={null} error="Network down." onRetry={retry} />);
    expect(screen.getByText('Capture health could not be loaded')).toBeTruthy();
    fireEvent.click(screen.getByText('Try again'));
    expect(retry).toHaveBeenCalled();
  });

  it('COMPLETE shows every counter the requirement lists', () => {
    render(<CaptureHealthPanel data={runsOf(run())} />);
    expect(screen.getByTestId('capture-latest').getAttribute('data-capture-status')).toBe('complete');
    const c = within(screen.getByTestId('capture-counters'));
    for (const label of ['Candidates', 'Captured', 'Already captured', 'Stale skipped', 'Snapshot skipped', 'Invalid', 'Guard rejected', 'Hash drift', 'Snapshot drift', 'Missing-feature rate', 'Runtime']) {
      expect(c.getByText(label)).toBeTruthy();
    }
    expect(c.getByText('2m 02s')).toBeTruthy();
    expect(c.getByText(/1\.1% · 800 of 70,034 slots/)).toBeTruthy();
  });

  it('PARTIAL is amber, says why, highlights skipped counts and lists the skipped symbols — never healthy', () => {
    render(<CaptureHealthPanel data={runsOf(partialRun())} />);
    const panel = screen.getByTestId('capture-panel');
    expect(panel.getAttribute('data-overall')).toBe('partial');
    expect(screen.getByTestId('capture-latest').getAttribute('data-capture-status')).toBe('partial');
    expect(screen.queryByText('COMPLETE')).toBeNull();
    expect(screen.getAllByText('PARTIAL').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/12 candidates were not captured/).length).toBeGreaterThan(0);
    const skipped = within(screen.getByTestId('capture-counters')).getByText('Snapshot skipped').nextElementSibling as HTMLElement;
    expect(skipped.textContent).toBe('12');
    expect(skipped.className).toContain('text-amber-800');
    expect(screen.getByTestId('capture-notes').textContent).toContain('hash-drift');
    expect(screen.getByText('2 skipped symbols')).toBeTruthy();
    expect(screen.getByText('ZZZZ')).toBeTruthy();
  });

  it('FAILED shows the error text and a red status; DISABLED is a distinct neutral status with no invented counters', () => {
    const { unmount } = render(<CaptureHealthPanel data={runsOf(failedRun())} />);
    expect(screen.getByTestId('capture-latest').getAttribute('data-capture-status')).toBe('failed');
    expect(screen.getByText('RuntimeError: price fetch timed out')).toBeTruthy();
    unmount();

    render(<CaptureHealthPanel data={runsOf(disabledRun())} />);
    const latest = screen.getByTestId('capture-latest');
    expect(latest.getAttribute('data-capture-status')).toBe('disabled');
    expect(within(latest).getAllByText('DISABLED').length).toBeGreaterThan(0);
    const captured = within(screen.getByTestId('capture-counters')).getByText('Captured').nextElementSibling as HTMLElement;
    expect(captured.textContent).toBe('—');
  });

  it('every status is a distinct label', () => {
    const history = [run({ id: 5, session_date: '2026-10-05' }), partialRun(), failedRun(), disabledRun(), run({ id: 9, status: 'running', session_date: '2026-10-06', health: { status: 'running', reasons: [], notes: [] } })];
    render(<CaptureHealthPanel data={runsOf(history[0], history)} />);
    const rows = within(screen.getByRole('table', { name: 'Capture history' })).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(5);
    const statuses = rows.map((r) => r.getAttribute('data-capture-status'));
    expect(statuses).toEqual(['complete', 'partial', 'failed', 'disabled', 'running']);
    const labels = rows.map((r) => within(r).getByText(/COMPLETE|PARTIAL|FAILED|DISABLED|RUNNING/).textContent);
    expect(new Set(labels).size).toBe(5);
    expect(statusOf(within(rows[1]).getByText('PARTIAL'))).toBe('partial');
  });

  it('history rows carry session, candidates, captured, skipped, guard-rejected, missing and runtime', () => {
    const p = partialRun();
    render(<CaptureHealthPanel data={runsOf(p, [p])} />);
    const row = within(screen.getByRole('table', { name: 'Capture history' })).getAllByRole('row')[1];
    const cells = within(row).getAllByRole('cell').map((c) => c.textContent);
    expect(cells[2]).toBe('1,843');
    expect(cells[3]).toBe('1,831');
    expect(cells[4]).toBe('12');
    expect(cells[5]).toBe('143');
  });
});

describe('Strategy extension registry', () => {
  it('Donchian declares its class labels and context fields; they are ordered and complete', () => {
    expect(candidateClassLabel('donchian_breakout', 'near_bullish')).toBe('Near bullish breakout');
    const rows = extensionRows('donchian_breakout', { weekly_asof: '2026-09-25', urgency: 'HIGH', surprise: 3 });
    expect(rows.map((r) => r.key)).toEqual(['urgency', 'weekly_asof', 'surprise']);
    expect(rows[2].label).toBe('Surprise');
  });

  it('a strategy without an entry still renders: classes and fields are humanised, nothing is hidden', () => {
    expect(getExtension('mean_reversion_demo').candidateClasses).toEqual({});
    expect(candidateClassLabel('mean_reversion_demo', 'oversold_bounce')).toBe('Oversold bounce');
    const rows = extensionRows('mean_reversion_demo', { rsi_trigger: 28.4, z_score_entry: null });
    expect(rows.map((r) => r.label)).toEqual(['Rsi trigger', 'Z score entry']);
    expect(rows[1].value).toBeNull();
  });
});

describe('Formatting helpers never turn missing into a number', () => {
  it('describeCount: unavailable / no_data / ok', () => {
    expect(describeCount({ value: null, state: 'not_available' }).text).toBe('—');
    expect(describeCount(undefined).note).toBe('Not collected — Release B');
    expect(describeCount({ value: null, state: 'no_data', reason: 'x' }).tone).toBe('empty');
    expect(describeCount({ value: 0, state: 'ok' }).text).toBe('0'); // a measured zero is a real zero
  });
  it('formatFeatureValue: null → "Missing", booleans, units', () => {
    expect(formatFeatureValue(null, 'price')).toBe('Missing');
    expect(formatFeatureValue(true, null)).toBe('Yes');
    expect(formatFeatureValue(25_300_000, 'usd')).toBe('$25,300,000');
    expect(formatFeatureValue(1.624, 'ratio')).toBe('1.62×');
    expect(formatFeatureValue(0, 'ratio')).toBe('0.00×');
  });
});
