// File: frontend/src/components/telegram/ScheduleView.tsx
// "Today's schedule": what is configured to post today for a channel, and whether it already has - built from the senders' own state files
// and the trading-day calendar (see mechanism/alerts/schedule.py's docstring), never a live read of the OS scheduler.
'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Card, CardBody, CardHeader, Chip, Divider, Spinner } from '@nextui-org/react';
import { CheckCircleIcon, ClockIcon, ExclamationTriangleIcon, MinusCircleIcon } from '@heroicons/react/24/outline';
import { telegramApi, toControlError } from '@/services/telegramApi';
import type { ChannelTarget, ScheduleReport, ScheduleStatus } from '@/services/telegramApi';
import ErrorAlert from '@/components/common/ErrorAlert';
import { Btn } from '@/components/telegram/ui';

const STATUS_CHIP: Record<ScheduleStatus, { icon: React.ComponentType<React.SVGProps<SVGSVGElement>>; className: string; label: string }> = {
  done: { icon: CheckCircleIcon, className: 'bg-emerald-100 text-emerald-800', label: 'Done' },
  pending: { icon: ClockIcon, className: 'bg-slate-100 text-slate-600', label: 'Pending' },
  overdue: { icon: ExclamationTriangleIcon, className: 'bg-amber-100 text-amber-800', label: 'Overdue' },
  skipped: { icon: MinusCircleIcon, className: 'bg-slate-100 text-slate-500', label: 'Not a trading day' },
};

function StatusChip({ status }: { status: ScheduleStatus }) {
  const s = STATUS_CHIP[status];
  const Icon = s.icon;
  return (
    <Chip size="sm" variant="flat" className={s.className} startContent={<Icon className="h-3.5 w-3.5" />}>
      {s.label}
    </Chip>
  );
}

export default function ScheduleView({ targets }: { targets: ChannelTarget[] }) {
  const [target, setTarget] = useState<ChannelTarget>(targets[0] ?? 'dev');
  const [report, setReport] = useState<ScheduleReport | null>(null);
  const [error, setError] = useState<ReturnType<typeof toControlError> | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setReport(await telegramApi.getSchedule(target));
      setError(null);
    } catch (err) {
      setError(toControlError(err));
    } finally {
      setLoading(false);
    }
  }, [target]);

  useEffect(() => {
    load();
  }, [load]);

  const overdueCount = report?.jobs.filter((j) => j.status === 'overdue').length ?? 0;

  return (
    <Card className="border border-slate-200 shadow-sm">
      <CardHeader className="flex flex-wrap items-center justify-between gap-2 p-4 pb-2">
        <div>
          <h2 className="text-sm font-semibold text-slate-800">Today&apos;s schedule</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            What is configured to post today, and whether it already has — read from the senders&apos; own records, not a live scheduler.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {targets.length > 1 && (
            <div className="flex gap-1 rounded-md bg-slate-100 p-0.5">
              {targets.map((t) => (
                <Btn key={t} variant={target === t ? 'primary' : 'ghost'} size="sm" onClick={() => setTarget(t)}>
                  {t === 'dev' ? 'Dev' : 'Production'}
                </Btn>
              ))}
            </div>
          )}
          {overdueCount > 0 && (
            <Chip size="sm" variant="flat" className="bg-amber-100 text-amber-800" startContent={<ExclamationTriangleIcon className="h-3.5 w-3.5" />}>
              {overdueCount} overdue
            </Chip>
          )}
        </div>
      </CardHeader>
      <Divider className="bg-slate-100" />
      <CardBody className="p-0">
        {loading && !report ? (
          <div className="flex justify-center py-8"><Spinner color="primary" /></div>
        ) : error ? (
          <div className="p-4"><ErrorAlert title="Cannot load the schedule" message={error.message} onRetry={load} /></div>
        ) : report ? (
          <ul className="divide-y divide-slate-100">
            {report.jobs.map((job) => (
              <li key={job.key} className="flex items-center justify-between gap-3 px-4 py-2.5">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-slate-800">{job.label}</p>
                  <p className="truncate text-xs text-slate-500" title={job.reason}>
                    {job.local_time} {report.timezone} · {job.reason}
                  </p>
                </div>
                <StatusChip status={job.status} />
              </li>
            ))}
          </ul>
        ) : null}
      </CardBody>
    </Card>
  );
}
