// Alerts page against both API shapes: the backend before the balance-sheet change (deploy/rollback window) and after.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const api = vi.hoisted(() => ({ scan: vi.fn() }));
const auth = vi.hoisted(() => ({ role: 'owner' as string | null }));
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ user: auth.role ? { id: 1, email: 'o@x.test', role: auth.role } : null }) }));
vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return { ...actual, deepValueApi: api };
});

import AlertsPage from '@/app/alerts/page';

// Exactly the old backend's entry shape: no balance_sheet / price_date / year_ago_*, no excluded_balance_sheet.
const oldEntry = {
  symbol: 'OLDCO', sector: 'Industrials', current_price: 10.5, distance_from_low_pct: 3.2, donchian_low_12m: 10.17,
  valuation_score: 15, financial_health_score: 18, overall_quality_score: 70, quality_grade: 'C', pe_ratio: 11.2,
  market_cap: 2e9, is_turnaround: false,
};
const oldTurnaround = {
  ...oldEntry, symbol: 'OLDTURN', is_turnaround: true,
  latest_quarter: '2026-06-30', latest_net_income: 5e6, previous_quarter: '2026-03-31', previous_net_income: -2e6,
};

const balanceSheet = (overrides = {}) => ({
  quarter: '2026-06-30',
  checks: { equity: 'pass', leverage: 'warn', liquidity: 'pass', cash_flow: 'pass', ...overrides },
  values: { debt_to_equity: 2.4, current_ratio: 1.5, operating_cash_flow: 3e6, cash_runway_quarters: null },
});

beforeEach(() => api.scan.mockReset());

describe('Alerts page', () => {
  it('renders an OLD-backend response without crashing (new frontend deployed / backend rolled back)', async () => {
    api.scan.mockResolvedValue({ turnaround_alerts: [oldTurnaround], deep_value_watch: [oldEntry] });
    const { container } = render(<AlertsPage />);
    expect(await screen.findByText('OLDCO')).toBeTruthy();
    expect(screen.getByText('OLDTURN')).toBeTruthy();
    expect(container.textContent).not.toMatch(/undefined|NaN/);
    expect(container.textContent).not.toMatch(/stocks excluded/);  // count unknown -> not shown, not "0"
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);    // balance sheet unknown, shown as such
    expect(screen.getByText(/Same quarter last year: —/)).toBeTruthy();
  });

  it('renders the NEW response: check chips, excluded count, seasonal flag, price date', async () => {
    api.scan.mockResolvedValue({
      turnaround_alerts: [{
        ...oldTurnaround, symbol: 'NEWTURN', price_date: '2026-09-23', balance_sheet: balanceSheet(),
        year_ago_quarter: '2025-06-30', year_ago_net_income: 4e6,
      }],
      deep_value_watch: [{ ...oldEntry, symbol: 'NEWCO', price_date: '2026-09-23', balance_sheet: balanceSheet({ leverage: 'n/a' }) }],
      excluded_balance_sheet: 49,
    });
    const { container } = render(<AlertsPage />);
    expect(await screen.findByText('NEWCO')).toBeTruthy();
    expect(container.textContent).toMatch(/49 otherwise-matching stocks excluded/);
    expect(container.textContent).toMatch(/was also profitable/);
    expect(container.textContent).toMatch(/close 2026-09-23/);
    expect(screen.getAllByText('D/E').length).toBeGreaterThanOrEqual(2);
    expect(container.textContent).not.toMatch(/undefined|NaN/);
  });

  it('no longer sends the deprecated health floor', async () => {
    api.scan.mockResolvedValue({ turnaround_alerts: [], deep_value_watch: [], excluded_balance_sheet: 0 });
    render(<AlertsPage />);
    await screen.findByText(/No turnaround matches/);
    expect(api.scan).toHaveBeenCalledWith({ near_low_pct: 15, min_valuation_score: 12 });
  });
});


describe('Deep Value is internal research (owner only)', () => {
  it('shows a not-available page to any other role and never calls the API', async () => {
    for (const role of ['collaborator', null]) {
      auth.role = role;
      api.scan.mockClear();
      const { unmount } = render(<AlertsPage />);
      expect(screen.getByTestId('deep-value-unavailable').textContent).toBe('This page is not part of First Light.');
      expect(api.scan).not.toHaveBeenCalled();
      unmount();
    }
    auth.role = 'owner';
  });
});
