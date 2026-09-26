// The docs site shares the platform's sign-in: same origin, same access token in
// localStorage, same rotating refresh cookie. It must therefore refresh exactly
// the way frontend/src/lib/session.ts does — same lock, same channel — or a
// refresh here and one in an app tab would present a rotated cookie and sign
// the user out.
const REFRESH_URL = '/api/users/auth/refresh-token'
const LOCK_NAME = 'cheese:session-refresh'
const CHANNEL_NAME = 'cheese:session'
const BUDGET_MS = 10_000

export function accessToken() {
  try { return localStorage.getItem('accessToken') || '' } catch { return '' }
}

// Seconds until the token expires; -1 when it cannot be read.
export function tokenTtl(token) {
  try {
    const { exp } = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    return exp - Date.now() / 1000
  } catch { return -1 }
}

let channel
function tabs() {
  if (channel !== undefined) return channel
  channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel(CHANNEL_NAME)
  return channel
}

async function refreshOnce(held) {
  const current = accessToken()
  if (current && current !== held) return { kind: 'ok', token: current }
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), BUDGET_MS)
  try {
    const res = await fetch(REFRESH_URL, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', signal: controller.signal })
    if (res.status === 401) { tabs()?.postMessage({ type: 'signed-out' }); return { kind: 'rejected' } }
    if (!res.ok) return { kind: 'unreachable' }
    const body = await res.json()
    const token = body?.data?.accessToken
    if (typeof token !== 'string' || !token) return { kind: 'unreachable' }
    const user = body?.data?.user
    try {
      localStorage.setItem('accessToken', token)
      if (user) localStorage.setItem('user', JSON.stringify(user))
    } catch { /* storage refused; the token is still returned */ }
    tabs()?.postMessage({ type: 'token', token, user })
    return { kind: 'ok', token }
  } catch {
    return { kind: 'unreachable' }
  } finally {
    clearTimeout(timer)
  }
}

let inFlight = null
export function refreshSession() {
  if (!inFlight) {
    const held = accessToken()
    const locks = navigator.locks
    const run = () => refreshOnce(held)
    inFlight = (locks ? locks.request(LOCK_NAME, run) : run()).finally(() => { inFlight = null })
  }
  return inFlight
}

// A token good for at least another minute, refreshing first if needed; '' when signed out.
export async function freshToken() {
  const token = accessToken()
  if (token && tokenTtl(token) > 60) return token
  if (!token) {
    // No token in storage can still mean a live refresh cookie (signed in, then cleared storage).
    const r = await refreshSession()
    return r.kind === 'ok' ? r.token : ''
  }
  const r = await refreshSession()
  return r.kind === 'ok' ? r.token : r.kind === 'unreachable' ? token : ''
}

export const signInUrl = () => `/account/signin?redirect=${encodeURIComponent(location.pathname + location.search + location.hash)}`
