// File: frontend/src/components/common/LoadingSpinner.tsx
import React from 'react';
import { clsx } from 'clsx';

interface LoadingSpinnerProps {
  size?: 'small' | 'medium' | 'large';
  className?: string;
}

export default function LoadingSpinner({ size = 'medium', className }: LoadingSpinnerProps) {
  return (
    <div className={clsx('flex justify-center items-center', className)}>
      <div
        className={clsx(
          'animate-spin rounded-full border-t-2 border-b-2 border-blue-500',
          size === 'small' && 'h-4 w-4',
          size === 'medium' && 'h-8 w-8',
          size === 'large' && 'h-12 w-12'
        )}
      />
    </div>
  );
}



