// File: frontend/src/services/api.ts
/**
 * API Service Layer - Centralized API communication with FastAPI backend
 */

import axios, { AxiosResponse } from 'axios';

// API Configuration
// The localhost default exists for `next dev` only. In a production build it would
// silently point every visitor's browser at *their own* machine, so there the URL
// must be configured (next.config.ts also fails the build when it is missing).
// The dev default is the IPv4 literal, not `localhost`: on Windows `localhost`
// resolves to ::1 first, uvicorn only listens on IPv4, and every new connection
// then stalls ~200ms on the failed IPv6 attempt before falling back (measured:
// 216ms connect vs. 0.2ms via 127.0.0.1, against a 1.7ms server response).
function resolveApiBaseUrl(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (configured) return configured;
  if (process.env.NODE_ENV === 'development') return 'http://127.0.0.1:8000';
  throw new Error('NEXT_PUBLIC_API_BASE_URL must be set outside of `next dev`.');
}

export const API_BASE_URL = resolveApiBaseUrl();

// Create axios instance with default configuration
export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000, // 30 second timeout
  headers: {
    'Content-Type': 'application/json',
  },
});

// A second, interceptor-free client used only for the token refresh call itself — routing it through
// `apiClient` would re-enter the 401 handler below on a failed refresh.
const rawClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

// --- Auth token plumbing --------------------------------------------------
// The access token lives in memory only (set by AuthContext after login/refresh, never persisted — a
// reload always goes through a fresh silent refresh). The refresh token is handed in by AuthContext (it
// owns the localStorage read/write); kept here too so a 401 can trigger a silent refresh-and-retry without
// AuthContext needing to wrap every single API call in this file.
let accessToken: string | null = null;
let refreshToken: string | null = null;
let onSessionExpired: (() => void) | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

/** For call sites that can't go through `apiClient` (e.g. DevQAPanel's raw `fetch()` + AbortController timeouts). */
export function getAccessToken(): string | null {
  return accessToken;
}

export function setRefreshToken(token: string | null): void {
  refreshToken = token;
}

/** Registered by AuthProvider: called when a session cannot be recovered (no/expired refresh token). */
export function setOnSessionExpired(handler: (() => void) | null): void {
  onSessionExpired = handler;
}

/** Exchanges the current refresh token for a new access token. Returns null (never throws) on failure. */
export async function refreshAccessToken(): Promise<string | null> {
  if (!refreshToken) return null;
  try {
    const response = await rawClient.post<{ access_token: string }>('/api/auth/refresh', { refresh_token: refreshToken });
    accessToken = response.data.access_token;
    return accessToken;
  } catch {
    return null;
  }
}

// Request interceptor: attaches the bearer token (when present) + debug logging
apiClient.interceptors.request.use(
  (config) => {
    if (accessToken) {
      config.headers = config.headers ?? {};
      config.headers.Authorization = `Bearer ${accessToken}`;
    }
    console.log(`API Request: ${config.method?.toUpperCase()} ${config.url}`);
    return config;
  },
  (error) => {
    console.error('API Request Error:', error);
    return Promise.reject(error);
  }
);

// Response interceptor: on 401, try exactly one silent refresh-and-retry before giving up and signaling
// AuthContext to sign the user out (never retries a request that was itself already retried once).
apiClient.interceptors.response.use(
  (response: AxiosResponse) => {
    console.log(`API Response: ${response.status} - ${response.config.url}`);
    return response;
  },
  async (error) => {
    const original = error.config as (typeof error.config & { _retried?: boolean }) | undefined;

    if (error.response?.status === 401 && original && !original._retried) {
      original._retried = true;
      const newToken = await refreshAccessToken();
      if (newToken) {
        original.headers = original.headers ?? {};
        original.headers.Authorization = `Bearer ${newToken}`;
        return apiClient(original);
      }
      onSessionExpired?.();
    }

    console.error('API Response Error:', error.response?.data || error.message);

    // Handle different error types
    if (error.response) {
      // Server responded with error status
      const status = error.response.status;
      const message = error.response.data?.detail || error.response.data?.message || 'Server Error';

      switch (status) {
        case 404:
          console.error('API endpoint not found:', error.config.url);
          break;
        case 500:
          console.error('Server error:', message);
          break;
        default:
          console.error(`API Error ${status}:`, message);
      }
    } else if (error.request) {
      // Network error
      console.error('Network error - cannot reach API server');
    }

    return Promise.reject(error);
  }
);

/** True when `err` is an HTTP error response with exactly this status (axios keeps that detail out of the callers). */
export function isHttpStatus(err: unknown, status: number): boolean {
  return axios.isAxiosError(err) && err.response?.status === status;
}

// Type definitions for API responses
export interface ApiResponse<T> {
  data: T;
  status: number;
  message?: string;
}

export interface HealthCheckResponse {
  status: string;
  timestamp: string;
  components: {
    database: { status: string; stock_prices_count: number };
    ml_data: { status: string; total_signals: number; top_ai_picks: number };
    screener: { status: string; available_sectors: number; filter_options_loaded: boolean };
  };
}

export interface MarketSummary {
  total_symbols: number;
  // null when technical_indicators has no rows yet -- never a fabricated 1.0 "normal volume".
  avg_volume_ratio: number | null;
  last_updated: string;
}

export interface StockItem {
  symbol: string;
  current_price: number;
  price_change_pct: number;
  volume: number;
  // null on the gainers/losers lists when no technical_indicators row exists for the
  // symbol -- "not known", never a fabricated "normal volume" default (see CLAUDE.md's
  // FM3.4 finding). The unusual-volume list only ever returns a real value.
  volume_ratio: number | null;
  sector: string;
  market_cap: number | null;
}

export interface MainPageData {
  timestamp: string;
  market_summary: MarketSummary;
  top_gainers: StockItem[];
  top_losers: StockItem[];
  unusual_volume: StockItem[];
}

export interface ScreenerFilterRequest {
  min_price?: number;
  max_price?: number;
  min_volume?: number;
  min_volume_ratio?: number;
  min_market_cap?: number;
  max_market_cap?: number;
  min_price_change?: number;
  max_price_change?: number;
  min_rsi?: number;
  max_rsi?: number;
  sectors?: string[];
  quality_grades?: string[];
  min_quality_score?: number;
  limit?: number;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
}

export interface ScreenerResult {
  symbol: string;
  current_price: number;
  price_change_pct: number;
  volume: number;
  volume_ratio: number;
  rsi_14: number | null;
  sector: string;
  market_cap: number | null;
  pe_ratio: number | null;
  quality_grade: string | null;
  quality_score: number | null;
  donchian_position: number;
  technical_strength: string;
}

/** Result counts per facet value (backend `_generate_facets`). */
export interface ScreenerFacets {
  sectors: Record<string, number>;
  quality_grades: Record<string, number>;
  technical_strength: Record<string, number>;
  market_cap_ranges: Record<'micro' | 'small' | 'mid' | 'large' | 'mega', number>;
}

export interface ScreenerResponse {
  /** The non-null request fields the backend actually applied, plus its resolved sort. */
  filters_applied: Partial<ScreenerFilterRequest>;
  total_matches: number;
  results: ScreenerResult[];
  facets: ScreenerFacets;
  execution_time_ms: number;
}

export interface FilterOptions {
  sectors: Array<{ name: string; stock_count: number }>;
  // null = unknown (no rows to aggregate yet), never a fabricated 0
  price_ranges: {
    min: number | null;
    max: number | null;
    percentiles: { '25th': number | null; median: number | null; '75th': number | null };
  };
  market_cap_ranges: {
    min: number | null;
    max: number | null;
    percentiles: { '25th': number | null; median: number | null; '75th': number | null };
  };
  quality_grades: string[];
  technical_ranges: {
    rsi: { min: number; max: number; oversold: number; overbought: number };
    volume_ratio: { min: number; max: number; normal: number; unusual: number };
  };
}

export interface FilterPreset {
  name: string;
  description: string;
  filters: ScreenerFilterRequest;
}

export interface MarketOverview {
  market_breadth: {
    total_stocks: number;
    gainers: number;
    losers: number;
    advance_decline_ratio: number;
    unusual_volume_count: number;
    overbought_count: number;
    oversold_count: number;
  };
  market_indicators: {
    avg_volume_ratio: number;
    avg_rsi: number;
    total_market_cap: number;
  };
  sector_performance: Array<{
    sector: string;
    stock_count: number;
    avg_performance: number;
  }>;
}

export interface SectorBreakdownResponse {
  sector_performance: Array<{ sector: string; stock_count: number; avg_performance: number }>;
  timestamp: string;
}

export interface TechnicalDistributionResponse {
  technical_summary: {
    overbought_stocks: number;
    oversold_stocks: number;
    unusual_volume_stocks: number;
    avg_rsi: number;
    avg_volume_ratio: number;
  };
  timestamp: string;
}

// Stock detail types
export interface StrategyPlan {
  direction: 'long' | 'short';
  entry_price: number;
  stop_loss_price: number;
  risk_pct: number;
  tp1: number;
  tp2: number;
  tp3: number;
  reward_pct_tp1: number;
  reward_pct_tp2: number;
  reward_pct_tp3: number;
  momentum_score: number | null;   // null = component unavailable (score is renormalised without it)
  fundamentals_score: number | null;
  alignment_score: number | null;
  model_risk_score: number | null;
  risk_bonus: number;
  strategy_score: number;
}

export interface StockAIRating {
  signal_type: string;
  urgency: string | null;
  ml_confidence: string | null;
  ml_momentum_probability: number | null;
  alignment_score: number | null;
  alignment_grade: string | null;
  signal_strength: string | null;
  reasoning: string | null;
  ml_trade_recommendation: string | null;
  plan: StrategyPlan | null;
}

export interface QuarterlyFinancials {
  quarter: string | null;
  fiscal_year: number | null;
  fiscal_quarter: number | null;
  revenue: number | null;
  net_income: number | null;
  eps: number | null;
  gross_margin: number | null;
  operating_margin: number | null;
  net_margin: number | null;
  revenue_growth_yoy: number | null;
  eps_growth_yoy: number | null;
  roe: number | null;
  roa: number | null;
  debt_to_equity: number | null;
  current_ratio: number | null;
  free_cash_flow: number | null;
}

export interface BreakoutHistoryEntry {
  date: string;
  breakout_type: string | null;
  entry_price: number | null;
  success: boolean | null;
  max_gain_10d: number | null;
  max_loss_10d: number | null;
  days_to_peak: number | null;
}

export interface BreakoutTrackRecord {
  total_breakouts: number;
  win_rate: number | null;
  avg_gain_10d: number | null;
  avg_loss_10d: number | null;
  entries: BreakoutHistoryEntry[];
}

/** Absent (no row / no key) or NULL in the database — both mean "unknown", never zero. */
export type Maybe<T> = T | null | undefined;

/** Price + fundamentals fields both snapshot shapes carry. Any of them can be unknown. */
interface StockSnapshotCommon {
  current_price: Maybe<number>;
  price_change_pct: Maybe<number>;
  sector: Maybe<string>;
  industry: Maybe<string>;
  quality_grade: Maybe<string>;
  growth_score: Maybe<number>;
  profitability_score: Maybe<number>;
  financial_health_score: Maybe<number>;
  valuation_score: Maybe<number>;
  overall_quality_score: Maybe<number>;
  pe_ratio: Maybe<number>;
  pb_ratio: Maybe<number>;
  ps_ratio: Maybe<number>;
  beta: Maybe<number>;
  dividend_yield: Maybe<number>;
  market_cap: Maybe<number>;
  /** Only present on no-signal snapshots (daily_fundamentals row); active-signal snapshots don't carry it. */
  peg_ratio?: Maybe<number>;
  /** Only present on active-signal snapshots. */
  market_cap_formatted?: Maybe<string>;
}

/** Latest weekly context the screener attached to an active signal (null when no weekly row exists). */
export interface WeeklyContext {
  week_ending_date: Maybe<string>;
  weekly_trend: Maybe<string>;
  weekly_close: Maybe<number>;
  weekly_donchian_high: Maybe<number>;
  weekly_donchian_low: Maybe<number>;
  weekly_rsi: Maybe<number>;
  weekly_volume_ratio: Maybe<number>;
}

/** Latest monthly context the screener attached to an active signal (null when no monthly row exists). */
export interface MonthlyContext {
  month_ending_date: Maybe<string>;
  monthly_trend: Maybe<string>;
  monthly_close: Maybe<number>;
  monthly_donchian_high: Maybe<number>;
  monthly_donchian_low: Maybe<number>;
  trend_strength: Maybe<number>;
}

export interface ActiveSignalSnapshot extends StockSnapshotCommon {
  rsi_14: Maybe<number>;
  donchian_high: Maybe<number>;
  donchian_low: Maybe<number>;
  price_position_in_channel: Maybe<number>;
  sma_20: Maybe<number>;
  sma_50: Maybe<number>;
  atr_14: Maybe<number>;
  weekly_context: WeeklyContext | null | undefined;
  monthly_context: MonthlyContext | null | undefined;
}

/** Raw `technical_indicators` columns the UI reads (a whole row is null if the symbol has none). */
export interface DailyIndicatorRow {
  rsi_14: Maybe<number>;
  donchian_high_20: Maybe<number>;
  donchian_low_20: Maybe<number>;
  price_position: Maybe<number>;
  sma_20: Maybe<number>;
  sma_50: Maybe<number>;
  atr_14: Maybe<number>;
}

/** Raw `weekly_technical_indicators` columns the UI reads. */
export interface WeeklyIndicatorRow {
  donchian_high_20w: Maybe<number>;
  donchian_low_20w: Maybe<number>;
  rsi_14w: Maybe<number>;
  price_position_weekly: Maybe<number>;
  weekly_close: Maybe<number>;
}

/** Raw `monthly_technical_indicators` columns the UI reads. */
export interface MonthlyIndicatorRow {
  donchian_high_12m: Maybe<number>;
  donchian_low_12m: Maybe<number>;
  trend_direction: Maybe<string>;
  trend_strength_6m: Maybe<number>;
  monthly_close: Maybe<number>;
}

export interface NoSignalSnapshot extends StockSnapshotCommon {
  volume: Maybe<number>;
  as_of_date: Maybe<string>;
  daily: DailyIndicatorRow | null | undefined;
  weekly: WeeklyIndicatorRow | null | undefined;
  monthly: MonthlyIndicatorRow | null | undefined;
}

interface StockDetailBase {
  symbol: string;
  ai_rating: StockAIRating;
  quarterly_financials: QuarterlyFinancials[];
  breakout_track_record: BreakoutTrackRecord;
}

/** Discriminated on `data_source`: the two shapes differ in where multi-timeframe technicals live. */
export type StockDetail =
  | (StockDetailBase & { data_source: 'active_signal'; snapshot: ActiveSignalSnapshot })
  | (StockDetailBase & { data_source: 'no_signal'; snapshot: NoSignalSnapshot });

export interface PriceBar {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number;
  volume: number | null;
  donchian_high_20: number | null;
  donchian_low_20: number | null;
}

export interface PriceHistoryResponse {
  symbol: string;
  days: number;
  bars: PriceBar[];
}

export interface EarningsDateEntry {
  date: string;
  eps_estimate: number | null;
  eps_actual: number | null;
  surprise_pct: number | null;
}

export interface EarningsResponse {
  symbol: string;
  earnings_dates: EarningsDateEntry[];
}

export interface SignalSummary {
  symbol: string;
  has_signal: boolean;
  signal_type?: string | null;
  ml_confidence?: string | null;
  urgency?: string | null;
  alignment_grade?: string | null;
}

export interface SymbolSearchResult {
  symbol: string;
  sector: string | null;
  current_price: number | null;
}

export interface AlphaFinderResult {
  symbol: string;
  current_price: number;
  price_change_pct: number;
  signal_type: string;
  sector: string;
  quality_grade: string | null;
  overall_quality_score: number | null;
  ml_confidence: string | null;
  ml_momentum_probability: number | null;
  alignment_score: number | null;
  alignment_grade: string | null;
  combined_score: number | null;
  stop_loss_price: number | null;
  target_price: number | null;
  reward_risk_ratio: number | null;
  summary_text: string | null;
  is_new: boolean;
}

export interface AlphaFinderResponse {
  generated_at: string | null;
  total: number;
  results: AlphaFinderResult[];
}

export interface AlphaFinderParams {
  signal_type?: string;
  min_confidence?: string;
  sector?: string;
  limit?: number;
}

// Momentum Board -- the dashboard's find-big-winners panel (see
// backend/services/momentum_board_service.py). Reads the same confluence rule
// (top ranks of 2+ of gainers/ATR-expansion/volume-surge = starred) already
// validated and running for the Telegram channel; no ML, no alignment score.
export interface MomentumBoardResult {
  symbol: string;
  category: 'breakout' | 'near_breakout';
  close: number | null;
  ret1_pct: number | null;
  rvol: number | null;         // today's volume / median of the prior 50 sessions
  range_atr: number | null;    // today's true range / prior-day ATR14
  below_high_pct: number | null;
  atr: number | null;
  dv20: number | null;
  list_ranks: Record<string, number>;
  starred: boolean;
  star_lists: string[];
  sector: string | null;
  quality_grade: string | null;
  overall_quality_score: number | null;
  market_cap: number | null;
  // From earnings_calendar -- coverage is thin as of 2026-09-23 (a handful of symbols), so
  // this is null for most rows today; it fills in as the updater's backfill progresses.
  next_earnings_date: string | null;
  days_to_earnings: number | null;
}

export interface MomentumBoardResponse {
  session_date: string | null;
  generated_at: string | null;
  universe_n: number;
  counts: Record<string, number>;
  // Unfiltered count of today's confirmed (2+ list) names, independent of this call's own
  // category/sector/grade filters -- the dashboard's Active Signals card uses this.
  starred_total: number;
  total: number;
  results: MomentumBoardResult[];
}

export interface MomentumBoardParams {
  category?: 'breakout' | 'near_breakout' | 'all';
  sector?: string;
  min_quality_grade?: string;
  limit?: number;
}

// Momentum Leaders -- "who's still moving after making an earlier Momentum Board list" (see
// backend/services/momentum_leaders_service.py, mechanism/alerts/board.py). Distinct from
// Momentum Board, which ranks TODAY's own lists.
export interface MomentumLeaderRow {
  symbol: string;
  close: number;
  prev_close: number;
  ret1_pct: number;
  listed_sessions: number;     // how many of the last few sessions it was on a list
  first_listed: string;
  last_listed: string;
  category: 'breakout' | 'near_breakout';
  range_pos: number | null;    // (close - low) / (high - low) for today's bar
  at_day_high: boolean;
  above_20d_high: boolean;
  rvol: number | null;
}

export interface MomentumLeadersBoard {
  session: string;
  window: string[];
  n_sessions: number;
  n_pool: number;
  n_measured: number;
  n_excluded: number;
  higher: number;
  lower: number;
  flat: number;
  at_day_high: number;
  above_20d_high: number;
  top: MomentumLeaderRow[];
  more: MomentumLeaderRow[];
}

export interface MomentumLeadersResponse {
  session_date: string | null;
  board: MomentumLeadersBoard | null;
}

export interface StrategyRankResult {
  symbol: string;
  sector: string;
  direction: 'long' | 'short';
  current_price: number;
  price_change_pct: number;
  stop_loss_price: number;
  risk_pct: number;
  tp1: number;
  tp2: number;
  tp3: number;
  reward_pct_tp1: number;
  reward_pct_tp2: number;
  reward_pct_tp3: number;
  momentum_score: number | null;   // null = component unavailable (score is renormalised without it)
  fundamentals_score: number | null;
  alignment_score: number | null;
  model_risk_score: number | null;
  risk_bonus: number;
  strategy_score: number;
  quality_grade: string | null;
  ml_confidence: string | null;
  summary_text: string | null;
  is_new: boolean;
}

export interface StrategyRankResponse {
  generated_at: string | null;
  total: number;
  results: StrategyRankResult[];
}

export interface StrategyRankParams {
  direction?: 'all' | 'long' | 'short';
  min_confidence?: string;
  limit?: number;
}

export interface DeepValueEntry {
  symbol: string;
  sector: string;
  current_price: number;
  distance_from_low_pct: number;
  donchian_low_12m: number;
  valuation_score: number;
  financial_health_score: number;
  overall_quality_score: number | null;
  quality_grade: string | null;
  pe_ratio: number | null;
  market_cap: number | null;
  is_turnaround: boolean;
  latest_quarter?: string;
  latest_net_income?: number;
  previous_quarter?: string;
  previous_net_income?: number;
}

export interface DeepValueScanResponse {
  turnaround_alerts: DeepValueEntry[];
  deep_value_watch: DeepValueEntry[];
}

export interface DeepValueScanParams {
  near_low_pct?: number;
  min_valuation_score?: number;
  min_financial_health_score?: number;
}

// System health types
export type HealthStatus = 'healthy' | 'warning' | 'critical' | 'unknown';

export interface HealthCheck {
  label: string;
  status: HealthStatus;
  detail?: string;
  error?: string;
  latest_date?: string | null;
  days_stale?: number | null;
}

export interface HealthSection {
  overall: HealthStatus;
  checks: HealthCheck[];
}

export interface SectorCoverage {
  sector: string;
  n: number;
}

export interface UniverseCoverage {
  overall: HealthStatus;
  total_symbols?: number;
  active_symbols?: number;
  inactive_symbols?: number;
  quarterly_fundamentals_coverage_pct?: number;
  ten_year_history_coverage_pct?: number;
  sector_breakdown?: SectorCoverage[];
  error?: string;
}

export interface SystemHealthReport {
  overall: HealthStatus;
  generated_at: string;
  pipeline_freshness: HealthSection;
  data_quality: HealthSection;
  ml_health: HealthSection;
  universe_coverage: UniverseCoverage;
}

// ML stats types
export interface CurrentModel {
  found: boolean;
  /** Why no model is served (found === false). */
  reason?: string;
  legacy_models_ignored?: number;
  target?: string | null;
  target_definition?: string | null;
  /** Untouched-holdout AUC recorded by the honest-evaluation trainer. */
  holdout_auc?: number | null;
  holdout_auc_ci95?: [number, number] | null;
  holdout_base_rate?: number | null;
  version?: string;
  trained_at?: string;
  days_since_trained?: number;
  feature_count?: number | null;
  registered?: boolean;
  accuracy?: number | null;
  precision?: number | null;
  recall?: number | null;
  f1?: number | null;
  auc?: number | null;
  training_samples?: number | null;
  training_start_date?: string | null;
  training_end_date?: string | null;
}

export interface FeatureImportanceEntry {
  feature: string;
  importance: number;
}

export interface FeatureImportance {
  available: boolean;
  reason?: string;
  model_version?: string;
  features?: FeatureImportanceEntry[];
}

export interface TrainingDatasetStats {
  total_rows?: number;
  unique_symbols?: number;
  earliest_date?: string | null;
  latest_date?: string | null;
  positive_class_pct?: number;
  negative_class_pct?: number;
  plan_profitable_pct?: number;
  price_discontinuities?: number;
  symbols_with_discontinuities?: number;
  bullish_pct?: number;
  bearish_pct?: number;
  error?: string;
}

export interface ConfidenceBucket {
  confidence: string;
  count: number;
}

export interface PredictionTrackRecord {
  total_predictions?: number;
  latest_prediction_date?: string | null;
  confidence_distribution?: ConfidenceBucket[];
  outcomes_evaluated?: number;
  win_rate_pct?: number | null;
  avg_return_pct?: number | null;
  error?: string;
}

export interface ModelEvaluationGateCheck {
  check: string;
  value: number;
  required: number;
  pass: boolean;
}

/** Latest honest (chronological-holdout) evaluation of a candidate model, promoted or not. */
export interface ModelEvaluation {
  target: string;
  target_definition: string;
  generated_at: string;
  promoted: boolean;
  excluded_features: string[];
  holdout_range: [string, string] | null;
  n_holdout: number | null;
  base_rate: number | null;
  auc: number | null;
  auc_ci95: [number, number] | null;
  direction_baseline_auc: number | null;
  auc_vs_plan_profit_bullish?: number | null;
  auc_vs_plan_profit_bearish?: number | null;
  top_decile_lift: number | null;
  walk_forward_auc: number[];
  gate: ModelEvaluationGateCheck[];
}

export interface ModelHistoryEntry {
  version: string;
  trained_at: string;
  /** false = legacy file on disk that the screener refuses to load (unvalidated pipeline). */
  served?: boolean;
  accuracy: number | null;
  auc: number | null;
}

export interface MLStatsReport {
  generated_at: string;
  current_model: CurrentModel;
  feature_importance: FeatureImportance;
  training_dataset: TrainingDatasetStats;
  prediction_track_record: PredictionTrackRecord;
  model_history: ModelHistoryEntry[];
  evaluations?: ModelEvaluation[];
}

// Performance types
export interface EndpointPerfStat {
  endpoint: string;
  method: string;
  sample_count: number;
  total_count: number;
  avg_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  max_ms: number | null;
  error_count: number;
  error_rate_pct: number;
  last_seen: number | null;
  status: HealthStatus;
}

export interface RoutePerfStat {
  route: string;
  metric: string;
  sample_count: number;
  total_count: number;
  avg_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  max_ms: number | null;
  status: HealthStatus;
}

export interface DatabasePerf {
  connection_acquire_ms: number | null;
  connection_status: HealthStatus;
  connection_error?: string;
  sample_query_total_ms: number | null;
  sample_query_status: HealthStatus;
  sample_query_label?: string;
  sample_query_error?: string;
  pure_query_ms?: number;
  connection_overhead_pct?: number;
  pooling_active: boolean;
}

export interface PerformanceReport {
  generated_at: string;
  overall: HealthStatus;
  backend_uptime_seconds: number;
  backend_endpoints: EndpointPerfStat[];
  frontend_routes: RoutePerfStat[];
  database: DatabasePerf;
}

// Market indices / macro data types (real S&P 500/Nasdaq/Russell/VIX/etc.
// data -- see backend/routers/market.py, distinct from screenerApi's
// stock-universe-only "market overview")
export interface MarketIndexEntry {
  symbol: string;
  display_name: string;
  latest_date: string | null;
  close: number | null;
  change: number | null;
  change_pct: number | null;
  sparkline: number[];
  regime?: 'calm' | 'normal' | 'elevated' | 'fear';
}

export interface MarketIndicesResponse {
  indices: MarketIndexEntry[];
}

export interface SectorHistoryPoint {
  date: string;
  stock_count: number;
  avg_performance: number;
}

export interface SectorHistoryEntry {
  sector: string;
  history: SectorHistoryPoint[];
}

export interface SectorHistoryResponse {
  days: number;
  sectors: SectorHistoryEntry[];
}

// Core API service class
class ApiService {
  // Health and status endpoints
  async healthCheck(): Promise<HealthCheckResponse> {
    const response = await apiClient.get<HealthCheckResponse>('/api/health');
    return response.data;
  }

  // Dashboard endpoints
  async getMainPageData(): Promise<MainPageData> {
    const response = await apiClient.get<MainPageData>('/api/dashboard/main-page-data');
    return response.data;
  }

  async getTopGainers(limit: number = 10): Promise<StockItem[]> {
    const response = await apiClient.get<StockItem[]>(`/api/dashboard/top-gainers?limit=${limit}`);
    return response.data;
  }

  async getTopLosers(limit: number = 10): Promise<StockItem[]> {
    const response = await apiClient.get<StockItem[]>(`/api/dashboard/top-losers?limit=${limit}`);
    return response.data;
  }

  async getUnusualVolume(limit: number = 10): Promise<StockItem[]> {
    const response = await apiClient.get<StockItem[]>(`/api/dashboard/unusual-volume?limit=${limit}`);
    return response.data;
  }

  // Screener endpoints
  async searchStocks(filters: ScreenerFilterRequest): Promise<ScreenerResponse> {
    const response = await apiClient.post<ScreenerResponse>('/api/screener/search', filters);
    return response.data;
  }

  async getFilterOptions(): Promise<FilterOptions> {
    const response = await apiClient.get<FilterOptions>('/api/screener/filters');
    return response.data;
  }

  async getMarketOverview(): Promise<MarketOverview> {
    const response = await apiClient.get<MarketOverview>('/api/screener/market-overview');
    return response.data;
  }

  // Real market-index/macro data endpoints
  async getMarketIndices(sparklineDays: number = 30): Promise<MarketIndicesResponse> {
    const response = await apiClient.get<MarketIndicesResponse>('/api/market/indices', {
      params: { sparkline_days: sparklineDays },
    });
    return response.data;
  }

  async getSectorHistory(days: number = 90): Promise<SectorHistoryResponse> {
    const response = await apiClient.get<SectorHistoryResponse>('/api/market/sectors/history', {
      params: { days },
    });
    return response.data;
  }

  async getFilterPresets(): Promise<{ presets: FilterPreset[] }> {
    const response = await apiClient.get<{ presets: FilterPreset[] }>('/api/screener/presets');
    return response.data;
  }

  async searchWithPreset(presetName: string): Promise<ScreenerResponse> {
    const response = await apiClient.post<ScreenerResponse>(`/api/screener/presets/${presetName}/search`);
    return response.data;
  }

  // Analytics endpoints
  async getSectorBreakdown(): Promise<SectorBreakdownResponse> {
    const response = await apiClient.get<SectorBreakdownResponse>('/api/screener/analytics/sector-breakdown');
    return response.data;
  }

  async getTechnicalDistribution(): Promise<TechnicalDistributionResponse> {
    const response = await apiClient.get<TechnicalDistributionResponse>('/api/screener/analytics/technical-distribution');
    return response.data;
  }

  // Stock detail endpoints
  async getStockDetail(symbol: string): Promise<StockDetail> {
    const response = await apiClient.get<StockDetail>(`/api/stock/${symbol}`);
    return response.data;
  }

  async getStockPriceHistory(symbol: string, days: number = 180): Promise<PriceHistoryResponse> {
    const response = await apiClient.get<PriceHistoryResponse>(`/api/stock/${symbol}/price-history`, {
      params: { days },
    });
    return response.data;
  }

  async getStockEarnings(symbol: string, limit: number = 12): Promise<EarningsResponse> {
    const response = await apiClient.get<EarningsResponse>(`/api/stock/${symbol}/earnings`, {
      params: { limit },
    });
    return response.data;
  }

  async getStockSignal(symbol: string): Promise<SignalSummary> {
    const response = await apiClient.get<SignalSummary>(`/api/stock/${symbol}/signal`);
    return response.data;
  }

  async searchSymbols(query: string, limit: number = 10): Promise<SymbolSearchResult[]> {
    if (!query.trim()) return [];
    const response = await apiClient.get<SymbolSearchResult[]>('/api/stock/search', {
      params: { q: query, limit },
    });
    return response.data;
  }

  // Alpha finder endpoint
  async getAlphaFinder(params: AlphaFinderParams = {}): Promise<AlphaFinderResponse> {
    const response = await apiClient.get<AlphaFinderResponse>('/api/alpha/finder', { params });
    return response.data;
  }

  async getMomentumBoard(params: MomentumBoardParams = {}): Promise<MomentumBoardResponse> {
    const response = await apiClient.get<MomentumBoardResponse>('/api/momentum-board', { params });
    return response.data;
  }

  async getMomentumLeaders(): Promise<MomentumLeadersResponse> {
    const response = await apiClient.get<MomentumLeadersResponse>('/api/momentum-leaders');
    return response.data;
  }

  // Trading strategy endpoint
  async getStrategyRank(params: StrategyRankParams = {}): Promise<StrategyRankResponse> {
    const response = await apiClient.get<StrategyRankResponse>('/api/strategy/rank', { params });
    return response.data;
  }

  // Deep value / turnaround alerts endpoint
  async getDeepValueScan(params: DeepValueScanParams = {}): Promise<DeepValueScanResponse> {
    const response = await apiClient.get<DeepValueScanResponse>('/api/deep-value/scan', { params });
    return response.data;
  }

  // System health endpoint
  async getSystemHealth(): Promise<SystemHealthReport> {
    const response = await apiClient.get<SystemHealthReport>('/api/system-health/');
    return response.data;
  }

  // ML stats endpoint
  async getMLStats(): Promise<MLStatsReport> {
    const response = await apiClient.get<MLStatsReport>('/api/ml-stats/');
    return response.data;
  }

  // Performance endpoint
  async getPerformance(): Promise<PerformanceReport> {
    const response = await apiClient.get<PerformanceReport>('/api/performance/');
    return response.data;
  }

  async reportClientMetric(route: string, metric: string, durationMs: number): Promise<void> {
    // Fire-and-forget — a failed report should never affect the page it's reporting on.
    try {
      await apiClient.post('/api/performance/client-metric', { route, metric, duration_ms: durationMs });
    } catch {
      /* non-critical */
    }
  }
}

// Export singleton instance
export const apiService = new ApiService();

// Export individual service functions for convenience
export const dashboardApi = {
  getMainPageData: () => apiService.getMainPageData(),
  getTopGainers: (limit?: number) => apiService.getTopGainers(limit),
  getTopLosers: (limit?: number) => apiService.getTopLosers(limit),
  getUnusualVolume: (limit?: number) => apiService.getUnusualVolume(limit),
};

export const screenerApi = {
  search: (filters: ScreenerFilterRequest) => apiService.searchStocks(filters),
  getFilterOptions: () => apiService.getFilterOptions(),
  getMarketOverview: () => apiService.getMarketOverview(),
  getPresets: () => apiService.getFilterPresets(),
  searchWithPreset: (presetName: string) => apiService.searchWithPreset(presetName),
};

export const marketApi = {
  getIndices: (sparklineDays?: number) => apiService.getMarketIndices(sparklineDays),
  getSectorHistory: (days?: number) => apiService.getSectorHistory(days),
};

export const analyticsApi = {
  getSectorBreakdown: () => apiService.getSectorBreakdown(),
  getTechnicalDistribution: () => apiService.getTechnicalDistribution(),
};

export const stockApi = {
  getDetail: (symbol: string) => apiService.getStockDetail(symbol),
  getPriceHistory: (symbol: string, days?: number) => apiService.getStockPriceHistory(symbol, days),
  getEarnings: (symbol: string, limit?: number) => apiService.getStockEarnings(symbol, limit),
  getSignal: (symbol: string) => apiService.getStockSignal(symbol),
  search: (query: string, limit?: number) => apiService.searchSymbols(query, limit),
};

export const alphaApi = {
  getFinder: (params?: AlphaFinderParams) => apiService.getAlphaFinder(params),
};

export const momentumBoardApi = {
  getBoard: (params?: MomentumBoardParams) => apiService.getMomentumBoard(params),
};

export const momentumLeadersApi = {
  getLeaders: () => apiService.getMomentumLeaders(),
};

export const strategyApi = {
  getRank: (params?: StrategyRankParams) => apiService.getStrategyRank(params),
};

export const deepValueApi = {
  scan: (params?: DeepValueScanParams) => apiService.getDeepValueScan(params),
};

export const systemHealthApi = {
  getReport: () => apiService.getSystemHealth(),
};

export const mlStatsApi = {
  getReport: () => apiService.getMLStats(),
};

export const performanceApi = {
  getReport: () => apiService.getPerformance(),
  reportClientMetric: (route: string, metric: string, durationMs: number) =>
    apiService.reportClientMetric(route, metric, durationMs),
};

// Utility functions
export const formatters = {
  currency: (value: number): string => {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value);
  },

  percentage: (value: number, decimals: number = 2): string => {
    return `${value >= 0 ? '+' : ''}${value.toFixed(decimals)}%`;
  },

  number: (value: number): string => {
    return new Intl.NumberFormat('en-US').format(value);
  },

  compact: (value: number): string => {
    return new Intl.NumberFormat('en-US', {
      notation: 'compact',
      maximumFractionDigits: 1,
    }).format(value);
  },

  marketCap: (value: number | null | undefined): string => {
    if (!value) return 'N/A';
    if (value >= 1e12) return `$${(value / 1e12).toFixed(1)}T`;
    if (value >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
    if (value >= 1e6) return `$${(value / 1e6).toFixed(1)}M`;
    return `$${value.toLocaleString()}`;
  },
};

export default apiService;