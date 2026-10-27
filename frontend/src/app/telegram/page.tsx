'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Card } from '@nextui-org/react';
import { ExclamationTriangleIcon, InformationCircleIcon } from '@heroicons/react/24/outline';
import { telegramApi, toControlError } from '@/services/telegramApi';
import type { ControlErrorInfo, ControlOverview, MessageFilters as ApiFilters, MessageList } from '@/services/telegramApi';
import { formatFull, formatWhen } from '@/lib/telegramFormat';
import { useControlToken } from '@/hooks/useControlToken';
import { usePagePerf } from '@/hooks/usePagePerf';
import ErrorAlert from '@/components/common/ErrorAlert';
import OverviewTiles from '@/components/telegram/OverviewTiles';
import ConfigPanel from '@/components/telegram/ConfigPanel';
import ControlTokenBar from '@/components/telegram/ControlTokenBar';
import MessageFilters, { DEFAULT_FILTERS, NO_FILTERS } from '@/components/telegram/MessageFilters';
import type { FilterState } from '@/components/telegram/MessageFilters';
import MessageTable from '@/components/telegram/MessageTable';
import type { RowMode } from '@/components/telegram/MessageTable';
import MessageModal from '@/components/telegram/MessageModal';
import ComposeModal from '@/components/telegram/ComposeModal';
import LiveNowPanel from '@/components/telegram/LiveNowPanel';
import AccessPanel from '@/components/telegram/AccessPanel';
import { SideTabs } from '@/components/telegram/SideTabs';
import type { SideTab } from '@/components/telegram/SideTabs';
import { Btn } from '@/components/telegram/ui';
import { useAuth } from '@/contexts/AuthContext';
import LoadingSpinner from '@/components/common/LoadingSpinner';

const PAGE_SIZE = 25;

type Tab = 'messages' | 'bots';

const TAB_COPY: Record<Tab, { title: string; description: string }> = {
  messages: {
    title: 'Messages Hub',
    description:
      'Every message First Light posted to the channels, with edit and delete, under the same rules as the rest of the system: production stays locked until launch.',
  },
  bots: {
    title: 'Bots',
    description: 'Who may use the First Light private assistant — approve or decline requests, create invite links, and revoke access.',
  },
};

function toApiFilters(f: FilterState, offset: number): ApiFilters {
  return {
    target: f.target || undefined,
    kind: f.kind || undefined,
    status: f.status || undefined,
    format: f.format || undefined,
    day: f.day || undefined,
    q: f.q || undefined,
    limit: PAGE_SIZE,
    offset,
  };
}

export default function TelegramControlPage() {
  const { markLoaded } = usePagePerf('/telegram');
  const { token, setToken, clearToken } = useControlToken();
  const { user } = useAuth();
  const isOwner = user?.role === 'owner';

  const [tab, setTab] = useState<Tab>('messages');
  const [botsTick, setBotsTick] = useState(0);        // bumped to force AccessPanel to remount + refetch on "Refresh"

  const tabs: SideTab[] = isOwner ? [{ id: 'messages', label: 'Messages Hub' }, { id: 'bots', label: 'Bots' }] : [{ id: 'messages', label: 'Messages Hub' }];

  const [overview, setOverview] = useState<ControlOverview | null>(null);
  const [overviewError, setOverviewError] = useState<ControlErrorInfo | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(true);

  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [reloadKey, setReloadKey] = useState(0);        // bumped by Refresh so the "What's live now" panel reloads too
  const [offset, setOffset] = useState(0);
  const [list, setList] = useState<MessageList | null>(null);
  const [listError, setListError] = useState<ControlErrorInfo | null>(null);
  const [listLoading, setListLoading] = useState(true);

  const [open, setOpen] = useState<{ id: number; mode: RowMode } | null>(null);
  const [composeOpen, setComposeOpen] = useState(false);
  const latestList = useRef(0);

  const loadOverview = useCallback(async () => {
    setOverviewLoading(true);
    try {
      setOverview(await telegramApi.getOverview());
      setOverviewError(null);
      markLoaded();
    } catch (err) {
      setOverviewError(toControlError(err));
    } finally {
      setOverviewLoading(false);
    }
  }, [markLoaded]);

  const loadList = useCallback(async () => {
    const mine = ++latestList.current;                  // a slow answer to an older filter must never overwrite a newer one
    setListLoading(true);
    try {
      const data = await telegramApi.listMessages(toApiFilters(filters, offset));
      if (mine !== latestList.current) return;
      if (data.items.length === 0 && data.total > 0 && data.offset > 0) {
        setOffset(Math.floor((data.total - 1) / data.limit) * data.limit);    // the last row of this page is gone: go to the last page that exists
        return;
      }
      setList(data);
      setListError(null);
    } catch (err) {
      if (mine === latestList.current) setListError(toControlError(err));
    } finally {
      if (mine === latestList.current) setListLoading(false);
    }
  }, [filters, offset]);

  useEffect(() => {
    loadOverview();
  }, [loadOverview]);
  useEffect(() => {
    loadList();
  }, [loadList]);
  useEffect(() => {
    if (tab === 'bots' && !isOwner) setTab('messages');       // only the Owner has a Bots tab to begin with
  }, [tab, isOwner]);

  const refreshAll = useCallback(() => {
    loadOverview();
    loadList();
    setReloadKey((n) => n + 1);
  }, [loadOverview, loadList]);

  const handleRefresh = useCallback(() => {
    if (tab === 'bots') setBotsTick((n) => n + 1);
    else refreshAll();
  }, [tab, refreshAll]);

  const changeTab = useCallback((id: string) => setTab(id as Tab), []);

  const changeFilters = useCallback((next: FilterState) => {
    setFilters(next);
    setOffset(0);
  }, []);

  const closeModal = useCallback(() => setOpen(null), []);
  const timezone = overview?.timezone ?? list?.timezone ?? 'UTC';
  const filtered = (Object.keys(NO_FILTERS) as (keyof FilterState)[]).some((k) => k !== 'format' && filters[k] !== '') || filters.format === 'old';
  const everythingFailed = overviewError !== null && listError !== null && overview === null && list === null;
  const copy = TAB_COPY[tab];

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto px-4 pb-12 pt-6 sm:px-6 lg:px-8 max-w-7xl space-y-4">
        <div className="px-1 pt-2 pb-1">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="large-title">Telegram</h1>
              <p className="mt-1.5 max-w-3xl text-[15px] leading-relaxed text-slate-500">{copy.description}</p>
              {tab === 'messages' && overview && (
                <p className="mt-2 text-xs text-slate-400">
                  Updated {formatWhen(overview.generated_at, timezone)} ({timezone})
                </p>
              )}
            </div>
            <div className="flex shrink-0 gap-2">
              {tab === 'messages' && isOwner && (
                <Btn size="sm" disabled={!overview} onClick={() => setComposeOpen(true)}>
                  New post
                </Btn>
              )}
              <Btn variant="primary" size="sm" onClick={handleRefresh} disabled={tab === 'messages' && (overviewLoading || listLoading)}>
                {tab === 'messages' && (overviewLoading || listLoading) ? 'Refreshing…' : 'Refresh'}
              </Btn>
            </div>
          </div>
          {tab === 'messages' && overview && (
            <div className="mt-4 border-t border-slate-100 pt-3">
              {isOwner ? (
                <ControlTokenBar controlsEnabled={overview.controls_enabled} token={token} onSubmit={setToken} onClear={clearToken} />
              ) : (
                <p className="text-xs text-slate-500">View only — only the Owner can edit or delete channel posts.</p>
              )}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-4 md:flex-row">
          <SideTabs tabs={tabs} active={tab} onChange={changeTab} />

          <div className="min-w-0 flex-1 space-y-4">
            {tab === 'bots' ? (
              isOwner && <AccessPanel key={botsTick} />
            ) : (
              <>
                {everythingFailed && (
                  <ErrorAlert title="Cannot load the Messages Hub" message={overviewError.message} onRetry={refreshAll} />
                )}

                {overview && overview.warnings.length > 0 && (
                  <ul className="space-y-1 rounded-lg border border-amber-200 bg-amber-50 p-3" role="status">
                    {overview.warnings.map((w) => (
                      <li key={w} className="flex items-start gap-2 text-sm text-amber-900">
                        <ExclamationTriangleIcon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden /> {w}
                      </li>
                    ))}
                  </ul>
                )}

                {overviewLoading && !overview && !everythingFailed && (
                  <div className="flex justify-center py-10"><LoadingSpinner /></div>
                )}
                {overviewError && overview === null && !everythingFailed && (
                  <ErrorAlert title="Cannot load the overview" message={overviewError.message} onRetry={loadOverview} />
                )}

                {overview && <OverviewTiles targets={overview.targets} timezone={timezone} />}

                {overview && (
                  <LiveNowPanel
                    target="prod"
                    timezone={timezone}
                    isOwner={isOwner}
                    token={token}
                    controlsEnabled={overview.controls_enabled}
                    reloadKey={reloadKey}
                    onChanged={() => {
                      loadOverview();
                      loadList();
                    }}
                    onBadToken={clearToken}
                  />
                )}

                {overview && (
                  <p className="flex items-start gap-2 text-xs text-slate-500">
                    <InformationCircleIcon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                    {overview.tracking_since
                      ? `Recording since ${formatFull(overview.tracking_since, timezone)}. Telegram does not let a bot read a channel’s history, so posts sent before then are not listed.`
                      : 'Nothing has been recorded yet. Every message a script sends to a channel from now on is listed here.'}
                  </p>
                )}

                {overview && (
                  <section aria-labelledby="messages-heading" className="space-y-3">
                    <h2 id="messages-heading" className="text-lg font-semibold text-slate-800">Messages</h2>
                    <Card className="border border-slate-200 p-4 shadow-sm">
                      <MessageFilters value={filters} kinds={overview.kinds} onChange={changeFilters} />
                    </Card>
                    {listError && list === null ? (
                      <ErrorAlert title="Cannot load the messages" message={listError.message} onRetry={loadList} />
                    ) : list === null ? (
                      <div className="flex justify-center py-10"><LoadingSpinner /></div>
                    ) : (
                      <>
                        {listError && <ErrorAlert title="The list may be out of date" message={listError.message} onRetry={loadList} />}
                        <MessageTable
                          data={list}
                          loading={listLoading}
                          filtered={filtered}
                          trackingSince={overview.tracking_since}
                          onOpen={(id, mode) => setOpen({ id, mode })}
                          onPage={setOffset}
                        />
                      </>
                    )}
                  </section>
                )}

                {overview && <ConfigPanel items={overview.config} />}
              </>
            )}
          </div>
        </div>
      </div>

      <MessageModal
        id={open?.id ?? null}
        initialMode={open?.mode ?? 'view'}
        timezone={timezone}
        token={token}
        controlsEnabled={overview?.controls_enabled ?? false}
        isOwner={isOwner}
        onClose={closeModal}
        onChanged={refreshAll}
        onBadToken={clearToken}
      />

      {isOwner && (
        <ComposeModal
          open={composeOpen}
          targets={overview?.targets ?? []}
          kinds={overview?.kinds ?? []}
          token={token}
          controlsEnabled={overview?.controls_enabled ?? false}
          onClose={() => setComposeOpen(false)}
          onSent={refreshAll}
          onBadToken={clearToken}
        />
      )}
    </div>
  );
}
