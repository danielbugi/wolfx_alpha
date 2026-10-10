'use client';

import React, { useId } from 'react';
import { motion } from 'framer-motion';
import { clsx } from 'clsx';

export interface SegmentOption<T extends string> {
  value: T;
  label: React.ReactNode;
}

/**
 * A segmented control: mutually exclusive options on a recessed track, with one raised thumb that springs to the
 * selected segment (critically damped, so it settles without overshoot and can be re-targeted mid-flight).
 */
export default function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  size = 'sm',
  className,
  'aria-label': ariaLabel,
}: {
  options: SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
  size?: 'sm' | 'md';
  className?: string;
  'aria-label'?: string;
}) {
  const layoutId = useId();
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={clsx('inline-flex shrink-0 items-center rounded-[10px] bg-black/[0.06] p-[2px]', className)}
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(option.value)}
            className={clsx(
              'relative whitespace-nowrap rounded-lg font-medium outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-accent/50',
              size === 'sm' ? 'px-3 py-1 text-xs' : 'px-4 py-1.5 text-[13px]',
              selected ? 'text-slate-900' : 'text-slate-600 hover:text-slate-900',
            )}
          >
            {selected && (
              <motion.span
                layoutId={`segment-thumb-${layoutId}`}
                aria-hidden
                className="absolute inset-0 rounded-lg bg-white shadow-[0_1px_3px_rgb(0_0_0/0.1),0_0_0_0.5px_rgb(0_0_0/0.04)]"
              />
            )}
            <span className="relative">{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}
