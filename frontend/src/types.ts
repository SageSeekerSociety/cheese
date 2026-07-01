// Shared types matching the backend API contract (CheeseX Phase 0).

export interface Project {
  id: string
  name: string
  created_at: string
  // 一页纸总结 (may be empty until 芝士 generates it).
  summary?: string
  // The project's root topic (= 本体 / 大本营). Its living doc is the 章程.
  root_topic_id?: string
  [key: string]: unknown
}

export interface Topic {
  id: string
  project_id: string
  parent_id: string | null
  title: string
  kind: string
  status: string
  created_at: string
}

export type AuthorType = 'human' | 'ai' | 'system'

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
  turn_id?: string | null
  refs?: string[]
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

// WebSocket server -> client frames.
export type WsServerFrame =
  | { type: 'user_block'; block: Block }
  | { type: 'delta'; text: string }
  | { type: 'tool'; name: string; input: Record<string, unknown> }
  | { type: 'todo'; items: TodoItem[] }
  | { type: 'state'; resource: string }
  | { type: 'event_block'; block: Block }
  | { type: 'assistant_block'; block: Block }
  | { type: 'error'; message: string }
  | { type: 'done' }

// WebSocket client -> server frame. `summon` = @芝士: true asks the AI to
// reply, false (default) just posts the message (spec §7.1 默认不 @).
export interface WsClientMessage {
  type: 'message'
  content: string
  author: string
  summon: boolean
  reply_to?: string // B3: thread this message under another
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

// Aggregated token/cost usage (GET /topics/{id}/usage, /projects/{id}/usage).
export interface UsageStats {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  cost_usd: number
  turns: number
}

// ---- 采纳卡 / 验收 (eval C5/A3) ----

export type AcceptStatus =
  | 'pending'
  | 'accepted'
  | 'rejected'
  | 'revoked'
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
