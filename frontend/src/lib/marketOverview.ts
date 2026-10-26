// File: frontend/src/lib/marketOverview.ts
// The Overview's market-activity payload (/api/screener/market-overview), normalised ONCE where it is fetched so no
// render path dereferences a missing number. The backend sends null for a figure it cannot compute for the session (e.g.
// RSI / volume ratio while the nightly pipeline has loaded a new session's prices but not yet its indicators); an older
// or partial payload can omit fields entirely. Either way the value becomes null here and the page shows "—" -- never a
// default, never a crash. Sectors without a numeric change or member count are dropped (they cannot be drawn honestly).
import type { MarketOverview } from '@/services/api';

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' ? (v as Record<string, unknown>) : {});

export function normalizeMarketOverview(raw: unknown): MarketOverview | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const b = obj(r.market_breadth);
  const i = obj(r.market_indicators);
  const c = r.coverage && typeof r.coverage === 'object' ? obj(r.coverage) : null;
  const p = r.population && typeof r.population === 'object' ? obj(r.population) : null;
  const sectors = Array.isArray(r.sector_performance) ? r.sector_performance : [];
  return {
    market_breadth: {
      total_stocks: num(b.total_stocks),
      gainers: num(b.gainers),
      losers: num(b.losers),
      unchanged: num(b.unchanged),
      advance_decline_ratio: num(b.advance_decline_ratio),
      unusual_volume_count: num(b.unusual_volume_count),
      overbought_count: num(b.overbought_count),
      oversold_count: num(b.oversold_count),
    },
    market_indicators: {
      avg_volume_ratio: num(i.avg_volume_ratio),
      avg_rsi: num(i.avg_rsi),
      total_market_cap: num(i.total_market_cap),
    },
    sector_performance: sectors
      .map(obj)
      .filter((s) => typeof s.sector === 'string' && num(s.avg_performance) != null && num(s.stock_count) != null)
      .map((s) => ({
        sector: s.sector as string,
        stock_count: num(s.stock_count) as number,
        avg_performance: num(s.avg_performance) as number,
        up_count: num(s.up_count) ?? undefined,
      })),
    session: typeof r.session === 'string' ? r.session : null,
    population:
      p && num(p.min_close) != null
        ? { min_close: num(p.min_close) as number, liquidity_floor: num(p.liquidity_floor), universe: num(p.universe) ?? 0, description: String(p.description ?? '') }
        : null,
    coverage: c
      ? {
          change_known: num(c.change_known) ?? 0,
          volume_ratio_known: num(c.volume_ratio_known) ?? 0,
          rsi_known: num(c.rsi_known) ?? 0,
          market_cap_known: num(c.market_cap_known) ?? 0,
        }
      : null,
  };
}

/** A count for display: the number, or "—" when it is not known. */
export const showCount = (v: number | null | undefined): string => (v == null ? '—' : String(v));
