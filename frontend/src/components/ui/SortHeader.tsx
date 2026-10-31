'use client';

import React from 'react';
import { ChevronDownIcon, ChevronUpIcon } from '@heroicons/react/20/solid';
import { clsx } from 'clsx';

/**
 * A clickable column title. Rendered inside a table header cell; shows a chevron for the active sort and a faint
 * one on hover for the others, so it's discoverable without cluttering every header.
 */
export default function SortHeader({
  label,
  active,
  dir,
  onClick,
  align = 'start',
}: {
  label: string;
  active: boolean;
  dir: 'asc' | 'desc' | null;
  onClick: () => void;
  align?: 'start' | 'end';
}) {
  const Icon = active && dir === 'asc' ? ChevronUpIcon : ChevronDownIcon;
  return (
    <button
      type="button"
      onClick={onClick}
      title={`Sort by ${label.toLowerCase()}`}
      className={clsx(
        `group/sort relative inline-flex items-center gap-0.5 uppercase tracking-[0.02em] hover:text-slate-800 after:absolute after:-inset-y-2 after:inset-x-0 after:content-['']`,
        align === 'end' && 'flex-row-reverse',
        active && 'text-slate-800',
      )}
    >
      {label}
      {active && <span className="sr-only">{dir === 'asc' ? ', sorted ascending' : ', sorted descending'}</span>}
      <Icon
        aria-hidden
        className={clsx('h-3.5 w-3.5 shrink-0 transition-opacity', active ? 'opacity-100' : 'opacity-0 group-hover/sort:opacity-40')}
      />
    </button>
  );
}
