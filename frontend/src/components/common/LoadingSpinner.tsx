import React from 'react';
import { clsx } from 'clsx';

interface LoadingSpinnerProps {
  size?: 'small' | 'medium' | 'large';
  className?: string;
}

const SPOKES = 8;

/** An activity indicator in the system style: eight spokes fading around a circle, stepping rather than spinning smoothly. */
export default function LoadingSpinner({ size = 'medium', className }: LoadingSpinnerProps) {
  return (
    <div className={clsx('flex justify-center items-center', className)} role="status" aria-label="Loading">
      <svg
        viewBox="0 0 24 24"
        className={clsx(
          'animate-spin [animation-duration:0.8s] [animation-timing-function:steps(8)] text-slate-500',
          size === 'small' && 'h-4 w-4',
          size === 'medium' && 'h-7 w-7',
          size === 'large' && 'h-10 w-10'
        )}
      >
        {Array.from({ length: SPOKES }, (_, i) => (
          <rect
            key={i}
            x="11"
            y="2"
            width="2"
            height="6"
            rx="1"
            fill="currentColor"
            opacity={0.15 + (0.85 * (i + 1)) / SPOKES}
            transform={`rotate(${(360 / SPOKES) * i} 12 12)`}
          />
        ))}
      </svg>
    </div>
  );
}
