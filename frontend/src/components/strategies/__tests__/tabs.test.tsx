// Fixtures are real responses from backend/routers/strategy_intelligence.py: "prod_*" is today's production ledger
// (490 open signals, exported read-only), "demo_*" a local synthetic strategy covering resolved/held/degraded states.
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import type { DataHealth, StrategySummary } from '@/services/strategyApi';
import OverviewTab from '@/components/strategies/OverviewTab';
import PerformanceTab from '@/components/strategies/PerformanceTab';
import DirectionsTab from '@/components/strategies/DirectionsTab';
import DataHealthTab from '@/components/strategies/DataHealthTab';
import ResearchTab from '@/components/strategies/ResearchTab';
import prodSummaryJson from './fixtures/prod_summary.json';
import prodHealthJson from './fixtures/prod_health.json';
import demoSummaryJson from './fixtures/demo_summary.json';
import demoHealthJson from './fixtures/demo_health.json';

const fixture = <T,>(json: unknown): T => structuredClone(json) as T;
const prodSummary = fixture<StrategySummary>(prodSummaryJson);
const prodHealth = fixture<DataHealth>(prodHealthJson);
const demoSummary = fixture<StrategySummary>(demoSummaryJson);
const demoHealth = fixture<DataHealth>(demoHealthJson);

const alerts = () => screen.queryAllByRole('alert');

describe('Overview — current production state (490 open, 0 resolved)', () => {
  it('shows the real counts and never a 0% / 0R performance number', () => {
    render(<OverviewTab summary={prodSummary} health={prodHealth} onOpenTab={() => {}} />);
    expect(screen.getByText('Total signals', { selector: 'p' }).parentElement).toHaveProperty('textContent', expect.stringContaining('490'));
    expect(screen.getByText('Resolved', { selector: 'p' }).parentElement?.textContent).toContain('0');
    expect(screen.getByText('Held', { selector: 'p' }).parentElement?.textContent).toContain('0');
    expect(screen.getByLabelText('Bullish 56, bearish 434')).toBeTruthy();
    const perf = screen.getByText('Performance').closest('section') as HTMLElement;
    expect(within(perf).getAllByText('No resolved signals yet')).toHaveLength(4);
    expect(perf.textContent).not.toMatch(/\d%|\dR\b/);
    const states = Array.from(perf.querySelectorAll('[data-metric-state]')).map((e) => e.getAttribute('data-metric-state'));
    expect(states).toEqual(['no_data', 'no_data', 'no_data', 'no_data']);
    expect(screen.getByText('Healthy')).toBeTruthy();
  });

  it('shows ok values with their N on a resolved sample', () => {
    render(<OverviewTab summary={demoSummary} health={demoHealth} onOpenTab={() => {}} />);
    const perf = screen.getByText('Performance').closest('section') as HTMLElement;
    expect(perf.textContent).toContain('41.7%');
    expect(within(perf).getAllByText('N=12').length).toBeGreaterThan(0);
    expect(screen.getByText('Needs attention')).toBeTruthy();
  });

  it('opens the data health tab from the summary card', () => {
    const onOpenTab = vi.fn();
    render(<OverviewTab summary={prodSummary} health={prodHealth} onOpenTab={onOpenTab} />);
    const card = screen.getByText('Data health').closest('section') as HTMLElement;
    within(card).getByRole('button', { name: 'Details →' }).click();
    expect(onOpenTab).toHaveBeenCalledWith('health');
  });
});

describe('Performance', () => {
  it('no resolved signals → empty state, MFE unavailable', () => {
    render(<PerformanceTab summary={prodSummary} />);
    expect(screen.getByText('Outcome analysis will appear after signals begin resolving.')).toBeTruthy();
    expect(screen.getByText('Not available — Release B')).toBeTruthy();
  });

  it('resolved sample → outcome bars with backend counts, ambiguity note, preliminary sub-samples', () => {
    render(<PerformanceTab summary={demoSummary} />);
    expect(screen.getByText('Target 1 (+1R)')).toBeTruthy();
    expect(screen.getByText('incl. 1 ambiguous same-bar')).toBeTruthy();
    expect(screen.getByText('Expired, positive')).toBeTruthy();
    expect(screen.getByText('Expired: average R').parentElement?.textContent).toContain('Preliminary · N=3');
    expect(screen.getByText('Average MAE (adverse)').parentElement?.textContent).not.toContain('+');
  });
});

describe('Direction analysis', () => {
  it('production: counts per direction, rates as —', () => {
    render(<DirectionsTab summary={prodSummary} />);
    const row = screen.getByRole('rowheader', { name: 'Signals' }).closest('tr') as HTMLElement;
    expect(row.textContent).toBe('Signals56434');
    const wr = screen.getByRole('rowheader', { name: 'Win rate' }).closest('tr') as HTMLElement;
    expect(wr.textContent).toBe('Win rate——');
    expect(screen.getByText(/No resolved signals in either direction yet/)).toBeTruthy();
  });

  it('demo: ok bullish sample vs preliminary bearish sample', () => {
    render(<DirectionsTab summary={demoSummary} />);
    const wr = screen.getByRole('rowheader', { name: 'Win rate' }).closest('tr') as HTMLElement;
    expect(wr.textContent).toContain('44.4%');
    expect(wr.textContent).toContain('N=9');
    expect(wr.textContent).toContain('33.3%');
    expect(wr.textContent).toContain('Preliminary · N=3');
  });
});

describe('Data health', () => {
  it('healthy production state is compact: no warning banners', () => {
    render(<DataHealthTab health={prodHealth} />);
    expect(screen.getByText('Healthy')).toBeTruthy();
    expect(screen.getByText('Waiting for first forward session').parentElement?.textContent).toContain('490');
    expect(screen.getByText('all 7 checks pass')).toBeTruthy();
    expect(screen.getByText('Not persisted yet')).toBeTruthy();
    expect(alerts()).toHaveLength(0);
  });

  it('degraded state raises one banner per condition and lists the signals', () => {
    render(<DataHealthTab health={demoHealth} />);
    expect(alerts()).toHaveLength(4);
    expect(screen.getByText('1 signal held for review')).toBeTruthy();
    expect(screen.getByText('1 open signal has stale price data')).toBeTruthy();
    expect(screen.getByText('ZSPLIT')).toBeTruthy();
    expect(screen.getByText('ZSTALE')).toBeTruthy();
  });

  it('an integrity violation is shown as a danger banner', () => {
    const broken = fixture<DataHealth>(prodHealthJson);
    broken.status = 'violation';
    broken.invariants.open_with_outcome = 2;
    render(<DataHealthTab health={broken} />);
    expect(screen.getByText('2 integrity violations')).toBeTruthy();
    expect(screen.getByText('Open signals carrying an outcome: 2')).toBeTruthy();
    expect(screen.getByText('Integrity violation')).toBeTruthy();
  });
});

describe('Research / ML', () => {
  it('shows real coverage and unavailable capabilities as Release B, never as 0', () => {
    render(<ResearchTab capabilities={prodSummary.capabilities} coverage={prodHealth.coverage} />);
    expect(screen.getByText('Model version').parentElement?.textContent).toContain('0 / 490');
    expect(screen.getByText('T0 feature snapshots').closest('li')?.textContent).toContain('Not collected — Release B');
    expect(screen.getByText('Candidate observations').closest('li')?.textContent).toContain('Not collected — Release B');
    expect(screen.getByText('Evaluator run history').closest('li')?.textContent).toContain('Not persisted yet');
    expect(screen.getByText('Feature snapshot link').parentElement?.textContent).toContain('Not collected — Release B');
  });

  it('coverage unavailable when data health failed', () => {
    render(<ResearchTab capabilities={prodSummary.capabilities} coverage={null} />);
    expect(screen.getByText(/Coverage is unavailable right now/)).toBeTruthy();
  });
});
