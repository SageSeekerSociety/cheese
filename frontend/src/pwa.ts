/**
 * pwa.ts
 *
 * Service-worker registration for offline support. Paired with the VitePWA
 * config in vite.config.ts (`registerType: 'autoUpdate'`, `injectRegister:
 * false` — we register here so nothing is injected into index.html twice).
 *
 * A new worker must finish precaching before it activates. The plugin reloads
 * on update activation; until then a controlled tab can still run its old
 * entry. Online navigations fetch current HTML separately in vite.config.ts;
 * the precached shell remains available when that network request fails.
 *
 * The whole thing is a no-op unless the browser supports service workers and
 * the build produced one (the virtual module resolves to a stub otherwise), so
 * this is safe to call unconditionally from main.ts.
 */
import { registerSW } from 'virtual:pwa-register'

export function registerPwa(): void {
  registerSW({ immediate: true })
}
