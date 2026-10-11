// File: frontend/src/components/telegram/MembersPanel.tsx
// Owner-only: everyone who has (or had) access to the private assistant, with their public Telegram name, when they joined and
// how, when they last used the bot, and what they use. Revoke / re-admit act through the same access.py rules as the bot's
// own /revoke and /approve. Recent access activity (the bot_audit trail) sits underneath.
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Card, CardBody, CardHeader, Divider } from '@nextui-org/react';
import { clsx } from 'clsx';
import { botAccessApi, displayName, toControlError } from '@/services/botAccessApi';
import type { AuditEntry, ControlErrorInfo, Member, MemberStatus } from '@/services/botAccessApi';
import { Btn } from '@/components/telegram/ui';
import ErrorAlert from '@/components/common/ErrorAlert';

const INACTIVE_DAYS = 30;
const DAY_MS = 86_400_000;

const JOINED_VIA: Record<string, string> = { invite: 'Invite link', auto: 'Auto-approved', request: 'Approved request', manual: 'Added by you' };

const ACTION_LABEL: Record<string, string> = {
  invite: 'Created an invite link',
  approve: 'Let in',
  redeem: 'Joined with an invite link',
  revoke: 'Revoked',
  decline: 'Declined',
  purge: 'Deleted saved data of',
  mode: 'Access mode set to',
  cap: 'Member cap set to',
};

function day(iso: string | null): string {
  return iso ? new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: '2-digit' }).format(new Date(iso)) : '—';
}

function ago(iso: string | null): string {
  if (!iso) return 'never';
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / DAY_MS);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  return `${days}d ago`;
}

function isInactive(m: Member): boolean {
  return !m.last_seen || Date.now() - new Date(m.last_seen).getTime() > INACTIVE_DAYS * DAY_MS;
}

function MemberRow({
  m,
  busy,
  confirming,
  onAsk,
  onRevoke,
  onRestore,
}: {
  m: Member;
  busy: boolean;
  confirming: boolean;
  onAsk: (uid: number | null) => void;
  onRevoke: (uid: number) => void;
  onRestore: (uid: number) => void;
}) {
  const name = displayName(m);
  return (
    <tr className="border-t border-slate-100 align-top">
      <td className="py-2 pr-3">
        <p className="text-sm font-medium text-slate-800">{name ?? <span className="font-normal text-slate-400">No name yet</span>}</p>
        <p className="font-mono text-[11px] text-slate-400">{m.telegram_user_id}</p>
      </td>
      <td className="py-2 pr-3 text-xs text-slate-600">
        {day(m.status === 'revoked' ? m.revoked_at : m.approved_at)}
        <p className="text-[11px] text-slate-400">{m.status === 'revoked' ? 'revoked' : JOINED_VIA[m.joined_via ?? ''] ?? '—'}</p>
      </td>
      <td className={clsx('py-2 pr-3 text-xs', isInactive(m) ? 'text-amber-600' : 'text-slate-600')}>{ago(m.last_seen)}</td>
      <td className="hidden py-2 pr-3 text-xs text-slate-600 sm:table-cell">
        {m.tracked} tracked
        <p className="text-[11px] text-slate-400">
          {[!m.finished_guide && 'guide not finished', m.morning_on && 'morning note on'].filter(Boolean).join(' · ') || ' '}
        </p>
      </td>
      <td className="py-2 text-right">
        {m.status === 'revoked' ? (
          <Btn size="sm" variant="secondary" disabled={busy} onClick={() => onRestore(m.telegram_user_id)}>
            Let back in
          </Btn>
        ) : confirming ? (
          <span className="inline-flex gap-1">
            <Btn size="sm" variant="danger" disabled={busy} onClick={() => onRevoke(m.telegram_user_id)}>
              {busy ? 'Revoking…' : 'Confirm'}
            </Btn>
            <Btn size="sm" variant="ghost" disabled={busy} onClick={() => onAsk(null)}>
              Cancel
            </Btn>
          </span>
        ) : (
          <Btn size="sm" variant="ghost" onClick={() => onAsk(m.telegram_user_id)}>
            Revoke
          </Btn>
        )}
      </td>
    </tr>
  );
}

export default function MembersPanel({ namesAvailable, onChanged }: { namesAvailable: boolean; onChanged: () => void }) {
  const [status, setStatus] = useState<MemberStatus>('active');
  const [members, setMembers] = useState<Member[] | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [error, setError] = useState<ControlErrorInfo | null>(null);
  const [query, setQuery] = useState('');
  const [inactiveOnly, setInactiveOnly] = useState(false);
  const [busyUid, setBusyUid] = useState<number | null>(null);
  const [confirmUid, setConfirmUid] = useState<number | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);

  const load = useCallback(async () => {
    try {
      const [list, log] = await Promise.all([botAccessApi.getMembers(status), botAccessApi.getAudit(30)]);
      setMembers(list);
      setAudit(log);
      setError(null);
    } catch (err) {
      setError(toControlError(err));
    }
  }, [status]);

  useEffect(() => {
    setMembers(null);
    setConfirmUid(null);
    load();
  }, [load]);

  const names = useMemo(() => {
    const map = new Map<number, string>();
    (members ?? []).forEach((m) => {
      const n = displayName(m);
      if (n) map.set(m.telegram_user_id, n);
    });
    return map;
  }, [members]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase().replace(/^@/, '');
    return (members ?? []).filter(
      (m) =>
        (!inactiveOnly || isInactive(m)) &&
        (!q ||
          String(m.telegram_user_id).includes(q) ||
          (m.first_name ?? '').toLowerCase().includes(q) ||
          (m.username ?? '').toLowerCase().includes(q)),
    );
  }, [members, query, inactiveOnly]);

  const act = useCallback(
    async (uid: number, kind: 'revoke' | 'restore') => {
      setBusyUid(uid);
      setNote(null);
      const who = names.get(uid) ?? String(uid);
      try {
        if (kind === 'revoke') {
          await botAccessApi.revoke(uid);
          setNote(`${who} is blocked now. Their saved data is deleted after 30 days.`);
        } else {
          const r = await botAccessApi.restore(uid);
          setNote(r.notified ? `${who} is back in and was told.` : `${who} is back in, but could not be messaged (they may have blocked the bot).`);
        }
        setConfirmUid(null);
        await load();
        onChanged();
      } catch (err) {
        setNote(toControlError(err).message);
      } finally {
        setBusyUid(null);
      }
    },
    [names, load, onChanged],
  );

  const inactiveCount = (members ?? []).filter(isInactive).length;

  return (
    <Card className="border border-slate-200 shadow-sm">
      <CardHeader className="flex flex-wrap items-center justify-between gap-2 p-4 pb-2">
        <div>
          <h3 className="text-[15px] font-semibold text-slate-900">Members</h3>
          <p className="text-xs text-slate-400">
            {namesAvailable ? 'Names are refreshed from Telegram whenever someone uses the bot.' : 'Names appear once migration 34 is applied.'}
          </p>
        </div>
        <div className="inline-flex rounded-lg border border-slate-200 p-0.5" role="tablist">
          {(['active', 'revoked'] as MemberStatus[]).map((s) => (
            <button
              key={s}
              type="button"
              role="tab"
              aria-selected={status === s}
              onClick={() => setStatus(s)}
              className={clsx('rounded-md px-3 py-1 text-xs font-medium', status === s ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-50')}
            >
              {s === 'active' ? 'Active' : 'Revoked'}
            </button>
          ))}
        </div>
      </CardHeader>
      <Divider className="bg-slate-100" />
      <CardBody className="space-y-3 p-4 pt-3">
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search name, @username or id"
            className="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-slate-500 focus:outline-none"
          />
          {status === 'active' && (
            <label className="flex items-center gap-1.5 text-xs text-slate-600">
              <input type="checkbox" checked={inactiveOnly} onChange={(e) => setInactiveOnly(e.target.checked)} />
              Inactive {INACTIVE_DAYS}+ days ({inactiveCount})
            </label>
          )}
        </div>

        {note && <p className="text-xs text-slate-600">{note}</p>}
        {error && <ErrorAlert title="Cannot load members" message={error.message} onRetry={load} />}

        {members === null && !error ? (
          <p className="py-4 text-center text-sm text-slate-400">Loading members…</p>
        ) : shown.length === 0 ? (
          <p className="py-4 text-center text-sm text-slate-400">
            {members && members.length > 0 ? 'Nobody matches.' : status === 'active' ? 'No members yet.' : 'Nobody is revoked.'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="text-[11px] uppercase tracking-wide text-slate-400">
                  <th className="pb-1 pr-3 font-medium">Member</th>
                  <th className="pb-1 pr-3 font-medium">{status === 'active' ? 'Joined' : 'Revoked'}</th>
                  <th className="pb-1 pr-3 font-medium">Last active</th>
                  <th className="hidden pb-1 pr-3 font-medium sm:table-cell">Uses</th>
                  <th className="pb-1" />
                </tr>
              </thead>
              <tbody>
                {shown.map((m) => (
                  <MemberRow
                    key={m.telegram_user_id}
                    m={m}
                    busy={busyUid === m.telegram_user_id}
                    confirming={confirmUid === m.telegram_user_id}
                    onAsk={setConfirmUid}
                    onRevoke={(uid) => act(uid, 'revoke')}
                    onRestore={(uid) => act(uid, 'restore')}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}

        <Divider className="bg-slate-100" />
        <button type="button" onClick={() => setShowLog((v) => !v)} className="text-xs font-semibold uppercase tracking-wide text-slate-500 hover:text-slate-700">
          {showLog ? '▾' : '▸'} Recent access activity
        </button>
        {showLog &&
          (audit.length === 0 ? (
            <p className="text-sm text-slate-400">Nothing recorded yet.</p>
          ) : (
            <ul className="space-y-1">
              {audit.map((e, i) => (
                <li key={i} className="flex gap-3 text-xs text-slate-600">
                  <span className="w-20 shrink-0 text-slate-400">{day(e.at)}</span>
                  <span>
                    {ACTION_LABEL[e.action] ?? e.action}{' '}
                    {e.target !== null && <span className="font-medium">{names.get(e.target) ?? e.target}</span>}
                    {e.detail && e.action !== 'approve' && <span className="text-slate-400"> {e.action === 'mode' || e.action === 'cap' ? e.detail : `(${e.detail})`}</span>}
                    {e.action === 'approve' && e.detail && <span className="text-slate-400"> ({e.detail === 'auto' ? 'auto' : 'request'})</span>}
                  </span>
                </li>
              ))}
            </ul>
          ))}
      </CardBody>
    </Card>
  );
}
