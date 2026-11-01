'use client';

/* eslint-disable @typescript-eslint/no-explicit-any */
import React, { useCallback, useEffect, useState } from 'react';
import { Spinner, Button } from '@nextui-org/react';
import { dataQualityApi, DataQualityPayload } from '@/services/dataQualityApi';
import { fmtInt } from '@/lib/labFormat';

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

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 p-2">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className="text-base font-semibold text-slate-800">{value}</div>
    </div>
  );
}

const pretty = (s: string) => s.replace(/_/g, ' ');
const pct = (x: number | null | undefined, digits = 1) => (x === null || x === undefined || Number.isNaN(x) ? '—' : `${(x * 100).toFixed(digits)}%`);
const ROLE_STYLE: Record<string, string> = { exploratory: 'bg-amber-100 text-amber-900', confirmation_eligible: 'bg-emerald-100 text-emerald-900' };
const ROLE_TEXT: Record<string, string> = { exploratory: 'Exploratory only', confirmation_eligible: 'Eligible for confirmation' };

function Reconciliation({ title, r }: { title: string; r: any }) {
  if (!r) return <p className="text-sm text-slate-500">{title}: both event families must be stored before they can be compared.</p>;
  const causes = Object.entries(r.cause_totals as Record<string, number>).filter(([, n]) => n > 0);
  const gap = r.availability_gap_days_a_minus_b;
  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold text-slate-700">{title}</h4>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Tile label="Company-quarters in both" value={fmtInt(r.shared_company_quarters)} />
        <Tile label="Events in both" value={fmtInt(r.events_in_both)} />
        <Tile label="Only in the first / second" value={`${fmtInt(r.events_only_in_a)} / ${fmtInt(r.events_only_in_b)}`} />
        <Tile label="Agreement" value={pct(r.agreement, 0)} />
      </div>
      {causes.length > 0 && (
        <Table head={['Cause of a difference', 'Events']} rows={causes.map(([c, n]) => [pretty(c), fmtInt(n)])} />
      )}
      {Object.entries(r.examples ?? {}).map(([cause, list]: [string, any]) => (
        <div key={cause} className="text-xs text-slate-600">
          <b>{pretty(cause)}</b>: {list.map((e: any) => `${e.symbol} ${e.quarter_end} ${pretty(e.event_type)} (${e.detail})`).join(' · ')}
        </div>
      ))}
      {gap?.n > 0 && (
        <p className="text-xs text-slate-500">Availability date, first minus second (days): median {gap.median}, 10th to 90th percentile {gap.p10} to {gap.p90}; first later in {fmtInt(gap.a_later)} of {fmtInt(gap.n)}, same day in {fmtInt(gap.same_day)}.</p>
      )}
      <p className="text-xs text-slate-500">Thresholds are not tuned to increase agreement; this only explains differences. An &ldquo;unexplained&rdquo; difference is shown, never hidden.</p>
    </div>
  );
}

export function DataQualityView({ data }: { data: DataQualityPayload }) {
  if (!data.available) {
    return <p className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">Data quality information is not available: {data.reason ?? 'unknown reason'}.</p>;
  }
  const cov = data.coverage;
  const u = cov.universe;
  return (
    <div className="space-y-5">
      <p className="rounded-md border border-cyan-200 bg-cyan-50 p-3 text-sm text-cyan-900">
        Which data may back which kind of study. Exploratory analysis is allowed on any event family and is labelled as such; only a point-in-time family with enough coverage can back a confirmatory test.
      </p>

      <h3 className="text-sm font-semibold text-slate-700">Event families: what each may be used for</h3>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        {data.namespaces.map((n: any) => (
          <div key={n.method_version} className="rounded-md border border-slate-200 p-3 space-y-1">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold text-slate-800">{n.label}</span>
              <span className={`rounded-full px-2 py-0.5 text-[11px] ${ROLE_STYLE[n.role_now] ?? ROLE_STYLE.exploratory}`}>{ROLE_TEXT[n.role_now] ?? n.role_now}</span>
            </div>
            <div className="text-[11px] text-slate-500">{n.method_version} · {n.point_in_time ? 'point in time' : 'not point in time'}</div>
            <div className="text-xs text-slate-700">Figures: {n.figures}. Availability: {n.availability}.</div>
            <div className="text-xs text-slate-700">{fmtInt(n.symbols)} companies · {fmtInt(n.features)} quarters · {fmtInt(n.events)} events{n.newest_available ? ` · newest ${n.newest_available}` : ''}</div>
            {n.role_reason && <div className="text-xs text-amber-800">{n.role_reason}</div>}
            {Array.isArray(n.gates) && (
              <ul className="space-y-0.5 pt-1" aria-label="Confirmation gates">
                {n.gates.map((g: any) => (
                  <li key={g.gate} className={`text-xs ${g.ok ? 'text-emerald-800' : 'text-red-700'}`}>{g.ok ? 'Met' : 'Not met'} · {pretty(g.gate)}: {g.detail}</li>
                ))}
              </ul>
            )}
            <div className="text-xs text-slate-500">{n.note}</div>
          </div>
        ))}
      </div>
      <ul className="list-disc pl-5 text-xs text-slate-600 space-y-1">{data.rules.map((r: string) => <li key={r}>{r}</li>)}</ul>

      <h3 className="text-sm font-semibold text-slate-700">SEC ingestion coverage</h3>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Tile label="Universe symbols" value={fmtInt(u.symbols)} />
        <Tile label="Mapped to a SEC CIK" value={fmtInt(u.with_cik)} />
        <Tile label="With SEC quarters" value={`${fmtInt(u.with_revenue_quarters)} (${pct(data.coverage_ratio, 1)})`} />
        <Tile label="Not yet checked" value={fmtInt(u.never_checked)} />
        <Tile label="Checked OK" value={fmtInt(u.status_ok)} />
        <Tile label="No US-GAAP periodic filings" value={fmtInt(u.no_us_gaap_periodic_filings)} />
        <Tile label="Errors (retry backlog)" value={fmtInt(u.status_error)} />
        <Tile label="Newest filing" value={cov.latest_filing?.newest_filing_date ?? '—'} />
      </div>
      <p className="text-xs text-slate-500">Foreign issuers are not assumed to have equivalent coverage: they file 20-F or 40-F (no quarterly report, often IFRS), so &ldquo;no US-GAAP periodic filings&rdquo; is reported separately and never counted as a failure.{u.without_cik > 0 ? ` ${fmtInt(u.without_cik)} universe symbols have no CIK in the SEC ticker file${cov.no_cik_sample?.length ? ` (e.g. ${cov.no_cik_sample.slice(0, 8).join(', ')})` : ''}.` : ''}</p>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div>
          <h4 className="text-sm font-semibold text-slate-700 mb-1">By sector</h4>
          <Table head={['Sector', 'Symbols', 'With SEC quarters', '%']} rows={cov.by_sector.map((s: any) => [s.sector, fmtInt(s.symbols), fmtInt(s.with_sec_quarters), `${s.pct}%`])} />
        </div>
        <div>
          <h4 className="text-sm font-semibold text-slate-700 mb-1">By reporting period</h4>
          <Table head={['Period', 'Companies']} rows={cov.by_reporting_period.slice(0, 12).map((p: any) => [p.period, fmtInt(p.companies)])} />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div>
          <h4 className="text-sm font-semibold text-slate-700 mb-1">Missing fields (companies with a recent revenue quarter)</h4>
          <Table head={['Field', 'Missing', 'Of', '%']} rows={cov.missing_fields_recent_quarters.map((m: any) => [pretty(m.metric), fmtInt(m.missing), fmtInt(m.of_company_quarters), m.pct_missing === null ? '—' : `${m.pct_missing}%`])} />
        </div>
        <div>
          <h4 className="text-sm font-semibold text-slate-700 mb-1">Freshness: SEC versus yfinance (newest quarter per company)</h4>
          <Table head={['', 'Companies']} rows={[['SEC newer', fmtInt(cov.freshness_vs_yfinance.sec_newer)], ['Same quarter', fmtInt(cov.freshness_vs_yfinance.same_quarter)], ['yfinance newer', fmtInt(cov.freshness_vs_yfinance.yfinance_newer)], ['In yfinance only', fmtInt(cov.freshness_vs_yfinance.yfinance_only)]]} />
          {cov.failures.error_symbols > 0 && <p className="mt-2 text-xs text-amber-800">{fmtInt(cov.failures.error_symbols)} companies failed on their last check: {cov.failures.top_errors.map((e: any) => `${e.error || 'no message'} (${e.n})`).join(' · ')}</p>}
        </div>
      </div>

      <h3 className="text-sm font-semibold text-slate-700">Reconciliation of event families</h3>
      <Reconciliation title="SEC (latest values) against yfinance, on the company-quarters both cover" r={data.reconciliation.sec_vs_yfinance} />
      <Reconciliation title="SEC latest values against SEC point in time: how much a restatement would have leaked" r={data.reconciliation.pit_vs_latest_values} />
    </div>
  );
}

export default function DataQuality() {
  const [data, setData] = useState<DataQualityPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setData(await dataQualityApi.get());
    } catch {
      setError('Failed to load the data quality summary — is the backend running and are you signed in?');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="p-4 bg-white rounded-lg shadow-sm border border-slate-200 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-800">Data quality and point-in-time eligibility (live)</h2>
        <Button size="sm" className="bg-slate-700 text-white shrink-0" onClick={load} isLoading={loading}>Refresh</Button>
      </div>
      {loading && !data ? <div className="flex justify-center py-12"><Spinner color="primary" /></div>
        : error ? <p className="text-center text-red-500 text-sm py-12">{error}</p>
        : data ? <DataQualityView data={data} /> : null}
    </div>
  );
}
