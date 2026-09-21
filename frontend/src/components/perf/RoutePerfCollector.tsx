// File: frontend/src/components/perf/RoutePerfCollector.tsx
'use client';

import { useEffect, useRef } from 'react';
import { usePathname } from 'next/navigation';
import { performanceApi } from '@/services/api';

/**
 * Reports real browser Navigation Timing for the first hard page load this
 * session (TTFB through domContentLoaded). This is deliberately the only
 * thing this component measures — per-route "time until data is actually on
 * screen" is a different, more meaningful number for a client-rendered SPA,
 * and is reported per-page instead via the `usePagePerf` hook (see
 * frontend/src/hooks/usePagePerf.ts), because it can only be measured from
 * inside each page's own fetch lifecycle, not generically here.
 */
export default function RoutePerfCollector() {
  const pathname = usePathname();
  const hasReportedInitialLoad = useRef(false);

  useEffect(() => {
    if (hasReportedInitialLoad.current) return;
    hasReportedInitialLoad.current = true;
    try {
      const [nav] = performance.getEntriesByType('navigation') as PerformanceNavigationTiming[];
      if (nav) {
        const loadMs = nav.domContentLoadedEventEnd - nav.startTime;
        if (loadMs > 0) {
          performanceApi.reportClientMetric(pathname, 'initial_load_ms', loadMs);
        }
      }
    } catch {
      /* Navigation Timing unsupported — skip, not critical */
    }
  }, [pathname]);

  return null;
}
