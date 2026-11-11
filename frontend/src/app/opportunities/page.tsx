'use client';

// Opportunity Intelligence v0 (owner only): companies whose reported business changed, with the independent evidence that supports or
// contradicts the change. Descriptive: no score, no forecast, no advice. docs/architecture/OPPORTUNITY_INTELLIGENCE.md
import React, { useCallback, useEffect, useState } from 'react';
import { clsx } from 'clsx';
import { opportunityApi, OIBrief, OIFact, OIReport } from '@/services/opportunityApi';
import { CLASS_LABEL, DASH, fmtFigure, fmtWhen, provenance, VERDICT_CLASS, VERDICT_LABEL } from '@/lib/opportunityFormat';

const DAYS = [7, 14, 30];

function Chip({ label, verdict }: { label: string; verdict: OIFact['verdict'] }) {
  return (
    <span className={clsx('inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[12px]', VERDICT_CLASS[verdict])}>
      <span className="font-medium">{label}</span>
      <span>· {VERDICT_LABEL[verdict]}</span>
    </span>
  );
}

function FactBlock({ fact }: { fact: OIFact }) {
  return (
    <div className="rounded-lg border border-slate-200 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[14px] font-semibold text-slate-800">{fact.label}</span>
        <Chip label="" verdict={fact.verdict} />
      </div>
      {fact.figures.length > 0 ? (
        <ul className="mt-2 space-y-1.5">
          {fact.figures.map((f) => (
            <li key={f.label} className="text-[13px]">
              <div className="flex justify-between gap-3">
                <span className="text-slate-600">{f.label}</span>
                <span className="font-mono tabular-nums text-slate-900">{fmtFigure(f.value, f.unit, f.label)}</span>
              </div>
              <div className="text-[11px] text-slate-400">{provenance(f)}</div>
            </li>
          ))}
        </ul>
      ) : null}
      {fact.note ? <p className="mt-2 text-[12px] text-slate-500">{fact.note}</p> : null}
    </div>
  );
}

function BriefCard({ b }: { b: OIBrief }) {
  const [open, setOpen] = useState(false);
  const c = b.convergence;
  return (
    <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <button type="button" className="w-full text-left" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <div className="flex items-baseline justify-between gap-2">
          <div>
            <span className="text-[17px] font-bold text-slate-900">{b.symbol}</span>
            <span className="ml-2 text-[13px] text-slate-500">{b.company_name || DASH}</span>
          </div>
          <span className="shrink-0 text-[12px] text-slate-500">public {b.first_public}</span>
        </div>
        <p className="mt-1 text-[13px] text-slate-700">{b.what_changed.map((w) => w.label).join(' · ')}</p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {(['fundamental', 'surprise', 'reaction'] as const).map((k) => (
            <Chip key={k} label={CLASS_LABEL[k]} verdict={c.classes[k]} />
          ))}
        </div>
        <p className="mt-2 text-[12px] text-slate-500">
          {c.summary}. Accounting facts improved: {c.fundamental_supporting.length ? c.fundamental_supporting.join(', ') : 'none'}
          {c.fundamental_contradicting.length ? `; worsened: ${c.fundamental_contradicting.join(', ')}` : ''}.
        </p>
      </button>
      {open ? (
        <div className="mt-3 space-y-3 border-t border-slate-100 pt-3">
          <section>
            <h3 className="text-[13px] font-semibold uppercase tracking-wide text-slate-500">What changed</h3>
            {b.what_changed.map((w) => (
              <div key={w.event_type} className="mt-1.5">
                <p className="text-[14px] font-medium text-slate-800">{w.label}</p>
                {w.lines.map((l) => (
                  <p key={l} className="text-[13px] text-slate-700">{l}</p>
                ))}
                <p className="text-[11px] text-slate-400">{w.source} · {fmtWhen(w.available_at)}</p>
              </div>
            ))}
          </section>
          {b.contradictions.length ? (
            <section>
              <h3 className="text-[13px] font-semibold uppercase tracking-wide text-rose-600">Contradicting evidence and cautions</h3>
              <ul className="mt-1 list-disc pl-5 text-[13px] text-slate-700">
                {b.contradictions.map((x) => (
                  <li key={x.family + x.text}>{x.text}</li>
                ))}
              </ul>
            </section>
          ) : null}
          <section className="grid gap-2 sm:grid-cols-2">
            {b.evidence.fundamental.map((f) => (
              <FactBlock key={f.family} fact={f} />
            ))}
            <FactBlock fact={b.evidence.surprise} />
            <FactBlock fact={b.evidence.reaction} />
          </section>
        </div>
      ) : null}
    </article>
  );
}

export default function OpportunitiesPage() {
  const [days, setDays] = useState(14);
  const [convergingOnly, setConvergingOnly] = useState(true);
  const [report, setReport] = useState<OIReport | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState('loading');
    try {
      setReport(await opportunityApi.briefs({ days, converging_only: convergingOnly, limit: 200 }));
      setState('ready');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'failed');
      setState('error');
    }
  }, [days, convergingOnly]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="mx-auto max-w-4xl space-y-4 px-4 py-4">
      <header>
        <h1 className="text-[22px] font-bold text-slate-900">Opportunity Intelligence</h1>
        <p className="text-[13px] text-slate-500">
          Owner only. Companies whose reported figures changed, with the independent evidence for and against the change. Descriptive: no score and
          no forecast.
        </p>
      </header>
      <div className="flex flex-wrap items-center gap-2">
        {DAYS.map((d) => (
          <button key={d} type="button" onClick={() => setDays(d)}
            className={clsx('h-10 rounded-lg border px-3 text-[14px]', d === days ? 'border-slate-800 bg-slate-800 text-white' : 'border-slate-200 bg-white')}>
            {d} days
          </button>
        ))}
        <label className="ml-auto flex items-center gap-2 text-[14px] text-slate-700">
          <input type="checkbox" checked={convergingOnly} onChange={(e) => setConvergingOnly(e.target.checked)} /> Converging only
        </label>
      </div>
      {state === 'loading' ? <p className="text-slate-500">Loading…</p> : null}
      {state === 'error' ? <p className="text-rose-600">Could not load: {error}</p> : null}
      {state === 'ready' && report ? (
        <>
          <div className="rounded-lg bg-slate-50 p-3 text-[13px] text-slate-600">
            Session {report.session} · knowledge cutoff {fmtWhen(report.knowledge_cutoff)}
            {report.provisional ? ' (not reached yet: provisional)' : ''} · {report.counts.company_quarters} company-quarters with a reported change,{' '}
            {report.counts.converging} converging · showing {report.returned}
          </div>
          <div className="space-y-3">
            {report.briefs.map((b) => (
              <BriefCard key={`${b.symbol}-${b.quarter_end}`} b={b} />
            ))}
            {report.briefs.length === 0 ? <p className="text-slate-500">Nothing in this window.</p> : null}
          </div>
        </>
      ) : null}
    </div>
  );
}
