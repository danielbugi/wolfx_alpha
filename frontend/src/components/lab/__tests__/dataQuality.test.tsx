/* eslint-disable @typescript-eslint/no-explicit-any */
// The fixture is the payload the real service produced from a scratch copy of the 20-company SEC sample; it exercises the rendering only.
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { DataQualityView } from '@/components/lab/DataQuality';
import type { DataQualityPayload } from '@/services/dataQualityApi';
import payloadJson from './fixtures/data_quality.json';

const payload = structuredClone(payloadJson) as unknown as DataQualityPayload;
afterEach(() => cleanup());

describe('Data quality view', () => {
  it('says what each event family may be used for and keeps non point-in-time data exploratory', () => {
    const { container } = render(<DataQualityView data={payload} />);
    expect(container.textContent).toContain('SEC, filing-version-aware');
    expect(container.textContent).toContain('point in time');
    expect(screen.getAllByText('Exploratory only').length).toBe(3); // coverage is far below the bar, so even the point-in-time family is exploratory for now
    expect(container.textContent).toContain('needs 80%');
    expect(container.textContent).not.toMatch(/NaN|undefined|null/);
  });

  it('shows coverage, the foreign-issuer caveat and the reconciliation causes without hiding unexplained ones', () => {
    const { container } = render(<DataQualityView data={payload} />);
    expect(container.textContent).toContain('Foreign issuers are not assumed');
    expect(container.textContent).toContain('history coverage');
    expect(container.textContent).toContain('Thresholds are not tuned');
    expect(screen.getByText('By sector')).toBeTruthy();
  });

  it('shows the confirmation badge only when the service says so', () => {
    const eligible = structuredClone(payload) as any;
    eligible.namespaces.find((n: any) => n.point_in_time).role_now = 'confirmation_eligible';
    render(<DataQualityView data={eligible} />);
    expect(screen.getAllByText('Eligible for confirmation').length).toBe(1);
  });

  it('renders an explicit message when the data is unavailable', () => {
    render(<DataQualityView data={{ schema: 'data_quality_v1', available: false, reason: 'the database is unavailable right now' }} />);
    expect(screen.getByText(/not available: the database is unavailable right now/)).toBeTruthy();
  });
});
