// File: frontend/src/components/telegram/SideTabs.tsx
// A tab rail along the side of a page that mixes distinct concerns (e.g. /telegram's channel-post control vs.
// bot-access control) — a vertical stack on desktop, a horizontal scrollable row on narrow screens. Reuses the
// existing Btn component's primary/ghost styling so it needs no new color tokens.
import React from 'react';
import { Btn } from '@/components/telegram/ui';

export interface SideTab {
  id: string;
  label: string;
}

export function SideTabs({ tabs, active, onChange }: { tabs: SideTab[]; active: string; onChange: (id: string) => void }) {
  return (
    <nav aria-label="Sections" className="flex gap-2 overflow-x-auto pb-1 md:w-44 md:shrink-0 md:flex-col md:overflow-visible md:pb-0">
      {tabs.map((t) => (
        <Btn
          key={t.id}
          variant={t.id === active ? 'primary' : 'ghost'}
          onClick={() => onChange(t.id)}
          aria-current={t.id === active ? 'page' : undefined}
          className="shrink-0 justify-start md:w-full"
        >
          {t.label}
        </Btn>
      ))}
    </nav>
  );
}
