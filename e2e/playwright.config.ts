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
  timeout: 30_000,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: process.env.BASE_URL || `http://localhost:${FRONTEND_PORT}`,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
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
      command: `cd ../frontend && pnpm run dev -- --port ${FRONTEND_PORT} --strictPort`,
      url: `http://localhost:${FRONTEND_PORT}`,
      env: { BACKEND_URL },
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
