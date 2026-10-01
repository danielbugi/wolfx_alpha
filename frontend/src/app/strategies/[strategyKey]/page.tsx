'use client';

// File: frontend/src/app/strategies/[strategyKey]/page.tsx
// /strategies/:strategyKey?version=&tab=&candidate= -- the same workspace pinned to one registered strategy.
import React from 'react';
import { useParams, useRouter } from 'next/navigation';
import StrategyWorkspace from '@/components/strategies/StrategyWorkspace';

export default function StrategyPage() {
  const params = useParams<{ strategyKey: string }>();
  const router = useRouter();
  const raw = params?.strategyKey ?? '';
  let key = raw;
  try { key = decodeURIComponent(raw); } catch { /* keep the raw segment */ }
  return (
    <StrategyWorkspace
      pinnedKey={key}
      onNavigateStrategy={(s) => router.push(`/strategies/${encodeURIComponent(s.key)}?version=${encodeURIComponent(s.version)}`)}
    />
  );
}
