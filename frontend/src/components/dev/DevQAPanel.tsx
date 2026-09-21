// File: frontend/src/components/dev/DevQAPanel.tsx
'use client';

import React, { useCallback, useState } from 'react';
import { API_BASE_URL } from '@/services/api';

/**
 * Dev-only endpoint health panel. Pings every API endpoint the frontend
 * actually depends on and reports status + latency, independent of whatever
 * any individual page's own error handling does — this catches "endpoint is
 * slow/down" even on pages that swallow fetch errors silently (e.g. the
 * dashboard's market-overview / alpha-finder calls).
 *
 * Gated on NODE_ENV rather than a custom env var so it can never leak into
 * a production build by a forgotten flag — `next build` always sets
 * NODE_ENV=production, dev server always sets 'development'.
 */

const SLOW_MS = 3000;
const TIMEOUT_MS = 15000;

type CheckStatus = 'pending' | 'healthy' | 'slow' | 'error';

/** A parsed JSON body narrowed to an object (null for arrays/scalars/null), so validators can probe keys safely. */
type JsonObject = Record<string, unknown>;

function asJsonObject(value: unknown): JsonObject | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? (value as JsonObject) : null;
}

interface EndpointCheck {
  name: string;
  path: string;
  validate?: (data: JsonObject | null) => string | null; // returns an error message, or null if OK
}

interface CheckResult {
  name: string;
  path: string;
  status: CheckStatus;
  latencyMs: number | null;
  detail: string | null;
}

const ENDPOINTS: EndpointCheck[] = [
  { name: 'API liveness', path: '/api/health' },
  {
    name: 'Dashboard main page',
    path: '/api/dashboard/main-page-data',
    validate: (d) => (!d?.market_summary ? 'missing market_summary' : null),
  },
  { name: 'Top gainers', path: '/api/dashboard/top-gainers?limit=5' },
  { name: 'Top losers', path: '/api/dashboard/top-losers?limit=5' },
  { name: 'Unusual volume', path: '/api/dashboard/unusual-volume?limit=5' },
  { name: 'Top AI picks', path: '/api/dashboard/top-ai-picks?limit=5' },
  { name: 'Screener filters', path: '/api/screener/filters' },
  { name: 'Screener presets', path: '/api/screener/presets' },
  { name: 'Market overview', path: '/api/screener/market-overview' },
  { name: 'Alpha finder', path: '/api/alpha/finder?limit=5' },
  { name: 'Strategy rank', path: '/api/strategy/rank?limit=5' },
  { name: 'Deep value scan', path: '/api/deep-value/scan' },
  {
    name: 'System health report',
    path: '/api/system-health/',
    validate: (d) => (!d?.overall ? 'missing overall status' : null),
  },
  {
    name: 'ML stats report',
    path: '/api/ml-stats/',
    validate: (d) => (!d?.current_model ? 'missing current_model' : null),
  },
  {
    name: 'Performance report',
    path: '/api/performance/',
    validate: (d) => (!d?.database ? 'missing database' : null),
  },
];

async function runCheck(check: EndpointCheck): Promise<CheckResult> {
  const started = performance.now();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const res = await fetch(`${API_BASE_URL}${check.path}`, { signal: controller.signal });
    const latencyMs = Math.round(performance.now() - started);
    clearTimeout(timeout);

    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = asJsonObject(await res.json());
        if (typeof body?.detail === 'string') detail = body.detail;
      } catch {
        /* body wasn't JSON — keep the status-only detail */
      }
      return { name: check.name, path: check.path, status: 'error', latencyMs, detail };
    }

    const data = asJsonObject(await res.json());
    const validationError = check.validate?.(data) ?? null;
    if (validationError) {
      return { name: check.name, path: check.path, status: 'error', latencyMs, detail: validationError };
    }

    return {
      name: check.name,
      path: check.path,
      status: latencyMs > SLOW_MS ? 'slow' : 'healthy',
      latencyMs,
      detail: latencyMs > SLOW_MS ? `${latencyMs}ms — exceeds ${SLOW_MS}ms budget` : null,
    };
  } catch (err) {
    clearTimeout(timeout);
    const latencyMs = Math.round(performance.now() - started);
    const isAbort = err instanceof DOMException && err.name === 'AbortError';
    return {
      name: check.name,
      path: check.path,
      status: 'error',
      latencyMs,
      detail: isAbort ? `timed out after ${TIMEOUT_MS}ms` : err instanceof Error ? err.message : 'network error',
    };
  }
}

function StatusDot({ status }: { status: CheckStatus }) {
  const color =
    status === 'healthy' ? 'bg-emerald-500' : status === 'slow' ? 'bg-amber-500' : status === 'error' ? 'bg-red-500' : 'bg-slate-300';
  return <span className={`inline-block h-2 w-2 rounded-full shrink-0 ${color}`} />;
}

function QAPanelInner() {
  const [open, setOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<CheckResult[]>([]);
  const [lastRun, setLastRun] = useState<Date | null>(null);

  const runAll = useCallback(async () => {
    setRunning(true);
    setResults(ENDPOINTS.map((e) => ({ name: e.name, path: e.path, status: 'pending', latencyMs: null, detail: null })));
    const settled = await Promise.all(ENDPOINTS.map(runCheck));
    setResults(settled);
    setLastRun(new Date());
    setRunning(false);
  }, []);

  const errorCount = results.filter((r) => r.status === 'error').length;
  const slowCount = results.filter((r) => r.status === 'slow').length;
  const healthyCount = results.filter((r) => r.status === 'healthy').length;

  const summaryColor =
    results.length === 0
      ? 'bg-slate-600'
      : errorCount > 0
      ? 'bg-red-600'
      : slowCount > 0
      ? 'bg-amber-600'
      : 'bg-emerald-600';

  return (
    <div className="fixed bottom-4 right-4 z-50 font-sans" style={{ fontFamily: 'inherit' }}>
      {open && (
        <div className="mb-2 w-96 max-h-[70vh] overflow-y-auto rounded-lg border border-slate-700 bg-slate-900 text-slate-100 shadow-2xl">
          <div className="flex items-center justify-between px-3 py-2 border-b border-slate-700 sticky top-0 bg-slate-900">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-300">QA · Endpoint Health</p>
              {lastRun && (
                <p className="text-[10px] text-slate-500 mt-0.5">Last run {lastRun.toLocaleTimeString()}</p>
              )}
            </div>
            <button
              onClick={runAll}
              disabled={running}
              className="text-[11px] font-medium px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-50"
            >
              {running ? 'Running…' : 'Run tests'}
            </button>
          </div>

          {results.length === 0 ? (
            <p className="text-xs text-slate-500 px-3 py-4">No results yet — click &quot;Run tests&quot;.</p>
          ) : (
            <div className="divide-y divide-slate-800">
              {results.map((r) => (
                <div key={r.path} className="px-3 py-2 flex items-start gap-2">
                  <div className="mt-1">
                    <StatusDot status={r.status} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-slate-200 truncate">{r.name}</span>
                      <span className="text-[10px] text-slate-500 shrink-0 tabular-nums">
                        {r.latencyMs != null ? `${r.latencyMs}ms` : ''}
                      </span>
                    </div>
                    <p className="text-[10px] text-slate-500 font-mono truncate">{r.path}</p>
                    {r.detail && (
                      <p className={`text-[10px] mt-0.5 ${r.status === 'error' ? 'text-red-400' : 'text-amber-400'}`}>
                        {r.detail}
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {results.length > 0 && (
            <div className="px-3 py-2 border-t border-slate-800 flex gap-3 text-[10px] text-slate-400">
              <span className="text-emerald-400">{healthyCount} healthy</span>
              <span className="text-amber-400">{slowCount} slow</span>
              <span className="text-red-400">{errorCount} error</span>
            </div>
          )}
        </div>
      )}

      <button
        onClick={() => {
          const next = !open;
          setOpen(next);
          if (next && results.length === 0) runAll();
        }}
        className={`flex items-center gap-2 rounded-full px-3 py-2 text-xs font-semibold text-white shadow-lg ${summaryColor}`}
        title="Dev QA panel — endpoint health checks (dev-only, never shown in production)"
      >
        <span className="inline-block h-2 w-2 rounded-full bg-white/80" />
        QA{results.length > 0 ? ` ${healthyCount}/${results.length}` : ''}
      </button>
    </div>
  );
}

export default function DevQAPanel() {
  if (process.env.NODE_ENV === 'production') return null;
  return <QAPanelInner />;
}
