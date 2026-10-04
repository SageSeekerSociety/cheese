// REST helpers for the CheeseX backend. All responses are wrapped in an
// ApiEnvelope; these helpers unwrap `data` and surface non-200 codes as errors.
import type {
  AcceptCard,
  AgentConfiguration,
  AgentType,
  ApiEnvelope,
  Block,
  BranchProtection,
  BranchProtectionPatch,
  BranchProtectionRules,
  ChatAttachment,
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
  OAuthConnectionInfo,
  OverviewAuto,
  PrChecks,
  PreviewInfo,
  ProfileTopic,
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
import type { DocComment } from './lib/docThreadTypes'
import type { AgentFieldChoice } from './lib/modelChoices'
import type { SitePage } from './types/site'

import { ApiError, authHeaders, authToken, BASE, request, requestConditional, roomRead } from './api/http'
import { shareInFlight } from './lib/inflight'
import { refusalWords } from './lib/noticeText'
import { createPreviewPdfReader } from './lib/previewPdf'
import { TOPIC_TITLE_MAX_LENGTH } from './lib/topicTitle'
import { isTransportFailure, readJson, transportFailureMessage } from './lib/transportFailure'
import { postFormWithProgress } from './lib/xhrUpload'
import { t } from './i18n'

export { TOPIC_TITLE_MAX_LENGTH }
// The transport's public surface, re-exported so every existing importer of
// `api` keeps resolving it here — the split is an internal one.
export type { ConditionalResult } from './api/http'
export {
  ApiError,
  authToken,
  BASE,
  ensureFreshToken,
  isEndpointMissing,
  isRetryableGetFailure,
  NotModified,
  READ_BUDGET_MS,
  refreshNow,
  request,
  requestConditional,
  RequestTimeoutError,
  tokenExpiresWithin,
} from './api/http'

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
      message = refusalWords(body) || body?.detail || message
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
    const serverSaid = refusalWords(body)
    const http = `HTTP ${res.status}`
    throw new Error(serverSaid ? t('global.labelWithAside', { label: serverSaid, aside: http }) : `${http} for ${path}`)
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
  teamId?: number,
  externalTaskId?: number,
  forgeKind?: 'forgejo' | 'github_app',
  intent?: string,
  agentName?: string,
  id?: string
): Promise<Project> {
  return request<Project>('/projects', {
    method: 'POST',
    body: JSON.stringify({
      id,
      name,
      team_id: teamId,
      // Set when the project is created FROM a 赛题, so the 赛题 can find it
      // again. Absent for a project made from the rail.
      external_task_id: externalTaskId,
      forge_kind: forgeKind,
      // 建项目时问的那一句「你打算做什么」。空串就是没答，服务端不写任何东西。
      intent,
      agent_name: agentName,
    }),
  })
}

// The 2.0 projects created from one 赛题 — what the 赛题 page shows instead of
// blindly offering to create another.
export function listProjectsForTask(taskId: number): Promise<ListPayload<Project>> {
  return request<ListPayload<Project>>(`/projects/by-task/${taskId}`)
}

// Single project card.
export function getProject(projectId: string): Promise<Project> {
  return request<Project>(`/projects/${encodeURIComponent(projectId)}`)
}

/** 归档项目：只有所有者能做。项目从所有人的列表里消失、不能再修改，里面的内容都保留。 */
export function archiveProject(projectId: string): Promise<Project> {
  return request<Project>(`/projects/${encodeURIComponent(projectId)}/archive`, { method: 'POST' })
}

/** 取消归档：项目和随它一起归档的话题回来。 */
export function unarchiveProject(projectId: string): Promise<Project> {
  return request<Project>(`/projects/${encodeURIComponent(projectId)}/unarchive`, { method: 'POST' })
}

/** 我归档过的项目 —— 它们只在这里列出来。 */
export function listArchivedProjects(): Promise<ListPayload<Project>> {
  return request<ListPayload<Project>>('/projects?archived=true')
}

/** 后端拒绝写入一个已归档项目时，错误名是这个。 */
export function isProjectArchivedError(e: unknown): boolean {
  return e instanceof ApiError && e.code === 'ProjectArchivedError'
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
// who they are, a year of activity, their projects, and — on your own page —
// what 芝士 has noted about you.
export function getUserProfile(handle: string): Promise<UserProfile> {
  return request<UserProfile>(`/users/${encodeURIComponent(handle)}/profile`)
}

// The topics a person wrote in, latest participation first. `from`/`to` are
// UTC dates (`YYYY-MM-DD`), both included.
export function getUserTopics(
  handle: string,
  range: { from?: string; to?: string; limit?: number } = {}
): Promise<{ topics: ProfileTopic[] }> {
  const query = new URLSearchParams()
  if (range.from) query.set('from', range.from)
  if (range.to) query.set('to', range.to)
  if (range.limit) query.set('limit', String(range.limit))
  const qs = query.toString()
  return request<{ topics: ProfileTopic[] }>(`/users/${encodeURIComponent(handle)}/topics${qs ? `?${qs}` : ''}`)
}

// Delete one thing an agent noted about the signed-in person.
export function deleteUnderstanding(id: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/users/me/understanding/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

// `last_activity_at` = 最后活动时间 (the topic's newest block). `updated_at` is
// the row's own mtime and does NOT move when a block lands — it is kept only
// because the API still accepts it.
export type TopicSortField = 'last_activity_at' | 'updated_at' | 'title'
export type TopicSortOrder = 'asc' | 'desc'

// 上一次读到的话题清单和它的 ETag，按请求路径记着。侧栏每 30s 轮询一次，一份 448
// 个话题的清单有近 300KB：服务端答「没变」（304）时把手里这同一个 payload 原样交回，
// 调用方的 `topics.value = payload.data` 就是一次同引用的赋值 —— Vue 的 ref setter
// 见到同一个对象会跳过触发（不解析、不换数组、不重画）。变了才落新的一份。
const topicListCache = new Map<string, { etag: string | null; payload: ListPayload<Topic> }>()

export function listTopics(
  projectId: string,
  opts?: { sort?: TopicSortField; order?: TopicSortOrder }
): Promise<ListPayload<Topic>> {
  const q = new URLSearchParams({ project_id: projectId })
  if (opts?.sort) q.set('sort', opts.sort)
  if (opts?.order) q.set('order', opts.order)
  const path = `/topics?${q.toString()}`
  const cached = topicListCache.get(path)
  // 带上上一次那版 ETag 去问。服务端算出的一模一样就回 304（见后端 list_topics）。
  return requestConditional<ListPayload<Topic>>(path, cached?.etag ?? null).then((result) => {
    if (result.notModified) {
      if (cached) return cached.payload
      // 304 但手里没留底（比如刚重启、缓存已清）：退回一次无条件读，别把空手当没变。
      return request<ListPayload<Topic>>(path)
    }
    if (!result.data) throw new Error('empty topic list response')
    topicListCache.set(path, { etag: result.etag, payload: result.data })
    return result.data
  })
}

/** 一个话题的名字，和它在哪个项目里。跨项目找话题只要这几样。 */
export type TopicName = Pick<Topic, 'id' | 'project_id' | 'title' | 'title_source' | 'kind' | 'status'>

/** 我能看到的所有项目里的话题名，最近有动静的在前。私聊不在里面。 */
export async function listTopicNames(): Promise<TopicName[]> {
  return (await request<{ topics: TopicName[] }>('/topics/names')).topics
}

/** 项目里一次搜索的结果：只搜这个人能看的房间，每组最相关的在前。 */
export interface ProjectSearchHits {
  records: {
    id: string
    room_id: string
    room_title: string
    room_title_source?: string
    kind: 'message' | 'doc' | 'doc_node' | 'comment' | 'weekly'
    author: string
    created_at: string
    /** 说在某件活的卡片里，而不是房间自己的对话里。 */
    task_id: string | null
    snippet: string
  }[]
  tasks: (Pick<RoomTask, 'id' | 'room_id' | 'title' | 'title_source' | 'status'> & {
    room_title: string
    room_title_source?: string
    snippet: string
  })[]
  library: { path: string; bytes: number; modified: string }[]
}

/**
 * `only` 只搜这几类（`message`、`doc_node`…、`tasks`、`library`），并且可以用 `offset`
 * 往后翻；不给 `only` 就是每类各取前 `limit` 条。
 */
export async function searchProject(
  projectId: string,
  q: string,
  limit = 10,
  page?: { only: string[]; offset: number }
): Promise<ProjectSearchHits> {
  return (await askProjectSearch(projectId, q, limit, page, false)).hits
}

/**
 * 同一次搜索，再带上每一类各能搜到多少（`message`、`doc`、`doc_node`、`comment`、
 * `weekly`、`tasks`、`library`）。搜索结果页第一次打开时用它，一次问完。
 */
export async function searchProjectCounted(
  projectId: string,
  q: string,
  limit: number,
  page?: { only: string[]; offset: number }
): Promise<{ hits: ProjectSearchHits; counts: Record<string, number> }> {
  const body = await askProjectSearch(projectId, q, limit, page, true)
  return { hits: body.hits, counts: body.counts ?? {} }
}

function askProjectSearch(
  projectId: string,
  q: string,
  limit: number,
  page: { only: string[]; offset: number } | undefined,
  withCounts: boolean
): Promise<{ hits: ProjectSearchHits; counts?: Record<string, number> }> {
  const params = new URLSearchParams({ q, limit: String(limit) })
  if (page) {
    for (const kind of page.only) params.append('only', kind)
    params.set('offset', String(page.offset))
  }
  if (withCounts) params.set('with_counts', 'true')
  return request(`/projects/${encodeURIComponent(projectId)}/context/search?${params}`)
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

export function createTopic(projectId: string, title?: string, parentId?: string): Promise<Topic> {
  const body: Record<string, string> = { project_id: projectId, ...(title ? { title } : {}) }
  if (parentId) body.parent_id = parentId
  return request<Topic>('/topics', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// ---- 话题级未读 (Feishu-style badges) —— 见 api/topicReads.ts ----
export * from './api/topicReads'

/** A person names the room. The platform stops renaming it on its own from then on. */
export function setTopicTitle(topicId: string, title: string): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}/title`, {
    method: 'POST',
    body: JSON.stringify({ title }),
  })
}

/** Undo the automatic rename announced by `eventId`; the old title comes back and stays. */
export function undoTopicTitle(topicId: string, eventId: string): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}/title/undo`, {
    method: 'POST',
    body: JSON.stringify({ event_id: eventId }),
  })
}

export type TopicNamingMode = 'auto' | 'manual'
export interface TopicNaming {
  mode: TopicNamingMode
  /** Whether the deployment can name rooms at all (a model gateway is configured). */
  available: boolean
  can_manage: boolean
}

export function getTopicNaming(projectId: string): Promise<TopicNaming> {
  return request<TopicNaming>(`/projects/${encodeURIComponent(projectId)}/topic-naming`)
}

export function setTopicNaming(projectId: string, mode: TopicNamingMode): Promise<TopicNaming> {
  return request<TopicNaming>(`/projects/${encodeURIComponent(projectId)}/topic-naming`, {
    method: 'PUT',
    body: JSON.stringify({ mode }),
  })
}

// ---- 归档去向: manual archive / unarchive ----

export function archiveTopic(topicId: string): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}/archive`, {
    method: 'POST',
  })
}

export function unarchiveTopic(topicId: string): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}/unarchive`, {
    method: 'POST',
  })
}

/** 把一条消息转为任务。房间里的消息变成这个房间的一个任务（回来的是 RoomTask，
 *  点的人是负责人）；私聊里的变成一个新房间（回来的是 Topic）——私聊不在话题树
 *  里，任务挂在那儿没人打得开。升级的人由会话认，不由请求体说。 */
export function upgradeBlock(blockId: string): Promise<Topic | RoomTask> {
  return request<Topic | RoomTask>(`/blocks/${encodeURIComponent(blockId)}/upgrade`, {
    method: 'POST',
    body: JSON.stringify({}),
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
  projects: {
    id: string
    name: string
    machines_used: number
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

// 房间的工作电脑：房间这一项（还没开工的 AI 队友开工时用哪台），和每个会话在哪台上。
export function getTopicComputeProfile(topicId: string): Promise<TopicComputeProfile> {
  return request<TopicComputeProfile>(`/topics/${encodeURIComponent(topicId)}/compute-profile`)
}

// The project's agent sessions on one self-hosted device, for a project manager
// to switch some elsewhere. Rooms the caller cannot open are only counted.
export function listDeviceSessions(
  projectId: string,
  deviceId: string
): Promise<{ sessions: import('./types/deviceSessions').DeviceSession[]; hidden: number }> {
  return request(`/projects/${encodeURIComponent(projectId)}/devices/${encodeURIComponent(deviceId)}/sessions`)
}

export function getProjectComputeConfigs(projectId: string): Promise<import('./cx_types').ProjectComputeConfigs> {
  return request(`/projects/${encodeURIComponent(projectId)}/compute-configs`)
}

export function saveProjectComputeConfigs(
  projectId: string,
  configs: Pick<import('./cx_types').ProjectComputeConfigs, 'default'>
): Promise<Pick<import('./cx_types').ProjectComputeConfigs, 'default'>> {
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

// 一个话题一个容器：改的是整个房间，每条会话都跟着搬，先推送，推不上去就整个不换。`abandonUnpushed` 只在原来那台
// 够不着时成立（`WorkComputerUnreachable`）；`ifIdle` 跳过正在干活的房间（409 SessionWorking）；`visibility` 是房间在
// 点名那台上能看到什么，不给就保持原样，新绑上的是隔离环境。
export function setTopicComputeChoice(
  topicId: string,
  choice: import('./cx_types').ComputeChoice,
  options: { abandonUnpushed?: boolean; ifIdle?: boolean; visibility?: 'host' | 'isolated' } = {}
): Promise<{ choice: import('./cx_types').ComputeChoice; proposal: ComputeProposal | null }> {
  return request(`/topics/${encodeURIComponent(topicId)}/compute-profile`, {
    method: 'PUT',
    body: JSON.stringify({
      choice,
      ...(options.abandonUnpushed ? { abandon_unpushed: true } : {}),
      ...(options.ifIdle ? { if_idle: true } : {}),
      ...(options.visibility ? { visibility: options.visibility } : {}),
    }),
  })
}
// ---- AI 队友 (agent 类型与实例) ----
//
// 「不能停用最后一个」and the like are the backend's to enforce; these are plain
// transports. What they must NOT do is paper over a missing endpoint: the agent
// backend lands separately, so a 404 here has to reach the caller as a 404 (see
// `isEndpointMissing`) rather than as an empty list that reads like "no agents".

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
// 项目的远程 MCP 服务器在 `api/mcp.ts`：这个文件在上限之上，只能变短。
export * from './api/mcp'
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

// Personal OAuth/App connections (1.0 router, see legacyRequest): every
// provider the user has linked, not just github_app; callers filter by providerId.
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
//
// A window can also open in the middle (`around` a message) and walk down towards
// the newest with `after`; `has_newer` + `newest_id` are the cursor that way.
export interface BlockPage extends ListPayload<Block> {
  has_more: boolean
  oldest_id: string | null
  has_newer: boolean
  newest_id: string | null
}

export function listBlocks(
  topicId: string,
  opts?: { limit?: number; before?: string; after?: string; around?: string }
): Promise<BlockPage> {
  const q = new URLSearchParams()
  if (opts?.limit !== undefined) q.set('limit', String(opts.limit))
  if (opts?.before) q.set('before', opts.before)
  if (opts?.after) q.set('after', opts.after)
  if (opts?.around) q.set('around', opts.around)
  const qs = q.toString()
  const query = qs ? `?${qs}` : ''
  const path = `/topics/${encodeURIComponent(topicId)}/blocks${query}`
  // 最新那一页会被两条路同时要：切话题的预取（lib/blockCache 的 refreshBlockCache，
  // 由 router 起头）和对话面板自己那一条（useChatPanel 一进房间就拉）。第二条跟着在
  // 飞的那条走，省下一次重复的 GET。
  //
  // 只合并最新页（不带游标）：带 before/after/around 的那些是用户翻页翻出来的、每一次
  // 都对应当下那一段窗口，合并它们没有好处，还会让两个调用方共享同一份数组。
  const newestPage = !opts?.before && !opts?.after && !opts?.around
  return newestPage ? shareInFlight(`blocks:${path}`, () => request<BlockPage>(path)) : request<BlockPage>(path)
}

// Emoji reactions (Slack semantics): toggles (emoji, caller) on a block and
// returns the block's fresh aggregate. The caller is whoever the session names.
// Other clients get the same aggregate pushed as a `reaction` WS frame on the
// topic channel.
export function toggleReaction(
  blockId: string,
  emoji: string
): Promise<{ toggled: 'added' | 'removed'; reactions: ReactionAgg[] }> {
  return request<{ toggled: 'added' | 'removed'; reactions: ReactionAgg[] }>(
    `/blocks/${encodeURIComponent(blockId)}/reactions`,
    { method: 'POST', body: JSON.stringify({ emoji }) }
  )
}

// Edit a message you sent. Everyone in the room, you included, also gets the
// edited block as a `block_updated` frame.
export function editMessage(blockId: string, content: string): Promise<Block> {
  return request<Block>(`/blocks/${encodeURIComponent(blockId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ content }),
  })
}

// ---- 资料库 ----

// 用户给这个项目的文件，按原名。项目一级，所以一个房间引用得到另一个房间上传的
// 那一份——「上周那份预算表」这句话正是在这种地方说的。
import type { LibraryFile } from './lib/libraryApi'
export type { LibraryFile }

export function listProjectLibrary(projectId: string): Promise<ListPayload<LibraryFile>> {
  return request<ListPayload<LibraryFile>>(`/projects/${encodeURIComponent(projectId)}/library`)
}

/** 一份资料的字节。这条端点一律按下载发，所以 `downloadFile` 补在末尾的
 *  `download=true` 在这里没有对应的参数，后端不看它。 */
export function libraryFileRawUrl(projectId: string, path: string): string {
  return `${BASE}/projects/${encodeURIComponent(projectId)}/library/raw?path=${encodeURIComponent(path)}`
}

/** 题目附件清单里那份材料的字节。清单本身走 `TasksApi.listAttachments`，但那条
 *  接口故意不带 url（存储给的是直链，发出来就绕过了下载那道门），所以取文件只能
 *  从这个端点走 —— 门在服务端，带的是这次请求自己的 Authorization。
 *  同 `libraryFileRawUrl`：`downloadFile` 补的 `download=true` 这里没有对应参数。 */
export function taskAttachmentRawUrl(taskId: number, attachmentId: number): string {
  return `${BASE}/tasks/${taskId}/attachments/${attachmentId}/download`
}

export interface Integration {
  id: string
  provider: 'mail' | 'feishu'
  label: string
  owner_handle: string
  config: Record<string, unknown>
  grants: string[]
  status: 'ok' | 'auth_failed' | 'unreachable' | 'error'
  last_error: string
  last_checked_at: string | null
  user_authorized: boolean
  /** 用的是平台管理员配的那一个应用，而不是这条连接自己带的凭据。 */
  shared_app: boolean
}

export interface MailDraft {
  id: string
  integration_id: string
  project_id: string
  topic_id: string | null
  created_by: string
  to: string[]
  cc: string[]
  subject: string
  body: string
  attachments: { path: string; name: string; size: number }[]
  status: 'drafted' | 'sent' | 'failed' | 'discarded'
  error: string
  sent_at: string | null
  created_at: string | null
}

export function listMyIntegrations(): Promise<ListPayload<Integration>> {
  return request<ListPayload<Integration>>('/me/integrations')
}

export function connectMail(body: Record<string, unknown>): Promise<Integration> {
  return request<Integration>('/me/integrations/mail', { method: 'POST', body: JSON.stringify(body) })
}

export function updateIntegration(id: string, body: Record<string, unknown>): Promise<Integration> {
  return request<Integration>(`/me/integrations/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function checkIntegration(id: string): Promise<Integration> {
  return request<Integration>(`/me/integrations/${encodeURIComponent(id)}/check`, { method: 'POST' })
}

export function deleteIntegration(id: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/me/integrations/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function listMyMailDrafts(status: string): Promise<ListPayload<MailDraft>> {
  return request<ListPayload<MailDraft>>(`/me/mail-drafts?status=${encodeURIComponent(status)}`)
}

export function sendMailDraft(id: string): Promise<{ draft: MailDraft; refused: string[]; notes: string[] }> {
  return request<{ draft: MailDraft; refused: string[]; notes: string[] }>(
    `/me/mail-drafts/${encodeURIComponent(id)}/send`,
    { method: 'POST' }
  )
}

export function discardMailDraft(id: string): Promise<MailDraft> {
  return request<MailDraft>(`/me/mail-drafts/${encodeURIComponent(id)}/discard`, { method: 'POST' })
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
  bytes: number | null
  room: { id: string; title: string; title_source?: string } | null
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
  throw new Error(message || t('global.request.versionReadFailed', { status: response.status }))
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
    throw new Error(refusalWords(envelope) || t('global.request.addFailed', { status: res.status }))
  }
  return envelope.data
}

// Upload a file into the project's 资料库, with a copy in this room.
//
// The body goes up over XHR so the composer can draw a determinate bar from its
// progress — `fetch` has no upload-progress event (see lib/xhrUpload.ts).
//
// `origin: 'clipboard'` 的那一份只留在这个房间：贴进来的截图没有名字（`image.png`
// 是浏览器编的），而资料库是按名字寻址的 —— 见 attachments.ts 里给它现起的名字。
export async function uploadAttachment(
  topicId: string,
  file: File,
  origin: 'file' | 'clipboard' = 'file',
  onProgress?: (fraction: number) => void
): Promise<ChatAttachment> {
  const form = new FormData()
  form.append('file', file)
  form.append('origin', origin)
  const url = `${BASE}/topics/${encodeURIComponent(topicId)}/attachments`
  const { status, body } = await postFormWithProgress<ApiEnvelope<ChatAttachment>>(url, form, authHeaders(), onProgress)
  if (status < 200 || status >= 300 || !body || body.code !== 200)
    throw new Error(refusalWords(body) || t('global.request.uploadFailed', { status }))
  return body.data
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
  if (!res.ok) throw new Error(t('global.request.imageFailed', { status: res.status }))
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
  if (!res.ok) throw new Error(t('global.request.fileReadFailed', { status: res.status }))
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
export { PreviewRendererUnavailable } from './lib/previewPdf'

/** Bytes and source fingerprint from the same authorized conversion response. */
export const previewDocumentPdfSnapshot = createPreviewPdfReader(BASE, authHeaders)

/** A Word or PowerPoint file converted to PDF, so a browser can draw it. */
export async function previewDocumentPdf(
  topicId: string,
  path: string,
  task?: string | null,
  source: FileSource = 'live'
): Promise<ArrayBuffer> {
  return (await previewDocumentPdfSnapshot(topicId, path, task, source)).bytes
}

// Downloads carry the same credentials as API requests, including token-only sessions.
export async function downloadFile(rawUrl: string, filename: string): Promise<void> {
  const res = await fetch(`${rawUrl}${rawUrl.includes('?') ? '&' : '?'}download=true`, { headers: authHeaders() })
  if (!res.ok) throw new Error(t('global.request.downloadFailed', { status: res.status }))
  const url = URL.createObjectURL(await res.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// 项目总览的自动区 (#1889): the overview room's ②③, structured so the doc
// panel can render them below the body and make each line clickable. Only the
// project's root topic has one — any other room answers 404 — and the caller
// must be able to read the room, same as the doc itself.
export function getOverviewAuto(topicId: string): Promise<OverviewAuto> {
  return request<OverviewAuto>(`/topics/${encodeURIComponent(topicId)}/overview`)
}

// 进度层 (#187): 芝士's checklist as of the last turn that touched this topic.
// Read on topic open — between turns there is no WS stream to carry it, and
// "做到哪了" has to be visible without summoning anyone. `items` is [] for a
// topic that never had a checklist. With `taskId`, that card's list — the one
// its 分身 wrote — instead of the room's.
export function getProgress(topicId: string, taskId?: string): Promise<TopicProgress> {
  const q = taskId ? `?task=${encodeURIComponent(taskId)}` : ''
  return request<TopicProgress>(`/topics/${encodeURIComponent(topicId)}/progress${q}`)
}

// A document's top-level blocks (heading/paragraph/list/…), in order: which
// passage each comment is aligned to.
export function getDocNodes(documentId: string): Promise<{ data: Block[]; total: number }> {
  return request<{ data: Block[]; total: number }>(`/documents/${encodeURIComponent(documentId)}/nodes`)
}

/** Start a comment thread on the words `quote` (or on the whole document without them). */
export function addComment(documentId: string, content: string, quote?: string): Promise<DocComment> {
  return request<DocComment>(`/documents/${encodeURIComponent(documentId)}/comments`, {
    method: 'POST',
    body: JSON.stringify({ content, quote: quote || undefined }),
  })
}

// 周报集 (spec §7.1): the project's weekly reports, newest first. Each Block
// carries the stretch it covers in `meta` (`since`/`until`) and points back to
// the room it was written in via `topic_id`.
export function getProjectWeeklies(projectId: string): Promise<ListPayload<Block>> {
  return request<ListPayload<Block>>(`/projects/${encodeURIComponent(projectId)}/weeklies`)
}

// 叫芝士现在就读它还没读到的消息（一轮失败之后的「重试」）。不发新消息 —— 那些
// 消息已经在时间线上了，补一条一模一样的只会让人分不清哪条是真的。
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
//
// `author` narrows a page to one teammate's steps — a room can seat several, and
// 现场 reads them one at a time. It narrows the query, not the page: the filter
// runs inside the paging (same as the backend's `kinds`), so a page still holds
// `limit` rows and `has_more` is about what is left for THAT teammate.
export const SITE_PAGE_SIZE = 120
export function getTranscript(
  topicId: string,
  opts: { limit?: number; before?: string; author?: string | null } = {}
): Promise<SitePage> {
  const q = new URLSearchParams()
  if (opts.limit != null) q.set('limit', String(opts.limit))
  if (opts.before) q.set('before', opts.before)
  if (opts.author) q.set('author', opts.author)
  const qs = q.toString()
  return request<SitePage>(`/topics/${encodeURIComponent(topicId)}/transcript${qs ? `?${qs}` : ''}`)
}

// 现场一步打印出来的东西：后端只留末尾一截（至多 8 KiB，凭据已抹掉）。列表和
// socket 上只带它有多长（`meta.output_bytes`），摊开那一步时才来取这一份。
export function getStepOutput(topicId: string, blockId: string): Promise<{ output: string; bytes: number }> {
  return request<{ output: string; bytes: number }>(
    `/topics/${encodeURIComponent(topicId)}/transcript/${encodeURIComponent(blockId)}/output`
  )
}

export type { AgentControlResult, AgentControlState } from './api/agentControl'
export { getAgentControl, sendAgentControl } from './api/agentControl'
export { requestPreviewSession } from './api/preview'
export type { PreviewSelection, PreviewSession } from './types/preview'

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

// 作废：结束一张未决的卡，不合并也不退回。作废人由后端从会话认定；验收人、
// 项目所有者、团队管理员能作废（server-side）。
export function voidCard(cardId: string, note: string): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/void`, {
    method: 'POST',
    body: JSON.stringify({ note }),
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

// 把一位外部成员移出项目。只有外部成员能这样移出——团队成员的去留在团队里定。后端
// 在服务层判「谁能移」，并且**不认**请求体里自称的 handle：身份从 token 解析。
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

// ---- 外部成员：只能点名邀请，本人接受才进来 ----------------------------------
// 团队里的人自动就在团队的每个项目里；项目自己能加的只有团队以外的人。进来之后他
// 看得见这个项目的话题，所以要由他本人点头——邀请发出去，他接受了才算数。

export function inviteExternalMember(projectId: string, handle: string): Promise<ProjectInvitation> {
  return request<ProjectInvitation>(`/projects/${encodeURIComponent(projectId)}/invitations`, {
    method: 'POST',
    body: JSON.stringify({ user_handle: handle }),
  })
}

export interface LookedUpUser {
  handle: string
  name: string
  avatar_id: number | null
}

// 按用户名或邮箱**精确**找一个人（像飞书加外部联系人那样）：只认完整的用户名或邮箱，
// 不做模糊搜索——邀请是把人放进项目的动作，「搜出来一串相似的名字再挑」正是加错人
// 的来路。找不到是 404。
export function lookupUser(q: string): Promise<LookedUpUser> {
  return request<LookedUpUser>(`/users/lookup?q=${encodeURIComponent(q)}`)
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

// 加人、改角色、移出在 `api/topicMembers.ts`：它们写成功要通知手上有名册副本的地方。
export { addTopicMember, removeTopicMember, updateTopicMemberRole } from './api/topicMembers'

// 一个 id 指向一个房间。任务的 id 问这条接口是 404，任务走 `api/tasks.ts`
// 的 `getRoomTask`（房间的地址 + 任务的 id）。
export function getTopic(topicId: string): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}`)
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
  status: { received: number; in_progress: number; resolved: number; deployed: number; declined: number }
  /** 这个管理员自己的未读数 —— 人各一份，和板子有多大无关。 */
  unread: number
  /** 长度恒等于 `days`、最早的一天在前。缺的那天是 0，不是一段缺口。 */
  series: { date: string; created: number; resolved: number; deployed: number }[]
  /** 上一等长窗口（`[since-days, since)`）的同口径合计 —— KPI 卡的环比差从这里出。
   *  可选：旧后端还没有它，前端按「键在才画 delta」接线。 */
  prev?: { created: number; resolved: number }
}

/** 用量那一块。`unpriced_tokens` 与 `cost_usd` **一起读才对**：前者是「这些 token
 *  算不出价钱」（模型没有单价，行上的 0 是「没有价」不是「免费」），少了它，几百万
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
    /** 同一行上的「算不出价钱」的那部分。0 是「没有价」不是「免费」。 */
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
  /** 投递账本积压。 */
  reliability: {
    delivery_unsent: number
    delivery_dead_letters: number
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

export type GatewayCapabilities = Partial<
  Record<'reasoning' | 'vision' | 'adaptive_thinking' | 'mid_conversation_system', boolean>
>

export interface GatewayUpstream {
  model: string
  host: string
  provider: string
}

/** 清单里的一个模型（契约 §3.1）。
 *
 *  `origin` 决定它是只读还是可改：`config` 来自 config.yaml、页面上只读；`runtime` 是
 *  网关里新增的，可改可删可停用。`offered = selectable && priced && !blocked`，是选择器
 *  真正会给出的那些；`blocked_reasons` 是没上架的原因码，`unpriced_reason` 是网关的说明。 */
export interface GatewayModelInfo {
  name: string
  model_id: string
  label: string
  origin: 'config' | 'runtime'
  blocked: boolean
  selectable: boolean
  priced: boolean
  offered: boolean
  blocked_reasons: string[]
  unpriced_reason: string | null
  upstream: GatewayUpstream
  prices: GatewayPrices
  capabilities: GatewayCapabilities
  usage: GatewayUsageNumbers
  /** 仅 origin=config 时给出：可复制的 config.yaml 片段（页面「怎么改」那一段）。 */
  config_yaml?: string
  /** 行内 sparkline 的逐日 token（与详情折线同源同账）；窗口内没用过是逐日 0。 */
  series?: number[]
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
  // token rides as ?token=. The socket only carries what lands in the room.
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

// ---- 房间文件：草稿历史与在线编辑 ----

/** 房间文件保存过的一版。`source` 说字节从哪条路进来：编辑器、芝士、恢复、上传。 */
export interface RoomFileRevision {
  id: string
  path: string
  seq: number
  version: string
  size: number
  author: string
  author_kind: 'human' | 'agent' | 'unknown'
  source: 'baseline' | 'upload' | 'ai' | 'editor' | 'restore' | 'scheduled'
  note: string | null
  /** 存下这一版的那次编辑会话；和自己打开时的那个相同，就是自己刚存的。 */
  editor_key: string | null
  created_at: string
}

export function roomFileRevisions(
  topicId: string,
  path: string
): Promise<{ data: RoomFileRevision[]; total: number; path: string; version: string | null }> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/revisions?path=${encodeURIComponent(path)}`)
}

export function restoreRoomFileRevision(topicId: string, revisionId: string): Promise<RoomFileRevision> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/revisions/${encodeURIComponent(revisionId)}/restore`, {
    method: 'POST',
  })
}

export async function downloadRoomFileRevision(topicId: string, revision: RoomFileRevision): Promise<void> {
  const res = await fetch(
    `${BASE}/topics/${encodeURIComponent(topicId)}/files/revisions/${encodeURIComponent(revision.id)}/raw`,
    { headers: authHeaders() }
  )
  if (!res.ok) throw new Error(t('global.request.downloadFailed', { status: res.status }))
  const blob = await res.blob()
  const leaf = revision.path.split('/').pop() ?? 'file'
  const dot = leaf.lastIndexOf('.')
  const [base, ext] = dot > 0 ? [leaf.slice(0, dot), leaf.slice(dot)] : [leaf, '']
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = t('global.request.revisionFileName', { name: base, seq: revision.seq, ext })
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function copyIntoRoom(
  topicId: string,
  source: string,
  path: string
): Promise<{ path: string; version: string }> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/copy`, {
    method: 'POST',
    body: JSON.stringify({ source, path }),
  })
}

/** 打开编辑器要的那份签过名的配置。`enabled` 为假时 `reason` 是为什么打不开的码。 */
export interface RoomFileEditorSession {
  enabled: boolean
  reason?: 'not_configured' | 'unsupported' | 'library_original'
  copyable?: boolean
  editable?: boolean
  api_url?: string
  version?: string
  config?: Record<string, unknown>
}

export function openRoomFileEditor(topicId: string, path: string): Promise<RoomFileEditorSession> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/editor?path=${encodeURIComponent(path)}`)
}

/** 平台的一份标准模板：从它新建的是一份带样式和【占位】的 Office 文件。 */
export interface DocumentTemplate {
  id: string
  name: string
  suffix: 'docx' | 'pptx' | 'xlsx'
  about: string
}

export function listDocumentTemplates(topicId: string): Promise<{ data: DocumentTemplate[]; total: number }> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/templates`)
}

export function newFromTemplate(
  topicId: string,
  template: string,
  path: string
): Promise<{ path: string; version: string }> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/new`, {
    method: 'POST',
    body: JSON.stringify({ template, path }),
  })
}
