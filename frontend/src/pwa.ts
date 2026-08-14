/**
 * pwa.ts
 *
 * Service-worker registration for offline support. Paired with the VitePWA
 * config in vite.config.ts (`registerType: 'autoUpdate'`, `injectRegister:
 * false` — we register here so nothing is injected into index.html twice).
 *
 * autoUpdate semantics: when a new deploy's SW installs, it takes control
 * (skipWaiting + clientsClaim, set by the plugin) and `registerSW` reloads the
 * page — so a running tab picks up new code without a manual refresh, which is
 * the "有网就自动转出来" half of the offline story. The default `onNeedReload`
 * is `window.location.reload()`; we keep it.
 *
 * The whole thing is a no-op unless the browser supports service workers and
 * the build produced one (the virtual module resolves to a stub otherwise), so
 * this is safe to call unconditionally from main.ts.
 */
import { registerSW } from 'virtual:pwa-register'

export function registerPwa(): void {
  registerSW({ immediate: true })
}
