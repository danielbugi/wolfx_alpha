// Earnings histories for the Earnings page / Overview card: ONE batch request for many symbols (the backend answers
// it with one query), cached; a symbol the server does not answer for is an error, never an empty "no earnings".
import { describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({ getEarningsBatch: vi.fn(), getEarnings: vi.fn() }));
vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return { ...actual, stockApi: { ...actual.stockApi, ...api } };
});

import { requestHistories, useEarningsHistories } from '@/hooks/useEarningsCalendar';

describe('earnings histories', () => {
  it('fetches many symbols in one batch request, never one request per symbol, and caches them', async () => {
    api.getEarningsBatch.mockResolvedValue({
      source: 'earnings_calendar', rule: 'current_row_v1', today: '2026-10-25',
      symbols: {
        AAPL: { symbol: 'AAPL', earnings_dates: [{ date: '2026-11-02', eps_estimate: 1.98, eps_actual: null, surprise_pct: null, status: 'upcoming', confidence: 'confirmed' }] },
        META: { symbol: 'META', earnings_dates: [] },
      },
    });
    const { result } = renderHook(() => useEarningsHistories());
    act(() => requestHistories(['AAPL', 'META', 'GONE']));
    await waitFor(() => expect(result.current.GONE).toBe('error'));
    expect(api.getEarningsBatch).toHaveBeenCalledTimes(1);
    expect(api.getEarningsBatch.mock.calls[0][0]).toEqual(['AAPL', 'META', 'GONE']);
    expect(api.getEarnings).not.toHaveBeenCalled();
    expect((result.current.AAPL as { date: string }[])[0].date).toBe('2026-11-02');
    expect(result.current.META).toEqual([]);                        // answered with no rows: an explicit empty history
    act(() => requestHistories(['AAPL', 'META']));
    expect(api.getEarningsBatch).toHaveBeenCalledTimes(1);           // cached
  });
});
