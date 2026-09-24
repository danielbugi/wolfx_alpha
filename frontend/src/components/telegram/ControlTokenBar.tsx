// File: frontend/src/components/telegram/ControlTokenBar.tsx
import React, { useState } from 'react';
import { KeyIcon, LockClosedIcon, LockOpenIcon } from '@heroicons/react/24/outline';
import { Btn } from '@/components/telegram/ui';

interface Props {
  controlsEnabled: boolean;
  token: string | null;
  onSubmit: (token: string) => void;
  onClear: () => void;
}

/** Edit and delete need the token from .env (TELEGRAM_CONTROL_TOKEN). Without it the page is a read-only view. */
export default function ControlTokenBar({ controlsEnabled, token, onSubmit, onClear }: Props) {
  const [draft, setDraft] = useState('');

  if (!controlsEnabled) {
    return (
      <p className="flex items-center gap-2 text-sm text-amber-800" role="status">
        <LockClosedIcon className="h-4 w-4 shrink-0" />
        Read-only: the server has no <span className="font-mono">TELEGRAM_CONTROL_TOKEN</span>, so edit and delete are switched off.
      </p>
    );
  }
  if (token) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="flex items-center gap-2 text-sm text-emerald-800" role="status">
          <LockOpenIcon className="h-4 w-4 shrink-0" />
          Edit and delete are unlocked for this browser tab.
        </p>
        <Btn size="sm" onClick={onClear}>
          Lock again
        </Btn>
      </div>
    );
  }
  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (draft.trim()) {
          onSubmit(draft);
          setDraft('');
        }
      }}
    >
      <KeyIcon className="h-4 w-4 shrink-0 text-slate-500" aria-hidden />
      <label htmlFor="control-token" className="text-sm text-slate-600">
        View only. Enter the control token to edit or delete:
      </label>
      <input
        id="control-token"
        type="password"
        autoComplete="off"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="TELEGRAM_CONTROL_TOKEN"
        className="w-64 max-w-full rounded-md border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm focus:bg-white focus:outline-none focus:ring-2 focus:ring-slate-300"
      />
      <Btn type="submit" variant="primary" size="sm" disabled={!draft.trim()}>
        Unlock
      </Btn>
    </form>
  );
}
