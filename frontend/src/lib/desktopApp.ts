// What the desktop app (desktop/) says about the window this page runs in, and
// what the page tells it back. The app sets `__CHEESE_APP__` before the page
// runs; outside the app, or in an app too old to set it, this page behaves as
// in a browser and nothing is sent.

import type { ThemePreference } from '@/theme'

/** What an app can do beyond the window itself; an older app lists fewer. */
export type DesktopAbility = 'notices' | 'badge' | 'autostart'

interface CheeseApp {
  /** 'overlay': the title bar is drawn over the page (macOS), so the page leaves room for its buttons. */
  titleBar?: 'overlay' | 'native'
  can?: DesktopAbility[]
}

interface TauriCore {
  invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>
}

interface TauriEvent {
  listen: <T>(event: string, handler: (event: { payload: T }) => void) => Promise<() => void>
}

function tauri(): { core?: TauriCore; event?: TauriEvent } | null {
  if (typeof window === 'undefined') return null
  return (window as unknown as { __TAURI__?: { core?: TauriCore; event?: TauriEvent } }).__TAURI__ ?? null
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
  tauri()
    ?.core?.invoke('set_theme', { preference })
    .catch(() => {})
}

/** This app can do `ability`; one that predates it is never asked to. */
export function desktopCan(ability: DesktopAbility): boolean {
  return app()?.can?.includes(ability) ?? false
}

/**
 * Hands the app a credential for this account's notices. From then on the app
 * keeps its own connection to the server and shows them as system
 * notifications, window or no window (desktop/src-tauri/src/notices.rs).
 * `issue` asks the server for the credential, which opens nothing else.
 */
export async function desktopListenForNotices(account: number, issue: () => Promise<string>): Promise<void> {
  if (!desktopCan('notices')) return
  const token = await issue()
  await tauri()?.core?.invoke('listen_for_notices', { token, account })
}

/** Signed out: the app stops listening and clears the count on its icon. */
export function desktopStopNotices(): void {
  if (!desktopCan('notices')) return
  tauri()
    ?.core?.invoke('stop_notices')
    .catch(() => {})
}

/** How many things wait on the person, shown on the Dock or taskbar icon. */
export function desktopBadge(count: number): void {
  if (!desktopCan('badge')) return
  tauri()
    ?.core?.invoke('set_badge', { count })
    .catch(() => {})
}

/** Calls `open` with a path in this web app when the app is asked to show one: a clicked notification, the tray menu. */
export function onDesktopOpenPage(open: (path: string) => void): () => void {
  const events = app() ? tauri()?.event : undefined
  if (!events) return () => {}
  let stop: (() => void) | null = null
  let stopped = false
  events
    .listen<string>('open-page', ({ payload }) => open(payload))
    .then((unlisten) => (stopped ? unlisten() : (stop = unlisten)))
    .catch(() => {})
  return () => {
    stopped = true
    stop?.()
  }
}

/** Whether the app opens by itself, out of sight, when the person logs in to this computer. */
export async function desktopOpensAtLogin(): Promise<boolean> {
  if (!desktopCan('autostart')) return false
  return ((await tauri()?.core?.invoke('opens_at_login')) as boolean | undefined) ?? false
}

/** Rejects when the system refused; the setting is then as it was. */
export async function setDesktopOpensAtLogin(on: boolean): Promise<void> {
  if (!desktopCan('autostart')) return
  await tauri()?.core?.invoke('set_opens_at_login', { on })
}
