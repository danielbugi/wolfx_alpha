// The performance loader: one request per symbol (shared while in flight, cached afterwards), long enough for 1Y/YTD, the
// S&P 500 fetched once, failures not cached, and an older server's missing discontinuity list reported as "not checked".
import { beforeEach, describe, expect, it, vi } from 'vitest';

const get = vi.hoisted(() => vi.fn());
vi.mock('@/services/api', () => ({ apiClient: { get } }));

import { BENCH_DAYS, STOCK_BARS, performanceApi } from '@/services/performanceApi';

beforeEach(() => {
  get.mockReset();
  performanceApi._clear();
});

describe('performanceApi', () => {
  it('fetches each symbol once (concurrent and repeat calls share it) with enough bars for 1Y and YTD', async () => {
    get.mockResolvedValue({ data: { bars: [{ date: '2026-10-16', close: 10 }], discontinuities: [{ date: '2026-06-01', kind: 'split' }] } });
    const [a, b] = await Promise.all([performanceApi.stock('NVDA'), performanceApi.stock('NVDA')]);
    await performanceApi.stock('NVDA');
    expect(get).toHaveBeenCalledTimes(1);
    expect(get.mock.calls[0]).toEqual(['/api/stock/NVDA/price-history', { params: { days: STOCK_BARS } }]);
    expect(STOCK_BARS).toBeGreaterThanOrEqual(260);
    expect(a).toBe(b);
    expect(a.discontinuitiesChecked).toBe(true);
  });

  it('fetches the S&P 500 once over enough calendar days', async () => {
    get.mockResolvedValue({ data: { bars: [] } });
    await performanceApi.benchmark();
    await performanceApi.benchmark();
    expect(get).toHaveBeenCalledTimes(1);
    expect(get.mock.calls[0]).toEqual(['/api/market/instruments/%5EGSPC/history', { params: { days: BENCH_DAYS } }]);
    expect(BENCH_DAYS).toBeGreaterThanOrEqual(400);
  });

  it('does not cache a failure, and reports a missing discontinuity list as not checked', async () => {
    get.mockRejectedValueOnce(new Error('down')).mockResolvedValueOnce({ data: { bars: [] } });
    await expect(performanceApi.stock('AAA')).rejects.toThrow('down');
    const h = await performanceApi.stock('AAA');
    expect(get).toHaveBeenCalledTimes(2);
    expect(h.discontinuitiesChecked).toBe(false);
  });
});
