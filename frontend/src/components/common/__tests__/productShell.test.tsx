// The product shell pieces: the disclaimer appears on product routes only, DataStamp states only what it is given,
// and breakout markers lose their outcome fields.
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import DataStamp from '@/components/common/DataStamp';
import ProductDisclaimer, { PRODUCT_DISCLAIMER } from '@/components/common/ProductDisclaimer';
import { neutralBreakoutMarkers } from '@/lib/productSafe';

const nav = vi.hoisted(() => ({ pathname: '/' }));
vi.mock('next/navigation', () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ status: 'authenticated', user: { email: 'o@example.com', role: 'owner' } }) }));
vi.mock('@/components/layout/Sidebar', () => ({ default: () => null }));
vi.mock('@/components/layout/MobileNav', () => ({ default: () => null }));
vi.mock('@/lib/stockNav', () => ({ useStockNavCapture: () => {} }));

import AuthGate from '@/components/auth/AuthGate';

describe('ProductDisclaimer via AuthGate', () => {
  it.each(['/', '/screener', '/alerts', '/markets', '/markets/GC=F', '/earnings', '/stock/AAPL'])('is shown on product route %s', (p) => {
    nav.pathname = p;
    render(<AuthGate>page</AuthGate>);
    expect(screen.getByTestId('product-disclaimer').textContent).toBe(PRODUCT_DISCLAIMER);
  });

  it.each(['/lab', '/strategy', '/strategies', '/momentum', '/track-record', '/telegram', '/system-health', '/ml-stats', '/performance', '/users'])(
    'is not shown on internal route %s',
    (p) => {
      nav.pathname = p;
      render(<AuthGate>page</AuthGate>);
      expect(screen.queryByTestId('product-disclaimer')).toBeNull();
    },
  );

  it('says information only and not investment advice', () => {
    render(<ProductDisclaimer />);
    expect(PRODUCT_DISCLAIMER).toMatch(/Information only/);
    expect(PRODUCT_DISCLAIMER).toMatch(/Not investment advice/);
  });
});

describe('DataStamp', () => {
  it('shows the date, source and provenance it is given', () => {
    render(<DataStamp asOf="2026-10-16" source="end-of-day prices" provenance="observed" />);
    const t = screen.getByTestId('data-stamp').textContent ?? '';
    expect(t).toMatch(/Data as of\s*2026-10-16/);
    expect(t).toMatch(/end-of-day prices/);
    expect(t).toMatch(/observed/);
  });

  it('says unavailable for a missing date and adds nothing it was not given', () => {
    render(<DataStamp asOf={null} />);
    const t = screen.getByTestId('data-stamp').textContent ?? '';
    expect(t).toMatch(/unavailable/);
    expect(t).not.toMatch(/observed|reconstructed|\d{4}-\d{2}-\d{2}/);
  });

  it('flags stale data only when told to', () => {
    const { rerender } = render(<DataStamp asOf="2026-10-16" />);
    expect(screen.queryByText(/may be out of date/)).toBeNull();
    rerender(<DataStamp asOf="2026-10-16" stale />);
    expect(screen.getByText(/may be out of date/)).toBeTruthy();
  });
});

describe('neutralBreakoutMarkers', () => {
  it('keeps the dates and types and drops every outcome field', () => {
    const out = neutralBreakoutMarkers([
      { date: '2026-08-03', breakout_type: 'bullish', entry_price: 40, success: true, max_gain_10d: 9, max_loss_10d: -1, days_to_peak: 4 },
    ]);
    expect(out).toEqual([{ date: '2026-08-03', breakout_type: 'bullish', entry_price: null, success: null, max_gain_10d: null, max_loss_10d: null, days_to_peak: null }]);
  });
});
