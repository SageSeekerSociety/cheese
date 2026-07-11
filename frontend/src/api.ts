// REST helpers for the CheeseX backend. All responses are wrapped in an
// ApiEnvelope; these helpers unwrap `data` and surface non-200 codes as errors.
import type {
  AcceptCard,
  ApiEnvelope,
  Block,
  ChatAttachment,
  ComputeProfiles,
  Contributions,
  ExecProfiles,
  ExpertRole,
  FileContent,
  GitCommit,
  InboxItem,
  ListPayload,
  MarketNodes,
  MarketPools,
  MarketTask,
  Me,
  MemberSummary,
  MilestoneFull,
  Notification,
  PreviewInfo,
  Project,
  ProjectCredits,
  ProjectMemberRow,
  ProjectOverview,
  ReactionAgg,
  SandboxImageInfo,
  Space,
  SpaceDashboard,
  TaskApplication,
  Topic,
  TopicMemberRow,
  UpstreamInfo,
  UpstreamSyncResult,
  UsageStats,
  UserProfile,
  WorkspaceFile,
} from './cx_types'

export const BASE = '/api'

// P1 真鉴权: read the signed session token straight from storage (avoids an
// import cycle with me.ts). Sent as `Authorization: Bearer` so the backend
// resolves the actor from a verified token instead of a forgeable body field.
// Empty when signed out or for an older pre-token cached identity.
export function authToken(): string {
  try {
    const raw = localStorage.getItem('cheesex.me')
    return raw ? (JSON.parse(raw)?.token ?? '') : ''
  } catch {
    return ''
  }
}

function authHeaders(): Record<string, string> {
  const token = authToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// ---- Fusion merge (A5): the original 知是 product API lives at the ROOT
// (no /api prefix) and is authed by the real access token (account.ts, stored
// under 'accessToken'). These helpers let our shell log in with a real account
// and read the product surfaces (spaces/tasks/teams). ----
export function productToken(): string {
  return localStorage.getItem('accessToken') ?? ''
}

async function productRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const token = productToken()
  const res = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  })
  const envelope = (await res.json()) as { code: number; message: string; data: T }
  if (envelope.code !== 200 && envelope.code !== 201) {
    throw new Error(envelope.message || `product API error ${envelope.code}`)
  }
  return envelope.data
}

export interface ProductSpace {
  id: number
  name: string
  intro?: string
  description?: string
  avatar?: string
}

export interface ProductTeam {
  id: number
  name: string
  intro?: string
}

/** Real login against the product auth (username/password). Returns the token +
 * user; the caller stores it via account.ts so product calls are authed. */
export function loginProduct(
  username: string,
  password: string,
): Promise<{ accessToken: string; user: { id: number; username: string; nickname?: string } }> {
  return productRequest('/users/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

export function listProductSpaces(): Promise<{ spaces: ProductSpace[] }> {
  return productRequest('/spaces?pageStart=0&pageSize=50')
}

export function listProductTeams(): Promise<{ teams: ProductTeam[] }> {
  return productRequest('/teams?pageStart=0&pageSize=50')
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...(init?.headers ?? {}),
    },
  })
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} for ${path}`)
  }
  const envelope = (await res.json()) as ApiEnvelope<T>
  if (envelope.code !== 200) {
    throw new Error(envelope.message || `API error code ${envelope.code}`)
  }
  return envelope.data
}

// The connector lives at the origin root (`/connector/*`), not under `/api`, and its
// responses are plain JSON (no ApiEnvelope). This mirrors `request` but skips the
// `/api` prefix + envelope unwrap. Still sends the Bearer token for owner-gated routes.
async function connectorRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
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

// Approve a pending device flow (the `/connect` page): binds the machine to the
// logged-in human as owner, mints its agent, optionally assigns it to a project.
export function connectDevice(
  deviceCode: string,
  projectId?: string,
): Promise<import('./cx_types').DeviceApproval> {
  return connectorRequest<import('./cx_types').DeviceApproval>('/connect', {
    method: 'POST',
    body: JSON.stringify({
      device_code: deviceCode,
      project_id: projectId ?? null,
    }),
  })
}

// 「我的设备」: the machines the signed-in human enrolled, with liveness + agents.
export function listMyDevices(): Promise<{
  devices: import('./cx_types').MyDevice[]
}> {
  return connectorRequest<{ devices: import('./cx_types').MyDevice[] }>(
    '/my/devices',
  )
}

export function renameMyDevice(
  deviceId: string,
  name: string,
): Promise<import('./cx_types').MyDevice> {
  return connectorRequest<import('./cx_types').MyDevice>(
    `/my/devices/${encodeURIComponent(deviceId)}`,
    { method: 'PATCH', body: JSON.stringify({ name }) },
  )
}

export function unbindMyDevice(
  deviceId: string,
): Promise<{ deleted: boolean; device_id: string }> {
  return connectorRequest<{ deleted: boolean; device_id: string }>(
    `/my/devices/${encodeURIComponent(deviceId)}`,
    { method: 'DELETE' },
  )
}

// Absolute WS URL for a device screen's 现场 (read-only terminal). The session token
// rides as ?token= (browsers can't set an Authorization header on a WebSocket); the
// backend authorizes the viewer against the screen's project/topic membership.
export function screenWsUrl(sid: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const token = authToken()
  const q = token ? `?token=${encodeURIComponent(token)}` : ''
  return `${proto}://${window.location.host}/connector/session/${encodeURIComponent(
    sid,
  )}/screen${q}`
}

export function listProjects(): Promise<ListPayload<Project>> {
  return request<ListPayload<Project>>('/projects')
}

export function createProject(
  name: string,
  ownerHandle?: string,
): Promise<Project> {
  return request<Project>('/projects', {
    method: 'POST',
    body: JSON.stringify({ name, owner_handle: ownerHandle }),
  })
}

// Single project card (includes `summary`, the 一页纸总结).
export function getProject(projectId: string): Promise<Project> {
  return request<Project>(`/projects/${encodeURIComponent(projectId)}`)
}

// 芝士 (re)generates the project's 一页纸总结. Takes a few seconds.
export function generateSummary(projectId: string): Promise<{ summary: string }> {
  return request<{ summary: string }>(
    `/projects/${encodeURIComponent(projectId)}/summary`,
    { method: 'POST' },
  )
}

// The member's 1:1 private chat with 芝士 (a normal Topic; open chat WS on its id).
export function getPrivateChat(
  projectId: string,
  userHandle: string,
): Promise<Topic> {
  return request<Topic>(
    `/projects/${encodeURIComponent(projectId)}/private-chat?user_handle=${encodeURIComponent(
      userHandle,
    )}`,
  )
}

// 成员页 / portfolio (spec §7.2): what a member started + what awaits them.
export function getMemberSummary(
  projectId: string,
  handle: string,
): Promise<MemberSummary> {
  return request<MemberSummary>(
    `/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(
      handle,
    )}/summary`,
  )
}

// 极简登录 (Phase 0): get-or-create by handle, no password.
export function login(handle: string, name = ''): Promise<Me> {
  return request<Me>('/users/login', {
    method: 'POST',
    body: JSON.stringify({ handle, name }),
  })
}

export function listUsers(): Promise<ListPayload<Me>> {
  return request<ListPayload<Me>>('/users')
}

// 个人主页 / LinkedIn-GitHub profile (spec §1, §7.2). Cross-project résumé:
// header + skills/interests + 芝士 understanding + per-project contributions.
export function getUserProfile(handle: string): Promise<UserProfile> {
  return request<UserProfile>(`/users/${encodeURIComponent(handle)}/profile`)
}

export function listTopics(projectId: string): Promise<ListPayload<Topic>> {
  return request<ListPayload<Topic>>(
    `/topics?project_id=${encodeURIComponent(projectId)}`,
  )
}

export function createTopic(
  projectId: string,
  title: string,
  parentId?: string,
): Promise<Topic> {
  const body: Record<string, string> = { project_id: projectId, title }
  if (parentId) body.parent_id = parentId
  return request<Topic>('/topics', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// ---- 话题级未读 (Feishu-style badges) ----

// {topic_id: unread_count} for one user; topics with zero unread are omitted.
export function getTopicUnread(
  projectId: string,
  handle: string,
): Promise<Record<string, number>> {
  return request<Record<string, number>>(
    `/projects/${encodeURIComponent(projectId)}/topic-unread?handle=${encodeURIComponent(handle)}`,
  )
}

// Opening a topic bumps the user's read cursor (clears its badge).
export function markTopicRead(
  topicId: string,
  handle: string,
): Promise<Record<string, string>> {
  return request<Record<string, string>>(
    `/topics/${encodeURIComponent(topicId)}/read`,
    { method: 'POST', body: JSON.stringify({ handle }) },
  )
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

// Split a topic into a sub-topic (芝士的分身 works there). eval A1 / tree.
export function splitTopic(
  topicId: string,
  title: string,
  createdBy: string,
): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}/split`, {
    method: 'POST',
    body: JSON.stringify({ title, created_by: createdBy }),
  })
}

// Upgrade a message block into its own topic (eval A1). `blockId` is the
// message's block id. Returns the newly created topic.
export function upgradeBlock(
  blockId: string,
  createdBy: string,
): Promise<Topic> {
  return request<Topic>(`/blocks/${encodeURIComponent(blockId)}/upgrade`, {
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
  return request<ProjectCredits>(
    `/projects/${encodeURIComponent(projectId)}/credits`,
  )
}

// ---- 题目匹配市场 (spec §13 阶段 6) ----

// Published 题目 (Task Templates), optionally keyword-filtered.
export function getMarketTasks(q?: string): Promise<ListPayload<MarketTask>> {
  const query = q ? `?q=${encodeURIComponent(q)}` : ''
  return request<ListPayload<MarketTask>>(`/market/tasks${query}`)
}

// 应征: apply with one of your projects. Idempotent per (题目, project).
export function applyMarketTask(
  templateId: string,
  projectId: string,
  pitch: string,
): Promise<TaskApplication> {
  return request<TaskApplication>(
    `/market/tasks/${encodeURIComponent(templateId)}/apply`,
    { method: 'POST', body: JSON.stringify({ project_id: projectId, pitch }) },
  )
}

// Space side: who applied to this 题目.
export function listTaskApplications(
  templateId: string,
): Promise<ListPayload<TaskApplication>> {
  return request<ListPayload<TaskApplication>>(
    `/market/tasks/${encodeURIComponent(templateId)}/applications`,
  )
}

// Accept/decline an 应征. Accept creates the Task + link and notifies the team.
export function decideTaskApplication(
  applicationId: string,
  decision: 'accept' | 'decline',
  decidedBy: string,
): Promise<TaskApplication> {
  return request<TaskApplication>(
    `/market/applications/${encodeURIComponent(applicationId)}/${decision}`,
    { method: 'POST', body: JSON.stringify({ decided_by: decidedBy }) },
  )
}

// AI 模型池: the project's current profile + the ones it may select.
export function getExecutionProfiles(projectId: string): Promise<ExecProfiles> {
  return request<ExecProfiles>(
    `/projects/${encodeURIComponent(projectId)}/execution-profiles`,
  )
}
export function setExecutionProfile(
  projectId: string,
  profile: string,
): Promise<{ current: string }> {
  return request(`/projects/${encodeURIComponent(projectId)}/execution-profile`, {
    method: 'PUT',
    body: JSON.stringify({ profile }),
  })
}

// 算力池: the project's current compute pool + the deployed ones it may select.
export function getComputeProfiles(projectId: string): Promise<ComputeProfiles> {
  return request<ComputeProfiles>(
    `/projects/${encodeURIComponent(projectId)}/compute-profiles`,
  )
}
export function setComputeProfile(
  projectId: string,
  profile: string,
): Promise<{ current: string }> {
  return request(`/projects/${encodeURIComponent(projectId)}/compute-profile`, {
    method: 'PUT',
    body: JSON.stringify({ profile }),
  })
}

// 专家角色 (spec §8.2): merged catalog — built-in file-library roles + custom
// (DB) roles; a custom role shadows a built-in with the same name.
export function listRoles(): Promise<ListPayload<ExpertRole>> {
  return request<ListPayload<ExpertRole>>('/roles')
}
export function createRole(payload: {
  name: string
  title: string
  description: string
  body: string
  created_by?: string
}): Promise<ExpertRole> {
  return request<ExpertRole>('/roles', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}
// Which persona 芝士 loads for this project; empty role clears it.
export function setProjectExpertRole(
  projectId: string,
  role: string,
): Promise<{ current: string | null }> {
  return request(`/projects/${encodeURIComponent(projectId)}/expert-role`, {
    method: 'PUT',
    body: JSON.stringify({ role }),
  })
}

// 环境 (spec §9.1): which sandbox image runs this project's agent.
export function getSandboxImage(projectId: string): Promise<SandboxImageInfo> {
  return request<SandboxImageInfo>(
    `/projects/${encodeURIComponent(projectId)}/sandbox-image`,
  )
}
export function setSandboxImage(
  projectId: string,
  image: string,
): Promise<{ current: string | null }> {
  return request(`/projects/${encodeURIComponent(projectId)}/sandbox-image`, {
    method: 'PUT',
    body: JSON.stringify({ image }),
  })
}

// 上游仓库 (spec §6.3): link an existing git repo and pull its history in.
export function getUpstream(projectId: string): Promise<UpstreamInfo> {
  return request<UpstreamInfo>(
    `/projects/${encodeURIComponent(projectId)}/upstream`,
  )
}
export function setUpstream(
  projectId: string,
  url: string,
): Promise<UpstreamInfo> {
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

// 项目总览 / 收件箱 (eval G2/G3).
export function getOverview(projectId: string): Promise<ProjectOverview> {
  return request<ProjectOverview>(
    `/projects/${encodeURIComponent(projectId)}/overview`,
  )
}

export function getInbox(
  projectId: string,
  targetHandle: string,
): Promise<ListPayload<InboxItem>> {
  return request<ListPayload<InboxItem>>(
    `/projects/${encodeURIComponent(projectId)}/inbox?target_handle=${encodeURIComponent(
      targetHandle,
    )}`,
  )
}

export function markRead(notificationId: string): Promise<InboxItem> {
  return request<InboxItem>(
    `/notifications/${encodeURIComponent(notificationId)}/read`,
    { method: 'POST' },
  )
}

export function sendFeedback(
  notificationId: string,
  feedback: 'up' | 'down',
): Promise<InboxItem> {
  return request<InboxItem>(
    `/notifications/${encodeURIComponent(notificationId)}/feedback`,
    { method: 'POST', body: JSON.stringify({ feedback }) },
  )
}

// 机构看板 / Space 看板 (eval F3).
export function listSpaces(): Promise<ListPayload<Space>> {
  return request<ListPayload<Space>>('/spaces')
}

export function getSpaceDashboard(spaceId: string): Promise<SpaceDashboard> {
  return request<SpaceDashboard>(
    `/spaces/${encodeURIComponent(spaceId)}/dashboard`,
  )
}

export function listBlocks(topicId: string): Promise<ListPayload<Block>> {
  return request<ListPayload<Block>>(
    `/topics/${encodeURIComponent(topicId)}/blocks`,
  )
}

// Emoji reactions (Slack semantics): toggles (emoji, author) on a block and
// returns the block's fresh aggregate. Other clients get the same aggregate
// pushed as a `reaction` WS frame on the topic channel.
export function toggleReaction(
  blockId: string,
  emoji: string,
  author: string,
): Promise<{ toggled: 'added' | 'removed'; reactions: ReactionAgg[] }> {
  return request<{ toggled: 'added' | 'removed'; reactions: ReactionAgg[] }>(
    `/blocks/${encodeURIComponent(blockId)}/reactions`,
    { method: 'POST', body: JSON.stringify({ emoji, author }) },
  )
}

// ---- 图片输入 (chat image attachments) ----

// Upload a chat image into the topic's worktree. NOTE: raw fetch, not
// request() — multipart needs the browser to set the boundary header itself.
export async function uploadAttachment(
  topicId: string,
  file: File,
): Promise<ChatAttachment> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(
    `${BASE}/topics/${encodeURIComponent(topicId)}/attachments`,
    { method: 'POST', body: form, headers: authHeaders() },
  )
  const envelope = (await res.json().catch(() => null)) as
    | ApiEnvelope<ChatAttachment>
    | null
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
  return request<Block | null>(
    `/topics/${encodeURIComponent(topicId)}/doc`,
  )
}

// PUT upserts the living doc and appends a "📝 编辑了文档" event to the
// conversation. Returns the doc Block.
export function putDoc(
  topicId: string,
  content: string,
  author: string,
): Promise<Block> {
  return request<Block>(`/topics/${encodeURIComponent(topicId)}/doc`, {
    method: 'PUT',
    body: JSON.stringify({ content, author }),
  })
}

// B1: the living doc's structured node tree (heading/paragraph/list/…), in order.
// Each node has a stable id + the turn_id that produced it — used for cross-view
// highlight (B1 P2) and comment anchoring (B4).
export function getDocNodes(
  topicId: string,
): Promise<{ data: Block[]; total: number }> {
  return request(`/topics/${encodeURIComponent(topicId)}/docs`)
}

// 段落评论 (eval B4): inline comments, each anchored to a doc node via reply_to.
export function getComments(
  topicId: string,
): Promise<{ data: Block[]; total: number }> {
  return request(`/topics/${encodeURIComponent(topicId)}/comments`)
}

export function addComment(
  topicId: string,
  content: string,
  author: string,
  anchor?: string,
  quote?: string,
): Promise<Block> {
  return request<Block>(`/topics/${encodeURIComponent(topicId)}/comments`, {
    method: 'POST',
    body: JSON.stringify({ content, author, anchor, quote }),
  })
}

// 决策记录 (spec §7.1): the project's decision log. Each entry is a Block whose
// `topic_id` points back to the source topic where the decision was made.
export function getProjectDecisions(
  projectId: string,
): Promise<ListPayload<Block>> {
  return request<ListPayload<Block>>(
    `/projects/${encodeURIComponent(projectId)}/decisions`,
  )
}

// 选项问题 (cheese ask): one-click answer.
export function answerOptions(
  blockId: string,
  option: string,
  author: string,
): Promise<Block> {
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
export function listMemory(
  projectId: string,
  userHandle?: string,
): Promise<ListPayload<MemoryEntryOut>> {
  const u = userHandle ? `&user_handle=${encodeURIComponent(userHandle)}` : ''
  return request<ListPayload<MemoryEntryOut>>(
    `/memory?project_id=${encodeURIComponent(projectId)}${u}`,
  )
}
export function deleteMemory(entryId: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/memory/${encodeURIComponent(entryId)}`, {
    method: 'DELETE',
  })
}

// ---- 执行面板 (Phase 4 tool drawers) ----

// 现场 (施工现场): 芝士's messages + 🔧 event lines for a topic (read-only).
export function getTranscript(topicId: string): Promise<ListPayload<Block>> {
  return request<ListPayload<Block>>(
    `/topics/${encodeURIComponent(topicId)}/transcript`,
  )
}

// 现场实时终端 (施工现场): whether this topic has an embeddable read-only
// terminal (only under the tmux agent backend, container up) and its iframe URL.
export interface TerminalInfo {
  available: boolean
  backend: string
  url?: string
}
export function getTerminal(topicId: string): Promise<TerminalInfo> {
  return request<TerminalInfo>(
    `/topics/${encodeURIComponent(topicId)}/terminal`,
  )
}

// Git: commit log + working-tree diff for the project repo.
export function getGitLog(projectId: string): Promise<ListPayload<GitCommit>> {
  return request<ListPayload<GitCommit>>(
    `/projects/${encodeURIComponent(projectId)}/git/log`,
  )
}

export function getGitDiff(projectId: string): Promise<{ diff: string }> {
  return request<{ diff: string }>(
    `/projects/${encodeURIComponent(projectId)}/git/diff`,
  )
}

// 文件: list workspace files; read one file's content.
export function listFiles(
  projectId: string,
  topicId?: string | null,
): Promise<ListPayload<WorkspaceFile>> {
  const t = topicId ? `?topic=${encodeURIComponent(topicId)}` : ''
  return request<ListPayload<WorkspaceFile>>(
    `/projects/${encodeURIComponent(projectId)}/files${t}`,
  )
}

// <img src=…> URL for a workspace file (binary raw endpoint) — the 文件 panel
// shows images as images instead of Monaco-mangled bytes.
export function workspaceFileRawUrl(
  projectId: string,
  path: string,
  topicId?: string,
): string {
  const t = topicId ? `&topic=${encodeURIComponent(topicId)}` : ''
  return `${BASE}/projects/${encodeURIComponent(projectId)}/file/raw?path=${encodeURIComponent(path)}${t}`
}

export function readFile(
  projectId: string,
  path: string,
  topicId?: string | null,
): Promise<FileContent> {
  const t = topicId ? `&topic=${encodeURIComponent(topicId)}` : ''
  return request<FileContent>(
    `/projects/${encodeURIComponent(projectId)}/file?path=${encodeURIComponent(
      path,
    )}${t}`,
  )
}

// Save an edited workspace file (人改文件即指令). The agent reads the latest on
// its next turn, like 改文档即指令.
export function writeFile(
  projectId: string,
  path: string,
  content: string,
  topicId?: string | null,
): Promise<{ path: string }> {
  const t = topicId ? `?topic=${encodeURIComponent(topicId)}` : ''
  return request(`/projects/${encodeURIComponent(projectId)}/file${t}`, {
    method: 'PUT',
    body: JSON.stringify({ path, content }),
  })
}

// 预览 (spec §9.1): the artifact 芝士 pointed at as the topic's current preview,
// or null if none is set. Content is fetched separately via readFile.
export function getPreview(topicId: string): Promise<PreviewInfo | null> {
  return request<PreviewInfo | null>(
    `/topics/${encodeURIComponent(topicId)}/preview`,
  )
}

// 资源: aggregated token/cost usage for a topic and for the whole project.
export function getTopicUsage(topicId: string): Promise<UsageStats> {
  return request<UsageStats>(`/topics/${encodeURIComponent(topicId)}/usage`)
}

export function getProjectUsage(projectId: string): Promise<UsageStats> {
  return request<UsageStats>(
    `/projects/${encodeURIComponent(projectId)}/usage`,
  )
}

// ---- 采纳卡 / 验收 (eval C5/A3) ----

// Accept cards for a topic, newest first.
export function getAcceptCards(
  topicId: string,
): Promise<ListPayload<AcceptCard>> {
  return request<ListPayload<AcceptCard>>(
    `/topics/${encodeURIComponent(topicId)}/accept-card`,
  )
}

export function acceptCard(
  cardId: string,
  decidedBy: string,
): Promise<AcceptCard> {
  return request<AcceptCard>(
    `/accept-cards/${encodeURIComponent(cardId)}/accept`,
    { method: 'POST', body: JSON.stringify({ decided_by: decidedBy }) },
  )
}

export function rejectCard(
  cardId: string,
  decidedBy: string,
  note: string,
): Promise<AcceptCard> {
  return request<AcceptCard>(
    `/accept-cards/${encodeURIComponent(cardId)}/reject`,
    { method: 'POST', body: JSON.stringify({ decided_by: decidedBy, note }) },
  )
}

// 撤回采纳 (spec §6.3: 采纳可撤销). Revoke an accepted card → un-archives the
// topic. Only the accepter / owner / lead may revoke (enforced server-side).
export function revokeCard(
  cardId: string,
  decidedBy: string,
): Promise<AcceptCard> {
  return request<AcceptCard>(
    `/accept-cards/${encodeURIComponent(cardId)}/revoke`,
    { method: 'POST', body: JSON.stringify({ decided_by: decidedBy }) },
  )
}

// 改验收人 (spec §4.4: 任何成员都可以改推荐/加人). Reassign a pending card to
// another reviewer. The backend reuses the create schema, so we pass an empty
// routing_reason to keep the recommendation neutral on a manual reassign.
export function reassignCard(
  cardId: string,
  reviewerHandle: string,
): Promise<AcceptCard> {
  return request<AcceptCard>(
    `/accept-cards/${encodeURIComponent(cardId)}/reassign`,
    {
      method: 'POST',
      body: JSON.stringify({
        reviewer_handle: reviewerHandle,
        routing_reason: '',
      }),
    },
  )
}

// 主分支保护 (spec §4.4): record one approval toward the card's accept. The
// backend enforces "AI 不能投票" and per-person uniqueness.
export function approveCard(
  cardId: string,
  approverHandle: string,
): Promise<AcceptCard> {
  return request<AcceptCard>(
    `/accept-cards/${encodeURIComponent(cardId)}/approve`,
    {
      method: 'POST',
      body: JSON.stringify({ approver_handle: approverHandle }),
    },
  )
}

// 项目成员列表 (used by the 改验收人 menu). Returns {data:[{user_handle, role}]}.
export function listProjectMembers(
  projectId: string,
): Promise<ListPayload<ProjectMemberRow>> {
  return request<ListPayload<ProjectMemberRow>>(
    `/projects/${encodeURIComponent(projectId)}/members`,
  )
}

// ---- 话题成员名册 (群聊房间的地基, fusion-design §3) --------------------------
// Roster of a topic's group room. `actor` is the acting user's handle — no auth
// layer yet (agent-as-user is P1), so the backend authorizes mutations against
// the actor's topic role (owner/admin may manage the roster).

export function listTopicMembers(
  topicId: string,
): Promise<ListPayload<TopicMemberRow>> {
  return request<ListPayload<TopicMemberRow>>(
    `/topics/${encodeURIComponent(topicId)}/members`,
  )
}

export function addTopicMember(
  topicId: string,
  handle: string,
  role: string,
  actor: string,
): Promise<TopicMemberRow> {
  return request<TopicMemberRow>(
    `/topics/${encodeURIComponent(topicId)}/members`,
    { method: 'POST', body: JSON.stringify({ handle, role, actor }) },
  )
}

export function updateTopicMemberRole(
  topicId: string,
  handle: string,
  role: string,
  actor: string,
): Promise<TopicMemberRow> {
  return request<TopicMemberRow>(
    `/topics/${encodeURIComponent(topicId)}/members/${encodeURIComponent(handle)}`,
    { method: 'PUT', body: JSON.stringify({ role, actor }) },
  )
}

export function removeTopicMember(
  topicId: string,
  handle: string,
  actor: string,
): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(
    `/topics/${encodeURIComponent(topicId)}/members/${encodeURIComponent(
      handle,
    )}?actor=${encodeURIComponent(actor)}`,
    { method: 'DELETE' },
  )
}

// Re-fetch a single topic (after accept it becomes archived). There's no
// single-topic GET, so we pull the project's topic list and pick it out.
export async function getTopic(
  projectId: string,
  topicId: string,
): Promise<Topic | null> {
  const payload = await listTopics(projectId)
  return payload.data.find((t) => t.id === topicId) ?? null
}

// ---- 日历 / 里程碑 (§7.2) ----

// Upcoming milestones (already sorted by due date).
export function getCalendar(
  projectId: string,
): Promise<ListPayload<MilestoneFull>> {
  return request<ListPayload<MilestoneFull>>(
    `/projects/${encodeURIComponent(projectId)}/calendar`,
  )
}

// All milestones (any status), for showing done ones faded.
export function listMilestones(
  projectId: string,
): Promise<ListPayload<MilestoneFull>> {
  return request<ListPayload<MilestoneFull>>(
    `/projects/${encodeURIComponent(projectId)}/milestones`,
  )
}

// ---- 通知中心 (G2/G3) ----

// Project notifications addressed to a handle (badge counts unread = read_at null).
export function getNotifications(
  projectId: string,
  targetHandle: string,
): Promise<ListPayload<Notification>> {
  return request<ListPayload<Notification>>(
    `/projects/${encodeURIComponent(projectId)}/notifications?target_handle=${encodeURIComponent(
      targetHandle,
    )}`,
  )
}

// Server-side bell badge: unread, non-silent, visible to this user.
export function getNotificationUnreadCount(
  projectId: string,
  targetHandle: string,
): Promise<{ unread: number }> {
  return request<{ unread: number }>(
    `/projects/${encodeURIComponent(projectId)}/notifications/unread-count?target_handle=${encodeURIComponent(
      targetHandle,
    )}`,
  )
}

// 全部标记已读 (Feishu-style).
export function markAllNotificationsRead(
  projectId: string,
  targetHandle: string,
): Promise<{ marked: number }> {
  return request<{ marked: number }>(
    `/projects/${encodeURIComponent(projectId)}/notifications/read-all?target_handle=${encodeURIComponent(
      targetHandle,
    )}`,
    { method: 'POST' },
  )
}

export function markNotificationRead(
  notificationId: string,
): Promise<Notification> {
  return request<Notification>(
    `/notifications/${encodeURIComponent(notificationId)}/read`,
    { method: 'POST' },
  )
}

export function sendNotificationFeedback(
  notificationId: string,
  feedback: 'up' | 'down',
): Promise<Notification> {
  return request<Notification>(
    `/notifications/${encodeURIComponent(notificationId)}/feedback`,
    { method: 'POST', body: JSON.stringify({ feedback }) },
  )
}

// 拍板 (spec G2): resolve a decision request by choosing one of its options.
export function resolveNotification(
  notificationId: string,
  chosen: string,
): Promise<Notification> {
  return request<Notification>(
    `/notifications/${encodeURIComponent(notificationId)}/resolve`,
    { method: 'POST', body: JSON.stringify({ chosen }) },
  )
}

// ---- 记一笔 / 导入 (E1/E3) ----

// Ingest raw offline input; 芝士 digests it into a structured [活动] topic.
// Takes a few seconds. Returns whatever the digestion produced.
export function ingestActivity(
  projectId: string,
  text: string,
  author: string,
): Promise<Record<string, unknown>> {
  return request<Record<string, unknown>>(
    `/projects/${encodeURIComponent(projectId)}/activities`,
    { method: 'POST', body: JSON.stringify({ text, author }) },
  )
}

// ---- 贡献图 (§10.1) ----

export function getContributions(projectId: string): Promise<Contributions> {
  return request<Contributions>(
    `/projects/${encodeURIComponent(projectId)}/contributions`,
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
  return `${proto}://${window.location.host}/api/topics/${encodeURIComponent(
    topicId,
  )}/chat${q}`
}
