'use client';

import React, { useId, useState } from 'react';
import { CheckIcon, ChevronUpDownIcon } from '@heroicons/react/20/solid';
import { clsx } from 'clsx';
import { useOutsideClick } from '@/hooks/useSymbolSearch';

/**
 * Plain form controls. NextUI's Input/Select need the NextUI Tailwind plugin, which this app does not load, so they
 * rendered with their floating label drawn on top of the value. These keep the label above the control instead.
 */

const CONTROL =
  'w-full rounded-lg border border-slate-300 bg-white px-3 py-[7px] text-[13px] text-slate-900 shadow-xs transition-[border-color,box-shadow] duration-150 placeholder:text-slate-400 hover:border-slate-400 focus:border-accent focus:outline-none focus:ring-[3px] focus:ring-accent/20 disabled:cursor-not-allowed disabled:opacity-50';

function Label({ htmlFor, children }: { htmlFor?: string; children: React.ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="mb-1 block text-xs font-medium text-slate-600">
      {children}
    </label>
  );
}

export function TextField({
  label,
  className,
  suffix,
  ...input
}: { label: React.ReactNode; suffix?: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <div className={className}>
      <Label htmlFor={id}>{label}</Label>
      <div className="relative">
        <input id={id} {...input} className={clsx(CONTROL, 'tabular-nums', suffix && 'pr-8')} />
        {suffix && (
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-400">{suffix}</span>
        )}
      </div>
    </div>
  );
}

export function SelectField<T extends string>({
  label,
  value,
  onChange,
  options,
  className,
}: {
  label: React.ReactNode;
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string }[];
  className?: string;
}) {
  const id = useId();
  return (
    <div className={className}>
      <Label htmlFor={id}>{label}</Label>
      <div className="relative">
        <select id={id} value={value} onChange={(e) => onChange(e.target.value as T)} className={clsx(CONTROL, 'appearance-none pr-8')}>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <ChevronUpDownIcon className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
      </div>
    </div>
  );
}

/** A checklist in a popover: the trigger summarises the selection, the panel toggles items without closing. */
export function MultiSelect({
  label,
  options,
  selected,
  onChange,
  placeholder = 'Any',
  className,
}: {
  label: React.ReactNode;
  options: { value: string; label: string; hint?: string }[];
  selected: string[];
  onChange: (selected: string[]) => void;
  placeholder?: string;
  className?: string;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const ref = useOutsideClick(() => setOpen(false));
  const summary =
    selected.length === 0
      ? placeholder
      : selected.length <= 2
        ? selected.map((v) => options.find((o) => o.value === v)?.label ?? v).join(', ')
        : `${selected.length} selected`;

  const toggle = (value: string) =>
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value]);

  return (
    <div className={clsx('relative', className)} ref={ref}>
      <Label htmlFor={id}>{label}</Label>
      <button
        id={id}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}
        className={clsx(CONTROL, 'flex items-center justify-between gap-2 text-left')}
      >
        <span className={clsx('truncate', selected.length === 0 && 'text-slate-400')}>{summary}</span>
        <ChevronUpDownIcon className="h-4 w-4 shrink-0 text-slate-400" />
      </button>
      {open && (
        <div
          role="listbox"
          aria-multiselectable
          onKeyDown={(e) => e.key === 'Escape' && setOpen(false)}
          className="material-thick absolute left-0 right-0 z-20 mt-1.5 max-h-72 overflow-y-auto rounded-xl border border-black/[0.06] p-1 shadow-lg"
        >
          {selected.length > 0 && (
            <button
              type="button"
              onClick={() => onChange([])}
              className="w-full rounded-lg px-2.5 py-1.5 text-left text-xs font-medium text-accent hover:bg-black/[0.04]"
            >
              Clear selection
            </button>
          )}
          {options.map((o) => {
            const checked = selected.includes(o.value);
            return (
              <button
                key={o.value}
                type="button"
                role="option"
                aria-selected={checked}
                onClick={() => toggle(o.value)}
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-slate-800 hover:bg-black/[0.04]"
              >
                <span
                  className={clsx(
                    'flex h-4 w-4 shrink-0 items-center justify-center rounded-[5px] border',
                    checked ? 'border-accent bg-accent text-white' : 'border-slate-300 bg-white',
                  )}
                >
                  {checked && <CheckIcon className="h-3 w-3" />}
                </span>
                <span className="flex-1 truncate">{o.label}</span>
                {o.hint && <span className="text-xs tabular-nums text-slate-400">{o.hint}</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function ProgressBar({ value, className, barClassName = 'bg-slate-700' }: { value: number; className?: string; barClassName?: string }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      className={clsx('h-1.5 w-full overflow-hidden rounded-full bg-slate-100', className)}
    >
      <div className={clsx('h-full rounded-full transition-[width] duration-500', barClassName)} style={{ width: `${pct}%` }} />
    </div>
  );
}

/** A small hover/focus tooltip anchored above its trigger. */
export function HoverTip({ content, children }: { content: React.ReactNode; children: React.ReactNode }) {
  return (
    <span className="group/tip relative inline-flex" tabIndex={0}>
      {children}
      <span
        role="tooltip"
        className="pointer-events-none invisible absolute bottom-full right-0 z-30 mb-2 w-max max-w-64 origin-bottom-right scale-95 rounded-lg bg-slate-900/95 px-2.5 py-2 text-left text-[11px] font-normal leading-relaxed text-white opacity-0 shadow-lg transition-[opacity,transform] duration-150 group-hover/tip:visible group-hover/tip:scale-100 group-hover/tip:opacity-100 group-focus/tip:visible group-focus/tip:scale-100 group-focus/tip:opacity-100"
      >
        {content}
      </span>
    </span>
  );
}
