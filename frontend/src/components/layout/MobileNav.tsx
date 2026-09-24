// File: frontend/src/components/layout/MobileNav.tsx
'use client';

import React, { useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ArrowLeftStartOnRectangleIcon, ArrowTrendingUpIcon, Bars3Icon, MagnifyingGlassIcon, XMarkIcon } from '@heroicons/react/24/outline';
import { clsx } from 'clsx';
import { navGroupsForRole, isActiveRoute } from '@/lib/navigation';
import Dialog from '@/components/common/Dialog';
import SearchBox from '@/components/layout/SearchBox';
import { useAuth } from '@/contexts/AuthContext';

/** A slim top bar + slide-over drawer, for narrower than lg (1024px) where Sidebar hides itself. */
export default function MobileNav() {
  const pathname = usePathname();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const { user, logout } = useAuth();
  const navGroups = navGroupsForRole(user?.role);

  return (
    <div className="lg:hidden">
      <div className="sticky top-0 z-30 flex h-14 items-center gap-1 border-b border-slate-200 bg-white px-2">
        <button
          type="button"
          aria-label="Open navigation"
          onClick={() => setDrawerOpen(true)}
          className="flex h-9 w-9 items-center justify-center rounded-md text-slate-600 hover:bg-slate-100"
        >
          <Bars3Icon className="h-6 w-6" />
        </button>
        <Link href="/" className="flex min-w-0 flex-1 items-center gap-2 px-1">
          <ArrowTrendingUpIcon className="h-5 w-5 shrink-0 text-slate-700" />
          <span className="truncate text-sm font-bold text-slate-800">Trading System</span>
        </Link>
        <button
          type="button"
          aria-label={searchOpen ? 'Close search' : 'Search symbol'}
          aria-pressed={searchOpen}
          onClick={() => setSearchOpen((v) => !v)}
          className={clsx('flex h-9 w-9 items-center justify-center rounded-md', searchOpen ? 'bg-slate-100 text-slate-800' : 'text-slate-600 hover:bg-slate-100')}
        >
          {searchOpen ? <XMarkIcon className="h-5 w-5" /> : <MagnifyingGlassIcon className="h-5 w-5" />}
        </button>
      </div>

      {searchOpen && (
        <div className="sticky top-14 z-30 border-b border-slate-200 bg-white p-2">
          <SearchBox autoFocus onNavigate={() => setSearchOpen(false)} />
        </div>
      )}

      <Dialog open={drawerOpen} onClose={() => setDrawerOpen(false)} labelledBy="mobile-nav-title" variant="drawer-left">
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-slate-100 px-4">
          <span id="mobile-nav-title" className="flex items-center gap-2 text-sm font-bold text-slate-800">
            <ArrowTrendingUpIcon className="h-5 w-5 text-slate-700" /> Trading System
          </span>
          <button
            type="button"
            aria-label="Close navigation"
            onClick={() => setDrawerOpen(false)}
            className="flex h-8 w-8 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100"
          >
            <XMarkIcon className="h-5 w-5" />
          </button>
        </div>
        <nav className="flex-1 space-y-5 overflow-y-auto px-2 py-3">
          {navGroups.map((group) => (
            <div key={group.label}>
              <p className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{group.label}</p>
              <div className="space-y-0.5">
                {group.items.map((item) => {
                  const active = isActiveRoute(pathname, item.href);
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      onClick={() => setDrawerOpen(false)}
                      aria-current={active ? 'page' : undefined}
                      className={clsx(
                        'flex items-center gap-3 rounded-md px-2.5 py-2.5 text-sm font-medium',
                        active ? 'bg-slate-100 text-slate-900' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900',
                      )}
                    >
                      <item.icon className={clsx('h-5 w-5 shrink-0', active ? 'text-slate-800' : 'text-slate-400')} />
                      <span className="truncate">{item.name}</span>
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>
        {user && (
          <div className="shrink-0 border-t border-slate-100 p-3">
            <div className="mb-2 truncate text-xs text-slate-500" title={user.email}>
              {user.email} · <span className="capitalize">{user.role}</span>
            </div>
            <button
              type="button"
              onClick={() => {
                setDrawerOpen(false);
                logout();
              }}
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-sm font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-800"
            >
              <ArrowLeftStartOnRectangleIcon className="h-4 w-4" /> Sign out
            </button>
          </div>
        )}
      </Dialog>
    </div>
  );
}
