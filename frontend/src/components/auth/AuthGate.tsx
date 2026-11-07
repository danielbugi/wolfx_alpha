// File: frontend/src/components/auth/AuthGate.tsx
'use client';

import React, { useEffect } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useAuth } from '@/contexts/AuthContext';
import Sidebar from '@/components/layout/Sidebar';
import MobileNav from '@/components/layout/MobileNav';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import ProductDisclaimer from '@/components/common/ProductDisclaimer';
import { isProductRoute } from '@/lib/navigation';
import { useStockNavCapture } from '@/lib/stockNav';
import { isOpenPath } from '@/lib/signals';

const PUBLIC_PATHS = new Set(['/login']);

function Centered({ text }: { text: string }) {
  return (
    <div className="flex min-h-screen w-full flex-col items-center justify-center gap-3 bg-slate-50 text-sm text-slate-500">
      <LoadingSpinner size="medium" />
      {text}
    </div>
  );
}

/** The frame of an OPEN page (lib/signals.ts) for a visitor who is not signed in: the brand and a sign-in link, nothing else. */
function PublicShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
          <Link href="/signals" className="text-sm font-semibold tracking-tight text-slate-900">First Light</Link>
          <Link href="/login" className="text-sm text-slate-500 hover:text-slate-800">Sign in</Link>
        </div>
      </header>
      <main className="min-w-0">{children}</main>
    </div>
  );
}

/**
 * The root of the app tree (see layout.tsx, inside AuthProvider): redirects to /login when signed out,
 * away from /login once signed in, and only wraps the Sidebar/MobileNav chrome around `children` once a
 * user is actually authenticated — the login screen itself renders full-page, with no nav.
 * OPEN paths (the public /signals pages) are a third kind: rendered for everyone with no redirect either way — inside the app chrome when signed
 * in, inside PublicShell otherwise (also while the session is still loading, so a visitor never waits on auth).
 */
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const isPublicPath = PUBLIC_PATHS.has(pathname);
  const isOpen = isOpenPath(pathname);
  // Remembers which stock list a stock was opened from, for the stock page's next/previous arrows.
  useStockNavCapture();

  useEffect(() => {
    if (isOpen) return;
    if (status === 'unauthenticated' && !isPublicPath) {
      router.replace('/login');
    } else if (status === 'authenticated' && isPublicPath) {
      router.replace('/');
    }
  }, [status, isPublicPath, isOpen, router]);

  if (isOpen && status !== 'authenticated') return <PublicShell>{children}</PublicShell>;
  if (status === 'loading') return <Centered text="Loading…" />;
  if (status === 'unauthenticated' && !isPublicPath) return <Centered text="Redirecting to sign in…" />;
  if (status === 'authenticated' && isPublicPath) return <Centered text="Redirecting…" />;
  if (isPublicPath) return <>{children}</>;

  return (
    <div className="flex min-h-screen bg-slate-50">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <MobileNav />
        <main className="min-w-0 flex-1">{children}</main>
        {isProductRoute(pathname) && <ProductDisclaimer />}
      </div>
    </div>
  );
}
