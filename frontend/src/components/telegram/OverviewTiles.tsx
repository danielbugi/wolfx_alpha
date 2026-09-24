// File: frontend/src/components/telegram/OverviewTiles.tsx
// One KPI row per channel: plain stat tiles (a value and its label - there is no series to plot, so no chart). A real zero shows as 0;
// an unknown value (no post recorded yet) shows as "—", never as a made-up number.
import React from 'react';
import { Card, CardBody, CardHeader, Chip, Divider } from '@nextui-org/react';
import { ExclamationTriangleIcon } from '@heroicons/react/24/outline';
import type { TargetOverview } from '@/services/telegramApi';
import { formatWhen } from '@/lib/telegramFormat';
import { LockChip } from '@/components/telegram/StatusChips';

function Tile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3">
      <p className="text-2xl font-semibold tabular-nums text-slate-800">{value}</p>
      <p className="mt-0.5 text-xs text-slate-500">{label}</p>
      {hint && <p className="text-[10px] text-slate-400">{hint}</p>}
    </div>
  );
}

function ChannelCard({ t, timezone }: { t: TargetOverview; timezone: string }) {
  return (
    <Card className="border border-slate-200 shadow-sm">
      <CardHeader className="flex flex-wrap items-center justify-between gap-2 p-4 pb-2">
        <div>
          <h2 className="text-sm font-semibold text-slate-800">{t.label}</h2>
          <p className="text-xs text-slate-400">{t.configured ? `Chat ${t.chat_hint}` : 'Not configured in .env'}</p>
        </div>
        <div className="flex items-center gap-2">
          {!t.configured && (
            <Chip size="sm" variant="flat" className="bg-red-100 text-red-700" startContent={<ExclamationTriangleIcon className="h-3.5 w-3.5" />}>
              Not configured
            </Chip>
          )}
          <LockChip target={t.target} locked={t.locked} />
        </div>
      </CardHeader>
      <Divider className="bg-slate-100" />
      <CardBody className="p-4 pt-3">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Tile label="Posted today" value={t.sent_today} />
          <Tile label="Live now" value={t.live} hint={`of ${t.recorded} recorded`} />
          <Tile label="Edited" value={t.edited} />
          <Tile label="Deleted" value={t.deleted} />
        </div>
        <p className="mt-3 text-xs text-slate-500">
          Last post: <span className="font-medium text-slate-700">{formatWhen(t.last_sent_at, timezone)}</span>
        </p>
      </CardBody>
    </Card>
  );
}

export default function OverviewTiles({ targets, timezone }: { targets: TargetOverview[]; timezone: string }) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {targets.map((t) => (
        <ChannelCard key={t.target} t={t} timezone={timezone} />
      ))}
    </div>
  );
}
