// The internal /momentum page keeps the Momentum Board and Momentum Leaders that left the product Overview:
// same components, same APIs, same default category, and the Overview's sector selection (?sector=) still filters
// the board through the board API's own `sector` parameter.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  getBoard: vi.fn(async () => ({ results: [], counts: { breakout: 0 }, starred_total: 0, universe_n: 2900, session_date: '2026-10-16' })),
  getLeaders: vi.fn(async () => ({ board: null, session_date: '2026-10-16' })),
}));
const nav = vi.hoisted(() => ({ search: '', replace: vi.fn() }));

vi.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ replace: nav.replace, push: vi.fn() }),
  usePathname: () => '/momentum',
}));
vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return { ...actual, momentumBoardApi: { getBoard: api.getBoard }, momentumLeadersApi: { getLeaders: api.getLeaders } };
});
vi.mock('@/components/dashboard/MomentumBoard', () => ({ default: (p: { category: string }) => <div>board:{p.category}</div> }));
vi.mock('@/components/dashboard/MomentumLeaders', () => ({ default: () => <div>leaders</div> }));

import MomentumPage from '@/app/momentum/page';

beforeEach(() => {
  api.getBoard.mockClear();
  api.getLeaders.mockClear();
  nav.replace.mockClear();
  nav.search = '';
});

describe('internal Momentum page', () => {
  it('loads the board (breakout by default, no sector) and the leaders from their existing APIs', async () => {
    render(<MomentumPage />);
    expect(await screen.findByText('board:breakout')).toBeTruthy();
    expect(screen.getByText('leaders')).toBeTruthy();
    await waitFor(() => expect(api.getBoard).toHaveBeenCalledWith({ category: 'breakout', min_quality_grade: undefined, sector: undefined, limit: 50 }));
    expect(api.getLeaders).toHaveBeenCalled();
    expect(screen.queryByText('Sector filter')).toBeNull();
  });

  it('filters the board by the sector picked on the Overview and can clear it', async () => {
    nav.search = 'sector=Health%20Care';
    render(<MomentumPage />);
    await waitFor(() => expect(api.getBoard).toHaveBeenCalledWith({ category: 'breakout', min_quality_grade: undefined, sector: 'Health Care', limit: 50 }));
    expect(screen.getByText('Health Care')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(nav.replace).toHaveBeenCalledWith('/momentum');
  });
});
