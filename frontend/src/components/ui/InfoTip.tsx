// File: frontend/src/components/ui/InfoTip.tsx
'use client';

import React, { useEffect, useId, useRef, useState } from 'react';
import { InformationCircleIcon } from '@heroicons/react/20/solid';

const WIDTH = 280;

/**
 * A small "i" button that explains a metric in plain language. A native `title` never shows on touch screens and is
 * hidden by the sort button's own title in table headers, so this opens on tap/click/Enter and closes on Escape or a
 * click elsewhere. The bubble is positioned against the viewport (fixed), so a horizontally scrolling table cannot clip
 * it; it follows its button on scroll and resize (focusing the button can scroll it into view) and closes once the
 * button leaves the screen.
 */
export default function InfoTip({ label, text }: { label: string; text: string }) {
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const id = useId();

  const place = () => {
    const r = btn.current?.getBoundingClientRect();
    if (!r || r.bottom < 0 || r.top > window.innerHeight || r.right < 0 || r.left > window.innerWidth) return null;
    return { top: r.bottom + 6, left: Math.max(8, Math.min(r.left + r.width / 2 - WIDTH / 2, window.innerWidth - WIDTH - 8)) };
  };

  const open = pos != null;
  useEffect(() => {
    if (!open) return;
    const close = () => setPos(null);
    const follow = () => setPos(place());
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close();
    const onDown = (e: MouseEvent) => !btn.current?.contains(e.target as Node) && close();
    window.addEventListener('keydown', onKey);
    window.addEventListener('mousedown', onDown);
    window.addEventListener('scroll', follow, true);
    window.addEventListener('resize', follow);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('scroll', follow, true);
      window.removeEventListener('resize', follow);
    };
  }, [open]);

  const toggle = (e: React.MouseEvent) => {
    e.stopPropagation(); // inside a sortable header: explaining a column must not sort it
    setPos(open ? null : place());
  };

  return (
    <>
      <button
        ref={btn}
        type="button"
        onClick={toggle}
        aria-label={`About ${label}`}
        aria-expanded={pos != null}
        aria-describedby={pos ? id : undefined}
        className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full align-middle text-slate-400 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        data-testid="info-tip"
      >
        <InformationCircleIcon className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
      {pos && (
        <span
          id={id}
          role="tooltip"
          className="fixed z-50 rounded-xl bg-white px-3 py-2 text-left text-[12px] font-normal normal-case leading-snug tracking-normal text-slate-700 shadow-lg ring-1 ring-black/[0.06]"
          style={{ top: pos.top, left: pos.left, width: WIDTH }}
        >
          <span className="block font-semibold text-slate-900">{label}</span>
          {text}
        </span>
      )}
    </>
  );
}
