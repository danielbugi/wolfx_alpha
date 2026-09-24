// File: frontend/src/components/auth/AuthGate.tsx
'use client';

import React, { useEffect } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { useAuth } from '@/contexts/AuthContext';
import Sidebar from '@/components/layout/Sidebar';
import MobileNav from '@/components/layout/MobileNav';

const PUBLIC_PATHS = new Set(['/login']);

function Centered({ text }: { text: string }) {
  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-slate-50 text-sm text-slate-500">{text}</div>
  );
}

/**
 * The root of the app tree (see layout.tsx, inside AuthProvider): redirects to /login when signed out,
 * away from /login once signed in, and only wraps the Sidebar/MobileNav chrome around `children` once a
 * user is actually authenticated — the login screen itself renders full-page, with no nav.
 */
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const isPublicPath = PUBLIC_PATHS.has(pathname);

  useEffect(() => {
    if (status === 'unauthenticated' && !isPublicPath) {
      router.replace('/login');
    } else if (status === 'authenticated' && isPublicPath) {
      router.replace('/');
    }
  }, [status, isPublicPath, router]);

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
      </div>
    </div>
  );
}
