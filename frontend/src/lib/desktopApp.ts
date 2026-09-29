// What the desktop app (desktop/) says about the window this page runs in, and
// what the page tells it back. The app sets `__CHEESE_APP__` before the page
// runs; outside the app, or in an app too old to set it, this page behaves as
// in a browser and nothing is sent.

import type { ThemePreference } from '@/theme'

interface CheeseApp {
  /** 'overlay': the title bar is drawn over the page (macOS), so the page leaves room for its buttons. */
  titleBar?: 'overlay' | 'native'
}

interface TauriCore {
  invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>
}

function app(): CheeseApp | null {
  if (typeof window === 'undefined') return null
  return (window as unknown as { __CHEESE_APP__?: CheeseApp }).__CHEESE_APP__ ?? null
}

/** Running inside the desktop app, any version. */
export function inDesktopApp(): boolean {
  if (typeof window === 'undefined') return false
  return !!(window as unknown as { __TAURI__?: { core?: unknown } }).__TAURI__?.core
}

/** The window's title bar lies over the top of the page. */
export function titleBarOverlay(): boolean {
  return app()?.titleBar === 'overlay'
}

/** The app paints its own first page and the title bar in this theme from now on. */
export function tellDesktopTheme(preference: ThemePreference): void {
  if (!app()) return
  const core = (window as unknown as { __TAURI__?: { core?: TauriCore } }).__TAURI__?.core
  core?.invoke('set_theme', { preference }).catch(() => {})
}
