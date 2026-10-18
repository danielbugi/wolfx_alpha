// The internal /momentum page keeps the Momentum Board and Momentum Leaders that left the product Overview:
// same components, same APIs, same default category.
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  getBoard: vi.fn(async () => ({ results: [], counts: { breakout: 0 }, starred_total: 0, universe_n: 2900, session_date: '2026-10-16' })),
  getLeaders: vi.fn(async () => ({ board: null, session_date: '2026-10-16' })),
}));

vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return { ...actual, momentumBoardApi: { getBoard: api.getBoard }, momentumLeadersApi: { getLeaders: api.getLeaders } };
});
vi.mock('@/components/dashboard/MomentumBoard', () => ({ default: (p: { category: string }) => <div>board:{p.category}</div> }));
vi.mock('@/components/dashboard/MomentumLeaders', () => ({ default: () => <div>leaders</div> }));

import MomentumPage from '@/app/momentum/page';

describe('internal Momentum page', () => {
  it('loads the board (breakout by default) and the leaders from their existing APIs', async () => {
    render(<MomentumPage />);
    expect(screen.getByText('Momentum Board')).toBeTruthy();
    expect(screen.getByText('board:breakout')).toBeTruthy();
    expect(screen.getByText('leaders')).toBeTruthy();
    await waitFor(() => expect(api.getBoard).toHaveBeenCalledWith({ category: 'breakout', min_quality_grade: undefined, limit: 50 }));
    expect(api.getLeaders).toHaveBeenCalled();
  });
});
