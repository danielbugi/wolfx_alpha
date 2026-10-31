/* eslint-disable @typescript-eslint/no-explicit-any */
// Fundamental Inflection UI against fixtures produced by the real engine and read API (a snapshot of real stored events), so no network is needed.
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import eventsJson from './fixtures/inflection_events.json';
import stockJson from './fixtures/inflection_stock.json';
import summaryJson from './fixtures/inflection_summary.json';

const auth = vi.hoisted(() => ({ value: null as null | { user: { role: string } | null } }));
vi.mock('@/contexts/AuthContext', () => ({ useOptionalAuth: () => auth.value }));
const perf = vi.hoisted(() => ({ markLoaded: () => {} }));      // stable, like the real hook (the page lists it in its effect dependencies)
vi.mock('@/hooks/usePagePerf', () => ({ usePagePerf: () => perf }));
vi.mock('next/link', () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

const api = vi.hoisted(() => ({ events: vi.fn(), summary: vi.fn(), stock: vi.fn() }));
vi.mock('@/services/inflectionApi', async (orig) => ({ ...(await orig<any>()), inflectionApi: api }));

import InflectionPage from '@/app/inflection/page';
import EventCard from '@/components/inflection/EventCard';
import OwnerInflection from '@/components/inflection/OwnerInflection';
import TrendCharts from '@/components/inflection/TrendCharts';
import { basisLabel, fmtMetric, fmtMoney, fmtPct, freshnessText } from '@/lib/inflectionFormat';

const events = eventsJson as any;
const stock = stockJson as any;
const summary = summaryJson as any;

beforeEach(() => {
  api.events.mockReset().mockResolvedValue(events);
  api.summary.mockReset().mockResolvedValue(summary);
  api.stock.mockReset().mockResolvedValue(stock);
  auth.value = { user: { role: 'owner' } };
});
afterEach(() => cleanup());

describe('formatting', () => {
  it('never turns a missing number into zero', () => {
    expect(fmtMoney(null)).toBe('—');
    expect(fmtPct(undefined)).toBe('—');
    expect(fmtMetric({ label: 'x', current: null, previous: null, unit: 'pct' }, 'current')).toBe('—');
    expect(fmtPct(0.1234)).toBe('+12.3%');
    expect(fmtMoney(-3_400_000)).toBe('-$3.4M');
    expect(freshnessText(0)).toBe('today');
    expect(freshnessText(1)).toBe('yesterday');
  });
  it('labels an assumed availability date as an assumption and a real report date as such', () => {
    expect(basisLabel('assumed_lag_75d')).toEqual({ text: 'assumed 75-day lag after quarter end', assumed: true });
    expect(basisLabel('earnings_calendar').assumed).toBe(false);
    expect(basisLabel('repricing_window_end(earnings_calendar)').text).toMatch(/repricing window/);
    expect(basisLabel('sec_filing').text).toBe('SEC filing date');
  });
});

describe('EventCard (a real stored event)', () => {
  it('shows what changed, how strong, when it was public and how that date was determined, and says it is a research signal', () => {
    const e = events.events[0];
    const { container } = render(<EventCard e={e} />);
    const t = container.textContent ?? '';
    expect(t).toContain(e.symbol);
    expect(t).toContain(e.label);
    expect(t).toContain('Research signal');
    expect(t).toContain(`strength ${Math.round(e.strength)}`);
    expect(t).toContain(`Public ${e.available_at}`);
    for (const l of e.explanation) expect(t).toContain(l);
    expect(t).not.toMatch(/NaN|undefined|null|\[object/);
    expect(screen.getByRole('link', { name: e.symbol }).getAttribute('href')).toBe(`/stock/${e.symbol}`);
  });
  it('marks an assumed date and partial data instead of hiding them', () => {
    const e = { ...events.events[0], availability_basis: 'assumed_lag_75d', data_quality: 'partial', revision_count: 2 };
    const { container } = render(<EventCard e={e} />);
    expect(container.textContent).toMatch(/an assumption, not a filing date/);
    expect(container.textContent).toMatch(/partial data/);
    expect(container.textContent).toMatch(/restated 2x/);
  });
  it('shows dashes for missing price and valuation context', () => {
    const e = { ...events.events[0], reaction_2d: null, excess_vs_sector_since_publication: null, valuation: { ps_ratio: null, pe_ratio: null, ps_percentile_own_history: null, ps_vs_sector_median: null, ps_ratio_at_publication: null, snapshot_date: null } };
    const { container } = render(<EventCard e={e} />);
    expect((container.textContent ?? '').match(/—/g)?.length).toBeGreaterThanOrEqual(3);
  });
});

describe('Inflection screener page', () => {
  it('lists real events with the summary, and every card links to its Stock Intelligence page', async () => {
    const { container } = render(<InflectionPage />);
    await waitFor(() => expect(screen.getAllByTestId('inflection-event').length).toBe(events.events.length));
    expect(screen.getByTestId('inflection-summary').textContent).toContain(`${summary.events.toLocaleString('en-US')} events`);
    expect(container.textContent).toMatch(/Research signals<\/strong>|Research signals/);
    for (const e of events.events) expect(container.querySelector(`a[href="/stock/${e.symbol}"]`)).toBeTruthy();
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });

  it('sends the chosen filters to the API and refetches from the start', async () => {
    render(<InflectionPage />);
    await waitFor(() => expect(api.events).toHaveBeenCalled());
    fireEvent.click(screen.getByRole('button', { name: 'Margin expansion' }));
    fireEvent.change(screen.getByLabelText('Minimum strength'), { target: { value: '75' } });
    fireEvent.change(screen.getByLabelText('Sector'), { target: { value: 'Technology' } });
    fireEvent.change(screen.getByLabelText('Market cap'), { target: { value: String(2e9) } });
    await waitFor(() => {
      const last = api.events.mock.calls[api.events.mock.calls.length - 1][0];
      expect(last).toMatchObject({ event_type: ['margin_expansion'], min_strength: 75, sector: 'Technology', min_market_cap: 2e9, offset: 0, sort: 'strength' });
      expect(last.since).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    });
    expect(screen.getByRole('button', { name: 'Margin expansion' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('pages with Show more and keeps what is already there', async () => {
    api.events.mockResolvedValueOnce({ ...events, total: 12, count: 6, offset: 0 }).mockResolvedValueOnce({ ...events, events: events.events.map((e: any) => ({ ...e, id: e.id + 1000 })), total: 12, count: 6, offset: 6 });
    render(<InflectionPage />);
    await waitFor(() => expect(screen.getAllByTestId('inflection-event').length).toBe(6));
    fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
    await waitFor(() => expect(screen.getAllByTestId('inflection-event').length).toBe(12));
    expect(api.events.mock.calls[1][0].offset).toBe(6);
    expect(screen.queryByRole('button', { name: 'Show more' })).toBeNull();
  });

  it('says the engine has no data yet, rather than showing an empty list, when the tables are not installed', async () => {
    api.events.mockResolvedValue({ method_version: 'inflection_v1', available: false, reason: 'inflection tables are not installed yet', total: 0, count: 0, offset: 0, events: [] });
    api.summary.mockResolvedValue({ ...summary, available: false });
    render(<InflectionPage />);
    await waitFor(() => expect(screen.getByText(/inflection tables are not installed yet/)).toBeTruthy());
    expect(screen.queryByTestId('inflection-event')).toBeNull();
  });

  it('shows an empty state and an error state in plain words', async () => {
    api.events.mockResolvedValue({ ...events, total: 0, count: 0, events: [] });
    const { unmount } = render(<InflectionPage />);
    await waitFor(() => expect(screen.getByText(/No inflection events match these filters/)).toBeTruthy());
    unmount();
    api.events.mockRejectedValue(new Error('x'));
    render(<InflectionPage />);
    await waitFor(() => expect(screen.getByText(/could not be loaded/)).toBeTruthy());
  });
});

describe('Stock page section (owner only)', () => {
  it('renders nothing for anyone but the owner', () => {
    auth.value = { user: { role: 'collaborator' } };
    expect(render(<OwnerInflection symbol="OSIS" />).container.textContent).toBe('');
    expect(api.stock).not.toHaveBeenCalled();
  });

  it('shows the business next to the price, the flagged events and the quarterly trend from real stored data', async () => {
    const { container } = render(<OwnerInflection symbol={stock.symbol} />);
    await waitFor(() => expect(screen.getByText('Inflection score')).toBeTruthy());
    const t = container.textContent ?? '';
    expect(t).toContain('Fundamental inflection');
    expect(t).toContain('Research signal');
    expect(t).toContain('Revenue growth (YoY)');
    expect(t).toContain('Price vs sector since');
    expect(screen.getAllByTestId('inflection-event').length).toBe(Math.min(4, stock.events.length));
    const trend = screen.getByTestId('inflection-trend');
    for (const label of ['Revenue by quarter', 'Operating margin by quarter', 'Net income by quarter', 'Free cash flow by quarter']) expect(within(trend).getByLabelText(label)).toBeTruthy();
    expect(t).toMatch(/quarters stored \(\d+ with partial data\)/);
    expect(t).not.toMatch(/NaN|undefined|null/);
  });

  it('says plainly when nothing has been analysed for the stock', async () => {
    api.stock.mockResolvedValue({ symbol: 'ZZZ', method_version: 'inflection_v1', available: false, reason: 'inflection tables are not installed yet', events: [], trend: [], latest_score: null, latest_quarter_end: null });
    render(<OwnerInflection symbol="ZZZ" />);
    await waitFor(() => expect(screen.getByText(/not installed yet/)).toBeTruthy());
    expect(screen.getByText(/Nothing is shown rather than a guess/)).toBeTruthy();
  });
});

describe('TrendCharts', () => {
  it('draws a missing quarter as a flat line, never a zero bar, and flags partial quarters in the tooltip', () => {
    const pts = stock.trend.map((p: any, i: number) => (i === 1 ? { ...p, revenue: null, data_quality: 'partial' } : p));
    const { container } = render(<TrendCharts trend={pts} />);
    expect(container.querySelectorAll('[aria-label="no data"]').length).toBeGreaterThanOrEqual(1);
    expect(Array.from(container.querySelectorAll('[title]')).some((n) => /partial data/.test(n.getAttribute('title') ?? ''))).toBe(true);
  });
  it('says so when there is no history', () => {
    render(<TrendCharts trend={[]} />);
    expect(screen.getByText(/no quarterly history stored/)).toBeTruthy();
  });
});
