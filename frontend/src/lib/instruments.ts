// Markets pages: the instrument types served by /api/market/instruments, and how each kind of price is written.
import type { PriceBar } from '@/services/api';

export type Window = '1d' | '1w' | '1m' | '3m' | 'ytd' | '1y';

export interface Instrument {
  symbol: string;
  name: string;
  group: string;
  /** shown after the price: "/oz", "¢/bu", "%" … */
  unit: string;
  /** false until the pipeline has fetched its history (nothing is shown for it but its name) */
  has_data: boolean;
  latest_date: string | null;
  close: number | null;
  /** % change over calendar windows; null when the history doesn't reach that far back */
  change_pct: Record<Window, number | null>;
  sparkline: number[];
}

export interface InstrumentsResponse {
  groups: { key: string; label: string }[];
  instruments: Instrument[];
}

export interface InstrumentHistory {
  symbol: string;
  name: string;
  group: string;
  unit: string;
  bars: PriceBar[];
}

type Kind = Pick<Instrument, 'symbol' | 'group' | 'unit'>;

/** Decimals that make a move readable: 4 for EUR/USD, 3 for yields and USD/JPY-type quotes, 2 otherwise. */
export function pricePrecision(i: Kind, close: number | null): number {
  if (i.group === 'currencies' && i.symbol !== 'DX-Y.NYB') return close != null && close >= 20 ? 3 : 4;
  if (i.unit === '%') return 3;
  if (close != null && close < 10) return 3;
  return 2;
}

/** "$4,380.80", "1.0843", "4.512%", "452.25¢" — a dollar sign only where the quote is in dollars. */
export function formatInstrumentPrice(i: Kind, close: number | null): string {
  if (close == null) return '—';
  const d = pricePrecision(i, close);
  const n = close.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  if (i.unit === '%') return `${n}%`;
  if (i.unit.startsWith('¢')) return `${n}¢`;
  if (i.group === 'currencies' || i.group === 'indices' || i.group === 'index_futures' || i.symbol === '^VIX') return n;
  return `$${n}`;
}

/** The unit suffix shown beside the price ("/oz", "/bu"); the %/¢ ones are already part of the price. */
export function unitSuffix(i: Kind): string {
  if (i.unit === '%') return '';
  return i.unit.startsWith('¢') ? i.unit.slice(1) : i.unit;
}

export function fmtChange(v: number | null | undefined): string {
  return v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;
}

export const WINDOWS: { key: Window; label: string }[] = [
  { key: '1d', label: '1D' },
  { key: '1w', label: '1W' },
  { key: '1m', label: '1M' },
  { key: '3m', label: '3M' },
  { key: 'ytd', label: 'YTD' },
  { key: '1y', label: '1Y' },
];
