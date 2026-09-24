// File: frontend/src/components/telegram/MessageTable.tsx
import React from 'react';
import { Card, CardBody, Spinner } from '@nextui-org/react';
import { EyeIcon, InboxIcon, PencilSquareIcon, TrashIcon } from '@heroicons/react/24/outline';
import type { MessageList, MessageSummary } from '@/services/telegramApi';
import { formatWhen } from '@/lib/telegramFormat';
import { ChannelChip, StatusChip, TypeLabel } from '@/components/telegram/StatusChips';
import { Btn } from '@/components/telegram/ui';

export type RowMode = 'view' | 'edit' | 'delete';

interface Props {
  data: MessageList;
  loading: boolean;
  filtered: boolean;
  /** When the ledger began recording (ISO), or null when nothing has been recorded yet. */
  trackingSince: string | null;
  onOpen: (id: number, mode: RowMode) => void;
  onPage: (offset: number) => void;
}

function RowActions({ m, onOpen }: { m: MessageSummary; onOpen: Props['onOpen'] }) {
  const reason = (d: MessageSummary['can_edit']) => (d.ok ? undefined : (d.reason ?? undefined));
  return (
    <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
      <Btn variant="ghost" size="icon" aria-label={`View message ${m.message_id}`} title="View" onClick={() => onOpen(m.id, 'view')}>
        <EyeIcon className="h-4 w-4" />
      </Btn>
      <span title={reason(m.can_edit)}>
        <Btn variant="ghost" size="icon" aria-label={`Edit message ${m.message_id}`} disabled={!m.can_edit.ok} onClick={() => onOpen(m.id, 'edit')}>
          <PencilSquareIcon className="h-4 w-4" />
        </Btn>
      </span>
      <span title={reason(m.can_delete)}>
        <Btn variant="ghost" size="icon" aria-label={`Delete message ${m.message_id}`} disabled={!m.can_delete.ok} className="text-red-600 hover:bg-red-50" onClick={() => onOpen(m.id, 'delete')}>
          <TrashIcon className="h-4 w-4" />
        </Btn>
      </span>
    </div>
  );
}

export default function MessageTable({ data, loading, filtered, trackingSince, onOpen, onPage }: Props) {
  const { items, total, limit, offset, timezone } = data;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);

  if (items.length === 0) {
    return (
      <Card className="border border-slate-200 shadow-sm">
        <CardBody className="flex flex-col items-center gap-2 py-12 text-center">
          <InboxIcon className="h-8 w-8 text-slate-300" aria-hidden />
          <p className="text-sm font-medium text-slate-700">{filtered ? 'No recorded message matches these filters' : 'No message recorded yet'}</p>
          <p className="max-w-md text-xs text-slate-500">
            {filtered
              ? 'Try a different day or clear a filter.'
              : trackingSince
                ? 'Nothing matches.'
                : 'Every message a script sends to a channel from now on appears here. Telegram does not let a bot read a channel’s history, so posts sent before this page existed cannot be listed.'}
          </p>
        </CardBody>
      </Card>
    );
  }

  return (
    <Card className="border border-slate-200 shadow-sm">
      <CardBody className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[46rem] text-sm">
            <thead>
              <tr className="bg-slate-50 text-left text-xs text-slate-500">
                <th scope="col" className="px-3 py-2 font-medium">Posted</th>
                <th scope="col" className="px-3 py-2 font-medium">Channel</th>
                <th scope="col" className="px-3 py-2 font-medium">Post</th>
                <th scope="col" className="px-3 py-2 font-medium">Status</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((m) => (
                <tr
                  key={m.id}
                  className="cursor-pointer border-t border-slate-100 align-top hover:bg-slate-50/60"
                  onClick={() => onOpen(m.id, 'view')}
                >
                  <td className="whitespace-nowrap px-3 py-2 tabular-nums text-slate-600">{formatWhen(m.sent_at, timezone)}</td>
                  <td className="px-3 py-2"><ChannelChip target={m.target} /></td>
                  <td className="max-w-[26rem] px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-800">{m.kind_label}</span>
                      <TypeLabel type={m.content_type} />
                      {m.pinned && <span className="text-[10px] uppercase tracking-wide text-slate-400">pinned</span>}
                    </div>
                    <p className={`truncate text-xs ${m.status === 'deleted' ? 'text-slate-400 line-through' : 'text-slate-500'}`} title={m.preview}>
                      {m.preview || '—'}
                    </p>
                  </td>
                  <td className="px-3 py-2"><StatusChip message={m} /></td>
                  <td className="px-3 py-2"><RowActions m={m} onOpen={onOpen} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-slate-100 px-3 py-2 text-xs text-slate-500">
          <span>
            {from}–{to} of {total} {loading && <Spinner size="sm" className="ml-2 align-middle" />}
          </span>
          <div className="flex gap-2">
            <Btn size="sm" disabled={offset === 0 || loading} onClick={() => onPage(Math.max(0, offset - limit))}>
              Newer
            </Btn>
            <Btn size="sm" disabled={offset + limit >= total || loading} onClick={() => onPage(offset + limit)}>
              Older
            </Btn>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
