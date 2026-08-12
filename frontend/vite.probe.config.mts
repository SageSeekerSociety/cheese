// Probe-only vite config: THE SAME APP, on its own port, proxied to an
// EPHEMERAL backend so UI changes can be verified end-to-end without touching
// the running dogfood stack (8799/3000). Used by scripts/probe_*.py.
//
// It is a delta over vite.config.ts, not a second config, because it was a
// second config once and silently rotted: it had no `@` alias (every fused-in
// 1.0 import failed to resolve, so the app would not even boot) and no `/api`
// rewrite (api.ts addresses the gateway as `/api/api`, which arrived at the
// backend doubled and 404'd). Both were invisible until someone ran a probe.
// Deriving means the only things that can differ are the two named here.
import { defineConfig, mergeConfig } from 'vite'

export default defineConfig(async () => {
  // vite.config.ts reads BACKEND_URL at module scope, so it must be set before
  // that module is evaluated — hence the dynamic import inside an async config.
  process.env.BACKEND_URL = process.env.PROBE_API_TARGET || 'http://127.0.0.1:8098'
  const base = (await import('./vite.config')).default
  return mergeConfig(await base, {
    server: { port: 5175, strictPort: true },
  })
})
