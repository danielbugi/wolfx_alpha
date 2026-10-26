// Stock Peers = the Screener's own query: the request it sends is exactly the query the Screener sends for its "All ...
// in the Screener" link (only the page size differs), for the stock's Screener sector and session; rows render the
// Screener's values; missing values stay dashes; no context means no guessed sector.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import type { QueryPairs, ScreenerRow } from '@/services/screenerV2Api';

const scr = vi.hoisted(() => ({ search: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }) }));
vi.mock('@/services/screenerV2Api', () => ({ screenerV2Api: scr }));
vi.mock('@/components/dashboard/SymbolHoverLink', () => ({ default: ({ symbol }: { symbol: string }) => <span>{symbol}</span> }));

import PeersPanel, { PEER_COUNT } from '@/components/stock/PeersPanel';
import { fromSearchParams, toQueryPairs } from '@/lib/screenerV2';

const row = (symbol: string, over: Partial<ScreenerRow> = {}) =>
  ({ symbol, sector: 'Technology', close: 100, change_1d: 1.5, market_cap: 2e12, pe_ratio: 30, rs_20: 80, quality_grade: 'B', ...over }) as ScreenerRow;

beforeEach(() => {
  scr.search.mockReset();
  scr.search.mockResolvedValue({ rows: [row('MSFT'), row('AAPL'), row('NVDA', { change_1d: null, pe_ratio: null, rs_20: null })] });
});

describe('PeersPanel', () => {
  it('sends the Screener query of its own link, for the stock sector and session', async () => {
    render(<PeersPanel symbol="AAPL" sector="Technology" session="2026-10-23" contextState="ready" />);
    await screen.findAllByTestId('peer-row');
    const sent = scr.search.mock.calls[0][0] as QueryPairs;
    const href = screen.getByTestId('peers-screener-link').getAttribute('href')!;
    expect(href).toBe('/screener?session=2026-10-23&sort=market_cap&order=desc&sector=Technology');
    const viaScreener = toQueryPairs(fromSearchParams(new URLSearchParams(href.split('?')[1])));
    const strip = (p: QueryPairs) => p.filter(([k]) => k !== 'page_size');
    expect(strip(sent)).toEqual(strip(viaScreener));
    expect(sent).toContainEqual(['page_size', String(PEER_COUNT + 1)]);
  });

  it('lists the peers without the stock itself, with Screener values, dashes for missing ones and the session', async () => {
    render(<PeersPanel symbol="AAPL" sector="Technology" session="2026-10-23" contextState="ready" />);
    const rows = await screen.findAllByTestId('peer-row');
    expect(rows.map((r) => r.textContent?.slice(0, 4))).toEqual(['MSFT', 'NVDA']);
    expect(rows[1].textContent).toContain('—');
    expect(screen.getByTestId('peers-avg').textContent).toContain('avg of 1');   // NVDA's missing change is not a 0
    expect(screen.getByTestId('data-stamp').textContent).toContain('2026-10-23');
  });

  it('never guesses a sector: no Screener row means no peers, and a failed context says so', async () => {
    const { unmount } = render(<PeersPanel symbol="AAPL" sector={null} session="2026-10-23" contextState="ready" />);
    expect(screen.getByText(/No Screener sector for this stock/)).toBeTruthy();
    unmount();
    render(<PeersPanel symbol="AAPL" sector={undefined} session={undefined} contextState="unavailable" />);
    expect(screen.getByText('Peers are unavailable right now.')).toBeTruthy();
    await waitFor(() => expect(scr.search).not.toHaveBeenCalled());
  });
});
