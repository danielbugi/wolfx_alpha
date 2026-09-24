// File: frontend/src/components/telegram/AccessPanel.tsx
// Owner-only panel: who may use the First Light private assistant (mechanism/alerts/access.py, surfaced via
// backend/routers/bot_access.py). This is a thin face on the exact same Access/PgStore classes the bot's own
// /approve /decline /revoke /invite chat commands use — approving here sends the same "you're in" + guide DM
// the bot sends when the owner approves by chat.
import React, { useCallback, useEffect, useState } from 'react';
import { Card, CardBody, CardHeader, Chip, Divider } from '@nextui-org/react';
import { CheckIcon, ClipboardIcon, ExclamationTriangleIcon } from '@heroicons/react/24/outline';
import { botAccessApi, toControlError } from '@/services/botAccessApi';
import type { AccessOverview, ControlErrorInfo, PendingRequest } from '@/services/botAccessApi';
import { Btn } from '@/components/telegram/ui';
import ErrorAlert from '@/components/common/ErrorAlert';

const MODE_LABEL: Record<string, string> = {
  approve: 'Manual approval — you decide each request',
  auto: 'Auto-approve up to the member cap',
  closed: 'Closed — nobody new can request',
};

function localWhen(iso: string): string {
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }).format(
    new Date(iso),
  );
}

function Tile({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3">
      <p className="text-2xl font-semibold tabular-nums text-slate-800">{value}</p>
      <p className="mt-0.5 text-xs text-slate-500">{label}</p>
    </div>
  );
}

function RequestRow({
  row,
  busy,
  onApprove,
  onDecline,
}: {
  row: PendingRequest;
  busy: boolean;
  onApprove: (uid: number) => void;
  onDecline: (uid: number) => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-100 px-3 py-2">
      <div>
        <p className="font-mono text-sm text-slate-800">{row.telegram_user_id}</p>
        <p className="text-xs text-slate-400">Requested {localWhen(row.requested_at)}</p>
      </div>
      <div className="flex gap-2">
        <Btn size="sm" variant="primary" disabled={busy} onClick={() => onApprove(row.telegram_user_id)}>
          Approve
        </Btn>
        <Btn size="sm" variant="secondary" disabled={busy} onClick={() => onDecline(row.telegram_user_id)}>
          Decline
        </Btn>
      </div>
    </div>
  );
}

export default function AccessPanel() {
  const [overview, setOverview] = useState<AccessOverview | null>(null);
  const [error, setError] = useState<ControlErrorInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyUid, setBusyUid] = useState<number | null>(null);
  const [actionNote, setActionNote] = useState<string | null>(null);

  const [inviteNote, setInviteNote] = useState('');
  const [inviteBusy, setInviteBusy] = useState(false);
  const [inviteResult, setInviteResult] = useState<{ payload: string; link: string | null } | null>(null);
  const [copied, setCopied] = useState(false);

  const [revokeId, setRevokeId] = useState('');
  const [revokeConfirm, setRevokeConfirm] = useState(false);
  const [revokeBusy, setRevokeBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setOverview(await botAccessApi.getOverview());
      setError(null);
    } catch (err) {
      setError(toControlError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const approve = useCallback(
    async (uid: number) => {
      setBusyUid(uid);
      setActionNote(null);
      try {
        const r = await botAccessApi.approve(uid);
        setActionNote(r.notified ? `Approved ${uid} and sent them the welcome message.` : `Approved ${uid}, but could not DM them (they may have blocked the bot).`);
        await load();
      } catch (err) {
        setActionNote(toControlError(err).message);
      } finally {
        setBusyUid(null);
      }
    },
    [load],
  );

  const decline = useCallback(
    async (uid: number) => {
      setBusyUid(uid);
      setActionNote(null);
      try {
        const r = await botAccessApi.decline(uid);
        setActionNote(r.notified ? `Declined ${uid} and let them know.` : `Declined ${uid}, but could not DM them.`);
        await load();
      } catch (err) {
        setActionNote(toControlError(err).message);
      } finally {
        setBusyUid(null);
      }
    },
    [load],
  );

  const createInvite = useCallback(async () => {
    setInviteBusy(true);
    setInviteResult(null);
    setCopied(false);
    try {
      const r = await botAccessApi.createInvite(inviteNote.trim() || null);
      setInviteResult(r);
      setInviteNote('');
      await load();
    } catch (err) {
      setActionNote(toControlError(err).message);
    } finally {
      setInviteBusy(false);
    }
  }, [inviteNote, load]);

  const copyLink = useCallback(() => {
    if (!inviteResult?.link) return;
    navigator.clipboard
      ?.writeText(inviteResult.link)
      .then(() => setCopied(true))
      .catch(() => setCopied(false));
  }, [inviteResult]);

  const doRevoke = useCallback(async () => {
    const uid = Number(revokeId.trim());
    if (!Number.isInteger(uid) || uid <= 0) return;
    setRevokeBusy(true);
    setActionNote(null);
    try {
      const r = await botAccessApi.revoke(uid);
      setActionNote(r.message);
      setRevokeId('');
      setRevokeConfirm(false);
      await load();
    } catch (err) {
      setActionNote(toControlError(err).message);
    } finally {
      setRevokeBusy(false);
    }
  }, [revokeId, load]);

  if (loading && !overview) return <div className="py-6 text-center text-sm text-slate-400">Loading access…</div>;
  if (error && !overview) return <ErrorAlert title="Cannot load bot access" message={error.message} onRetry={load} />;
  if (!overview) return null;

  return (
    <section aria-labelledby="access-heading" className="space-y-3">
      <h2 id="access-heading" className="text-lg font-semibold text-slate-800">
        Private assistant access
      </h2>
      <Card className="border border-slate-200 shadow-sm">
        <CardHeader className="flex flex-wrap items-center justify-between gap-2 p-4 pb-2">
          <div>
            <h3 className="text-sm font-semibold text-slate-800">Who may use the bot</h3>
            <p className="text-xs text-slate-400">{MODE_LABEL[overview.mode] ?? overview.mode}</p>
          </div>
          {!overview.owner_configured && (
            <Chip size="sm" variant="flat" className="bg-red-100 text-red-700" startContent={<ExclamationTriangleIcon className="h-3.5 w-3.5" />}>
              BOT_OWNER_ID not set
            </Chip>
          )}
        </CardHeader>
        <Divider className="bg-slate-100" />
        <CardBody className="space-y-4 p-4 pt-3">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Tile label="Active members" value={overview.active} />
            <Tile label="Waiting for approval" value={overview.pending_count} />
            <Tile label="Revoked" value={overview.revoked} />
            <Tile label="Open invite links" value={overview.open_invites} />
          </div>

          {actionNote && <p className="text-xs text-slate-600">{actionNote}</p>}

          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
              Waiting for approval {overview.pending_count > 0 && `(${overview.pending_count})`}
            </h4>
            {overview.pending.length === 0 ? (
              <p className="text-sm text-slate-400">Nobody is waiting.</p>
            ) : (
              <div className="space-y-2">
                {overview.pending.map((row) => (
                  <RequestRow key={row.telegram_user_id} row={row} busy={busyUid === row.telegram_user_id} onApprove={approve} onDecline={decline} />
                ))}
              </div>
            )}
          </div>

          <Divider className="bg-slate-100" />

          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Invite someone directly</h4>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={inviteNote}
                  onChange={(e) => setInviteNote(e.target.value)}
                  placeholder="Optional note (e.g. a name)"
                  maxLength={60}
                  className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-slate-500 focus:outline-none"
                />
                <Btn size="sm" variant="primary" disabled={inviteBusy} onClick={createInvite}>
                  {inviteBusy ? 'Creating…' : 'Create link'}
                </Btn>
              </div>
              {inviteResult && (
                <div className="mt-2 flex items-center gap-2 rounded-md bg-slate-50 p-2 text-xs">
                  <code className="flex-1 truncate">{inviteResult.link ?? inviteResult.payload}</code>
                  {inviteResult.link && (
                    <button type="button" onClick={copyLink} className="shrink-0 text-slate-500 hover:text-slate-700" aria-label="Copy link">
                      {copied ? <CheckIcon className="h-4 w-4" /> : <ClipboardIcon className="h-4 w-4" />}
                    </button>
                  )}
                </div>
              )}
              <p className="mt-1 text-[10px] text-slate-400">One-time use, expires in 72 hours.</p>
            </div>

            <div>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Revoke a member</h4>
              <div className="flex gap-2">
                <input
                  type="text"
                  inputMode="numeric"
                  value={revokeId}
                  onChange={(e) => {
                    setRevokeId(e.target.value);
                    setRevokeConfirm(false);
                  }}
                  placeholder="Telegram id"
                  className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-slate-500 focus:outline-none"
                />
                {revokeConfirm ? (
                  <Btn size="sm" variant="danger" disabled={revokeBusy} onClick={doRevoke}>
                    {revokeBusy ? 'Revoking…' : 'Confirm'}
                  </Btn>
                ) : (
                  <Btn size="sm" variant="secondary" disabled={!revokeId.trim()} onClick={() => setRevokeConfirm(true)}>
                    Revoke
                  </Btn>
                )}
              </div>
              <p className="mt-1 text-[10px] text-slate-400">Blocks them at once; a new invite link cannot re-admit them, only Approve.</p>
            </div>
          </div>
        </CardBody>
      </Card>
    </section>
  );
}
