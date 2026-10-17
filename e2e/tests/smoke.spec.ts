import { test, expect } from '@playwright/test';

import { BACKEND_PORT } from '../stack.mjs';

// Infra canary only — "is the stack up at all". Real UI coverage (login,
// topics, chat) lives in auth.spec.ts / topic-and-chat.spec.ts; this file used
// to also assert `page title non-empty` / `body visible`, which pass even when
// the app renders nothing useful, so they added no real signal and were
// replaced by the specs above.
// BACKEND_PORT is the one the stack was started on (stack.mjs). On CI the
// runner was once the dev box, which serves an unrelated deployment on the 8081
// default, so a hardcoded 8081 here silently health-checked the WRONG backend
// (always green, tests nothing about this run).

test.describe('Smoke', () => {
  test('backend health check responds', async ({ request }) => {
    const response = await request.get(`http://localhost:${BACKEND_PORT}/healthz`);
    expect(response.ok()).toBeTruthy();
  });
});
