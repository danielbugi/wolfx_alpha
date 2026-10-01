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

// ---------------------------------------------------------------- Release B research layer
// GET /api/strategies/{key}/{version}/research/*  (mechanism/strategy_analytics/research.py). Before migration 22 every
// list answers 200 with availability.state === 'not_available' and no numbers; a count is never 0 unless it was measured.
export type AvailabilityState = 'ok' | 'no_data' | 'not_available';

export interface Availability {
  state: AvailabilityState;
  release: string;
  reason: string | null;
  capture_runs: number | null;
  schema: Record<string, boolean>;
  activation?: CaptureActivation | null;
}

/** Where capture is expected from (research_capture_activation). Before `active_from` capture is 'not_active'. */
export interface CaptureActivation {
  state: 'not_active' | 'enabled' | 'disabled';
  active_from: string | null;
  current: { state: 'enabled' | 'disabled'; effective_from_session: string; set_by: string; set_at: string; note: string } | null;
  boundaries: number;
  pre_activation_sessions?: number;
}

/** A count that may be unmeasured: `value` is null unless state is 'ok'. */
export interface CountMetric {
  value: number | null;
  state: MetricState;
  reason?: string;
  release?: string;
  /** ledger stage only: rows carrying this session's observation ids */
  linked?: number;
}

export interface FunnelStage extends CountMetric {
  key: 'evaluated' | 'candidates' | 'guard_passed' | 'ranked' | 'selected' | 'ledger_signals';
  label: string;
}

export type CaptureStatus = 'complete' | 'partial' | 'failed' | 'running' | 'missing' | 'disabled' | 'not_active' | 'not_available';

export interface FeatureSetInfo {
  version: string;
  description: string | null;
  extends: string | null;
  manifest_hash: string;
  feature_count: number | null;
  registered_at: string;
}

export interface ResearchSummary {
  strategy: StrategyRef;
  availability: Availability;
  definitions: Record<string, string>;
  forward_outcomes: Metric;
  latest_session: { session_date: string; run_id: number; capture_status: CaptureStatus; finished_at: string | null } | null;
  feature_set: { current: string | null; registered: FeatureSetInfo[] };
  funnel: { session_date: string | null; stages: FunnelStage[] };
  cards: Record<'observations' | 'snapshots' | 'sessions_captured' | 'bullish' | 'bearish' | 'guard_passed'
    | 'guard_rejected' | 'guard_not_evaluated' | 'selected', CountMetric>;
  rates: Record<'guard_pass_rate' | 'selected_rate' | 'missing_feature_rate' | 'snapshot_coverage' | 'capture_coverage', Metric>;
}

export interface RunCounters {
  candidates: number | null;
  captured: number | null;
  already_captured: number | null;
  stale_skipped: number | null;
  snapshot_skipped: number | null;
  invalid_skipped: number | null;
  guard_rejected: number | null;
  guard_not_evaluated: number | null;
  hash_drift: number | null;
  snapshot_drift: number | null;
  defaulted_flagged: number | null;
}

export interface MissingStats {
  snapshots: number;
  partial_snapshots: number;
  missing_slots: number;
  total_slots: number;
  rate: Metric;
}

export interface CaptureRun {
  id: number | null;
  session_date: string;
  stored_status: string | null;
  health: { status: CaptureStatus; reasons: string[]; notes: string[] };
  feature_set_version: string | null;
  run_attempts: number;
  started_at: string | null;
  finished_at: string | null;
  runtime_seconds: number | null;
  universe_size: number | null;
  counters: RunCounters;
  accounted: number | null;
  unaccounted: number | null;
  skipped_symbol_count: number;
  error: string | null;
  code_ref: string | null;
  missing_features?: MissingStats | null;
  ledger_signals?: number;
  skipped_symbols?: Record<string, string>;
}

export interface CaptureRunsResponse {
  strategy: StrategyRef;
  availability: Availability;
  stuck_after_minutes: number;
  definitions: Record<string, string>;
  activation: CaptureActivation | null;
  overall: { status: CaptureStatus; session_date: string | null; reason: string | null };
  latest: CaptureRun | null;
  history: CaptureRun[];
}

export type GuardStatus = 'passed' | 'rejected' | 'not_evaluated';

export interface CandidateItem {
  id: number;
  symbol: string;
  session_date: string;
  direction: Direction;
  candidate_class: string;
  triggered: boolean;
  entry_close: number | null;
  breakout_dist_atr: number | null;
  distance_to_channel_pct: number | null;
  passed_guard: boolean | null;
  guard_status: GuardStatus;
  guard_reasons: string[];
  alignment_score: number | null;
  quality_grade: string | null;
  combined_score: number | null;
  session_rank: number | null;
  ml_status: string | null;
  ml_score: number | null;
  selected: boolean;
  snapshot_id: number;
  snapshot_status: 'complete' | 'partial';
  missing_count: number | null;
  ledger_signal_id: number | null;
}

export type CandidateSort = 'rank' | 'combined_desc' | 'alignment_desc' | 'breakout_desc' | 'symbol' | 'grade';

export interface CandidatePage {
  strategy: StrategyRef;
  availability: Availability;
  items: CandidateItem[];
  /** null when research data is unavailable -- never 0 */
  total: number | null;
  limit: number;
  offset: number;
  sort: CandidateSort;
  has_more: boolean;
  session_date: string | null;
  facets: { classes: { value: string; count: number }[]; grades: { value: string; count: number }[] };
}

export interface CandidateQuery {
  session_date?: string;
  direction?: Direction;
  guard?: GuardStatus;
  selected?: boolean;
  symbol?: string;
  candidate_class?: string;
  grade?: string;
  sort?: CandidateSort;
  limit: number;
  offset: number;
}

export interface CandidateDetail {
  id: number;
  strategy: StrategyRef;
  identity: { symbol: string; session_date: string; bar_date: string; direction: Direction; strategy_version: string };
  strategy_context: {
    candidate_class: string;
    triggered: boolean;
    levels: { entry_close: number | null; channel_high_prev: number | null; channel_low_prev: number | null };
    breakout_dist_atr: number | null;
    distance_to_channel_pct: number | null;
    guard: { status: GuardStatus; reasons: string[] };
    alignment_score: number | null;
    quality_grade: string | null;
    combined_score: number | null;
    session_rank: number | null;
    selected: boolean;
    screener_defaults: string[];
    model: { status: string | null; score: number | null; confidence: string | null; version: string | null };
    extension: Record<string, unknown>;
  };
  capture: { run_id: number; run_status: string; captured_at: string; run_finished_at: string | null; code_ref: string | null };
  snapshot: { id: number; feature_set_version: string; snapshot_status: 'complete' | 'partial'; missing_count: number | null };
  lineage: {
    observation_id: number;
    snapshot_id: number;
    signal: { id: number; signal_date: string; status: SignalStatus; lifecycle: Lifecycle; outcome_r: number | null } | null;
  };
}

export interface SnapshotFeature {
  name: string;
  value: number | string | boolean | null;
  unit: string | null;
  definition: string | null;
  missing: boolean;
}

export interface SnapshotDetail {
  id: number;
  strategy: StrategyRef;
  identity: { symbol: string; session_date: string; bar_date: string; feature_set_version: string };
  snapshot_status: 'complete' | 'partial';
  missing_features: string[];
  groups: { key: string; label: string; items: SnapshotFeature[] }[];
  feature_set: { version: string; manifest_hash: string; description: string | null };
  provenance: { content_hash: string; code_ref: string | null; captured_at: string };
  observations: { id: number; direction: Direction; candidate_class: string; session_date: string }[];
}

export type ApiError = ControlErrorInfo;

/** Maps any failure (network, 401 after the client's own refresh-and-retry, 404, 422) to {code, message}. */
export const toApiError = (err: unknown): ApiError => toControlError(err);

function cleanQuery(q: SignalQuery | CandidateQuery): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {};
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
  researchSummary: async (key: string, version: string): Promise<ResearchSummary> =>
    (await apiClient.get<ResearchSummary>(`${base(key, version)}/research/summary`)).data,
  captureRuns: async (key: string, version: string, limit = 30): Promise<CaptureRunsResponse> =>
    (await apiClient.get<CaptureRunsResponse>(`${base(key, version)}/research/capture-runs`, { params: { limit } })).data,
  candidates: async (key: string, version: string, query: CandidateQuery): Promise<CandidatePage> =>
    (await apiClient.get<CandidatePage>(`${base(key, version)}/research/candidates`, { params: cleanQuery(query) })).data,
  candidate: async (key: string, version: string, id: number): Promise<CandidateDetail> =>
    (await apiClient.get<CandidateDetail>(`${base(key, version)}/research/candidates/${id}`)).data,
  snapshot: async (key: string, version: string, id: number): Promise<SnapshotDetail> =>
    (await apiClient.get<SnapshotDetail>(`${base(key, version)}/research/snapshots/${id}`)).data,
};
