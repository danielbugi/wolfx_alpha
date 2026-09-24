// File: frontend/src/components/layout/Sidebar.tsx
'use client';

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ArrowLeftStartOnRectangleIcon, ArrowTrendingUpIcon, ChevronDoubleLeftIcon, ChevronDoubleRightIcon, MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import { clsx } from 'clsx';
import { navGroupsForRole, isActiveRoute } from '@/lib/navigation';
import { useSidebarCollapsed } from '@/hooks/useSidebarCollapsed';
import SearchBox from '@/components/layout/SearchBox';
import { useAuth } from '@/contexts/AuthContext';

/**
 * The persistent desktop nav (>= lg / 1024px). A vertical list has room for every route plus room to grow, unlike
 * the single horizontal bar it replaces (that one clipped the search box at 820px and ran out of room entirely
 * once Telegram was added — FRONTEND_FIX_MILESTONES.md FM6.1). Collapses to an icon rail; MobileNav covers < lg.
 */
export default function Sidebar() {
  const pathname = usePathname();
  const { collapsed, setCollapsed } = useSidebarCollapsed();
  const { user, logout } = useAuth();
  const navGroups = navGroupsForRole(user?.role);

  return (
    <aside
      className={clsx(
        'sticky top-0 hidden h-screen shrink-0 flex-col border-r border-slate-200 bg-white transition-[width] duration-200 ease-out lg:flex',
        collapsed ? 'w-16' : 'w-60',
      )}
    >
      <Link
        href="/"
        title="Trading System"
        className={clsx('flex h-14 shrink-0 items-center gap-2 border-b border-slate-100 px-4', collapsed && 'justify-center px-0')}
      >
        <ArrowTrendingUpIcon className="h-6 w-6 shrink-0 text-slate-700" />
        {!collapsed && <span className="truncate text-sm font-bold tracking-wide text-slate-800">Trading System</span>}
      </Link>

      <div className="shrink-0 border-b border-slate-100 p-3">
        {collapsed ? (
          <button
            type="button"
            title="Search symbol"
            onClick={() => setCollapsed(false)}
            className="flex h-9 w-9 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-700"
          >
            <MagnifyingGlassIcon className="h-5 w-5" />
          </button>
        ) : (
          <SearchBox />
        )}
      </div>

      <nav className="flex-1 space-y-5 overflow-y-auto px-2 py-3">
        {navGroups.map((group) => (
          <div key={group.label}>
            {!collapsed && (
              <p className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{group.label}</p>
            )}
            <div className="space-y-0.5">
              {group.items.map((item) => {
                const active = isActiveRoute(pathname, item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    title={collapsed ? `${item.name} — ${item.description}` : undefined}
                    aria-current={active ? 'page' : undefined}
                    className={clsx(
                      'flex items-center gap-3 rounded-md px-2.5 py-2 text-sm font-medium transition-colors',
                      collapsed && 'justify-center px-0',
                      active ? 'bg-slate-100 text-slate-900' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900',
                    )}
                  >
                    <item.icon className={clsx('h-5 w-5 shrink-0', active ? 'text-slate-800' : 'text-slate-400')} />
                    {!collapsed && <span className="truncate">{item.name}</span>}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {user && (
        <div className={clsx('shrink-0 border-t border-slate-100 p-2', collapsed && 'flex justify-center')}>
          {!collapsed && (
            <div className="mb-1 truncate px-2 text-xs text-slate-500" title={user.email}>
              {user.email} · <span className="capitalize">{user.role}</span>
            </div>
          )}
          <button
            type="button"
            title="Sign out"
            onClick={() => logout()}
            className={clsx(
              'flex items-center gap-2 rounded-md px-2.5 py-2 text-xs font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-800',
              collapsed ? 'justify-center px-0' : 'w-full',
            )}
          >
            <ArrowLeftStartOnRectangleIcon className="h-4 w-4" />
            {!collapsed && 'Sign out'}
          </button>
        </div>
      )}

      <div className="shrink-0 border-t border-slate-100 p-2">
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          className={clsx(
            'flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-xs font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-800',
            collapsed && 'justify-center px-0',
          )}
        >
          {collapsed ? <ChevronDoubleRightIcon className="h-4 w-4" /> : <ChevronDoubleLeftIcon className="h-4 w-4" />}
          {!collapsed && 'Collapse'}
        </button>
      </div>
    </aside>
  );
}
