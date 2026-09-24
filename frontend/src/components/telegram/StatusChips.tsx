// File: frontend/src/components/telegram/StatusChips.tsx
// Every status here is an icon AND a word (never colour alone), so it reads the same for colour-blind users and in print.
import React from 'react';
import { Chip } from '@nextui-org/react';
import {
  BeakerIcon,
  CheckCircleIcon,
  DocumentTextIcon,
  LockClosedIcon,
  LockOpenIcon,
  PencilSquareIcon,
  PhotoIcon,
  TrashIcon,
} from '@heroicons/react/24/outline';
import type { ChannelTarget, MessageSummary } from '@/services/telegramApi';

const ICON = 'h-3.5 w-3.5';

export function LockChip({ target, locked }: { target: ChannelTarget; locked: boolean }) {
  if (target === 'dev') {
    return (
      <Chip size="sm" variant="flat" className="bg-slate-100 text-slate-700" startContent={<BeakerIcon className={ICON} />}>
        Test channel
      </Chip>
    );
  }
  return locked ? (
    <Chip size="sm" variant="flat" className="bg-amber-100 text-amber-800" startContent={<LockClosedIcon className={ICON} />}>
      Locked · view only
    </Chip>
  ) : (
    <Chip size="sm" variant="flat" className="bg-red-100 text-red-700" startContent={<LockOpenIcon className={ICON} />}>
      Open · public channel
    </Chip>
  );
}

export function ChannelChip({ target }: { target: ChannelTarget }) {
  return (
    <Chip size="sm" variant="flat" className="border border-slate-300 bg-white text-slate-600">
      {target === 'dev' ? 'Dev' : 'Production'}
    </Chip>
  );
}

export function StatusChip({ message }: { message: Pick<MessageSummary, 'status' | 'edit_count'> }) {
  if (message.status === 'deleted') {
    return (
      <Chip size="sm" variant="flat" className="bg-slate-200 text-slate-600" startContent={<TrashIcon className={ICON} />}>
        Deleted
      </Chip>
    );
  }
  if (message.edit_count > 0) {
    return (
      <Chip size="sm" variant="flat" className="bg-sky-100 text-sky-800" startContent={<PencilSquareIcon className={ICON} />}>
        Edited{message.edit_count > 1 ? ` ×${message.edit_count}` : ''}
      </Chip>
    );
  }
  return (
    <Chip size="sm" variant="flat" className="bg-emerald-100 text-emerald-800" startContent={<CheckCircleIcon className={ICON} />}>
      Live
    </Chip>
  );
}

export function TypeLabel({ type }: { type: 'text' | 'photo' }) {
  const Icon = type === 'photo' ? PhotoIcon : DocumentTextIcon;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-slate-500">
      <Icon className={ICON} />
      {type === 'photo' ? 'Photo' : 'Text'}
    </span>
  );
}
