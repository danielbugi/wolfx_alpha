// File: frontend/src/components/layout/MobileNav.tsx
'use client';

import React, { useCallback, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { motion } from 'framer-motion';
import { ArrowLeftStartOnRectangleIcon, Bars3Icon, MagnifyingGlassIcon, XMarkIcon } from '@heroicons/react/24/outline';
import { clsx } from 'clsx';
import { navGroupsForRole, isActiveRoute } from '@/lib/navigation';
import Dialog from '@/components/common/Dialog';
import SearchBox from '@/components/layout/SearchBox';
import Logo from '@/components/brand/Logo';
import ThemeSwitch from '@/components/layout/ThemeSwitch';
import MarketClock from '@/components/layout/MarketClock';
import { useAuth } from '@/contexts/AuthContext';
import { useSearchShortcut } from '@/hooks/useSearchShortcut';

/** A slim top bar + slide-over drawer, for narrower than lg (1024px) where Sidebar hides itself. */
export default function MobileNav() {
  const pathname = usePathname();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const { user, logout } = useAuth();
  const navGroups = navGroupsForRole(user?.role);
  useSearchShortcut(useCallback(() => setSearchOpen(true), []), 'mobile');

  return (
    <div className="lg:hidden">
      <div className="material-chrome sticky top-0 z-30 flex h-14 items-center gap-1 border-b border-black/[0.06] px-2">
        <button
          type="button"
          aria-label="Open navigation"
          onClick={() => setDrawerOpen(true)}
          className="press flex h-10 w-10 items-center justify-center rounded-full text-slate-700 hover:bg-black/[0.05]"
        >
          <Bars3Icon className="h-6 w-6" />
        </button>
        <Link href="/" className="flex min-w-0 flex-1 items-center px-1">
          <Logo />
        </Link>
        <button
          type="button"
          aria-label={searchOpen ? 'Close search' : 'Search symbol'}
          aria-pressed={searchOpen}
          onClick={() => setSearchOpen((v) => !v)}
          className={clsx('press flex h-10 w-10 items-center justify-center rounded-full', searchOpen ? 'bg-black/[0.06] text-slate-900' : 'text-slate-700 hover:bg-black/[0.05]')}
        >
          {searchOpen ? <XMarkIcon className="h-5 w-5" /> : <MagnifyingGlassIcon className="h-5 w-5" />}
        </button>
      </div>

      {searchOpen && (
        <div className="material-chrome sticky top-14 z-30 border-b border-black/[0.06] px-3 py-2">
          <SearchBox autoFocus onNavigate={() => setSearchOpen(false)} />
        </div>
      )}

      <Dialog open={drawerOpen} onClose={() => setDrawerOpen(false)} labelledBy="mobile-nav-title" variant="drawer-left">
        <div className="flex h-16 shrink-0 items-center justify-between px-4">
          <span id="mobile-nav-title">
            <Logo />
          </span>
          <button
            type="button"
            aria-label="Close navigation"
            onClick={() => setDrawerOpen(false)}
            className="press flex h-8 w-8 items-center justify-center rounded-full bg-black/[0.05] text-slate-500 hover:bg-black/[0.08] hover:text-slate-800"
          >
            <XMarkIcon className="h-5 w-5" />
          </button>
        </div>
        <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-2">
          {navGroups.map((group) => (
            <div key={group.label}>
              <p className="px-2.5 pb-1.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-slate-400">{group.label}</p>
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
                        'press relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-[15px] font-medium',
                        active ? 'text-slate-900' : 'text-slate-600 hover:bg-black/[0.04] hover:text-slate-900',
                      )}
                    >
                      {active && (
                        <motion.span
                          layoutId="mobile-nav-active"
                          aria-hidden
                          className="absolute inset-0 rounded-xl bg-accent-soft"
                        />
                      )}
                      <item.icon className={clsx('relative h-5 w-5 shrink-0', active ? 'text-accent' : 'text-slate-400')} />
                      <span className="relative truncate">{item.name}</span>
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>
        <div className="shrink-0 space-y-3 px-4 pb-3">
          <MarketClock />
          <ThemeSwitch />
        </div>
        {user && (
          <div className="shrink-0 border-t border-black/[0.06] p-4">
            <div className="mb-3 flex items-center gap-2.5" title={user.email}>
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-b from-slate-400 to-slate-500 text-sm font-semibold uppercase text-white">
                {user.email.charAt(0)}
              </span>
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-800">{user.email}</p>
                <p className="text-xs capitalize text-slate-500">{user.role}</p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => {
                setDrawerOpen(false);
                logout();
              }}
              className="press flex w-full items-center justify-center gap-2 rounded-xl bg-black/[0.05] px-3 py-2.5 text-sm font-medium text-slate-700 hover:bg-black/[0.08]"
            >
              <ArrowLeftStartOnRectangleIcon className="h-4 w-4" /> Sign out
            </button>
          </div>
        )}
      </Dialog>
    </div>
  );
}
