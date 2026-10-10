import React from 'react';
import { ExclamationTriangleIcon } from '@heroicons/react/24/solid';

interface ErrorAlertProps {
  title?: string;
  message: string;
  onRetry?: () => void;
  className?: string;
}

export default function ErrorAlert({
  title = 'Error',
  message,
  onRetry,
  className
}: ErrorAlertProps) {
  return (
    <div className={`rounded-xl border border-red-200/70 bg-red-50/80 p-4 ${className || ''}`} role="alert">
      <div className="flex gap-3">
        <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-red-100">
          <ExclamationTriangleIcon className="h-4 w-4 text-red-500" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-red-900">{title}</h3>
          <p className="mt-0.5 text-sm text-red-800/90">{message}</p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="press mt-3 inline-flex items-center rounded-full bg-white px-3.5 py-1.5 text-xs font-semibold text-red-700 shadow-xs ring-1 ring-red-200 hover:bg-red-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
            >
              Try Again
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
