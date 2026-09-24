// REST helpers for the CheeseX backend. All responses are wrapped in an
// ApiEnvelope; these helpers unwrap `data` and surface non-200 codes as errors.
import type {
  AcceptCard,
  AgentConfiguration,
  AgentControlRequest,
  AgentControlState,
  AgentType,
  ApiEnvelope,
  Block,
  BranchProtection,
  BranchProtectionPatch,
  BranchProtectionRules,
  ChatAttachment,
  ComputeProfiles,
  DocumentRevision,
  EnvironmentConfig,
  EnvironmentStatus,
  FeedbackCard,
  FeedbackComment,
  FeedbackCommentLikeResult,
  FeedbackCounts,
  FeedbackCreateBody,
  FeedbackDetail,
  FeedbackListPayload,
  FeedbackMeta,
  FeedbackNote,
  FeedbackPriority,
  FeedbackProposal,
  FeedbackStatus,
  FeedbackSupportResult,
  FeedbackVisibility,
  FileContent,
  FileSource,
  ForgeAttribution,
  ForgeConnection,
  GitCommit,
  GithubConnection,
  InboxItem,
  ListPayload,
  MarketNodes,
  MarketPools,
  MemberSummary,
  MilestoneFull,
  OAuthConnectionInfo,
  PrChecks,
  PreviewInfo,
  Project,
  ProjectAgent,
  ProjectCredits,
  ProjectEnvironmentInfo,
  ProjectInvitation,
  ProjectMemberRow,
  ProjectSite,
  ProjectSiteInfo,
  ReactionAgg,
  RoomTask,
  Topic,
  TopicComputeProfile,
  TopicMemberRow,
  TopicProgress,
  TopicWorkSummary,
  UpstreamInfo,
  UsageStats,
  UserProfile,
  WaitingItem,
  WorkspaceFile,
} from './cx_types'

import { TOPIC_TITLE_MAX_LENGTH } from './lib/topicTitle'
import { isTransportFailure, transportFailureMessage } from './lib/transportFailure'
import { SudoRequiredError } from './network/types/error'

export { TOPIC_TITLE_MAX_LENGTH }

// The API base a BROWSER sends. One `/api`: the gateway's mount point, which
// `location /api/ { proxy_pass …:8081/; }` strips on the way through.
//
// It was `/api/api` until #370 step 2. The 2.0 routers used to carry their own
// `/api` — the only way to keep `topics`, `projects` and `tasks` from meaning
// two different things at one URL — so a browser had to send the prefix twice
// and the gateway ate one. Those words are now owned once each (1.0's tag is
// `/tags`, its team project `/team-projects`, and 赛题 are merged), so the
// namespace that separated them has nothing left to separate.
export const BASE = '/api'

// A project's join link. Permanent until a manager resets it; `approval` says
// whether joining through it waits for a manager or happens on confirmation.
export interface ProjectJoinLink {
  token: string
  approval: boolean
}

export type ProjectJoinStatus = 'member' | 'pending' | 'none'

export interface ProjectJoinPreview {
  project_id: string
  project_name: string
  approval: boolean
  join_status: ProjectJoinStatus
}

export interface ProjectJoinRequest {
  id: string
  requester_handle: string
  name: string
  avatar_id: number | null
  message: string
  created_at: string
}

function joinLinkPath(projectId: string) {
  return `/projects/${encodeURIComponent(projectId)}/join-link`
}

export function getProjectJoinLink(projectId: string) {
  return request<ProjectJoinLink>(joinLinkPath(projectId))
}

export function setProjectJoinApproval(projectId: string, approval: boolean) {
  return request<ProjectJoinLink>(joinLinkPath(projectId), { method: 'PATCH', body: JSON.stringify({ approval }) })
}

export function resetProjectJoinLink(projectId: string) {
  return request<ProjectJoinLink>(`${joinLinkPath(projectId)}/reset`, { method: 'POST' })
}

export function previewProjectJoinLink(token: string) {
  return request<ProjectJoinPreview>(`/project-invites/${encodeURIComponent(token)}`)
}

export function joinProjectByLink(token: string, message?: string) {
  return request<ProjectJoinPreview>(`/project-invites/${encodeURIComponent(token)}/join`, {
    method: 'POST',
    body: JSON.stringify(message ? { message } : {}),
  })
}

export function listProjectJoinRequests(projectId: string) {
  return request<ProjectJoinRequest[]>(`/projects/${encodeURIComponent(projectId)}/join-requests`)
}

export function decideProjectJoinRequest(projectId: string, requestId: string, decision: 'approve' | 'reject') {
  return request<{ ok: boolean }>(
    `/projects/${encodeURIComponent(projectId)}/join-requests/${encodeURIComponent(requestId)}/${decision}`,
    { method: 'POST' }
  )
}

// Chat requests use the same access token as AccountService.
export function authToken(): string {
  try {
    return localStorage.getItem('accessToken') ?? ''
  } catch {
    return ''
  }
}

function authHeaders(): Record<string, string> {
  const token = authToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
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

// The parsed body, or NOT_JSON when there is no JSON to parse. The content-type
// is checked first so an HTML page is never handed to a JSON parser; a response
// with no `headers` at all (fetch always sets them, test doubles do not) is
// given the benefit of the parse.
const NOT_JSON = Symbol('not JSON')

async function readJson(res: Response): Promise<unknown> {
  const type = res.headers?.get('content-type')
  if (type != null && !type.includes('application/json')) return NOT_JSON
  try {
    return await res.json()
  } catch {
    return NOT_JSON
  }
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

export class RequestTimeoutError extends Error {
  constructor() {
    super('请求等待超时，请重试')
    this.name = 'RequestTimeoutError'
  }
}

export const READ_BUDGET_MS = 20_000
export const TOKEN_REFRESH_BUDGET_MS = 10_000

async function withinBudget<T>(
  work: (signal: AbortSignal) => Promise<T>,
  ms: number,
  outer?: AbortSignal | null
): Promise<T> {
  if (outer?.aborted) throw outer.reason ?? new DOMException('请求已取消', 'AbortError')
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  let onAbort: (() => void) | undefined
  const cancelled = new Promise<never>((_, reject) => {
    onAbort = () => {
      controller.abort(outer?.reason)
      reject(outer?.reason ?? new DOMException('请求已取消', 'AbortError'))
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

// One refresh in flight at a time. Without this, a page that fires eight
// requests on mount fires eight refreshes, and the losers race to overwrite
// `accessToken` with each other's result.
let refreshInFlight: Promise<void> | null = null

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
 * seconds later worked — and `decode_token` does pure JWT verification with no
 * revocation store, so the signing secret must have changed under us (a backend
 * restart). Trusting `exp` alone means a signed-in user then 401s on every
 * request for up to 14 minutes, until the token nears the expiry that would
 * finally trigger a refresh. That is the 「通知铃铛必 401」 shape.
 *
 * Shares `refreshInFlight` with `ensureFreshToken`, so a burst of 401s costs one
 * refresh, not one each.
 */
export async function refreshNow(): Promise<void> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const next = await withinBudget(async (signal) => {
          const res = await fetch('/api/users/auth/refresh-token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            signal,
          })
          if (!res.ok) return undefined
          const body = (await res.json()) as { data?: { accessToken?: string } }
          signal.throwIfAborted()
          return body?.data?.accessToken
        }, TOKEN_REFRESH_BUDGET_MS)
        if (next) localStorage.setItem('accessToken', next)
      } catch {
        // Offline, or the refresh cookie is gone. Sending the stale token is
        // no worse than sending nothing, and the caller still sees the result.
      } finally {
        refreshInFlight = null
      }
    })()
  }
  await refreshInFlight
}

// Room chrome, chat and the work panel request the same roster/task summary
// on mount. Share only pending reads; the next refresh always goes to the server.
const pendingRoomReads = new Map<string, Promise<unknown>>()
function roomRead<T>(path: string): Promise<T> {
  const key = `${authToken()}:${path}`
  const pending = pendingRoomReads.get(key)
  if (pending) return pending as Promise<T>
  const started = request<T>(path).finally(() => {
    if (pendingRoomReads.get(key) === started) pendingRoomReads.delete(key)
  })
  pendingRoomReads.set(key, started)
  return started
}

function request<T>(path: string, init?: RequestInit): Promise<T> {
  if ((init?.method ?? 'GET').toUpperCase() !== 'GET') return performRequest<T>(path, init)
  return withinBudget((signal) => performRequest<T>(path, { ...init, signal }), READ_BUDGET_MS, init?.signal)
}

async function performRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? 'GET').toUpperCase()
  if (method !== 'GET') pendingRoomReads.clear()
  await ensureFreshToken()
  // A 401 is retried once, for ANY method, after forcing a refresh — see
  // `refreshNow`. Safe for writes too: a 401 means the request was rejected at
  // the door, so nothing happened that a retry could duplicate. Only retried
  // when the refresh actually produced a different token, or a server that 401s
  // for some other reason would make every call fire twice.
  let authRetried = false
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
      const said = body as { message?: string; error?: { message?: string } }
      const serverSaid = said.error?.message || said.message || ''
      throw new ApiError(
        res.status,
        serverSaid || `请求失败（HTTP ${res.status}）`,
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
    return envelope.data
  }
}

// The connector lives at the origin root (`/connector/*`), not under `/api`, and its
// responses are plain JSON (no ApiEnvelope). This mirrors `request` but skips the
// `/api` prefix + envelope unwrap. Still sends the Bearer token for owner-gated routes.
async function connectorRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/connector${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init?.headers ?? {}),
    },
  })
  if (!res.ok) {
    let message = `HTTP ${res.status}`
    try {
      const body = await res.json()
      message = body?.message || body?.detail || message
    } catch {
      // non-JSON error body — keep the status message
    }
    throw new Error(message)
  }
  return (await res.json()) as T
}

// Mirrors `request`'s envelope unwrap and auth header, minus the GET retry.
//
// It exists because 1.0 was single-prefixed while 2.0 was doubled, and that
// reason is gone: since #370 step 2 `BASE` is `/api` too, so the two differ
// ONLY by that retry. Folding them together is worth doing and is not a
// rename — it decides whether 1.0 calls start being retried, or 2.0 calls stop
// being — so it wants its own change, not a drive-by.
async function legacyRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? 'GET'
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init?.headers ?? {}),
    },
  })
  const body = await readJson(res)
  if (isTransportFailure(body)) {
    throw new ApiError(res.status, transportFailureMessage(method, res.status))
  }
  if (!res.ok) {
    const said = body as { message?: string; error?: { name?: string; message?: string } }
    // The one refusal the caller answers differently: withSudo sends the user
    // through re-authentication and retries, so it must see the typed error.
    if (said.error?.name === 'SudoRequiredError') throw new SudoRequiredError(said.message)
    const serverSaid = said.message || said.error?.message || ''
    throw new Error(serverSaid ? `${serverSaid}（HTTP ${res.status}）` : `HTTP ${res.status} for ${path}`)
  }
  const envelope = body as ApiEnvelope<T>
  if (envelope.code !== 200) {
    throw new Error(envelope.message || `API error code ${envelope.code}`)
  }
  return envelope.data
}

// The name the cli proposed for a pending code (this machine's hostname), so the
// approval page can prefill an editable default instead of leaving it blank.
export function deviceProposedName(code: string): Promise<{ device_name: string | null }> {
  return connectorRequest<{ device_name: string | null }>(`/auth/device/proposed-name?code=${encodeURIComponent(code)}`)
}

// Approve a pending device flow (the `/connect` page): binds the compute machine to
// the logged-in human as owner (optionally naming it). A device is pure compute — no
// agent is minted here; the agent that runs on it is resolved per session/project.
export function connectDevice(
  deviceCode: string,
  deviceName?: string,
  projectId?: string
): Promise<import('./cx_types').DeviceApproval> {
  return connectorRequest<import('./cx_types').DeviceApproval>('/connect', {
    method: 'POST',
    body: JSON.stringify({
      device_code: deviceCode,
      device_name: deviceName ?? null,
      project_id: projectId ?? null,
    }),
  })
}

// 「我的设备」: the machines the signed-in human enrolled, with liveness + agents.
export function listMyDevices(): Promise<{
  devices: import('./cx_types').MyDevice[]
}> {
  return connectorRequest<{ devices: import('./cx_types').MyDevice[] }>('/my/devices')
}

export function renameMyDevice(deviceId: string, name: string): Promise<import('./cx_types').MyDevice> {
  return connectorRequest<import('./cx_types').MyDevice>(`/my/devices/${encodeURIComponent(deviceId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  })
}

export function unbindMyDevice(deviceId: string): Promise<{ deleted: boolean; device_id: string }> {
  return connectorRequest<{ deleted: boolean; device_id: string }>(`/my/devices/${encodeURIComponent(deviceId)}`, {
    method: 'DELETE',
  })
}

// 为团队注册设备 (v4): bind/unbind a machine to a team so the team's projects can
// run on it. Both return the updated device view.
export function registerDeviceForTeam(deviceId: string, teamId: number): Promise<import('./cx_types').MyDevice> {
  return connectorRequest<import('./cx_types').MyDevice>(`/my/devices/${encodeURIComponent(deviceId)}/teams`, {
    method: 'POST',
    body: JSON.stringify({ team_id: teamId }),
  })
}
export function unregisterDeviceFromTeam(deviceId: string, teamId: number): Promise<import('./cx_types').MyDevice> {
  return connectorRequest<import('./cx_types').MyDevice>(
    `/my/devices/${encodeURIComponent(deviceId)}/teams/${teamId}`,
    { method: 'DELETE' }
  )
}

// 团队算力 (v4): the machines registered for a team — the team's compute, with
// liveness. Any team member may view.
export function listTeamDevices(teamId: number): Promise<{ devices: import('./cx_types').MyDevice[] }> {
  return connectorRequest<{ devices: import('./cx_types').MyDevice[] }>(`/teams/${teamId}/devices`)
}

// The teams the signed-in user belongs to. Includes the auto-provisioned personal
// team (个人 = 单人真团队), which the backend sorts first and flags `personal`.
export function listMyTeams(): Promise<import('./cx_types').MyTeam[]> {
  return request<{ teams: Array<{ id: number; handle: string; name: string; personal?: boolean }> }>(
    '/teams/my-teams'
  ).then((r) => r.teams.map((t) => ({ id: t.id, handle: t.handle, name: t.name, personal: t.personal === true })))
}

// Absolute WS URL for a device screen's 现场 (read-only terminal). The session token
// rides as ?token= (browsers can't set an Authorization header on a WebSocket); the
// backend authorizes the viewer against the screen's project/topic membership.
// VITE_CONNECTOR_WS_BASE (a plain http(s) origin, runtime-injected in prod) reroutes
// just this socket when the site origin sits behind a WS-stripping edge (校园前置
// 反代); token-in-query means cross-origin needs no cookie/CORS handling.
export function screenWsUrl(sid: string): string {
  const token = authToken()
  const q = token ? `?token=${encodeURIComponent(token)}` : ''
  const override = (import.meta.env.VITE_CONNECTOR_WS_BASE as string | undefined) ?? ''
  let base: string
  if (override && !override.startsWith('__')) {
    base = override.replace(/^http/, 'ws').replace(/\/$/, '')
  } else {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    base = `${proto}://${window.location.host}`
  }
  return `${base}/connector/session/${encodeURIComponent(sid)}/screen${q}`
}

export function listProjects(teamId?: number): Promise<ListPayload<Project>> {
  const q = teamId != null ? `?team_id=${teamId}` : ''
  return request<ListPayload<Project>>(`/projects${q}`)
}

/** 待我处理：跨项目、点到我的那些事项，最近动过的在前。
 *
 *  和看板读同一份规则（后端 `room_task/presentation.py`），所以一件事在看板上是待
 *  处理，在这里就是待处理。它不是通知列表的另一种视图：通知是事件记录，答不出
 *  「现在还没处理完的有哪些」。 */
export function listAwaitingMe(): Promise<ListPayload<WaitingItem>> {
  return request<ListPayload<WaitingItem>>('/awaiting-me')
}

/** 订阅浏览器推送要用的 VAPID 公钥；这个部署没开推送时 `key` 是 null。 */
export function pushPublicKey(): Promise<{ key: string | null; available: boolean }> {
  return request<{ key: string | null; available: boolean }>('/push/key')
}

/** 登记这个浏览器的推送订阅。幂等：同一个 endpoint 再来一次就覆盖。 */
export function savePushSubscription(body: {
  endpoint: string
  p256dh: string
  auth: string
  user_agent?: string
}): Promise<{ saved: boolean }> {
  return request<{ saved: boolean }>('/push/subscriptions', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

/** 退订这个浏览器。走 POST 是因为 endpoint 是个上千字符的 URL，见后端那条注释。 */
export function dropPushSubscription(endpoint: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>('/push/subscriptions/delete', {
    method: 'POST',
    body: JSON.stringify({ endpoint }),
  })
}

export function createProject(
  name: string,
  ownerHandle?: string,
  teamId?: number,
  externalTaskId?: number,
  forgeKind?: 'forgejo' | 'github_app',
  agentName?: string
): Promise<Project> {
  return request<Project>('/projects', {
    method: 'POST',
    body: JSON.stringify({
      name,
      owner_handle: ownerHandle,
      team_id: teamId,
      // Set when the project is created FROM a 赛题, so the 赛题 can find it
      // again. Absent for a project made from the rail.
      external_task_id: externalTaskId,
      forge_kind: forgeKind,
      agent_name: agentName,
    }),
  })
}

// The 2.0 projects created from one 赛题 — what the 赛题 page shows instead of
// blindly offering to create another.
export function listProjectsForTask(taskId: number): Promise<ListPayload<Project>> {
  return request<ListPayload<Project>>(`/projects/by-task/${taskId}`)
}

// Single project card (includes `summary`, the 一页纸总结).
export function getProject(projectId: string): Promise<Project> {
  return request<Project>(`/projects/${encodeURIComponent(projectId)}`)
}

export function getProjectSite(projectId: string): Promise<ProjectSiteInfo> {
  return request<ProjectSiteInfo>(`/projects/${encodeURIComponent(projectId)}/site`)
}

export function publishProjectSite(
  projectId: string,
  body: { directory: string; expected_source_revision: string }
): Promise<ProjectSite> {
  return request<ProjectSite>(`/projects/${encodeURIComponent(projectId)}/site`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function requestSiteSession(projectId: string): Promise<{ url: string; grant: string }> {
  return request<{ url: string; grant: string }>(`/projects/${encodeURIComponent(projectId)}/site-session`, {
    method: 'POST',
  })
}

// NOTE: there is deliberately no `generateSummary` wrapper here. The POST it
// called is parked (see `backend/app/api/routes/activities.py`), so keeping the
// wrapper would only leave a 404 waiting for its first caller. `summary` still
// arrives on the project card above — it just has no trigger in the UI.

// A 1:1 private chat as a normal Topic (open the chat WS on its id). `peerHandle`
// is a person-to-person DM between the two humans (shared by both); `agentHandle`
// is the member's 1:1 with that AI teammate — one room per teammate, and it stays
// that teammate's even after the project's default changes. Neither → the
// project's default teammate.
export function getPrivateChat(
  projectId: string,
  userHandle: string,
  peerHandle?: string,
  agentHandle?: string
): Promise<Topic> {
  const peer = peerHandle ? `&peer_handle=${encodeURIComponent(peerHandle)}` : ''
  const agent = agentHandle ? `&agent_handle=${encodeURIComponent(agentHandle)}` : ''
  return request<Topic>(
    `/projects/${encodeURIComponent(projectId)}/private-chat?user_handle=${encodeURIComponent(userHandle)}${peer}${agent}`
  )
}

// 成员页 / portfolio (spec §7.2): what a member started + what awaits them.
export function getMemberSummary(projectId: string, handle: string): Promise<MemberSummary> {
  return request<MemberSummary>(
    `/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(handle)}/summary`
  )
}

// 个人主页 / LinkedIn-GitHub profile (spec §1, §7.2). Cross-project résumé:
// header + skills/interests + 芝士 understanding + per-project contributions.
export function getUserProfile(handle: string): Promise<UserProfile> {
  return request<UserProfile>(`/users/${encodeURIComponent(handle)}/profile`)
}

// `last_activity_at` = 最后活动时间 (the topic's newest block). `updated_at` is
// the row's own mtime and does NOT move when a block lands — it is kept only
// because the API still accepts it.
export type TopicSortField = 'last_activity_at' | 'updated_at' | 'title'
export type TopicSortOrder = 'asc' | 'desc'

export function listTopics(
  projectId: string,
  // `activeSince` (ISO 时刻) 只留那一刻之后有动静的话题——「最近活跃的那些」。
  // 我的工作那一页用它把每个项目的请求收在一个时间窗口里：一个项目可以有上百个
  // 房间，全量拉回来只为了看有没有在跑，是拿一屏的时间换一个数字。
  opts?: { sort?: TopicSortField; order?: TopicSortOrder; activeSince?: string }
): Promise<ListPayload<Topic>> {
  const q = new URLSearchParams({ project_id: projectId })
  if (opts?.sort) q.set('sort', opts.sort)
  if (opts?.order) q.set('order', opts.order)
  if (opts?.activeSince) q.set('active_since', opts.activeSince)
  return request<ListPayload<Topic>>(`/topics?${q.toString()}`)
}

// 整个项目的支线，每条带着它当前骑的那张验收卡。侧栏要画「房间 → 它派出去的活
// → 那件活的 PR」这棵树，而按房间问是一个房间一个请求（这里有一百七十多个）。
export function listProjectTasks(projectId: string): Promise<ListPayload<RoomTask>> {
  return request<ListPayload<RoomTask>>(`/projects/${encodeURIComponent(projectId)}/tasks`)
}

/** Tasks in this room, each with its own branch and delivery. */
export function listRoomTasks(
  roomId: string,
  // 每条支线最多带回多少块对话。标记只要支线本身，所以取 1 —— 不传的话后端会把
  // 房间里每条支线的全部历史都吐回来（它自己的 docstring 说明了为什么没有默认上限）。
  opts?: { limit?: number }
): Promise<ListPayload<RoomTask & { blocks: Block[] }>> {
  const q = new URLSearchParams()
  if (opts?.limit != null) q.set('limit', String(opts.limit))
  const query = q.toString() ? `?${q.toString()}` : ''
  return roomRead<ListPayload<RoomTask & { blocks: Block[] }>>(`/topics/${encodeURIComponent(roomId)}/tasks${query}`)
}

export function createTopic(projectId: string, title: string, parentId?: string): Promise<Topic> {
  const body: Record<string, string> = { project_id: projectId, title }
  if (parentId) body.parent_id = parentId
  return request<Topic>('/topics', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// ---- 话题级未读 (Feishu-style badges) ----

// {topic_id: unread_count} for one user; topics with zero unread are omitted.
export function getTopicUnread(projectId: string, handle: string): Promise<Record<string, number>> {
  return request<Record<string, number>>(
    `/projects/${encodeURIComponent(projectId)}/topic-unread?handle=${encodeURIComponent(handle)}`
  )
}

// {peer_handle: unread_count} for one user's 私聊; `cheese` is the 芝士 DM.
// Keyed by peer, not topic id: DM rows come from the member roster, which
// carries no topic id, so getTopicUnread's map cannot address them.
export function getPrivateUnread(projectId: string, handle: string): Promise<Record<string, number>> {
  return request<Record<string, number>>(
    `/projects/${encodeURIComponent(projectId)}/private-unread?handle=${encodeURIComponent(handle)}`
  )
}

// Opening a topic bumps the user's read cursor (clears its badge).
export function markTopicRead(topicId: string, handle: string): Promise<Record<string, string>> {
  return request<Record<string, string>>(`/topics/${encodeURIComponent(topicId)}/read`, {
    method: 'POST',
    body: JSON.stringify({ handle }),
  })
}

export function setTopicTitle(topicId: string, title: string): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}/title`, {
    method: 'POST',
    body: JSON.stringify({ title }),
  })
}

// ---- 归档去向: manual archive / unarchive ----

export function archiveTopic(topicId: string, by: string): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}/archive`, {
    method: 'POST',
    body: JSON.stringify({ by }),
  })
}

export function unarchiveTopic(topicId: string, by: string): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}/unarchive`, {
    method: 'POST',
    body: JSON.stringify({ by }),
  })
}

// 把一条消息升级成它自己的地点 (eval A1)。`blockId` 是那条消息的 block id。
// 房间里的消息升级出来的是一条**支线**；私聊里的升级出来的是一个真房间——私聊
// 不在话题树里，支线在那儿没人打得开。所以回答有两种形状。
/** 升级一条消息。房间里的消息变成这个房间的一张**卡**（回来的是 RoomTask），
 *  私聊里的变成一个新房间（回来的是 Topic）。 */
export function upgradeBlock(blockId: string, createdBy: string): Promise<Topic | RoomTask> {
  return request<Topic | RoomTask>(`/blocks/${encodeURIComponent(blockId)}/upgrade`, {
    method: 'POST',
    body: JSON.stringify({ created_by: createdBy }),
  })
}

// ---- 资源池市场 + 项目设置 (design v3) ----

// The full 市场 catalog: every AI pool + compute pool on offer.
export function getMarketPools(): Promise<MarketPools> {
  return request<MarketPools>('/market/pools')
}

// 节点看板: configured compute nodes with liveness + current load.
export function getMarketNodes(): Promise<MarketNodes> {
  return request<MarketNodes>('/market/nodes')
}

// 算力额度: the project's grant balance (unlimited when no grants).
export function getProjectCredits(projectId: string): Promise<ProjectCredits> {
  return request<ProjectCredits>(`/projects/${encodeURIComponent(projectId)}/credits`)
}

// ---- 题目匹配市场 (spec §13 阶段 6) ----

// 算力池: the project's current compute pool + the deployed ones it may select.
export function getComputeProfiles(projectId: string): Promise<ComputeProfiles> {
  return request<ComputeProfiles>(`/projects/${encodeURIComponent(projectId)}/compute-profiles`)
}
export function setComputeProfile(projectId: string, profile: string): Promise<{ current: string }> {
  return request(`/projects/${encodeURIComponent(projectId)}/compute-profile`, {
    method: 'PUT',
    body: JSON.stringify({ profile }),
  })
}

// MicroCloud machines are billed/audited through one project but enroll into that
// project's team compute pool. The browser never receives provider credentials.
export interface ResourceLimits {
  max_machines_per_team: number
  max_concurrent_turns: number
}

export function getResourceLimits(): Promise<ResourceLimits> {
  return request('/projects/resource-limits')
}

export interface MachineQuota {
  team_id: number
  used: number
  limit: number
  project_used: number
}

export interface TeamResourceQuotas {
  team_id: number
  machines: { used: number; limit: number }
  credits: {
    unlimited: boolean
    credits_total: number
    credits_used: number
    credits_remaining: number
    tokens_per_credit: number
  }
  projects: {
    id: string
    name: string
    machines_used: number
    total_tokens: number
    restricted_credits_remaining: number
  }[]
}

export function getTeamResourceQuotas(teamId: number): Promise<TeamResourceQuotas> {
  return request(`/teams/${teamId}/resource-quotas`)
}

export function listProjectMachines(
  projectId: string
): Promise<ListPayload<import('./cx_types').ProjectMachine> & { quota: MachineQuota }> {
  return request(`/projects/${encodeURIComponent(projectId)}/machines`)
}

export function createProjectMachine(
  projectId: string,
  spec: import('./cx_types').ProjectMachineCreate
): Promise<import('./cx_types').ProjectMachine> {
  return request(`/projects/${encodeURIComponent(projectId)}/machines`, {
    method: 'POST',
    body: JSON.stringify(spec),
  })
}

export function deleteProjectMachine(
  projectId: string,
  machineId: string
): Promise<import('./cx_types').ProjectMachine | null> {
  return request(`/projects/${encodeURIComponent(projectId)}/machines/${encodeURIComponent(machineId)}`, {
    method: 'DELETE',
  })
}

export function changeProjectMachinePower(
  projectId: string,
  machineId: string,
  operation: 'suspend' | 'resume'
): Promise<import('./cx_types').ProjectMachine> {
  return request(`/projects/${encodeURIComponent(projectId)}/machines/${encodeURIComponent(machineId)}/${operation}`, {
    method: 'POST',
  })
}

// 会话级算力 (v4): a topic's own compute选择, switchable until its first turn.
export function getTopicComputeProfile(topicId: string): Promise<TopicComputeProfile> {
  return request<TopicComputeProfile>(`/topics/${encodeURIComponent(topicId)}/compute-profile`)
}

export function getSessionWorkLeases(topicId: string): Promise<{ sessions: import('./cx_types').SessionWorkLease[] }> {
  return request(`/topics/${encodeURIComponent(topicId)}/sessions/work-leases`)
}

export function setSessionWorkChoice(topicId: string, sessionId: string, choice: import('./cx_types').ComputeChoice) {
  return request<{ session: import('./cx_types').SessionWorkLease }>(
    `/topics/${encodeURIComponent(topicId)}/sessions/${encodeURIComponent(sessionId)}/work-choice`,
    { method: 'PUT', body: JSON.stringify({ choice }) }
  )
}

export function getProjectComputeConfigs(projectId: string): Promise<import('./cx_types').ProjectComputeConfigs> {
  return request(`/projects/${encodeURIComponent(projectId)}/compute-configs`)
}

export function saveProjectComputeConfigs(
  projectId: string,
  configs: Pick<import('./cx_types').ProjectComputeConfigs, 'default' | 'favorites'>
): Promise<Pick<import('./cx_types').ProjectComputeConfigs, 'default' | 'favorites'>> {
  return request(`/projects/${encodeURIComponent(projectId)}/compute-configs`, {
    method: 'PUT',
    body: JSON.stringify(configs),
  })
}

// 撞上项目档位策略时这次选择没有发生，换来的是一条给人的提议 —— 接口照样 200，
// 所以「有没有 proposal」是调用方唯一能看出区别的地方（backend
// `domain/policy/gate.py`）。丢掉它就等于告诉点了按钮的人什么也没发生。
export interface ComputeProposal {
  approver: string
  tier: string
  content: string
}

export function setTopicComputeChoice(
  topicId: string,
  choice: import('./cx_types').ComputeChoice
): Promise<{ choice: import('./cx_types').ComputeChoice; proposal: ComputeProposal | null }> {
  return request(`/topics/${encodeURIComponent(topicId)}/compute-profile`, {
    method: 'PUT',
    body: JSON.stringify({ choice }),
  })
}
export function setTopicComputeProfile(
  topicId: string,
  profile: string,
  deviceId: string | null = null
): Promise<{
  current: string
  device_id: string | null
  locked: boolean
  inherited: boolean
}> {
  return request(`/topics/${encodeURIComponent(topicId)}/compute-profile`, {
    method: 'PUT',
    body: JSON.stringify(profile === 'device' ? { profile, device_id: deviceId } : { profile }),
  })
}

// ---- AI 队友 (agent 类型与实例) ----
//
// 「不能停用最后一个」and the like are the backend's to enforce; these are plain
// transports. What they must NOT do is paper over a missing endpoint: the agent
// backend lands separately, so a 404 here has to reach the caller as a 404 (see
// `isEndpointMissing`) rather than as an empty list that reads like "no agents".

// 一个模型在选单上的样子。后端是唯一事实源（model_choices），这里不留第二份
// 清单。
export interface AgentFieldChoice {
  id: string
  label: string
  description: string
  default: boolean
}

// Project main and native subagent model defaults.
export interface ProjectDefaultModel {
  subagent_model: string | null
  /** 项目显式设的模型；null = 没设，走 deployment_default */
  model: string | null
  /** 没设显式默认时，部署兜底算出来的那个 */
  deployment_default: string | null
  /** 当前项目能用的全部模型，每个带 default 标记（项目显式设过的那条=True） */
  choices: AgentFieldChoice[]
  can_manage: boolean
}

export function getProjectDefaultModel(projectId: string): Promise<ProjectDefaultModel> {
  return request(`/projects/${encodeURIComponent(projectId)}/default-model`)
}

export function setProjectDefaultModel(
  projectId: string,
  model: string | null,
  subagentModel?: string | null
): Promise<ProjectDefaultModel> {
  return request(`/projects/${encodeURIComponent(projectId)}/default-model`, {
    method: 'PUT',
    body: JSON.stringify({ model, subagent_model: subagentModel }),
  })
}

// Built-in starting configurations, copied only when creating an agent.
export function listAgentTypes(): Promise<ListPayload<AgentType>> {
  return request<ListPayload<AgentType>>('/agent-types')
}

export function listProjectAgents(projectId: string): Promise<ListPayload<ProjectAgent>> {
  return request<ListPayload<ProjectAgent>>(`/projects/${encodeURIComponent(projectId)}/agents`)
}

export function createProjectAgent(
  projectId: string,
  payload: { display_name: string; handle?: string; type_name?: string | null; configuration: AgentConfiguration }
): Promise<ProjectAgent> {
  return request<ProjectAgent>(`/projects/${encodeURIComponent(projectId)}/agents`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateProjectAgent(
  projectId: string,
  agentId: string,
  payload: { display_name?: string; configuration?: AgentConfiguration }
): Promise<ProjectAgent> {
  return request<ProjectAgent>(`/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

// 停用 — not a physical delete. Topics already using it keep working and its
// memory is kept; it just stops being选得到 for new ones.
export function deactivateProjectAgent(projectId: string, agentId: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(
    `/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}`,
    { method: 'DELETE' }
  )
}

// Select the existing agent that new rooms start with.
export function setProjectDefaultAgent(projectId: string, body: { instance_id: string }): Promise<ProjectAgent> {
  return request<ProjectAgent>(`/projects/${encodeURIComponent(projectId)}/default-agent`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function getProjectEnvironment(projectId: string): Promise<ProjectEnvironmentInfo> {
  return request(`/projects/${encodeURIComponent(projectId)}/environment`)
}
export function saveProjectEnvironment(
  projectId: string,
  config: Omit<EnvironmentConfig, 'revision'>
): Promise<EnvironmentConfig> {
  return request(`/projects/${encodeURIComponent(projectId)}/environment`, {
    method: 'PUT',
    body: JSON.stringify(config),
  })
}
export function getRoomEnvironment(projectId: string, roomId: string): Promise<EnvironmentStatus> {
  return request(`/projects/${encodeURIComponent(projectId)}/environment/rooms/${encodeURIComponent(roomId)}`)
}
export function applyRoomEnvironment(projectId: string, roomId: string, latest: boolean): Promise<EnvironmentStatus> {
  return request(`/projects/${encodeURIComponent(projectId)}/environment/rooms/${encodeURIComponent(roomId)}/apply`, {
    method: 'POST',
    body: JSON.stringify({ latest }),
  })
}

// 上游仓库 (spec §6.3): link an existing git repo and pull its history in.
export function getUpstream(projectId: string): Promise<UpstreamInfo> {
  return request<UpstreamInfo>(`/projects/${encodeURIComponent(projectId)}/upstream`)
}
export function setUpstream(projectId: string, url: string): Promise<UpstreamInfo> {
  return request(`/projects/${encodeURIComponent(projectId)}/upstream`, {
    method: 'PUT',
    body: JSON.stringify({ url }),
  })
}

// 分支保护 (#718): 平台侧的合并规则。GET 附带只读的 merge_method 和
// github_protection；PUT 是 partial-update，body 里出现哪个键就改哪个。
export function getBranchProtection(projectId: string): Promise<BranchProtection> {
  return request<BranchProtection>(`/projects/${encodeURIComponent(projectId)}/branch-protection`)
}
export function setBranchProtection(projectId: string, patch: BranchProtectionPatch): Promise<BranchProtectionRules> {
  return request(`/projects/${encodeURIComponent(projectId)}/branch-protection`, {
    method: 'PUT',
    body: JSON.stringify(patch),
  })
}

export function getForgeConnection(projectId: string): Promise<ForgeConnection> {
  return request(`/projects/${encodeURIComponent(projectId)}/forge`)
}

export function getForgeAttribution(projectId: string): Promise<ForgeAttribution> {
  return request(`/projects/${encodeURIComponent(projectId)}/forge-attribution`)
}

export function setForgeAttribution(projectId: string, requesterCoauthor: boolean | null): Promise<ForgeAttribution> {
  return request(`/projects/${encodeURIComponent(projectId)}/forge-attribution`, {
    method: 'PUT',
    body: JSON.stringify({ requester_coauthor: requesterCoauthor }),
  })
}

// GitHub App install flow (#192).
export function getGithubConnection(projectId: string): Promise<GithubConnection> {
  return request(`/projects/${encodeURIComponent(projectId)}/github/connection`)
}
export function getGithubInstallUrl(projectId: string): Promise<{ url: string }> {
  return request(`/projects/${encodeURIComponent(projectId)}/github/install-url`)
}
// Connect via an existing cheesex-app installation when one already covers the
// upstream repo; {connected:false, install_url} means "go through GitHub".
// (GitHub's install page never fires the callback when the App is already
// installed, so the frontend must try this first.)
export function connectGithubRepo(
  projectId: string
): Promise<{ connected: boolean; repo?: string; account?: string; install_url?: string }> {
  return request(`/projects/${encodeURIComponent(projectId)}/github/connect`, { method: 'POST' })
}
export function getGithubAccountAuthorizeUrl(projectId: string): Promise<{ url: string }> {
  return request(`/users/me/github-account/authorize-url?return_project_id=${encodeURIComponent(projectId)}`)
}

// Personal OAuth/App connections (1.0 router, single `/api` prefix — see
// legacyRequest). Includes every provider the user has linked, not just
// github_app; callers filter by providerId.
export function listOAuthConnections(userId: string): Promise<{ connections: OAuthConnectionInfo[] }> {
  return legacyRequest(`/users/${encodeURIComponent(userId)}/oauth/connections`)
}
// Spends a sudo ticket minted for 'oauth:unbind' (see utils/sudo.ts).
export function deleteOAuthConnection(userId: string, connectionId: number, sudoTicket: string): Promise<void> {
  return legacyRequest(`/users/${encodeURIComponent(userId)}/oauth/connections/${connectionId}`, {
    method: 'DELETE',
    body: JSON.stringify({ sudoTicket }),
  })
}

// The AI-workspace project for a 知是 Team (fusion P4). Null when the team has no
// project yet — the team page uses this to show/hide its 「AI 工作台」 entry.
export function getProjectForTeam(teamId: number): Promise<Project | null> {
  return request<Project | null>(`/projects/by-team/${teamId}`)
}

export function getInbox(projectId: string, targetHandle: string): Promise<ListPayload<InboxItem>> {
  return request<ListPayload<InboxItem>>(
    `/projects/${encodeURIComponent(projectId)}/inbox?target_handle=${encodeURIComponent(targetHandle)}`
  )
}

export function markRead(alertId: number): Promise<InboxItem> {
  return request<InboxItem>(`/alerts/${alertId}/read`, { method: 'POST' })
}

// 拍板。答复之后这一条不再等人，收件箱里就没有它了。
export function resolveAlert(alertId: number, chosen: string): Promise<InboxItem> {
  return request<InboxItem>(`/alerts/${alertId}/resolve`, {
    method: 'POST',
    body: JSON.stringify({ chosen }),
  })
}

export function sendFeedback(alertId: number, feedback: 'up' | 'down'): Promise<InboxItem> {
  return request<InboxItem>(`/alerts/${alertId}/feedback`, {
    method: 'POST',
    body: JSON.stringify({ feedback }),
  })
}

// 机构看板 / Space 看板 (eval F3).
//
// Paging is opt-in on the server: no `limit` returns the WHOLE timeline, which
// is 2.1 MB / 2226 rows on a long topic. The chat panel always passes a limit;
// `has_more` + `oldest_id` walk backwards from there (a cursor, not an offset —
// the tail keeps growing while you read history).
export interface BlockPage extends ListPayload<Block> {
  has_more: boolean
  oldest_id: string | null
}

export function listBlocks(topicId: string, opts?: { limit?: number; before?: string }): Promise<BlockPage> {
  const q = new URLSearchParams()
  if (opts?.limit !== undefined) q.set('limit', String(opts.limit))
  if (opts?.before) q.set('before', opts.before)
  const qs = q.toString()
  const query = qs ? `?${qs}` : ''
  return request<BlockPage>(`/topics/${encodeURIComponent(topicId)}/blocks${query}`)
}

// Emoji reactions (Slack semantics): toggles (emoji, author) on a block and
// returns the block's fresh aggregate. Other clients get the same aggregate
// pushed as a `reaction` WS frame on the topic channel.
export function toggleReaction(
  blockId: string,
  emoji: string,
  author: string
): Promise<{ toggled: 'added' | 'removed'; reactions: ReactionAgg[] }> {
  return request<{ toggled: 'added' | 'removed'; reactions: ReactionAgg[] }>(
    `/blocks/${encodeURIComponent(blockId)}/reactions`,
    { method: 'POST', body: JSON.stringify({ emoji, author }) }
  )
}

// ---- 资料库 ----

// 用户给这个项目的文件，按原名。项目一级，所以一个房间引用得到另一个房间上传的
// 那一份——「上周那份预算表」这句话正是在这种地方说的。
export interface LibraryFile {
  path: string
  bytes: number
  /** Unix seconds; the list comes back newest first. */
  modified: number
}

export function listProjectLibrary(projectId: string): Promise<ListPayload<LibraryFile>> {
  return request<ListPayload<LibraryFile>>(`/projects/${encodeURIComponent(projectId)}/library`)
}

/** 一份资料的字节。这条端点一律按下载发，所以 `downloadFile` 补在末尾的
 *  `download=true` 在这里没有对应的参数，后端不看它。 */
export function libraryFileRawUrl(projectId: string, path: string): string {
  return `${BASE}/projects/${encodeURIComponent(projectId)}/library/raw?path=${encodeURIComponent(path)}`
}

export function deleteLibraryFile(projectId: string, path: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(
    `/projects/${encodeURIComponent(projectId)}/library?path=${encodeURIComponent(path)}`,
    { method: 'DELETE' }
  )
}

// ---- 产物清单 ----

// 这个项目交出去的东西，一项一行。清单由交付长出来，所以这里没有「新建」——
// 能做的三件事都是人的判断：改名、合并、删除。
export interface ProjectArtifact {
  id: string
  name: string
  /** 一句话：这是什么东西、给谁的。交付时写下，没人写过时是空串。 */
  about: string
  /** 交付过几次。0 = 有一张卡正在交付它，但还没有哪一次落地。 */
  version: number
  /** 最近一次交付被采纳的时刻（ISO），一次都还没有时为 null。 */
  delivered_at: string | null
}

export function listProjectArtifacts(projectId: string): Promise<ListPayload<ProjectArtifact>> {
  return request<ListPayload<ProjectArtifact>>(`/projects/${encodeURIComponent(projectId)}/artifacts`)
}

/** 交出去的是什么形态：一份文件、一个地址、一次合并。null = 这一版是交付物落地
 *  之前递的卡，当时没有记，而那份构建产物已经不在了。 */
export type DeliverableKind = 'file' | 'link' | 'merge'

/** 这一项的第 N 版 —— 就是第 N 张采纳了的卡。 */
export interface ArtifactVersion {
  number: number
  card_id: string
  /** 这次交付改了什么（卡上那句 Conventional Commit 标题）。 */
  subject: string | null
  delivered_at: string | null
  decided_by: string | null
  kind: DeliverableKind | null
  filename: string | null
  url: string | null
}

export interface ProjectArtifactDetail extends ProjectArtifact {
  versions: ArtifactVersion[]
}

export interface ArtifactComparison {
  kind: 'file' | 'merge' | 'link' | 'unavailable'
  identical: boolean | null
  note: string | null
  files: {
    path: string
    diff: string | null
    note: string | null
    before_mode?: string | null
    after_mode?: string | null
    status?: string
  }[]
}

export function compareArtifactVersions(
  projectId: string,
  artifactId: string,
  before: string,
  after: string
): Promise<ArtifactComparison> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/compare?before=${encodeURIComponent(before)}&after=${encodeURIComponent(after)}`
  )
}

export async function artifactVersionBytes(
  projectId: string,
  artifactId: string,
  cardId: string,
  asPdf = false
): Promise<ArrayBuffer> {
  const response = await fetch(
    artifactVersionFileUrl(projectId, artifactId, cardId) + (asPdf ? '?preview_pdf=true' : ''),
    { headers: authHeaders() }
  )
  if (response.ok) return response.arrayBuffer()
  let message = ''
  try {
    message = String((await response.json())?.message || '')
  } catch {
    /* Keep the HTTP error when the server sent no JSON. */
  }
  throw new Error(message || `未能读取这一版（HTTP ${response.status}）`)
}

export function getProjectArtifact(projectId: string, artifactId: string): Promise<ProjectArtifactDetail> {
  return request<ProjectArtifactDetail>(
    `/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}`
  )
}

/** 这一版当时交出去的那一份字节。取的是快照，不是现在重建一次的结果。 */
export function artifactVersionFileUrl(projectId: string, artifactId: string, cardId: string): string {
  return (
    `${BASE}/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}` +
    `/versions/${encodeURIComponent(cardId)}/file`
  )
}

/** 换个名字。卡指着的是这一项的 id，所以之前的交付照样算它的版本。 */
export function renameProjectArtifact(
  projectId: string,
  artifactId: string,
  name: string
): Promise<{ id: string; name: string }> {
  return request<{ id: string; name: string }>(
    `/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}`,
    { method: 'PATCH', body: JSON.stringify({ name }) }
  )
}

/** 这两项其实是同一个东西：把 `artifactId` 的交付都算到 `into` 上，它自己没了。 */
export function mergeProjectArtifacts(
  projectId: string,
  artifactId: string,
  into: string
): Promise<{ id: string; name: string }> {
  return request<{ id: string; name: string }>(
    `/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}/merge`,
    { method: 'POST', body: JSON.stringify({ into }) }
  )
}

export function deleteProjectArtifact(projectId: string, artifactId: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(
    `/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}`,
    { method: 'DELETE' }
  )
}

// ---- 这个房间里摆出来的东西 (#1085 结论四) ----

// 摆出来的东西属于这个房间：用户看完拿走就完了。要把它留下来以后还用，由人按
// 「保存到资料库」——留着要用的东西是资料；交出去的东西走交付，那才上产物清单。
export interface RoomOutput {
  path: string
  mime: string
  kind: 'file' | 'app'
  shown_at: string
}

export function listRoomOutputs(topicId: string): Promise<ListPayload<RoomOutput>> {
  return request<ListPayload<RoomOutput>>(`/topics/${encodeURIComponent(topicId)}/shown`)
}

/** 把房间里的这一份留进资料库：按原名，撞名加 `(2)`，所有房间都引用得到。 */
export function saveRoomOutputToLibrary(topicId: string, path: string): Promise<{ name: string }> {
  return request(`/topics/${encodeURIComponent(topicId)}/shown/save`, {
    method: 'POST',
    body: JSON.stringify({ path }),
  })
}

// ---- Chat attachments ----

/** 把资料库里已有的一份文件附在这条消息上。返回的形状和一次上传相同。 */
export async function attachLibraryFile(topicId: string, libraryPath: string): Promise<ChatAttachment> {
  const form = new FormData()
  form.append('library_path', libraryPath)
  const res = await fetch(`${BASE}/topics/${encodeURIComponent(topicId)}/attachments`, {
    method: 'POST',
    body: form,
    headers: authHeaders(),
  })
  const envelope = (await res.json().catch(() => null)) as ApiEnvelope<ChatAttachment> | null
  if (!res.ok || !envelope || envelope.code !== 200) {
    throw new Error(envelope?.message || `添加失败（HTTP ${res.status}）`)
  }
  return envelope.data
}

// Upload a file into the project's 资料库, with a copy in this room. NOTE: raw
// fetch, not request() — multipart needs the browser to set the boundary itself.
//
// `origin: 'clipboard'` 的那一份只留在这个房间：贴进来的截图没有名字（`image.png`
// 是浏览器编的），而资料库是按名字寻址的。
export async function uploadAttachment(
  topicId: string,
  file: File,
  origin: 'file' | 'clipboard' = 'file'
): Promise<ChatAttachment> {
  const form = new FormData()
  form.append('file', file)
  form.append('origin', origin)
  const res = await fetch(`${BASE}/topics/${encodeURIComponent(topicId)}/attachments`, {
    method: 'POST',
    body: form,
    headers: authHeaders(),
  })
  const envelope = (await res.json().catch(() => null)) as ApiEnvelope<ChatAttachment> | null
  if (!res.ok || !envelope || envelope.code !== 200) {
    throw new Error(envelope?.message || `上传失败（HTTP ${res.status}）`)
  }
  return envelope.data
}

// <img src=…> URL for an uploaded attachment (binary raw endpoint).
//
// 注意这不是一个可以挂在 <img src> 上的地址：raw 端点从 Authorization 头认人，
// 而浏览器发图片请求时带不了头（也读不到 localStorage）。挂上去的结果是 401，
// 读者看到的是一张裂图。要显示图片用下面的 attachmentImageUrl。
/** `task` 说的是从哪个库读：某个任务工作树上的那一份，还是房间自己的文件（不传）。
 *  同一个路径在两个库里可以是两份不同的文件，所以看谁的文件必须说出来。 */
export function attachmentRawUrl(
  topicId: string,
  path: string,
  task?: string | null,
  source: FileSource = 'live'
): string {
  const from = task ? `&task=${encodeURIComponent(task)}` : ''
  return `${BASE}/topics/${encodeURIComponent(topicId)}/attachments/raw?path=${encodeURIComponent(path)}${from}&source=${source}`
}

/** 图片附件的字节，取回来做成 <img> 能用的 object URL。
 *
 * 先 fetch 再转 URL 不是为了多走一步，是因为只有 fetch 才能带上 Authorization：
 * 这个端点不接受匿名请求，而 `<img src="/api/…/attachments/raw?path=…">` 恰恰
 * 是匿名的。调用方负责在不再需要时 URL.revokeObjectURL（见 AttachmentImage）。
 */
export async function attachmentImageUrl(topicId: string, path: string): Promise<string> {
  const res = await fetch(attachmentRawUrl(topicId, path), { headers: authHeaders() })
  if (!res.ok) throw new Error(`图片加载失败（HTTP ${res.status}）`)
  return URL.createObjectURL(await res.blob())
}

/** A published file's bytes, for a viewer that draws them in the page. */
export async function previewFileBytes(
  topicId: string,
  path: string,
  task?: string | null,
  source: FileSource = 'live'
): Promise<ArrayBuffer> {
  // `download=true` is what makes the raw endpoint serve a non-image at all; it
  // only changes the Content-Disposition, which nothing here reads.
  const res = await fetch(`${attachmentRawUrl(topicId, path, task, source)}&download=true`, {
    headers: authHeaders(),
  })
  if (!res.ok) throw new Error(`读取文件失败（HTTP ${res.status}）`)
  return res.arrayBuffer()
}

/** 一份 .docx 里的修订，按读者读到的顺序。
 *
 *  旁边那份 PDF 已经把改动画出来了（LibreOffice 会渲染修订：插入带下划线、删除带
 *  删除线）。这份清单不是为了让人看见改动，是为了让人**处理**改动——不打开 Word 就能
 *  逐条接受或拒绝。 */
export function documentRevisions(
  topicId: string,
  path: string,
  task?: string | null,
  source: FileSource = 'live'
): Promise<{ path: string; version: string; revisions: DocumentRevision[] }> {
  const query = `?path=${encodeURIComponent(path)}&source=${source}` + (task ? `&task=${encodeURIComponent(task)}` : '')
  return request<{ path: string; version: string; revisions: DocumentRevision[] }>(
    `/topics/${encodeURIComponent(topicId)}/documents/revisions${query}`
  )
}

/** 接受或拒绝其中几处，写回文件，返回剩下的那些。
 *
 *  序号对应的是调用方刚拿到的那份清单。处理完之后剩下的会重新从 1 数起，所以调用方
 *  要用返回的这份清单替换手上那份，不能接着用旧序号。
 *
 *  `version` 是读这份清单时那份文件的版本。芝士在这中间重新交付过这个文件时，这次处理
 *  会被拒绝而不是把新的那份盖掉。 */
export function decideDocumentRevisions(
  topicId: string,
  path: string,
  version: string,
  decision: { accept?: number[]; reject?: number[] },
  task?: string | null
): Promise<{ path: string; version: string; revisions: DocumentRevision[] }> {
  return request<{ path: string; version: string; revisions: DocumentRevision[] }>(
    `/topics/${encodeURIComponent(topicId)}/documents/revisions`,
    { method: 'POST', body: JSON.stringify({ path, version, task: task ?? undefined, ...decision }) }
  )
}

/** Raised when the deployment has no document renderer, as opposed to when this
 *  particular file cannot be converted. The panel says a different thing for
 *  each: one is about the deployment and one is about the file. */
export class PreviewRendererUnavailable extends Error {}

/** A Word or PowerPoint file converted to PDF, so a browser can draw it. */
export async function previewDocumentPdf(
  topicId: string,
  path: string,
  task?: string | null,
  source: FileSource = 'live'
): Promise<ArrayBuffer> {
  const url =
    `${BASE}/topics/${encodeURIComponent(topicId)}/attachments/pdf` +
    `?path=${encodeURIComponent(path)}&source=${source}` +
    (task ? `&task=${encodeURIComponent(task)}` : '')
  const res = await fetch(url, { headers: authHeaders() })
  if (res.ok) return res.arrayBuffer()
  let message = ''
  try {
    message = String((await res.json())?.message || '')
  } catch {
    message = ''
  }
  if (res.status === 503) {
    throw new PreviewRendererUnavailable(message || '这个部署没有启用文档预览')
  }
  throw new Error(message || `无法生成预览（HTTP ${res.status}）`)
}

// Downloads carry the same credentials as API requests, including token-only sessions.
export async function downloadFile(rawUrl: string, filename: string): Promise<void> {
  const res = await fetch(`${rawUrl}${rawUrl.includes('?') ? '&' : '?'}download=true`, { headers: authHeaders() })
  if (!res.ok) throw new Error(`下载失败（HTTP ${res.status}）`)
  const url = URL.createObjectURL(await res.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// Living-doc helpers (spec §2.2 docs-out/docs-in). `content` is markdown.
// GET returns the doc Block, or null when the topic has no doc yet.
export function getDoc(topicId: string): Promise<Block | null> {
  return request<Block | null>(`/topics/${encodeURIComponent(topicId)}/doc`)
}

// 进度层 (#187): 芝士's checklist as of the last turn that touched this topic.
// Read on topic open — between turns there is no WS stream to carry it, and
// "做到哪了" has to be visible without summoning anyone. `items` is [] for a
// topic that never had a checklist.
export function getProgress(topicId: string): Promise<TopicProgress> {
  return request<TopicProgress>(`/topics/${encodeURIComponent(topicId)}/progress`)
}

// PUT upserts the living doc and appends a "📝 编辑了文档" event to the
// conversation. Returns the doc Block.
//
// `expectedVersion` is the doc_version this edit is based on (0 = "there is no
// doc yet"). The doc is only ever written whole, so the write is conditional on
// it: if 芝士 set the doc in between, the backend answers 409 instead of letting
// this save erase what it wrote.
export function putDoc(topicId: string, content: string, author: string, expectedVersion: number): Promise<Block> {
  return request<Block>(`/topics/${encodeURIComponent(topicId)}/doc`, {
    method: 'PUT',
    body: JSON.stringify({ content, author, expected_version: expectedVersion }),
  })
}

// B1: the living doc's structured node tree (heading/paragraph/list/…), in order.
// Each node has a stable id + the turn_id that produced it — used for cross-view
// highlight (B1 P2) and comment anchoring (B4).
export function getDocNodes(topicId: string): Promise<{ data: Block[]; total: number }> {
  return request(`/topics/${encodeURIComponent(topicId)}/docs`)
}

// 段落评论 (eval B4): inline comments, each anchored to a doc node via reply_to.
export function getComments(topicId: string): Promise<{ data: Block[]; total: number }> {
  return request(`/topics/${encodeURIComponent(topicId)}/comments`)
}

export function addComment(
  topicId: string,
  content: string,
  author: string,
  anchor?: string,
  quote?: string
): Promise<Block> {
  return request<Block>(`/topics/${encodeURIComponent(topicId)}/comments`, {
    method: 'POST',
    body: JSON.stringify({ content, author, anchor, quote }),
  })
}

// 决策记录 (spec §7.1): the project's decision log. Each entry is a Block whose
// `topic_id` points back to the source topic where the decision was made.
export function getProjectDecisions(projectId: string): Promise<ListPayload<Block>> {
  return request<ListPayload<Block>>(`/projects/${encodeURIComponent(projectId)}/decisions`)
}

// 周报集 (spec §7.1): the project's weekly reports, newest first. Each Block
// carries the stretch it covers in `meta` (`since`/`until`) and points back to
// the room it was written in via `topic_id`.
export function getProjectWeeklies(projectId: string): Promise<ListPayload<Block>> {
  return request<ListPayload<Block>>(`/projects/${encodeURIComponent(projectId)}/weeklies`)
}

// 选项问题 (cheese ask): one-click answer.
export function answerOptions(blockId: string, option: string, author: string): Promise<Block> {
  return request<Block>(`/topics/blocks/${encodeURIComponent(blockId)}/answer`, {
    method: 'POST',
    body: JSON.stringify({ option, author }),
  })
}

// 忘了 @ 的补救：叫芝士现在就读它还没读到的消息。不发新消息 —— 那条消息已经
// 在时间线上了，补一条一模一样的只会让人分不清哪条是真的。
// `started` 为 false 时说明这一下没必要（房间已经在干活，或者没有待读的东西）。
export function summonAgent(topicId: string): Promise<{ started: boolean; reason?: string }> {
  return request<{ started: boolean; reason?: string }>(`/topics/${encodeURIComponent(topicId)}/summon`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

// ---- 记忆 (spec §8.4: 记忆可见) ----
export interface MemoryEntryOut {
  id: string
  scope: string
  scope_id: string
  content: string
  created_at: string
}
export function listMemory(projectId: string, userHandle?: string): Promise<ListPayload<MemoryEntryOut>> {
  const u = userHandle ? `&user_handle=${encodeURIComponent(userHandle)}` : ''
  return request<ListPayload<MemoryEntryOut>>(`/memory?project_id=${encodeURIComponent(projectId)}${u}`)
}
export function deleteMemory(entryId: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/memory/${encodeURIComponent(entryId)}`, {
    method: 'DELETE',
  })
}

// ---- 执行面板 (Phase 4 tool drawers) ----

// 现场 (施工现场): a topic's tool/event record (read-only), newest window first.
// Paged: events are the most numerous kind of block (one per tool call), so an
// unpaged 现场 is the largest request the app can make and it only grows.
export const SITE_PAGE_SIZE = 120
export function getTranscript(
  topicId: string,
  opts: { limit?: number; before?: string } = {}
): Promise<ListPayload<Block> & { has_more?: boolean; oldest_id?: string | null }> {
  const q = new URLSearchParams()
  if (opts.limit != null) q.set('limit', String(opts.limit))
  if (opts.before) q.set('before', opts.before)
  const qs = q.toString()
  return request<ListPayload<Block> & { has_more?: boolean; oldest_id?: string | null }>(
    `/topics/${encodeURIComponent(topicId)}/transcript${qs ? `?${qs}` : ''}`
  )
}

// 现场实时终端 (施工现场): whether this topic has a live pane to watch, and the
// screen WebSocket ("/connector/session/{sid}/screen") the 现场 renders with
// DeviceLiveViewer. A turn runs on a machine we reach only over that link.
export interface TerminalInfo {
  available: boolean
  ws?: string
}
export function getTerminal(topicId: string): Promise<TerminalInfo> {
  return request<TerminalInfo>(`/topics/${encodeURIComponent(topicId)}/terminal`)
}

export interface PreviewSession {
  url: string
  grant: string
}

export function requestPreviewSession(topicId: string): Promise<PreviewSession> {
  return request<PreviewSession>(`/topics/${encodeURIComponent(topicId)}/preview-session`, { method: 'POST' })
}

export interface AgentControlResult {
  request_id: string
  status: string
  result: { response: { subtype: string; error?: string; response?: Record<string, unknown> } } | null
}

export type { AgentControlRequest, AgentControlState }

export function getAgentControl(topicId: string) {
  return request<AgentControlState>(`/topics/${encodeURIComponent(topicId)}/agent/control`)
}

export function getAgentControlResult(topicId: string, sessionId: string, requestId: string) {
  return request<{ result: AgentControlResult['result']; status: string }>(
    `/topics/${encodeURIComponent(topicId)}/agent/control/${encodeURIComponent(requestId)}?session_id=${encodeURIComponent(sessionId)}`
  )
}

export function sendAgentControl(
  topicId: string,
  sessionId: string,
  control: Record<string, unknown>,
  requestId = crypto.randomUUID()
) {
  return request<AgentControlResult>(`/topics/${encodeURIComponent(topicId)}/agent/control`, {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, request_id: requestId, request: control }),
  })
}

export function answerAgentControl(
  topicId: string,
  sessionId: string,
  requestId: string,
  response: Record<string, unknown>
) {
  return request<{ status: string }>(`/topics/${encodeURIComponent(topicId)}/agent/answer`, {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, request_id: requestId, response }),
  })
}

export function getGitLog(
  projectId: string,
  topicId?: string | null,
  taskId?: string | null
): Promise<ListPayload<GitCommit>> {
  const t = `?${new URLSearchParams({ ...(topicId ? { topic: topicId } : {}), ...(taskId ? { task: taskId } : {}) })}`
  return request<ListPayload<GitCommit>>(`/projects/${encodeURIComponent(projectId)}/git/log${t}`)
}

export function getGitDiff(
  projectId: string,
  topicId?: string | null,
  taskId?: string | null,
  source: FileSource = 'committed'
): Promise<{ diff: string }> {
  const t = `?${new URLSearchParams({ source, ...(topicId ? { topic: topicId } : {}), ...(taskId ? { task: taskId } : {}) })}`
  return request<{ diff: string }>(`/projects/${encodeURIComponent(projectId)}/git/diff${t}`)
}

// 工作面板 asks this while its tabs are CLOSED: which of them have anything to
// show, and what count belongs on 改动. Both are facts about tabs nobody is
// looking at, so neither may cost what opening the tab costs.
export function getTopicWorkSummary(projectId: string, topicId: string): Promise<TopicWorkSummary> {
  const p = encodeURIComponent(projectId)
  return request<TopicWorkSummary>(`/projects/${p}/topics/${encodeURIComponent(topicId)}/work-summary`)
}

// 文件: list workspace files; read one file's content.
export function listFiles(
  projectId: string,
  topicId?: string | null,
  taskId?: string | null,
  source: FileSource = 'live'
): Promise<ListPayload<WorkspaceFile> & { source: FileSource }> {
  const t = `?${new URLSearchParams({ source, ...(topicId ? { topic: topicId } : {}), ...(taskId ? { task: taskId } : {}) })}`
  return request<ListPayload<WorkspaceFile> & { source: FileSource }>(
    `/projects/${encodeURIComponent(projectId)}/files${t}`
  )
}

// <img src=…> URL for a workspace file (binary raw endpoint) — the 文件 panel
// shows images as images instead of Monaco-mangled bytes.
export function workspaceFileRawUrl(
  projectId: string,
  path: string,
  topicId?: string,
  taskId?: string | null,
  source: FileSource = 'live'
): string {
  const t = `&${new URLSearchParams({ source, ...(topicId ? { topic: topicId } : {}), ...(taskId ? { task: taskId } : {}) })}`
  return `${BASE}/projects/${encodeURIComponent(projectId)}/file/raw?path=${encodeURIComponent(path)}${t}`
}

export function readFile(
  projectId: string,
  path: string,
  topicId?: string | null,
  taskId?: string | null,
  source: FileSource = 'live'
): Promise<FileContent> {
  const t = `&${new URLSearchParams({ source, ...(topicId ? { topic: topicId } : {}), ...(taskId ? { task: taskId } : {}) })}`
  return request<FileContent>(`/projects/${encodeURIComponent(projectId)}/file?path=${encodeURIComponent(path)}${t}`)
}

// Save an edited workspace file (人改文件即指令). The agent reads the latest on
// its next turn, like 改文档即指令.
//
// `version` is the one readFile returned. Sending it makes the write
// conditional: if 芝士 wrote the same file in between, the backend answers 409
// instead of letting this save erase their edits without a trace.
export function writeFile(
  projectId: string,
  path: string,
  content: string,
  topicId?: string | null,
  version?: string | null,
  taskId?: string | null
): Promise<{ path: string; version: string }> {
  const t = `?${new URLSearchParams({ ...(topicId ? { topic: topicId } : {}), ...(taskId ? { task: taskId } : {}) })}`
  return request(`/projects/${encodeURIComponent(projectId)}/file${t}`, {
    method: 'PUT',
    body: JSON.stringify({ path, content, version: version ?? null }),
  })
}

// 预览 (spec §9.1): the artifact 芝士 pointed at as the topic's current preview,
// or null if none is set. Content is fetched separately via readFile.
export function getPreview(topicId: string): Promise<PreviewInfo | null> {
  return request<PreviewInfo | null>(`/topics/${encodeURIComponent(topicId)}/preview`)
}

/** 预览正在显示的那份文件，或者房间里指名的某一份。
 *
 *  消息里的 `<&路径>` 只是一个路径，不带它在哪个库。房间自己的文件不在任何分支上，
 *  所以按路径读这里，是从那枚 chip 走到那份文件的唯一一条路。 */
export function readPreviewFile(topicId: string, path?: string): Promise<FileContent> {
  const query = path ? `?path=${encodeURIComponent(path)}` : ''
  return request<FileContent>(`/topics/${encodeURIComponent(topicId)}/preview/file${query}`)
}

// 资源: aggregated token/cost usage for a topic and for the whole project.
export function getTopicUsage(topicId: string): Promise<UsageStats> {
  return request<UsageStats>(`/topics/${encodeURIComponent(topicId)}/usage`)
}

export function getProjectUsage(projectId: string): Promise<UsageStats> {
  return request<UsageStats>(`/projects/${encodeURIComponent(projectId)}/usage`)
}

// 内测版本徽标: the running backend build. `badge` is the box's opt-in flag;
// `short` is the 7-char sha to show. Public, unauthenticated.
export interface AppVersion {
  sha: string
  short: string
  badge: boolean
}

export function getAppVersion(): Promise<AppVersion> {
  return request<AppVersion>('/version')
}

// ---- 采纳卡 / 验收 (eval C5/A3) ----

// Accept cards for a topic, newest first.
export function getAcceptCards(topicId: string, taskId?: string | null): Promise<ListPayload<AcceptCard>> {
  return request<ListPayload<AcceptCard>>(
    `/topics/${encodeURIComponent(topicId)}/accept-card${taskId ? `?task=${encodeURIComponent(taskId)}` : ''}`
  )
}

// 采纳 PR 化 (#188 §5.1): live CI state of the newest card's PR. Safe to poll —
// answers {available:false} when the topic has no PR-riding card.
export function getPrChecks(topicId: string, taskId?: string | null): Promise<PrChecks> {
  return request<PrChecks>(
    `/topics/${encodeURIComponent(topicId)}/pr-checks${taskId ? `?task=${encodeURIComponent(taskId)}` : ''}`
  )
}

/** 这张卡交出去的那一份字节。快照在递卡那一刻就落下来了，所以人点采纳之前就取得
 *  到——他要审的正是这一份。 */
export function cardDeliverableUrl(cardId: string): string {
  return `${BASE}/accept-cards/${encodeURIComponent(cardId)}/deliverable`
}

// 合的是人看到的那个 commit：会触发合并的三个入口（采纳 / 人工放行 / 布防）都
// 带上卡片渲染时 `merge_state.head_sha` 的值。轮询器每分钟把卡刷到 PR 的新
// head，屏幕上那份不会自己变——不声明看的是哪一版，点下去合的就可能是一段没人
// 看过的代码。后端拿它和卡当前的 head 对，不一致就 422 让人重新看过。
// null 是合法值：卡还没被镜像过 head，或者根本不骑 PR（平台 lane）。
export function acceptCard(cardId: string, decidedBy: string, headSha: string | null): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/accept`, {
    method: 'POST',
    body: JSON.stringify({ decided_by: decidedBy, head_sha: headSha }),
  })
}

export function rejectCard(cardId: string, decidedBy: string, note: string): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/reject`, {
    method: 'POST',
    body: JSON.stringify({ decided_by: decidedBy, note }),
  })
}

// 撤回采纳 (spec §6.3: 采纳可撤销). Revoke an accepted card → un-archives the
// topic. Only the accepter / owner / lead may revoke (enforced server-side).
export function revokeCard(cardId: string, decidedBy: string): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ decided_by: decidedBy }),
  })
}

// 人工放行 (#718): merge a pending card's PR even though its merge state is
// not clean. The platform never does this on its own —红着合有时候是对的，
// 不能接受的是没有人做过这个决定。So the actor is taken from the session
// server-side (never the body) and the card records who / when / what the
// checks said / why. Only the project's override list (owner/lead when
// unconfigured) may call it, and 芝士 is refused outright.
export function mergeCardAnyway(cardId: string, reason: string, headSha: string | null): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/merge-anyway`, {
    method: 'POST',
    body: JSON.stringify({ reason, head_sha: headSha }),
  })
}

// 绿了自动合 (#718): arm/disarm auto-merge on a pending card. Reviewer-side
// switch, only meaningful on a project with auto_merge_allowed; the actor is
// the session user server-side, and 新提交作废采纳 disarms it again.
export function setAutoMerge(cardId: string, enabled: boolean, headSha: string | null): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/auto-merge`, {
    method: 'POST',
    body: JSON.stringify({ enabled, head_sha: headSha }),
  })
}

// 改验收人 (spec §4.4: 任何成员都可以改推荐/加人). Reassign a pending card to
// another reviewer. The backend reuses the create schema, so we pass an empty
// routing_reason to keep the recommendation neutral on a manual reassign.
export function reassignCard(cardId: string, reviewerHandle: string): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/reassign`, {
    method: 'POST',
    body: JSON.stringify({
      reviewer_handle: reviewerHandle,
      routing_reason: '',
    }),
  })
}

// 主分支保护 (spec §4.4): record one approval toward the card's accept. The
// backend enforces "AI 不能投票" and per-person uniqueness.
export function approveCard(cardId: string, approverHandle: string): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/approve`, {
    method: 'POST',
    body: JSON.stringify({ approver_handle: approverHandle }),
  })
}

// 项目成员列表 (used by the 改验收人 menu). Returns {data:[{user_handle, role}]}.
export function listProjectMembers(projectId: string): Promise<ListPayload<ProjectMemberRow>> {
  return request<ListPayload<ProjectMemberRow>>(`/projects/${encodeURIComponent(projectId)}/members`)
}

// 项目成员的增 / 改角色 / 移出。后端在服务层就把「只有 owner / lead 能写」这条
// 授权做掉了（membership/services.py），并且**不认**请求体里自称的 handle —— 身份
// 从 token 解析。所以这里不传 actor：传了也不会被信，反而读起来像是能伪造。
export function addProjectMember(projectId: string, handle: string, role: string): Promise<ProjectMemberRow> {
  return request<ProjectMemberRow>(`/projects/${encodeURIComponent(projectId)}/members`, {
    method: 'POST',
    body: JSON.stringify({ user_handle: handle, role }),
  })
}

export function updateProjectMemberRole(projectId: string, handle: string, role: string): Promise<ProjectMemberRow> {
  return request<ProjectMemberRow>(`/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(handle)}`, {
    method: 'PUT',
    body: JSON.stringify({ role }),
  })
}

export function removeProjectMember(projectId: string, handle: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(
    `/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(handle)}`,
    { method: 'DELETE' }
  )
}

// 自己退出项目。路径是 `/membership` 而不是 `/members/me`：`/members/{handle}` 那条
// 路由先注册，`me` 到了那里就是一个人的名字。同样不传 handle —— 退的恒是当前身份
// 那个人，后端没有代退的入口（membership/services.py 的 `leave`）。
export function leaveProject(projectId: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/projects/${encodeURIComponent(projectId)}/membership`, {
    method: 'DELETE',
  })
}

// 转让项目给名册上的另一个人。所有者自己退不掉（后端会拒，得先转让），这是他
// 离得开的那条路的第一步。新所有者必须已经在名册上（后端会验，不在就退回来一句
// 「请先把 TA 加进项目成员」）；他接手之后，原所有者就只剩成员身份，再退出一次
// 才真的走（membership/services.py 的 leave）。
export function setProjectOwner(projectId: string, ownerHandle: string): Promise<Project> {
  return request<Project>(`/projects/${encodeURIComponent(projectId)}/owner`, {
    method: 'PUT',
    body: JSON.stringify({ owner_handle: ownerHandle }),
  })
}

// ---- 邀请：加人这件事要两个人同意 --------------------------------------------
// 进了项目就看得见这个项目的全部话题，那是别人的工作内容，所以从界面上加人得由
// 被加的那个人点头。`addProjectMember` 那条路仍然在，它是接受之后真正把人放上名册
// 的那一步，也是脚本用的原语——界面上走的是这里。

export function inviteProjectMember(projectId: string, handle: string, role: string): Promise<ProjectInvitation> {
  return request<ProjectInvitation>(`/projects/${encodeURIComponent(projectId)}/invitations`, {
    method: 'POST',
    body: JSON.stringify({ user_handle: handle, role }),
  })
}

export function listProjectInvitations(projectId: string): Promise<ListPayload<ProjectInvitation>> {
  return request<ListPayload<ProjectInvitation>>(`/projects/${encodeURIComponent(projectId)}/invitations`)
}

// 等我答复的邀请。没有 project 那一层是刻意的：被邀请的人还不在那个项目里，一个
// 项目作用域的接口他根本够不着。
export function listMyInvitations(): Promise<ListPayload<ProjectInvitation>> {
  return request<ListPayload<ProjectInvitation>>('/me/invitations')
}

export function respondToInvitation(invitationId: string, accept: boolean): Promise<ProjectInvitation> {
  return request<ProjectInvitation>(`/invitations/${encodeURIComponent(invitationId)}/respond`, {
    method: 'POST',
    body: JSON.stringify({ accept }),
  })
}

export function revokeInvitation(invitationId: string): Promise<ProjectInvitation> {
  return request<ProjectInvitation>(`/invitations/${encodeURIComponent(invitationId)}`, {
    method: 'DELETE',
  })
}

// ---- 话题成员名册 (群聊房间的地基, fusion-design §3) --------------------------
// Roster of a topic's group room. `actor` is the acting user's handle — no auth
// layer yet (agent-as-user is P1), so the backend authorizes mutations against
// the actor's topic role (owner/admin may manage the roster).

export function listTopicMembers(topicId: string): Promise<ListPayload<TopicMemberRow>> {
  return roomRead<ListPayload<TopicMemberRow>>(`/topics/${encodeURIComponent(topicId)}/members`)
}

export function addTopicMember(topicId: string, handle: string, role: string, actor: string): Promise<TopicMemberRow> {
  return request<TopicMemberRow>(`/topics/${encodeURIComponent(topicId)}/members`, {
    method: 'POST',
    body: JSON.stringify({ handle, role, actor }),
  })
}

export function updateTopicMemberRole(
  topicId: string,
  handle: string,
  role: string,
  actor: string
): Promise<TopicMemberRow> {
  return request<TopicMemberRow>(`/topics/${encodeURIComponent(topicId)}/members/${encodeURIComponent(handle)}`, {
    method: 'PUT',
    body: JSON.stringify({ role, actor }),
  })
}

export function removeTopicMember(topicId: string, handle: string, actor: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(
    `/topics/${encodeURIComponent(topicId)}/members/${encodeURIComponent(handle)}?actor=${encodeURIComponent(actor)}`,
    { method: 'DELETE' }
  )
}

// 一个 id 指向一个房间。**卡不是地点**：拿卡的 id 问这条接口是 404，卡走
// `getRoomTask`（房间的地址 + 卡的 id）。
export function getTopic(topicId: string): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}`)
}

/** 一张卡，连着它自己的对话。`limit` 只截对话，卡本身照常整份回来。 */
export function getRoomTask(
  roomId: string,
  taskId: string,
  opts?: { limit?: number }
): Promise<RoomTask & { blocks: Block[] }> {
  const q = new URLSearchParams()
  if (opts?.limit != null) q.set('limit', String(opts.limit))
  const query = q.toString() ? `?${q.toString()}` : ''
  return request<RoomTask & { blocks: Block[] }>(
    `/topics/${encodeURIComponent(roomId)}/tasks/${encodeURIComponent(taskId)}${query}`
  )
}

/** 在一张卡下面说话。落在这条活的时间线上，房间被叫来转达 —— 做这条活的分身住在
 *  房间的会话里，只有房间的芝士递得到话。 */
export function sayOnRoomTask(roomId: string, taskId: string, content: string, author: string): Promise<Block> {
  return request<Block>(`/topics/${encodeURIComponent(roomId)}/tasks/${encodeURIComponent(taskId)}/messages`, {
    method: 'POST',
    body: JSON.stringify({ content, author }),
  })
}

// ---- 日历 / 里程碑 (§7.2) ----

// Upcoming milestones (already sorted by due date).
export function getCalendar(projectId: string): Promise<ListPayload<MilestoneFull>> {
  return request<ListPayload<MilestoneFull>>(`/projects/${encodeURIComponent(projectId)}/calendar`)
}

// All milestones (any status), for showing done ones faded.
export function listMilestones(projectId: string): Promise<ListPayload<MilestoneFull>> {
  return request<ListPayload<MilestoneFull>>(`/projects/${encodeURIComponent(projectId)}/milestones`)
}

// ---- 反馈 (feedback) ----
//
// 平台级的收件箱，前缀是 `/feedback`，**不在任何项目或话题下面**（理由见
// `backend/app/api/routes/feedback.py`）。只有提案卡那三个函数挂在话题上 ——
// 提案是一句在某话题里说的话，配额和鉴权都挂在那一边。
//
// 路由按「新模块」写：`/feedback/meta`、`/feedback/counts`、`/feedback/mine` 这类
// 固定段在 `/{feedback_id}` 之前注册，所以不会被当成一个 uuid 吃掉。

/** 查询串拼装。空值一律不出现 —— 发 `?q=` 和发 `?q` 对 FastAPI 的 `str | None`
 *  是两件事（后者才是「没给」）。 */
function feedbackQuery(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue
    search.set(key, String(value))
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

/** 词表：有哪些状态、按什么顺序流动、我是不是管理员。 */
export function getFeedbackMeta(): Promise<FeedbackMeta> {
  return request<FeedbackMeta>('/feedback/meta')
}

/** 铃铛和 Tab 上的数字。列表接口也带一份，但铃铛不该为了一个整数拉一整页。 */
export function getFeedbackCounts(): Promise<FeedbackCounts> {
  return request<FeedbackCounts>('/feedback/counts')
}

/** 把未读游标推到此刻。 */
export function markFeedbackRead(): Promise<{ last_read_at: string }> {
  return request<{ last_read_at: string }>('/feedback/read', { method: 'POST' })
}

export interface FeedbackListQuery {
  /** 一页几条。**必填**，不是有默认值的可选项：列表的翻页边界是拿「手上这一页满没
   *  满」算的（`stores/feedback.ts` 的 `adminHasNext`），而后端的默认值是 20 ——
   *  漏传时两边对同一个问题的答案不一样，症状是每一页都短一截、而且第二页永远取不到，
   *  一点都不像报错。 */
  pageSize: number
  tab?: string
  q?: string
  sort?: string
  pageStart?: number
  author?: string | null
  kind?: string | null
  status?: string | null
  /** 起始时间（ISO）。**两条列表都吃**：管理端是「点看板上一个数字，看那一段」，
   *  反馈中心是「最近 24 小时 / 7 天 / 30 天」那个下拉。客户端只负责把「最近 N 天」
   *  折成一个时刻，窗口的对齐由服务端那套 UTC 日说。 */
  since?: string | null
  resolvedSince?: string
  deployedSince?: string
}

/** 公开列表。`tab` 不认识时后端回 400 而不是悄悄退回 `all` —— 猜错栏位会让人
 *  以为「这条反馈不见了」。所以调用方传的 tab 必须来自 `getFeedbackMeta().tabs`。 */
export function listFeedback(query: FeedbackListQuery): Promise<FeedbackListPayload> {
  return request<FeedbackListPayload>(
    `/feedback${feedbackQuery({
      tab: query.tab,
      q: query.q,
      sort: query.sort,
      page_start: query.pageStart,
      page_size: query.pageSize,
      // 四个筛选。空值由 `feedbackQuery` 丢掉，所以「不限」就是不传。
      author: query.author,
      kind: query.kind,
      status: query.status,
      since: query.since,
    })}`
  )
}

/** 「我的反馈」：我提的 + 我替谁提的 + 指派给我的。访客拿空列表，不是 401。 */
export function listMyFeedback(query: FeedbackListQuery): Promise<FeedbackListPayload> {
  return request<FeedbackListPayload>(
    `/feedback/mine${feedbackQuery({ page_start: query.pageStart, page_size: query.pageSize })}`
  )
}

export function getFeedback(feedbackId: string): Promise<FeedbackDetail> {
  return request<FeedbackDetail>(`/feedback/${encodeURIComponent(feedbackId)}`)
}

/** 一页评论。不给 `parentId` 是顶层评论那一页（一页若干栋楼，每栋跟着它的前若干条
 *  回复走），给了就是**那一栋楼里**从 `after` 往后的一段回复。
 *
 *  两个取法共用一条路由、一套游标，客户端不记第二种形状。`after` 是服务端发出来的
 *  **不透明**串，原样带回来 —— 自己拼一个（「最后一条的时间戳 + id」）拼得出来，
 *  但那是把服务端的排序规则抄了第二份，改排序的那天两边会漂开，症状是翻页漏行。
 *
 *  `next_cursor` 为 null 表示这一层取完了（顶层评论取完了 / 这栋楼取完了）。 */
export function listFeedbackComments(
  feedbackId: string,
  opts: { after?: string | null; parentId?: string | null } = {}
): Promise<{ items: FeedbackComment[]; next_cursor: string | null }> {
  const params = new URLSearchParams()
  if (opts.after) params.set('after', opts.after)
  if (opts.parentId) params.set('parent_id', opts.parentId)
  const query = params.toString()
  return request<{ items: FeedbackComment[]; next_cursor: string | null }>(
    `/feedback/${encodeURIComponent(feedbackId)}/comments${query ? `?${query}` : ''}`
  )
}

/** 提一条反馈。**agent 不能走这条路** —— 服务端会 403；agent 的入口是提案卡。
 *  作者不是参数：它是验证过的会话身份，客户端说了不算。 */
export function createFeedback(body: FeedbackCreateBody): Promise<FeedbackDetail> {
  return request<FeedbackDetail>('/feedback', { method: 'POST', body: JSON.stringify(body) })
}

/** 支持。重复点是幂等的，回的是**写完之后**的计数，不是增量 —— 增量会让两个
 *  同时点的人各自渲染出一个从来没存在过的数字。 */
export function supportFeedback(feedbackId: string): Promise<FeedbackSupportResult> {
  return request<FeedbackSupportResult>(`/feedback/${encodeURIComponent(feedbackId)}/supports`, {
    method: 'POST',
  })
}

export function unsupportFeedback(feedbackId: string): Promise<FeedbackSupportResult> {
  return request<FeedbackSupportResult>(`/feedback/${encodeURIComponent(feedbackId)}/supports`, {
    method: 'DELETE',
  })
}

/** 发一条评论。`parentId` 指向**任意**一条评论：回复的回复由服务端折到顶层，
 *  层级恒为两层，这个判断不放在客户端（放这里就会有第二份实现对不上）。 */
export function createFeedbackComment(
  feedbackId: string,
  body: string,
  parentId?: string | null
): Promise<FeedbackComment> {
  return request<FeedbackComment>(`/feedback/${encodeURIComponent(feedbackId)}/comments`, {
    method: 'POST',
    body: JSON.stringify({ body, parent_id: parentId ?? null }),
  })
}

/** 删一条评论。**只是这一条**，除非它是顶层评论 —— 楼里的回复由服务端一起删掉
 *  （一条回复挂在一个查不到的父亲下面，是没人再问起的孤儿），客户端不需要自己
 *  遍历，多算一次就会和服务端的答案漂开。 */
/** 删掉**整条反馈**（软删，连带它下面的评论）。
 *
 *  **谁能删由服务端说了算**：每一条详情上的 `can_delete` 就是那个答案（作者 —— 写它的
 *  那个 handle 或按下发送的那个 —— 或平台管理员），客户端不自己拼一遍判据。这个仓库
 *  已经吃过一次「客户端重算一遍服务端的规则」的亏（`deployed` 那次：按钮亮着、服务端
 *  回 412），评论那一层也因此把 `can_delete` 交给服务端算。 */
export function deleteFeedback(feedbackId: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/feedback/${encodeURIComponent(feedbackId)}`, {
    method: 'DELETE',
  })
}

export function deleteFeedbackComment(feedbackId: string, commentId: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(
    `/feedback/${encodeURIComponent(feedbackId)}/comments/${encodeURIComponent(commentId)}`,
    { method: 'DELETE' }
  )
}

const commentLikeUrl = (feedbackId: string, commentId: string) =>
  `/feedback/${encodeURIComponent(feedbackId)}/comments/${encodeURIComponent(commentId)}/likes`

/** 点赞一条评论。和 `supportFeedback` 同一个形状：回的是**写完之后的计数**，
 *  不是增量。重复点是幂等的，所以「双击」这件事不需要客户端去防。 */
export function likeFeedbackComment(feedbackId: string, commentId: string): Promise<FeedbackCommentLikeResult> {
  return request<FeedbackCommentLikeResult>(commentLikeUrl(feedbackId, commentId), { method: 'POST' })
}

export function unlikeFeedbackComment(feedbackId: string, commentId: string): Promise<FeedbackCommentLikeResult> {
  return request<FeedbackCommentLikeResult>(commentLikeUrl(feedbackId, commentId), { method: 'DELETE' })
}

/* ---- 管理端 (`/admin/feedback`) ---- */

export function listAdminFeedback(query: FeedbackListQuery & { assignee?: string }): Promise<FeedbackListPayload> {
  return request<FeedbackListPayload>(
    `/admin/feedback${feedbackQuery({
      tab: query.tab,
      assignee: query.assignee,
      q: query.q,
      sort: query.sort,
      page_start: query.pageStart,
      page_size: query.pageSize,
      since: query.since,
      resolved_since: query.resolvedSince,
      deployed_since: query.deployedSince,
    })}`
  )
}

export function getAdminFeedback(feedbackId: string): Promise<FeedbackDetail> {
  return request<FeedbackDetail>(`/admin/feedback/${encodeURIComponent(feedbackId)}`)
}

/* ---- 看板：一个分类一条接口 -------------------------------------------
 *
 * 服务端把看板拆成三块（`backend/app/api/routes/admin_stats.py`），**切到哪一类才拉
 * 哪一类**：合成一条的话，切到第二、第三类时读的是几十秒前的数，而这三块里有两块读
 * 的是全平台增长最快的表（`resource_usage` 每调一次 `/v1/messages` 长一行）。
 *
 * `days` 默认 7（就是页头上那句「过去 7 天」）。窗口是**页面**问的问题，所以由调用方
 * 给，不写死在这里。
 *
 * ⚠️ 这里以前是一条 `/admin/feedback/stats`。服务端把它拆成下面这三条之后，前端有
 * 一段时间还指着老路 —— 而老路上没有路由了，`stats` 就落进 `/admin/feedback/{id}`
 * 那条动态段，回来的是一句「不是合法 uuid」的 400，报错指向的地方和原因差很远。
 * 三块各自有名字之后，这种「路径悄悄指向另一个资源」不可能再发生。
 */

/** 反馈那一块：**全量口径**的总量 / 四栏 / 四级状态，加窗口内按天的三条曲线。
 *
 *  口径是这个类型的全部内容：看板数的是**整个板子**，不是公开那一臂。此前
 *  `counts` 走的是反馈中心那一行标签页的数（被 `PUBLIC_ONLY` 收窄，因为匿名读者
 *  不该从一个数字里得知私密反馈有多少），而旁边的 `series` 是全量 —— 卡片和曲线
 *  各答各的问题。现在两边都是全量，形状上也把三个切口分开写，不再挤在一个扁平的
 *  字典里让人猜哪个是哪个。
 *
 *  `columns` 的四个是**筛选，不是划分**（`agent` 是来源，和公开/私密重叠），所以
 *  它们**加起来不等于** `total.all` —— 和队列那四栏是同一批判据。
 */
export interface StatsFeedback {
  days: number
  /** 一句话答完的几个数。`open`/`closed` 用的是和别处同一个 `CLOSED_STATUSES`。 */
  total: {
    all: number
    open: number
    closed: number
    unassigned: number
    /** 压着没人管的急件（high/urgent 且未办完）—— 分诊台最该先动的一格。 */
    urgent_open: number
  }
  /** 队列那四栏，重拼成计数。**加起来不等于 `total.all`**，理由见上。 */
  columns: { public: number; private: number; agent: number; security: number }
  /** 梯子上的每一级，全量。这是四处里唯一并排展示四级状态的地方。 */
  status: { received: number; in_progress: number; resolved: number; deployed: number }
  /** 这个管理员自己的未读数 —— 人各一份，和板子有多大无关。 */
  unread: number
  /** 长度恒等于 `days`、最早的一天在前。缺的那天是 0，不是一段缺口。 */
  series: { date: string; created: number; resolved: number; deployed: number }[]
  /** 上一等长窗口（`[since-days, since)`）的同口径合计 —— KPI 卡的环比差从这里出。
   *  可选：旧后端还没有它，前端按「键在才画 delta」接线。 */
  prev?: { created: number; resolved: number }
}

/** 用量那一块。`unpriced_tokens` 与 `cost_usd` **一起读才对**：前者是「这些 token
 *  算不出价钱」（订阅按月计费，行上的 0 是「没有价」不是「免费」），少了它，几百万
 *  token 上印一个 `$0.0000` 读起来像「这个月没花钱」。 */
export interface StatsUsage {
  days: number
  totals: { tokens: number; calls: number; cost_usd: number; unpriced_tokens: number }
  series: { date: string; tokens: number; calls: number; cost_usd: number }[]
  /** 柱状图的每一根都带 id 和名字。**今天柱子不点得开**（看板上那一张只报数），
   *  `project_id` 是给以后的钻取和「同名项目」留的**身份** —— 名字在平台上不唯一，
   *  只按名字连线，两个同名项目会合成一根柱子。 */
  top_projects: { project_id: string; name: string; tokens: number; cost_usd: number }[]
  /** 按模型拆。和 `by_route` 是两个正交的切口：「贵的是模型还是计费方式」要两个一起看。 */
  by_model: {
    model: string
    tokens: number
    calls: number
    cost_usd: number
    /** 同一行上的「算不出价钱」的那部分。订阅按月计费，0 是「没有价」不是「免费」。 */
    unpriced_tokens: number
  }[]
  /** 按供给通路拆：gateway（网关）/ subscription（订阅）/ native（自带凭据）/ ''（旧数据）。 */
  by_route: {
    route: string
    tokens: number
    calls: number
    cost_usd: number
    unpriced_tokens: number
  }[]
  /** 额度燃尽。已耗尽 / 快烧完 / 不限量是**三个互斥集合** —— unlimited 是没有 grant。 */
  credits: {
    exhausted: {
      project_id: string | null
      name: string
      credits_total: number
      credits_used: number
      credits_remaining: number
      ratio: number
    }[]
    low: {
      project_id: string | null
      name: string
      credits_total: number
      credits_used: number
      credits_remaining: number
      ratio: number
    }[]
    unlimited_project_ids: string[]
    unlimited_count: number
    burn: {
      credits_in_window: number
      credits_per_day: number
      priced_credits: number
      flat_credits: number
      method: string
    }
  }
  /** 上一等长窗口的同口径合计（环比用），形状与 token / 调用 / 成本三张卡一一对应。
   *  可选：旧后端还没有它，前端按「键在才画 delta」接线。 */
  prev?: { tokens: number; calls: number; cost_usd: number }
}

/** 平台那一块。`machines` 是四张台账的**存量**，不是在线数 —— 在线状态住在进程内存
 *  里，库里没有可以查的那一列。 */
export interface StatsPlatform {
  days: number
  people: {
    total: number
    new: number
    /** 上一等长窗口新增的账号数（「{d} 日新增」那张卡的环比）。可选，理由同上。 */
    prev_new?: number
    admins: number
    /** 真人 / agent 的拆分。判据是 `agent_bindings`，和后端 `IdentityService.is_agent` 同一份。
     *  `total`/`new`/`series[].created` 仍是和，拆分是附加列。 */
    humans: number
    agents: number
    new_humans: number
    new_agents: number
    series: { date: string; created: number; human_created: number; agent_created: number }[]
  }
  machines: { devices: number; hosted_devices: number; warm_machines: number; project_machines: number }
  /** **这一刻**的健康度（和上面两组的「存量 / 窗口」不是一回事）。判据与 `/health/detailed` 同源。 */
  health: {
    overall: 'healthy' | 'degraded' | 'unknown'
    checks: Record<string, { status: string; detail?: string | number | null }>
  }
  /** 三样缺口。各自的口径写在 `gaps.py` —— 磁盘只覆盖后端这一台，预览活在进程内存，
   *  机器普查数的是台账行不是容器。 */
  extras: {
    disk: {
      available: boolean
      free_gb?: number
      total_gb?: number
      used_pct?: number
      tier?: string
      warn_pct?: number
      critical_pct?: number
      note_key: string
    }
    preview: { available: boolean; attached: number | null; note_key: string }
    machines: {
      devices: number
      hosted_devices: number
      warm_total: number
      warm_by_state: Record<string, number>
      warm_error: number
      project_total: number
      project_by_status: Record<string, number>
      project_leased: number
      project_enroll_error: number
      note_key: string
    }
  }
}

/** 一个网络平面的速率读数。见 `core/net_io.py` 的模块 docstring。 */
export interface NetIoBlock {
  available: boolean
  iface: string | null
  scope: 'host' | 'container' | 'process' | null
  /** 字节/秒。读不到是 `null`，**绝不为 0**。 */
  rx_bps: number | null
  tx_bps: number | null
  samples: { rx_bps: number | null; tx_bps: number | null }[]
  note_key: string
}

/** 接口耗时那一块。**和上面三块有一条根本区别：它读进程内存，不读库。**
 *
 *  所以它**没有 `days`**（没有窗口）、重启即清零，而且只覆盖这一个进程 —— 生产上
 *  业务 API 就一个 backend 进程，dev 栈里那个 device-connection 是另一份。口径写在
 *  响应里（`routes_total` 与 `routes_shown`），页面照读。
 *
 *  `p50/p95/p99` 单位是**毫秒**，没有样本的路由是 `null` 不是 0：0 是一个读数
 *  （「真的很快」），null 是「没有数据」，两者画成同一个数会骗人。 */
export interface StatsPerformance {
  /** 这个 app 注册的全部路由（**每一条端点都在 `routes` 里有一行**，没样本的也在）。 */
  routes_registered?: number | null
  /** 有样本的路由数。和 `routes_registered` 一起读才答得了「是不是太少了」。 */
  routes_with_samples: number
  /** 线上护栏截断掉的条数。**非 0 就必须在页面上说出来** —— 静默截断读起来像「就这些」。 */
  routes_omitted?: number
  /** 溢出桶丢掉的样本（`core/route_metrics.py` 的 `MAX_ROUTE_SERIES`）。 */
  dropped_series?: number
  routes: {
    method: string
    /** 路由**模板**（`/feedback/{feedback_id}`），不是带 uuid 的原始路径。 */
    route: string
    count: number
    error_count: number
    status: { '2xx': number; '3xx': number; '4xx': number; '5xx': number }
    /** 毫秒。**最近 256 个样本窗口上的精确分位**，不是全生命期；没有样本是 `null` 不是 0。 */
    p50: number | null
    p95: number | null
    p99: number | null
    /** 每分钟平均耗时，最多 24 点；空槽是 `null` 不是 0。 */
    spark: (number | null)[]
  }[]
  /** 平台网络：两面都给，各自有口径（见 `core/net_io.py`）。
   *  `uplink` 是**这台机器的网卡**（含计量代理到 LLM 的出向流量），`api` 是本进程的
   *  HTTP 载荷。**读不到是 `null` 不是 0** —— 0 说「网是闲的」，null 说「看不见」。 */
  network?: {
    uplink: NetIoBlock
    api: NetIoBlock
  }
  /** 这一刻正在处理的请求数。**探针（`/health`、`/metrics`）不算**，否则读它的那一次
   *  自己就在里面、这个数恒 ≥1。 */
  active_requests: number
  /** 这个进程起来了多久。 */
  uptime_seconds: number
  /** 事件循环的滞后：接口慢而 p95 不高时，答案常常在这里。 */
  loop_lag: { recent_ms: number; worst_ms: number }
  /** 投递账本积压 + 本机 spool 未读（**上界**，读不等于消费）。 */
  reliability: {
    delivery_unsent: number
    delivery_dead_letters: number
    spool: {
      available: boolean
      unread: number
      oldest_age_seconds: number | null
      spools: number
      note_key: string
    }
  }
}

/** 分类 → 它那条接口的形状。`getStats` 的返回类型由这个映射查出来，所以调用方
 *  拿到的永远是它问的那一类，而不是一个四选一的联合（联合要在每个用的地方再窄化
 *  一次，而那正是「切到用量页却读了反馈的字段」这类错会藏身的地方）。 */
/** 交付管线那一块。口径的三条硬事实写在 `domain/platform_stats/pipeline.py`：
 *  机器闸门已退役（`pending_gate`/`gate_failed`/`gate_blocked`/`pr_open` 是死写入）、
 *  `void` 不是一个状态（它是 `revoked` + `note_code=voided`）、`decided_at` 会被
 *  revoke 覆写。页面上的注脚对应的就是它们。 */
export interface StatsPipeline {
  days: number
  backlog: {
    /** 每一档都在，**包括死写入的那几档**：缺档和 0 在屏幕上必须长得不一样。 */
    by_status: Record<string, number>
    /** `note_code ∈ _STUCK` 的卡。判据是码，不是文案。 */
    stuck: number
    /** 非终态的卡 —— 它们会堵死整间房的重新递卡。 */
    blocking_refile: number
    /** 人工作废：`revoked` 里带 `note_code=voided` 的那部分。 */
    voided_stock: number
    live_total: number
    settled_total: number
  }
  stuck_cards: {
    card_id: string
    topic_id: string
    topic_title: string
    project_id: string
    task_id: string | null
    reviewer_handle: string
    status: string
    note_code: string | null
    note: string
    change_subject: string | null
    age_seconds: number
  }[]
  dwell: {
    filed_to_decision: {
      count: number
      p50_seconds: number | null
      p90_seconds: number | null
      max_seconds: number | null
    }
    filed_to_merge: {
      count: number
      p50_seconds: number | null
      p90_seconds: number | null
      max_seconds: number | null
    }
    /** 在途卡的年龄。未决议的卡是右删失样本，不进上面的百分位。 */
    open_card_age: {
      count: number
      p50_seconds: number | null
      max_seconds: number | null
    }
    accepted_not_archived: { count: number; max_seconds: number | null }
  }
  needs_you: {
    reviewer_pending: number
    open_tasks: number
    awaiting_answer: number
    /** 判据是 `delivery/addressing.py` 的三个码，不发明第四个。 */
    reasons: { reviewer: number; reporter: number; asked: number }
    items: {
      kind: string
      id: string
      title: string
      project_id: string
      topic_id: string
      at: string | null
    }[]
  }
  turn_failures: {
    /** 结构化来源是 `blocks.meta`，**不是** `agent_turns`（那张表没有失败列）。 */
    by_code: Record<string, number>
    other: number
    /** `credits_refused_at` 是唯一可靠的额度拒答来源。 */
    credits_refused: number
    prompt_undelivered: number
  }
  host_health: {
    /** 只保留**当前** streak —— 成功一次就删行，答不了「上周隔离过几台」。 */
    tracked: number
    quarantined: number
    rows: {
      device_id: string
      consecutive_failures: number
      last_failure_code: string | null
      last_failure_at: string | null
      quarantined_until: string | null
    }[]
  }
  unsettled_dispatches: number
}

/** 产品健康：北极星 + 护栏。`unavailable` 里那两条**今天根本算不出来**。 */
export interface StatsProduct {
  days: number
  north_star: {
    /** 按 `decided_at` 分桶、只数**现在**仍是 accepted 的卡 —— 窗口口径，
     *  和 series、和卡片标签同一把尺子。 */
    total: number
    /** 上一等长窗口的同一口径合计（环比用）。可选：旧后端还没有它。 */
    prev_total?: number
    series: { date: string; accepted: number }[]
    note_key: string
  }
  rejection: {
    filed: number
    returned: number
    /** 打回率。分母是窗口内**创建**的卡（含还在走的）。 */
    returned_rate: number | null
    buckets: {
      accepted: number
      rejected: number
      voided: number
      /** `revoked` 里**不带** `voided` 码的那部分（采纳后撤销 / 归档扫尾）。 */
      revoked_after_accept: number
      revoked_other: number
      gate_failed: number
      gate_blocked: number
      conflict: number
      live: number
      pr_open: number
    }
    note_key: string
  }
  usefulness: {
    /** 反馈只存在于**通知**上；房间里的主动消息大多不在这里。 */
    up: number
    down: number
    unrated_read: number
    unread: number
    useful_rate: number | null
    proposal_dismissals: number
    note_key: string
  }
  unavailable: { name: string; reason_key: string; needs: string }[]
}

/** 集成与凭据：静默降级。**没有 `days`** —— 存量问题，不是窗口曲线。 */
export interface StatsIntegrations {
  oauth: {
    total: number
    expired: number
    expiring_7d: number
    /** 和「过期」不是一件事：没有 refresh 的到期那天就永远接不上了。 */
    no_refresh_token: number
    note_key: string
  }
  passkey: {
    accounts: number
    with_passkey: number
    coverage: number | null
    note_key: string
  }
  delivery: {
    /** 还会补发的（`attempts < MAX`）和已放弃的死信，**两档必须分开**。 */
    unsent: number
    dead_letters: number
    oldest_unsent_at: string | null
    max_attempts: number
    note_key: string
  }
  unavailable: { name: string; reason_key: string; needs: string }[]
}

export interface StatsShapes {
  feedback: StatsFeedback
  usage: StatsUsage
  platform: StatsPlatform
  performance: StatsPerformance
  pipeline: StatsPipeline
  product: StatsProduct
  integrations: StatsIntegrations
}
export type StatsKind = keyof StatsShapes

/** 看板的统计窗口。三档而不是任意整数：页头切换器只有三个位置，而「窗口」这一档
 *  该进 store（切窗口重拉已加载的类），不该在每个调用点各自传一个字面量。 */
export type StatsDays = 7 | 30 | 90

export function getStats<K extends StatsKind>(kind: K, opts?: { days?: StatsDays }): Promise<StatsShapes[K]> {
  return request<StatsShapes[K]>(`/admin/stats/${kind}${feedbackQuery({ days: opts?.days ?? 7 })}`)
}

/** 改了哪几项就传哪几项，`undefined` 表示「别动它」。**没有 visibility**：
 *  公开与否是提交者一次性的选择，管理员能改它就等于那个决定是假的。 */
export interface FeedbackAdminPatch {
  priority?: FeedbackPriority
  assignee_handle?: string | null
  security?: boolean
}

export function patchAdminFeedback(feedbackId: string, patch: FeedbackAdminPatch): Promise<FeedbackDetail> {
  return request<FeedbackDetail>(`/admin/feedback/${encodeURIComponent(feedbackId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

/** 推一个状态。状态和它那条时间线在后端同一个事务里落库，所以这个动作没有
 *  「只改状态不写历史」的版本。 */
export function setAdminFeedbackStatus(feedbackId: string, status: FeedbackStatus): Promise<FeedbackDetail> {
  return request<FeedbackDetail>(`/admin/feedback/${encodeURIComponent(feedbackId)}/status`, {
    method: 'POST',
    body: JSON.stringify({ status }),
  })
}

/** 内部备注。**只增不改**：一个字符串列会在两个管理员之间互相覆盖，而「上一版
 *  写了什么」正是分诊时最需要知道的。 */
export function createAdminFeedbackNote(feedbackId: string, body: string): Promise<FeedbackDetail> {
  return request<FeedbackDetail>(`/admin/feedback/${encodeURIComponent(feedbackId)}/notes`, {
    method: 'POST',
    body: JSON.stringify({ body }),
  })
}

export type { FeedbackNote }

/* ---- 平台管理员名单 (`/admin/admins`) ----
 *
 * 「谁算平台管理员」是**服务端**的一个判据（配置里的根名单 ∪ 这张表），客户端只画。
 * 名单分两份给，因为两份在页面上的操作权不一样：`root` 来自部署配置、删不掉，`added`
 * 是页面上加的、每行都有删除按钮。分组规则不在这里再定一份 —— 接口给的就是两块。 */

/** 名单里的一行「这个人是谁」。**两组同一个形状**：`root` 不再是一串裸 handle ——
 *  「这一行是谁」在两组里是同一个问题，两份形状就得让人自己把两处对起来。
 *
 *  两格都可能为 null，而 null 各有各的意思，界面**不许回退**：`nickname` 为 null =
 *  这个人没有 profile 行（或平台上根本没有这个账号），回退成 handle 之后界面就分不清
 *  「没设昵称」和「他叫这个 handle」；`avatar_id` 为 null = 他从没自己挑过头像
 *  （判据在服务端 `UserProfileRepository.chosen_avatar_ids`，不是硬比 id），界面这时
 *  画彩色首字母 —— `getAvatarUrl` 对空值回的那张默认图是所有人共用的一张脸。 */
export interface PlatformAdminRow {
  handle: string
  nickname: string | null
  avatar_id: number | null
  /** false = 平台上没有（或已注销）这个账号 —— 这行是死权限，页面要明画，
   *  不许靠 nickname=null 隐式猜。 */
  has_account: boolean
  /** User.created_at；has_account=false 时为 null。 */
  registered_at: string | null
  /** agent 不能做管理动作，所以这行权限用不上 —— 只会从根配置混进来。 */
  is_agent: boolean
}

/** 页面上加进名单的一行：在「这个人是谁」之上多两格出处。`added_by_handle` 是快照：
 *  加人的那个人注销之后，这一行仍然要说得出是谁加的。 */
export interface PlatformAdminAddedRow extends PlatformAdminRow {
  added_by_handle: string
  created_at: string
}

export interface PlatformAdminsPayload {
  /** 部署配置里那份。列得出来，删不掉。 */
  root: PlatformAdminRow[]
  added: PlatformAdminAddedRow[]
}

export function listPlatformAdmins(): Promise<PlatformAdminsPayload> {
  return request<PlatformAdminsPayload>('/admin/admins')
}

/** 「加一个人」那个选择器的候选：按 handle 或昵称搜账号。
 *
 *  单开一条而不是复用用户目录接口：那条只在它取回的那一页里过滤（这个部署上账号
 *  上千，搜昵称十有八九回空），而这里「搜不到」是要么换个说法要么这个人没有账号。
 *  `already_admin` 里的人照常返回 —— 选择器要把他们画成已选中，而不是「搜不到」。 */
export interface AdminCandidate {
  handle: string
  nickname: string
  /** 没挑过头像的人是 null（和反馈卡片、聊天区名册同一条判据），界面画彩色首字母。 */
  avatar_id: number | null
  already_admin: boolean
}

export function searchAdminCandidates(q: string, limit = 20): Promise<{ items: AdminCandidate[] }> {
  return request<{ items: AdminCandidate[] }>(
    `/admin/users?q=${encodeURIComponent(q)}&limit=${encodeURIComponent(String(limit))}`
  )
}

/** 加一个人。回的是**更新后的整份名单**（`created` 说明这次是真加了还是他本来就在）：
 *  加完之后页面上两块都可能变，让客户端自己再拉一次中间那一下页面是旧的。 */
export function addPlatformAdmin(handle: string): Promise<PlatformAdminsPayload & { created: boolean }> {
  return request<PlatformAdminsPayload & { created: boolean }>('/admin/admins', {
    method: 'POST',
    body: JSON.stringify({ handle }),
  })
}

/** 从名单里移出一个人，同样回整份（`removed` 说这次有没有真删掉一行 —— 删一个不在
 *  名单里的人不是错误，他的目的已经成立了）。根管理员到这里会拿到 409。 */
export function removePlatformAdmin(handle: string): Promise<PlatformAdminsPayload & { removed: boolean }> {
  return request<PlatformAdminsPayload & { removed: boolean }>(`/admin/admins/${encodeURIComponent(handle)}`, {
    method: 'DELETE',
  })
}

/* ---- 管理端（网关模型）----
 *
 * 后台「模型管理」那一页的九条接口（`backend/app/api/routes/admin_models.py`）。全是平台
 * 管理员的接口，网关侧的失败在服务端已经折成 503（不可达）/ 502（被拒），原因放在
 * `detail` 里 —— `request` 会把 `error.message` 原样带出来，页面要显示的正是服务端那句
 * 中文原因，所以这里不改写、不吞异常。
 *
 * 和看板那几条同一条纪律：`days` 是**页面**问的问题（窗口由人选的），由调用方给，不写死这里。
 */

/** 网关这一刻的状态。`admin_configured` 为 false 是「这个部署没配管理密钥」，不是
 *  「网关挂了」—— 页面上这两句话得分开说。 */
export interface GatewayStatus {
  reachable: boolean
  readiness: string | null
  admin_configured: boolean
  detail: string | null
  fetched_at: string | null
}

/** 用量窗口。`end_date` 当天**含**在内（闭区间，实测见契约 §0）。 */
export interface GatewayWindow {
  days: number
  start_date: string
  end_date: string
}

/** 一个模型在一个窗口里的用量。`/model/info` 里找不到它时**全 0**，不是 null。 */
export interface GatewayUsageNumbers {
  spend_usd: number
  requests: number
  failed_requests: number
  prompt_tokens: number
  completion_tokens: number
  cache_read_tokens: number
  total_tokens: number
}

/** 单价，单位是**每 token**（网关就是这么记的，页面负责 ×1e6 那类换算）。
 *  缺的键不出现 —— 「没价」和「0 价」在页面上是两回事。 */
export interface GatewayPrices {
  input?: number | null
  output?: number | null
  cache_read?: number | null
  cache_creation?: number | null
}

export interface GatewayCapabilities {
  reasoning?: boolean
  vision?: boolean
  adaptive_thinking?: boolean
}

export interface GatewayUpstream {
  model: string
  host: string
  provider: string
}

/** 清单里的一个模型（契约 §3.1）。
 *
 *  `origin` 决定它是只读还是可改：`config` 来自 config.yaml、页面上只读；`runtime` 是
 *  网关里新增的，可改可删可停用。`offered = selectable && priced && !blocked`，是选择器
 *  真正会给出的那些；`blocked_reason` / `unpriced_reason` 在它没上架时给出人话。 */
export interface GatewayModelInfo {
  name: string
  model_id: string
  label: string
  origin: 'config' | 'runtime'
  blocked: boolean
  selectable: boolean
  priced: boolean
  offered: boolean
  blocked_reason: string | null
  unpriced_reason: string | null
  upstream: GatewayUpstream
  prices: GatewayPrices
  capabilities: GatewayCapabilities
  usage: GatewayUsageNumbers
  /** 仅 origin=config 时给出：可复制的 config.yaml 片段（页面「怎么改」那一段）。 */
  config_yaml?: string
  /** 行内 sparkline 的逐日 token（与详情折线同源同账）；窗口内没用过是逐日 0。 */
  series?: number[]
  /** 这条模型由一条导入的订阅喂养时的 overlay（终态订阅不给）：列表「订阅」徽章
   *  与详情抽屉订阅块的数据。 */
  subscription?: GatewaySubscriptionOverlay | null
}

/** 列表/详情里模型项上的订阅 overlay。 */
export interface GatewaySubscriptionOverlay {
  id: string
  status: string
  account_email: string | null
  /** 这条订阅显式选的上游模型；null = 跟随部署默认。 */
  upstream_model?: string | null
  token_expires_at?: string | null
  last_refresh_error?: string | null
  quota: { tiers: SubscriptionQuotaTier[]; fetched_at: string | null } | null
}

export interface GatewayModelsPayload {
  gateway: GatewayStatus
  window: GatewayWindow
  totals: GatewayUsageNumbers
  models: GatewayModelInfo[]
}

/** 详情（契约 §3.2）。`series` 是这条模型每天的花费；`platform_usage` 是平台侧归因的
 *  读数，**以网关账本为准**（`resource_usage.by_model` 对网关流量不可信，见契约 §0）。 */
export interface GatewayModelDetail {
  model: GatewayModelInfo
  series: { date: string; spend_usd: number; requests: number; tokens: number }[]
  platform_usage: {
    calls: number
    tokens: number
    cost_usd: number
    unpriced_tokens: number
    note: string
  }
}

/** 新增一个运行时模型（契约 §3.3）。`api_key` 只在请求体里出现，**绝不回显**。 */
export interface GatewayModelCreateInput {
  name: string
  upstream_model: string
  api_base?: string | null
  api_key?: string | null
  label?: string
  selectable?: boolean
  prices?: GatewayPrices
  capabilities?: GatewayCapabilities
}

/** 改一个运行时模型（契约 §3.3）。PATCH 语义：只传要改的字段，缺的表示「别动它」。
 *
 *  `api_key_unchanged` 是「编辑界面不回显、也不拿空串覆盖上游凭据」那条路：界面不知道
 *  现在的 key，所以它要么给一个新的 `api_key`，要么声明「不改动」—— 不能发一个空串，
 *  那会把已存的凭据抹掉。 */
export interface GatewayModelUpdateInput {
  upstream_model?: string
  api_base?: string | null
  api_key?: string | null
  api_key_unchanged?: boolean
  label?: string
  selectable?: boolean
  prices?: GatewayPrices
  capabilities?: GatewayCapabilities
}

export interface GatewayProjectCredits {
  total: number
  used: number
  remaining: number
  unlimited: boolean
}

/** 额度段里的一个项目（契约 §3.4）。
 *
 *  `budget_derived_usd` 是按算力额度折出的刹车值（unlimited 时为 null）；
 *  `budget_override_usd` 是网关 key 上实际设的、与 derived 不一致的那个值 —— 两个都在，
 *  才看得出「这个项目的额度是不是被人手动改过」。 */
export interface GatewayProject {
  project_id: string
  name: string
  key_alias: string
  has_key: boolean
  gateway_spend_usd: number
  max_budget_usd: number | null
  budget_derived_usd: number | null
  budget_override_usd: number | null
  credits: GatewayProjectCredits
  usage: GatewayUsageNumbers
}

export interface GatewayProjectsPayload {
  window: GatewayWindow
  projects: GatewayProject[]
  totals: { projects: number; with_key: number; over_budget: number; unlimited: number }
}

/** 最近操作里的一行（契约 §3.5）。**失败的写操作也落行**（`result="failed"`），所以这段
 *  同时是「谁改了什么」和「哪一次没成」—— 少了失败那半，页面对「改不动」是无痕的。 */
export interface GatewayAuditEntry {
  created_at: string
  actor_handle: string
  action: string
  target: string
  result: 'ok' | 'failed'
  detail: string | null
  /** 改动前后的字段快照（写入时已脱敏）。两者都为空时这项操作没有可展示的字段变化。 */
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
}

export interface GatewayAuditPayload {
  items: GatewayAuditEntry[]
}

/** 这一节的查询串：`days` / `limit` 都是数字，统一走 URLSearchParams 编码。 */
function gatewayQuery(params: Record<string, number>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) search.set(key, String(value))
  return `?${search.toString()}`
}

export function getGatewayModels(days: number): Promise<GatewayModelsPayload> {
  return request<GatewayModelsPayload>(`/admin/gateway/models${gatewayQuery({ days })}`)
}

export function getGatewayModel(name: string, days: number): Promise<GatewayModelDetail> {
  return request<GatewayModelDetail>(`/admin/gateway/models/${encodeURIComponent(name)}${gatewayQuery({ days })}`)
}

export function createGatewayModel(body: GatewayModelCreateInput): Promise<{ model: GatewayModelInfo }> {
  return request<{ model: GatewayModelInfo }>('/admin/gateway/models', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateGatewayModel(name: string, body: GatewayModelUpdateInput): Promise<{ model: GatewayModelInfo }> {
  return request<{ model: GatewayModelInfo }>(`/admin/gateway/models/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function deleteGatewayModel(name: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/admin/gateway/models/${encodeURIComponent(name)}`, { method: 'DELETE' })
}

export function setGatewayModelBlocked(name: string, blocked: boolean): Promise<{ blocked: boolean }> {
  return request<{ blocked: boolean }>(`/admin/gateway/models/${encodeURIComponent(name)}/blocked`, {
    method: 'POST',
    body: JSON.stringify({ blocked }),
  })
}

export function getGatewayProjects(days: number): Promise<GatewayProjectsPayload> {
  return request<GatewayProjectsPayload>(`/admin/gateway/projects${gatewayQuery({ days })}`)
}

/** 设或清一个项目的额度上限（`null` = 清除覆盖）。服务端立刻落到网关，回来的是**同一项**
 *  更新后的样子，页面拿它替换那一行即可。 */
export function setGatewayProjectBudget(projectId: string, maxBudgetUsd: number | null): Promise<GatewayProject> {
  return request<GatewayProject>(`/admin/gateway/projects/${encodeURIComponent(projectId)}/budget`, {
    method: 'PUT',
    body: JSON.stringify({ max_budget_usd: maxBudgetUsd }),
  })
}

export function getGatewayAudit(limit: number): Promise<GatewayAuditPayload> {
  return request<GatewayAuditPayload>(`/admin/gateway/audit${gatewayQuery({ limit })}`)
}

/* ---- 管理端（LLM 订阅导入）----
 *
 * 「网关池里的订阅型上游」的平台侧一半：device flow 四步（开、轮询、取消）加凭据
 * 生命周期（列表、手动刷新、按需额度、移除）。凭据永不出现 —— 服务端 DTO 已经
 * 脱敏，这里也没有一个 token 字段。 */

/** 一个速率窗口的额度读数（wham/usage 的解析结果）。`utilization` 是 0–100。 */
export interface SubscriptionQuotaTier {
  name: string
  utilization: number
  resets_at: string | null
}

/** 一条平台级 LLM 订阅（契约 §3.3 的 DTO）。token 与密文字段一个字母都不在。 */
export interface LlmSubscription {
  id: string
  provider: string
  label: string
  status: string
  account_email: string | null
  chatgpt_account_id: string | null
  token_expires_at: string | null
  last_refresh_at: string | null
  last_refresh_error: string | null
  linked_model_name: string | null
  /** 显式选的上游模型；null = 跟随部署默认（settings.subscription_upstream_model）。 */
  upstream_model: string | null
  quota: { tiers: SubscriptionQuotaTier[]; fetched_at: string | null } | null
  created_by_handle: string
  created_at: string
}

export interface DeviceFlowStartResponse {
  flow_id: string
  user_code: string
  verification_uri: string
  expires_in: number
  interval: number
}

/** 轮询一次的三态：`pending` 继续等；`complete` 带订阅 DTO；`expired` 要重开一个。 */
export type DeviceFlowPollResponse =
  | { state: 'pending' }
  | { state: 'complete'; subscription: LlmSubscription }
  | { state: 'expired' }

/** 开一次导入（或定向重授权：`targetSubscriptionId` 非空时服务端校验同一身份）。 */
export function startSubscriptionDeviceFlow(body: {
  provider?: 'openai_codex'
  label?: string | null
  target_subscription_id?: string | null
  /** 显式指定上游模型（如 openai/gpt-5.6-luna）；空 = 跟随部署默认。 */
  upstream_model?: string | null
}): Promise<DeviceFlowStartResponse> {
  return request<DeviceFlowStartResponse>('/admin/subscriptions/device-flows', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function pollSubscriptionDeviceFlow(flowId: string): Promise<DeviceFlowPollResponse> {
  return request<DeviceFlowPollResponse>(`/admin/subscriptions/device-flows/${encodeURIComponent(flowId)}/poll`, {
    method: 'POST',
  })
}

export function cancelSubscriptionDeviceFlow(flowId: string): Promise<{ cancelled: boolean }> {
  return request<{ cancelled: boolean }>(`/admin/subscriptions/device-flows/${encodeURIComponent(flowId)}/cancel`, {
    method: 'POST',
  })
}

export function listSubscriptions(): Promise<{ items: LlmSubscription[] }> {
  return request<{ items: LlmSubscription[] }>('/admin/subscriptions')
}

/** 手动刷新一次并推进网关。凭据被判死时回 `reauth_required` 状态（不是报错）。 */
export function refreshSubscription(id: string): Promise<{
  status: string
  token_expires_at: string | null
  last_refresh_error: string | null
}> {
  return request(`/admin/subscriptions/${encodeURIComponent(id)}/refresh`, { method: 'POST' })
}

/** 按需查一次额度。传输错误时服务端回旧快照（`stale: true`）。 */
export function getSubscriptionQuota(id: string): Promise<{
  tiers: SubscriptionQuotaTier[]
  queried_at: string | null
  stale: boolean
}> {
  return request(`/admin/subscriptions/${encodeURIComponent(id)}/quota`)
}

/** 改一条订阅的上游模型并推进网关；`upstream_model` 为 null = 清除显式选择、回落部署默认。 */
export function updateSubscriptionUpstreamModel(id: string, upstreamModel: string | null): Promise<LlmSubscription> {
  return request<LlmSubscription>(`/admin/subscriptions/${encodeURIComponent(id)}/upstream-model`, {
    method: 'PATCH',
    body: JSON.stringify({ upstream_model: upstreamModel }),
  })
}

/** 移除一条订阅：置终态，并 best-effort 停用挂在网关上的模型。 */
export function revokeSubscription(id: string): Promise<{ revoked: boolean }> {
  return request<{ revoked: boolean }>(`/admin/subscriptions/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

/* ---- 提案卡：agent 举手，人决定 (`/topics/{id}/feedback-proposals`) ---- */

/** 这个话题里**还活着**的提案卡，最新的一张在前。
 *
 *  「还活着」由服务端的指纹决定，不由组件状态决定：已经「不用」过的不会回来 ——
 *  原型的「不用」只活在内存里，刷新就回来。 */
export function listFeedbackProposals(topicId: string): Promise<FeedbackProposal[]> {
  return request<FeedbackProposal[]>(`/topics/${encodeURIComponent(topicId)}/feedback-proposals`)
}

/** 「不用」。落一行；那张卡本身留在话题历史里（「问过」要记得，「以后别再问」
 *  也要记得）。 */
export function dismissFeedbackProposal(topicId: string, blockId: string): Promise<{ dismissed: boolean }> {
  return request<{ dismissed: boolean }>(
    `/topics/${encodeURIComponent(topicId)}/feedback-proposals/${encodeURIComponent(blockId)}/dismiss`,
    { method: 'POST' }
  )
}

/** 发送：把卡变成一条正式反馈。
 *
 *  正文走请求体而不是卡上的原文 —— 表单是预填的，人可以改完再发，而按下发送的
 *  人为自己发出去的东西负责。作者从卡上取（提案的 agent），提交者取验证过的
 *  调用者，两个字段都不是客户端能填的。 */
export function acceptFeedbackProposal(
  topicId: string,
  blockId: string,
  body: FeedbackCreateBody
): Promise<FeedbackDetail> {
  return request<FeedbackDetail>(
    `/topics/${encodeURIComponent(topicId)}/feedback-proposals/${encodeURIComponent(blockId)}/accept`,
    { method: 'POST', body: JSON.stringify(body) }
  )
}

// Build the absolute WebSocket URL for a topic's chat channel, honoring the
// current page protocol (ws/wss) so it works behind the dev proxy and in prod.
export function chatWsUrl(topicId: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  // Browsers can't set an Authorization header on a WebSocket, so the session
  // token rides as ?token= (the backend pins authorship from it, ignoring any
  // per-message `author` the client sends).
  const token = authToken()
  const q = token ? `?token=${encodeURIComponent(token)}` : ''
  // BASE, not a hand-written '/api': the gateway strips exactly one '/api', so a
  // single prefix arrived as '/topics/.../chat', matched no route, and the
  // handshake was refused 403. The browser retried every 16s, which surfaced as
  // 「连接断开，正在自动重连」 and read like a flaky network.
  return `${proto}://${window.location.host}${BASE}/topics/${encodeURIComponent(topicId)}/chat${q}`
}

// Dev-only observability hook, same purpose as `window.__blockCache`: the probe
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
