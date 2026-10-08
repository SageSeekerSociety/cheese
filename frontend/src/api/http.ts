// The HTTP transport every endpoint in `api.ts` rides on: the auth header, the
// GET retry/backoff, the read budget, and the conditional-GET plumbing (see
// `requestConditional`). Split out of `api.ts`, which was long past its size cap
// and had stopped having one reason to change — this half knows about HTTP and
// tokens, not about topics, projects or artifacts.
import type { ApiEnvelope } from '../cx_types'

import { t } from '../i18n'
import { desktopAppHeaders } from '../lib/desktopApp'
import { refusalText, refusalWords } from '../lib/noticeText'
import { rateLimitedText, rateLimitRetryMs } from '../lib/rateLimit'
import { refreshSession } from '../lib/session'
import { isTransportFailure, readJson, transportFailureMessage } from '../lib/transportFailure'

// The API base a BROWSER sends. One `/api`: the gateway's mount point, which
// `location /api/ { proxy_pass …:8081/; }` strips on the way through.
//
// It was `/api/api` until #370 step 2. The 2.0 routers used to carry their own
// `/api` — the only way to keep `topics`, `projects` and `tasks` from meaning
// two different things at one URL — so a browser had to send the prefix twice
// and the gateway ate one. Those words are now owned once each (1.0's tag is
// `/tags` and 赛题 are merged), so the namespace that separated them has
// nothing left to separate.
export const BASE = '/api'

// Chat requests use the same access token as AccountService.
export function authToken(): string {
  try {
    return localStorage.getItem('accessToken') ?? ''
  } catch {
    return ''
  }
}

export function authHeaders(): Record<string, string> {
  const token = authToken()
  return { ...desktopAppHeaders(), ...(token ? { Authorization: `Bearer ${token}` } : {}) }
}

// Retried on GET: the edge's own statuses. nginx answers 502–504 for an app it
// could not reach, Cloudflare answers 520–530 for an origin it could not (a
// tunnel that flapped is 530, error 1033). Each is about the second it was
// sent in, which is why the next attempt is worth making.
const CLOUDFLARE_ORIGIN_STATUSES = Array.from({ length: 11 }, (_, i) => 520 + i)
const RETRYABLE_GET_STATUSES = new Set([502, 503, 504, ...CLOUDFLARE_ORIGIN_STATUSES])
const GET_RETRY_DELAYS_MS = [250, 750]

// `errorPage`: the body was not JSON. That is the edge's page in place of an
// answer whatever the status line says — a captive portal and the SPA fallback
// both say 200 — and, like the statuses above, it is about this second.
export function isRetryableGetFailure(method: string, status?: number, error?: unknown, errorPage = false): boolean {
  if (method.toUpperCase() !== 'GET') return false
  if (error instanceof ApiError && error.retryable === false) return false
  if (errorPage) return true
  if (status != null) return RETRYABLE_GET_STATUSES.has(status)
  return !(error instanceof DOMException && error.name === 'AbortError')
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

// A failed request still carries its HTTP status. Callers that must tell one
// failure from another — a save rejected as a conflict (409) vs. anything else —
// would otherwise be left substring-matching the message.
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code?: string,
    readonly requestId?: string,
    readonly retryable?: boolean
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** A refusal, as the error a caller raises: the sentence the server said
 *  (`refusalWords`), with the status beside it when it said nothing, plus the
 *  name the backend gave the condition (`error.name`) as `code`. Callers tell
 *  one refusal from another by those fields, never by substring-matching the
 *  sentence.
 *
 *  Lives here, not beside `legacyRequest`: `api.ts` is over its size cap and
 *  frozen (`.claude/scripts/check-file-sizes.py`), so that call site may only
 *  shrink. Fold it back when the two request stacks do. */
export function refusalError(res: Response, body: unknown, path: string): ApiError {
  const serverSaid = refusalWords(body)
  const http = `HTTP ${res.status}`
  const said = serverSaid ? t('global.labelWithAside', { label: serverSaid, aside: http }) : `${http} for ${path}`
  return new ApiError(res.status, said, (body as { error?: { name?: string } } | null)?.error?.name)
}

export class RequestTimeoutError extends Error {
  constructor() {
    super(t('global.request.timeout'))
    this.name = 'RequestTimeoutError'
  }
}

export const READ_BUDGET_MS = 20_000

async function withinBudget<T>(
  work: (signal: AbortSignal) => Promise<T>,
  ms: number,
  outer?: AbortSignal | null
): Promise<T> {
  if (outer?.aborted) throw outer.reason ?? new DOMException(t('global.request.canceled'), 'AbortError')
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  let onAbort: (() => void) | undefined
  const cancelled = new Promise<never>((_, reject) => {
    onAbort = () => {
      controller.abort(outer?.reason)
      reject(outer?.reason ?? new DOMException(t('global.request.canceled'), 'AbortError'))
    }
    if (outer?.aborted) onAbort()
    else outer?.addEventListener('abort', onAbort, { once: true })
    timer = setTimeout(() => {
      const error = new RequestTimeoutError()
      controller.abort(error)
      reject(error)
    }, ms)
  })
  try {
    return await Promise.race([work(controller.signal), cancelled])
  } finally {
    clearTimeout(timer)
    if (onAbort) outer?.removeEventListener('abort', onAbort)
  }
}

// A page whose backend ships separately has to tell "this feature is not
// deployed here yet" apart from "it is deployed and it failed" — otherwise the
// first render of a not-yet-merged API is an error banner that reads like a bug.
// 404/405 is the only honest signal for it: the route does not exist.
export function isEndpointMissing(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 404 || e.status === 405)
}

// How close to expiry is "about to expire". The refresh below is what keeps a
// request from going out as nobody; a token that dies in flight costs the same
// as one that was already dead, so leave room for the round trip.
const TOKEN_REFRESH_LEEWAY_MS = 60_000

export function tokenExpiresWithin(token: string, ms: number): boolean {
  try {
    const payload = JSON.parse(atob(token.split('.')[1] ?? '')) as { exp?: number }
    if (typeof payload.exp !== 'number') return false
    return payload.exp * 1000 - Date.now() <= ms
  } catch {
    // Not a JWT we can read — leave it alone rather than refresh on every call.
    return false
  }
}

// 2.0 rides raw `fetch`, so it never passes through the axios response
// interceptor that refreshes on 401 — and most 2.0 routes do not answer 401
// anyway: they resolve the actor from the token and fall back to "nobody" when
// it does not verify. Both halves fail silently, which is how an expired token
// turned into 「左边栏冒出一堆不是我的项目」: the request went out as an anonymous
// caller, and the sidebar listing used to answer an anonymous caller with every
// project on the platform. The listing is scoped now (that is the security
// half), but a signed-in user whose token lapsed would still see an empty
// sidebar. So refresh it here, before the request, rather than react to a
// failure the transport cannot see.
//
// `GET /projects` is no longer one of the silent ones — it 401s on a bearer
// that failed to verify, so `request()`'s retry can heal it. Do not read that
// as "the transport can see it now": it holds for that one route, and this
// pre-request refresh is still what covers the rest.
export async function ensureFreshToken(): Promise<void> {
  const token = authToken()
  if (!token || !tokenExpiresWithin(token, TOKEN_REFRESH_LEEWAY_MS)) return
  await refreshNow()
}

/**
 * Refresh regardless of what the token's own `exp` claims.
 *
 * `ensureFreshToken` trusts `exp`, and `exp` is not the only way a token dies.
 * Measured on dev: a token minted 443s earlier, with 457s of its 900s life
 * left, was rejected 24 times out of 24 by BOTH api layers, while one minted
 * seconds later worked — the signing secret had changed under us (a backend
 * restart). Trusting `exp` alone means a signed-in user then 401s on every
 * request for up to 14 minutes, until the token nears the expiry that would
 * finally trigger a refresh. That is the 「通知铃铛必 401」 shape.
 *
 * Goes through `refreshSession`, so a burst of 401s costs one refresh, not one
 * each, and never races a refresh in another tab.
 */
export async function refreshNow(): Promise<void> {
  // A failed refresh leaves the stored token as it was; the caller still sees
  // its own request's result.
  await refreshSession()
}

// Room chrome, chat and the work panel request the same roster/task summary
// on mount. Share only pending reads; the next refresh always goes to the server.
const pendingRoomReads = new Map<string, Promise<unknown>>()
export function roomRead<T>(path: string): Promise<T> {
  const key = `${authToken()}:${path}`
  const pending = pendingRoomReads.get(key)
  if (pending) return pending as Promise<T>
  const started = request<T>(path).finally(() => {
    if (pendingRoomReads.get(key) === started) pendingRoomReads.delete(key)
  })
  pendingRoomReads.set(key, started)
  return started
}

/** 这个文件里的每个端点都过它。飞书那一块在 `api/feishu.ts`，也用这一个（见那儿的说明）。 */
export function request<T>(path: string, init?: RequestInit): Promise<T> {
  if ((init?.method ?? 'GET').toUpperCase() !== 'GET') return performRequest<T>(path, init)
  return withinBudget((signal) => performRequest<T>(path, { ...init, signal }), READ_BUDGET_MS, init?.signal)
}

/** A conditional GET came back 304: the representation the caller holds is still
 *  current, and the response carried no body to read. Thrown, not returned, so the
 *  ordinary `request<T>` path never has to know about it. */
export class NotModified extends Error {
  constructor(readonly etag: string | null) {
    super('not modified')
    this.name = 'NotModified'
  }
}

function performRequest<T>(path: string, init?: RequestInit): Promise<T> {
  return performRequestFull<T>(path, init).then((res) => res.data)
}

/** The full shape of a request: the unwrapped `data`, plus the response's `ETag`
 *  (null when the server did not send one). `request<T>` throws the tag away. */
async function performRequestFull<T>(path: string, init?: RequestInit): Promise<{ data: T; etag: string | null }> {
  const method = (init?.method ?? 'GET').toUpperCase()
  if (method !== 'GET') pendingRoomReads.clear()
  await ensureFreshToken()
  // A 401 is retried once, for ANY method, after forcing a refresh — see
  // `refreshNow`. Safe for writes too: a 401 means the request was rejected at
  // the door, so nothing happened that a retry could duplicate. Only retried
  // when the refresh actually produced a different token, or a server that 401s
  // for some other reason would make every call fire twice.
  let authRetried = false
  let rateRetried = false
  for (let attempt = 0; ; attempt += 1) {
    init?.signal?.throwIfAborted()
    let res: Response
    try {
      res = await fetch(`${BASE}${path}`, {
        ...init,
        headers: {
          'Content-Type': 'application/json',
          ...authHeaders(),
          ...(init?.headers ?? {}),
        },
      })
    } catch (error) {
      if (attempt >= GET_RETRY_DELAYS_MS.length || !isRetryableGetFailure(method, undefined, error)) {
        throw error
      }
      await wait(GET_RETRY_DELAYS_MS[attempt])
      continue
    }
    if (res.status === 304) {
      // Only reachable for a caller that sent `If-None-Match` (see
      // `requestConditional`). A 304 has no body, so this must come before
      // `readJson`, which would choke on the empty stream.
      throw new NotModified(res.headers?.get?.('ETag') ?? null)
    }
    if (res.status === 401 && !authRetried) {
      authRetried = true
      const before = authToken()
      await refreshNow()
      // `attempt` is deliberately not advanced: this retry is not one of the
      // transport's backoff attempts, and spending one here would cost a real
      // 502 its retry budget.
      if (authToken() !== before) {
        attempt -= 1
        continue
      }
    }
    // A read refused for coming too fast waits as long as it was told, once.
    const backoff = rateRetried ? null : rateLimitRetryMs(method, res)
    if (backoff != null) {
      rateRetried = true
      await wait(backoff)
      attempt -= 1
      continue
    }
    // Before asking what the app said, ask whether it was the app that spoke.
    // `HTTP 530 for /topics` and a raw `SyntaxError: Unexpected token '<'` were
    // what a hackathon room read while Cloudflare's tunnel flapped for a few
    // seconds, and they asked whether the backend was broken. It was not.
    const body = await readJson(res)
    if (isTransportFailure(body)) {
      if (attempt < GET_RETRY_DELAYS_MS.length && isRetryableGetFailure(method, res.status, undefined, true)) {
        await wait(GET_RETRY_DELAYS_MS[attempt])
        continue
      }
      throw new ApiError(res.status, transportFailureMessage(method, res.status))
    }
    if (!res.ok) {
      const details = body as { message?: string; error?: { name?: string; message?: string; retryable?: boolean } }
      if (
        details.error?.retryable !== false &&
        attempt < GET_RETRY_DELAYS_MS.length &&
        isRetryableGetFailure(method, res.status)
      ) {
        await wait(GET_RETRY_DELAYS_MS[attempt])
        continue
      }
      // #450 rule 2 (frontend edition): the backend's errors carry a human
      // sentence (`message`) — a toast that shows only "HTTP 422 for /path"
      // sends the room hunting a mystery the server had already explained.
      const serverSaid =
        rateLimitedText(res, body) ?? refusalText(body, details.error?.message || details.message || '')
      throw new ApiError(
        res.status,
        serverSaid || t('global.request.failed', { status: res.status }),
        details.error?.name,
        res.headers?.get('X-Request-ID') ?? undefined,
        details.error?.retryable
      )
    }
    const envelope = body as ApiEnvelope<T>
    if (envelope.code !== 200) {
      throw new Error(envelope.message || `API error code ${envelope.code}`)
    }
    if (method !== 'GET') pendingRoomReads.clear()
    return { data: envelope.data, etag: res.headers?.get?.('ETag') ?? null }
  }
}

/** The result of a conditional GET: either a fresh `data` (with the `etag` to ask
 *  with next time), or `notModified` for a 304 — the caller's copy is current, so
 *  there is nothing to parse, assign, or re-render. */
export interface ConditionalResult<T> {
  notModified: boolean
  data: T | null
  etag: string | null
}

/** A GET that asks the server "has this changed since `etag`?".
 *
 *  Sends `If-None-Match` and turns a 304 into `{ notModified: true }` instead of an
 *  error. `cache: 'no-store'` keeps the decision in THIS code rather than in the
 *  browser's HTTP cache, which would answer the 304 transparently with a stored copy
 *  and make "nothing changed" indistinguishable from "here is the body again" — the
 *  whole point is to skip the parse and the store write on an unchanged poll. */
export function requestConditional<T>(path: string, etag: string | null): Promise<ConditionalResult<T>> {
  const headers: Record<string, string> = etag ? { 'If-None-Match': etag } : {}
  return withinBudget(async (signal) => {
    try {
      const { data, etag: next } = await performRequestFull<T>(path, {
        headers,
        cache: 'no-store',
        signal,
      })
      return { notModified: false, data, etag: next }
    } catch (error) {
      if (error instanceof NotModified) {
        return { notModified: true, data: null, etag: error.etag ?? etag }
      }
      throw error
    }
  }, READ_BUDGET_MS)
}
