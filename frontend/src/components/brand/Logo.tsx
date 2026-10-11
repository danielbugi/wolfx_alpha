import React, { useId } from 'react';
import { clsx } from 'clsx';

/** The First Light mark: a sun breaking the horizon, on a dawn-gradient tile. */
export function LogoMark({ className }: { className?: string }) {
  const gradientId = useId();
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={clsx('shrink-0', className)}>
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ff9f0a" />
          <stop offset="100%" stopColor="#ff375f" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill={`url(#${gradientId})`} />
      <path d="M9 20a7 7 0 0 1 14 0Z" fill="#fff" />
      <path d="M6 20h20" stroke="#fff" strokeWidth="2" strokeLinecap="round" />
      <path d="M16 7.5v2.5M8.6 10.6l1.7 1.7M23.4 10.6l-1.7 1.7" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" opacity="0.85" />
    </svg>
  );
}

export default function Logo({ collapsed = false, className }: { collapsed?: boolean; className?: string }) {
  return (
    <span className={clsx('flex min-w-0 items-center gap-2.5', className)}>
      <LogoMark className="h-7 w-7" />
      {!collapsed && <span className="truncate text-[15px] font-semibold tracking-[-0.01em] text-slate-900">First Light</span>}
    </span>
  );
}
