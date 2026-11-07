// File: frontend/src/app/research/page.tsx
// Research Missions (Owner only): budget, mission list, draft creation and a mission's verified findings, companies, evidence and report.
// Running a mission is a manual CLI step in the MVP; this page never starts one.
'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import {
  DomainInfo,
  MissionDetail,
  MissionSummary,
  ResearchBudget,
  researchMissionsApi,
} from '@/services/researchMissionsApi';

const STATUS_STYLE: Record<string, string> = {
  completed: 'bg-emerald-50 text-emerald-700',
  running: 'bg-blue-50 text-blue-700',
  failed: 'bg-red-50 text-red-700',
  stopped_budget: 'bg-amber-50 text-amber-700',
  draft: 'bg-slate-100 text-slate-600',
  queued: 'bg-slate-100 text-slate-600',
};
const CLAIM_STYLE: Record<string, string> = {
  verified: 'text-emerald-700',
  partial: 'text-amber-700',
  unsupported: 'text-red-600',
  unverified: 'text-slate-500',
};

function usd(v: number): string {
  return `$${v.toFixed(v < 1 ? 4 : 2)}`;
}

function Badge({ status }: { status: string }) {
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[status] ?? 'bg-slate-100 text-slate-600'}`}>{status.replace('_', ' ')}</span>;
}

function BudgetCard({ b }: { b: ResearchBudget | null }) {
  if (!b) return null;
  const used = Math.min(100, ((b.spent_usd + b.reserved_usd) / Math.max(b.cap_usd, 0.0001)) * 100);
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-800">Research budget · {b.month}</h2>
        <span className={`text-xs ${b.paid_calls_enabled ? 'text-emerald-700' : 'text-slate-500'}`}>
          Paid model calls {b.paid_calls_enabled ? 'enabled' : 'disabled'}
        </span>
      </div>
      <div className="mt-2 h-2 w-full rounded bg-slate-100">
        <div className="h-2 rounded bg-slate-700" style={{ width: `${used}%` }} />
      </div>
      <p className="mt-2 text-xs text-slate-600">
        {usd(b.spent_usd)} spent · {usd(b.reserved_usd)} reserved · {usd(b.remaining_usd)} left of {usd(b.cap_usd)} · per-mission cap {usd(b.mission_cap_usd)}
      </p>
    </section>
  );
}

function NewMission({ domains, onCreated }: { domains: DomainInfo[]; onCreated: () => void }) {
  const [kind, setKind] = useState<'thematic' | 'company'>('thematic');
  const [title, setTitle] = useState('HBM and advanced packaging');
  const [question, setQuestion] = useState(
    'Which US-listed companies are exposed to HBM and advanced semiconductor packaging, in which value-chain role, and what are the main risks?',
  );
  const [theme, setTheme] = useState('HBM supply, CoWoS / advanced packaging capacity');
  const [symbol, setSymbol] = useState('');
  const [domain, setDomain] = useState('ai_semiconductors');
  const [msg, setMsg] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      const params = kind === 'thematic' ? { theme } : { symbol: symbol.trim().toUpperCase() };
      const r = await researchMissionsApi.createDraft({ kind, title: kind === 'company' ? `Company deep-dive: ${symbol.trim().toUpperCase()}` : title, question, domain, params });
      setMsg(`Draft #${r.id} created (budget ${usd(r.budget_usd)}). Run it from the CLI: python -m research_missions run ${r.id}`);
      onCreated();
    } catch {
      setMsg('Could not create the draft. Check the fields and try again.');
    }
  }

  const options = domains.filter((d) => (kind === 'thematic' ? d.thematic : true));
  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-800">New mission (draft)</h2>
      <div className="flex gap-4 text-sm">
        {(['thematic', 'company'] as const).map((k) => (
          <label key={k} className="flex items-center gap-1">
            <input type="radio" checked={kind === k} onChange={() => { setKind(k); setDomain(k === 'thematic' ? 'ai_semiconductors' : 'general'); }} />
            {k === 'thematic' ? 'Thematic research' : 'Company deep-dive'}
          </label>
        ))}
      </div>
      {kind === 'thematic' ? (
        <>
          <input className="w-full rounded border border-slate-300 px-2 py-1 text-sm" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title" />
          <input className="w-full rounded border border-slate-300 px-2 py-1 text-sm" value={theme} onChange={(e) => setTheme(e.target.value)} placeholder="Theme details" />
        </>
      ) : (
        <input className="w-full rounded border border-slate-300 px-2 py-1 text-sm uppercase" value={symbol} onChange={(e) => setSymbol(e.target.value)} placeholder="Ticker, e.g. MU" />
      )}
      <textarea className="w-full rounded border border-slate-300 px-2 py-1 text-sm" rows={3} value={question} onChange={(e) => setQuestion(e.target.value)} />
      <select className="rounded border border-slate-300 px-2 py-1 text-sm" value={domain} onChange={(e) => setDomain(e.target.value)}>
        {options.map((d) => (
          <option key={d.key} value={d.key}>{d.label}</option>
        ))}
      </select>
      <div className="flex items-center gap-3">
        <button type="submit" className="rounded bg-slate-800 px-3 py-1.5 text-sm text-white hover:bg-slate-700">Create draft</button>
        {msg && <span className="text-xs text-slate-600">{msg}</span>}
      </div>
    </form>
  );
}

function Detail({ id }: { id: number }) {
  const [d, setD] = useState<MissionDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    researchMissionsApi.get(id).then(setD).catch(() => setErr('Could not load this mission.'));
  }, [id]);
  if (err) return <p className="text-sm text-red-600">{err}</p>;
  if (!d) return <p className="text-sm text-slate-400">Loading…</p>;
  const shown = d.claims.filter((c) => c.status === 'verified' || c.status === 'partial');
  const byLabel = Object.fromEntries(d.claims.map((c) => [c.label, c]));
  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold text-slate-900">{d.mission.title}</h2>
          <Badge status={d.mission.status} />
        </div>
        <p className="mt-1 text-sm text-slate-600">{d.mission.question}</p>
        <p className="mt-2 text-xs text-slate-500">
          {d.usage.calls} model calls · {usd(d.usage.cost_usd)} of {usd(d.mission.budget_usd)} · {d.evidence.length} evidence items · {shown.length}/{d.claims.length} claims verified
          {d.mission.error ? ` · ${d.mission.error}` : ''}
        </p>
        <p className="mt-2 text-xs text-slate-500">Research notes, not investment advice. Internal-only sources must not be redistributed.</p>
      </div>
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h3 className="mb-2 text-sm font-semibold text-slate-800">Verified findings</h3>
        {shown.length === 0 ? <p className="text-sm text-slate-500">No verified findings yet.</p> : (
          <ul className="space-y-1.5 text-sm">
            {shown.map((c) => (
              <li key={c.label}>
                <span className="font-mono text-xs text-slate-400">{c.label}</span>{' '}
                <span className="text-xs uppercase text-slate-400">{c.kind}</span> {c.text}{' '}
                <span className={`text-xs ${CLAIM_STYLE[c.status]}`}>{c.status}</span>{' '}
                <span className="font-mono text-xs text-slate-400">[{c.evidence.join(', ')}]</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      {d.companies.length > 0 && (
        <section className="overflow-x-auto rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-800">Companies and roles</h3>
          <table className="w-full min-w-[36rem] text-xs">
            <thead><tr className="text-left text-slate-400"><th className="py-1 pr-3">Symbol</th><th className="pr-3">Role</th><th className="pr-3">Exposure</th><th className="pr-3">Findings</th><th>Universe</th></tr></thead>
            <tbody>
              {d.companies.map((co) => {
                const ok = co.claim_labels.filter((l) => ['verified', 'partial'].includes(byLabel[l]?.status));
                return (
                  <tr key={co.symbol} className="border-t border-slate-100">
                    <td className="py-1.5 pr-3 font-medium">{co.symbol} <span className="text-slate-400">{co.name}</span></td>
                    <td className="pr-3">{co.role}</td>
                    <td className="pr-3">{co.exposure}</td>
                    <td className="pr-3 font-mono">{ok.join(', ') || <span className="text-slate-400">none verified</span>}</td>
                    <td>{co.in_universe ? 'yes' : 'no'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}
      <section className="overflow-x-auto rounded-lg border border-slate-200 bg-white p-4">
        <h3 className="mb-2 text-sm font-semibold text-slate-800">Evidence ({d.evidence.length})</h3>
        <table className="w-full min-w-[36rem] text-xs">
          <tbody>
            {d.evidence.map((e) => (
              <tr key={e.label} className="border-t border-slate-100 align-top">
                <td className="py-1.5 pr-3 font-mono text-slate-400">{e.label}</td>
                <td className="pr-3 text-slate-500">{e.source}{e.license === 'internal_only' ? ' · internal' : ''}</td>
                <td className="pr-3 text-slate-500">{e.published_at?.slice(0, 10) ?? '—'}</td>
                <td>{e.url.startsWith('http') ? <a className="text-blue-700 hover:underline" href={e.url} target="_blank" rel="noreferrer">{e.title}</a> : e.title}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {d.report_markdown && (
        <section className="rounded-lg border border-slate-200 bg-white p-4">
          <h3 className="mb-2 text-sm font-semibold text-slate-800">Report v{d.report_version}</h3>
          <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap text-xs text-slate-700">{d.report_markdown}</pre>
        </section>
      )}
    </div>
  );
}

export default function ResearchPage() {
  const { user } = useAuth();
  const [missions, setMissions] = useState<MissionSummary[]>([]);
  const [domains, setDomains] = useState<DomainInfo[]>([]);
  const [budget, setBudget] = useState<ResearchBudget | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    researchMissionsApi.list().then((r) => { setMissions(r.missions); setDomains(r.domains); }).catch(() => setErr('Research missions are not available.'));
    researchMissionsApi.budget().then(setBudget).catch(() => setBudget(null));
  }, []);
  useEffect(() => { load(); }, [load]);

  if (user && user.role !== 'owner') return <p className="p-6 text-sm text-slate-600">Research missions are available to the Owner only.</p>;
  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 sm:p-6">
      <h1 className="text-xl font-semibold text-slate-900">Research missions</h1>
      {err && <p className="text-sm text-red-600">{err}</p>}
      <BudgetCard b={budget} />
      <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
        <div className="space-y-4">
          <NewMission domains={domains} onCreated={load} />
          <section className="rounded-lg border border-slate-200 bg-white p-2">
            {missions.length === 0 ? <p className="p-2 text-sm text-slate-500">No missions yet.</p> : missions.map((m) => (
              <button key={m.id} onClick={() => setSelected(m.id)} className={`block w-full rounded p-2 text-left hover:bg-slate-50 ${selected === m.id ? 'bg-slate-50' : ''}`}>
                <div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-medium text-slate-800">#{m.id} {m.title}</span><Badge status={m.status} /></div>
                <div className="text-xs text-slate-500">{m.kind} · {usd(m.spent_usd)} / {usd(m.budget_usd)}</div>
              </button>
            ))}
          </section>
        </div>
        <div>{selected ? <Detail id={selected} /> : <p className="p-2 text-sm text-slate-500">Select a mission.</p>}</div>
      </div>
    </div>
  );
}
