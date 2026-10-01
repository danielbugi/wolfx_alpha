// Synthetic Release B API responses in the exact shape of mechanism/strategy_analytics/research.py. These are test
// fixtures: they are NOT production data and no production capture exists yet (migration 22 is not applied).
import type {
  Availability, CandidateDetail, CandidateItem, CandidatePage, CaptureRun, CaptureRunsResponse, CountMetric, Metric, ResearchSummary,
  SnapshotDetail, StrategyRef,
} from '@/services/strategyApi';

export const STRATEGY: StrategyRef = {
  id: 1, key: 'donchian_breakout', version: 'v1', display_name: 'Donchian Breakout', description: null, registered_at: '2026-09-29T08:26:00+00:00',
};

export const OTHER_STRATEGY: StrategyRef = {
  id: 7, key: 'mean_reversion_demo', version: 'v2', display_name: 'Mean Reversion Demo', description: null, registered_at: '2026-10-01T08:00:00+00:00',
};

const RELEASE_B = 'B';

export const AVAIL_NOT_AVAILABLE: Availability = {
  state: 'not_available', release: RELEASE_B, capture_runs: null,
  reason: 'Candidate capture is not installed on this database yet (migration 22 is not applied).',
  schema: { candidate_capture_run: false, candidate_observation: false, feature_snapshot: false },
};
export const AVAIL_NO_DATA: Availability = {
  state: 'no_data', release: RELEASE_B, capture_runs: 0,
  reason: 'The research tables exist, but candidate capture has not run for this strategy yet.',
  schema: { candidate_capture_run: true, candidate_observation: true, feature_snapshot: true },
};
export const AVAIL_OK: Availability = {
  state: 'ok', release: RELEASE_B, capture_runs: 3, reason: null,
  schema: { candidate_capture_run: true, candidate_observation: true, feature_snapshot: true },
};

const unavailableCount = (): CountMetric => ({ value: null, state: 'not_available', release: RELEASE_B });
const noDataCount = (): CountMetric => ({ value: null, state: 'no_data', reason: 'No captured sessions yet' });
const okCount = (value: number): CountMetric => ({ value, state: 'ok' });
const unavailableRate = (): Metric => ({ value: null, state: 'not_available', n: null, reason: 'Not collected — Release B' } as unknown as Metric);
const noDataRate = (): Metric => ({ value: null, state: 'no_data', n: 0, reason: 'No captured sessions yet' } as unknown as Metric);
const okRate = (value: number, n: number): Metric => ({ value, state: 'ok', n } as unknown as Metric);

const STAGE_LABELS: [string, string][] = [
  ['evaluated', 'Evaluated'], ['candidates', 'Candidates'], ['guard_passed', 'Guard passed'],
  ['ranked', 'Qualified / ranked'], ['selected', 'Selected'], ['ledger_signals', 'Ledger signals'],
];

const FORWARD: Metric = {
  value: null, state: 'not_available', n: null,
  reason: 'Forward-outcome capture (migration 23, feature set fwd_v1) does not exist yet.',
} as unknown as Metric;

const FEATURE_SET = {
  version: 't0_v1', description: 'T0 market snapshot, first feature set', extends: null,
  manifest_hash: 'abcdef0123456789abcdef', feature_count: 38, registered_at: '2026-10-01T09:00:00+00:00',
};

const CARD_KEYS = ['observations', 'snapshots', 'sessions_captured', 'bullish', 'bearish', 'guard_passed', 'guard_rejected', 'guard_not_evaluated', 'selected'] as const;
const RATE_KEYS = ['guard_pass_rate', 'selected_rate', 'missing_feature_rate', 'snapshot_coverage', 'capture_coverage'] as const;

function summaryWith(avail: Availability, parts: Partial<ResearchSummary>): ResearchSummary {
  return {
    strategy: STRATEGY, availability: avail, definitions: {}, forward_outcomes: FORWARD, latest_session: null,
    feature_set: { current: null, registered: [] },
    funnel: { session_date: null, stages: STAGE_LABELS.map(([key, label]) => ({ key, label, ...unavailableCount() })) as ResearchSummary['funnel']['stages'] },
    cards: Object.fromEntries(CARD_KEYS.map((k) => [k, unavailableCount()])) as ResearchSummary['cards'],
    rates: Object.fromEntries(RATE_KEYS.map((k) => [k, unavailableRate()])) as ResearchSummary['rates'],
    ...parts,
  };
}

export const researchUnavailable = (): ResearchSummary => summaryWith(AVAIL_NOT_AVAILABLE, {});

export const researchNoData = (): ResearchSummary => summaryWith(AVAIL_NO_DATA, {
  funnel: { session_date: null, stages: STAGE_LABELS.map(([key, label]) => ({ key, label, ...noDataCount() })) as ResearchSummary['funnel']['stages'] },
  cards: Object.fromEntries(CARD_KEYS.map((k) => [k, noDataCount()])) as ResearchSummary['cards'],
  rates: Object.fromEntries(RATE_KEYS.map((k) => [k, noDataRate()])) as ResearchSummary['rates'],
});

export function researchOk(status: 'complete' | 'partial' = 'complete'): ResearchSummary {
  const stages = [
    { key: 'evaluated', label: 'Evaluated', value: null, state: 'not_available', reason: 'Universe size is not recorded by the screener.' },
    { key: 'candidates', label: 'Candidates', value: 1843, state: 'ok' },
    { key: 'guard_passed', label: 'Guard passed', value: 1700, state: 'ok' },
    { key: 'ranked', label: 'Qualified / ranked', value: 412, state: 'ok' },
    { key: 'selected', label: 'Selected', value: 25, state: 'ok' },
    { key: 'ledger_signals', label: 'Ledger signals', value: 25, state: 'ok', linked: 25 },
  ] as ResearchSummary['funnel']['stages'];
  return summaryWith(AVAIL_OK, {
    latest_session: { session_date: '2026-10-02', run_id: 3, capture_status: status, finished_at: '2026-10-02T21:10:00+00:00' },
    feature_set: { current: 't0_v1', registered: [FEATURE_SET] },
    funnel: { session_date: '2026-10-02', stages },
    cards: {
      observations: okCount(5400), snapshots: okCount(5400), sessions_captured: okCount(3), bullish: okCount(3100), bearish: okCount(2300),
      guard_passed: okCount(5000), guard_rejected: okCount(400), guard_not_evaluated: okCount(0), selected: okCount(75),
    },
    rates: {
      guard_pass_rate: okRate(0.9225, 1843), selected_rate: okRate(0.0136, 1843), missing_feature_rate: okRate(0.021, 5400),
      snapshot_coverage: okRate(1, 5400), capture_coverage: okRate(status === 'complete' ? 1 : 0.97, 1843),
    },
  });
}

const counters = (over: Partial<CaptureRun['counters']> = {}): CaptureRun['counters'] => ({
  candidates: 1843, captured: 1843, already_captured: 0, stale_skipped: 0, snapshot_skipped: 0, invalid_skipped: 0,
  guard_rejected: 143, guard_not_evaluated: 0, hash_drift: 0, snapshot_drift: 0, defaulted_flagged: 0, ...over,
});

export function run(over: Partial<CaptureRun> & { status?: CaptureRun['health']['status'] } = {}): CaptureRun {
  const { status = 'complete', ...rest } = over;
  return {
    id: 3, session_date: '2026-10-02', stored_status: status === 'complete' ? 'complete' : status, feature_set_version: 't0_v1',
    health: { status, reasons: [], notes: [] }, run_attempts: 1, started_at: '2026-10-02T21:08:00+00:00', finished_at: '2026-10-02T21:10:02+00:00',
    runtime_seconds: 122.1, universe_size: null, counters: counters(), accounted: 1843, unaccounted: 0, skipped_symbol_count: 0, error: null,
    code_ref: 'abc123def456', missing_features: { snapshots: 1843, partial_snapshots: 12, missing_slots: 800, total_slots: 70034, rate: okRate(0.0114, 70034) },
    ledger_signals: 25, skipped_symbols: {}, ...rest,
  };
}

export const runsNotAvailable = (): CaptureRunsResponse => ({
  strategy: STRATEGY, availability: AVAIL_NOT_AVAILABLE, stuck_after_minutes: 30, definitions: {}, activation: null,
  overall: { status: 'not_available', session_date: null, reason: AVAIL_NOT_AVAILABLE.reason }, latest: null, history: [],
});

export function runsOf(latest: CaptureRun, history: CaptureRun[] = [latest], overall?: CaptureRunsResponse['overall']): CaptureRunsResponse {
  return {
    strategy: STRATEGY, availability: AVAIL_OK, stuck_after_minutes: 30, definitions: {},
    activation: { state: 'enabled', active_from: '2026-10-01', current: null, boundaries: 1, pre_activation_sessions: 7 },
    overall: overall ?? { status: latest.health.status, session_date: latest.session_date, reason: latest.health.reasons[0] ?? null },
    latest, history,
  };
}

export const partialRun = (): CaptureRun => run({
  status: 'partial',
  health: { status: 'partial', reasons: ['12 candidates were not captured (snapshot could not be computed)'], notes: ['3 hash-drift candidates kept their first capture'] },
  counters: counters({ captured: 1831, snapshot_skipped: 12, hash_drift: 3 }),
  accounted: 1843, skipped_symbol_count: 2, skipped_symbols: { ZZZZ: 'no price bars', QQQQ: 'insufficient history' },
});

export const failedRun = (): CaptureRun => run({
  status: 'failed', health: { status: 'failed', reasons: ['Capture run failed'], notes: [] }, error: 'RuntimeError: price fetch timed out',
  counters: counters({ captured: 0 }), runtime_seconds: 12.4, missing_features: null, ledger_signals: 0,
});

export const disabledRun = (): CaptureRun => run({
  id: null, status: 'disabled', stored_status: null, health: { status: 'disabled', reasons: ['No capture run exists for this session, after capture was activated'], notes: [] },
  counters: { candidates: null, captured: null, already_captured: null, stale_skipped: null, snapshot_skipped: null, invalid_skipped: null, guard_rejected: null, guard_not_evaluated: null, hash_drift: null, snapshot_drift: null, defaulted_flagged: null },
  accounted: null, unaccounted: null, runtime_seconds: null, started_at: null, finished_at: null, missing_features: null, ledger_signals: 25,
});

export function candidate(i: number, over: Partial<CandidateItem> = {}): CandidateItem {
  return {
    id: 1000 + i, symbol: `SYM${i}`, session_date: '2026-10-02', direction: i % 2 ? 'bearish' : 'bullish',
    candidate_class: i % 2 ? 'near_bearish' : 'bullish_breakout', triggered: true, entry_close: 50 + i, breakout_dist_atr: 0.4, distance_to_channel_pct: 1.2,
    passed_guard: true, guard_status: 'passed', guard_reasons: [], alignment_score: 70 + i, quality_grade: 'A', combined_score: 80.5 + i,
    session_rank: i + 1, ml_status: null, ml_score: null, selected: i === 0, snapshot_id: 2000 + i, snapshot_status: 'complete', missing_count: 0,
    ledger_signal_id: i === 0 ? 463 : null, ...over,
  };
}

export function candidatePage(over: Partial<CandidatePage> = {}): CandidatePage {
  const items = over.items ?? [0, 1, 2].map((i) => candidate(i));
  return {
    strategy: STRATEGY, availability: AVAIL_OK, items, total: items.length, limit: 50, offset: 0, sort: 'rank', has_more: false, session_date: '2026-10-02',
    facets: { classes: [{ value: 'bullish_breakout', count: 2 }, { value: 'near_bearish', count: 1 }], grades: [{ value: 'A', count: 3 }] },
    ...over,
  };
}

export const candidatePageUnavailable = (): CandidatePage => ({
  strategy: STRATEGY, availability: AVAIL_NOT_AVAILABLE, items: [], total: null, limit: 50, offset: 0, sort: 'rank', has_more: false, session_date: null,
  facets: { classes: [], grades: [] },
});

export const candidatePageNoData = (): CandidatePage => ({ ...candidatePageUnavailable(), availability: AVAIL_NO_DATA });

export function candidateDetail(over: Partial<CandidateDetail> = {}): CandidateDetail {
  return {
    id: 1000, strategy: STRATEGY,
    identity: { symbol: 'SYM0', session_date: '2026-10-02', bar_date: '2026-10-02', direction: 'bullish', strategy_version: 'v1' },
    strategy_context: {
      candidate_class: 'bullish_breakout', triggered: true, levels: { entry_close: 50, channel_high_prev: 48.5, channel_low_prev: 41 },
      breakout_dist_atr: 0.4, distance_to_channel_pct: 1.2, guard: { status: 'passed', reasons: [] }, alignment_score: 70, quality_grade: 'A',
      combined_score: 80.5, session_rank: 1, selected: true, screener_defaults: [], model: { status: null, score: null, confidence: null, version: null },
      extension: { urgency: 'HIGH', donchian_high_20: 48.5, donchian_low_20: 41, weekly_trend: 'up', weekly_asof: '2026-09-25', mystery_field: 'kept' },
    },
    capture: { run_id: 3, run_status: 'complete', captured_at: '2026-10-02T21:09:00+00:00', run_finished_at: '2026-10-02T21:10:02+00:00', code_ref: 'abc123def456' },
    snapshot: { id: 2000, feature_set_version: 't0_v1', snapshot_status: 'complete', missing_count: 0 },
    lineage: { observation_id: 1000, snapshot_id: 2000, signal: { id: 463, signal_date: '2026-10-02', status: 'open', lifecycle: 'open', outcome_r: null } },
    ...over,
  };
}

export function snapshotDetail(over: Partial<SnapshotDetail> = {}): SnapshotDetail {
  const f = (name: string, value: number | string | boolean | null, unit: string | null) =>
    ({ name, value, unit, definition: `${name} definition`, missing: value === null });
  return {
    id: 2000, strategy: STRATEGY, identity: { symbol: 'SYM0', session_date: '2026-10-02', bar_date: '2026-10-02', feature_set_version: 't0_v1' },
    snapshot_status: 'complete', missing_features: [],
    groups: [
      { key: 'price', label: 'Price', items: [f('close', 50, 'price'), f('open', 49.2, 'price')] },
      { key: 'trend', label: 'Trend', items: [f('sma_50', 46.1, 'price'), f('above_sma_200', true, 'bool')] },
      { key: 'momentum', label: 'Momentum', items: [f('rsi_14', 63.4, null)] },
      { key: 'volatility', label: 'Volatility', items: [f('atr_14', 1.8, 'price')] },
      { key: 'volume_liquidity', label: 'Volume / liquidity', items: [f('rvol_20', 1.62, 'ratio'), f('dollar_volume_20', 25_300_000, 'usd')] },
      { key: 'week52', label: '52-week', items: [f('pct_from_52w_high', -2.4, 'percent')] },
      { key: 'market_metadata', label: 'Market metadata', items: [f('sector', 'Technology', null)] },
      { key: 'data_quality', label: 'Data quality', items: [f('bars_available', 520, 'count')] },
    ],
    feature_set: { version: 't0_v1', manifest_hash: 'abcdef0123456789abcdef', description: null },
    provenance: { content_hash: '0123456789abcdef0123', code_ref: 'abc123def456', captured_at: '2026-10-02T21:09:00+00:00' },
    observations: [{ id: 1000, direction: 'bullish', candidate_class: 'bullish_breakout', session_date: '2026-10-02' }],
    ...over,
  };
}
