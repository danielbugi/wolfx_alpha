// File: frontend/src/components/layout/Sidebar.tsx
'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { motion } from 'framer-motion';
import { ArrowLeftStartOnRectangleIcon, ChevronDoubleLeftIcon, ChevronDoubleRightIcon, MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import { clsx } from 'clsx';
import { navGroupsForRole, isActiveRoute } from '@/lib/navigation';
import { useSidebarCollapsed } from '@/hooks/useSidebarCollapsed';
import SearchBox from '@/components/layout/SearchBox';
import Logo from '@/components/brand/Logo';
import ThemeSwitch from '@/components/layout/ThemeSwitch';
import MarketClock from '@/components/layout/MarketClock';
import { useAuth } from '@/contexts/AuthContext';
import { useSearchShortcut } from '@/hooks/useSearchShortcut';

/**
 * The persistent desktop nav (>= lg / 1024px). A vertical list has room for every route plus room to grow, unlike
 * the single horizontal bar it replaces (that one clipped the search box at 820px and ran out of room entirely
 * once Telegram was added — FRONTEND_FIX_MILESTONES.md FM6.1). Collapses to an icon rail; MobileNav covers < lg.
 *
 * Rendered as a translucent material so it reads as a structural layer above the page; the active-route highlight is
 * one shared element (layoutId) that springs from the old item to the new one on navigation.
 */
export default function Sidebar() {
  const pathname = usePathname();
  const { collapsed, setCollapsed } = useSidebarCollapsed();
  const { user, logout } = useAuth();
  const navGroups = navGroupsForRole(user?.role);
  const searchRef = useRef<HTMLInputElement>(null);
  // Set when the shortcut fires on a collapsed rail: expand first, focus once the field has mounted.
  const [focusPending, setFocusPending] = useState(false);

  const focusSearch = useCallback(() => {
    if (collapsed) {
      setCollapsed(false);
      setFocusPending(true);
    } else {
      searchRef.current?.focus();
      searchRef.current?.select();
    }
  }, [collapsed, setCollapsed]);
  useSearchShortcut(focusSearch, 'desktop');

  useEffect(() => {
    if (focusPending && !collapsed) {
      searchRef.current?.focus();
      setFocusPending(false);
    }
  }, [focusPending, collapsed]);

  return (
    <aside
      className={clsx(
        'material-chrome sticky top-0 z-20 hidden h-screen shrink-0 flex-col border-r border-black/[0.06] transition-[width] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] lg:flex',
        collapsed ? 'w-[68px]' : 'w-64',
      )}
    >
      <Link
        href="/"
        title="First Light"
        className={clsx('press flex h-16 shrink-0 items-center px-5', collapsed && 'justify-center px-0')}
      >
        <Logo collapsed={collapsed} />
      </Link>

      <div className={clsx('shrink-0 px-3 pb-2', collapsed && 'flex justify-center')}>
        {collapsed ? (
          <button
            type="button"
            title="Search symbol (/)"
            onClick={focusSearch}
            className="press flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 hover:bg-black/[0.05] hover:text-slate-800"
          >
            <MagnifyingGlassIcon className="h-5 w-5" />
          </button>
        ) : (
          <SearchBox inputRef={searchRef} showShortcut />
        )}
      </div>

      <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-3">
        {navGroups.map((group) => (
          <div key={group.label}>
            {!collapsed ? (
              <p className="px-2.5 pb-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-400">{group.label}</p>
            ) : (
              <div className="mx-auto mb-2 h-px w-6 bg-black/[0.08]" />
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
                      'press relative flex items-center gap-3 rounded-lg px-2.5 py-[7px] text-[13.5px] font-medium',
                      collapsed && 'justify-center px-0',
                      active ? 'text-slate-900' : 'text-slate-600 hover:bg-black/[0.04] hover:text-slate-900',
                    )}
                  >
                    {active && (
                      <motion.span
                        layoutId="sidebar-active"
                        aria-hidden
                        className="absolute inset-0 rounded-lg bg-white shadow-[0_1px_2px_rgb(0_0_0/0.06),0_0_0_0.5px_rgb(0_0_0/0.05)]"
                      />
                    )}
                    <item.icon className={clsx('relative h-[18px] w-[18px] shrink-0', active ? 'text-accent' : 'text-slate-400')} />
                    {!collapsed && <span className="relative truncate">{item.name}</span>}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className={clsx('shrink-0 px-3 pb-3', collapsed && 'flex justify-center px-0')}>
        <MarketClock compact={collapsed} />
      </div>

      {user && (
        <div className={clsx('shrink-0 border-t border-black/[0.06] p-3', collapsed && 'flex justify-center px-0')}>
          {!collapsed && (
            <div className="mb-2 flex items-center gap-2.5 px-1" title={user.email}>
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-b from-slate-400 to-slate-500 text-xs font-semibold uppercase text-white">
                {user.email.charAt(0)}
              </span>
              <div className="min-w-0">
                <p className="truncate text-xs font-medium text-slate-800">{user.email}</p>
                <p className="text-[11px] capitalize text-slate-500">{user.role}</p>
              </div>
            </div>
          )}
          <button
            type="button"
            title="Sign out"
            onClick={() => logout()}
            className={clsx(
              'press flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs font-medium text-slate-500 hover:bg-black/[0.04] hover:text-slate-800',
              collapsed ? 'justify-center px-2.5' : 'w-full',
            )}
          >
            <ArrowLeftStartOnRectangleIcon className="h-4 w-4" />
            {!collapsed && 'Sign out'}
          </button>
        </div>
      )}

      <div className={clsx('shrink-0 border-t border-black/[0.06] p-3', collapsed && 'flex flex-col items-center gap-1 px-0')}>
        {collapsed ? <ThemeSwitch compact /> : <ThemeSwitch className="mb-2" />}
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          className={clsx(
            'press flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs font-medium text-slate-500 hover:bg-black/[0.04] hover:text-slate-800',
            collapsed ? 'justify-center' : 'w-full',
          )}
        >
          {collapsed ? <ChevronDoubleRightIcon className="h-4 w-4" /> : <ChevronDoubleLeftIcon className="h-4 w-4" />}
          {!collapsed && 'Collapse'}
        </button>
      </div>
    </aside>
  );
}
