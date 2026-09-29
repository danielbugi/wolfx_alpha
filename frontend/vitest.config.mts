import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  esbuild: { jsx: 'automatic' },
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./vitest.setup.ts'],
    // services/api.ts refuses to start without an API URL outside `next dev`; tests never make a real request.
    env: { NEXT_PUBLIC_API_BASE_URL: 'http://api.test.invalid' },
  },
});
