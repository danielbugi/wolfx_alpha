// Chart indicators: the saved choice (tolerant parsing, defaults, shared by every chart, nothing written on read), the
// indicator window (tick / untick, reset, Escape, stock-only layers never saved), and the chart's Indicators button.
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, renderHook, screen, within } from '@testing-library/react';

// lightweight-charts needs a real canvas; a chainable stub keeps the chart component renderable in jsdom.
vi.mock('lightweight-charts', () => {
  const stub = (): unknown =>
    new Proxy(function () {}, {
      get: (_t, p) => (p === 'then' ? undefined : p === Symbol.toPrimitive ? () => 0 : stub()),
      apply: () => stub(),
    });
  return { createChart: () => stub(), LineStyle: { Dashed: 2, Dotted: 1, Solid: 0 }, CrosshairMode: { Normal: 0 } };
});

import { DEFAULT_INDICATORS, parseIndicatorPrefs, useIndicatorPrefs } from '@/lib/chartIndicators';
import IndicatorsDialog from '@/components/charts/IndicatorsDialog';
import StockChart from '@/components/charts/StockChart';

const KEY = 'chart_indicators_v1';
const bars = Array.from({ length: 60 }, (_, i) => ({
  date: new Date(Date.UTC(2026, 6, 1 + i)).toISOString().slice(0, 10), open: 10 + i, high: 11 + i, low: 9 + i, close: 10 + i, volume: 1000, donchian_high_20: null, donchian_low_20: null,
}));

// jsdom has no ResizeObserver (the chart resizes with its container)
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

beforeEach(() => window.localStorage.clear());

describe('saved indicator choice', () => {
  it('falls back to the defaults and drops unknown keys and duplicates', () => {
    expect(parseIndicatorPrefs(null)).toEqual(DEFAULT_INDICATORS);
    for (const raw of ['{', '[]', '{"overlays":"x","panels":[]}']) expect(parseIndicatorPrefs(raw)).toEqual(DEFAULT_INDICATORS);
    expect(parseIndicatorPrefs(JSON.stringify({ overlays: ['sma200', 'sma200', 'plan', 'breakouts', 'nope'], panels: ['macd', 'x'] }))).toEqual({ overlays: ['sma200'], panels: ['macd'] });
    expect(parseIndicatorPrefs(JSON.stringify({ overlays: [], panels: [] }))).toEqual({ overlays: [], panels: [] });
  });

  it('saves, never writes on read, and keeps every chart in sync', () => {
    window.localStorage.setItem(KEY, '{bad');
    const a = renderHook(() => useIndicatorPrefs());
    const b = renderHook(() => useIndicatorPrefs());
    expect(window.localStorage.getItem(KEY)).toBe('{bad');
    expect(a.result.current.prefs).toEqual(DEFAULT_INDICATORS);
    act(() => void a.result.current.save({ overlays: ['sma200', 'bollinger'], panels: ['macd'] }));
    expect(JSON.parse(window.localStorage.getItem(KEY)!)).toEqual({ overlays: ['sma200', 'bollinger'], panels: ['macd'] });
    expect(b.result.current.prefs).toEqual({ overlays: ['sma200', 'bollinger'], panels: ['macd'] });
  });

  it('applies the choice even when the browser refuses to save it', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    const { result } = renderHook(() => useIndicatorPrefs());
    let ok = true;
    act(() => void (ok = result.current.save({ overlays: ['sma20'], panels: [] })));
    expect(ok).toBe(false);
    expect(result.current.prefs).toEqual({ overlays: ['sma20'], panels: [] });
    spy.mockRestore();
  });
});

describe('IndicatorsDialog', () => {
  it('ticks and unticks overlays and panels, resets to the defaults, and closes on Escape', () => {
    const onChange = vi.fn();
    const onClose = vi.fn();
    render(<IndicatorsDialog prefs={DEFAULT_INDICATORS} onChange={onChange} onClose={onClose} />);
    const dialog = screen.getByRole('dialog', { name: 'Indicators' });
    expect((dialog.querySelector('#ind-sma20') as HTMLInputElement).checked).toBe(true);
    expect((dialog.querySelector('#ind-sma200') as HTMLInputElement).checked).toBe(false);
    fireEvent.click(dialog.querySelector('#ind-sma200')!);
    expect(onChange).toHaveBeenLastCalledWith({ overlays: [...DEFAULT_INDICATORS.overlays, 'sma200'], panels: ['rsi'] });
    fireEvent.click(dialog.querySelector('#ind-rsi')!);
    expect(onChange).toHaveBeenLastCalledWith({ overlays: DEFAULT_INDICATORS.overlays, panels: [] });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reset to defaults' }));
    expect(onChange).toHaveBeenLastCalledWith(DEFAULT_INDICATORS);
    expect(within(dialog).queryByText('This stock only (not saved)')).toBeNull();
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('lists stock-only layers separately, as not saved', () => {
    const onToggle = vi.fn();
    render(<IndicatorsDialog prefs={DEFAULT_INDICATORS} onChange={vi.fn()} onClose={vi.fn()} extras={[{ key: 'breakouts', label: 'Past breakout markers', description: 'x', on: false, onToggle }]} />);
    expect(screen.getByText('This stock only (not saved)')).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/Past breakout markers/));
    expect(onToggle).toHaveBeenCalled();
  });
});

describe('StockChart Indicators button', () => {
  it('shows the saved choice, opens the window, and a change is saved for every chart', () => {
    window.localStorage.setItem(KEY, JSON.stringify({ overlays: ['sma50'], panels: [] }));
    render(<StockChart bars={bars} plan={null} />);
    expect(screen.getByTestId('active-indicators').textContent).toBe('SMA 50');
    fireEvent.click(screen.getByRole('button', { name: /Indicators/ }));
    fireEvent.click(within(screen.getByRole('dialog')).getByLabelText(/MACD/));
    expect(JSON.parse(window.localStorage.getItem(KEY)!)).toEqual({ overlays: ['sma50'], panels: ['macd'] });
    expect(screen.getByTestId('active-indicators').textContent).toBe('SMA 50MACD');
    expect(screen.getByRole('button', { name: /Indicators/ }).textContent).toContain('2');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('never saves a stock-only layer such as past breakout markers', () => {
    render(<StockChart bars={bars} breakouts={[{ date: bars[30].date } as never]} plan={null} />);
    fireEvent.click(screen.getByRole('button', { name: /Indicators/ }));
    fireEvent.click(screen.getByLabelText(/Past breakout markers/));
    expect(screen.getByTestId('active-indicators').textContent).toContain('Breakouts');
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });
});
