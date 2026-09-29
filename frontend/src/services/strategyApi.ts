// File: frontend/src/services/strategyApi.ts
// Strategy Intelligence API (backend/routers/strategy_intelligence.py). Every number shown on /strategies comes from
// here -- win rate, R, outcome counts, held/stale counts and each metric's sample state are computed once, in
// mechanism/strategy_analytics, and only presented by the frontend. Types mirror that contract exactly.
import { apiClient } from '@/services/api';
import { toControlError } from '@/services/telegramApi';
import type { ControlErrorInfo } from '@/services/telegramApi';

export type MetricState = 'ok' | 'preliminary' | 'no_data' | 'not_available';

/** A rate or average with its sample size. `value` is null unless state is 'ok' or 'preliminary'. */
export interface Metric {
  value: number | null;
  n: number | null;
  state: MetricState;
  reason?: string;
  release?: string | null;
}

export interface StrategyRef {
  id: number;
  key: string;
  version: string;
  display_name: string;
  description: string | null;
  registered_at: string;
}

export interface StrategyListItem extends StrategyRef {
  tracking: {
    total_signals: number;
    open: number;
    resolved: number;
    first_tracked_session: string | null;
    latest_tracked_session: string | null;
    tracking_status: 'tracking' | 'no_signals_yet';
  };
}

export interface Capability {
  available: boolean;
  release: string | null;
  reason?: string;
}

export type Capabilities = Record<string, Capability>;

export interface Performance {
  resolved_n: number;
  winners: number;
  stopped: number;
  expired: number;
  expired_positive: number;
  expired_negative: number;
  ambiguous: number;
  win_rate: Metric;
  stop_rate: Metric;
  expiry_rate: Metric;
  average_r: Metric;
  median_r: Metric;
  average_holding_bars: Metric;
  median_holding_bars: Metric;
  average_mae_r: Metric;
  average_mfe_r: Metric;
}

export interface TerminalOutcome {
  count: number;
  share: Metric;
}

export interface ExpiredOutcome extends TerminalOutcome {
  average_r: Metric;
  positive_count: number;
  negative_count: number;
  flat_count: number;
  positive_share: Metric;
}

export interface Outcomes {
  resolved_n: number;
  terminal: {
    stopped: TerminalOutcome;
    target1: TerminalOutcome;
    target2: TerminalOutcome;
    target3: TerminalOutcome;
    expired: ExpiredOutcome;
  };
  ambiguous: { count: number; share_of_resolved: Metric; share_of_stopped: Metric };
  target_milestones: Record<'reached_target1' | 'reached_target2' | 'reached_target3', { count: number; rate: Metric }>;
  milestone_semantics: string;
}

export type Direction = 'bullish' | 'bearish';

export interface DirectionBlock {
  signals: number;
  open: number;
  normally_open: number;
  held: number;
  resolved: number;
  winners: number;
  stopped: number;
  expired: number;
  ambiguous: number;
  win_rate: Metric;
  average_r: Metric;
  median_r: Metric;
  average_holding_bars: Metric;
  median_holding_bars: Metric;
  stop_rate: Metric;
  expiry_rate: Metric;
  target_rates: Record<'target1' | 'target2' | 'target3', Metric>;
}

export interface StrategySummary {
  strategy: StrategyRef;
  definitions_version: string;
  reference_session: string | null;
  tracking: {
    tracking_status: 'tracking' | 'no_signals_yet';
    first_tracked_session: string | null;
    latest_tracked_session: string | null;
    total_signals: number;
    open: number;
    normally_open: number;
    held: number;
    resolved: number;
    bullish: number;
    bearish: number;
    signals_this_week: number | null;
    signals_this_month: number | null;
  };
  performance: Performance;
  outcomes: Outcomes;
  directions: Record<Direction, DirectionBlock>;
  capabilities: Capabilities;
}

export type PriceDataState = 'awaiting_first_session' | 'current' | 'lagging' | 'stale' | 'no_price_data';
export type EvaluationState = 'up_to_date' | 'pending' | 'invalid_price_blocked';

export interface AttentionSignal {
  id: number;
  symbol: string;
  signal_date: string;
  symbol_latest_bar: string | null;
  lag_sessions: number;
  lag_capped: boolean;
  price_data_state: PriceDataState;
  evaluation_state: EvaluationState;
}

export interface HeldSignal {
  id: number;
  symbol: string;
  signal_date: string;
  direction: 1 | -1;
  evaluation_flag: string;
  held_since: string;
}

export interface CoverageEntry {
  count: number;
  total: number;
  share: Metric;
  collected: boolean;
  release?: string;
}

export interface DataHealth {
  strategy: StrategyRef;
  definitions_version: string;
  status: 'healthy' | 'attention' | 'violation';
  issues: string[];
  ledger: {
    total_rows: number;
    latest_signal_session: string | null;
    normally_open: number;
    held: number;
    held_by_flag: Record<string, number>;
    resolved: number;
    ambiguous_resolutions: number;
  };
  market_data: {
    reference_session: string | null;
    stale_after_sessions: number;
    session_window: number;
    open_signals_by_price_data_state: Record<PriceDataState, number>;
    open_signals_without_forward_bar: number;
  };
  evaluation: {
    open_signals_by_evaluation_state: Record<EvaluationState, number>;
    latest_evaluated_session_open: string | null;
    evaluator_runs: Metric;
  };
  attention: { open_signals: AttentionSignal[]; open_signals_total: number; held_signals: HeldSignal[] };
  invariants: Record<string, number>;
  coverage: Record<string, CoverageEntry>;
  capabilities: Capabilities;
}

export type SignalStatus = 'open' | 'stopped' | 'target1' | 'target2' | 'target3' | 'expired';
export type Lifecycle = 'open' | 'held' | 'resolved';

export interface SignalItem {
  id: number;
  symbol: string;
  signal_date: string;
  direction: Direction;
  entry_price: number;
  atr: number;
  stop_price: number;
  target1_price: number;
  target2_price: number;
  target3_price: number;
  sector: string | null;
  quality_grade: string | null;
  status: SignalStatus;
  outcome_r: number | null;
  mae_r: number | null;
  resolved_date: string | null;
  bars_held: number | null;
  last_evaluated_date: string;
  resolution_flag: string | null;
  evaluation_flag: string | null;
  strategy_version: string;
  model_version: string | null;
  lifecycle: Lifecycle;
  is_winner: boolean | null;
}

export interface SignalPage {
  items: SignalItem[];
  total: number;
  limit: number;
  offset: number;
  sort: SignalSort;
  has_more: boolean;
}

export interface SignalDetail {
  id: number;
  strategy: StrategyRef;
  identity: { symbol: string; signal_date: string; direction: Direction; strategy_version: string };
  trade_plan: {
    entry_price: number;
    stop_price: number;
    target1_price: number;
    target2_price: number;
    target3_price: number;
    atr: number;
    risk_per_share: number;
    risk_pct_of_entry: number | null;
  };
  context: { quality_grade: string | null; sector: string | null; model_version: string | null; feature_set_version: string | null };
  lifecycle: {
    state: Lifecycle;
    status: SignalStatus;
    is_winner: boolean | null;
    outcome_r: number | null;
    resolved_date: string | null;
    bars_held: number | null;
    mae_r: number | null;
    last_evaluated_date: string;
    resolution_flag: string | null;
    evaluation_flag: string | null;
    reference_session: string | null;
    symbol_latest_bar: string | null;
    forward_bars_available: number;
  };
  provenance: {
    ledger_created_at: string;
    observation_id: number | null;
    feature_snapshot_id: number | null;
    release_b_lineage: boolean;
  };
  timeline: { date: string; event: string }[];
}

export type SignalSort = 'newest' | 'oldest' | 'symbol' | 'grade' | 'r_desc' | 'r_asc' | 'holding_desc' | 'holding_asc';

export interface SignalQuery {
  symbol?: string;
  direction?: Direction;
  status?: SignalStatus;
  lifecycle?: Lifecycle;
  evaluation_flag?: 'split_suspect';
  resolution_flag?: 'same_bar_stop_and_target';
  date_from?: string;
  date_to?: string;
  quality_grade?: string;
  sort?: SignalSort;
  limit: number;
  offset: number;
}

export type ApiError = ControlErrorInfo;

/** Maps any failure (network, 401 after the client's own refresh-and-retry, 404, 422) to {code, message}. */
export const toApiError = (err: unknown): ApiError => toControlError(err);

function cleanQuery(q: SignalQuery): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [k, v] of Object.entries(q)) {
    if (v === undefined || v === null || v === '') continue;
    out[k] = typeof v === 'string' ? v.trim() : v;
  }
  return out;
}

const base = (key: string, version: string) => `/api/strategies/${encodeURIComponent(key)}/${encodeURIComponent(version)}`;

export const strategyApi = {
  list: async (): Promise<StrategyListItem[]> =>
    (await apiClient.get<{ strategies: StrategyListItem[] }>('/api/strategies')).data.strategies,
  summary: async (key: string, version: string): Promise<StrategySummary> =>
    (await apiClient.get<StrategySummary>(`${base(key, version)}/summary`)).data,
  dataHealth: async (key: string, version: string): Promise<DataHealth> =>
    (await apiClient.get<DataHealth>(`${base(key, version)}/data-health`)).data,
  signals: async (key: string, version: string, query: SignalQuery): Promise<SignalPage> =>
    (await apiClient.get<SignalPage>(`${base(key, version)}/signals`, { params: cleanQuery(query) })).data,
  signal: async (key: string, version: string, id: number): Promise<SignalDetail> =>
    (await apiClient.get<SignalDetail>(`${base(key, version)}/signals/${id}`)).data,
};
