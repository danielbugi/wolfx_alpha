'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { Spinner, Button } from '@nextui-org/react';
import { trackRecordApi, TrackRecordSummary, TrackRecordSignal } from '@/services/api';
import TrackRecordPanel from '@/components/dashboard/TrackRecordPanel';
import { usePagePerf } from '@/hooks/usePagePerf';

export default function TrackRecordPage() {
  const { markLoaded } = usePagePerf('/track-record');
  const [summary, setSummary] = useState<TrackRecordSummary | null>(null);
  const [signals, setSignals] = useState<TrackRecordSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const [summaryData, signalsData] = await Promise.all([
        trackRecordApi.getSummary(),
        trackRecordApi.getSignals({ limit: 100 }),
      ]);
      setSummary(summaryData);
      setSignals(signalsData);
      markLoaded();
    } catch {
      setError('Failed to load the track record — is the backend running?');
    } finally {
      setLoading(false);
    }
  }, [markLoaded]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-cyan-50">
      <div className="container mx-auto p-4 max-w-5xl space-y-4">
        <div className="p-4 bg-white rounded-lg shadow-sm border border-slate-200 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">Track Record</h1>
            <p className="text-slate-600 text-sm mt-1">
              Every daily breakout signal, tracked forward to a real result and shown back unfiltered.
              Disclosed history, not a forecast — see the note on each stat below.
            </p>
          </div>
          <Button size="sm" className="bg-slate-700 text-white shrink-0" onClick={fetchData} isLoading={loading}>
            Refresh
          </Button>
        </div>

        {loading && !summary ? (
          <div className="flex justify-center py-16"><Spinner color="primary" /></div>
        ) : error ? (
          <p className="text-center text-red-500 text-sm py-16">{error}</p>
        ) : summary ? (
          <TrackRecordPanel summary={summary} signals={signals} />
        ) : null}
      </div>
    </div>
  );
}
