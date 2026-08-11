import { defineConfig } from '@playwright/test';

// Ports are overridable because the CI runner is the dev box itself, which is
// already serving the dev deployment on 8081 — a hardcoded port made Playwright
// fail with "8081 is already used" before a single test ran. Defaults keep local
// runs exactly as they were.
const BACKEND_PORT = process.env.E2E_BACKEND_PORT ?? '8081';
const FRONTEND_PORT = process.env.E2E_FRONTEND_PORT ?? '3000';
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;

export default defineConfig({
  testDir: './tests',
  // 60s (not 30s): the vite dev server compiles routes on-demand, and the first
  // navigation into a heavy route (the project workspace pulls in tiptap /
  // prosemirror / DocEditor) can take >30s to transform on a cold start.
  timeout: 60_000,
  // Serial on CI: parallel workers each trigger a fresh cold compile at once,
  // and the resulting storm blows the per-test timeout. The suite is small, so
  // serializing costs little and makes cold runs deterministic. Local stays
  // parallel (dev servers are usually already warm via reuseExistingServer).
  workers: process.env.CI ? 1 : undefined,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: process.env.BASE_URL || `http://localhost:${FRONTEND_PORT}`,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    // The CI runner IS the dev box, so a failed run cannot be reproduced by
    // re-running it later — the deployment underneath has already moved on.
    // A video of the failing run is the only artifact that survives that.
    video: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
  ],
  webServer: [
    {
      command: `cd ../backend && uv run uvicorn app.main:app --host 0.0.0.0 --port ${BACKEND_PORT}`,
      url: `${BACKEND_URL}/healthz`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
    },
    {
      // `pnpm run dev -- --port N` forwards the separator itself, so vite is
      // invoked as `vite -- --port N` and takes `--` as its POSITIONAL root
      // directory. It then serves a directory that does not exist: no error, no
      // banner, never reachable — which is exactly how it failed in CI.
      command: `cd ../frontend && pnpm exec vite --port ${FRONTEND_PORT} --strictPort`,
      url: `http://localhost:${FRONTEND_PORT}`,
      // VITE_API_BASE_URL=/api makes the 知是 1.0 layer prefix its calls with
      // /api (so /users/auth/login → /api/users/auth/login), which the proxy's
      // strip-one-/api rewrite then forwards to the backend as /users/auth/login.
      // Production bakes the same value at build time; without it the 1.0 routes
      // are relative (/users/...), miss the /api proxy entirely, and hit the SPA.
      env: { BACKEND_URL, VITE_API_BASE_URL: '/api' },
      reuseExistingServer: !process.env.CI,
      // Same 180s the backend gets: a cold vite start pre-bundles deps and runs
      // the legacy plugin, on a runner that is also building and deploying.
      timeout: 180_000,
    },
  ],
});
