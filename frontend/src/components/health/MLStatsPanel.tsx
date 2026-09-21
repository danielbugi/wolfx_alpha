// File: frontend/src/components/health/MLStatsPanel.tsx
'use client';

import React from 'react';
import { Card, CardBody, CardHeader, Chip, Divider } from '@nextui-org/react';
import { MLStatsReport, ModelEvaluation, ModelHistoryEntry } from '@/services/api';

function daysBadgeColor(days: number | undefined): string {
  if (days == null) return 'bg-slate-100 text-slate-600';
  if (days > 90) return 'bg-red-100 text-red-700';
  if (days > 30) return 'bg-amber-100 text-amber-800';
  return 'bg-emerald-100 text-emerald-800';
}

function StatTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-slate-50 rounded-lg p-3 text-center">
      <p className="text-lg font-bold text-slate-800">{value}</p>
      <p className="text-[11px] text-slate-500">{label}</p>
      {sub && <p className="text-[10px] text-slate-400 mt-0.5">{sub}</p>}
    </div>
  );
}

function MetricPill({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <div className="flex items-center justify-between text-xs py-1.5 border-b border-slate-100 last:border-0">
      <span className="text-slate-500">{label}</span>
      <span className="font-semibold text-slate-800">
        {value != null ? value.toFixed(3) : <span className="text-slate-400 font-normal">not recorded</span>}
      </span>
    </div>
  );
}

function FeatureBar({ name, value, maxValue }: { name: string; value: number; maxValue: number }) {
  const pct = maxValue > 0 ? (value / maxValue) * 100 : 0;
  return (
    <div className="flex items-center gap-2 py-1">
      <span className="w-40 shrink-0 text-xs text-slate-600 truncate" title={name}>{name}</span>
      <div className="flex-1 h-2 rounded-full bg-slate-100 overflow-hidden">
        <div className="h-full rounded-full bg-indigo-500" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-14 shrink-0 text-xs text-right text-slate-500 tabular-nums">{value.toFixed(3)}</span>
    </div>
  );
}

/** Model accuracy, feature importance, training data composition, live
 * prediction track record, and retraining cadence. Shared between the
 * standalone /ml-stats page and the "ML Health & Accuracy" view on
 * /system-health so the two never drift into two different renderings of
 * the same report. */
export default function MLStatsPanel({ report }: { report: MLStatsReport }) {
  const model = report.current_model;
  const fi = report.feature_importance;
  const dataset = report.training_dataset;
  const preds = report.prediction_track_record;
  const history = report.model_history ?? [];
  const evaluations = report.evaluations ?? [];
  const firstServedVersion = history.find((h) => h.served)?.version;
  const maxImportance = fi?.features?.[0]?.importance ?? 1;

  return (
    <div className="space-y-4">
      {/* Current model */}
      <Card className="shadow-sm border border-slate-200">
        <CardHeader className="flex items-center justify-between p-4 pb-2">
          <div>
            <h3 className="text-sm font-semibold text-slate-800">Current Model</h3>
            <p className="text-xs text-slate-500 mt-0.5 font-mono">{model?.found ? model.version : 'none served'}</p>
          </div>
          {model?.found && (
            <Chip size="sm" className={`text-xs font-semibold ${daysBadgeColor(model.days_since_trained)}`}>
              {model.days_since_trained} days since trained
            </Chip>
          )}
        </CardHeader>
        <Divider className="bg-slate-100" />
        <CardBody className="p-4 pt-3">
          {!model?.found ? (
            <p className="text-sm text-amber-700">{model?.reason ?? 'No trained model file found.'}</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="grid grid-cols-3 gap-2">
                <StatTile label="Features" value={String(model.feature_count ?? '—')} />
                <StatTile label="Training samples" value={model.training_samples ? model.training_samples.toLocaleString() : '—'} />
                <StatTile
                  label="Registered"
                  value={model.registered ? 'Yes' : 'No'}
                  sub={!model.registered ? 'trained before tracking was added' : undefined}
                />
              </div>
              <div>
                <MetricPill label="Accuracy" value={model.accuracy} />
                <MetricPill label="Precision" value={model.precision} />
                <MetricPill label="Recall" value={model.recall} />
                <MetricPill label="F1 score" value={model.f1} />
                <MetricPill label="Holdout AUC (untouched, chronological)" value={model.holdout_auc ?? model.auc} />
                {model.holdout_auc_ci95 && (
                  <p className="text-[11px] text-slate-400 mt-1">
                    95% CI {model.holdout_auc_ci95[0].toFixed(3)}–{model.holdout_auc_ci95[1].toFixed(3)}
                    {model.holdout_base_rate != null ? ` · base rate ${(model.holdout_base_rate * 100).toFixed(1)}%` : ''}
                  </p>
                )}
              </div>
            </div>
          )}
        </CardBody>
      </Card>

      {/* Latest honest evaluation */}
      {evaluations.length > 0 && (
        <Card className="shadow-sm border border-slate-200">
          <CardHeader className="p-4 pb-2">
            <div>
              <h3 className="text-sm font-semibold text-slate-800">Latest honest evaluation</h3>
              <p className="text-xs text-slate-500 mt-0.5">
                Trained on the past, scored on an untouched later period (30-day embargo). A model is served only if every gate check passes.
              </p>
            </div>
          </CardHeader>
          <Divider className="bg-slate-100" />
          <CardBody className="p-4 pt-3 space-y-4">
            {evaluations.map((e: ModelEvaluation) => (
              <div key={e.target} className="rounded-lg border border-slate-100 p-3">
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div>
                    <p className="text-xs font-semibold text-slate-700">{e.target_definition}</p>
                    <p className="text-[11px] text-slate-400">
                      holdout {e.holdout_range?.[0]} → {e.holdout_range?.[1]} · n={e.n_holdout?.toLocaleString()} · base rate {e.base_rate != null ? `${(e.base_rate * 100).toFixed(1)}%` : '—'}
                      {e.excluded_features.length > 0 ? ` · without ${e.excluded_features.join(', ')}` : ''}
                    </p>
                  </div>
                  <Chip size="sm" className={`text-[10px] shrink-0 ${e.promoted ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}>
                    {e.promoted ? 'promoted' : 'not promoted'}
                  </Chip>
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-2">
                  <StatTile label="Holdout AUC" value={e.auc != null ? e.auc.toFixed(3) : '—'}
                    sub={e.auc_ci95 ? `CI ${e.auc_ci95[0].toFixed(3)}–${e.auc_ci95[1].toFixed(3)}` : undefined} />
                  <StatTile label="Direction-only baseline" value={e.direction_baseline_auc != null ? e.direction_baseline_auc.toFixed(3) : '—'} />
                  <StatTile label="Score → plan profit (bull / bear)"
                    value={e.auc_vs_plan_profit_bullish != null && e.auc_vs_plan_profit_bearish != null
                      ? `${e.auc_vs_plan_profit_bullish.toFixed(2)} / ${e.auc_vs_plan_profit_bearish.toFixed(2)}` : '—'}
                    sub="0.50 = no ranking power" />
                  <StatTile label="Top-decile lift" value={e.top_decile_lift != null ? `${e.top_decile_lift.toFixed(2)}×` : '—'} />
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {e.gate.map((g) => (
                    <Chip key={g.check} size="sm" className={`text-[10px] ${g.pass ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'}`}>
                      {g.pass ? '✓' : '✗'} {g.check.replace(/_/g, ' ')} {g.value} (need {g.required})
                    </Chip>
                  ))}
                </div>
              </div>
            ))}
          </CardBody>
        </Card>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Feature importance */}
        <Card className="shadow-sm border border-slate-200">
          <CardHeader className="p-4 pb-2">
            <h3 className="text-sm font-semibold text-slate-800">Feature Importance</h3>
            <p className="text-xs text-slate-500 mt-0.5">What the model actually weighs most</p>
          </CardHeader>
          <Divider className="bg-slate-100" />
          <CardBody className="p-4 pt-3">
            {!fi?.available ? (
              <p className="text-sm text-slate-400">{fi?.reason ?? 'Not available'}</p>
            ) : (
              <div>
                {fi.features?.slice(0, 12).map((f) => (
                  <FeatureBar key={f.feature} name={f.feature} value={f.importance} maxValue={maxImportance} />
                ))}
              </div>
            )}
          </CardBody>
        </Card>

        {/* Training dataset */}
        <Card className="shadow-sm border border-slate-200">
          <CardHeader className="p-4 pb-2">
            <h3 className="text-sm font-semibold text-slate-800">Training Dataset</h3>
            <p className="text-xs text-slate-500 mt-0.5">What the model is learning from</p>
          </CardHeader>
          <Divider className="bg-slate-100" />
          <CardBody className="p-4 pt-3 space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <StatTile label="Training rows" value={dataset?.total_rows?.toLocaleString() ?? '—'} />
              <StatTile label="Unique symbols" value={dataset?.unique_symbols?.toLocaleString() ?? '—'} />
            </div>
            <p className="text-[11px] text-slate-400 text-center">
              {dataset?.earliest_date} → {dataset?.latest_date}
            </p>
            <div>
              <div className="flex justify-between text-[11px] text-slate-500 mb-1">
                <span>High momentum (target)</span>
                <span>{dataset?.positive_class_pct}% / {dataset?.negative_class_pct}%</span>
              </div>
              <div className="h-2 rounded-full overflow-hidden flex">
                <div className="h-full bg-emerald-500" style={{ width: `${dataset?.positive_class_pct ?? 0}%` }} />
                <div className="h-full bg-slate-300" style={{ width: `${dataset?.negative_class_pct ?? 0}%` }} />
              </div>
            </div>
            {dataset?.plan_profitable_pct != null && (
              <p className="text-[11px] text-slate-500 text-center">
                Trade plan (2×ATR stop, 6×ATR target) ends profitable in {dataset.plan_profitable_pct}% of samples
              </p>
            )}
            {dataset?.price_discontinuities != null && (
              <p className="text-[11px] text-slate-400 text-center">
                {dataset.price_discontinuities.toLocaleString()} price discontinuities in {dataset.symbols_with_discontinuities} symbols — samples crossing them are excluded
              </p>
            )}
            <div>
              <div className="flex justify-between text-[11px] text-slate-500 mb-1">
                <span>Bullish / Bearish breakouts</span>
                <span>{dataset?.bullish_pct}% / {dataset?.bearish_pct}%</span>
              </div>
              <div className="h-2 rounded-full overflow-hidden flex">
                <div className="h-full bg-cyan-500" style={{ width: `${dataset?.bullish_pct ?? 0}%` }} />
                <div className="h-full bg-rose-400" style={{ width: `${dataset?.bearish_pct ?? 0}%` }} />
              </div>
            </div>
          </CardBody>
        </Card>
      </div>

      {/* Prediction track record */}
      <Card className="shadow-sm border border-slate-200">
        <CardHeader className="p-4 pb-2">
          <h3 className="text-sm font-semibold text-slate-800">Live Prediction Track Record</h3>
          <p className="text-xs text-slate-500 mt-0.5">Is the model actually right, in production</p>
        </CardHeader>
        <Divider className="bg-slate-100" />
        <CardBody className="p-4 pt-3">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-4">
            <StatTile label="Total predictions" value={preds?.total_predictions?.toLocaleString() ?? '—'} />
            <StatTile label="Latest" value={preds?.latest_prediction_date ?? '—'} />
            <StatTile
              label="Outcomes evaluated"
              value={preds?.outcomes_evaluated?.toLocaleString() ?? '0'}
              sub={!preds?.outcomes_evaluated ? 'never run — see System Health' : undefined}
            />
            <StatTile
              label="Win rate"
              value={preds?.win_rate_pct != null ? `${preds.win_rate_pct}%` : '—'}
              sub={preds?.avg_return_pct != null ? `avg ${preds.avg_return_pct > 0 ? '+' : ''}${preds.avg_return_pct}%` : undefined}
            />
          </div>
          {preds?.confidence_distribution && (
            <div className="grid grid-cols-5 gap-2">
              {preds.confidence_distribution.map((c) => (
                <div key={c.confidence} className="text-center">
                  <div className="h-16 flex items-end justify-center bg-slate-50 rounded">
                    <div
                      className="w-6 rounded-t bg-indigo-500"
                      style={{
                        height: `${preds.total_predictions ? Math.max(4, (c.count / preds.total_predictions) * 64) : 4}px`,
                      }}
                    />
                  </div>
                  <p className="text-[10px] text-slate-500 mt-1 capitalize">{c.confidence.replace('_', ' ')}</p>
                  <p className="text-[10px] font-semibold text-slate-700">{c.count}</p>
                </div>
              ))}
            </div>
          )}
        </CardBody>
      </Card>

      {/* Retraining history */}
      <Card className="shadow-sm border border-slate-200">
        <CardHeader className="p-4 pb-2">
          <h3 className="text-sm font-semibold text-slate-800">Retraining History</h3>
          <p className="text-xs text-slate-500 mt-0.5">Actual cadence — is this happening on a schedule?</p>
        </CardHeader>
        <Divider className="bg-slate-100" />
        <CardBody className="p-4 pt-3">
          {history.length === 0 ? (
            <p className="text-sm text-slate-400">No model files found.</p>
          ) : (
            <div className="space-y-1.5">
              {history.map((h: ModelHistoryEntry) => (
                <div
                  key={h.version}
                  className={`flex items-center justify-between text-xs px-2.5 py-1.5 rounded ${h.version === firstServedVersion ? 'bg-indigo-50' : 'bg-slate-50'}`}
                >
                  <span className="font-mono text-slate-600">{h.version}</span>
                  <div className="flex items-center gap-3">
                    {h.served !== false && h.accuracy != null && <span className="text-slate-500">acc {h.accuracy.toFixed(3)}</span>}
                    {h.served !== false && h.auc != null && <span className="text-slate-500">auc {h.auc.toFixed(3)}</span>}
                    {h.served === false && (
                      <Chip size="sm" className="bg-slate-200 text-slate-600 text-[10px]" title="Trained on an unvalidated pipeline; the screener refuses to load it. Its recorded metrics are not shown.">
                        legacy · not served
                      </Chip>
                    )}
                    {h.version === firstServedVersion && <Chip size="sm" className="bg-indigo-100 text-indigo-700 text-[10px]">live</Chip>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
