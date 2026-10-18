// SYNTHETIC market-intelligence payload for tests (shape of mechanism/market_intelligence/payload.py, mi_payload_v1).
// Values are invented; only the structure follows the real API.
import type { MarketSnapshotAvailable, RegimeComponent } from '@/services/marketIntelligenceApi';

const comp = (key: string, label: string, weight: number, value: number | null, present = true, missing_reason: string | null = null): RegimeComponent => ({
  key, label, weight, present, value, score: present ? 0 : null, missing_reason, raw: {},
});

const rec = (numerator: number, denominator: number, universe: number) => ({
  numerator, denominator, universe, missing: universe - denominator, pct: +((100 * numerator) / denominator).toFixed(4), state: 'ok' as const,
});

const hz = (ret: number, vs_spx: number, vs_univ: number, n: number) => ({ n_valid: n, n_excluded: 0, ret, vs_spx, vs_univ });

export function snapshot(overrides: Partial<MarketSnapshotAvailable> = {}): MarketSnapshotAvailable {
  return {
    schema: 'mi_payload_v1',
    available: true,
    session_date: '2026-03-02',
    provenance: 'observed',
    observed: true,
    provenance_note: null,
    regime: {
      model_version: 'risk_regime_v1',
      state: 'NEUTRAL',
      available: true,
      score: -0.12,
      strength: 0.12,
      strength_label: 'mixed',
      agreement: 0.5,
      present_weight: 0.9,
      min_present_weight: 0.7,
      thresholds: { risk_on: 0.3, risk_off: -0.3 },
      components: [
        comp('c1_index_trend', 'S&P 500 trend structure', 0.25, 2),
        comp('c2_index_momentum', 'S&P 500 20-session change', 0.1, 1.25),
        comp('c3_breadth_sma50', 'Stocks above 50-day average', 0.2, 41.5),
        comp('c4_breadth_sma200', 'Stocks above 200-day average', 0.1, 55.2),
        comp('c5_net_highs_lows', 'Net 52-week highs minus lows', 0.1, null, false, 'stocks with a usable 52-week range 900 < 1000'),
        comp('c6_volatility', 'VIX level', 0.15, 18.4),
        comp('c7_risk_appetite', 'Russell 2000 vs S&P 500, 20 sessions', 0.1, -2.1),
      ],
      reasons: ['Stocks above 50-day average: 42%'],
      is_heuristic: true,
      disclaimer: 'V1 heuristic: fixed weights and thresholds that were declared, not fitted to outcomes. Descriptive context only.',
    },
    returns: {
      spx: { '5': 0.5, '20': 1.25, '60': 3.0 },
      universe_median: { '5': { ret: -0.2, n_valid: 2000 }, '20': { ret: -1.0, n_valid: 1990 }, '60': { ret: null, n_valid: 0 } },
      unit: 'percent',
      universe_definition: 'equal-weight median of all valid stock returns; S&P 500 is cap-weighted',
    },
    sectors: [
      { sector: 'Gamma', n_members: 40, rank_20: 2, horizons: { '5': hz(1, 0.5, 1.2, 40), '20': hz(2, 0.75, 3, 40), '60': hz(4, 1, 2, 39) } },
      { sector: 'Alpha', n_members: 120, rank_20: 1, horizons: { '5': hz(2, 1.5, 2.2, 120), '20': hz(4, 2.75, 5, 120), '60': hz(6, 3, 4, 118) } },
      { sector: 'Delta', n_members: 4, rank_20: null, horizons: { '5': hz(0, 0, 0, 4), '20': { n_valid: 4, n_excluded: 0, ret: null, vs_spx: null, vs_univ: null }, '60': hz(0, 0, 0, 4) } },
      { sector: 'Beta', n_members: 80, rank_20: 3, horizons: { '5': hz(-1, -1.5, -0.8, 80), '20': hz(-3, -4.25, -2, 80), '60': hz(-2, -5, -1, 80) } },
    ],
    sector_map: { source: 'daily_fundamentals.sector', asof_rule: 'pit_evidenced', pit_safe: true, n_symbols: 2100, n_classified: 2050 },
    coverage: {
      n_universe: 2100,
      n_classified: 2050,
      n_unclassified: 50,
      classified_share: 0.976,
      n_sectors: 4,
      n_sectors_ranked: 3,
      min_universe: 1000,
      min_sector_members: 5,
      n_excluded_discontinuity: { '5': 0, '20': 1, '60': 3 },
      sector_map: { source: 'daily_fundamentals.sector', pit_safe: true, provenance: 'observed', caveat: null },
      measurements: {
        breadth: {
          market: {
            above_sma50: rec(830, 2000, 2100),
            above_sma200: rec(1050, 1900, 2100),
            positive_ret_20: { numerator: 400, denominator: 900, universe: 2100, missing: 1200, pct: null, state: 'insufficient_eligible' },
            new_high_52w: rec(60, 1850, 2100),
            new_low_52w: rec(45, 1850, 2100),
            advancing_1d: rec(1200, 2100, 2100),
            declining_1d: rec(850, 2100, 2100),
          },
        },
        sectors: [
          { sector: 'Alpha', strength: { rank_20: 1, n_ranked: 3, strength_state: 'leading' }, breadth: { above_sma50: rec(70, 118, 120) } },
          { sector: 'Gamma', strength: { rank_20: 2, n_ranked: 3, strength_state: 'in_line' } },
          { sector: 'Beta', strength: { rank_20: 3, n_ranked: 3, strength_state: 'lagging' }, breadth: { above_sma50: rec(20, 80, 80) } },
          { sector: 'Delta', strength: { rank_20: null, n_ranked: 3, strength_state: 'not_available' } },
        ],
      },
    },
    meta: { captured_at: '2026-03-03 06:45:12.5+02', source: 'mi_runner_v2', feature_set_version: 'mi_v2' },
    ...overrides,
  };
}
