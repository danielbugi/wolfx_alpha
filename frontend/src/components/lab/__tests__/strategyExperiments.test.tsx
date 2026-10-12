/* eslint-disable @typescript-eslint/no-explicit-any */
// The fixture is the real report served at /api/lab/reports/strategy_experiments (backend/lab_reports/strategy_experiments.json): a snapshot, so this test needs no network.
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import LabReportView from '@/components/lab/LabReportView';
import type { LabReport } from '@/services/labApi';
import reportJson from './fixtures/strategy_experiments.json';

const report = structuredClone(reportJson) as unknown as LabReport;
afterEach(() => cleanup());

describe('Strategy experiments report', () => {
  it('shows the five registered identities with their definition hashes and the manifest hash', () => {
    const { container } = render(<LabReportView report={report} />);
    const d = (report as any).data;
    for (const v of d.variants) {
      expect(container.textContent).toContain(String(v.definition_hash).slice(0, 16));
      expect(container.textContent).toContain(v.key.replace('donchian_breakout__exit_', '').replace(/_/g, ' '));
    }
    expect(container.textContent).toContain(String(d.manifest.manifest_hash).slice(0, 12));
    expect(screen.getByText('5 of 5')).toBeTruthy();
  });

  it('renders every table from real numbers, never NaN or undefined', () => {
    const { container } = render(<LabReportView report={report} />);
    expect(container.textContent).not.toMatch(/NaN|undefined|null/);
    expect(screen.getAllByRole('table').length).toBe(6);
    expect(screen.getByText(/Longer holding periods/)).toBeTruthy();
    expect(screen.getByText(/Actual initial risk vs the standardised/)).toBeTruthy();
    expect(screen.getByText(/Strategy-selection baselines/)).toBeTruthy();
  });

  it('puts the out-of-sample expectancy of each variant on its own row, with the registered 60-bar cap', () => {
    render(<LabReportView report={report} />);
    const d = (report as any).data;
    const heading = screen.getByText(/Out of sample \(test period\), registered 60-bar cap/);
    const table = heading.nextElementSibling as HTMLElement;
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(5);
    const first = d.position_accounting_primary_cost[d.variants[0].key]['60']['test|all'].std;
    expect(rows[0].textContent).toContain(first.n_positions.toLocaleString('en-US'));
    expect(rows[0].textContent).toMatch(/[−+]0\.\d{3}/);
  });

  it('states that the paper tracker is validated by replay and is not activated', () => {
    render(<LabReportView report={report} />);
    const d = (report as any).data;
    expect(screen.getByText(/Forward paper tracker: replay validation \(not activated\)/)).toBeTruthy();
    expect(screen.getByText(`${d.paper_tracker_replay_validation.mismatches} mismatches`)).toBeTruthy();
    expect(screen.getByText(/opens no trade, writes nothing and changes no signal/)).toBeTruthy();
  });
});
