// What the desktop app (desktop/) says about the window this page runs in, and
// what the page tells it back. The app sets `__CHEESE_APP__` before the page
// runs; outside the app, or in an app too old to set it, this page behaves as
// in a browser and nothing is sent.

import type { ThemePreference } from '@/theme'

/** What an app can do beyond the window itself; an older app lists fewer. */
export type DesktopAbility = 'notices' | 'badge' | 'autostart' | 'links'

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

// Authorizing with another site — signing in with GitHub or Google, connecting
// GitHub, Feishu or an MCP server — happens in the person's browser, where
// their accounts are signed in and where Google agrees to show its page. The
// browser then hands the result back through a `cheese://` link
// (desktop/src-tauri/src/links.rs). An app too old to take those links keeps
// the old way.

/** Sent with a request that starts an authorization, so its result comes back to the app (backend/app/api/app_return.py). */
export function authorizeInAppHeaders(): Record<string, string> {
  return desktopCan('links') ? { 'X-Cheese-App': '1' } : {}
}

/** Goes to `url` to authorize; in the app, in the browser. True when the page stays where it is. */
export function goAuthorize(url: string): boolean {
  if (desktopCan('links')) {
    // The app opens every new window in the browser.
    window.open(url, '_blank')
    return true
  }
  window.location.href = url
  return false
}

const SIGN_IN_VERIFIER_KEY = 'cheese.appSignIn'

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
}

/**
 * Signs in with `provider` in the browser. The app keeps a secret and gives
 * the browser only its hash; the sign-in comes back as a code that is good
 * only with that secret (views/account/AppSignInFinish.vue).
 */
export async function signInInBrowser(provider: string, target: string): Promise<void> {
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)))
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)))
  localStorage.setItem(SIGN_IN_VERIFIER_KEY, JSON.stringify({ verifier, target }))
  const query = new URLSearchParams({ provider, challenge: base64url(digest) })
  window.open(`/account/oauth/app?${query}`, '_blank')
}

/** The secret of the sign-in started last, and where it was headed; each is used at most once. */
export function takeSignInVerifier(): { verifier: string; target: string } | null {
  const raw = localStorage.getItem(SIGN_IN_VERIFIER_KEY)
  localStorage.removeItem(SIGN_IN_VERIFIER_KEY)
  try {
    const kept = raw ? JSON.parse(raw) : null
    return typeof kept?.verifier === 'string' ? { verifier: kept.verifier, target: String(kept.target ?? '/') } : null
  } catch {
    return null
  }
}

/** The `cheese://` link that opens the app on `page`, a path in this web app. */
export function appLink(page: string): string {
  return `cheese://open?${new URLSearchParams({ path: page })}`
}
