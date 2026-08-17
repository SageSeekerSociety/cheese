/**
 * Global frontend error capture → 现场 (backend: app/domain/frontend_log.py).
 *
 * The dogfooding debugger is usually an agent, and an agent can never read a
 * user's browser console — so runtime errors are reported to the backend and
 * become event blocks in the open topic's 现场 timeline. console.log stays a
 * local dev convenience; this reporter is the guarantee.
 *
 * Self-contained on purpose: plain fetch (not api.ts's request helper — an
 * error inside the API layer must not recurse into reporting), its own queue
 * and caps, and every path swallows its own failures. The backend dedups and
 * rate-limits again server-side; the client-side cooldown just keeps the
 * network quiet.
 */
import type { App } from 'vue'

import { BASE } from './api'

type PendingError = { message: string; stack?: string; source?: string; page?: string }

const FLUSH_INTERVAL_MS = 10_000
const COOLDOWN_MS = 60_000 // per-fingerprint client-side cooldown
const MAX_BATCH = 10 // matches the backend's batch cap
const MAX_PER_SESSION = 50 // hard stop for a hopelessly broken session

const lastSent = new Map<string, number>()
const queue: PendingError[] = []
let sessionCount = 0
let timer: number | null = null

function fingerprint(e: PendingError): string {
  return `${e.message}\n${(e.stack || '').split('\n')[0]}\n${e.source || ''}`
}

// Project/topic straight out of the path — `/projects/<uuid>/topics/<uuid>`.
// Both used to be read from elsewhere (`/project/` singular, and a `?topic=`
// query), and both had gone stale: a rename moved the first and the topic left
// the query string when it became a path segment. An error report is the one
// place where a silently-null field looks exactly like "no topic was open".
const UUID = '[0-9a-f][0-9a-f-]{34}[0-9a-f]'
const WORKSPACE_PATH = new RegExp(`/projects/(${UUID})(?:/topics/(${UUID}))?`, 'i')

function context(): { projectId: string | null; topicId: string | null } {
  const m = WORKSPACE_PATH.exec(location.pathname)
  return { projectId: m?.[1] ?? null, topicId: m?.[2] ?? null }
}

export function reportError(message: string, stack?: string, source?: string): void {
  try {
    if (!message || sessionCount >= MAX_PER_SESSION) return
    const item: PendingError = {
      message: String(message).slice(0, 500),
      stack: stack ? String(stack).slice(0, 4000) : undefined,
      source: source ? String(source).slice(0, 300) : undefined,
      page: (location.pathname + location.search).slice(0, 300),
    }
    const fp = fingerprint(item)
    const now = Date.now()
    const last = lastSent.get(fp)
    if (last !== undefined && now - last < COOLDOWN_MS) return
    lastSent.set(fp, now)
    sessionCount += 1
    queue.push(item)
    if (queue.length >= MAX_BATCH) {
      void flush()
    } else if (timer === null) {
      timer = window.setTimeout(() => void flush(), FLUSH_INTERVAL_MS)
    }
  } catch {
    // The reporter itself must never throw.
  }
}

function batchBody(batch: PendingError[], projectId: string, topicId: string | null): string {
  return JSON.stringify({
    project_id: projectId,
    topic_id: topicId || undefined,
    errors: batch,
  })
}

async function flush(): Promise<void> {
  if (timer !== null) {
    clearTimeout(timer)
    timer = null
  }
  const { projectId, topicId } = context()
  const batch = queue.splice(0, MAX_BATCH)
  // Outside a project there is no 现场 to attach to — drop rather than hold
  // (holding would report stale errors against whatever project opens next).
  if (!projectId || batch.length === 0) return
  try {
    await fetch(`${BASE}/frontend-errors`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: batchBody(batch, projectId, topicId),
      keepalive: true,
    })
  } catch {
    // Backend unreachable — drop; 现场 reporting is best-effort by design.
  }
}

export function installErrorReporter(app: App): void {
  window.addEventListener('error', (ev: ErrorEvent) => {
    // Resource-load failures dispatch a plain Event (no message) — skip those.
    if (!ev.message) return
    const src = ev.filename ? `${ev.filename}:${ev.lineno}` : undefined
    reportError(ev.message, ev.error instanceof Error ? ev.error.stack : undefined, src)
  })
  window.addEventListener('unhandledrejection', (ev: PromiseRejectionEvent) => {
    const r: unknown = ev.reason
    if (r instanceof Error) {
      reportError(`Unhandled rejection: ${r.message}`, r.stack)
    } else {
      reportError(`Unhandled rejection: ${String(r)}`)
    }
  })
  app.config.errorHandler = (err, _instance, info) => {
    if (err instanceof Error) {
      reportError(err.message, err.stack, `vue:${info}`)
    } else {
      reportError(String(err), undefined, `vue:${info}`)
    }
    // Keep the default devtools visibility — reporting replaces silence,
    // not the console.
    console.error(err)
  }
  window.addEventListener('pagehide', () => {
    const { projectId, topicId } = context()
    const batch = queue.splice(0, MAX_BATCH)
    if (!projectId || batch.length === 0) return
    try {
      navigator.sendBeacon?.(
        `${BASE}/frontend-errors`,
        new Blob([batchBody(batch, projectId, topicId)], { type: 'application/json' })
      )
    } catch {
      // best-effort
    }
  })
}
