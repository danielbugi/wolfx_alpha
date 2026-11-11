'use client';

// Opportunity Intelligence v1 (owner only, research): companies whose reported business changed, with three SEPARATE dimensions of evidence
// (business, expectations, market response), contradictions and caveats. Descriptive: no score, no forecast, not a recommendation.
// docs/architecture/OPPORTUNITY_INTELLIGENCE.md
import React, { useCallback, useEffect, useState } from 'react';
import { clsx } from 'clsx';
import { opportunityApi, OIBrief, OIFact, OIReport, View } from '@/services/opportunityApi';
import { DASH, DIMENSION_LABEL, fmtFigure, fmtWhen, provenance, VERDICT_CLASS, VERDICT_LABEL, VIEW_LABEL } from '@/lib/opportunityFormat';

const DAYS = [7, 14, 30];
const VIEWS: View[] = ['agreeing', 'discovery', 'all'];
const VIEW_HELP: Record<View, string> = {
  agreeing: 'Two or more accounting facts improved, none worsened, and the price rose relative to the S&P 500 after the release.',
  discovery: 'Two or more accounting facts improved, none worsened, but the price was flat or fell relative to the S&P 500. A research list, not a call to act.',
  all: 'Every company with a reported change in the window, in the same evidence order.',
};

function Chip({ label, verdict }: { label?: string; verdict: OIFact['verdict'] }) {
  return (
    <span className={clsx('inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[12px]', VERDICT_CLASS[verdict])}>
      {label ? <span className="font-medium">{label} ·</span> : null}
      <span>{VERDICT_LABEL[verdict]}</span>
    </span>
  );
}

function FactBlock({ fact }: { fact: OIFact }) {
  return (
    <div className="min-w-0 rounded-lg border border-slate-200 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[14px] font-semibold text-slate-800">{fact.label}</span>
        <Chip verdict={fact.verdict} />
      </div>
      {fact.figures.length > 0 ? (
        <ul className="mt-2 space-y-1.5">
          {fact.figures.map((f) => (
            <li key={f.label} className="text-[13px]">
              <div className="flex justify-between gap-3">
                <span className="min-w-0 text-slate-600">{f.label}</span>
                <span className="whitespace-nowrap font-mono tabular-nums text-slate-900">{fmtFigure(f.value, f.unit, f.label)}</span>
              </div>
              <div className="break-all text-[11px] text-slate-400">{provenance(f)}</div>
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
  const a = b.assessment;
  return (
    <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <button type="button" className="w-full text-left" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <div className="flex items-baseline justify-between gap-2">
          <div className="min-w-0">
            <span className="text-[17px] font-bold text-slate-900">{b.symbol}</span>
            <span className="ml-2 text-[13px] text-slate-500">{b.company_name || DASH}</span>
          </div>
          <span className="shrink-0 text-[12px] text-slate-500">public {b.first_public}</span>
        </div>
        <p className="mt-1 text-[13px] text-slate-700">{b.what_changed.map((w) => w.label).join(' · ')}</p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {(['business', 'expectations', 'market'] as const).map((k) => (
            <Chip key={k} label={DIMENSION_LABEL[k]} verdict={a.dimensions[k]} />
          ))}
        </div>
        <p className="mt-2 text-[12px] text-slate-500">
          {a.category_label}. Accounting facts improved: {a.business_improved.length ? a.business_improved.join(', ') : 'none'}
          {a.business_worsened.length ? `; worsened: ${a.business_worsened.join(', ')}` : ''}.
        </p>
      </button>
      {open ? (
        <div className="mt-3 space-y-4 border-t border-slate-100 pt-3">
          <section>
            <h3 className="text-[13px] font-semibold uppercase tracking-wide text-slate-500">What changed</h3>
            {b.what_changed.map((w) => (
              <div key={w.event_type} className="mt-1.5">
                <p className="text-[14px] font-medium text-slate-800">{w.label}</p>
                {w.lines.map((l) => (
                  <p key={l} className="text-[13px] text-slate-700">{l}</p>
                ))}
                <p className="break-all text-[11px] text-slate-400">{w.source} · {fmtWhen(w.available_at)}</p>
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
          <section>
            <h3 className="text-[13px] font-semibold uppercase tracking-wide text-slate-500">1 · Business: the reported figures</h3>
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              {b.evidence.business.map((f) => (
                <FactBlock key={f.family} fact={f} />
              ))}
            </div>
          </section>
          <section className="grid gap-2 sm:grid-cols-2">
            <div className="min-w-0">
              <h3 className="mb-2 text-[13px] font-semibold uppercase tracking-wide text-slate-500">2 · Expectations</h3>
              <FactBlock fact={b.evidence.expectations} />
            </div>
            <div className="min-w-0">
              <h3 className="mb-2 text-[13px] font-semibold uppercase tracking-wide text-slate-500">3 · Market response (not business evidence)</h3>
              <FactBlock fact={b.evidence.market} />
            </div>
          </section>
        </div>
      ) : null}
    </article>
  );
}

export default function OpportunitiesPage() {
  const [days, setDays] = useState(14);
  const [view, setView] = useState<View>('agreeing');
  const [report, setReport] = useState<OIReport | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setState('loading');
    try {
      setReport(await opportunityApi.briefs({ days, view, limit: 200 }));
      setState('ready');
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'failed');
      setState('error');
    }
  }, [days, view]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="mx-auto max-w-4xl space-y-4 px-4 py-4">
      <header>
        <h1 className="text-[22px] font-bold text-slate-900">Opportunity Intelligence</h1>
        <p className="text-[13px] text-slate-500">
          Owner-only research. Companies whose reported figures changed, with the evidence for and against the change in three separate
          dimensions. Agreement between them describes evidence; it is not a forecast or a recommendation, and outcomes are not measured yet.
        </p>
      </header>
      <div className="flex flex-wrap gap-2" role="tablist">
        {VIEWS.map((v) => (
          <button key={v} type="button" role="tab" aria-selected={v === view} onClick={() => setView(v)}
            className={clsx('h-10 rounded-lg border px-3 text-[14px]', v === view ? 'border-slate-800 bg-slate-800 text-white' : 'border-slate-200 bg-white')}>
            {VIEW_LABEL[v]}
          </button>
        ))}
      </div>
      <p className="text-[13px] text-slate-600">{VIEW_HELP[view]}</p>
      <div className="flex flex-wrap items-center gap-2">
        {DAYS.map((d) => (
          <button key={d} type="button" onClick={() => setDays(d)}
            className={clsx('h-9 rounded-lg border px-3 text-[13px]', d === days ? 'border-slate-700 bg-slate-100' : 'border-slate-200 bg-white')}>
            {d} days
          </button>
        ))}
      </div>
      {state === 'loading' ? <p className="text-slate-500">Loading…</p> : null}
      {state === 'error' ? <p className="text-rose-600">Could not load: {error}</p> : null}
      {state === 'ready' && report ? (
        <>
          <div className="space-y-1 rounded-lg bg-slate-50 p-3 text-[13px] text-slate-600">
            <p>
              Session {report.session} · knowledge cutoff {fmtWhen(report.knowledge_cutoff)}
              {report.provisional ? ' (not reached yet: provisional)' : ''} · {report.counts.company_quarters} company-quarters with a reported change ·
              showing {report.returned} of {report.matching}
            </p>
            <ul className="list-disc pl-5 text-[12px]">
              {report.caveats.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </div>
          <div className="space-y-3">
            {report.briefs.map((b) => (
              <BriefCard key={`${b.symbol}-${b.quarter_end}`} b={b} />
            ))}
            {report.briefs.length === 0 ? <p className="text-slate-500">Nothing in this view for the window.</p> : null}
          </div>
        </>
      ) : null}
    </div>
  );
}
