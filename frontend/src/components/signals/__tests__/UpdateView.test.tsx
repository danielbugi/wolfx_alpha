import React from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { UpdateDetail, UpdateSummary } from '@/components/signals/UpdateView';
import type { PublicUpdate } from '@/services/publicSignalsApi';

const U: PublicUpdate = {
  key: 'POWI/2026-09-30/margin-expansion',
  symbol: 'POWI',
  company_name: 'Power Integrations, Inc.',
  event_type: 'margin_expansion',
  label: 'Operating margin expanded',
  quarter_end: '2026-09-30',
  fiscal_label: 'Fiscal Q3 2026',
  public_date: '2026-11-05',
  date_source: 'SEC filing accepted',
  sector: 'Technology',
  industry: 'Semiconductors',
  market_cap: { value: '$2.17B', as_of: '2026-11-05' },
  figures: [
    { label: 'Operating margin', value: '15.2%', compare: 'a year earlier: -3.3%' },
    { label: 'Margin change', value: '+18.6 points', compare: null },
    { label: 'Revenue growth, year over year', value: '+5.5%', compare: null },
  ],
  why: 'Operating margin moved from -3.3% to 15.2% in a year while revenue changed +5.5%: more of each dollar of sales now reaches operating income.',
  links: {
    finviz: 'https://finviz.com/quote.ashx?t=POWI',
    detail: 'https://dashboard.first-light.finance/signals/POWI/2026-09-30/margin-expansion',
    sec: 'https://www.sec.gov/Archives/edgar/data/833640/000083364026000156/0000833640-26-000156-index.htm',
    sec_kind: 'filing',
  },
  news: [{ published_at: '2026-11-04T23:03:00Z', source: 'Fiscal.ai', headline: 'Power Integrations, Inc. Reports Q3 2026 Financial Results', url: 'https://example.com/r' }],
  content_hash: 'a'.repeat(64),
  render_version: 'fu1',
};

const ADVICE = /\b(buy\w*|sell\w*|targets?|stops?|recommend\w*|signals?|should|alpha|returns?|outperform\w*)\b/i;

describe('UpdateDetail', () => {
  it('shows the reported facts, the date source and only SEC + Finviz as outside links', () => {
    render(<UpdateDetail u={U} status="published" corrections={[]} firstPublishedAt="2026-11-07T01:00:00Z" />);
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('POWI');
    expect(screen.getByText('15.2%')).toBeTruthy();
    expect(screen.getByText('a year earlier: -3.3%')).toBeTruthy();
    expect(screen.getByText(/Public Nov 5, 2026 \(SEC filing accepted\)/)).toBeTruthy();
    expect(screen.getByText('Fiscal.ai · Nov 4, 2026, 23:03 UTC')).toBeTruthy();
    const outside = screen.getAllByRole('link').map((a) => a.getAttribute('href'));
    expect(outside).toEqual([U.news[0].url, U.links.sec, U.links.finviz]);
    for (const a of screen.getAllByRole('link')) expect(a.getAttribute('rel')).toContain('noopener');
    expect(screen.getByText('SEC filing')).toBeTruthy();
  });

  it('says nothing that reads as advice and shows no internal numbers', () => {
    const { container } = render(<UpdateDetail u={U} status="published" corrections={[]} firstPublishedAt="2026-11-07T01:00:00Z" />);
    const text = (container.textContent ?? '').replace(U.news[0].headline, '');
    expect(text).not.toMatch(ADVICE);
    expect(text).not.toMatch(/strength|score|severity|percentile|research signal/i);
  });

  it('shows the correction history and the withdrawn banner', () => {
    render(<UpdateDetail u={U} status="withdrawn" firstPublishedAt="2026-11-07T01:00:00Z"
                         corrections={[{ version: 2, kind: 'restated', changed: ['Operating margin', 'why'], at: '2026-11-09T01:00:00Z' },
                                       { version: 3, kind: 'withdrawn', changed: ['event'], at: '2026-11-10T01:00:00Z' }]} />);
    expect(screen.getByRole('status').textContent).toMatch(/withdrawn/i);
    expect(screen.getByText('Nov 9, 2026, 01:00 UTC: Figures revised (Operating margin, why)')).toBeTruthy();
    expect(screen.getByText('Nov 10, 2026, 01:00 UTC: Withdrawn')).toBeTruthy();
  });

  it('omits what is missing instead of inventing it', () => {
    const bare: PublicUpdate = { ...U, company_name: null, sector: null, industry: null, market_cap: null, news: [], links: { finviz: U.links.finviz, detail: U.links.detail } };
    render(<UpdateDetail u={bare} status="published" corrections={[]} firstPublishedAt="2026-11-07T01:00:00Z" />);
    expect(screen.queryByText(/News at the time/)).toBeNull();
    expect(screen.queryByRole('link', { name: /SEC filing/ })).toBeNull();
    expect(screen.queryByText(/Market cap/)).toBeNull();
    expect(screen.queryByText(/n\/a|undefined|null/)).toBeNull();
  });
});

describe('UpdateSummary', () => {
  it('links to the permanent page and marks corrections', () => {
    render(<UpdateSummary u={U} status="corrected" />);
    expect(screen.getByRole('link', { name: 'POWI' }).getAttribute('href')).toBe('/signals/POWI/2026-09-30/margin-expansion');
    expect(screen.getByText('Corrected')).toBeTruthy();
  });
});
