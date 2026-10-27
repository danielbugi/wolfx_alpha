// File: frontend/src/components/telegram/LiveNowPanel.tsx
// "What's live now": what the channel posts today, what is switched off (and whether anything switched off has started posting again), and
// how many old-format posts are still sitting in the channel, with a one-click cleanup. Replaces the old "Today's schedule", which was built
// from the previous Windows setup and called everything "overdue". The server builds this from the post-type registry next to the ledger's own
// last-posted times (mechanism/alerts/channel_kinds.py), so it shows what actually happened, not only what we intended.
'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Card, CardBody, CardHeader, Chip, Divider } from '@nextui-org/react';
import { CheckCircleIcon, ClockIcon, ExclamationTriangleIcon, PauseCircleIcon } from '@heroicons/react/24/outline';
import { telegramApi, toControlError } from '@/services/telegramApi';
import type { ChannelTarget, ControlErrorInfo, LiveCurrent, LiveStatus } from '@/services/telegramApi';
import { formatWhen } from '@/lib/telegramFormat';
import ErrorAlert from '@/components/common/ErrorAlert';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { Btn } from '@/components/telegram/ui';

const ICON = 'h-3.5 w-3.5';

const CURRENT_CHIP: Record<LiveCurrent['status'], { className: string; label: string; icon: React.ReactNode }> = {
  posted: { className: 'bg-emerald-100 text-emerald-800', label: 'Posting', icon: <CheckCircleIcon className={ICON} /> },
  check: { className: 'bg-amber-100 text-amber-800', label: 'Quiet longer than usual', icon: <ExclamationTriangleIcon className={ICON} /> },
  never: { className: 'bg-slate-100 text-slate-600', label: 'Nothing recorded yet', icon: <ClockIcon className={ICON} /> },
};

interface Props {
  target: ChannelTarget;
  timezone: string;
  isOwner: boolean;
  token: string | null;
  controlsEnabled: boolean;
  /** Called after posts were removed, so the list and the counts refresh. */
  onChanged: () => void;
  onBadToken: () => void;
  /** Bumped by the page to force a reload (Refresh). */
  reloadKey?: number;
}

export default function LiveNowPanel({ target, timezone, isOwner, token, controlsEnabled, onChanged, onBadToken, reloadKey = 0 }: Props) {
  const [live, setLive] = useState<LiveStatus | null>(null);
  const [error, setError] = useState<ControlErrorInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<ControlErrorInfo | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setLive(await telegramApi.getLive(target));
      setError(null);
    } catch (err) {
      setError(toControlError(err));
    } finally {
      setLoading(false);
    }
  }, [target]);

  useEffect(() => {
    load();
  }, [load, reloadKey]);

  const removeOld = async () => {
    if (!token) return;
    setBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      let removed = 0;
      for (let i = 0; i < 12; i += 1) {                                      // 30 per call; stop when none are left (or after 12 rounds)
        const r = await telegramApi.deleteOld(target, false, token);
        removed += r.deleted;
        if (r.remaining === 0 || r.deleted === 0) break;
      }
      setNotice(`Removed ${removed} old-format post${removed === 1 ? '' : 's'} from the channel.`);
      setConfirming(false);
      await load();
      onChanged();
    } catch (err) {
      const info = toControlError(err);
      if (info.code === 'bad_token') onBadToken();
      setActionError(info);
    } finally {
      setBusy(false);
    }
  };

  const resumed = live?.paused.filter((p) => p.resumed) ?? [];
  const old = live?.old_posts;
  const canRemove = isOwner && controlsEnabled && !!old && old.removable_now > 0;

  return (
    <Card className="border border-slate-200 shadow-sm" data-testid="live-now">
      <CardHeader className="flex flex-wrap items-center justify-between gap-2 p-4 pb-2">
        <div>
          <h2 className="text-sm font-semibold text-slate-800">What&apos;s live now</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            What the channel posts today, what is switched off, and the older posts still visible in the channel. Times in {live?.timezone ?? 'UTC'}.
          </p>
        </div>
        {resumed.length > 0 && (
          <Chip size="sm" variant="flat" className="bg-red-100 text-red-700" startContent={<ExclamationTriangleIcon className={ICON} />}>
            {resumed.length} switched-off post type{resumed.length === 1 ? ' is' : 's are'} posting again
          </Chip>
        )}
      </CardHeader>
      <Divider className="bg-slate-100" />
      <CardBody className="p-0">
        {loading && !live ? (
          <div className="flex justify-center py-8"><LoadingSpinner /></div>
        ) : error ? (
          <div className="p-4"><ErrorAlert title="Cannot load what is live" message={error.message} onRetry={load} /></div>
        ) : live ? (
          <div className="divide-y divide-slate-100">
            <section aria-labelledby="live-posting">
              <h3 id="live-posting" className="px-4 pt-3 text-xs font-semibold uppercase tracking-wide text-emerald-700">Posting now</h3>
              <ul>
                {live.current.map((c) => {
                  const chip = CURRENT_CHIP[c.status];
                  return (
                    <li key={c.key} className="flex items-center justify-between gap-3 px-4 py-2.5" data-testid={`live-current-${c.key}`}>
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-slate-800">{c.label}</p>
                        <p className="truncate text-xs text-slate-500">
                          {c.cadence} · last posted {c.last_sent_at ? formatWhen(c.last_sent_at, timezone) : '—'}
                        </p>
                      </div>
                      <Chip size="sm" variant="flat" className={chip.className} startContent={chip.icon}>{chip.label}</Chip>
                    </li>
                  );
                })}
              </ul>
            </section>

            <section aria-labelledby="live-paused">
              <h3 id="live-paused" className="px-4 pt-3 text-xs font-semibold uppercase tracking-wide text-slate-500">Switched off</h3>
              <ul>
                {live.paused.map((p) => (
                  <li key={p.key} className="px-4 py-2.5" data-testid={`live-paused-${p.key}`}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-slate-700">{p.label}</p>
                        <p className="text-xs text-slate-500">{p.reason}</p>
                        <p className="text-xs text-slate-400">
                          Last posted {p.last_sent_at ? formatWhen(p.last_sent_at, timezone) : 'never'} · To bring it back: {p.restore}
                        </p>
                      </div>
                      {p.resumed ? (
                        <Chip size="sm" variant="flat" className="shrink-0 bg-red-100 text-red-700" startContent={<ExclamationTriangleIcon className={ICON} />}>
                          Posting again
                        </Chip>
                      ) : (
                        <Chip size="sm" variant="flat" className="shrink-0 bg-slate-100 text-slate-600" startContent={<PauseCircleIcon className={ICON} />}>
                          Off
                        </Chip>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </section>

            <section aria-labelledby="live-old" className="px-4 py-3">
              <h3 id="live-old" className="text-xs font-semibold uppercase tracking-wide text-slate-500">Old-format posts still in the channel</h3>
              {old && old.live > 0 ? (
                <>
                  <p className="mt-1 text-sm text-slate-700" data-testid="live-old-summary">
                    <span className="font-semibold tabular-nums">{old.live}</span> from before {live.format_changed_on}.{' '}
                    <span className="tabular-nums">{old.removable_now}</span> can still be removed from here (posted in the last 48 hours);{' '}
                    <span className="tabular-nums">{old.too_old_for_a_bot}</span> are older, which Telegram only lets you remove by hand in the Telegram app
                    {old.pinned > 0 ? `; ${old.pinned} pinned (unpin it first)` : ''}.
                  </p>
                  {old.by_kind.length > 0 && (
                    <p className="mt-1 text-xs text-slate-400">
                      {old.by_kind.map((k) => `${k.label} ${k.live}`).join(' · ')}
                    </p>
                  )}
                  {isOwner ? (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {!confirming ? (
                        <Btn
                          size="sm"
                          variant="danger"
                          disabled={!canRemove || !token || busy}
                          title={!controlsEnabled ? 'Editing is switched off on the server.' : !token ? 'Enter the control token at the top of the page first.' : undefined}
                          onClick={() => setConfirming(true)}
                        >
                          Remove old-format posts ({old.removable_now})
                        </Btn>
                      ) : (
                        <>
                          <p className="text-sm text-red-700">
                            Remove {old.removable_now} old-format post{old.removable_now === 1 ? '' : 's'} from the public channel? This cannot be undone.
                          </p>
                          <Btn size="sm" variant="danger" disabled={busy} onClick={removeOld}>{busy ? 'Removing…' : 'Yes, remove them'}</Btn>
                          <Btn size="sm" disabled={busy} onClick={() => setConfirming(false)}>Cancel</Btn>
                        </>
                      )}
                    </div>
                  ) : (
                    <p className="mt-2 text-xs text-slate-500">Only the Owner account can remove posts.</p>
                  )}
                </>
              ) : (
                <p className="mt-1 text-sm text-slate-600">None — every post still in the channel is in the current format.</p>
              )}
              {notice && <p className="mt-2 text-sm text-emerald-700" role="status">{notice}</p>}
              {actionError && <p className="mt-2 text-sm text-red-700" role="alert">{actionError.message}</p>}
            </section>
          </div>
        ) : null}
      </CardBody>
    </Card>
  );
}
