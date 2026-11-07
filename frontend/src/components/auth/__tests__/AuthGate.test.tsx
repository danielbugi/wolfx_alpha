import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const nav = { pathname: '/', replace: vi.fn() };
const auth = { status: 'unauthenticated' as 'loading' | 'authenticated' | 'unauthenticated' };

vi.mock('next/navigation', () => ({ usePathname: () => nav.pathname, useRouter: () => ({ replace: nav.replace }) }));
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => auth }));
vi.mock('@/components/layout/Sidebar', () => ({ default: () => <nav data-testid="sidebar" /> }));
vi.mock('@/components/layout/MobileNav', () => ({ default: () => <nav data-testid="mobilenav" /> }));
vi.mock('@/components/common/ProductDisclaimer', () => ({ default: () => null }));
vi.mock('@/lib/stockNav', () => ({ useStockNavCapture: () => undefined }));

import AuthGate from '@/components/auth/AuthGate';

beforeEach(() => {
  nav.replace.mockClear();
});

describe('AuthGate open paths', () => {
  it.each(['unauthenticated', 'loading'] as const)('renders /signals for a %s visitor in the public shell, with no redirect', (status) => {
    auth.status = status;
    nav.pathname = '/signals/POWI/2026-09-30/margin-expansion';
    render(<AuthGate><p>update page</p></AuthGate>);
    expect(screen.getByText('update page')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Sign in' }).getAttribute('href')).toBe('/login');
    expect(screen.queryByTestId('sidebar')).toBeNull();
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it('renders /signals inside the app chrome for a signed-in user, with no redirect', () => {
    auth.status = 'authenticated';
    nav.pathname = '/signals';
    render(<AuthGate><p>feed</p></AuthGate>);
    expect(screen.getByText('feed')).toBeTruthy();
    expect(screen.getByTestId('sidebar')).toBeTruthy();
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it('still sends a signed-out visitor of a private page to sign in', () => {
    auth.status = 'unauthenticated';
    nav.pathname = '/inflection';
    render(<AuthGate><p>private</p></AuthGate>);
    expect(screen.queryByText('private')).toBeNull();
    expect(nav.replace).toHaveBeenCalledWith('/login');
  });
});
