'use client';

import React from 'react';
import { MotionConfig } from 'framer-motion';

/**
 * App-wide motion defaults. `reducedMotion="user"` makes every framer-motion animation honor the OS
 * "reduce motion" setting (transforms are skipped, opacity still fades). The default transition is a
 * critically damped spring — no overshoot — which is what non-momentum UI (indicators, toggles) wants.
 */
export const SPRING = { type: 'spring', bounce: 0, duration: 0.35 } as const;

export default function MotionProvider({ children }: { children: React.ReactNode }) {
  return (
    <MotionConfig reducedMotion="user" transition={SPRING}>
      {children}
    </MotionConfig>
  );
}
