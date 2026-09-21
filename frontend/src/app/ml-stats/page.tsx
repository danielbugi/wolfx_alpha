'use client';

import React, { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { Spinner, Button } from '@nextui-org/react';
import { mlStatsApi, MLStatsReport } from '@/services/api';
import MLStatsPanel from '@/components/health/MLStatsPanel';
import { usePagePerf } from '@/hooks/usePagePerf';

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
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-cyan-50">
      <div className="container mx-auto p-4 max-w-5xl space-y-4">
        <div className="p-4 bg-white rounded-lg shadow-sm border border-slate-200 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-800">ML Stats</h1>
            <p className="text-slate-600 text-sm mt-1">
              Model accuracy, what it&apos;s actually learning from, and how its live predictions have played
              out. For data-pipeline correctness, see{' '}
              <Link href="/system-health" className="text-cyan-700 hover:underline">System Health</Link>.
            </p>
            {report && (
              <p className="text-xs text-slate-400 mt-2">
                Last checked {new Date(report.generated_at).toLocaleTimeString()}
              </p>
            )}
          </div>
          <Button size="sm" className="bg-slate-700 text-white shrink-0" onClick={fetchReport} isLoading={loading}>
            Refresh
          </Button>
        </div>

        {loading && !report ? (
          <div className="flex justify-center py-16"><Spinner color="primary" /></div>
        ) : error ? (
          <p className="text-center text-red-500 text-sm py-16">{error}</p>
        ) : report ? (
          <MLStatsPanel report={report} />
        ) : null}
      </div>
    </div>
  );
}
