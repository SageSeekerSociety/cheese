// REST helpers for the CheeseX backend. All responses are wrapped in an
// ApiEnvelope; these helpers unwrap `data` and surface non-200 codes as errors.
import type {
  AcceptCard,
  ApiEnvelope,
  Block,
  ComputeProfiles,
  Contributions,
  ExecProfiles,
  FileContent,
  GitCommit,
  InboxItem,
  ListPayload,
  MarketPools,
  Me,
  MemberSummary,
  MilestoneFull,
  Notification,
  PreviewInfo,
  Project,
  ProjectMemberRow,
  ProjectOverview,
  SandboxImageInfo,
  Space,
  SpaceDashboard,
  Topic,
  UpstreamInfo,
  UpstreamSyncResult,
  UsageStats,
  UserProfile,
  WorkspaceFile,
} from './types'

const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
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

// ---- 执行面板 (Phase 4 tool drawers) ----

// 现场 (施工现场): 芝士's messages + 🔧 event lines for a topic (read-only).
export function getTranscript(topicId: string): Promise<ListPayload<Block>> {
  return request<ListPayload<Block>>(
    `/topics/${encodeURIComponent(topicId)}/transcript`,
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

// 项目成员列表 (used by the 改验收人 menu). Returns {data:[{user_handle, role}]}.
export function listProjectMembers(
  projectId: string,
): Promise<ListPayload<ProjectMemberRow>> {
  return request<ListPayload<ProjectMemberRow>>(
    `/projects/${encodeURIComponent(projectId)}/members`,
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
  return `${proto}://${window.location.host}/api/topics/${encodeURIComponent(
    topicId,
  )}/chat`
}
