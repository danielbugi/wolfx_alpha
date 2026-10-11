import React from 'react';
import { clsx } from 'clsx';

/** A grey placeholder block in the shape of content that hasn't arrived yet. Pulses opacity only (no movement). */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={clsx('animate-pulse rounded-lg bg-black/[0.06]', className)} />;
}

/** The dashboard's layout in placeholder form, shown until its first data lands. */
export function DashboardSkeleton() {
  return (
    <div className="container mx-auto max-w-7xl px-4 pb-12 pt-6 sm:px-6 lg:px-8" role="status" aria-label="Loading dashboard">
      <Skeleton className="mb-4 h-[84px] rounded-2xl" />
      <div className="mb-6 px-1 pt-2">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="mt-3 h-4 w-80 max-w-full" />
      </div>
      <div className="mb-6 grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="rounded-2xl border border-black/[0.05] bg-white p-5 shadow-sm">
            <Skeleton className="h-3.5 w-24" />
            <Skeleton className="mt-3 h-7 w-20" />
          </div>
        ))}
      </div>
      <div className="mb-6 rounded-2xl border border-black/[0.05] bg-white p-5 shadow-sm">
        <Skeleton className="h-5 w-52" />
        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-20 rounded-xl" />
          ))}
        </div>
        <Skeleton className="mt-5 h-64 rounded-xl" />
      </div>
      <div className="rounded-2xl border border-black/[0.05] bg-white p-5 shadow-sm">
        <Skeleton className="h-5 w-40" />
        <div className="mt-5 space-y-3">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-6" />
          ))}
        </div>
      </div>
    </div>
  );
}
