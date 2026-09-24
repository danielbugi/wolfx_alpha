// File: frontend/src/components/telegram/ConfigPanel.tsx
// The system configuration that governs posting, read-only. Secrets are never shown: the API sends "Set" / "Missing", not the value.
import React from 'react';
import { Card, CardBody, CardHeader, Chip, Divider } from '@nextui-org/react';
import { CheckCircleIcon, ExclamationTriangleIcon, MinusCircleIcon } from '@heroicons/react/24/outline';
import type { ConfigItem } from '@/services/telegramApi';

const GOOD = new Set(['Set', 'Configured', 'Enabled', 'On', 'Locked']);
const BAD = new Set(['Missing', 'Disabled', 'Not configured', 'Open']);

function ValueChip({ value }: { value: string }) {
  if (GOOD.has(value)) {
    return (
      <Chip size="sm" variant="flat" className="bg-emerald-100 text-emerald-800" startContent={<CheckCircleIcon className="h-3.5 w-3.5" />}>
        {value}
      </Chip>
    );
  }
  if (BAD.has(value)) {
    return (
      <Chip size="sm" variant="flat" className="bg-amber-100 text-amber-800" startContent={<ExclamationTriangleIcon className="h-3.5 w-3.5" />}>
        {value}
      </Chip>
    );
  }
  if (value === 'Off') {
    return (
      <Chip size="sm" variant="flat" className="bg-slate-100 text-slate-600" startContent={<MinusCircleIcon className="h-3.5 w-3.5" />}>
        {value}
      </Chip>
    );
  }
  return <span className="text-sm font-medium text-slate-700">{value}</span>;
}

export default function ConfigPanel({ items }: { items: ConfigItem[] }) {
  const groups = Array.from(new Set(items.map((i) => i.group)));
  return (
    <Card className="border border-slate-200 shadow-sm">
      <CardHeader className="flex-col items-start p-4 pb-2">
        <h2 className="text-sm font-semibold text-slate-800">System configuration</h2>
        <p className="mt-0.5 text-xs text-slate-500">
          Read-only. These settings come from <span className="font-mono">.env</span>; change them there and restart the API and the scheduled jobs.
        </p>
      </CardHeader>
      <Divider className="bg-slate-100" />
      <CardBody className="grid grid-cols-1 gap-x-8 gap-y-4 p-4 md:grid-cols-2">
        {groups.map((group) => (
          <div key={group}>
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">{group}</h3>
            <ul className="divide-y divide-slate-100">
              {items
                .filter((i) => i.group === group)
                .map((i) => (
                  <li key={i.key} className="flex items-start justify-between gap-3 py-2">
                    <div className="min-w-0">
                      <p className="text-sm text-slate-700">{i.label}</p>
                      {i.note && <p className="text-xs text-slate-400">{i.note}</p>}
                    </div>
                    <div className="shrink-0">
                      <ValueChip value={i.value} />
                    </div>
                  </li>
                ))}
            </ul>
          </div>
        ))}
      </CardBody>
    </Card>
  );
}
