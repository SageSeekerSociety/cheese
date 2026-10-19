// The servers a whole cheese stack is made of when there is no Docker: a stub
// model gateway, the backend, the collaboration service and the frontend. Two
// things start them from this one list: Playwright, before the suite
// (playwright.config.ts), and `task dev:sandbox` (dev-stack.mjs), which keeps
// them running for a person or an agent to look at. Postgres and Redis are not
// in it: CI gets them as service containers, dev-stack.mjs from
// .claude/scripts/dev-db.sh, and the backend finds both through DATABASE_URL
// and REDIS_URL in its environment.
//
// Each entry is a Playwright `webServer` item; `command` runs in a shell from
// this directory (e2e/), which is why every one starts with `cd`.

// Ports are overridable because CI once ran on the dev box itself, which serves
// the dev deployment on 8081 — a hardcoded port made Playwright fail with
// "8081 is already used" before a single test ran. CI has since moved to the
// cheese-ci pool (e2e.yml still passes E2E_*_PORT overrides; nothing else
// listens there, but a local dev stack does). Defaults keep local runs exactly
// as they were.
export const BACKEND_PORT = process.env.E2E_BACKEND_PORT ?? '8081';
export const FRONTEND_PORT = process.env.E2E_FRONTEND_PORT ?? '3000';
export const STUB_GATEWAY_PORT = process.env.E2E_STUB_GATEWAY_PORT ?? '4010';
// The collaboration service every living document opens through. It and the
// backend share COLLAB_SECRET; the browser reaches it through vite's /collab.
export const COLLAB_PORT = process.env.E2E_COLLAB_PORT ?? '8902';

const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
const STUB_GATEWAY_URL = `http://127.0.0.1:${STUB_GATEWAY_PORT}`;
const COLLAB_URL = `http://127.0.0.1:${COLLAB_PORT}`;
const COLLAB_SECRET = 'e2e-collab-secret';

/**
 * @param {object} options
 * @param {boolean} options.ci  serve the production build e2e.yml made instead
 *   of the dev server, and refuse a server that is already running.
 * @param {string} options.backendHost  the address uvicorn binds.
 * @param {string} [options.frontendHost]  the address vite binds; vite's own
 *   default (`localhost`) when not given.
 */
export function stackServers({ ci, backendHost, frontendHost }) {
  const viteHost = frontendHost ? ` --host ${frontendHost}` : '';
  return [
    {
      command: `node ./stub-gateway.mjs`,
      env: { STUB_GATEWAY_PORT },
      url: `${STUB_GATEWAY_URL}/healthz`,
      reuseExistingServer: !ci,
      timeout: 30_000,
    },
    {
      command: `cd ../backend && uv run uvicorn app.main:app --host ${backendHost} --port ${BACKEND_PORT}`,
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
      reuseExistingServer: !ci,
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
      reuseExistingServer: !ci,
      timeout: 60_000,
    },
    {
      // CI serves the production build e2e.yml made (`vite preview`, which
      // applies the same `server.proxy` table); a local run keeps the dev
      // server and its hot reload. Preview fails loudly when frontend/dist is
      // missing, so CI cannot fall back to the dev server unnoticed.
      command: ci
        ? `cd ../frontend && pnpm exec vite preview --port ${FRONTEND_PORT} --strictPort`
        : `cd ../frontend && pnpm exec vite --port ${FRONTEND_PORT} --strictPort${viteHost}`,
      url: `http://${frontendHost ?? 'localhost'}:${FRONTEND_PORT}`,
      // VITE_API_BASE_URL=/api makes the 知是 1.0 layer prefix its calls with
      // /api (so /users/auth/login → /api/users/auth/login), which the proxy's
      // strip-one-/api rewrite then forwards to the backend as /users/auth/login.
      // Production bakes the same value at build time; without it the 1.0 routes
      // are relative (/users/...), miss the /api proxy entirely, and hit the SPA.
      env: { BACKEND_URL, COLLAB_URL: COLLAB_URL.replace(/^http/, 'ws'), VITE_API_BASE_URL: '/api' },
      reuseExistingServer: !ci,
      // Same 180s the backend gets: a cold dev server pre-bundles deps before
      // it answers. Preview answers in seconds.
      timeout: 180_000,
    },
  ];
}
