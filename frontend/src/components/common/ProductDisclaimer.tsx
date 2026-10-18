// File: frontend/src/components/common/ProductDisclaimer.tsx
import React from 'react';

export const PRODUCT_DISCLAIMER =
  'Information only. Not investment advice, a recommendation or a forecast. End-of-day data from third-party providers; it may be delayed, revised or incomplete.';

/** The footer every product page carries (rendered once by AuthGate for product routes; see lib/navigation.ts). */
export default function ProductDisclaimer() {
  return (
    <footer className="border-t border-black/[0.06] px-4 py-5 text-center sm:px-6 lg:px-8" data-testid="product-disclaimer">
      <p className="mx-auto max-w-3xl text-xs leading-relaxed text-slate-400">{PRODUCT_DISCLAIMER}</p>
    </footer>
  );
}
