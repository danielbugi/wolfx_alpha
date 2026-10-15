'use client';

/* eslint-disable @typescript-eslint/no-explicit-any */
import React, { useCallback, useEffect, useState } from 'react';
import { Spinner, Button } from '@nextui-org/react';
import { forwardTrackerApi, ForwardTrackerPayload } from '@/services/forwardTrackerApi';
import { fmtInt, fmtInterval, fmtR } from '@/lib/labFormat';

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

const share = (x: number | null | undefined, digits = 0): string => (x === null || x === undefined || Number.isNaN(x) ? '—' : `${(x * 100).toFixed(digits)}%`);
const vlabel = (name: string) => name.replace(/_/g, ' ');
const HINDSIGHT = 'Hindsight: measured on bars after the decision, not known when the position was opened';

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 p-2">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className="text-base font-semibold text-slate-800">{value}</div>
    </div>
  );
}

const HEALTH_STYLE: Record<string, string> = { ok: 'bg-emerald-100 text-emerald-800', warn: 'bg-amber-100 text-amber-800', info: 'bg-slate-100 text-slate-700' };

export function ForwardTrackerView({ data }: { data: ForwardTrackerPayload }) {
  if (!data.available) {
    return (
      <div className="space-y-2">
        <p className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">Forward tracker data is not available: {data.reason ?? 'unknown reason'}.</p>
      </div>
    );
  }
  const variants: any[] = data.variants;
  const cmp: any[] = data.comparison;
  const ls = data.latest_session;
  const comp = data.completeness;
  const lastSessions: any[] = comp.sessions.slice(-10);
  return (
    <div className="space-y-5">
      <p className="rounded-md border border-cyan-200 bg-cyan-50 p-3 text-sm text-cyan-900">{data.banner} <b>Live forward observation since {data.since}</b>, {fmtInt(data.sessions_processed)} session(s) processed. Historical replay results are in the research reports and are never mixed into these numbers.</p>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Tile label="Latest processed session" value={ls.session_date} />
        <Tile label="Candidates / observations registered" value={`${fmtInt(ls.n_candidates)} / ${fmtInt(ls.n_registered)}`} />
        <Tile label="Entered / closed in that session" value={`${fmtInt(ls.n_entered)} / ${fmtInt(ls.n_closed)}`} />
        <Tile label="Open or pending after it" value={fmtInt(ls.n_open_after)} />
      </div>

      <p className="text-xs text-slate-500"><b>Pending entry</b> = registered, enters at the next session&apos;s open (no position yet). <b>Open</b> = entered and still held. <b>Closed</b> = exited; only closed positions have a realised result.</p>
      <h3 className="text-sm font-semibold text-slate-700">Variant comparison (the same candidates followed by each registered strategy variant)</h3>
      <Table head={['Variant', 'Registered', 'Pending entry', 'Open', 'Closed', 'Net R, 10 bps (mean)', '95% interval', 'Win rate', 'Profit factor', 'Avg MFE / MAE over the holding period (R)', 'Status']} rows={cmp.map((c) => [
        vlabel(c.name), fmtInt(c.registered), fmtInt(c.pending_entry), fmtInt(c.open), fmtInt(c.closed), c.closed ? fmtR(c.net_r_10bps_mean) : '—', c.interval95 ? fmtInterval(c.interval95) : '—',
        c.closed ? share(c.win_rate) : '—', c.profit_factor === null || c.profit_factor === undefined ? '—' : String(c.profit_factor), c.closed ? `${fmtR(c.mfe_r_mean, 2)} / ${fmtR(c.mae_r_mean, 2)}` : '—',
        c.status === 'n_too_small' ? 'n too small for an interval' : 'interval shown'])} />

      <h3 className="text-sm font-semibold text-slate-700">Closed positions: realised net R by round-trip cost (read back from the stored events; standardised R, 1R = 2 x ATR)</h3>
      <Table head={['Variant', 'Closed', 'Net R, 0 bps', 'Net R, 10 bps (primary)', 'Net R, 25 bps', 'Median, 10 bps', 'Avg bars held', 'Exit reasons']} rows={variants.map((v) => {
        const c = v.closed;
        return [vlabel(v.name), fmtInt(c.n), c.n ? fmtR(c.net_r_std['0'].mean) : '—', c.n ? fmtR(c.net_r_std['10'].mean) : '—', c.n ? fmtR(c.net_r_std['25'].mean) : '—', c.n ? fmtR(c.net_r_std['10'].median) : '—',
          c.n ? String(c.bars_held.mean) : '—', Object.keys(c.exit_reasons).map((k) => `${k} ${c.exit_reasons[k]}`).join(' · ') || '—'];
      })} />

      <p className="text-xs text-slate-500">MFE / MAE of closed positions: {variants[0].closed.mfe_mae_window}.</p>
      <h3 className="text-sm font-semibold text-slate-700">Open positions by variant (marked at the last loaded close; an open position is not a result)</h3>
      <p className="text-xs text-slate-500">Running MFE / MAE: {variants[0].open.mfe_mae_window}. A position with no loaded price is counted as unmarked, never as zero.</p>
      <Table head={['Variant', 'Open', 'Marked', 'Unmarked', 'Unrealised gross R (mean)', 'Unrealised net R, 10 bps (mean)', 'Running MFE since entry (mean)', 'Running MAE since entry (mean)', 'Avg bars since entry']} rows={variants.map((v) => {
        const o = v.open;
        return [vlabel(v.name), fmtInt(o.n), fmtInt(o.marked), fmtInt(o.unmarked), o.marked ? fmtR(o.unrealised_gross_r.mean) : '—', o.marked ? fmtR(o.unrealised_net_r_10bps.mean) : '—',
          o.marked ? fmtR(o.running_mfe_r.mean, 2) : '—', o.marked ? fmtR(o.running_mae_r.mean, 2) : '—', o.marked ? String(o.bars_since_entry.mean) : '—'];
      })} />

      <h3 className="text-sm font-semibold text-slate-700">Big-winner opportunity so far</h3>
      <p className="text-xs text-slate-500">{HINDSIGHT}. Windows are still short; &ldquo;peak&rdquo; is the best price reached since entry, not something that could have been sold. {variants[0].big_winner.observation_window.note}</p>
      <Table head={['Variant', 'Entered', 'Peak >= 3R', 'Peak >= 5R', 'Bars observed since entry (mean / max)', 'Median share of the move captured (closed)', 'Premature exits (closed)', 'Bars observed after exit (mean / max)', 'Further move after exit, mean R']} rows={variants.map((v) => {
        const b = v.big_winner;
        const w = b.observation_window;
        return [vlabel(v.name), fmtInt(b.entered_positions), fmtInt(b.peak_ge_3r), fmtInt(b.peak_ge_5r), w.bars_since_entry.n ? `${w.bars_since_entry.mean} / ${w.bars_since_entry.max}` : '—', b.capture_ratio.n ? share(b.capture_ratio.median) : '—',
          b.premature_exit_n ? `${share(b.premature_exit_share)} of ${fmtInt(b.premature_exit_n)}` : '—', w.bars_after_exit.n ? `${w.bars_after_exit.mean} / ${w.bars_after_exit.max}` : '—', b.continuation_after_exit_r.n ? fmtR(b.continuation_after_exit_r.mean, 2) : '—'];
      })} />

      <h3 className="text-sm font-semibold text-slate-700">Data health</h3>
      <div className="flex flex-wrap gap-2">
        {data.health.map((h: any) => (
          <span key={h.name} className={`rounded-full px-3 py-1 text-xs ${HEALTH_STYLE[h.status] ?? HEALTH_STYLE.info}`} title={h.detail}>{vlabel(h.name)}: {h.status} — {h.detail}</span>
        ))}
      </div>

      <h3 className="text-sm font-semibold text-slate-700">Completeness and missing context, last {lastSessions.length} session(s)</h3>
      <Table head={['Session', 'Candidates', 'Market regime attached', 'Relative strength attached', 'Sector known', 'Catalysts unavailable', 'Registered', 'Entered', 'Closed', 'Skipped (already open)']} rows={lastSessions.map((r) => [
        r.session, fmtInt(r.candidates), `${fmtInt(r.market_regime_attached)} / ${fmtInt(r.candidates)}`, `${fmtInt(r.relative_strength_attached)} / ${fmtInt(r.candidates)}`, `${fmtInt(r.sector_known)} / ${fmtInt(r.candidates)}`,
        `${fmtInt(r.catalysts_unavailable)} / ${fmtInt(r.candidates)}`, fmtInt(r.registered), fmtInt(r.entered), fmtInt(r.closed), fmtInt(r.skipped_open)])} />
      {comp.captured_not_processed.length > 0 && <p className="text-xs text-amber-700">Capture complete but not yet processed: {comp.captured_not_processed.join(', ')}.</p>}
      <p className="text-xs text-slate-500">Where a context item is missing the stored record says why (for example &ldquo;no event has been recorded yet (no job feeds this source)&rdquo; for catalysts); nothing is filled in.</p>
    </div>
  );
}

export default function ForwardTracker() {
  const [data, setData] = useState<ForwardTrackerPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      setData(await forwardTrackerApi.get());
    } catch {
      setError('Failed to load the forward tracker summary — is the backend running and are you signed in?');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="p-4 bg-white rounded-lg shadow-sm border border-slate-200 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-800">Forward paper tracker (live)</h2>
        <Button size="sm" className="bg-slate-700 text-white shrink-0" onClick={load} isLoading={loading}>Refresh</Button>
      </div>
      {loading && !data ? <div className="flex justify-center py-12"><Spinner color="primary" /></div>
        : error ? <p className="text-center text-red-500 text-sm py-12">{error}</p>
        : data ? <ForwardTrackerView data={data} /> : null}
    </div>
  );
}
