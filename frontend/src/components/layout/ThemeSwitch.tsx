'use client';

import React from 'react';
import { ComputerDesktopIcon, MoonIcon, SunIcon } from '@heroicons/react/20/solid';
import { clsx } from 'clsx';
import SegmentedControl from '@/components/ui/SegmentedControl';
import { ThemePreference, useThemePreference } from '@/lib/theme';

const OPTIONS: { value: ThemePreference; label: string; Icon: typeof SunIcon }[] = [
  { value: 'light', label: 'Light', Icon: SunIcon },
  { value: 'dark', label: 'Dark', Icon: MoonIcon },
  { value: 'system', label: 'Auto', Icon: ComputerDesktopIcon },
];

/** Light / Dark / Auto (follow the OS). `compact` shows icons only, for the collapsed sidebar rail. */
export default function ThemeSwitch({ compact = false, className }: { compact?: boolean; className?: string }) {
  const { preference, setPreference } = useThemePreference();

  if (compact) {
    // One button that cycles, since three segments don't fit a 68px rail.
    const index = OPTIONS.findIndex((o) => o.value === preference);
    const current = OPTIONS[index];
    const next = OPTIONS[(index + 1) % OPTIONS.length];
    return (
      <button
        type="button"
        title={`Appearance: ${current.label} (switch to ${next.label})`}
        onClick={() => setPreference(next.value)}
        className={clsx('press flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 hover:bg-black/[0.05] hover:text-slate-800', className)}
      >
        <current.Icon className="h-4 w-4" />
      </button>
    );
  }

  return (
    <SegmentedControl
      aria-label="Appearance"
      className={clsx('w-full [&>button]:flex-1', className)}
      options={OPTIONS.map(({ value, label, Icon }) => ({
        value,
        label: (
          <span className="inline-flex items-center justify-center gap-1">
            <Icon className="h-3.5 w-3.5" />
            {label}
          </span>
        ),
      }))}
      value={preference}
      onChange={setPreference}
    />
  );
}
