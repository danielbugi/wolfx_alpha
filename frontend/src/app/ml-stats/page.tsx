'use client';

import React, { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { Button } from '@nextui-org/react';
import { mlStatsApi, MLStatsReport } from '@/services/api';
import MLStatsPanel from '@/components/health/MLStatsPanel';
import { usePagePerf } from '@/hooks/usePagePerf';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ErrorAlert from '@/components/common/ErrorAlert';

export default function MLStatsPage() {
  const { markLoaded } = usePagePerf('/ml-stats');
  const [report, setReport] = useState<MLStatsReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchReport = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await mlStatsApi.getReport();
      setReport(data);
      markLoaded();
    } catch {
      setError('Failed to load ML stats — is the backend running?');
    } finally {
      setLoading(false);
    }
  }, [markLoaded]);

  useEffect(() => {
    fetchReport();
  }, [fetchReport]);

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto px-4 pb-12 pt-6 sm:px-6 lg:px-8 max-w-5xl space-y-4">
        <div className="px-1 pt-2 pb-1 flex items-center justify-between">
          <div>
            <h1 className="large-title">ML Stats</h1>
            <p className="mt-1.5 max-w-3xl text-[15px] leading-relaxed text-slate-500">
              Model accuracy, what it&apos;s actually learning from, and how its live predictions have played
              out. For data-pipeline correctness, see{' '}
              <Link href="/system-health" className="text-accent hover:underline">System Health</Link>.
            </p>
            {report && (
              <p className="text-xs text-slate-400 mt-2">
                Last checked {new Date(report.generated_at).toLocaleTimeString()}
              </p>
            )}
          </div>
          <Button size="sm" className="rounded-full bg-slate-900 px-4 font-medium text-white shrink-0" onClick={fetchReport} isLoading={loading}>
            Refresh
          </Button>
        </div>

        {loading && !report ? (
          <div className="flex justify-center py-16"><LoadingSpinner /></div>
        ) : error ? (
          <ErrorAlert title="Couldn’t load ML stats" message={error} onRetry={fetchReport} className="my-4" />
        ) : report ? (
          <MLStatsPanel report={report} />
        ) : null}
      </div>
    </div>
  );
}
