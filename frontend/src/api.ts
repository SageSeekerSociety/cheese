// REST helpers for the CheeseX backend. All responses are wrapped in an
// ApiEnvelope; these helpers unwrap `data` and surface non-200 codes as errors.
import type {
  AcceptCard,
  AgentType,
  ApiEnvelope,
  Block,
  ChatAttachment,
  ComputeProfiles,
  Contributions,
  ExecProfiles,
  FileContent,
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
  ProjectMemberRow,
  ProjectOverview,
  ReactionAgg,
  RoomTask,
  RoomTree,
  SandboxImageInfo,
  Topic,
  TopicComputeProfile,
  TopicMemberRow,
  TopicProgress,
  TopicWorkSummary,
  UpstreamInfo,
  UpstreamSyncResult,
  UsageStats,
  UserProfile,
  WorkspaceFile,
} from './cx_types'
import type { PlacePayload } from './lib/place'

import { isThreadPayload } from './lib/place'
import { TOPIC_TITLE_MAX_LENGTH } from './lib/topicTitle'
import { isTransportFailure, transportFailureMessage } from './lib/transportFailure'

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

// P1 真鉴权: read the signed session token straight from storage (avoids an
// import cycle with me.ts). Sent as `Authorization: Bearer` so the backend
// resolves the actor from a verified token instead of a forgeable body field.
// Empty when signed out or for an older pre-token cached identity.
export function authToken(): string {
  // fusion unify P3: ONE token. The 知是 login stores its JWT under `accessToken`
  // (sub=int id + a `handle` claim); the merged backend's cheesex auth reads the
  // handle claim, so the same token authenticates both API layers. Fall back to
  // the legacy `cheesex.me` token for any older cached session.
  try {
    const main = localStorage.getItem('accessToken')
    if (main) return main
    const raw = localStorage.getItem('cheesex.me')
    return raw ? JSON.parse(raw)?.token ?? '' : ''
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
    message: string
  ) {
    super(message)
    this.name = 'ApiError'
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
        const res = await fetch('/api/users/auth/refresh-token', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
        })
        if (!res.ok) return
        const body = (await res.json()) as { data?: { accessToken?: string } }
        const next = body?.data?.accessToken
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? 'GET').toUpperCase()
  await ensureFreshToken()
  // A 401 is retried once, for ANY method, after forcing a refresh — see
  // `refreshNow`. Safe for writes too: a 401 means the request was rejected at
  // the door, so nothing happened that a retry could duplicate. Only retried
  // when the refresh actually produced a different token, or a server that 401s
  // for some other reason would make every call fire twice.
  let authRetried = false
  for (let attempt = 0; ; attempt += 1) {
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
    if (isTransportFailure(res.status, body)) {
      if (
        attempt < GET_RETRY_DELAYS_MS.length &&
        isRetryableGetFailure(method, res.status, undefined, body === NOT_JSON)
      ) {
        await wait(GET_RETRY_DELAYS_MS[attempt])
        continue
      }
      throw new ApiError(res.status, transportFailureMessage(method, res.status))
    }
    if (!res.ok) {
      // #450 rule 2 (frontend edition): the backend's errors carry a human
      // sentence (`message`) — a toast that shows only "HTTP 422 for /path"
      // sends the room hunting a mystery the server had already explained.
      const said = body as { message?: string; error?: { message?: string } }
      const serverSaid = said.message || said.error?.message || ''
      throw new ApiError(
        res.status,
        serverSaid ? `${serverSaid}（HTTP ${res.status}）` : `HTTP ${res.status} for ${path}`
      )
    }
    const envelope = body as ApiEnvelope<T>
    if (envelope.code !== 200) {
      throw new Error(envelope.message || `API error code ${envelope.code}`)
    }
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
  if (isTransportFailure(res.status, body)) {
    throw new ApiError(res.status, transportFailureMessage(method, res.status))
  }
  if (!res.ok) {
    const said = body as { message?: string; error?: { message?: string } }
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
  return request<{ teams: Array<{ id: number; name: string; personal?: boolean }> }>('/teams/my-teams').then((r) =>
    r.teams.map((t) => ({ id: t.id, name: t.name, personal: t.personal === true }))
  )
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

export function createProject(
  name: string,
  ownerHandle?: string,
  teamId?: number,
  externalTaskId?: number
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

// NOTE: there is deliberately no `generateSummary` wrapper here. The POST it
// called is parked (see `backend/app/api/routes/activities.py`), so keeping the
// wrapper would only leave a 404 waiting for its first caller. `summary` still
// arrives on the project card above — it just has no trigger in the UI.

// A 1:1 private chat as a normal Topic (open the chat WS on its id). Without
// `peerHandle` it's the member's 1:1 with 芝士; with `peerHandle` it's a
// person-to-person DM between the two humans (shared by both).
export function getPrivateChat(projectId: string, userHandle: string, peerHandle?: string): Promise<Topic> {
  const peer = peerHandle ? `&peer_handle=${encodeURIComponent(peerHandle)}` : ''
  return request<Topic>(
    `/projects/${encodeURIComponent(projectId)}/private-chat?user_handle=${encodeURIComponent(userHandle)}${peer}`
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
  opts?: { sort?: TopicSortField; order?: TopicSortOrder }
): Promise<ListPayload<Topic>> {
  const q = new URLSearchParams({ project_id: projectId })
  if (opts?.sort) q.set('sort', opts.sort)
  if (opts?.order) q.set('order', opts.order)
  return request<ListPayload<Topic>>(`/topics?${q.toString()}`)
}

// 整个项目的支线，每条带着它当前骑的那张验收卡。侧栏要画「房间 → 它派出去的活
// → 那件活的 PR」这棵树，而按房间问是一个房间一个请求（这里有一百七十多个）。
export function listProjectTasks(projectId: string): Promise<ListPayload<RoomTask>> {
  return request<ListPayload<RoomTask>>(`/projects/${encodeURIComponent(projectId)}/tasks`)
}

/** 一个房间的一批批活（树），最新的在前。一棵树 = 一个分支 = 一个 PR = 一批活。
 *
 *  房间会封口一批、开下一批，所以「我现在写的东西进的是哪一批」才有答案 —— 封了
 *  口的房间和没封口的在屏幕上长得一模一样，是「我改了半天，改动怎么不在 PR 上」
 *  的来源。 */
export function listRoomTrees(roomId: string): Promise<ListPayload<RoomTree>> {
  return request<ListPayload<RoomTree>>(`/topics/${encodeURIComponent(roomId)}/trees`)
}

export function listRoomTasks(
  roomId: string,
  // 每条支线最多带回多少块对话。标记只要支线本身，所以取 1 —— 不传的话后端会把
  // 房间里每条支线的全部历史都吐回来（它自己的 docstring 说明了为什么没有默认上限）。
  opts?: { limit?: number }
): Promise<ListPayload<RoomTask & { blocks: Block[] }>> {
  const q = new URLSearchParams()
  if (opts?.limit != null) q.set('limit', String(opts.limit))
  const query = q.toString() ? `?${q.toString()}` : ''
  return request<ListPayload<RoomTask & { blocks: Block[] }>>(`/topics/${encodeURIComponent(roomId)}/tasks${query}`)
}

export function createTopic(
  projectId: string,
  title: string,
  parentId?: string,
  // 谁在这个话题里干活。不传 = 跟着项目的默认走，而且**继续跟着**它变 —— 这和
  // 「把当前默认抄一份存下来」不是一回事，后者会在换默认时留下一批不动的旧话题。
  agentInstanceId?: string | null
): Promise<Topic> {
  const body: Record<string, string> = { project_id: projectId, title }
  if (parentId) body.parent_id = parentId
  if (agentInstanceId) body.agent_instance_id = agentInstanceId
  return request<Topic>('/topics', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// ---- 话题用哪个 AI 队友 ----

export interface TopicAgent {
  topic_id: string
  instance_id: string | null
  handle: string
  type_name: string | null
  display_name: string
  /** true = 这个话题没自己选过，跟着项目默认走（换了默认它会跟着换） */
  inherited: boolean
}

export function getTopicAgent(topicId: string): Promise<TopicAgent> {
  return request<TopicAgent>(`/topics/${encodeURIComponent(topicId)}/agent`)
}

// instance_id: null = 交还给项目默认。
export function setTopicAgent(topicId: string, instanceId: string | null): Promise<TopicAgent> {
  return request<TopicAgent>(`/topics/${encodeURIComponent(topicId)}/agent`, {
    method: 'PUT',
    body: JSON.stringify({ instance_id: instanceId }),
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
export function upgradeBlock(blockId: string, createdBy: string): Promise<PlacePayload> {
  return request<PlacePayload>(`/blocks/${encodeURIComponent(blockId)}/upgrade`, {
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

// AI 模型池: the project's current profile + the ones it may select.
export function getExecutionProfiles(projectId: string): Promise<ExecProfiles> {
  return request<ExecProfiles>(`/projects/${encodeURIComponent(projectId)}/execution-profiles`)
}
export function setExecutionProfile(projectId: string, profile: string): Promise<{ current: string }> {
  return request(`/projects/${encodeURIComponent(projectId)}/execution-profile`, {
    method: 'PUT',
    body: JSON.stringify({ profile }),
  })
}

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
export function listProjectMachines(projectId: string): Promise<ListPayload<import('./cx_types').ProjectMachine>> {
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

// 订阅模型: the project's current Claude model + the ones it may select. Same
// shape as compute pools; a project picks Sonnet 5 (default) or Opus 5.
export function getModelProfiles(projectId: string): Promise<ComputeProfiles> {
  return request<ComputeProfiles>(`/projects/${encodeURIComponent(projectId)}/model-profiles`)
}
export function setModelProfile(projectId: string, profile: string): Promise<{ current: string }> {
  return request(`/projects/${encodeURIComponent(projectId)}/model-profile`, {
    method: 'PUT',
    body: JSON.stringify({ profile }),
  })
}

// 会话级算力 (v4): a topic's own compute选择, switchable until its first turn.
export function getTopicComputeProfile(topicId: string): Promise<TopicComputeProfile> {
  return request<TopicComputeProfile>(`/topics/${encodeURIComponent(topicId)}/compute-profile`)
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

// Which type the project's default agent wears; an empty name clears it. The
// agent itself stays — and so does the memory it has been accumulating.
export function setProjectAgentType(projectId: string, typeName: string): Promise<ProjectAgent> {
  return request(`/projects/${encodeURIComponent(projectId)}/default-agent`, {
    method: 'PUT',
    body: JSON.stringify({ type_name: typeName }),
  })
}

// ---- AI 队友 (agent 类型与实例) ----
//
// 「不能停用最后一个」and the like are the backend's to enforce; these are plain
// transports. What they must NOT do is paper over a missing endpoint: the agent
// backend lands separately, so a 404 here has to reach the caller as a 404 (see
// `isEndpointMissing`) rather than as an empty list that reads like "no agents".

// 一个字段要么给得出选项，要么说得出为什么给不出 —— 没有第三种。后端是唯一
// 事实源（backend/app/domain/agent_type/options.py），这里不留第二份清单：某个
// 字段哪天真的接上了运行链路，改那边一处，编辑器自己就跟着变。
export interface AgentFieldChoice {
  id: string
  label: string
  description: string
  default: boolean
}

export interface AgentFieldOptions {
  /** 'choosable' = choices 就是全部会生效的取值；'unavailable' = 见 reason/note */
  state: 'choosable' | 'unavailable'
  choices: AgentFieldChoice[]
  reason: string
  note: string
}

export type AgentTypeOptions = Record<string, AgentFieldOptions>

export function getAgentTypeOptions(): Promise<AgentTypeOptions> {
  return request<AgentTypeOptions>('/agent-types/options')
}

// The merged type catalog: platform presets + this project's custom types.
export function listAgentTypes(): Promise<ListPayload<AgentType>> {
  return request<ListPayload<AgentType>>('/agent-types')
}

export interface AgentTypeInput {
  title?: string
  description?: string
  body?: string
  skills?: string[]
  mcp_servers?: string[]
  model?: string | null
  effort?: string | null
  harness?: string | null
  // Who authored the type — the backend records it and shows it in the catalog.
  created_by?: string
}

export function createAgentType(payload: AgentTypeInput & { name: string; body: string }): Promise<AgentType> {
  return request<AgentType>('/agent-types', { method: 'POST', body: JSON.stringify(payload) })
}

export function updateAgentType(name: string, payload: AgentTypeInput): Promise<AgentType> {
  return request<AgentType>(`/agent-types/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

// The agents this project has. A project that never configured one still gets a
// row back — the implicit 芝士, `configured: false` — because it is really
// working in every room and owns a real memory pool.
export function listProjectAgents(projectId: string): Promise<ListPayload<ProjectAgent>> {
  return request<ListPayload<ProjectAgent>>(`/projects/${encodeURIComponent(projectId)}/agents`)
}

export function createProjectAgent(
  projectId: string,
  payload: { display_name: string; handle?: string; type_name?: string | null }
): Promise<ProjectAgent> {
  return request<ProjectAgent>(`/projects/${encodeURIComponent(projectId)}/agents`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function updateProjectAgent(
  projectId: string,
  agentId: string,
  payload: { display_name?: string; type_name?: string | null }
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

// Which agent a new topic gets. `instance_id` picks a different agent (a
// different memory pool); `type_name` re-skins the one the project already has,
// so the pool it has been filling stays its own.
export function setProjectDefaultAgent(
  projectId: string,
  body: { instance_id?: string | null; type_name?: string | null }
): Promise<ProjectAgent> {
  return request<ProjectAgent>(`/projects/${encodeURIComponent(projectId)}/default-agent`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

// 环境 (spec §9.1): which sandbox image runs this project's agent.
export function getSandboxImage(projectId: string): Promise<SandboxImageInfo> {
  return request<SandboxImageInfo>(`/projects/${encodeURIComponent(projectId)}/sandbox-image`)
}
export function setSandboxImage(projectId: string, image: string): Promise<{ current: string | null }> {
  return request(`/projects/${encodeURIComponent(projectId)}/sandbox-image`, {
    method: 'PUT',
    body: JSON.stringify({ image }),
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
export function syncUpstream(projectId: string): Promise<UpstreamSyncResult> {
  return request(`/projects/${encodeURIComponent(projectId)}/upstream/sync`, {
    method: 'POST',
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
export function deleteOAuthConnection(userId: string, connectionId: number): Promise<void> {
  return legacyRequest(`/users/${encodeURIComponent(userId)}/oauth/connections/${connectionId}`, {
    method: 'DELETE',
  })
}

// 项目总览 / 收件箱 (eval G2/G3).
export function getOverview(projectId: string): Promise<ProjectOverview> {
  return request<ProjectOverview>(`/projects/${encodeURIComponent(projectId)}/overview`)
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

export function markRead(alertId: string): Promise<InboxItem> {
  return request<InboxItem>(`/alerts/${encodeURIComponent(alertId)}/read`, { method: 'POST' })
}

export function sendFeedback(alertId: string, feedback: 'up' | 'down'): Promise<InboxItem> {
  return request<InboxItem>(`/alerts/${encodeURIComponent(alertId)}/feedback`, {
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

// ---- 图片输入 (chat image attachments) ----

// Upload a chat image into the topic's worktree. NOTE: raw fetch, not
// request() — multipart needs the browser to set the boundary header itself.
export async function uploadAttachment(topicId: string, file: File): Promise<ChatAttachment> {
  const form = new FormData()
  form.append('file', file)
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
export function attachmentRawUrl(topicId: string, path: string): string {
  return `${BASE}/topics/${encodeURIComponent(topicId)}/attachments/raw?path=${encodeURIComponent(path)}`
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

// 选项问题 (cheese ask): one-click answer.
export function answerOptions(blockId: string, option: string, author: string): Promise<Block> {
  return request<Block>(`/topics/blocks/${encodeURIComponent(blockId)}/answer`, {
    method: 'POST',
    body: JSON.stringify({ option, author }),
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

// 运行环境预览 authenticates its iframe deliberately differently from every other
// embed: the frame renders whatever 芝士 chose to serve, and a `?token=` in the
// URL is readable by that page's own JS (location.search) even sandboxed — so
// instead this call, which DOES carry the Authorization header, leaves an
// HttpOnly path-scoped cookie that the iframe's same-origin requests present by
// themselves.
export function primeAppPreview(topicId: string): Promise<{ ready: boolean }> {
  return request<{ ready: boolean }>(`/topics/${encodeURIComponent(topicId)}/app-session`)
}

export function getGitLog(projectId: string, topicId?: string | null): Promise<ListPayload<GitCommit>> {
  const t = topicId ? `?topic=${encodeURIComponent(topicId)}` : ''
  return request<ListPayload<GitCommit>>(`/projects/${encodeURIComponent(projectId)}/git/log${t}`)
}

export function getGitDiff(projectId: string, topicId?: string | null): Promise<{ diff: string }> {
  const t = topicId ? `?topic=${encodeURIComponent(topicId)}` : ''
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
export function listFiles(projectId: string, topicId?: string | null): Promise<ListPayload<WorkspaceFile>> {
  const t = topicId ? `?topic=${encodeURIComponent(topicId)}` : ''
  return request<ListPayload<WorkspaceFile>>(`/projects/${encodeURIComponent(projectId)}/files${t}`)
}

// <img src=…> URL for a workspace file (binary raw endpoint) — the 文件 panel
// shows images as images instead of Monaco-mangled bytes.
export function workspaceFileRawUrl(projectId: string, path: string, topicId?: string): string {
  const t = topicId ? `&topic=${encodeURIComponent(topicId)}` : ''
  return `${BASE}/projects/${encodeURIComponent(projectId)}/file/raw?path=${encodeURIComponent(path)}${t}`
}

export function readFile(projectId: string, path: string, topicId?: string | null): Promise<FileContent> {
  const t = topicId ? `&topic=${encodeURIComponent(topicId)}` : ''
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
  version?: string | null
): Promise<{ path: string; version: string }> {
  const t = topicId ? `?topic=${encodeURIComponent(topicId)}` : ''
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
export function getAcceptCards(topicId: string): Promise<ListPayload<AcceptCard>> {
  return request<ListPayload<AcceptCard>>(`/topics/${encodeURIComponent(topicId)}/accept-card`)
}

// 采纳 PR 化 (#188 §5.1): live CI state of the newest card's PR. Safe to poll —
// answers {available:false} when the topic has no PR-riding card.
export function getPrChecks(topicId: string): Promise<PrChecks> {
  return request<PrChecks>(`/topics/${encodeURIComponent(topicId)}/pr-checks`)
}

export function acceptCard(cardId: string, decidedBy: string): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/accept`, {
    method: 'POST',
    body: JSON.stringify({ decided_by: decidedBy }),
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

// 人工放行 (App 采纳等 CI 再合): merge a pr_open card's PR even though its
// checks are not all green. The platform never does this on its own —红着合
// 有时候是对的，不能接受的是没有人做过这个决定。So the actor is taken from the
// session server-side (never the body) and the card records who / when / what
// the checks said / why. Only the reviewer, the authorizer, or an owner/lead
// may call it, and 芝士 is refused outright.
export function mergeCardAnyway(cardId: string, reason: string): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/merge-anyway`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
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

// ---- 话题成员名册 (群聊房间的地基, fusion-design §3) --------------------------
// Roster of a topic's group room. `actor` is the acting user's handle — no auth
// layer yet (agent-as-user is P1), so the backend authorizes mutations against
// the actor's topic role (owner/admin may manage the roster).

export function listTopicMembers(topicId: string): Promise<ListPayload<TopicMemberRow>> {
  return request<ListPayload<TopicMemberRow>>(`/topics/${encodeURIComponent(topicId)}/members`)
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

// 一个 id 指向一个「地点」——房间答 Topic，支线答 RoomTask (lib/place.ts)。
// 打开一条支线只有这一条路：侧栏那份列表只查 topics 表，支线从来不在里面。
export function getPlace(placeId: string): Promise<PlacePayload> {
  return request<PlacePayload>(`/topics/${encodeURIComponent(placeId)}`)
}

// Re-fetch a single ROOM (after accept it becomes archived). Null for a thread:
// the caller patches a row in the rail's list, and threads have no row there.
export async function getTopic(topicId: string): Promise<Topic | null> {
  const place = await getPlace(topicId)
  return isThreadPayload(place) ? null : place
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

// ---- 贡献图 (§10.1) ----

export function getContributions(projectId: string): Promise<Contributions> {
  return request<Contributions>(`/projects/${encodeURIComponent(projectId)}/contributions`)
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
