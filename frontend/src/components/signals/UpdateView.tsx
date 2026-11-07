'use client';

// The public view of one Fundamental Update, from the allowlisted API fields only. Two forms: a feed row (UpdateSummary) and the full page
// (UpdateDetail). Factual wording only: what the company reported, when it became public and where to check it. No scores, no prices, no advice.
import React from 'react';
import Link from 'next/link';
import { clsx } from 'clsx';
import type { PublicCorrection, PublicStatus, PublicUpdate } from '@/services/publicSignalsApi';
import { CORRECTION_TEXT, fmtDay, fmtUtc, signalHref } from '@/lib/signals';

function StatusBadge({ status }: { status: PublicStatus }) {
  if (status === 'published') return null;
  return (
    <span className={clsx('rounded px-2 py-0.5 text-[11px] font-semibold',
      status === 'withdrawn' ? 'bg-slate-200 text-slate-700' : 'bg-amber-100 text-amber-800')}>
      {status === 'withdrawn' ? 'Withdrawn' : 'Corrected'}
    </span>
  );
}

function Figures({ u, compact }: { u: PublicUpdate; compact?: boolean }) {
  return (
    <dl className={clsx('grid gap-2', compact ? 'grid-cols-2 sm:grid-cols-3' : 'grid-cols-1 sm:grid-cols-3')}>
      {u.figures.map((f) => (
        <div key={f.label} className="min-w-0 rounded-lg bg-slate-50 px-3 py-2">
          <dt className="text-[11px] text-slate-500">{f.label}</dt>
          <dd className={clsx('font-semibold tabular-nums text-slate-900', compact ? 'text-sm' : 'text-xl')}>{f.value}</dd>
          {f.compare && <dd className="text-[11px] tabular-nums text-slate-500">{f.compare}</dd>}
        </div>
      ))}
    </dl>
  );
}

function Context({ u }: { u: PublicUpdate }) {
  const parts = [u.sector, u.industry].filter(Boolean) as string[];
  if (u.market_cap) parts.push(`Market cap ${u.market_cap.value} (${fmtDay(u.market_cap.as_of)})`);
  return parts.length ? <p className="text-xs text-slate-500">{parts.join(' · ')}</p> : null;
}

export function UpdateSummary({ u, status }: { u: PublicUpdate; status: PublicStatus }) {
  return (
    <article className="rounded-xl border border-slate-200 bg-white p-4" data-testid="update-summary">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
          <Link href={signalHref(u.key)} className="text-base font-semibold text-slate-900 hover:text-accent hover:underline">{u.symbol}</Link>
          {u.company_name && <span className="truncate text-sm text-slate-500">{u.company_name}</span>}
        </div>
        <StatusBadge status={status} />
      </div>
      <h3 className="mt-1 text-sm font-semibold text-amber-700">{u.label}</h3>
      <p className="mt-1 text-sm text-slate-700">{u.why}</p>
      <div className="mt-3"><Figures u={u} compact /></div>
      <p className="mt-2 text-[11px] text-slate-500">{u.fiscal_label} · public {fmtDay(u.public_date)} ({u.date_source})</p>
    </article>
  );
}

function ExternalLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex items-center rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:border-slate-300 hover:text-slate-900">
      {children}
    </a>
  );
}

export function UpdateDetail({ u, status, corrections, firstPublishedAt }: { u: PublicUpdate; status: PublicStatus; corrections: PublicCorrection[]; firstPublishedAt: string }) {
  return (
    <article className="space-y-5" data-testid="update-detail">
      {status === 'withdrawn' && (
        <div role="status" className="rounded-lg border border-slate-300 bg-slate-100 px-4 py-3 text-sm text-slate-700">
          This update was withdrawn: the underlying figures were revised and it no longer applies. It stays here, as first published, for the record.
        </div>
      )}
      <header className="space-y-1">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-amber-700">Fundamental update</p>
        <div className="flex flex-wrap items-baseline gap-x-3">
          <h1 className="text-3xl font-bold tracking-tight text-slate-900">{u.symbol}</h1>
          {u.company_name && <span className="text-lg text-slate-500">{u.company_name}</span>}
          <StatusBadge status={status} />
        </div>
        <h2 className="text-xl font-semibold text-amber-700">{u.label}</h2>
        <Context u={u} />
      </header>

      <p className="max-w-prose text-base text-slate-800">{u.why}</p>

      <section aria-labelledby="figures-h" className="space-y-2">
        <h3 id="figures-h" className="text-sm font-semibold text-slate-700">{u.fiscal_label}</h3>
        <Figures u={u} />
        <p className="text-xs text-slate-500">Public {fmtDay(u.public_date)} ({u.date_source}). Quarter ended {fmtDay(u.quarter_end)}.</p>
      </section>

      {u.news.length > 0 && (
        <section aria-labelledby="news-h" className="space-y-2">
          <h3 id="news-h" className="text-sm font-semibold text-slate-700">News at the time</h3>
          <ul className="space-y-2">
            {u.news.map((n) => (
              <li key={n.url} className="text-sm">
                <a href={n.url} target="_blank" rel="noopener noreferrer" className="text-slate-800 hover:underline">{n.headline}</a>
                <p className="text-[11px] text-slate-500">{n.source} · {fmtUtc(n.published_at)}</p>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="flex flex-wrap gap-2" aria-label="Sources">
        {u.links.sec && <ExternalLink href={u.links.sec}>{u.links.sec_kind === 'filing' ? 'SEC filing' : 'SEC filings'}</ExternalLink>}
        <ExternalLink href={u.links.finviz}>Finviz</ExternalLink>
      </section>

      {corrections.length > 0 && (
        <section aria-labelledby="corr-h" className="space-y-1">
          <h3 id="corr-h" className="text-sm font-semibold text-slate-700">Corrections</h3>
          <ul className="space-y-1 text-xs text-slate-600">
            {corrections.map((c) => (
              <li key={c.version}>
                {fmtUtc(c.at)}: {CORRECTION_TEXT[c.kind] ?? c.kind}{c.changed.length > 0 && c.kind !== 'withdrawn' ? ` (${c.changed.join(', ')})` : ''}
              </li>
            ))}
          </ul>
        </section>
      )}

      <footer className="border-t border-slate-200 pt-3 text-xs text-slate-500">
        <p>First published {fmtUtc(firstPublishedAt)}.</p>
        <p className="mt-1 max-w-prose">
          A Fundamental Update reports a change in a company&apos;s reported figures, taken from its filings and dated by when they became public.
          It describes what happened; it is not advice.
        </p>
      </footer>
    </article>
  );
}
