/* eslint-disable @typescript-eslint/no-explicit-any */
// The fixture is a payload produced by the real aggregation module (mechanism/research/paper_dashboard.py) from hand-built SYNTHETIC rows; it exercises the rendering only and is not production data.
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ForwardTrackerView } from '@/components/lab/ForwardTracker';
import type { ForwardTrackerPayload } from '@/services/forwardTrackerApi';
import payloadJson from './fixtures/forward_tracker.json';
import pendingOnlyJson from './fixtures/forward_tracker_pending_only.json';

const payload = structuredClone(payloadJson) as unknown as ForwardTrackerPayload;
afterEach(() => cleanup());

describe('Forward paper tracker view', () => {
  it('labels itself as live forward paper observation and keeps replay out', () => {
    const { container } = render(<ForwardTrackerView data={payload} />);
    expect(container.textContent).toContain('simulated positions only');
    expect(container.textContent).toContain(`Live forward observation since ${payload.since}`);
    expect(container.textContent).toContain('never mixed into these numbers');
    expect(container.textContent?.replace(/not mixed|never mixed/gi, '')).not.toMatch(/profitable/i);
  });

  it('shows the latest session, every variant in the comparison, and renders only real numbers', () => {
    const { container } = render(<ForwardTrackerView data={payload} />);
    const ls = (payload as any).latest_session;
    const tile = screen.getByText('Latest processed session').parentElement as HTMLElement;
    expect(tile.textContent).toContain(ls.session_date);
    expect(container.textContent).not.toMatch(/NaN|undefined|null/);
    expect(screen.getAllByRole('table').length).toBe(5);
    const cmp = screen.getByText(/Variant comparison/).nextElementSibling as HTMLElement;
    const rows = within(cmp).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(5);
    for (const c of (payload as any).comparison) expect(cmp.textContent).toContain(String(c.name).replace(/_/g, ' '));
    expect(cmp.textContent).toContain('n too small for an interval');
  });

  it('shows realised net R at all three costs, read from the payload', () => {
    render(<ForwardTrackerView data={payload} />);
    const t = screen.getByText(/Closed positions: realised net R by round-trip cost/).nextElementSibling as HTMLElement;
    for (const h of ['Net R, 0 bps', 'Net R, 10 bps (primary)', 'Net R, 25 bps']) expect(t.textContent).toContain(h);
    const v = (payload as any).variants[0];
    expect(within(t).getAllByRole('row')[1].textContent).toContain(String(v.closed.n));
  });

  it('marks open positions as marks, not results, and counts unmarked ones', () => {
    render(<ForwardTrackerView data={payload} />);
    expect(screen.getByText(/an open position is not a result/)).toBeTruthy();
    const t = screen.getByText(/Open positions by variant/).nextElementSibling?.nextElementSibling as HTMLElement;      // heading, then its note, then the table
    expect(t.textContent).toContain('Unmarked');
  });

  it('labels the big-winner analysis as hindsight', () => {
    const { container } = render(<ForwardTrackerView data={payload} />);
    expect(container.textContent).toContain('Hindsight: measured on bars after the decision, not known when the position was opened');
    expect(container.textContent).toContain('Peak >= 5R');
  });

  it('shows data health, the missing-context reason and the completeness table', () => {
    const { container } = render(<ForwardTrackerView data={payload} />);
    for (const h of (payload as any).health) expect(container.textContent).toContain(h.detail);
    expect(container.textContent).toContain('no event has been recorded yet (no job feeds this source)');
    const comp = screen.getByText(/Completeness and missing context/).nextElementSibling as HTMLElement;
    expect(comp.textContent).toContain('Market regime attached');
    expect(within(comp).getAllByRole('row').length).toBe(1 + (payload as any).completeness.sessions.length);
  });

  it('states the observation window of MFE, MAE and the big-winner metrics', () => {
    const { container } = render(<ForwardTrackerView data={payload} />);
    expect(container.textContent).toContain("each closed position's own holding period, entry to exit");
    expect(container.textContent).toContain('since entry, up to the last loaded bar (still running)');
    expect(container.textContent).toContain('Bars observed since entry (mean / max)');
    expect(container.textContent).toContain('Bars observed after exit (mean / max)');
    expect(container.textContent).toContain('Short windows understate both');
  });

  it('distinguishes pending entry from open from closed', () => {
    const { container } = render(<ForwardTrackerView data={payload} />);
    expect(container.textContent).toContain('Pending entry = registered, enters at the next session');
    const cmp = screen.getByText(/Variant comparison/).nextElementSibling as HTMLElement;
    for (const h of ['Pending entry', 'Open', 'Closed']) expect(cmp.textContent).toContain(h);
  });

  it('renders the right-after-activation state (everything pending, nothing entered) without inventing numbers', () => {
    const { container } = render(<ForwardTrackerView data={pendingOnlyJson as unknown as ForwardTrackerPayload} />);
    expect(container.textContent).not.toMatch(/NaN|undefined|null/);
    const cmp = screen.getByText(/Variant comparison/).nextElementSibling as HTMLElement;
    const rows = within(cmp).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(5);
    for (const r of rows) {
      const cells = within(r).getAllByRole('cell').map((c) => c.textContent);
      expect(cells[2]).toBe('3');                       // pending entry
      expect(cells[3]).toBe('0');                       // open
      expect(cells[4]).toBe('0');                       // closed
      expect(cells[5]).toBe('—');                       // no realised result
    }
  });

  it('answers an unavailable payload with the reason and no tables', () => {
    const { container } = render(<ForwardTrackerView data={{ schema: 'x', source: 'forward', available: false, reason: 'no forward session has been processed yet', sessions_processed: 0 }} />);
    expect(container.textContent).toContain('Forward tracker data is not available: no forward session has been processed yet');
    expect(screen.queryAllByRole('table')).toHaveLength(0);
  });
});

describe('Quant Lab page tabs', () => {
  it('keeps the historical research reports as the default view and loads the forward view only on request', async () => {
    vi.resetModules();
    const get = vi.fn().mockResolvedValue(payload);
    vi.doMock('@/services/forwardTrackerApi', () => ({ forwardTrackerApi: { get } }));
    vi.doMock('@/services/labApi', () => ({ labApi: { list: vi.fn().mockResolvedValue({ available: true, notice: 'Research only.', reports: [], invalid: [] }), get: vi.fn() } }));
    vi.doMock('@/hooks/usePagePerf', () => ({ usePagePerf: () => ({ markLoaded: vi.fn() }) }));
    const { default: LabPage } = await import('@/app/lab/page');
    render(<LabPage />);
    await waitFor(() => expect(screen.getByRole('tab', { name: /Research reports \(historical\)/ }).getAttribute('aria-selected')).toBe('true'));
    expect(get).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('tab', { name: /Forward paper tracker \(live\)/ }));
    await waitFor(() => expect(screen.getByText(/Live forward observation since/)).toBeTruthy());
    expect(get).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('tab', { name: /Forward paper tracker \(live\)/ }).getAttribute('aria-selected')).toBe('true');
  });
});
