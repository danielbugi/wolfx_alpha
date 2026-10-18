// File: frontend/src/components/sectors/MembershipNote.tsx
import React from 'react';
import { DEFAULT_MEMBERSHIP, MembershipInfo } from '@/lib/sectors';

/** The current-membership caveat for every figure built from today's sector assignment (windows, index, industries). */
export default function MembershipNote({ membership }: { membership?: MembershipInfo | null }) {
  const m = membership ?? DEFAULT_MEMBERSHIP;
  return (
    <p className="rounded-xl bg-slate-100 px-3 py-2 text-xs leading-relaxed text-slate-600" data-testid="membership-note">
      <span className="font-semibold">Current membership{m.point_in_time ? '' : ', not point-in-time'}.</span> {m.note}{' '}
      <span className="text-slate-400">Source: {m.source}.</span>
    </p>
  );
}
