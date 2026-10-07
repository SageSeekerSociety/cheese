// What the desktop app (desktop/) says about the window this page runs in, and
// what the page tells it back. The app sets `__CHEESE_APP__` before the page
// runs; outside the app, or in an app too old to set it, this page behaves as
// in a browser and nothing is sent.

import type { ThemePreference } from '@/theme'

/** What an app can do beyond the window itself; an older app lists fewer. */
export type DesktopAbility = 'notices' | 'badge' | 'autostart' | 'links' | 'updates' | 'device'

interface CheeseApp {
  /** 'overlay': the title bar is drawn over the page (macOS), so the page leaves room for its buttons. */
  titleBar?: 'overlay' | 'native'
  /** The app's own version, e.g. "0.1.42". */
  version?: string
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

/** Calls `handle` with each `event` the app sends this page; the returned function stops. */
function onDesktopEvent<T>(event: string, handle: (payload: T) => void): () => void {
  const events = app() ? tauri()?.event : undefined
  if (!events) return () => {}
  let stop: (() => void) | null = null
  let stopped = false
  events
    .listen<T>(event, ({ payload }) => handle(payload))
    .then((unlisten) => (stopped ? unlisten() : (stop = unlisten)))
    .catch(() => {})
  return () => {
    stopped = true
    stop?.()
  }
}

/** Calls `open` with a path in this web app when the app is asked to show one: a clicked notification, the tray menu. */
export function onDesktopOpenPage(open: (path: string) => void): () => void {
  return onDesktopEvent('open-page', open)
}

// The app keeps itself current (desktop/src-tauri/src/updates.rs): it looks for
// a new version at launch, every few hours and when asked, downloads it, and
// installs it when the person clicks 重启以完成更新 or once the window is out
// of sight. An app from before it said so still updates, only out of sight.

/** The app's own version; null in an app from before it said. */
export function desktopAppVersion(): string | null {
  return app()?.version ?? null
}

/** Where the app's own update stands, as desktop/src-tauri/src/updates.rs reports it. */
export type DesktopUpdateStatus =
  | { state: 'idle' | 'checking' | 'latest' | 'failed' }
  | { state: 'downloading' | 'ready'; version: string }

/** Null in a browser and in an app that cannot say. */
export async function desktopUpdateStatus(): Promise<DesktopUpdateStatus | null> {
  if (!desktopCan('updates')) return null
  return ((await tauri()?.core?.invoke('update_status')) as DesktopUpdateStatus | undefined) ?? null
}

/** Looks for a new version now; resolves with where that leaves the update. */
export async function checkDesktopUpdates(): Promise<DesktopUpdateStatus | null> {
  if (!desktopCan('updates')) return null
  return ((await tauri()?.core?.invoke('check_for_updates')) as DesktopUpdateStatus | undefined) ?? null
}

/** Installs the downloaded update and restarts the app. Rejects with "connecting"
 *  while this computer is being connected, which a restart would cut off. */
export async function restartDesktopToUpdate(): Promise<void> {
  if (!desktopCan('updates')) return
  await tauri()?.core?.invoke('restart_to_update')
}

export function onDesktopUpdateStatus(handle: (status: DesktopUpdateStatus) => void): () => void {
  return desktopCan('updates') ? onDesktopEvent('update-status', handle) : () => {}
}

/** The app menu's "About Cheese" or "Check for Updates…" (macOS). */
export function onDesktopShowAbout(handle: () => void): () => void {
  return desktopCan('updates') ? onDesktopEvent('show-about', handle) : () => {}
}

/** Opens `url` (a path on this site or a full address) in the person's browser
 *  rather than in the app's window. False where the app cannot: the caller then
 *  leaves the link as it is. */
export function openInBrowser(url: string): boolean {
  if (!desktopCan('links')) return false
  // The app opens every new window in the browser.
  window.open(new URL(url, window.location.origin).href, '_blank')
  return true
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

/** Sent with every request from the app: an authorization started there comes back to it (backend/app/api/app_return.py). */
export function desktopAppHeaders(): Record<string, string> {
  return desktopCan('links') ? { 'X-Cheese-App': '1' } : {}
}

/** Goes to `url` to authorize; in the app, in the browser. True when the page stays where it is. */
export function goAuthorize(url: string): boolean {
  if (desktopCan('links')) {
    // The app opens every new window in the browser.
    window.open(url, '_blank')
    return true
  }
  window.location.assign(url)
  return false
}

const SIGN_IN_VERIFIER_KEY = 'cheese.appSignIn'

function base64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
}

/** Which page the browser opens on: signing in, signing up, or resetting a password. */
export type BrowserSignInEntry = 'signin' | 'signup' | 'recover'

/**
 * Signs in to the app in the person's browser, every way in included: a
 * password, an email code, a passkey, a provider (RFC 8252). The app keeps a
 * secret and gives the browser only its hash; the sign-in comes back as a code
 * that is good only with that secret (views/account/AppSignInFinish.vue).
 * Returns the address opened, which holds no secret and can be pasted into a
 * browser by hand.
 */
export async function signInInBrowser(entry: BrowserSignInEntry, target: string): Promise<string> {
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)))
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)))
  localStorage.setItem(SIGN_IN_VERIFIER_KEY, JSON.stringify({ verifier, target }))
  const query = new URLSearchParams({ entry, challenge: base64url(digest) })
  const url = new URL(`/account/oauth/app?${query}`, window.location.origin).href
  window.open(url, '_blank')
  return url
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
