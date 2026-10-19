// File: frontend/src/components/charts/IndicatorsDialog.tsx
'use client';

import React, { useEffect, useRef } from 'react';
import { XMarkIcon } from '@heroicons/react/20/solid';
import { DEFAULT_INDICATORS, INDICATOR_CATALOG, IndicatorPrefs, SavedOverlay, SavedPanel } from '@/lib/chartIndicators';

export interface ExtraLayer {
  key: string;
  label: string;
  description: string;
  on: boolean;
  onToggle: () => void;
}

/**
 * The chart's indicator window: tick what to draw on the price chart and which panels to show under it. Every change
 * applies at once and is saved in this browser for all charts. `extras` are layers that belong to one stock only (shown,
 * never saved).
 */
export default function IndicatorsDialog({
  prefs,
  onChange,
  onClose,
  extras = [],
  notSaved = false,
}: {
  prefs: IndicatorPrefs;
  onChange: (p: IndicatorPrefs) => void;
  onClose: () => void;
  extras?: ExtraLayer[];
  notSaved?: boolean;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    panelRef.current?.querySelector<HTMLElement>('input')?.focus();
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const flip = <T extends string>(list: T[], key: T): T[] => (list.includes(key) ? list.filter((k) => k !== key) : [...list, key]);
  const row = (id: string, label: string, description: string, checked: boolean, onToggle: () => void) => (
    <label key={id} htmlFor={id} className="flex cursor-pointer items-start gap-3 rounded-xl px-3 py-2.5 hover:bg-black/[0.03]">
      <input id={id} type="checkbox" checked={checked} onChange={onToggle} className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer accent-slate-900" />
      <span className="min-w-0">
        <span className="block text-[14px] font-medium text-slate-900">{label}</span>
        <span className="block text-[12px] text-slate-500">{description}</span>
      </span>
    </label>
  );

  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/30 p-0 sm:items-center sm:p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="indicators-title"
        className="max-h-[85vh] w-full overflow-y-auto rounded-t-2xl bg-white shadow-xl sm:max-w-md sm:rounded-2xl"
        data-testid="indicators-dialog"
      >
        <div className="sticky top-0 flex items-center justify-between border-b border-black/[0.06] bg-white px-4 py-3">
          <h2 id="indicators-title" className="text-[16px] font-semibold text-slate-900">
            Indicators
          </h2>
          <button type="button" onClick={onClose} aria-label="Close indicators" className="press inline-flex h-8 w-8 items-center justify-center rounded-full text-slate-500 hover:bg-black/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
            <XMarkIcon className="h-5 w-5" />
          </button>
        </div>
        <div className="p-2">
          <p className="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">On the price chart</p>
          {INDICATOR_CATALOG.overlays.map((o) =>
            row(`ind-${o.key}`, o.label, o.description, prefs.overlays.includes(o.key), () => onChange({ ...prefs, overlays: flip<SavedOverlay>(prefs.overlays, o.key) })),
          )}
          <p className="px-3 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Below the chart</p>
          {INDICATOR_CATALOG.panels.map((p) =>
            row(`ind-${p.key}`, p.label, p.description, prefs.panels.includes(p.key), () => onChange({ ...prefs, panels: flip<SavedPanel>(prefs.panels, p.key) })),
          )}
          {extras.length > 0 && (
            <>
              <p className="px-3 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-slate-400">This stock only (not saved)</p>
              {extras.map((x) => row(`ind-x-${x.key}`, x.label, x.description, x.on, x.onToggle))}
            </>
          )}
        </div>
        <div className="sticky bottom-0 flex items-center justify-between gap-2 border-t border-black/[0.06] bg-white px-4 py-3">
          <button type="button" onClick={() => onChange(DEFAULT_INDICATORS)} className="press rounded-lg px-2 py-1.5 text-[13px] text-slate-600 hover:bg-black/[0.04]">
            Reset to defaults
          </button>
          <div className="flex items-center gap-3">
            <span className="text-[11px] text-slate-400" role="status">
              {notSaved ? 'Applied, but this browser is not saving it' : 'Saved for all charts in this browser'}
            </span>
            <button type="button" onClick={onClose} className="press rounded-lg bg-slate-900 px-4 py-1.5 text-[13px] font-medium text-white">
              Done
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
