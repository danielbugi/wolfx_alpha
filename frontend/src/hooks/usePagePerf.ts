// File: frontend/src/hooks/usePagePerf.ts
'use client';

import { useCallback, useRef } from 'react';
import { performanceApi } from '@/services/api';

/**
 * Measures "time from this page mounting to its data actually being on
 * screen" — the number that matters for perceived route load speed, and one
 * that can only be measured from inside each page (a generic route-change
 * listener can't see into an individual page's own async fetch). Call
 * `markLoaded()` once, right after the first successful data fetch resolves.
 *
 * Reports only the FIRST successful load per mount — a manual "Refresh"
 * click afterwards is a different thing (repeat-visit latency, already
 * covered by the backend endpoint's own tracked latency) and would only add
 * noise to "how long did this route take to first show data."
 */
export function usePagePerf(routeLabel: string) {
  const mountedAt = useRef(performance.now());
  const reported = useRef(false);

  const markLoaded = useCallback(() => {
    if (reported.current) return;
    reported.current = true;
    const durationMs = performance.now() - mountedAt.current;
    performanceApi.reportClientMetric(routeLabel, 'data_load_ms', durationMs);
  }, [routeLabel]);

  return { markLoaded };
}
