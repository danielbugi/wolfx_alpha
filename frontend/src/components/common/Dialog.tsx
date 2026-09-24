// File: frontend/src/components/common/Dialog.tsx
'use client';

import React, { useEffect, useRef } from 'react';

/**
 * A modal dialog on the native <dialog> element: the browser traps focus inside it, closes it on Escape, makes the page behind it inert and
 * draws it in the top layer. Plain Tailwind on purpose - this app does not load NextUI's Tailwind plugin, so NextUI's Modal renders unstyled here.
 *
 * `variant="drawer-left"` reuses the same element for a slide-over panel (the mobile nav drawer): the dialog's own box becomes the panel itself,
 * flush to the left edge and full height, while `::backdrop` covers the rest of the screen — clicking it still closes the dialog, because a
 * `::backdrop` click's target is the `<dialog>` element, exactly like clicking outside the centered panel does for the default variant.
 */
const VARIANT_CLASS: Record<'center' | 'drawer-left', string> = {
  center:
    'm-auto max-h-[90vh] w-[min(64rem,calc(100vw-1.5rem))] rounded-xl open:flex open:flex-col',
  'drawer-left':
    'inset-y-0 left-0 m-0 h-dvh max-h-none w-72 max-w-[85vw] rounded-none rounded-r-xl open:flex open:flex-col',
};

interface DialogProps {
  open: boolean;
  onClose: () => void;
  /** id of the element that titles the dialog (for screen readers). */
  labelledBy: string;
  /** false while something is in flight: Escape and a click on the backdrop do nothing. */
  dismissable?: boolean;
  /** 'center' (default) = a centered panel, e.g. the Telegram message editor. 'drawer-left' = a full-height slide-over from the left edge, e.g. the mobile nav. */
  variant?: 'center' | 'drawer-left';
  children: React.ReactNode;
}

export default function Dialog({ open, onClose, labelledBy, dismissable = true, variant = 'center', children }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const pressStartedOnBackdrop = useRef(false);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';               // the page must not scroll behind the dialog
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={labelledBy}
      onClose={onClose}
      onCancel={(e) => {
        if (!dismissable) e.preventDefault();
      }}
      onMouseDown={(e) => {
        pressStartedOnBackdrop.current = e.target === ref.current;   // the dialog has no padding of its own, so its own surface IS the backdrop
      }}
      onClick={(e) => {
        // closes only when the press began AND ended on the backdrop: selecting text in a field and releasing outside must not discard the work
        if (dismissable && pressStartedOnBackdrop.current && e.target === ref.current) onClose();
        pressStartedOnBackdrop.current = false;
      }}
      className={`bg-white p-0 text-slate-800 shadow-2xl backdrop:bg-slate-900/50 ${VARIANT_CLASS[variant]}`}
    >
      {open && children}
    </dialog>
  );
}
