// File: frontend/src/lib/strategyFormat.ts
// Presentation only for /strategies. No statistic is derived here: every value, sample size and state comes from the
// Strategy Intelligence API (mechanism/strategy_analytics owns the definitions). These helpers turn a backend Metric
// into text without ever turning "nothing measured" into a 0, and translate machine states into readable labels.
import type { EvaluationState, Lifecycle, Metric, PriceDataState, SignalStatus } from '@/services/strategyApi';

/** 'r' is a signed result (+1.00R / −1.00R); 'magnitude_r' is an unsigned size in R (e.g. MAE, never read as profit). */
export type MetricKind = 'rate' | 'r' | 'magnitude_r' | 'sessions';
export type MetricTone = 'ok' | 'preliminary' | 'empty' | 'unavailable';

export interface MetricDisplay {
  text: string;
  note: string;
  tone: MetricTone;
}

export const NO_RESOLVED_NOTE = 'No resolved signals yet';
export const RELEASE_B_NOTE = 'Not collected — Release B';
const MINUS = '−';

export function formatValue(value: number, kind: MetricKind): string {
  if (kind === 'rate') return `${(value * 100).toFixed(1)}%`;
  if (kind === 'r') return formatR(value);
  if (kind === 'magnitude_r') return `${Math.abs(value).toFixed(2)}R`;
  return value.toFixed(1);
}

/** An instant (ISO timestamp) as '2026-09-28 23:22 UTC' -- explicit, so it cannot silently shift with the viewer's zone. */
export function formatUtcTimestamp(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

/** Signed R with a real minus sign: +1.00R, −0.35R, 0.00R. */
export function formatR(value: number, digits = 2): string {
  const sign = value > 0 ? '+' : value < 0 ? MINUS : '';
  return `${sign}${Math.abs(value).toFixed(digits)}R`;
}

/** The backend's state decides everything: 'no_data' is never shown as 0, 'preliminary' always carries its N. */
export function describeMetric(m: Metric, kind: MetricKind, emptyNote: string = NO_RESOLVED_NOTE): MetricDisplay {
  if (m.state === 'not_available' || (m.state !== 'no_data' && m.value === null)) {
    return { text: '—', note: m.release ? `Not available — Release ${m.release}` : 'Not available', tone: 'unavailable' };
  }
  if (m.state === 'no_data') return { text: '—', note: emptyNote, tone: 'empty' };
  const text = formatValue(m.value as number, kind);
  if (m.state === 'preliminary') return { text, note: `Preliminary · N=${m.n}`, tone: 'preliminary' };
  return { text, note: `N=${m.n}`, tone: 'ok' };
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** A market-session date ('YYYY-MM-DD') as 'Sep 28, 2026'. Parsed as a calendar date, never through the browser's
 * time zone, so it cannot shift a day. */
export function formatSession(d: string | null | undefined): string {
  if (!d) return '—';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(d);
  if (!m) return d;
  return `${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}, ${m[1]}`;
}

export function formatPrice(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function formatCount(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : v.toLocaleString('en-US');
}

export const STATUS_LABEL: Record<SignalStatus, string> = {
  open: 'Open',
  stopped: 'Stopped',
  target1: 'Target 1',
  target2: 'Target 2',
  target3: 'Target 3',
  expired: 'Expired',
};

export const LIFECYCLE_LABEL: Record<Lifecycle, string> = { open: 'Open', held: 'Held', resolved: 'Resolved' };

export const PRICE_STATE_LABEL: Record<PriceDataState, string> = {
  awaiting_first_session: 'Waiting for first forward session',
  current: 'Price data current',
  lagging: 'Price data lagging',
  stale: 'Price data stale',
  no_price_data: 'No price data',
};

export const EVAL_STATE_LABEL: Record<EvaluationState, string> = {
  up_to_date: 'Evaluated through latest session',
  pending: 'Evaluation pending',
  invalid_price_blocked: 'Blocked by an invalid price bar',
};

export const FLAG_LABEL: Record<string, string> = {
  split_suspect: 'Split suspect',
  same_bar_stop_and_target: 'Ambiguous — stop and target on the same bar',
};

export const INVARIANT_LABEL: Record<string, string> = {
  duplicate_event_identities: 'Duplicate signal identities',
  multiple_open_positions: 'Multiple open positions per symbol',
  strategy_version_mismatch: 'Strategy version mismatches',
  resolved_missing_outcome: 'Resolved signals missing an outcome',
  open_with_outcome: 'Open signals carrying an outcome',
  resolution_flag_on_non_stop: 'Ambiguity flag on a non-stop',
  evaluation_flag_on_resolved: 'Hold flag on a resolved signal',
};

export const CAPABILITY_LABEL: Record<string, string> = {
  signal_ledger: 'Signal ledger',
  outcome_evaluation: 'Outcome evaluation',
  same_bar_ambiguity_flag: 'Same-bar ambiguity flag',
  split_suspect_hold: 'Split-suspect hold',
  max_adverse_excursion: 'Max adverse excursion (MAE)',
  max_favourable_excursion: 'Max favourable excursion (MFE)',
  post_exit_trajectory: 'Post-exit price trajectory',
  forward_return_labels: 'Forward-return labels',
  candidate_observations: 'Candidate observations',
  feature_snapshots: 'T0 feature snapshots',
  training_ready_observations: 'Training-ready observations',
  evaluator_run_history: 'Evaluator run history',
};

export function humanize(key: string): string {
  return key.replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}
