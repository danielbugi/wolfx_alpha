// Watchlist storage: compatible with the saved format, tolerant of malformed data without destroying valid entries,
// no duplicates, a strict 30-symbol limit that never evicts, failure to save reported, and hook instances in sync.
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WATCHLIST_MAX, addTo, normalizeSymbol, parseStored, useWatchlist, watchlistMessage } from '@/lib/watchlist';

const KEY = 'watchlist_symbols';
const stored = () => JSON.parse(window.localStorage.getItem(KEY) ?? 'null');
const thirty = Array.from({ length: WATCHLIST_MAX }, (_, i) => `S${i}`);

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe('parsing the saved list', () => {
  it('reads the existing format unchanged', () => {
    expect(parseStored(JSON.stringify(['NVDA', 'AAPL', 'BRK.B']))).toEqual(['NVDA', 'AAPL', 'BRK.B']);
  });

  it('normalises case and whitespace, drops duplicates and invalid entries, keeps the valid ones in order', () => {
    expect(parseStored(JSON.stringify([' nvda', 'NVDA', 42, null, 'A;B', '', 'msft', { s: 'X' }, 'TOO-LONG-SYMBOL', 'Msft']))).toEqual(['NVDA', 'MSFT']);
  });

  it('yields an empty list for unreadable or non-list data', () => {
    for (const raw of [null, '', '{', '"NVDA"', '{"a":1}', '42']) expect(parseStored(raw)).toEqual([]);
  });

  it('validates symbols', () => {
    expect(normalizeSymbol(' brk.b ')).toBe('BRK.B');
    expect(normalizeSymbol('BF-B')).toBe('BF-B');
    for (const bad of ['', '.A', 'A B', 'A/B', 'ABCDEFGHIJK', 7, null]) expect(normalizeSymbol(bad)).toBeNull();
  });
});

describe('the add rule', () => {
  it('adds newest first and refuses duplicates', () => {
    expect(addTo(['AAPL'], 'nvda')).toEqual({ next: ['NVDA', 'AAPL'], result: 'added' });
    expect(addTo(['AAPL'], ' aapl ')).toEqual({ next: ['AAPL'], result: 'already' });
  });

  it('refuses a 31st symbol without evicting anything', () => {
    const { next, result } = addTo(thirty, 'NEW');
    expect(result).toBe('full');
    expect(next).toBe(thirty);
    expect(watchlistMessage('full', 'new')).toBe('Your watchlist is full (30 stocks). Remove one to add NEW.');
  });

  it('refuses invalid symbols', () => {
    expect(addTo([], 'A;B').result).toBe('invalid');
  });
});

describe('useWatchlist', () => {
  it('adds and removes, persisting in the existing format', () => {
    const { result } = renderHook(() => useWatchlist());
    act(() => void result.current.add('nvda'));
    act(() => void result.current.add('AAPL'));
    expect(stored()).toEqual(['AAPL', 'NVDA']);
    expect(result.current.symbols).toEqual(['AAPL', 'NVDA']);
    act(() => result.current.remove('NVDA'));
    expect(stored()).toEqual(['AAPL']);
    expect(result.current.has('aapl')).toBe(true);
  });

  it('toggle adds then removes, and a duplicate add changes nothing', () => {
    const { result } = renderHook(() => useWatchlist());
    act(() => void result.current.toggle('NVDA'));
    let r: string = '';
    act(() => void (r = result.current.add('nvda')));
    expect(r).toBe('already');
    expect(stored()).toEqual(['NVDA']);
    act(() => void result.current.toggle('NVDA'));
    expect(stored()).toEqual([]);
  });

  it('at 30 symbols refuses the next add with a message and leaves the list untouched', () => {
    window.localStorage.setItem(KEY, JSON.stringify(thirty));
    const { result } = renderHook(() => useWatchlist());
    expect(result.current.full).toBe(true);
    let r: string = '';
    act(() => void (r = result.current.toggle('NEW')));
    expect(r).toBe('full');
    expect(stored()).toEqual(thirty);
    expect(result.current.notice).toContain('Your watchlist is full (30 stocks)');
    act(() => result.current.remove('S0'));
    expect(result.current.notice).toBeNull();
    act(() => void result.current.add('NEW'));
    expect(stored()[0]).toBe('NEW');
    expect(stored()).toHaveLength(30);
  });

  it('reading malformed storage never rewrites it; the first change keeps every valid saved symbol', () => {
    const raw = JSON.stringify(['nvda', 'NVDA', 5, 'bad symbol', 'AAPL']);
    window.localStorage.setItem(KEY, raw);
    const { result } = renderHook(() => useWatchlist());
    expect(result.current.symbols).toEqual(['NVDA', 'AAPL']);
    expect(window.localStorage.getItem(KEY)).toBe(raw);
    act(() => void result.current.add('MSFT'));
    expect(stored()).toEqual(['MSFT', 'NVDA', 'AAPL']);
  });

  it('reports a save the browser refuses, and does not pretend it was added', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceeded');
    });
    const { result } = renderHook(() => useWatchlist());
    let r: string = '';
    act(() => void (r = result.current.add('NVDA')));
    expect(r).toBe('unsaved');
    expect(result.current.symbols).toEqual([]);
    expect(result.current.notice).toContain('not allowing the watchlist to be saved');
  });

  it('keeps every instance in this tab in sync (e.g. Screener star and stock page star) and follows other tabs', () => {
    const a = renderHook(() => useWatchlist());
    const b = renderHook(() => useWatchlist());
    act(() => void a.result.current.add('NVDA'));
    expect(b.result.current.symbols).toEqual(['NVDA']);
    act(() => b.result.current.remove('NVDA'));
    expect(a.result.current.symbols).toEqual([]);
    act(() => {
      window.localStorage.setItem(KEY, JSON.stringify(['TSLA']));
      window.dispatchEvent(new StorageEvent('storage', { key: KEY }));
    });
    expect(a.result.current.symbols).toEqual(['TSLA']);
    expect(b.result.current.symbols).toEqual(['TSLA']);
  });
});
