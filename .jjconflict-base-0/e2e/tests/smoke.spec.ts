import { test, expect } from '@playwright/test';

// Infra canary only — "is the stack up at all". Real UI coverage (login,
// topics, chat) lives in auth.spec.ts / topic-and-chat.spec.ts; this file used
// to also assert `page title non-empty` / `body visible`, which pass even when
// the app renders nothing useful, so they added no real signal and were
// replaced by the specs above.
// Must track playwright.config.ts's BACKEND_PORT resolution: on CI the runner
// is the dev box, which already serves an unrelated, already-running
// deployment on the 8081 default, so a hardcoded 8081 here silently health-checks
// the WRONG backend (always green, tests nothing about this run) instead of the
// one this suite just built and started via webServer.
const BACKEND_PORT = process.env.E2E_BACKEND_PORT ?? '8081';

test.describe('Smoke', () => {
  test('backend health check responds', async ({ request }) => {
    const response = await request.get(`http://localhost:${BACKEND_PORT}/healthz`);
    expect(response.ok()).toBeTruthy();
  });
});
