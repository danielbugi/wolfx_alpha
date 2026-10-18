// File: frontend/src/components/common/DataStamp.tsx
'use client';

import React from 'react';
import { clsx } from 'clsx';

export type Provenance = 'observed' | 'reconstructed';

export interface DataStampProps {
  /** The market session or date the data describes (as delivered by the API). Missing -> "unavailable", never a guess. */
  asOf?: string | null;
  /** What the date refers to; default "Data as of". */
  label?: string;
  /** Where the figures come from, e.g. "end-of-day prices". Optional; shown only when given. */
  source?: string;
  /** observed = captured for that session at the time; reconstructed = recomputed later from stored history. */
  provenance?: Provenance | null;
  /** The caller's own judgement that the data is older than expected (e.g. a failed refresh). */
  stale?: boolean;
  className?: string;
}

/**
 * One consistent "how fresh is this, and what is it" line for product cards. It only states what it is given: a
 * missing date reads "unavailable", an unknown provenance is simply not shown, and nothing is inferred from the clock.
 */
export default function DataStamp({ asOf, label = 'Data as of', source, provenance, stale, className }: DataStampProps) {
  const parts: React.ReactNode[] = [
    <span key="asof">
      {label}{' '}
      <span className={clsx('font-medium tabular-nums', asOf ? 'text-slate-600' : 'text-slate-400')}>{asOf || 'unavailable'}</span>
    </span>,
  ];
  if (source) parts.push(<span key="source">{source}</span>);
  if (provenance)
    parts.push(
      <span
        key="prov"
        title={provenance === 'observed' ? 'Captured for this session at the time' : 'Recomputed later from stored history; not a record of what was known then'}
      >
        {provenance}
      </span>,
    );
  return (
    <p className={clsx('flex flex-wrap items-center gap-x-1.5 text-xs text-slate-400', className)} data-testid="data-stamp">
      {parts.map((p, i) => (
        <React.Fragment key={i}>
          {i > 0 && <span aria-hidden>·</span>}
          {p}
        </React.Fragment>
      ))}
      {stale && (
        <span className="ml-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-800">may be out of date</span>
      )}
    </p>
  );
}
