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

export default function LabReportView({ report }: { report: LabReport }) {
  const body = (() => {
    switch (report.id) {
      case 'research_insights': return <Insights d={report.data} />;
      case 'catalyst_sec_earnings': return <SecEarnings d={report.data} />;
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
