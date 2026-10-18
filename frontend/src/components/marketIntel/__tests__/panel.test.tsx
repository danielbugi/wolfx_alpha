// Market Overview v2 panel: every card shows stored values with their counts and provenance, and missing, thin,
// unavailable, failed or stale data is said out loud instead of being filled in.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import type { MarketHistory, MarketSnapshot } from '@/services/marketIntelligenceApi';
import { snapshot } from './fixture';

const api = vi.hoisted(() => ({
  snap: null as unknown,
  fail: false,
  history: { available: true, sessions: [] } as unknown,
}));
vi.mock('@/services/marketIntelligenceApi', () => ({
  marketIntelligenceApi: {
    getSnapshot: vi.fn(async () => {
      if (api.fail) throw new Error('network');
      return api.snap as MarketSnapshot;
    }),
    getHistory: vi.fn(async () => api.history as MarketHistory),
  },
}));

import MarketIntelligencePanel from '@/components/marketIntel/MarketIntelligencePanel';

beforeEach(() => {
  api.snap = snapshot();
  api.fail = false;
  api.history = {
    available: true,
    sessions: [
      { session_date: '2026-03-02', provenance: 'observed', regime_state: 'NEUTRAL', regime_score: -0.12 },
      { session_date: '2026-02-27', provenance: 'observed', regime_state: 'NEUTRAL', regime_score: 0.05 },
      { session_date: '2026-02-26', provenance: 'reconstructed', regime_state: 'RISK_ON', regime_score: 0.4 },
    ],
  };
});

describe('MarketIntelligencePanel', () => {
  it('shows session, provenance, capture time and observed history', async () => {
    render(<MarketIntelligencePanel priceDate="2026-03-02" />);
    const stamp = await screen.findByTestId('data-stamp');
    expect(stamp.textContent).toMatch(/Market snapshot for session\s*2026-03-02/);
    expect(stamp.textContent).toMatch(/captured 2026-03-03 06:45 \(UTC\+02\)/);
    expect(stamp.textContent).toMatch(/observed/);
    expect(await screen.findByText('2 observed sessions since 2026-02-27')).toBeTruthy();
    expect(screen.queryByTestId('mi-stale')).toBeNull();
  });

  it('regime: neutral words, every input with its weight, a missing input marked missing, the heuristic disclaimer', async () => {
    render(<MarketIntelligencePanel priceDate="2026-03-02" />);
    const card = (await screen.findByText('Market regime')).closest('section')!;
    const t = card.textContent ?? '';
    expect(t).toMatch(/Mixed/);
    expect(t).not.toMatch(/NEUTRAL|RISK_ON|RISK_OFF/); // model codes only in the tooltip
    expect(t).toMatch(/S&P 500 trend structure\s*2 of 3 conditions\s*25%/);
    expect(within(card).getAllByText('missing')).toHaveLength(1);
    expect(t).toMatch(/Inputs present: 90% of the weight/);
    expect(t).toMatch(/not a forecast/);
    expect(t).toMatch(/V1 heuristic/);
  });

  it('breadth: numerator of denominator with percentage, and a thin denominator withholds the percentage', async () => {
    render(<MarketIntelligencePanel priceDate="2026-03-02" />);
    const card = (await screen.findByText('Market breadth')).closest('section')!;
    const rows = within(card).getAllByTestId('breadth-row').map((r) => r.textContent);
    expect(rows[0]).toMatch(/Above 50-day average\s*830 of 2,000\s*41\.5%/);
    expect(rows[0]).toMatch(/100 not evaluable/);
    expect(rows[2]).toMatch(/400 of 900/);
    expect(rows[2]).not.toMatch(/44\.4%/);
    expect(rows[2]).toMatch(/too few evaluable stocks/);
    expect(card.textContent).toMatch(/60 \/ 45/);
    expect(card.textContent).toMatch(/of 1,850 stocks with 200\+ sessions/);
    expect(card.textContent).toMatch(/Advancing \/ declining \(1 session\)\s*1,200 \/ 850/);
    expect(card.textContent).toMatch(/close vs the previous close, of 2,100 stocks/);
    // every percentage row states its window
    expect(rows[0]).toMatch(/close above its 50-session simple average/);
    expect(rows[1]).toMatch(/close above its 200-session simple average/);
    expect(rows[2]).toMatch(/close above its close 20 sessions earlier/);
  });

  it('returns: index vs median stock with sample sizes; a missing return stays missing', async () => {
    render(<MarketIntelligencePanel priceDate="2026-03-02" />);
    const card = (await screen.findByText('Index vs typical stock')).closest('section')!;
    const rows = within(card).getAllByRole('row').slice(1).map((r) => r.textContent);
    expect(rows[1]).toMatch(/20\+1\.25%-1\.00%n=1,990-2\.25 pp/);
    expect(rows[2]).toMatch(/60\+3\.00%—n=0—/);
  });

  it('sectors: ranked order, unranked last with dashes, states in words, provenance of the sector map', async () => {
    render(<MarketIntelligencePanel priceDate="2026-03-02" />);
    const card = (await screen.findByText('Sector leadership')).closest('section')!;
    const rows = within(card).getAllByTestId('sector-row').map((r) => r.textContent ?? '');
    expect(rows.map((r) => r.match(/Alpha|Beta|Gamma|Delta/)?.[0])).toEqual(['Alpha', 'Gamma', 'Beta', 'Delta']);
    expect(rows[0]).toMatch(/Leading/);
    expect(rows[2]).toMatch(/Lagging/);
    expect(rows[3]).toMatch(/^—Delta/);
    expect(card.textContent).toMatch(/as known on the session/);
    expect(card.textContent).toMatch(/2,050 of 2,100 stocks classified/);
  });

  it('flags a snapshot older than the newest price session', async () => {
    render(<MarketIntelligencePanel priceDate="2026-03-03" />);
    expect((await screen.findByTestId('mi-stale')).textContent).toMatch(/snapshot is for 2026-03-02; prices on this page go to 2026-03-03/);
    expect(screen.getByText(/may be out of date/)).toBeTruthy();
  });

  it('shows the reconstructed note when the payload is not observed', async () => {
    api.snap = snapshot({ provenance: 'reconstructed', observed: false, provenance_note: 'Reconstructed after the fact from stored history.' });
    render(<MarketIntelligencePanel priceDate="2026-03-02" />);
    expect(await screen.findByText('Reconstructed after the fact from stored history.')).toBeTruthy();
  });

  it('says unavailable when the backend has no snapshot, and shows nothing invented', async () => {
    api.snap = { schema: 'mi_payload_v1', available: false, reason: 'not_provisioned', message: 'Market Intelligence storage is not provisioned on this database yet.' };
    render(<MarketIntelligencePanel priceDate="2026-03-02" />);
    expect((await screen.findByTestId('mi-unavailable')).textContent).toMatch(/not available: Market Intelligence storage is not provisioned on this database yet\.$/);
    expect(screen.queryByText('Market regime')).toBeNull();
  });

  it('shows a retryable error when the request fails', async () => {
    api.fail = true;
    render(<MarketIntelligencePanel priceDate="2026-03-02" />);
    expect(await screen.findByText('Market intelligence unavailable')).toBeTruthy();
  });

  it('contains no forecast, recommendation or performance claim', async () => {
    const { container } = render(<MarketIntelligencePanel priceDate="2026-03-02" />);
    await screen.findByText('Sector leadership');
    const t = container.textContent ?? '';
    for (const re of [/\bbuy\b/i, /\bsell\b/i, /outperform/i, /will (rise|fall|go)/i, /expected to/i, /recommend/i, /signal/i, /opportunit/i]) {
      expect(t).not.toMatch(re);
    }
  });
});
