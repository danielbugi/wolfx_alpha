import type { Metadata, Viewport } from 'next';
import { Inter } from 'next/font/google';
import './globals.css';
import { NextUIProvider } from '@nextui-org/react';
import MotionProvider from '@/components/ui/MotionProvider';
import DevQAPanel from '@/components/dev/DevQAPanel';
import RoutePerfCollector from '@/components/perf/RoutePerfCollector';
import { TopProgressProvider } from '@/lib/topProgress';
import { AuthProvider } from '@/contexts/AuthContext';
import { THEME_BOOT_SCRIPT } from '@/lib/themeScript';
import AuthGate from '@/components/auth/AuthGate';

const inter = Inter({ subsets: ['latin'], variable: '--font-inter', display: 'swap' });

export const metadata: Metadata = {
  title: 'First Light',
  description:
    'Advanced stock screening platform with ML-enhanced trading signals',
};

export const viewport: Viewport = {
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#f5f5f7' },
    { media: '(prefers-color-scheme: dark)', color: '#0e0e10' },
  ],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // suppressHydrationWarning: THEME_BOOT_SCRIPT sets data-theme on <html> before React hydrates.
    <html lang="en" className={inter.variable} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body className="font-sans">
        <NextUIProvider>
          <MotionProvider>
            <TopProgressProvider>
              <AuthProvider>
                <AuthGate>{children}</AuthGate>
              </AuthProvider>
              <DevQAPanel />
              <RoutePerfCollector />
            </TopProgressProvider>
          </MotionProvider>
        </NextUIProvider>
      </body>
    </html>
  );
}
