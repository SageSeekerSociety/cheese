---
paths:
  - "e2e/**"
---

# e2e specs — determinism pitfalls (each one has bitten us)

- **Login delay**: after 5 wrong passwords for a username, every further
  attempt has to wait (30 s, doubling to 5 minutes), and the count lives in
  Redis/Valkey for an hour, which outlives runs on any persistent box.
  Negative-auth tests use a throwaway username — `retries: 2` spends 3 attempts
  per run. Never fail a sign-in as `alice`; every other spec signs in as her.
- **Transient toasts**: vuetify-sonner toasts auto-dismiss. Asserting one is a
  timing bet; prefer asserting the resulting state (URL, storage, rendered
  element) or accept the flake risk knowingly.
- **Selectors**: `getByLabel` / `getByRole` match substrings by default. The
  show-password toggle is labelled `显示密码` and the passkey button reads
  `使用通行密钥登录`, so use `getByLabel('密码', { exact: true })` and
  `getByRole('button', { name: '登录', exact: true })`. Project rail tiles are
  `.app-rail-item--tile`; the bare `:not(--add)` also matches the 首页 icon
  which sits first.
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
