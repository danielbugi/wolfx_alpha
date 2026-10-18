// File: frontend/src/components/marketIntel/MarketIntelligencePanel.tsx
'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { marketIntelligenceApi, MarketSnapshot } from '@/services/marketIntelligenceApi';
import DataStamp from '@/components/common/DataStamp';
import ErrorAlert from '@/components/common/ErrorAlert';
import { capturedLabel, isOlderThan } from '@/lib/marketIntel';
import RegimeCard from './RegimeCard';
import BreadthCard from './BreadthCard';
import ReturnsCard from './ReturnsCard';
import SectorLeadershipCard from './SectorLeadershipCard';

/**
 * Market Overview v2: regime, breadth, index-vs-typical-stock returns and sector leadership from the newest OBSERVED
 * market-intelligence snapshot (captured by the daily run for that session). It states the session, provenance,
 * capture time and coverage; when the snapshot is missing, unavailable or older than the newest price session it says
 * so instead of showing anything else.
 */
export default function MarketIntelligencePanel({ priceDate }: { priceDate?: string | null }) {
  const [snap, setSnap] = useState<MarketSnapshot | null>(null);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [sessions, setSessions] = useState<number | null>(null);
  const [firstSession, setFirstSession] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSnap(await marketIntelligenceApi.getSnapshot());
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
    try {
      const h = await marketIntelligenceApi.getHistory(250);
      const obs = (h.sessions ?? []).filter((s) => s.provenance === 'observed');
      setSessions(h.available ? obs.length : null);
      setFirstSession(obs.length ? obs[obs.length - 1].session_date : null);
    } catch {
      setSessions(null);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (loading && !snap) {
    return (
      <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2" aria-busy="true">
        {[0, 1].map((i) => (
          <div key={i} className="h-64 animate-pulse rounded-2xl bg-black/[0.04]" />
        ))}
      </div>
    );
  }
  if (failed && !snap) {
    return (
      <div className="mb-6">
        <ErrorAlert title="Market intelligence unavailable" message="Couldn't load the market snapshot." onRetry={load} />
      </div>
    );
  }
  if (!snap) return null;
  if (!snap.available) {
    return (
      <div className="mb-6 rounded-2xl border border-black/[0.05] bg-white p-5 text-sm text-slate-500 shadow-sm" data-testid="mi-unavailable">
        Market intelligence is not available{snap.message ? `: ${snap.message.replace(/\.\s*$/, '')}` : ` (${snap.reason})`}.
      </div>
    );
  }

  const stale = isOlderThan(snap.session_date, priceDate);
  return (
    <section className="mb-6" aria-label="Market intelligence">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 px-1">
        <DataStamp
          label="Market snapshot for session"
          asOf={snap.session_date}
          provenance={snap.provenance}
          source={capturedLabel(snap.meta.captured_at) ?? undefined}
          stale={stale === true}
        />
        <p className="text-xs text-slate-400">
          {sessions != null && firstSession ? `${sessions} observed session${sessions === 1 ? '' : 's'} since ${firstSession}` : ''}
        </p>
      </div>
      {stale === true && (
        <p className="mb-3 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800" data-testid="mi-stale">
          This snapshot is for {snap.session_date}; prices on this page go to {priceDate}. The newer session has not been captured yet.
        </p>
      )}
      {snap.provenance_note && <p className="mb-3 rounded-xl bg-slate-100 px-3 py-2 text-xs text-slate-600">{snap.provenance_note}</p>}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <RegimeCard snap={snap} />
        <div className="flex flex-col gap-4">
          <BreadthCard snap={snap} />
          <ReturnsCard snap={snap} />
        </div>
        <div className="lg:col-span-2">
          <SectorLeadershipCard snap={snap} />
        </div>
      </div>
    </section>
  );
}
