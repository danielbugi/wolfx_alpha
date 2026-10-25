// The Earnings page's "Data as of" line: the session the board (and so its stored earnings dates) describes, as the
// API delivers it; null (shown as "unavailable") when the board does not say or cannot be loaded.
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({ get: vi.fn(), getBoard: vi.fn() }));
vi.mock('@/services/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/api')>();
  return { ...actual, apiClient: { ...actual.apiClient, get: api.get }, momentumBoardApi: { ...actual.momentumBoardApi, getBoard: api.getBoard } };
});

import { useEarningsCalendar } from '@/hooks/useEarningsCalendar';

beforeEach(() => {
  api.get.mockReset().mockResolvedValue({ data: { holidays_known: false, sessions: [] } });
  api.getBoard.mockReset();
});

describe('useEarningsCalendar boardSession', () => {
  it('passes the board session through', async () => {
    api.getBoard.mockResolvedValue({ session_date: '2026-10-23', results: [] });
    const { result } = renderHook(() => useEarningsCalendar());
    await waitFor(() => expect(result.current.boardState).toBe('ok'));
    expect(result.current.boardSession).toBe('2026-10-23');
  });

  it('is null when the board fails, never a date from the clock', async () => {
    api.getBoard.mockRejectedValue(new Error('down'));
    const { result } = renderHook(() => useEarningsCalendar());
    await waitFor(() => expect(result.current.boardState).toBe('error'));
    expect(result.current.boardSession).toBeNull();
  });
});
