/* eslint-disable @typescript-eslint/no-explicit-any */
// The fixture is the real report served at /api/lab/reports/strategy_lab_loop (backend/lab_reports/strategy_lab_loop.json): a snapshot, so this test needs no network.
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import LabReportView from '@/components/lab/LabReportView';
import type { LabReport } from '@/services/labApi';
import reportJson from './fixtures/strategy_lab_loop.json';

const report = structuredClone(reportJson) as unknown as LabReport;
afterEach(() => cleanup());

describe('Strategy research loop report', () => {
  it('shows every experiment with its verdict, lineage and the numbers it rests on', () => {
    const { container } = render(<LabReportView report={report} />);
    const d = (report as any).data;
    expect(d.experiments.length).toBeGreaterThanOrEqual(3);
    for (const e of d.experiments) {
      expect(container.textContent).toContain(`${e.key} ${e.version}`);
      expect(container.textContent).toContain(String(e.spec_hash).slice(0, 12));
    }
    expect(screen.getAllByText('No edge demonstrated').length).toBe(d.experiments.length);
    expect(container.textContent).toContain('train-only subgroup analysis');
    expect(container.textContent).toContain('owner hypothesis, a priori');
  });

  it('never shows NaN, undefined or null, and keeps the failures visible', () => {
    const { container } = render(<LabReportView report={report} />);
    expect(container.textContent).not.toMatch(/NaN|undefined|null/);
    expect(container.textContent).toMatch(/Why the parent failed/);
    expect(container.textContent).toMatch(/the pool is indistinguishable from zero/);
  });

  it('presents the registered next hypothesis and the next steps, with what is blocked on data', () => {
    const { container } = render(<LabReportView report={report} />);
    expect(container.textContent).toMatch(/Next hypothesis registered by the loop/);
    expect(container.textContent).toMatch(/Forward holdout/);
    expect(screen.getAllByText('blocked on data').length).toBeGreaterThanOrEqual(2);
  });

  it('shows the report-level caveats and provenance', () => {
    const { container } = render(<LabReportView report={report} />);
    expect(container.textContent).toMatch(/Read this before using the numbers/);
    expect(container.textContent).toMatch(/not point-in-time/);
    expect(container.textContent).toMatch(/Short interest is not used/);
  });

  it('renders a pass as a pass and never as a trading claim', () => {
    const r = structuredClone(report) as any;
    r.data.experiments[0].verdict = 'PASS_FOR_FORWARD_SHADOW';
    const { container } = render(<LabReportView report={r as LabReport} />);
    expect(screen.getByText('Pass: forward shadow only')).toBeTruthy();
    expect(container.textContent).not.toMatch(/guaranteed|will outperform|buy signal/i);
  });
});
