import { afterEach, describe, expect, it, vi } from 'vitest';
import { fmtDay, fmtUtc, isOpenPath, parseSignalParams, signalHref, UPDATE_TYPES } from '@/lib/signals';
import { detailPath, feedPath, PublicApiError, publicSignalsApi } from '@/services/publicSignalsApi';

describe('open paths', () => {
  it('covers /signals and its pages only', () => {
    expect(isOpenPath('/signals')).toBe(true);
    expect(isOpenPath('/signals/POWI/2026-09-30/margin-expansion')).toBe(true);
    expect(isOpenPath('/signalsx')).toBe(false);
    expect(isOpenPath('/inflection')).toBe(false);
    expect(isOpenPath('/')).toBe(false);
    expect(isOpenPath(null)).toBe(false);
  });
});

describe('natural-key URLs', () => {
  it('builds and parses the permanent address', () => {
    expect(signalHref('POWI/2026-09-30/margin-expansion')).toBe('/signals/POWI/2026-09-30/margin-expansion');
    expect(signalHref('BRK.B/2026-09-30/fcf-turnaround')).toBe('/signals/BRK.B/2026-09-30/fcf-turnaround');
    expect(parseSignalParams('POWI', '2026-09-30', 'margin-expansion')).toEqual({ symbol: 'POWI', quarter: '2026-09-30', slug: 'margin-expansion' });
    expect(parseSignalParams('BRK.B', '2026-09-30', 'fcf-turnaround')?.symbol).toBe('BRK.B');
  });

  it.each([
    ['powi', '2026-09-30', 'margin-expansion'],
    ['POWI', '2026-9-30', 'margin-expansion'],
    ['POWI', '2026-02-30', 'margin-expansion'],
    ['POWI', '2026-09-30', 'limited-repricing'],
    ['POWI', '2026-09-30', '%3Cscript%3E'],
    ['A B', '2026-09-30', 'margin-expansion'],
    ['', '', ''],
  ])('rejects %s/%s/%s', (s, q, t) => {
    expect(parseSignalParams(s, q, t)).toBeNull();
  });

  it('has exactly the five public types', () => {
    expect(UPDATE_TYPES.map((t) => t.slug)).toEqual(['revenue-acceleration', 'margin-expansion', 'operating-leverage', 'net-income-turnaround', 'fcf-turnaround']);
  });
});

describe('dates', () => {
  it('formats calendar dates and UTC times without the viewer time zone', () => {
    expect(fmtDay('2026-11-05')).toBe('Nov 5, 2026');
    expect(fmtUtc('2026-11-04T23:03:00Z')).toBe('Nov 4, 2026, 23:03 UTC');
  });
});

describe('public API client', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('builds feed and detail paths with encoded parameters', () => {
    expect(feedPath()).toBe('/api/public/signals');
    expect(feedPath({ limit: 20, cursor: 'abc', types: ['margin-expansion', 'fcf-turnaround'], sector: 'Real Estate' }))
      .toBe('/api/public/signals?limit=20&cursor=abc&type=margin-expansion&type=fcf-turnaround&sector=Real+Estate');
    expect(detailPath('BRK.B', '2026-09-30', 'fcf-turnaround')).toBe('/api/public/signals/BRK.B/2026-09-30/fcf-turnaround');
  });

  it('sends no credentials and no token, and reports the status on failure', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 429, statusText: 'Too Many Requests' });
    vi.stubGlobal('fetch', fetchMock);
    await expect(publicSignalsApi.feed()).rejects.toMatchObject({ status: 429 });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api.test.invalid/api/public/signals');
    expect(init.credentials).toBe('omit');
    expect(Object.keys(init.headers)).toEqual(['Accept']);
    fetchMock.mockRejectedValueOnce(new TypeError('offline'));
    await expect(publicSignalsApi.detail('POWI', '2026-09-30', 'margin-expansion')).rejects.toBeInstanceOf(PublicApiError);
  });
});
