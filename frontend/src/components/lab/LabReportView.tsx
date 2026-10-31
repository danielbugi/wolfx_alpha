'use client';

/* eslint-disable @typescript-eslint/no-explicit-any */
import React from 'react';
import type { LabReport } from '@/services/labApi';
import { excludesZero, fmtInt, fmtInterval, fmtPct, fmtR } from '@/lib/labFormat';

const th = 'px-2 py-1 text-left text-xs font-semibold text-slate-500 border-b border-slate-200';
const td = 'px-2 py-1 text-sm text-slate-800 border-b border-slate-100 tabular-nums';

function Table({ head, rows }: { head: string[]; rows: React.ReactNode[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full">
        <thead><tr>{head.map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className={td}>{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function Sig({ ci }: { ci: number[] | null | undefined }) {
  return <span className={excludesZero(ci) ? 'text-slate-900 font-medium' : 'text-slate-400'} title="95% interval">{fmtInterval(ci)}</span>;
}

function StrategyBaseline({ d }: { d: any }) {
  const b = d.baseline;
  const years = Object.keys(b.by_year);
  return (
    <div className="space-y-4">
      <Table head={['Direction', 'Trades', 'Mean R', '95% interval', 'Win rate', 'Stopped']} rows={(['all', 'bullish', 'bearish'] as const).map((k) => [
        k, fmtInt(b[k].n), fmtR(b[k].mean_r), <Sig key="c" ci={b[k].ci95} />, fmtPct(b[k].win_rate_r_gt_0, 1), fmtPct(b[k].stopped, 1)])} />
      <Table head={['Year', 'Bullish mean R', 'n', 'Bearish mean R', 'n']} rows={years.map((y) => [y, fmtR(b.by_year[y].bullish.mean_r), fmtInt(b.by_year[y].bullish.n), fmtR(b.by_year[y].bearish.mean_r), fmtInt(b.by_year[y].bearish.n)])} />
    </div>
  );
}

function RankerStability({ d }: { d: any }) {
  const names = Object.keys(d).filter((k) => d[k] && typeof d[k] === 'object' && 'by_year' in d[k]);
  return (
    <Table head={['Ranker (high score = predicted better)', 'Years with positive spread', 'Mean yearly spread (R)', 'Spearman']} rows={names.map((n) => [
      n.replace(/_/g, ' '), `${d[n].years_with_positive_spread} / ${d[n].n_years}`, fmtR(d[n].mean_spread), fmtR(d[n].spearman_all, 4)])} />
  );
}

function MlEvaluation({ d }: { d: any }) {
  const runs = d.trainer_runs;
  const oos = d.out_of_sample_plan_outcome;
  return (
    <div className="space-y-4">
      <Table head={['Run', 'Holdout AUC', '95% CI', 'Direction-only baseline', 'AUC vs plan profit (bull / bear)', 'Promoted', 'Failed gates']} rows={Object.keys(runs).map((k) => [
        k.replace('train_', ''), String(runs[k].holdout_auc), `${runs[k].auc_ci95?.[0]} to ${runs[k].auc_ci95?.[1]}`, String(runs[k].direction_only_baseline_auc),
        `${runs[k].auc_vs_plan_profit_bullish} / ${runs[k].auc_vs_plan_profit_bearish}`, runs[k].promoted ? 'yes' : 'no', (runs[k].not_promoted_because || []).join(', ') || '—'])} />
      <Table head={['Out-of-sample feature set', 'AUC (R>0)', 'AUC bullish', 'AUC bearish', 'Mean R by score quintile (low → high)']} rows={['price_features_only', 'price_plus_market_regime', 'no_volume_features'].map((k) => [
        k.replace(/_/g, ' '), String(oos[k].auc_r_gt_0), String(oos[k].auc_within_bullish), String(oos[k].auc_within_bearish),
        oos[k].mean_r_by_score_quintile_low_to_high.map((x: number) => fmtR(x, 2)).join('  ')])} />
    </div>
  );
}

function ExitPolicies({ d }: { d: any }) {
  const names = Object.keys(d.overall);
  return (
    <div className="space-y-4">
      <Table head={['Policy', 'Net expectancy (R, 10 bps)', '95% interval', 'Bullish', 'Bearish', 'Profit factor', 'Avg duration', 'Top 5% share of profit', 'Trades |R|>10', 'Expectancy without them']} rows={names.map((p) => {
        const c = d.overall[p].cost_10bps;
        return [p.replace(/_/g, ' '), fmtR(c.expectancy_r), <Sig key="c" ci={c.expectancy_ci95} />, fmtR(d.overall[p].bullish_10bps.expectancy_r), fmtR(d.overall[p].bearish_10bps.expectancy_r),
          String(c.profit_factor), `${c.avg_duration_bars} bars`, fmtPct(c.top5pct_share_of_gross_profit, 0), fmtInt(c.trades_abs_r_gt_10), fmtR(c.expectancy_r_excluding_abs_r_gt_10)];
      })} />
      <p className="text-xs text-slate-500">Paired difference vs the fixed-3R exit on the same trades (gross R): {Object.keys(d.paired_vs_A_3R_gross_r).map((p) => `${p.replace(/_/g, ' ')} ${fmtR(d.paired_vs_A_3R_gross_r[p].mean)} (${fmtInterval(d.paired_vs_A_3R_gross_r[p].ci95)})`).join(' · ')}</p>
    </div>
  );
}

function CatalystEarnings({ d }: { d: any }) {
  const b = d.by_surprise_bucket;
  const x = d.catalyst_x_breakout_plan_r;
  const m1 = d.ml_targets.target1_event_significance_big_move;
  const m2 = d.ml_targets.target2_post_reaction_drift_after_positive_surprise;
  return (
    <div className="space-y-4">
      <Table head={['EPS surprise', 'Events', 'Reaction (mkt-adj.)', '5-day drift', '20-day drift', 'Share positive 20d']} rows={Object.keys(b).map((k) => [
        k, fmtInt(b[k].n), `${fmtPct(b[k].ar_reaction.mean)} (${fmtInterval(b[k].ar_reaction.ci95, (v) => fmtPct(v, 1))})`, `${fmtPct(b[k].ar_drift_5d.mean)}`,
        `${fmtPct(b[k].ar_drift_20d.mean)} (${fmtInterval(b[k].ar_drift_20d.ci95, (v) => fmtPct(v, 1))})`, fmtPct(b[k].share_positive_drift_20d, 1)])} />
      <Table head={['Breakout plan R (2021–2026)', 'Trades', 'Mean R', '95% interval']} rows={(['bullish', 'bearish'] as const).flatMap((dir) => Object.keys(x[dir]).map((k) => [
        `${dir}: ${k.replace(/_/g, ' ')}`, fmtInt(x[dir][k].n), fmtR(x[dir][k].mean), <Sig key={dir + k} ci={x[dir][k].ci95} />]))} />
      <p className="text-xs text-slate-500">Event-significance model AUC {m1.auc_model} vs trailing-volatility baseline {m1.auc_baseline_trailing_volatility_only}; post-reaction drift model AUC {m2.auc_model} (about chance).</p>
    </div>
  );
}

const NATURE_LABEL: Record<string, string> = {
  none: 'No anomaly',
  recurring_operating: 'Recurring operating improvement',
  one_time_suspected: 'Net income jump, no operating move (one-time suspected)',
  unclassified: 'Anomaly, unclassified',
};

const COMBO_LABEL: Record<string, string> = {
  all_bullish: 'Every bullish breakout (baseline)',
  recurring_catalyst: 'Recent recurring-operating catalyst',
  recurring_catalyst_and_relvol_ge_1_5: 'Catalyst + relative volume ≥ 1.5',
  recurring_catalyst_and_relvol_and_index_uptrend: 'Catalyst + relative volume ≥ 1.5 + index uptrend',
  no_catalyst_relvol_ge_1_5: 'No recent earnings, relative volume ≥ 1.5',
};

type YearStat = { n: number; mean: number };

/** Paired horizontal bars per calendar year: the plain bullish breakout (grey) and the cell (blue), each with its mean net R and trade count. Zero is the centre line; the scale is shared by every row. */
function YearBars({ plain, cell }: { plain: Record<string, YearStat>; cell: Record<string, YearStat> }) {
  const years = Object.keys(plain).sort();
  const max = Math.max(0.05, ...years.flatMap((y) => [Math.abs(plain[y]?.mean ?? 0), Math.abs(cell[y]?.mean ?? 0)]));
  const bar = (s: YearStat | undefined, color: string) => {
    if (!s) return <div className="h-2" />;
    const w = (Math.abs(s.mean) / max) * 50;
    return (
      <div className="relative h-2">
        <div className="absolute inset-y-0 left-1/2 w-px bg-slate-300" />
        <div className={`absolute inset-y-0 rounded-sm ${color}`} style={s.mean >= 0 ? { left: '50%', width: `${w}%` } : { right: '50%', width: `${w}%` }} />
      </div>
    );
  };
  return (
    <div className="space-y-1" role="img" aria-label="Mean net R by calendar year, plain bullish breakouts against the catalyst and relative-volume cell">
      {years.map((y) => (
        <div key={y} className="grid grid-cols-[3rem_1fr_9rem] items-center gap-2 text-[11px] text-slate-600">
          <span className="font-mono">{y}</span>
          <div className="space-y-0.5">{bar(plain[y], 'bg-slate-400')}{bar(cell[y], 'bg-cyan-600')}</div>
          <span className="font-mono">{plain[y] ? `${fmtR(plain[y].mean)} (${plain[y].n})` : '-'} / {cell[y] ? `${fmtR(cell[y].mean)} (${cell[y].n})` : '-'}</span>
        </div>
      ))}
      <div className="flex gap-4 pt-1 text-[11px] text-slate-500"><span><span className="mr-1 inline-block h-2 w-3 bg-slate-400 align-middle" />every bullish breakout</span><span><span className="mr-1 inline-block h-2 w-3 bg-cyan-600 align-middle" />catalyst + relative volume ≥ 1.5</span></div>
    </div>
  );
}

function SecEarnings({ d }: { d: any }) {
  const q = d.quality;
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {([
          ['Periodic filings seen', fmtInt(q.periodic_filings_seen)],
          ['With usable XBRL facts', fmtPct(q.share_with_xbrl_facts, 0)],
          ['Events matched to their 8-K 2.02 release', fmtPct(q.release_8k_matched_share, 0)],
          ['Filing date inconsistent with acceptance', fmtInt(q.timestamp_integrity.filing_date_inconsistent_with_acceptance)],
        ] as const).map(([label, value]) => (
          <div key={label} className="rounded-md border border-slate-200 p-2">
            <div className="text-[11px] text-slate-500">{label}</div>
            <div className="text-base font-semibold text-slate-800">{value}</div>
          </div>
        ))}
      </div>
      <p className="text-xs text-slate-500">
        Release timing: {Object.keys(q.timing_mix).map((k) => `${k} ${fmtInt(q.timing_mix[k])}`).join(' · ')}. Filing records the contract refused: {fmtInt(q.timestamp_integrity.rejected_filing_records_without_explicit_utc_acceptance_or_other_defect)}
        {' '}(all pre-2005 8-K item formats, outside the study window).
      </p>
      <Table head={['Earnings event', 'Events', 'Reaction (mkt-adj.)', '20-day drift after', 'Mean relative volume']} rows={Object.keys(d.reaction_by_nature).map((k) => [
        NATURE_LABEL[k] ?? k, fmtInt(d.reaction_by_nature[k].n),
        `${fmtPct(d.reaction_by_nature[k].ar_reaction.mean)} (${fmtInterval(d.reaction_by_nature[k].ar_reaction.ci95, (v) => fmtPct(v, 1))})`,
        `${fmtPct(d.reaction_by_nature[k].ar_drift_20d.mean)} (${fmtInterval(d.reaction_by_nature[k].ar_drift_20d.ci95, (v) => fmtPct(v, 1))})`, String(d.reaction_by_nature[k].mean_relvol)])} />
      <Table head={['Donchian breakout after earnings (15 days)', 'Trades', 'Plan R', 'Fixed 3R, net R']} rows={(['bullish', 'bearish'] as const).flatMap((dir) => Object.keys(d.breakouts_by_catalyst[dir]).map((k) => [
        `${dir}: ${NATURE_LABEL[k] ?? k.replace(/_/g, ' ')}`, fmtInt(d.breakouts_by_catalyst[dir][k].n), fmtR(d.breakouts_by_catalyst[dir][k].plan_r.mean),
        `${fmtR(d.breakouts_by_catalyst[dir][k].fixed_3R_net10.mean)} (${fmtInterval(d.breakouts_by_catalyst[dir][k].fixed_3R_net10.ci95)})`]))} />
      {d.bullish_combinations_chronological && (
        <>
          <h3 className="text-sm font-semibold text-slate-700">Out of sample: bullish breakouts, trained before 2022, tested from February 2022 (fixed 3R, net of costs)</h3>
          <Table head={['Cell', 'Train trades', 'Train R', 'Test trades', 'Test R (95% CI)']} rows={Object.keys(d.bullish_combinations_chronological).map((k) => {
            const c = d.bullish_combinations_chronological[k];
            return [COMBO_LABEL[k] ?? k.replace(/_/g, ' '), fmtInt(c.train.n), fmtR(c.train.mean), fmtInt(c.test.n), `${fmtR(c.test.mean)} (${fmtInterval(c.test.ci95)})`];
          })} />
        </>
      )}
      {d.exit_policy_selection_out_of_sample && (
        <>
          <h3 className="text-sm font-semibold text-slate-700">Exit policy picked on the train period, judged on the test period</h3>
          <Table head={['Catalyst group', 'Picked on train', 'Test: picked policy', 'Test: fixed 3R']} rows={Object.keys(d.exit_policy_selection_out_of_sample).map((k) => {
            const s = d.exit_policy_selection_out_of_sample[k];
            return [NATURE_LABEL[k] ?? k.replace(/_/g, ' '), s.best_policy_on_train, `${fmtR(s.test_best_policy.mean)} (${fmtInterval(s.test_best_policy.ci95)}), n=${fmtInt(s.test_best_policy.n)}`,
              `${fmtR(s.test_fixed_3R.mean)} (${fmtInterval(s.test_fixed_3R.ci95)}), n=${fmtInt(s.test_fixed_3R.n)}`];
          })} />
        </>
      )}
      {d.outcome_table && (
        <p className="text-xs text-slate-500">
          ML-ready outcome table: {fmtInt(d.outcome_table.rows)} events with point-in-time features and realised outcomes, split {Object.keys(d.outcome_table.split_counts).map((k) => `${k} ${fmtInt(d.outcome_table.split_counts[k])}`).join(' · ')}.
          {' '}Committed as <span className="font-mono">{d.outcome_table.file}</span>; csv sha256 <span className="font-mono">{String(d.outcome_table.sha256_of_csv).slice(0, 16)}…</span>
        </p>
      )}
      {d.bullish_combinations_by_year && (
        <>
          <h3 className="text-sm font-semibold text-slate-700">Walk-forward by year: catalyst + relative volume cell vs every bullish breakout (net R). Exploratory, not a validated edge.</h3>
          <YearBars plain={d.bullish_combinations_by_year.cells.all_bullish} cell={d.bullish_combinations_by_year.cells.recurring_catalyst_and_relvol_ge_1_5} />
          <p className="text-xs text-slate-500">
            Better than the plain breakout in {d.bullish_combinations_by_year.wins_vs_plain.recurring_catalyst_and_relvol_ge_1_5.years_better_than_plain} of {d.bullish_combinations_by_year.wins_vs_plain.recurring_catalyst_and_relvol_ge_1_5.years_evaluable} years with at least 30 trades on both sides. {d.bullish_combinations_by_year.rule}.
          </p>
        </>
      )}
      {d.timing_quality && (
        <>
          <h3 className="text-sm font-semibold text-slate-700">Timestamp check: does the information land on the bar the acceptance time says?</h3>
          <Table head={['Release timing', 'Events', 'Gap, reaction bar', 'Gap, bar before (placebo)', 'Abs. excess return, reaction bar', 'Abs. excess return, bar before']} rows={Object.keys(d.timing_quality.by_timing_class).map((k) => {
            const t = d.timing_quality.by_timing_class[k];
            return [k, fmtInt(t.n), fmtPct(t.mean_abs_gap_on_reaction_bar), fmtPct(t.mean_abs_gap_on_bar_before), fmtPct(t.mean_abs_ar_on_reaction_bar), fmtPct(t.mean_abs_ar_on_bar_before)];
          })} />
          <p className="text-xs text-slate-500">
            8-K release to periodic filing lag (days): median {d.timing_quality.release_to_periodic_filing_lag_days.median}, 10th to 90th percentile {d.timing_quality.release_to_periodic_filing_lag_days.p10} to {d.timing_quality.release_to_periodic_filing_lag_days.p90}; {fmtPct(d.timing_quality.release_to_periodic_filing_lag_days.share_released_same_day_or_before_periodic, 1)} of releases came on or before the periodic filing.
          </p>
        </>
      )}
      {d.strategy_variant_outcomes && (
        <>
          <h3 className="text-sm font-semibold text-slate-700">Outcomes by registered strategy variant (donchian_breakout v1 × exit policy; net of 10 bps)</h3>
          <Table head={['Variant (key, version, definition hash)', 'Bullish R (95% CI)', 'Bearish R (95% CI)', 'Bullish after recurring-operating catalyst']} rows={Object.keys(d.strategy_variant_outcomes).map((k) => {
            const v = d.strategy_variant_outcomes[k];
            return [`${k} ${v.version} ${String(v.definition_hash).slice(0, 10)}`, `${fmtR(v.bullish.all.mean)} (${fmtInterval(v.bullish.all.ci95)}), n=${fmtInt(v.bullish.all.n)}`,
              `${fmtR(v.bearish.all.mean)} (${fmtInterval(v.bearish.all.ci95)}), n=${fmtInt(v.bearish.all.n)}`, `${fmtR(v.bullish_after_recurring_operating.mean)} (${fmtInterval(v.bullish_after_recurring_operating.ci95)}), n=${fmtInt(v.bullish_after_recurring_operating.n)}`];
          })} />
        </>
      )}
      {d.breakout_outcome_table && (
        <p className="text-xs text-slate-500">
          Breakout-level ML-ready table: {fmtInt(d.breakout_outcome_table.rows)} signals with the catalyst class, relative volume, regime and the realised net R under each registered variant, split {Object.keys(d.breakout_outcome_table.split_counts).map((k) => `${k} ${fmtInt(d.breakout_outcome_table.split_counts[k])}`).join(' · ')}.
          {' '}<span className="font-mono">{d.breakout_outcome_table.file}</span>; csv sha256 <span className="font-mono">{String(d.breakout_outcome_table.sha256_of_csv).slice(0, 16)}…</span>
        </p>
      )}
      <h3 className="text-sm font-semibold text-slate-700">Evidence: the most recent flagged filings</h3>
      <Table head={['Symbol', 'Form', 'Release accepted (UTC)', 'Timing', 'Class', 'Revenue YoY', 'Op. margin change', 'Reaction', 'SEC filing']} rows={d.evidence_events_sample.map((e: any) => [
        e.symbol, e.form, String(e.decision_accepted_at).replace('T', ' ').slice(0, 16), e.timing, NATURE_LABEL[e.nature] ?? e.nature, fmtPct(e.revenue_yoy, 0), fmtPct(e.operating_margin_change, 1), fmtPct(e.ar_react),
        <a key={e.period_accession} className="text-cyan-700 underline" href={e.source_url} target="_blank" rel="noreferrer noopener">{e.release_accession ?? e.period_accession}</a>])} />
    </div>
  );
}

const vlabel = (key: string) => key.replace('donchian_breakout__exit_', '').replace(/_/g, ' ');

function reasonMix(r: Record<string, number> | undefined): string {
  if (!r) return '—';
  const total = Object.values(r).reduce((a, b) => a + b, 0) || 1;
  return Object.keys(r).sort((a, b) => r[b] - r[a]).map((k) => `${k} ${Math.round((r[k] / total) * 100)}%`).join(' · ');
}

function StrategyExperiments({ d }: { d: any }) {
  const keys: string[] = d.variants.map((v: any) => v.key);
  const caps: number[] = d.design.hold_caps;
  const reg: number = d.design.registered_hold_cap;
  const cell = (k: string, cap: number, part: string, basis: 'std' | 'actual' = 'std') => d.position_accounting_primary_cost[k][String(cap)][`${part}|all`][basis] ?? {};
  const rv = d.paper_tracker_replay_validation;
  const sel = d.strategy_selection_baselines_cap60_std_net;
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {([
          ['Analysis signals', fmtInt(d.design.n_analysis_signals)],
          ['Registered variants verified', `${d.variants.filter((v: any) => v.registry_verified).length} of ${d.variants.length}`],
          ['Train / test split', `${d.design.train_end} / ${d.design.test_start}`],
          ['Manifest hash', String(d.manifest.manifest_hash).slice(0, 12)],
        ] as const).map(([label, value]) => (
          <div key={label} className="rounded-md border border-slate-200 p-2">
            <div className="text-[11px] text-slate-500">{label}</div>
            <div className="text-base font-semibold text-slate-800">{value}</div>
          </div>
        ))}
      </div>
      <Table head={['Registered variant (immutable identity)', 'Version', 'Definition hash', 'Exit policy']} rows={d.variants.map((v: any) => [vlabel(v.key), v.version, <span key="h" className="font-mono text-xs">{String(v.definition_hash).slice(0, 16)}…</span>, v.policy.replace(/_/g, ' ')])} />

      <h3 className="text-sm font-semibold text-slate-700">Out of sample (test period), registered {reg}-bar cap, position-level, net of {d.design.primary_cost_bps} bps, standardised R (1R = 2 x ATR)</h3>
      <Table head={['Variant', 'Positions', 'Net expectancy', '95% interval', 'Win rate', 'Profit factor', 'Avg bars', 'Avg MFE / MAE', 'Max drawdown (cum R)', 'Exits']} rows={keys.map((k) => {
        const c = cell(k, reg, 'test');
        return [vlabel(k), fmtInt(c.n_positions), fmtR(c.expectancy_r), <Sig key="c" ci={c.expectancy_ci95} />, fmtPct(c.win_rate, 0), String(c.profit_factor ?? '—'), String(c.avg_duration_bars ?? '—'),
          `${fmtR(c.avg_mfe_r, 2)} / ${fmtR(c.avg_mae_r, 2)}`, String(c.max_drawdown_r_cumulative ?? '—'), reasonMix(c.exit_reasons)];
      })} />
      <Table head={['Variant', 'Train expectancy', 'Test expectancy', 'Test bullish', 'Test bearish', ...COSTS_LABEL(d)]} rows={keys.map((k) => [vlabel(k), fmtR(cell(k, reg, 'train').expectancy_r), fmtR(cell(k, reg, 'test').expectancy_r),
        fmtR(d.position_accounting_primary_cost[k][String(reg)]['test|bullish'].std.expectancy_r), fmtR(d.position_accounting_primary_cost[k][String(reg)]['test|bearish'].std.expectancy_r),
        ...d.design.costs_bps.map((b: number) => fmtR(d.cost_sensitivity_test[k][String(reg)][`${Math.trunc(b)}bps`].std_expectancy))])} />

      <h3 className="text-sm font-semibold text-slate-700">Longer holding periods (test period; the registered definition is {reg} bars, longer caps are research conditions)</h3>
      <Table head={['Variant', ...caps.map((c) => `Expectancy, ${c}-bar cap`), ...caps.slice(1).map((c) => `Paired ${c} minus ${caps[0]} (95% interval)`), `Avg bars at ${caps[caps.length - 1]}`]} rows={keys.map((k) => [
        vlabel(k), ...caps.map((c) => fmtR(cell(k, c, 'test').expectancy_r)),
        ...caps.slice(1).map((c) => { const p = d.longer_hold_paired_effect[k][`cap${c}_minus_cap${caps[0]}_std_net_r`]['test|all']; return `${fmtR(p.mean)} (${fmtInterval(p.ci95)})`; }),
        String(cell(k, caps[caps.length - 1], 'test').avg_duration_bars ?? '—')])} />

      <h3 className="text-sm font-semibold text-slate-700">Actual initial risk vs the standardised 2 x ATR unit ({reg}-bar cap, all positions)</h3>
      <Table head={['Variant', 'Median initial risk (ATR)', 'Under 0.5 ATR', 'Over 3 ATR', 'Expectancy, standardised R', 'Expectancy, actual R', 'Actual R, winsorised', 'Worst loss, std R', 'Worst loss, actual R', 'Invalid risk']} rows={keys.map((k) => {
        const r = d.risk_actual_vs_standardised_cap60[k];
        return [vlabel(k), String(r.initial_risk_atr?.median ?? '—'), fmtPct(r.initial_risk_atr?.share_below_0_5_atr, 1), fmtPct(r.initial_risk_atr?.share_above_3_atr, 1), fmtR(r.std_expectancy), fmtR(r.actual_expectancy),
          fmtR(r.actual_winsorised_mean), String(r.std_max_loss_r), String(r.actual_max_loss_r), fmtInt(r.n_invalid_actual_risk)];
      })} />

      <h3 className="text-sm font-semibold text-slate-700">Strategy-selection baselines (fitted on train only, judged on test, {reg}-bar cap, net standardised R per signal)</h3>
      <Table head={['Grouping', 'Best single variant (picked on train)', 'Best single, test', 'Best variant per group, test', 'Per-signal oracle (not attainable)', 'Group rule minus best single (95% interval)']} rows={Object.keys(sel.rules).map((g) => {
        const r = sel.rules[g];
        return [g.replace(/_/g, ' '), vlabel(String(r.best_single_variant_on_train ?? '—')), fmtR(r.rules_test_mean?.best_single_variant), fmtR(r.rules_test_mean?.best_variant_per_group), fmtR(r.rules_test_mean?.per_signal_oracle_UPPER_BOUND),
          r.group_rule_minus_best_single_ci95 ? `${fmtR(r.group_rule_minus_best_single_ci95.mean)} (${fmtInterval(r.group_rule_minus_best_single_ci95.ci95)})` : '—'];
      })} />

      <div className="rounded-md border border-slate-200 p-3 text-sm text-slate-700">
        <div className="font-semibold text-slate-800">Forward paper tracker: replay validation (not activated)</div>
        <p className="mt-1">
          Real history replayed day by day through the tracker ({fmtInt(rv.symbols)} symbols, {fmtInt(rv.sessions_replayed)} sessions, {fmtInt(rv.signals_registered)} signals, {rv.variants} variants in parallel): {fmtInt(rv.batch_positions_compared)} batch
          positions compared field by field, <b>{rv.mismatches} mismatches</b>, {rv.tracker_extra_closed} unexpected closes. The tracker is a pure state machine: it opens no trade, writes nothing and changes no signal.
          No forward paper observation exists in production; activation needs separate approval.
        </p>
      </div>
      {d.big_winner_intelligence && <BigWinners b={d.big_winner_intelligence} reg={reg} />}
      {d.forward_tracker_shadow && <TrackerShadow t={d.forward_tracker_shadow} />}
      {d.ml_ready_table && (
        <p className="text-xs text-slate-500">
          ML-ready strategy-selection table: {fmtInt(d.ml_ready_table.rows)} signals with point-in-time features, the catalyst class with its SEC acceptance timestamp, and realised net R under every variant at {reg} and {caps[caps.length - 1]} bars on both risk bases.
          {' '}<span className="font-mono">{d.ml_ready_table.file}</span>; csv sha256 <span className="font-mono">{String(d.ml_ready_table.sha256_of_csv).slice(0, 16)}…</span>
        </p>
      )}
    </div>
  );
}

const share = (x: number | null | undefined, digits = 1): string => (x === null || x === undefined || Number.isNaN(x) ? '—' : `${(x * 100).toFixed(digits)}%`);

function BigWinners({ b, reg }: { b: any; reg: number }) {
  const od = b.opportunity_distribution;
  const cap = b.capture_by_variant_cap60;
  const lifts = b.decision_time_lift;
  const m = b.model_vs_baselines;
  const bestBase = Math.max(...Object.keys(m.baselines).map((k) => m.baselines[k].auc));
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-slate-700">Big-winner intelligence (hindsight measurements, kept apart from what was known at the decision)</h3>
      <p className="text-xs text-slate-500">
        A big winner is a signal whose favourable excursion reached at least 5R (R = 2 x ATR) within 120 bars of the entry: {share(b.big_winner_rate)} of {fmtInt(b.n_signals)} signals. It is a peak that nobody can sell at, used only as a label.
        Everything below the opportunity table that conditions on the signal bar uses decision-time information only.
      </p>
      <Table head={['Signals', 'Horizon (bars)', 'n', 'Median excursion (R)', 'Mean (R)', '90th pct', '99th pct', 'Top 5% share of the total']} rows={(['all', 'bullish', 'bearish'] as const).flatMap((g) => ['60', '120', '250'].map((h) => {
        const x = od[g].by_horizon[h];
        return [g, h, fmtInt(x.n), fmtR(x.median, 2), fmtR(x.mean, 2), fmtR(x.p90, 2), fmtR(x.p99, 2), share(x.top_5pct_share_of_positive)];
      }))} />
      <h3 className="text-sm font-semibold text-slate-700">How much of a big winner each registered exit captures ({reg}-bar cap, all positions, hindsight)</h3>
      <Table head={['Variant', 'Positions', 'Big-winner positions', 'Median capture of the favourable move', 'Mean realised R on them', 'Mean MFE on them (R)', 'Premature exit, big winners', 'Premature exit, all', 'Missed continuation, all (R)', 'Realised R, 99th pct']} rows={Object.keys(cap).map((k) => {
        const c = cap[k];
        return [vlabel(k), fmtInt(c.positions), fmtInt(c.big_winner_positions), share(c.big_winner_capture_ratio?.median, 0), fmtR(c.big_winner_mean_realized_r, 2), fmtR(c.big_winner_mean_mfe_r, 2),
          share(c.big_winner_premature_exit_share, 0), share(c.all_premature_exit_share, 0), fmtR(c.all_mean_missed_continuation_r, 2), fmtR(c.realized_r_tail?.p99, 2)];
      })} />
      <h3 className="text-sm font-semibold text-slate-700">Decision-time conditions and the big-winner rate (test period; base rate {share(m.base_rate_test)})</h3>
      <Table head={['Condition known at the signal close', 'Bucket', 'n', 'Big-winner rate', '95% interval', 'Lift vs base rate']} rows={Object.keys(lifts).flatMap((name) => {
        const bk = lifts[name].test.buckets;
        return Object.keys(bk).map((k) => [name.replace(/_/g, ' '), k.replace(/_/g, ' '), fmtInt(bk[k].n), share(bk[k].rate), bk[k].rate_ci95 ? `${share(bk[k].rate_ci95[0])} to ${share(bk[k].rate_ci95[1])}` : '—', bk[k].lift.toFixed(2)]);
      })} />
      <p className="text-sm text-slate-700">
        A gradient-boosted model on decision-time features only (chronological split, train labels overlapping the test period purged): test AUC <b>{m.model.auc.toFixed(3)}</b> against {bestBase.toFixed(3)} for the best single-feature baseline;
        top-decile precision {share(m.model.precision, 0)} against the {share(m.base_rate_test, 0)} base rate (lift {m.model.lift.toFixed(2)}). Direction and the index regime carry much of the signal, so part of this is market direction in a rising market.
        This ranks which signals later became big winners; it says nothing about profit after exits and costs, and no strategy here is labelled profitable.
      </p>
    </div>
  );
}

function TrackerShadow({ t }: { t: any }) {
  const r = t.real_source;
  const h = t.historical_replay;
  const yn = (x: boolean) => (x ? 'yes' : 'NO');
  return (
    <div className="space-y-2 rounded-md border border-slate-200 p-3 text-sm text-slate-700">
      <div className="font-semibold text-slate-800">Forward paper tracker: dry-run and shadow tests on a scratch restore{t.activated_in_production ? '' : ' (not activated in production)'}</div>
      <Table head={['Test', 'Scope', 'Result']} rows={[
        ...(r ? [['Real forward capture', `session ${r.session}: ${fmtInt(r.candidates)} candidates x ${r.variants} variants`,
          `dry run wrote nothing: ${yn(r.dry_run_wrote_nothing)}; write equals dry run: ${yn(r.write_matches_dry_run)}; ${fmtInt(r.observations_written)} observations; snapshot hash verified: ${yn(r.snapshot_hash_verified)}; re-run is a no-op: ${yn(r.rerun_is_noop)}; audit problems: ${r.audit_problems}`]] : []),
        ...(h ? [['Historical replay, session by session', `${fmtInt(h.design.sessions)} sessions, ${fmtInt(h.design.symbols)} symbols, ${fmtInt(h.positions)} positions (${fmtInt(h.closed)} closed), ${fmtInt(h.hindsight_events)} hindsight events`,
          `${h.mismatches_vs_batch} mismatches against the batch experiment; dry run equals write: ${yn(h.dry_run_equals_write)}; re-runs change nothing: ${yn(h.rerun_is_noop)}; audit problems: ${h.audit_problems}; ${h.seconds_per_session.mean} s per session on average`]] : []),
      ]} />
      {r && <p className="text-xs text-slate-500">Context available on the one real session: sector {fmtInt(r.context_availability.sector_known)} of {fmtInt(r.context_availability.signals)}, market regime {fmtInt(r.context_availability.market_regime_available)}, catalysts attached {fmtInt(r.context_availability.catalysts_with_events)}: absent context is recorded as absent, never filled in.</p>}
    </div>
  );
}

const COSTS_LABEL = (d: any): string[] => d.design.costs_bps.map((b: number) => `Test, ${Math.trunc(b)} bps`);

function Radar({ d }: { d: any }) {
  return (
    <Table head={['Symbol', 'Report date', 'EPS est.', 'EPS actual', 'Surprise', 'Reaction (mkt-adj.)', 'Since (mkt-adj.)', 'Verification']} rows={d.events.map((e: any) => [
      e.symbol, e.report_date, String(e.eps_estimate), String(e.eps_actual), `${e.surprise_pct}%`, `${e.abnormal_reaction_pct}%`, `${e.abnormal_return_to_latest_close_pct}%`, e.verification])} />
  );
}

const STATUS_STYLE: Record<string, string> = {
  supported: 'bg-emerald-100 text-emerald-800',
  suggestive: 'bg-amber-100 text-amber-800',
  inconclusive: 'bg-slate-100 text-slate-700',
  not_supported: 'bg-rose-100 text-rose-800',
};

function Insights({ d }: { d: any }) {
  return (
    <ul className="space-y-2">
      {d.claims.map((c: any) => (
        <li key={c.claim} className="rounded-md border border-slate-200 p-3">
          <div className="flex items-start gap-2">
            <span className={`shrink-0 rounded px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${STATUS_STYLE[c.status] ?? STATUS_STYLE.inconclusive}`}>{String(c.status).replace('_', ' ')}</span>
            <span className="text-sm font-medium text-slate-800">{c.claim}</span>
          </div>
          <p className="mt-1 text-xs text-slate-600">{c.evidence}</p>
          <p className="mt-0.5 text-[11px] text-slate-400">Evidence: {c.reports.join(', ')}</p>
        </li>
      ))}
    </ul>
  );
}

const POLICY_LABEL: Record<string, string> = { A_atr_stop_fixed_3R: 'Fixed 3R exit', B_donchian_trail_10: '10-bar Donchian trail' };

function Attribution({ d }: { d: any }) {
  return (
    <div className="space-y-5">
      {Object.keys(d).map((p) => (
        <div key={p} className="space-y-3">
          <h3 className="text-sm font-semibold text-slate-700">{POLICY_LABEL[p] ?? p}</h3>
          <Table head={['Year', 'Bullish mean R', 'Bearish mean R', 'Trades']} rows={Object.keys(d[p].by_year).map((y) => [y, fmtR(d[p].by_year[y].bullish), fmtR(d[p].by_year[y].bearish), fmtInt(d[p].by_year[y].n)])} />
          <Table head={['Regime', 'Bullish n', 'Bullish mean R', 'Bearish n', 'Bearish mean R']} rows={(['index_uptrend', 'index_downtrend', 'breadth50_ge_0.5', 'breadth50_lt_0.5'] as const).map((k) => [
            k.replace(/_/g, ' '), fmtInt(d[p].by_regime.bullish[k].n), fmtR(d[p].by_regime.bullish[k].mean_r), fmtInt(d[p].by_regime.bearish[k].n), fmtR(d[p].by_regime.bearish[k].mean_r)])} />
          <Table head={['Sector', 'Trades', 'Mean R']} rows={Object.keys(d[p].by_sector).map((s) => [s.replace(/_/g, ' '), fmtInt(d[p].by_sector[s].n), fmtR(d[p].by_sector[s].mean_r)])} />
        </div>
      ))}
    </div>
  );
}

const VERDICT_STYLE: Record<string, string> = {
  PASS_FOR_FORWARD_SHADOW: 'bg-emerald-100 text-emerald-800',
  NO_EDGE_DEMONSTRATED: 'bg-slate-200 text-slate-700',
  INSUFFICIENT_DATA: 'bg-amber-100 text-amber-800',
};
const VERDICT_LABEL: Record<string, string> = {
  PASS_FOR_FORWARD_SHADOW: 'Pass: forward shadow only',
  NO_EDGE_DEMONSTRATED: 'No edge demonstrated',
  INSUFFICIENT_DATA: 'Insufficient data',
};
const STEP_STYLE: Record<string, string> = { ready: 'bg-emerald-100 text-emerald-800', proposed: 'bg-cyan-100 text-cyan-800', 'blocked on data': 'bg-amber-100 text-amber-800' };

function Delta({ label, v }: { label: string; v: any }) {
  return (
    <div className="rounded-lg bg-slate-50 px-2.5 py-2">
      <p className="text-[11px] text-slate-500">{label}</p>
      <p className="text-sm font-semibold tabular-nums text-slate-800">{v && v.diff != null ? fmtR(v.diff) : '—'}</p>
      <p className="text-[11px] tabular-nums"><Sig ci={v?.ci95} /></p>
    </div>
  );
}

function StrategyLabLoop({ d }: { d: any }) {
  const a = d.analysis;
  return (
    <div className="space-y-5">
      <div className="space-y-3">
        {d.experiments.map((e: any) => (
          <div key={`${e.key}-${e.version}`} className="rounded-lg border border-slate-200 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold text-slate-800">{e.key} {e.version}</span>
              <span className={`rounded px-2 py-0.5 text-[11px] font-semibold ${VERDICT_STYLE[e.verdict] ?? VERDICT_STYLE.NO_EDGE_DEMONSTRATED}`}>{VERDICT_LABEL[e.verdict] ?? e.verdict}</span>
              <span className="text-[11px] text-slate-500">{e.parent ? `refines ${e.parent}` : 'parent strategy'} · {e.derived_from ?? 'registered by hand'}</span>
            </div>
            <p className="mt-1 text-xs text-slate-600">{e.hypothesis}</p>
            <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <div className="rounded-lg bg-slate-50 px-2.5 py-2">
                <p className="text-[11px] text-slate-500">Test net R ({fmtInt(e.n_test)} positions)</p>
                <p className="text-sm font-semibold tabular-nums text-slate-800">{fmtR(e.test_expectancy_r)}</p>
                <p className="text-[11px] tabular-nums"><Sig ci={e.test_ci95} /></p>
              </div>
              <Delta label="vs random date" v={e.vs_random} />
              <Delta label="vs all reports" v={e.vs_all_reports} />
              {e.vs_parent ? <Delta label="vs parent" v={e.vs_parent} /> : (
                <div className="rounded-lg bg-slate-50 px-2.5 py-2">
                  <p className="text-[11px] text-slate-500">Train net R ({fmtInt(e.n_train)})</p>
                  <p className="text-sm font-semibold tabular-nums text-slate-800">{fmtR(e.train_expectancy_r)}</p>
                </div>
              )}
            </div>
            <details className="mt-2">
              <summary className="cursor-pointer text-xs text-slate-500">Decision checks (declared before the run)</summary>
              <ul className="mt-1 space-y-0.5">
                {e.checks.map((c: any) => (
                  <li key={c.check} className="text-xs text-slate-700"><span className={c.ok ? 'text-emerald-700 font-semibold' : 'text-red-700 font-semibold'}>{c.ok ? 'PASS' : 'FAIL'}</span> {c.check}</li>
                ))}
              </ul>
              <p className="mt-1 text-[11px] text-slate-400">spec {String(e.spec_hash).slice(0, 12)} · run {String(e.run_hash).slice(0, 12)} · report {String(e.report_sha256).slice(0, 12)}</p>
            </details>
          </div>
        ))}
      </div>

      <div>
        <h3 className="text-sm font-semibold text-slate-700">Why the parent failed (train period only)</h3>
        <ul className="mt-1 list-disc ml-5 space-y-0.5">
          {a.failure_patterns.length === 0 ? <li className="text-xs text-slate-500">No pattern found.</li> : a.failure_patterns.map((p: any) => (
            <li key={p.pattern} className="text-xs text-slate-700"><span className="font-medium">{p.pattern}</span>: {p.evidence}</li>
          ))}
        </ul>
      </div>

      <div>
        <h3 className="text-sm font-semibold text-slate-700">Subgroups looked at ({a.candidates_tested} candidates, intervals widened for that; {a.n_passing} passed)</h3>
        <Table head={['Candidate', 'n', 'Mean net R', 'Adjusted interval', 'Halves']} rows={a.subgroups.slice(0, 8).map((s: any) => [
          s.name.replace(/_/g, ' '), fmtInt(s.n), fmtR(s.mean_r), <Sig key="c" ci={s.adjusted_ci} />, `${fmtR(s.mean_first_half, 2)} / ${fmtR(s.mean_second_half, 2)}`])} />
      </div>

      {d.derived_hypothesis && (
        <div className="rounded-lg border border-cyan-200 bg-cyan-50 p-3">
          <p className="text-xs font-semibold text-cyan-900">Next hypothesis registered by the loop ({String(d.derived_hypothesis.selection_strength).replace('_', ' ')})</p>
          <p className="mt-1 text-xs text-cyan-900">{d.derived_hypothesis.statement}</p>
        </div>
      )}

      <div>
        <h3 className="text-sm font-semibold text-slate-700">What to do next</h3>
        <ul className="mt-1 space-y-1.5">
          {d.next_steps.map((n: any) => (
            <li key={n.statement} className="flex items-start gap-2">
              <span className={`shrink-0 rounded px-2 py-0.5 text-[11px] font-semibold ${STEP_STYLE[n.status] ?? STEP_STYLE.proposed}`}>{n.status}</span>
              <span className="text-xs text-slate-700">{n.statement}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function InflectionResearch({ d }: { d: any }) {
  const cell = (k: string) => d.cells?.[k] ?? {};
  const H = d.horizons as number[];
  const rows: React.ReactNode[][] = [];
  for (const h of H) {
    for (const period of ['train', 'validation', 'test']) {
      const c = cell(`union|${period}|h${h}`);
      rows.push([`${h}`, period, c.n ? fmtInt(c.n) : '0', c.n ? fmtPct(c.mean, 1) : 'not testable', c.n ? <Sig key={`${h}${period}`} ci={c.ci95} /> : '—', c.n ? `${fmtInt(c.blocks)} weeks` : '—']);
    }
  }
  const base = [['union', 'Inflection events'], ['earnings_surprise', 'Earnings surprise ≥ +10%'], ['price_momentum', 'Price momentum (top decile)'], ['relative_strength', 'Relative strength (top quintile)']];
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded px-2 py-0.5 text-[11px] font-semibold ${VERDICT_STYLE[d.verdict] ?? VERDICT_STYLE.NO_EDGE_DEMONSTRATED}`}>{VERDICT_LABEL[d.verdict] ?? d.verdict}</span>
        <span className="text-[11px] text-slate-500">research only · a pass would authorise a forward shadow run, not trading</span>
      </div>
      <p className="text-xs text-slate-600">{d.hypothesis}</p>
      <div>
        <h3 className="text-sm font-semibold text-slate-700">Decision checks (primary cell: all events, 20 sessions, test period)</h3>
        <ul className="mt-1 space-y-0.5">
          {d.checks.map((c: any) => (
            <li key={c.check} className="text-xs text-slate-700"><span className={c.ok ? 'text-emerald-700 font-semibold' : 'text-red-700 font-semibold'}>{c.ok ? 'PASS' : 'FAIL'}</span> {c.check}</li>
          ))}
        </ul>
      </div>
      <div>
        <h3 className="text-sm font-semibold text-slate-700">Net sector-adjusted excess return by horizon</h3>
        <Table head={['Sessions', 'Period', 'Events', 'Mean', '95% interval', 'Independent blocks']} rows={rows} />
      </div>
      <div>
        <h3 className="text-sm font-semibold text-slate-700">Against simple baselines (test period)</h3>
        <Table head={['Group', '20 sessions', '60 sessions']} rows={base.map(([g, label]) => {
          const a = cell(`${g}|test|h20`), b = cell(`${g}|test|h60`);
          return [label, a.n ? `${fmtPct(a.mean, 1)} (n ${fmtInt(a.n)})` : '—', b.n ? `${fmtPct(b.mean, 1)} (n ${fmtInt(b.n)})` : '—'];
        })} />
      </div>
      <div>
        <h3 className="text-sm font-semibold text-slate-700">What the data can show yet</h3>
        <ul className="mt-1 list-disc ml-5 space-y-0.5">
          {H.map((h) => (
            <li key={h} className="text-xs text-slate-700">{h} sessions: {fmtInt(d.feasibility?.[String(h)]?.events_with_complete_window)} company-quarters have a complete window.</li>
          ))}
        </ul>
      </div>
      <div className="rounded-lg border border-cyan-200 bg-cyan-50 p-3">
        <p className="text-xs font-semibold text-cyan-900">Follow-up hypotheses registered (forward evaluation only)</p>
        <ul className="mt-1 space-y-1">
          {d.followups.map((f: any) => (
            <li key={f.label} className="text-xs text-cyan-900"><span className="font-medium">{f.label}</span>: {fmtInt(f.n_company_quarters)} company-quarters so far (train {fmtInt(f.n_by_period?.train)}, validation {fmtInt(f.n_by_period?.validation)}, test {fmtInt(f.n_by_period?.test)}).</li>
          ))}
          {(d.horizon_followups ?? []).map((f: any) => <li key={f.cell} className="text-xs text-cyan-900"><span className="font-medium">Horizon lead</span>: {f.statement}</li>)}
        </ul>
      </div>
    </div>
  );
}

export default function LabReportView({ report }: { report: LabReport }) {
  const body = (() => {
    switch (report.id) {
      case 'research_insights': return <Insights d={report.data} />;
      case 'catalyst_sec_earnings': return <SecEarnings d={report.data} />;
      case 'strategy_experiments': return <StrategyExperiments d={report.data} />;
      case 'strategy_lab_loop': return <StrategyLabLoop d={report.data} />;
      case 'fundamental_inflection_research': return <InflectionResearch d={report.data} />;
      case 'performance_attribution': return <Attribution d={report.data} />;
      case 'strategy_baseline': return <StrategyBaseline d={report.data} />;
      case 'ranker_stability': return <RankerStability d={report.data} />;
      case 'ml_evaluation': return <MlEvaluation d={report.data} />;
      case 'exit_policies': return <ExitPolicies d={report.data} />;
      case 'catalyst_earnings': return <CatalystEarnings d={report.data} />;
      case 'catalyst_radar_recent': return <Radar d={report.data} />;
      default: return <p className="text-sm text-slate-500">No dedicated view for this report yet; the raw data is available from the API.</p>;
    }
  })();
  return (
    <div className="space-y-3">
      <p className="text-slate-800 text-sm">{report.headline}</p>
      {body}
      <div className="rounded-md bg-amber-50 border border-amber-200 p-3">
        <p className="text-xs font-semibold text-amber-900">Read this before using the numbers</p>
        <ul className="list-disc ml-5 text-xs text-amber-900 mt-1 space-y-0.5">{report.caveats.map((c) => <li key={c}>{c}</li>)}</ul>
      </div>
      <p className="text-xs text-slate-400">Source: {report.provenance.source} · generated {report.generated_at} · code {report.code_ref}</p>
    </div>
  );
}
