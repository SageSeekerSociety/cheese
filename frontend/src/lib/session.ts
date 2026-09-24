// The one place an access token is renewed.
//
// The server rotates the refresh cookie on every refresh and treats the
// replaced cookie, presented again later, as stolen: it ends the sign-in. So
// two refreshes that do not know about each other — in one tab or across tabs,
// which share the cookie — sign the user out. Everything that needs a fresh
// token comes here, one refresh runs at a time per browser (`navigator.locks`),
// and the tab that ran it tells the others (`BroadcastChannel`).
//
// Only the refresh answering 401 means the sign-in is over. A network failure,
// a timeout or any other status leaves the session alone.
//
// Deliberately free of imports from `network/` and `services/`: those import
// each other already, and everything that refreshes sits among them.
import type { User } from '@/types/users'

export type RefreshOutcome = { kind: 'ok'; token: string; user?: User } | { kind: 'rejected' } | { kind: 'unreachable' }

export type SessionEvent = { type: 'token'; token: string; user?: User } | { type: 'signed-out' }

export const REFRESH_URL = '/api/users/auth/refresh-token'
// Longer than any refresh should take; short enough that a hung one does not
// hold every request in every tab behind it.
export const TOKEN_REFRESH_BUDGET_MS = 10_000

const LOCK_NAME = 'cheese:session-refresh'
const CHANNEL_NAME = 'cheese:session'

const listeners = new Set<(event: SessionEvent) => void>()
let channel: BroadcastChannel | null | undefined
let inFlight: Promise<RefreshOutcome> | null = null

function storedToken(): string | null {
  try {
    return localStorage.getItem('accessToken')
  } catch {
    return null
  }
}

function storedUser(): User | undefined {
  try {
    const raw = localStorage.getItem('user')
    return raw ? (JSON.parse(raw) as User) : undefined
  } catch {
    return undefined
  }
}

function tabs(): BroadcastChannel | null {
  if (channel !== undefined) return channel
  channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(CHANNEL_NAME)
  channel?.addEventListener('message', (message: MessageEvent<SessionEvent>) => deliver(message.data))
  return channel
}

function deliver(event: SessionEvent) {
  for (const listener of listeners) listener(event)
}

// This tab hears it at once; the others through the channel.
function announce(event: SessionEvent) {
  deliver(event)
  tabs()?.postMessage(event)
}

/** Hear new tokens and sign-outs, from this tab and every other. */
export function onSessionEvent(listener: (event: SessionEvent) => void): () => void {
  tabs()
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** Tell the other tabs about a sign-in this tab made. */
export function announceSignIn(token: string, user?: User) {
  tabs()?.postMessage({ type: 'token', token, user } satisfies SessionEvent)
}

/** Tell the other tabs this browser has signed out. */
export function announceSignOut() {
  tabs()?.postMessage({ type: 'signed-out' } satisfies SessionEvent)
}

async function oneAtATime<T>(work: () => Promise<T>): Promise<T> {
  const locks = typeof navigator === 'undefined' ? undefined : navigator.locks
  if (!locks) return work()
  return locks.request(LOCK_NAME, work)
}

async function withinBudget(url: string): Promise<Response> {
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  const expired = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      controller.abort()
      reject(new DOMException('refresh timed out', 'TimeoutError'))
    }, TOKEN_REFRESH_BUDGET_MS)
  })
  try {
    return await Promise.race([
      fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        signal: controller.signal,
      }),
      expired,
    ])
  } finally {
    clearTimeout(timer)
  }
}

async function refreshOnce(held: string | null): Promise<RefreshOutcome> {
  // Another tab refreshed while this one waited for the lock: its token is
  // the successor this tab was about to ask for, and asking again would
  // present a cookie the server has just rotated.
  const current = storedToken()
  if (current && current !== held) {
    const user = storedUser()
    // The other tab announced it already; say it here too, in case this tab
    // has no channel to have heard it on.
    deliver({ type: 'token', token: current, user })
    return { kind: 'ok', token: current, user }
  }

  let res: Response
  let body: { data?: { accessToken?: unknown; user?: User } } | undefined
  try {
    res = await withinBudget(REFRESH_URL)
    if (res.status === 401) {
      announce({ type: 'signed-out' })
      return { kind: 'rejected' }
    }
    if (!res.ok) return { kind: 'unreachable' }
    body = await res.json()
  } catch {
    return { kind: 'unreachable' }
  }
  const token = body?.data?.accessToken
  if (typeof token !== 'string' || !token) return { kind: 'unreachable' }
  const user = body?.data?.user ?? undefined
  try {
    localStorage.setItem('accessToken', token)
    if (user) localStorage.setItem('user', JSON.stringify(user))
  } catch {
    // Storage refused (private mode, quota). The listeners still hold it.
  }
  announce({ type: 'token', token, user })
  return { kind: 'ok', token, user }
}

/**
 * Trade the refresh cookie for a new access token.
 *
 * Callers in this tab share one refresh; tabs take turns. On success the new
 * token is already stored and announced when this resolves.
 */
export function refreshSession(): Promise<RefreshOutcome> {
  if (!inFlight) {
    const held = storedToken()
    inFlight = oneAtATime(() => refreshOnce(held)).finally(() => {
      inFlight = null
    })
  }
  return inFlight
}
