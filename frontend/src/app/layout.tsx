import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import './globals.css';
import { NextUIProvider } from '@nextui-org/react';
import DevQAPanel from '@/components/dev/DevQAPanel';
import RoutePerfCollector from '@/components/perf/RoutePerfCollector';
import { TopProgressProvider } from '@/lib/topProgress';
import { AuthProvider } from '@/contexts/AuthContext';
import AuthGate from '@/components/auth/AuthGate';

const inter = Inter({ subsets: ['latin'] });

export const metadata: Metadata = {
  title: 'Trading System - Professional Stock Analytics',
  description:
    'Advanced stock screening platform with ML-enhanced trading signals',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <NextUIProvider>
          <TopProgressProvider>
            <AuthProvider>
              <AuthGate>{children}</AuthGate>
            </AuthProvider>
            <DevQAPanel />
            <RoutePerfCollector />
          </TopProgressProvider>
        </NextUIProvider>
      </body>
    </html>
  );
}
