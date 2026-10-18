// File: frontend/src/app/momentum/page.tsx
'use client';

// INTERNAL page. The Momentum Board and Momentum Leaders used to sit on the dashboard; they rank breakout lists and
// mark "confirmed" names, which is signal output, so the product Overview no longer shows them. They are kept here,
// unchanged in behaviour and data (same components, same APIs), under the Internal navigation group. This page is not
// access-controlled beyond sign-in: hiding the link is a UX choice, not a security boundary.
import React, { Suspense, useCallback, useEffect, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Chip } from '@nextui-org/react';
import { momentumBoardApi, momentumLeadersApi, MomentumBoardResult, MomentumLeadersBoard } from '@/services/api';
import MomentumBoard, { BoardCategory } from '@/components/dashboard/MomentumBoard';
import MomentumLeaders from '@/components/dashboard/MomentumLeaders';
import LoadingSpinner from '@/components/common/LoadingSpinner';

/** `useSearchParams()` opts the route out of static prerendering, so Next requires it under a Suspense boundary. */
export default function MomentumPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-slate-50">
          <LoadingSpinner size="large" />
        </div>
      }
    >
      <MomentumContent />
    </Suspense>
  );
}

/** `?sector=` carries the sector picked on the Overview heatmap; it filters the board through the board API's own
 * `sector` parameter, exactly as the dashboard did before the board moved here. */
function MomentumContent() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const selectedSector = params.get('sector') || null;
  const clearSector = useCallback(() => router.replace(pathname), [router, pathname]);
  // Defaults to Breakout only (long side), exactly as the dashboard did.
  const [boardCategory, setBoardCategory] = useState<BoardCategory>('breakout');
  const [boardMinQualityGrade, setBoardMinQualityGrade] = useState<string | undefined>(undefined);
  const [boardResults, setBoardResults] = useState<MomentumBoardResult[]>([]);
  const [boardSessionDate, setBoardSessionDate] = useState<string | null>(null);
  const [boardUniverseN, setBoardUniverseN] = useState(0);
  const [boardCounts, setBoardCounts] = useState<Record<string, number>>({});
  const [boardLoading, setBoardLoading] = useState(true);
  const [boardError, setBoardError] = useState(false);

  const [leadersBoard, setLeadersBoard] = useState<MomentumLeadersBoard | null>(null);
  const [leadersSessionDate, setLeadersSessionDate] = useState<string | null>(null);
  const [leadersLoading, setLeadersLoading] = useState(true);
  const [leadersError, setLeadersError] = useState(false);

  const fetchBoard = useCallback(async () => {
    try {
      setBoardLoading(true);
      const resp = await momentumBoardApi.getBoard({
        category: boardCategory,
        min_quality_grade: boardMinQualityGrade,
        sector: selectedSector || undefined,
        limit: 50,
      });
      setBoardResults(resp.results);
      setBoardSessionDate(resp.session_date);
      setBoardUniverseN(resp.universe_n);
      setBoardCounts(resp.counts);
      setBoardError(false);
    } catch {
      setBoardResults([]);
      setBoardError(true);
    } finally {
      setBoardLoading(false);
    }
  }, [boardCategory, boardMinQualityGrade, selectedSector]);

  const fetchLeaders = useCallback(async () => {
    try {
      setLeadersLoading(true);
      const resp = await momentumLeadersApi.getLeaders();
      setLeadersBoard(resp.board);
      setLeadersSessionDate(resp.session_date);
      setLeadersError(false);
    } catch {
      setLeadersBoard(null);
      setLeadersError(true);
    } finally {
      setLeadersLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchBoard();
  }, [fetchBoard]);

  useEffect(() => {
    fetchLeaders();
  }, [fetchLeaders]);

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto max-w-7xl px-4 pb-12 pt-6 sm:px-6 lg:px-8">
        <div className="mb-6 px-1 pt-2 pb-1">
          <h1 className="large-title">Momentum Board</h1>
          <p className="mt-1.5 max-w-3xl text-[15px] leading-relaxed text-slate-500">
            Internal research view: breakout lists and leaders as produced by the pipeline. Not part of the product
            experience.
          </p>
          {selectedSector && (
            <div className="mt-3 flex items-center gap-2 text-xs text-slate-500">
              Sector filter
              <Chip size="sm" className="bg-accent text-white text-xs cursor-pointer" onClose={clearSector}>
                {selectedSector}
              </Chip>
            </div>
          )}
        </div>
        <MomentumBoard
          results={boardResults}
          loading={boardLoading}
          error={boardError}
          sessionDate={boardSessionDate}
          universeN={boardUniverseN}
          counts={boardCounts}
          category={boardCategory}
          onCategoryChange={setBoardCategory}
          minQualityGrade={boardMinQualityGrade}
          onMinQualityGradeChange={setBoardMinQualityGrade}
          onRetry={fetchBoard}
        />
        <MomentumLeaders board={leadersBoard} sessionDate={leadersSessionDate} loading={leadersLoading} error={leadersError} onRetry={fetchLeaders} />
      </div>
    </div>
  );
}
