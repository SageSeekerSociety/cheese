---
paths:
  - "e2e/**"
---

# e2e specs — determinism pitfalls (each one has bitten us)

- **Login rate limiter**: the backend locks a username out after 5 failed
  attempts, and that state lives in Redis/Valkey which OUTLIVES runs on any
  persistent box. Negative-auth tests must use a throwaway username — and
  remember `retries: 2` burns 3 attempts per run. Never fail-login as `alice`;
  every other spec depends on her.
- **Transient toasts**: vuetify-sonner toasts auto-dismiss. Asserting one is a
  timing bet; prefer asserting the resulting state (URL, storage, rendered
  element) or accept the flake risk knowingly.
- **Selectors**: Vuetify's password-visibility button gets aria-label
  `"密码 appended action"` — a substring match; use `getByLabel('密码',
  { exact: true })`. Project rail tiles are `.app-rail-item--tile`; the bare
  `:not(--add)` also matches the 首页 icon which sits first.
- **Seed chain**: alice comes from the seed migration; her PROJECT comes from
  `backend/scripts/seed_fusion_demo.py` (CI runs it in e2e.yml). A spec
  assuming a project tile without that seed passes locally on a dev DB and
  fails on fresh CI.
- **Environment truths live in `playwright.config.ts` comments** (cold-compile
  timeouts, serial workers on CI, why ports are overridable, the
  vite-proxy-mirrors-nginx contract in `frontend/vite.config.ts`). Read them
  before changing config; `smoke.spec.ts`'s `BACKEND_PORT` must track the
  config's resolution by hand.
- **A new spec only counts once you've seen it run in CI.** The scope gate
  skips heavy jobs on squash-merged commits, and this suite once sat broken
  for days while "green" runs were skips. Check the job actually executed.
