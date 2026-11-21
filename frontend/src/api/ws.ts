import { authToken, BASE } from './http'

// Absolute WS URL for a device screen's 现场 (read-only terminal). The session token
// rides as ?token= (browsers can't set an Authorization header on a WebSocket); the
// backend authorizes the viewer against the screen's project/topic membership.
// VITE_CONNECTOR_WS_BASE (a plain http(s) origin, runtime-injected in prod) reroutes
// just this socket when the site origin sits behind a WS-stripping edge (校园前置
// 反代); token-in-query means cross-origin needs no cookie/CORS handling.
export function screenWsUrl(sid: string): string {
  const token = authToken()
  const q = token ? `?token=${encodeURIComponent(token)}` : ''
  return `${connectorWsBase()}/connector/session/${encodeURIComponent(sid)}/screen${q}`
}

function connectorWsBase(): string {
  const override = (import.meta.env.VITE_CONNECTOR_WS_BASE as string | undefined) ?? ''
  if (override && !override.startsWith('__')) return override.replace(/^http/, 'ws').replace(/\/$/, '')
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}`
}

// A person's terminal in a task: one of their shells there, by the id the page chose. Served by
// the same process as the 现场 socket, reached the same way.
export function taskTerminalWsUrl(taskId: string, terminalId: string, cols: number, rows: number): string {
  const query = new URLSearchParams({ cols: String(cols), rows: String(rows) })
  const token = authToken()
  if (token) query.set('token', token)
  return `${connectorWsBase()}/connector/tasks/${encodeURIComponent(taskId)}/terminals/${encodeURIComponent(terminalId)}?${query}`
}

// Dev-only observability hook, same purpose as `window.__queryClient`: the probe
// scripts under scripts/ open real sockets and issue real fetches from inside
// the page, and the prefix they need is the one BASE exists to spell ONCE. Four
// of them had it hand-written instead, and every copy was a copy that could be
// wrong — which is what a doubled prefix nobody remembers reliably produces.
declare global {
  interface Window {
    __cxApi?: { base: string }
  }
}
if (import.meta.env.DEV) window.__cxApi = { base: BASE }
