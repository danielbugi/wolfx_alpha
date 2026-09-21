import type { NextConfig } from 'next';
import { PHASE_PRODUCTION_BUILD } from 'next/constants';

export default function config(phase: string): NextConfig {
  // NEXT_PUBLIC_* values are inlined into the browser bundle at build time. If it
  // were unset, every visitor's browser would be pointed at *their own* localhost
  // (see the dev-only fallback in src/services/api.ts), so a production build must
  // say where the API is or fail here, before anything is emitted.
  if (phase === PHASE_PRODUCTION_BUILD && !process.env.NEXT_PUBLIC_API_BASE_URL) {
    throw new Error(
      'NEXT_PUBLIC_API_BASE_URL is not set. Production builds need the public URL of the ' +
        'FastAPI backend, e.g. NEXT_PUBLIC_API_BASE_URL=https://api.example.com (set it in the ' +
        'build environment or frontend/.env.local).'
    );
  }

  return {
    reactStrictMode: true,

    // Verification builds use their own output dir (NEXT_DIST_DIR=.next-build npm run build)
    // so they never overwrite the .next cache a running `next dev` is using.
    distDir: process.env.NEXT_DIST_DIR || '.next',

    compiler: {
      removeConsole: process.env.NODE_ENV === 'production',
    },
  };
}
