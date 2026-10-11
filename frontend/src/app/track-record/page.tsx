'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { Button } from '@nextui-org/react';
import { trackRecordApi, TrackRecordSummary, TrackRecordSignal } from '@/services/api';
import TrackRecordPanel from '@/components/dashboard/TrackRecordPanel';
import { usePagePerf } from '@/hooks/usePagePerf';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ErrorAlert from '@/components/common/ErrorAlert';

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
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto px-4 pb-12 pt-6 sm:px-6 lg:px-8 max-w-5xl space-y-4">
        <div className="px-1 pt-2 pb-1 flex items-center justify-between">
          <div>
            <h1 className="large-title">Track Record</h1>
            <p className="mt-1.5 max-w-3xl text-[15px] leading-relaxed text-slate-500">
              Every daily breakout signal, tracked forward to a real result and shown back unfiltered.
              Disclosed history, not a forecast — see the note on each stat below.
            </p>
          </div>
          <Button size="sm" className="rounded-full bg-slate-900 px-4 font-medium text-white shrink-0" onClick={fetchData} isLoading={loading}>
            Refresh
          </Button>
        </div>

        {loading && !summary ? (
          <div className="flex justify-center py-16"><LoadingSpinner /></div>
        ) : error ? (
          <ErrorAlert title="Couldn’t load the track record" message={error} onRetry={fetchData} className="my-4" />
        ) : summary ? (
          <TrackRecordPanel summary={summary} signals={signals} />
        ) : null}
      </div>
    </div>
  );
}
