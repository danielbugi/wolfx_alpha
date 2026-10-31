/* eslint-disable @typescript-eslint/no-explicit-any */
// The fixture is the real report served at /api/lab/reports/fundamental_inflection_research (backend/lab_reports): a snapshot, so this test needs no network.
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import LabReportView from '@/components/lab/LabReportView';
import type { LabReport } from '@/services/labApi';
import reportJson from './fixtures/fundamental_inflection_research.json';

const report = structuredClone(reportJson) as unknown as LabReport;
afterEach(() => cleanup());

describe('Fundamental inflection research report', () => {
  it('shows the verdict, the declared checks and the baselines', () => {
    const { container } = render(<LabReportView report={report} />);
    expect(screen.getAllByText('No edge demonstrated').length).toBeGreaterThan(0);
    expect(container.textContent).toMatch(/Decision checks/);
    expect(container.textContent).toMatch(/Earnings surprise/);
    expect(container.textContent).toMatch(/research only/);
  });

  it('lists the registered follow-ups and the data limits, never NaN or undefined', () => {
    const { container } = render(<LabReportView report={report} />);
    const d = (report as any).data;
    for (const f of d.followups) expect(container.textContent).toContain(f.label);
    expect(container.textContent).toMatch(/complete window/);
    expect(container.textContent).not.toMatch(/NaN|undefined|null/);
  });
});
