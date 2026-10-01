'use client';

// File: frontend/src/app/strategies/page.tsx
// /strategies: the Quant Lab control center for the default (tracking) strategy, with a selector for the others.
// /strategies/[strategyKey] pins one. Both render the same StrategyWorkspace.
import React from 'react';
import StrategyWorkspace from '@/components/strategies/StrategyWorkspace';

export default function StrategiesPage() {
  return <StrategyWorkspace />;
}
