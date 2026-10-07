import { defineConfig } from '@playwright/test';

// Ports are overridable because CI once ran on the dev box itself, which serves
// the dev deployment on 8081 — a hardcoded port made Playwright fail with
// "8081 is already used" before a single test ran. CI has since moved to the
// cheese-ci pool (e2e.yml still passes E2E_*_PORT overrides; nothing else
// listens there, but a local dev stack does). Defaults keep local runs exactly
// as they were. NOTE: smoke.spec.ts hardcodes its own BACKEND_PORT fallback and
// must be kept in step with the resolution below by hand.
const BACKEND_PORT = process.env.E2E_BACKEND_PORT ?? '8081';
const FRONTEND_PORT = process.env.E2E_FRONTEND_PORT ?? '3000';
const STUB_GATEWAY_PORT = process.env.E2E_STUB_GATEWAY_PORT ?? '4010';
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
const STUB_GATEWAY_URL = `http://127.0.0.1:${STUB_GATEWAY_PORT}`;
// The collaboration service every living document opens through. It and the
// backend share COLLAB_SECRET; the browser reaches it through vite's /collab.
const COLLAB_PORT = process.env.E2E_COLLAB_PORT ?? '8902';
const COLLAB_URL = `http://127.0.0.1:${COLLAB_PORT}`;
const COLLAB_SECRET = 'e2e-collab-secret';

export default defineConfig({
  testDir: './tests',
  globalSetup: './global-setup.ts',
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
    { name: 'chromium', use: { browserName: 'chromium' } },
  ],
  webServer: [
    {
      command: `node ./stub-gateway.mjs`,
      env: { STUB_GATEWAY_PORT },
      url: `${STUB_GATEWAY_URL}/healthz`,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      command: `cd ../backend && uv run uvicorn app.main:app --host 0.0.0.0 --port ${BACKEND_PORT}`,
      // A model catalogue without a live inference provider.
      //
      // PLATFORM_ADMIN_HANDLES / FEEDBACK_TRIAGE_HANDLES: the admin surface
      // (feedback-flows.spec.ts 的「管理员」那条) is gated on two handle
      // allowlists — the admin shell on the platform one, the feedback queue on
      // the feedback one — that no deployment config sets, so without them every
      // admin step is a 403 and the spec fails on the gate instead of on what it
      // means to check. Values are JSON lists — pydantic-settings parses them,
      // commas make the app refuse to boot.
      env: {
        AGENT_HARNESS_MODELS: '{"codex": ["codex-ci-fixture"]}',
        PLATFORM_ADMIN_HANDLES: '["alice"]',
        FEEDBACK_TRIAGE_HANDLES: '["alice"]',
        // Which models the platform pool offers is the gateway's answer, so a
        // run without one can only pick AGENT_MODEL — and a deployment with no
        // model at all cannot start a turn. stub-gateway.mjs answers that one
        // question and nothing else; it is not an inference provider.
        LLM_GATEWAY_ADMIN_BASE: STUB_GATEWAY_URL,
        LLM_GATEWAY_ADMIN_KEY: 'stub-gateway-key',
        COLLAB_SECRET,
        COLLAB_INTERNAL_URL: COLLAB_URL,
      },
      url: `${BACKEND_URL}/healthz`,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
    },
    {
      // `pnpm run dev -- --port N` forwards the separator itself, so vite is
      // invoked as `vite -- --port N` and takes `--` as its POSITIONAL root
      // directory. It then serves a directory that does not exist: no error, no
      // banner, never reachable — which is exactly how it failed in CI.
      command: `cd ../frontend && pnpm run build:collab && node dist-collab/main.mjs`,
      env: { PORT: COLLAB_PORT, COLLAB_SECRET, COLLAB_BACKEND_URL: BACKEND_URL },
      url: `${COLLAB_URL}/healthz`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      // CI serves the production build e2e.yml made (`vite preview`, which
      // applies the same `server.proxy` table); a local run keeps the dev
      // server and its hot reload. Preview fails loudly when frontend/dist is
      // missing, so CI cannot fall back to the dev server unnoticed.
      command: process.env.CI
        ? `cd ../frontend && pnpm exec vite preview --port ${FRONTEND_PORT} --strictPort`
        : `cd ../frontend && pnpm exec vite --port ${FRONTEND_PORT} --strictPort`,
      url: `http://localhost:${FRONTEND_PORT}`,
      // VITE_API_BASE_URL=/api makes the 知是 1.0 layer prefix its calls with
      // /api (so /users/auth/login → /api/users/auth/login), which the proxy's
      // strip-one-/api rewrite then forwards to the backend as /users/auth/login.
      // Production bakes the same value at build time; without it the 1.0 routes
      // are relative (/users/...), miss the /api proxy entirely, and hit the SPA.
      env: { BACKEND_URL, COLLAB_URL: COLLAB_URL.replace(/^http/, 'ws'), VITE_API_BASE_URL: '/api' },
      reuseExistingServer: !process.env.CI,
      // Same 180s the backend gets: a cold dev server pre-bundles deps before
      // it answers. Preview answers in seconds.
      timeout: 180_000,
    },
  ],
});
