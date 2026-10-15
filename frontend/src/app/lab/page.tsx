'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Spinner, Button } from '@nextui-org/react';
import { labApi, LabReport, LabReportList } from '@/services/labApi';
import LabReportView from '@/components/lab/LabReportView';
import ForwardTracker from '@/components/lab/ForwardTracker';
import { usePagePerf } from '@/hooks/usePagePerf';

const KIND_LABEL: Record<string, string> = { strategy_lab: 'Strategy Lab', ml: 'ML Intelligence', catalyst: 'Catalyst Intelligence' };

export default function LabPage() {
  const { markLoaded } = usePagePerf('/lab');
  const [list, setList] = useState<LabReportList | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [report, setReport] = useState<LabReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<'reports' | 'forward'>('reports');

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const l = await labApi.list();
      setList(l);
      setSelected((cur) => cur ?? l.reports[0]?.id ?? null);
      markLoaded();
    } catch {
      setError('Failed to load the Quant Lab reports — is the backend running and are you signed in?');
    } finally {
      setLoading(false);
    }
  }, [markLoaded]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!selected) { setReport(null); return; }
    let cancelled = false;
    labApi.get(selected).then((r) => { if (!cancelled) setReport(r); }).catch(() => { if (!cancelled) setReport(null); });
    return () => { cancelled = true; };
  }, [selected]);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-cyan-50">
      <div className="container mx-auto p-4 max-w-6xl space-y-4">
        <div className="p-4 bg-white rounded-lg shadow-sm border border-slate-200 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Quant Lab</h1>
            <p className="text-slate-600 text-sm mt-1">{list?.notice ?? 'Historical research evidence with its uncertainty. Not signals, not advice.'}</p>
          </div>
          <Button size="sm" className="bg-slate-700 text-white shrink-0" onClick={load} isLoading={loading}>Refresh</Button>
        </div>

        <div role="tablist" aria-label="Quant Lab views" className="flex gap-2">
          <button role="tab" aria-selected={view === 'reports'} onClick={() => setView('reports')} className={`px-3 py-1.5 rounded-md text-sm border ${view === 'reports' ? 'bg-white border-cyan-400 shadow-sm font-medium' : 'bg-white/60 border-slate-200'}`}>Research reports (historical)</button>
          <button role="tab" aria-selected={view === 'forward'} onClick={() => setView('forward')} className={`px-3 py-1.5 rounded-md text-sm border ${view === 'forward' ? 'bg-white border-cyan-400 shadow-sm font-medium' : 'bg-white/60 border-slate-200'}`}>Forward paper tracker (live)</button>
        </div>

        {view === 'forward' ? (
          <ForwardTracker />
        ) : loading && !list ? (
          <div className="flex justify-center py-16"><Spinner color="primary" /></div>
        ) : error ? (
          <p className="text-center text-red-500 text-sm py-16">{error}</p>
        ) : list && !list.available ? (
          <p className="text-center text-slate-500 text-sm py-16">No research reports are published yet.</p>
        ) : list ? (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className="space-y-2">
              {list.reports.map((r) => (
                <button key={r.id} onClick={() => setSelected(r.id)} className={`w-full text-left p-3 rounded-lg border ${selected === r.id ? 'bg-white border-cyan-400 shadow-sm' : 'bg-white/60 border-slate-200'}`}>
                  <span className="text-[11px] uppercase tracking-wide text-slate-500">{KIND_LABEL[r.kind] ?? r.kind} · research only</span>
                  <span className="block text-sm font-medium text-slate-800">{r.title}</span>
                  <span className="block text-xs text-slate-500 mt-1">{r.headline}</span>
                </button>
              ))}
              {list.invalid.length > 0 && <p className="text-xs text-red-500">{list.invalid.length} report file(s) failed validation and are not shown.</p>}
            </div>
            <div className="lg:col-span-2 p-4 bg-white rounded-lg shadow-sm border border-slate-200">
              {report ? (<><h2 className="text-lg font-semibold text-slate-800 mb-2">{report.title}</h2><LabReportView report={report} /></>) : <p className="text-sm text-slate-500">Select a report.</p>}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
