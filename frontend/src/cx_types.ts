// Shared types matching the backend API contract (CheeseX Phase 0).

export interface Project {
  id: string
  name: string
  created_at: string
  // 一页纸总结 (may be empty until 芝士 generates it).
  summary?: string
  // The project's root topic (= 本体 / 大本营). Its living doc is the 章程.
  root_topic_id?: string
  // 专家角色 (spec §8.2): which persona 芝士 loads. Null = generic 芝士.
  expert_role?: string | null
  [key: string]: unknown
  /** 这个项目是从哪道赛题创建的（1.0 `task` 的整数 id）；不来自赛题时为 null。 */
  external_task_id?: number | null
}

// 专家角色 (spec §8.2): one entry of the merged catalog — built-in roles come
// from the file library (read-only), custom roles from the DB. The body is the
// persona system prompt.
export interface ExpertRole {
  name: string
  title: string
  description: string
  body: string
  builtin: boolean
  space_id?: string | null
  created_by?: string | null
}

export interface Topic {
  id: string
  project_id: string
  parent_id: string | null
  title: string
  kind: string
  status: string
  created_at: string
  // Any activity (a turn, a status flip) touches this — the sidebar's 右锚.
  updated_at?: string
  // Lifecycle markers (spec §6.3) — used by the 已归档 group ordering.
  accepted_by?: string | null
  accepted_at?: string | null
  archived_at?: string | null
  // 本轮是否在跑（TurnRunner, 内存态）——和 status/归档完全分开：一个话题可以
  // 是 active 且空闲，也可以是 active 且正在跑一轮。只有 list/get 话题时才带。
  running?: boolean
}

export type AuthorType = 'human' | 'ai' | 'system'

// One aggregated emoji reaction group on a block (Slack-style chip):
// e.g. {emoji: '✅', count: 2, authors: ['cheese', 'alice']}.
export interface ReactionAgg {
  emoji: string
  count: number
  authors: string[]
}

export interface BlockMeta {
  [key: string]: unknown
  tool?: string
  arg?: string
  platform?: boolean
  action?: string
  event_type?: string
  code?: string
  severity?: string
  title?: string
  retryable?: boolean
}

export interface Block {
  id: string
  topic_id: string
  kind: string
  author_type: AuthorType
  author: string
  content: string
  // 双树 + 引用 (spec §5): conversation tree, document tree, citations, and the
  // live link an upgraded block points to.
  reply_to?: string | null
  struct_parent?: string | null
  node_type?: string | null
  struct_order?: number | null
  anchor_quote?: string | null
  // Render-by-type: mimeType of an artifact block (set on kind=artifact).
  mime_type?: string | null
  turn_id?: string | null
  refs?: string[]
  // Structured event payload (kind=event): {tool, arg, platform} — the UI
  // translates/classifies from this; content is the baked-text fallback.
  meta?: BlockMeta | null
  // Aggregated emoji reactions (Slack chips), kept fresh by `reaction` frames.
  reactions?: ReactionAgg[]
  upgraded_to_topic_id?: string | null
  created_at: string
}

// Standard API envelope: {"code":200,"message":"ok","data":<payload>}
export interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}

// Paginated list payload: {data: T[], total: number}
export interface ListPayload<T> {
  data: T[]
  total: number
}

// A live working-log task item (芝士's TaskCreate/TaskUpdate, rendered as a
// real-time checklist in the in-progress message — process, not state).
export interface TodoItem {
  id: string
  subject: string
  status: 'pending' | 'in_progress' | 'completed'
}

// WebSocket server -> client frames. No token streaming: 芝士 speaks in
// discrete assistant_block messages (one per completed SDK message boundary).
export type WsServerFrame =
  | { type: 'user_block'; block: Block }
  // A block's reactions changed (someone toggled / 芝士's ✅ receipt landed).
  | { type: 'reaction'; block_id: string; reactions: ReactionAgg[] }
  | { type: 'tool'; name: string; input: Record<string, unknown> }
  | { type: 'todo'; items: TodoItem[] }
  | { type: 'state'; resource: string }
  | { type: 'event_block'; block: Block }
  | { type: 'assistant_block'; block: Block }
  // persisted=true → the failure already landed in the timeline as an event
  // block; the client must not double-show it as a floating banner.
  | { type: 'error'; message: string; persisted?: boolean; code?: string }
  | { type: 'done' }
  // Sent once on WS connect when a turn is already mid-stream on this topic,
  // so a re-entering client rebuilds the 正在思考 indicator.
  | { type: 'turn_active' }
  // A just-persisted block turned out to be a provider-error echo — remove it.
  | { type: 'retract_block'; block_id: string }
  // An existing block's data changed in place (e.g. an option question got
  // answered) — replace it in the timeline.
  | { type: 'block_updated'; block: Block }

// 图片输入: an uploaded worktree image the message carries. `path` comes from
// POST /topics/{id}/attachments; the WS frame only references it (no binary).
export interface ChatAttachment {
  path: string
  mime: string
}

// WebSocket client -> server frame. `summon` = @芝士: true asks the AI to
// reply, false (default) just posts the message (spec §7.1 默认不 @).
export interface WsClientMessage {
  type: 'message'
  content: string
  author: string
  summon: boolean
  reply_to?: string // B3: thread this message under another
  attachments?: ChatAttachment[] // 图片输入 (uploaded first, referenced here)
}

// ---- 项目总览 / 收件箱 (eval G2/G3) ----

// A lightweight topic reference used inside overview payloads.
export interface TopicRef {
  id: string
  title: string
  kind: string
}

export interface Milestone {
  title: string
  due_date: string | null
}

export interface ProjectMember {
  handle: string
  role: string
}

// GET /api/projects/{id}/members → {data:[{user_handle, role, name}], total}
export interface ProjectMemberRow {
  user_handle: string
  role: string
  name?: string
  [key: string]: unknown
}

// 话题成员名册 (fusion-design §3): a topic's group-room roster. Roles are
// owner/admin/member (distinct from ProjectMemberRow's lead/member/mentor);
// `agent` marks 芝士 (the AI member) so the UI can badge it.
export interface TopicMemberRow {
  id: string
  topic_id: string
  member_handle: string
  role: 'owner' | 'admin' | 'member'
  name?: string
  agent?: boolean
  created_at: string
}

// GET /api/projects/{id}/overview
export interface ProjectOverview {
  project_id: string
  name: string
  // 一页纸总结. The overview extends the project card, so it may carry summary.
  summary?: string
  topic_count: number
  // status -> count, e.g. {active: 3, archived: 1, draft: 0}
  topics_by_status: Record<string, number>
  // handle -> the items waiting on that person.
  waiting_on_you: Record<string, TopicRef[]>
  next_milestone: Milestone | null
  upcoming_milestones: Milestone[]
  members: ProjectMember[]
  [key: string]: unknown
}

// GET /api/projects/{id}/inbox?target_handle=
// A notification / decision request addressed to a handle.
export interface InboxItem {
  id: string
  kind: string
  title: string
  body: string
  target_handle: string
  source_handle: string | null
  read: boolean
  feedback: 'up' | 'down' | null
  created_at: string
  topic_id: string | null
  [key: string]: unknown
}

// ---- 机构看板 / Space 看板 (eval F3) ----

export interface Space {
  id: string
  name: string
  created_at?: string
  [key: string]: unknown
}

// One team (= project) row inside a space dashboard.
export interface SpaceTeam {
  project_id: string
  name: string
  // 一页纸总结 for this team (may be empty).
  summary?: string
  ai_mode: string
  owner_handle: string
  topic_count: number
  topics_by_status: Record<string, number>
  next_milestone: Milestone | null
  upcoming_milestones: Milestone[]
  last_activity_at?: string | null
  contributions?: { human: number; ai: number }
  [key: string]: unknown
}

export interface SpaceDashboard {
  space_id: string
  name: string
  teams: SpaceTeam[]
  [key: string]: unknown
}

// ---- 成员页 / portfolio (spec §7.2) ----

// A topic the member started, shown on their member page.
export interface MemberTopic {
  id: string
  title: string
  status: string
}

// GET /api/projects/{id}/members/{handle}/summary
export interface MemberSummary {
  handle: string
  role: string
  topics_started: MemberTopic[]
  topics_active?: MemberTopic[]
  weekly_contributions?: number
  waiting_on_you: TopicRef[]
  [key: string]: unknown
}

// ---- 个人主页 / LinkedIn-GitHub profile (spec §1, §7.2, §8.4) ----

// One project the user participates in, with their cross-project contribution.
export interface ProfileProject {
  project_id: string
  name: string
  role: string
  topics_started: number
  contributions: number
}

// GET /api/users/{handle}/profile — the cross-project résumé view.
// `understanding` = what 芝士 has learned about this person (个人记忆, §8.4).
export interface UserProfile {
  handle: string
  name: string
  bio: string
  interests: string[]
  skills: string[]
  projects: ProfileProject[]
  understanding: string[]
  [key: string]: unknown
}

// ---- 执行面板 (Phase 4 tool drawers) ----

// One git commit row (GET /projects/{id}/git/log).
export interface GitCommit {
  hash: string
  author: string
  message: string
}

// A file in the project workspace (GET /projects/{id}/files).
export interface WorkspaceFile {
  path: string
  bytes: number
}

// GET /projects/{id}/file?path=
export interface FileContent {
  path: string
  content: string
}

// GET /topics/{id}/preview (spec §9.1): the artifact 芝士 pointed at as the
// topic's current preview. Null when 芝士 hasn't set one.
export interface PreviewInfo {
  // kind=file → render the file's content; kind=app → iframe straight to the
  // running app the agent started in its container (url, live-resolved).
  kind?: 'file' | 'app'
  path: string
  mime: string | null
  url?: string | null
}

// Aggregated token/cost usage (GET /topics/{id}/usage, /projects/{id}/usage).
export interface UsageStats {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: number
  turns: number
  // Tokens that burned real capacity but carry NO USD price (subscription
  // routing is billed by the month). Non-zero means the cost figure is
  // incomplete — the panel says 未知 rather than printing $0.0000.
  unpriced_tokens: number
}

// ---- 采纳卡 / 验收 (eval C5/A3) ----

export type AcceptStatus =
  | 'pending'
  | 'accepted'
  | 'rejected'
  | 'revoked'
  | 'conflict'
  // 机器闸门 (eval C2): the project's check_command is running / failed.
  | 'pending_gate'
  | 'gate_failed'
  | string

// GET /topics/{id}/accept-card (list, newest first).
export interface AcceptCard {
  id: string
  topic_id: string
  reviewer_handle: string
  routing_reason: string
  status: AcceptStatus
  decided_by: string | null
  decided_at: string | null
  note: string
  created_at: string
  // 机器闸门: when the check passed + the tail of its output.
  gate_passed_at: string | null
  gate_output: string
  // 主分支保护 (spec §4.4): votes so far / votes needed.
  approvals: string[]
  approvals_required: number
  // 采纳 PR 化 (#188 §5.1): the real GitHub PR this card rides on, when the
  // platform opened one (flag-gated, best-effort).
  pr_number: number | null
  pr_url: string | null
}

// GET /topics/{id}/pr-checks — live CI state of the card's PR (display only).
export interface PrChecks {
  available: boolean
  reason?: string
  pr_number?: number
  pr_url?: string
  state?: string
  mergeable?: boolean | null
  checks?: { name: string; status: string; conclusion: string | null; url?: string }[]
}

// ---- 通知中心 (G2/G3) ----

// GET /projects/{id}/notifications?target_handle=
export interface Notification {
  id: string
  project_id: string
  topic_id: string | null
  level: string
  kind: string
  target_handle: string | null
  title: string
  body: string
  payload: Record<string, unknown>
  read_at: string | null
  feedback: 'up' | 'down' | null
  created_at: string
}

// ---- 日历 / 里程碑 (§7.2) ----

// GET /projects/{id}/calendar and /projects/{id}/milestones.
export interface MilestoneFull {
  id: string
  project_id: string
  title: string
  description: string
  due_date: string | null
  status: string
  source_topic_id: string | null
  auto_pinned: boolean
  created_at: string
}

// ---- 贡献图 (§10.1) ----

// GET /projects/{id}/contributions
export interface Contributions {
  by_author_type: { human: number; ai: number; system: number }
  by_author: Record<string, number>
}

// ---- 资源池市场 (design v3: AI 池 + 算力池) ----

// A pool listing in the 市场 catalog / a project's settings selector.
export interface PoolListing {
  kind: 'ai' | 'compute'
  id: string
  label: string
  tier: string
  price: string
  description: string
  available: boolean
  default: boolean
}

// GET /api/market/pools
export interface MarketPools {
  ai: PoolListing[]
  compute: PoolListing[]
}

// ---- 节点看板 (spec §9.1: where turns physically run) ----

// One configured compute node (local docker / remote cheesed) with liveness.
export interface MarketNode {
  id: string
  label: string
  kind: 'local' | 'remote'
  online: boolean
  // Whether turns currently run on this node (one provider at a time today).
  current: boolean
  active_turns: number
  detail: string
  description: string
}

// GET /api/market/nodes
export interface MarketNodes {
  nodes: MarketNode[]
  active_turns_total: number
  current_provider: string
}

// ---- 算力额度 (spec §9.1 机构提供算力 → real quotas) ----

// One credit grant (issued when the project linked an institutional task).
export interface ComputeGrantRow {
  id: string
  source_task_id: string | null
  credits_total: number
  credits_used: number
  created_at: string
}

// GET /projects/{id}/credits — unlimited=true means no grants (自治项目).
export interface ProjectCredits {
  unlimited: boolean
  credits_total: number
  credits_used: number
  credits_remaining: number
  grants: ComputeGrantRow[]
}

// ---- 题目匹配市场 (spec §13 阶段 6: Space 发布题目, 团队应征) ----

// GET /api/market/tasks — a published Task Template as a market listing.
export interface MarketTask {
  id: string
  space_id: string
  space_name: string
  name: string
  description: string
  resource_pack: Record<string, unknown>
  conditions: Array<Record<string, unknown>>
  default_role: string | null
  created_at: string
}

// An 应征 (team applies with a Project). status: pending → accepted | declined.
export interface TaskApplication {
  id: string
  template_id: string
  project_id: string
  project_name: string
  pitch: string
  status: 'pending' | 'accepted' | 'declined'
  decided_by: string | null
  decided_at: string | null
  task_id: string | null
  created_at: string
}

// A selectable AI execution profile (GET /projects/{id}/execution-profiles).
export interface ExecProfileOption {
  name: string
  label: string
  tier: string
  model: string
  available: boolean
}

// GET /projects/{id}/execution-profiles
export interface ExecProfiles {
  current: string
  profiles: ExecProfileOption[]
}

// GET /projects/{id}/compute-profiles
export interface ComputeProfiles {
  current: string
  profiles: PoolListing[]
}

export type ProjectMachineStatus =
  | 'provisioning'
  | 'starting'
  | 'running'
  | 'stopping'
  | 'stopped'
  | 'deleting'
  | 'deleted'
  | 'error'
  | 'unknown'

export type ProjectMachineAiStatus = 'disabled' | 'provisioning' | 'ready' | 'error' | 'unknown'

// A MicroCloud machine billed/audited through a project. Once enrolled, its device
// belongs to the project's team pool and is available to every project on that team.
export interface ProjectMachine {
  id: string
  project_id: string
  machine_id: number
  hostname: string
  login_user: string
  cores: number
  memory_mb: number
  disk_gb: number
  status: ProjectMachineStatus
  ip: string | null
  ai_mode: string
  ai_status: ProjectMachineAiStatus
  device_id: string | null
  enrolled_at: string | null
  enroll_error: string | null
  enroll_attempts: number
  enroll_max_attempts: number
  requested_by: string | null
  created_at: string
}

export interface ProjectMachineCreate {
  cores: number
  memoryMb: number
  diskGb: number
}

// GET /topics/{id}/compute-profile — a topic's session-level compute选择 (v4).
// `current` is effective (topic → project sticky → team default → platform);
// `locked` freezes the picker once the topic has run (session started);
// `inherited` = still following project/team/platform defaults (no own choice yet);
// `sticky` = project sticky if present, otherwise the team/platform default.
export interface TopicComputeProfile {
  current: string
  locked: boolean
  inherited: boolean
  sticky: string
  profiles: PoolListing[]
}

// GET /projects/{id}/sandbox-image (spec §9.1 environment): which image runs the
// project's agent. current=null → using the pool default base image.
export interface SandboxImageOption {
  image: string
  label: string
}
export interface SandboxImageInfo {
  current: string | null
  default: string
  options: SandboxImageOption[]
}

// 当前用户 (Phase 0 极简登录): what /users/login returns and what we keep locally.
export interface Me {
  id: string
  handle: string
  name: string
  // P1 真鉴权: signed session token minted at login. Sent as
  // `Authorization: Bearer` on every request (and as ?token= on the chat WS) so
  // the backend resolves the actor from a verified token, not a forgeable body
  // field. Optional so an older stored identity (pre-token) still type-checks.
  token?: string
}

// 上游仓库 (spec §6.3): a project can bind an existing git repo (关联已有 repo)
// and keep pulling its history in via 同步上游.
export interface UpstreamInfo {
  url: string | null
}
export interface UpstreamSyncResult {
  synced: boolean
  commits?: number
  reason?: string
}

// GitHub App install flow (#192): a project connects to one repo via
// cheesex-app, replacing the classic 上游仓库 URL entry for repos it manages.
export interface GithubConnection {
  connected: boolean
  repo?: string
  account?: string
}

// A user's OAuth/App connections — GET /users/{userId}/oauth/connections
// (list_user_connections). login/tokenExpires/hasRefreshToken (2026-08-09) are
// token-health metadata only; the raw access token is never sent to the client.
export interface OAuthConnectionInfo {
  id: number
  providerId: string
  providerName: string
  providerUserId: string
  connectedAt: string | null
  login: string | null
  tokenExpires: string | null
  hasRefreshToken: boolean
}

// ---- self-hosted 设备连接器 (P3 Phase B) ----

// One agent (a screen) currently running on an enrolled device — a live 现场 the
// browser can watch read-only via `screenWsUrl(sid)`.
export interface DeviceScreen {
  sid: string
  agent_handle: string
  agent_user_id: string
  project_id: string | null
  topic_id: string | null
}

// A compute machine (算力节点) the signed-in human enrolled. A device is pure compute
// — it has NO agent identity; the agents running on it are `screens` (each carries its
// own agent). See execution-architecture v3 / fusion-design §5.
export interface MyDevice {
  device_id: string
  name: string
  online: boolean
  project_ids: string[]
  // Teams this machine is registered for (为团队注册设备, v4): every project of
  // these teams may run on it.
  team_ids: number[]
  screens: DeviceScreen[]
}

// A team the signed-in user belongs to (GET /teams/my-teams) — trimmed to what
// the device pages need. Includes the auto-provisioned personal team (个人 =
// 单人真团队), which the backend sorts first and flags `personal`.
export interface MyTeam {
  id: number
  name: string
  personal?: boolean
}

// The result of approving a pending device flow (binds the machine to its owner).
export interface DeviceApproval {
  device_id: string
  device_name: string
  project_id: string | null
}
