// What the desktop app (desktop/) says about the window this page runs in, and
// what the page tells it back. The app sets `__CHEESE_APP__` before the page
// runs; outside the app, or in an app too old to set it, this page behaves as
// in a browser and nothing is sent.

import type { ThemePreference } from '@/theme'

import { storageGetJson, storageRemove, storageSetJson } from '@/lib/browserStorage'

/** What an app can do beyond the window itself; an older app lists fewer. */
export type DesktopAbility =
  | 'notices'
  | 'badge'
  | 'autostart'
  | 'links'
  | 'updates'
  | 'device'
  | 'teamEnrollment'
  | 'modelCredentialLogin'
  | 'executor'

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

/**
 * Stamps `title-bar-overlay` on `<html>` when the app draws its window buttons
 * over the page (macOS), so stylesheets can leave them room without every
 * component asking again. `--window-buttons-inset` (style.css) reads it; only
 * that one declaration changes, and it is 0 everywhere else.
 */
export function markTitleBarOverlay(): void {
  if (typeof document === 'undefined' || !titleBarOverlay()) return
  document.documentElement.classList.add('title-bar-overlay')
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
  | { state: 'idle' | 'checking' | 'latest' | 'failed' | 'install-failed' }
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

// Once this computer is connected, the app fetches the Claude Code build a
// teammate runs here in the background (desktop/src-tauri/src/executor.rs), so
// the first session here does not wait for it. Nothing waits on it.

/** Where fetching that build stands. */
export type DesktopExecutorStatus =
  | { state: 'idle' | 'ready' | 'failed' }
  | { state: 'fetching'; percent: number | null }

/** Null in a browser and in an app that does not fetch it ahead. */
export async function desktopExecutorStatus(): Promise<DesktopExecutorStatus | null> {
  if (!desktopCan('executor')) return null
  return ((await tauri()?.core?.invoke('executor_status')) as DesktopExecutorStatus | undefined) ?? null
}

export function onDesktopExecutorStatus(handle: (status: DesktopExecutorStatus) => void): () => void {
  return desktopCan('executor') ? onDesktopEvent('executor-status', handle) : () => {}
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

/** Where the window goes after a landing page hands itself to the browser: the
 *  page it came from when that is an ordinary page of the app, else the start
 *  page. Never a page that could hand over again (the landing pages, and the
 *  account pages, whose sign-in redirects to one) and never the history entry before the first page
 *  of the app: in the desktop app that is the app's own first page, which goes
 *  on to the page a `cheese://` link named at launch, a landing page again. */
function afterLeaving(): string {
  const back = (window.history.state as { back?: unknown } | null)?.back
  if (typeof back !== 'string' || !back.startsWith('/') || back.startsWith('//')) return '/'
  const first = back.slice(1).split(/[/?#]/)[0].toLowerCase()
  return ['previews', 'sites', 'account'].includes(first) ? '/' : back
}

let leftForBrowser = false

/** A page that hands the reader to the content domain at the top level (a
 *  preview, a published Site) is the browser's to show: in the app, the
 *  window would end up on a page someone else wrote, with no way back. Opens
 *  `path` in the browser, once per page load, and replaces this page with
 *  where the window came from (`afterLeaving`); false outside the app, where
 *  the caller goes on as in a browser. */
export function leaveForBrowser(path: string): boolean {
  if (!desktopCan('links')) return false
  if (leftForBrowser) return true
  leftForBrowser = true
  openInBrowser(path)
  window.location.replace(afterLeaving())
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
 * browser by hand. A friend's invite code rides along as `invite` (not `code`,
 * which on these pages means a sign-in code).
 */
export async function signInInBrowser(entry: BrowserSignInEntry, target: string, invite = ''): Promise<string> {
  // If this does not land, the code that comes back has nothing to be traded
  // with and the sign-in fails the way it does for a code this app did not ask
  // for — a browser window the person can close.
  const challenge = await keepSignInVerifier(target)
  const query = new URLSearchParams({ entry, challenge, ...(invite ? { invite } : {}) })
  const url = new URL(`/account/oauth/app?${query}`, window.location.origin).href
  window.open(url, '_blank')
  return url
}

/**
 * Signs in with a provider whose sign-in runs on another origin of this
 * deployment (the one its callback is registered on). The same hand-off as
 * the app's: this page keeps the secret, the sign-in happens there, and the
 * code comes back to this origin's AppSignInFinish.vue.
 */
export async function signInAtOrigin(origin: string, provider: string, target: string): Promise<void> {
  const challenge = await keepSignInVerifier(target)
  const query = new URLSearchParams({ entry: 'signin', challenge, provider, returnTo: window.location.origin })
  window.location.assign(`${origin}/account/oauth/app?${query}`)
}

/** Keeps a new secret and where the sign-in is headed; returns the secret's hash. */
async function keepSignInVerifier(target: string): Promise<string> {
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)))
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)))
  storageSetJson(SIGN_IN_VERIFIER_KEY, { verifier, target })
  return base64url(digest)
}

/** The secret of the sign-in started last, and where it was headed; each is used at most once. */
export function takeSignInVerifier(): { verifier: string; target: string } | null {
  // Removed whether or not it reads back as one: a secret is used at most once,
  // so nothing must be left for a second try to find.
  const kept = storageGetJson<{ verifier?: unknown; target?: unknown }>(SIGN_IN_VERIFIER_KEY)
  storageRemove(SIGN_IN_VERIFIER_KEY)
  return typeof kept?.verifier === 'string' ? { verifier: kept.verifier, target: String(kept.target ?? '/') } : null
}

/** The `cheese://` link that opens the app on `page`, a path in this web app. */
export function appLink(page: string): string {
  return `cheese://open?${new URLSearchParams({ path: page })}`
}
