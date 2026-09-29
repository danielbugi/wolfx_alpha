// File: frontend/src/components/strategies/DataHealthTab.tsx
// Operational health of the strategy's data, not its performance. Every count and state comes from the backend's
// /data-health response; this component only arranges it, keeping a healthy system compact and warnings prominent.
import React from 'react';
import type { DataHealth, EvaluationState, PriceDataState } from '@/services/strategyApi';
import { EVAL_STATE_LABEL, FLAG_LABEL, formatCount, formatSession, INVARIANT_LABEL, PRICE_STATE_LABEL } from '@/lib/strategyFormat';
import { HealthBadge, Section, StatTile, WarningBanner } from '@/components/strategies/ui';

const PRICE_ORDER: PriceDataState[] = ['awaiting_first_session', 'current', 'lagging', 'stale', 'no_price_data'];
const EVAL_ORDER: EvaluationState[] = ['up_to_date', 'pending', 'invalid_price_blocked'];
const PRICE_TONE: Partial<Record<PriceDataState, string>> = { lagging: 'text-amber-700', stale: 'text-red-700', no_price_data: 'text-red-700' };
const EVAL_TONE: Partial<Record<EvaluationState, string>> = { invalid_price_blocked: 'text-red-700' };

function StateList<S extends string>({ order, counts, labels, tones }: {
  order: S[];
  counts: Record<S, number>;
  labels: Record<S, string>;
  tones: Partial<Record<S, string>>;
}) {
  return (
    <ul className="divide-y divide-slate-100 text-sm">
      {order.filter((s) => s in counts).map((s) => (
        <li key={s} className="flex items-center justify-between py-1.5">
          <span className={counts[s] > 0 ? tones[s] ?? 'text-slate-700' : 'text-slate-400'}>{labels[s]}</span>
          <span className={`tabular-nums ${counts[s] > 0 ? 'font-semibold text-slate-800' : 'text-slate-400'}`}>{formatCount(counts[s])}</span>
        </li>
      ))}
    </ul>
  );
}

export default function DataHealthTab({ health }: { health: DataHealth }) {
  const ps = health.market_data.open_signals_by_price_data_state;
  const es = health.evaluation.open_signals_by_evaluation_state;
  const violations = Object.entries(health.invariants).filter(([, n]) => n > 0);
  const violationTotal = violations.reduce((s, [, n]) => s + n, 0);
  const splitSuspect = health.ledger.held_by_flag.split_suspect ?? 0;

  return (
    <div className="space-y-4">
      <Section
        title="Data health"
        description={`Collection health of this strategy's ledger — separate from performance. Measured in market sessions against the latest landed session (${formatSession(health.market_data.reference_session)}), so weekends and holidays never read as stale.`}
        actions={<HealthBadge status={health.status} />}
      >
        <div className="space-y-3">
          {violations.length > 0 && (
            <WarningBanner tone="danger" title={`${formatCount(violationTotal)} integrity violation${violationTotal === 1 ? '' : 's'}`}>
              <ul className="list-disc pl-4">
                {violations.map(([k, n]) => <li key={k}>{INVARIANT_LABEL[k] ?? k}: {formatCount(n)}</li>)}
              </ul>
            </WarningBanner>
          )}
          {health.ledger.held > 0 && (
            <WarningBanner title={`${formatCount(health.ledger.held)} signal${health.ledger.held === 1 ? '' : 's'} held for review`}>
              A split-shaped price step appeared on the signal&apos;s path, so evaluation paused instead of resolving it. Held signals stay open and occupy their slot until reviewed.
            </WarningBanner>
          )}
          {ps.stale > 0 && (
            <WarningBanner tone="danger" title={`${formatCount(ps.stale)} open signal${ps.stale === 1 ? ' has' : 's have'} stale price data`}>
              The symbol is {health.market_data.stale_after_sessions} or more market sessions behind the latest session — possibly delisted or missing from the feed.
            </WarningBanner>
          )}
          {es.invalid_price_blocked > 0 && (
            <WarningBanner tone="danger" title={`${formatCount(es.invalid_price_blocked)} open signal${es.invalid_price_blocked === 1 ? ' is' : 's are'} blocked by an invalid price bar`}>
              The evaluator will not resolve a signal across a bar with a missing, non-positive or inconsistent price.
            </WarningBanner>
          )}
          {ps.lagging > 0 && (
            <WarningBanner title={`${formatCount(ps.lagging)} open signal${ps.lagging === 1 ? ' is' : 's are'} lagging`}>
              The symbol is missing the latest session (fewer than {health.market_data.stale_after_sessions} sessions behind).
            </WarningBanner>
          )}

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <StatTile label="Ledger signals" value={formatCount(health.ledger.total_rows)} />
            <StatTile label="Normally open" value={formatCount(health.ledger.normally_open)} />
            <StatTile label="Held" value={formatCount(health.ledger.held)} />
            <StatTile label="Split suspect" value={formatCount(splitSuspect)} />
            <StatTile label="Ambiguous resolutions" value={formatCount(health.ledger.ambiguous_resolutions)} />
            <StatTile label="Integrity violations" value={formatCount(violationTotal)} hint={violations.length === 0 ? `all ${Object.keys(health.invariants).length} checks pass` : undefined} />
          </div>
        </div>
      </Section>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Section title="Price data (open signals)" description="Does each open signal's symbol have prices for the latest session?">
          <StateList order={PRICE_ORDER} counts={ps} labels={PRICE_STATE_LABEL} tones={PRICE_TONE} />
          <p className="mt-2 text-xs text-slate-500">
            Open signals with no price bar since their signal session: <span className="font-semibold text-slate-700">{formatCount(health.market_data.open_signals_without_forward_bar)}</span>
          </p>
        </Section>
        <Section title="Evaluation (open signals)" description="Has the nightly evaluator walked each open signal through the latest session?">
          <StateList order={EVAL_ORDER} counts={es} labels={EVAL_STATE_LABEL} tones={EVAL_TONE} />
          <dl className="mt-2 space-y-1 text-xs">
            <div className="flex justify-between"><dt className="text-slate-500">Latest evaluated session (open signals)</dt><dd className="font-medium text-slate-700">{formatSession(health.evaluation.latest_evaluated_session_open)}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-slate-500">Evaluator execution history</dt><dd className="text-right text-slate-400">Not persisted yet</dd></div>
          </dl>
        </Section>
      </div>

      {health.attention.open_signals_total > 0 && (
        <Section title="Open signals needing attention" description={`${formatCount(health.attention.open_signals_total)} total${health.attention.open_signals_total > health.attention.open_signals.length ? `, most-behind ${health.attention.open_signals.length} shown` : ''}.`}>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="border-b border-slate-200 text-left text-xs text-slate-500">
                <th className="px-2 py-1.5 font-medium">Symbol</th><th className="px-2 py-1.5 font-medium">Signal</th>
                <th className="hidden px-2 py-1.5 font-medium sm:table-cell">Latest price bar</th>
                <th className="px-2 py-1.5 text-right font-medium">Sessions behind</th><th className="px-2 py-1.5 font-medium">State</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100">
                {health.attention.open_signals.map((a) => (
                  <tr key={a.id}>
                    <td className="px-2 py-1.5 font-semibold text-slate-800">{a.symbol}</td>
                    <td className="px-2 py-1.5 text-xs text-slate-500">{formatSession(a.signal_date)}</td>
                    <td className="hidden px-2 py-1.5 text-xs text-slate-500 sm:table-cell">{formatSession(a.symbol_latest_bar)}</td>
                    <td className="px-2 py-1.5 text-right tabular-nums">{a.lag_capped ? `≥ ${a.lag_sessions}` : a.lag_sessions}</td>
                    <td className="px-2 py-1.5 text-xs">{a.evaluation_state === 'invalid_price_blocked' ? EVAL_STATE_LABEL[a.evaluation_state] : PRICE_STATE_LABEL[a.price_data_state]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {health.attention.held_signals.length > 0 && (
        <Section title="Held signals" description="Evaluation paused until a person reviews and clears the flag.">
          <ul className="divide-y divide-slate-100 text-sm">
            {health.attention.held_signals.map((h) => (
              <li key={h.id} className="flex flex-wrap items-center justify-between gap-2 py-1.5">
                <span className="font-semibold text-slate-800">{h.symbol} <span className="text-xs font-normal text-slate-500">signal {formatSession(h.signal_date)}</span></span>
                <span className="text-xs text-amber-800">{FLAG_LABEL[h.evaluation_flag] ?? h.evaluation_flag} · held since {formatSession(h.held_since)}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}
