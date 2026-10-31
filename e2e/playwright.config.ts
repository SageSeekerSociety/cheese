import { defineConfig } from '@playwright/test';

import { FRONTEND_PORT, stackServers } from './stack.mjs';

// The ports and the servers below come from stack.mjs, which `task
// dev:sandbox` starts as well.

// Specs that mount source components straight from the dev server
// (`/src/...`, `/node_modules/.vite/deps/...`) rather than visiting the app.
// A production build has no such paths, so on CI they get a dev server of
// their own; locally the one frontend server is already a dev server.
const COMPONENT_SPECS = ['chat-file-reference-hit-area.spec.ts', 'design-region-note.spec.ts', 'chat-back-to-latest.spec.ts', 'work-panel-file-tab-width.spec.ts', 'work-panel-tab-scroll-arrow.spec.ts', 'work-panel-pdf-text-layer.spec.ts', 'model-connections-layout.spec.ts', 'settings-overlay-macos-arrow.spec.ts', 'guide-bubble-does-not-cover-text.spec.ts', 'sidebar-resizer-follows-the-pointer.spec.ts'];
const COMPONENT_PORT = process.env.CI ? (process.env.E2E_COMPONENT_PORT ?? '3300') : FRONTEND_PORT;

export default defineConfig({
  testDir: './tests',
  globalSetup: './global-setup.ts',
  // About twice the slowest test measured against the production build on CI
  // (22.9 s, a layout check looping over several viewports). A file whose
  // tests sweep many screens sets its own, longer limit.
  timeout: 45_000,
  // Serial on CI: every spec shares one backend and signs in as alice, and
  // what one test leaves behind (an unread badge, a notification) shows up in
  // another's page. Running them concurrently against that one backend has
  // not been tried; the five CI shards are where the parallelism comes from.
  // Local runs stay parallel.
  workers: process.env.CI ? 1 : undefined,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [
    ['github'],
    ['json', { outputFile: 'test-results/playwright-results.json' }],
  ] : 'list',
  use: {
    connectOptions: process.env.E2E_BROWSER_WS_ENDPOINT ? {
      wsEndpoint: process.env.E2E_BROWSER_WS_ENDPOINT,
      exposeNetwork: '<loopback>',
    } : undefined,
    // The production build registers a service worker that precaches the
    // whole bundle. Every test opens a fresh context, so each one would
    // install it again and download every chunk; no spec is about offline
    // behaviour.
    serviceWorkers: process.env.CI ? 'block' : 'allow',
    // Existing workspace scenarios assert Chinese UI labels explicitly.
    locale: 'zh-CN',
    baseURL: process.env.BASE_URL || `http://localhost:${FRONTEND_PORT}`,
    // Keep the failed attempt even when its retry passes. The workflow uploads
    // the retained trace after a passing retry, while clean runs retain only the
    // JSON results and CI evidence.
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // The CI runner IS the dev box, so a failed run cannot be reproduced by
    // re-running it later — the deployment underneath has already moved on.
    // A video of the failing run is the only artifact that survives that.
    video: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' }, testIgnore: COMPONENT_SPECS },
    {
      name: 'components',
      use: { browserName: 'chromium', baseURL: `http://localhost:${COMPONENT_PORT}` },
      testMatch: COMPONENT_SPECS,
      // The dev server compiles a component and everything it imports on the
      // first request for it, which can take tens of seconds on a cold start.
      timeout: 60_000,
    },
    // The desktop app renders with WebKit, and how the chat pane keeps its place
    // at the bottom differs there: WebKit clamps scrollTop where Chromium does not.
    {
      name: 'components-webkit',
      use: { browserName: 'webkit', baseURL: `http://localhost:${COMPONENT_PORT}` },
      testMatch: ['chat-back-to-latest.spec.ts'],
      timeout: 60_000,
    },
  ],
  webServer: [
    ...stackServers({ ci: Boolean(process.env.CI), backendHost: '0.0.0.0' }),
    ...(process.env.CI
      ? [
          {
            // The component specs' dev server. Their fixtures replace fetch and
            // WebSocket, so it needs no proxy target.
            command: `cd ../frontend && pnpm exec vite --port ${COMPONENT_PORT} --strictPort`,
            url: `http://localhost:${COMPONENT_PORT}`,
            reuseExistingServer: false,
            timeout: 180_000,
          },
        ]
      : []),
  ],
});
