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
  /** 这个项目是从哪道赛题创建的（1.0 `task` 的整数 id）；不来自赛题时为 null。 */
  external_task_id?: number | null
}

export interface Topic {
  id: string
  project_id: string
  parent_id: string | null
  title: string
  kind: string
  status: string
  created_at: string
  // 话题这一行自己被改过的时间（改标题、归档、拿到 session id）——落一块消息
  // 不会动它。要"这个话题最后有动静是什么时候"，看 last_activity_at。
  updated_at?: string
  // 最后活动时间 = 话题里最新一块的时间（没有块就是话题的创建时间）。侧栏的
  // 右锚和"最后活动"排序都用它。只有 list/get 话题时才带。
  last_activity_at?: string
  // Lifecycle markers (spec §6.3) — used by the 已归档 group ordering.
  accepted_by?: string | null
  accepted_at?: string | null
  archived_at?: string | null
  // 这个话题是从哪一块「升级」出来的（讨论升级 / 文档 🧩）。非空 = 它的来源 block
  // 上已经有一条「已升级为话题」的活引用了，时间线不必再标一次「已派出」。
  upgraded_from_block_id?: string | null
  // In-memory session activity, independent of topic status and archival state.
  // Present only on topic list/get responses.
  running?: boolean
  // 我和这个话题有没有关系：我在名册里 / 是我建的 / 我是验收人 / 我被 @ 过，
  // 四者取一。只有 list/get 话题时才带。
  i_participate?: boolean
  // 这个话题在等我做事：有点名给我的待办验收卡，或有 @我 的未读。为真时
  // i_participate 必然为真，所以「需要我行动的」只看这一个字段就够。
  // 只有 list/get 话题时才带。
  awaits_me?: boolean
  // 哪个 AI 队友在这个话题里工作。null = 跟着项目的默认走（不是「没有」），
  // 所以换了项目默认，这个话题也跟着换。
  agent_instance_id?: string | null
  // 这个房间在看板那套词里处在哪一列。侧栏房间行的色点读它。
  //
  // 和上面 `running` / `awaits_me` / `i_participate` 一样是「只有 list/get 话题时
  // 才带」的字段——`Topic` 同时也是私聊和项目本体的形状，那些地方没有列可言。所以
  // 拿不到就**不画点**，而不是退回前端自己算一个：一旦有了退路，两个算法会同时活
  // 着，而屏幕上那个颜色是哪一个算出来的，谁也说不清。
  presentation?: Presentation
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
  // How many times the living doc has been written (kind=doc). Send it back as
  // `expected_version` to save; the write is refused if the doc moved since.
  doc_version?: number
  turn_id?: string | null
  refs?: string[]
  // Structured event payload (kind=event): {tool, arg, platform} — the UI
  // translates/classifies from this; content is the baked-text fallback.
  meta?: BlockMeta | null
  // Aggregated emoji reactions (Slack chips), kept fresh by `reaction` frames.
  reactions?: ReactionAgg[]
  upgraded_to_topic_id?: string | null
  // 这一块被派成了哪条支线（房间里的「讨论升级」走这条）。两者只会有一个非空。
  upgraded_to_task_id?: string | null
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

// A working-log task item (芝士's TaskCreate/TaskUpdate, rendered as a checklist
// in the in-progress message). Live during a turn; persisted between turns as
// the topic's 进度层 (#187) so a new machine — and the room — can still see how
// far the work got.
export interface TodoItem {
  id: string
  subject: string
  status: 'pending' | 'in_progress' | 'completed'
}

// 进度层 (#187): the stored checklist for a topic. `updated_at` is null when the
// topic has never had one (items is then []).
// 一件活 —— 房间里的一条支线，不是话题树上的一个节点。房间有名册，一件活只有
// 唯一的主（`owner_handle`），那个差别就是它不再是房间的全部理由。
export interface RoomTask {
  id: string
  project_id: string
  // 它挂在哪个房间里。永远是房间——活不嵌套。
  room_id: string
  title: string
  status: string
  owner_handle?: string | null
  created_by?: string | null
  branch_name?: string | null
  // 派它出去时说的那份要求，和分身交回来的那句话。两样都住在卡上：简报以前存在
  // 「活自己的实况文档」里，而做活的分身拿的是房间的 token，够不着那个地址，
  // 于是那份文档从播种那一刻起就再没人改过。
  brief?: string
  conclusion?: string | null
  // 它干在哪一批上。一棵树 = 一个分支 = 一个 PR = 一批活，所以这是「我这条活最后
  // 会从哪个 PR 出去」的答案，也是总览把活和 PR 对上的唯一依据。
  tree_id?: string | null
  // 交付，和 `status` 不是同一个问题：活可以已交付但还开着，也可以关掉却什么都没交付。
  accepted_by?: string | null
  accepted_at?: string | null
  closed_at?: string | null
  upgraded_from_block_id?: string | null
  created_at: string
  updated_at: string
  // 项目级那条列表（`GET /projects/{id}/tasks`）和房间级那条（`GET
  // /topics/{id}/tasks`）都带它——「等人验收」也是安静的，没有它就和「闲着」
  // 在屏幕上长得一模一样。
  card?: ThreadCard | null
  // 这条活在看板上落哪一列、卡上写哪句话。**必有字段，不是可选的**：状态从今往后
  // 只在后端算一次，前端没有一条退回本地推导的路——留一条兜底路，两个算法就会同时
  // 存在，而且谁也说不清屏幕上那个词是哪一个算出来的。
  presentation: Presentation
}

/** 看板的一列。判据是「**该谁动**」，不是「事情进行到哪一步」——同一个客观事实，
 *  下一步在平台手上还是在人手上，落在不同的列里。
 *
 *    building   施工中 —— 还没递出交付
 *    delivering 交付中 —— 下一步在平台/芝士手上
 *    needs_you  等你   —— 下一步在人手上
 *    done       已完成 —— 已采纳，或已收工且没交付
 *    archived   已归档 —— 房间才有；活不归档
 */
export type BoardColumn = 'building' | 'delivering' | 'needs_you' | 'done' | 'archived'

/** 后端算好的呈现，前端照抄。
 *
 *  `display_status` 已经是可以直接显示的中文，**前端不再做第二张映射表**——这正是
 *  这个字段存在的理由。同一个客观事实（比如快检红了）在不同的列里是不同的话：平台
 *  自己在修时是「修复检查」，等人拍板时是「检查未通过」，所以短语属于列，一列只会
 *  产出属于它自己的那几个词。前端再映射一次，两边就会各说各的。 */
export interface Presentation {
  column: BoardColumn
  display_status: string
}

/** 一批活 —— 一棵树 = 一个分支 = 一个 PR。房间封口一批、开下一批，所以一个房间
 *  同时可以有好几棵，但只有一棵是 `open` 的。 */
export interface RoomTree {
  id: string
  status: 'open' | 'sealed' | 'merged'
  created_at: string
  sealed_at?: string | null
  merged_at?: string | null
  // 快检最后一次说了什么，关于这棵树现在的内容。它谁也不拦（#296 定了由 PR 上
  // 真的 CI 决定），在这里只是为了让红的那次被将要验收的人看见。
  last_check_at?: string | null
  last_check_ok?: boolean | null
  last_check_detail?: string
  card?: ThreadCard | null
}

/** 一条支线绑着的验收卡，窄到只剩一行侧栏放得下的东西：活到哪一步、骑在哪个 PR 上。 */
export interface ThreadCard {
  id: string
  status: string
  pr_number?: number | null
  pr_url?: string | null
}

export interface TopicProgress {
  items: TodoItem[]
  updated_at: string | null
}

// WebSocket server -> client frames. No token streaming: 芝士 speaks in
// discrete assistant_block messages (one per completed SDK message boundary).
export type WsServerFrame =
  | { type: 'user_block'; block: Block }
  // A block's reactions changed (someone toggled / 芝士's ✅ receipt landed).
  | { type: 'reaction'; block_id: string; reactions: ReactionAgg[] }
  | { type: 'tool'; name: string; input: Record<string, unknown> }
  // `restored` = this is the checklist a PREVIOUS turn left behind, replayed at
  // turn start; without the flag the UI cannot tell it from live progress.
  | { type: 'todo'; items: TodoItem[]; restored?: boolean }
  | { type: 'state'; resource: string }
  | { type: 'event_block'; block: Block }
  | { type: 'assistant_block'; block: Block }
  // persisted=true → the failure already landed in the timeline as an event
  // block; the client must not double-show it as a floating banner.
  | { type: 'error'; message: string; persisted?: boolean; code?: string }
  | { type: 'done' }
  | { type: 'turn_started'; turn_id: string }
  | { type: 'turn_finished'; turn_id: string }
  // Sent once on WS connect when a turn is already mid-stream on this topic,
  // so a re-entering client rebuilds the 正在思考 indicator.
  | { type: 'turn_active'; turn_ids?: string[] }
  // A just-persisted block turned out to be a provider-error echo — remove it.
  | { type: 'retract_block'; block_id: string }
  // An existing block's data changed in place (e.g. an option question got
  // answered) — replace it in the timeline.
  | { type: 'block_updated'; block: Block }

// An uploaded worktree file the message carries. `path` comes from
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
  // No `author`: the backend takes it from the socket's ?token=. Sending one
  // was never authoritative — it was the forgeable field that let an expired
  // session post as 匿名者 — so the client no longer names itself at all.
  summon: boolean
  reply_to?: string // B3: thread this message under another
  attachments?: ChatAttachment[] // Uploaded first, referenced here.
  // 乐观渲染的对账号：客户端给自己这一次发送起的 id，后端原样戳回块的 meta 上。
  // 靠文本对账是不行的——落库那一步会把 @名字 改写成 <@handle>。
  client_id?: string
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

// GET /api/projects/{id}/members → {data:[{user_handle, role, name, avatar_id}], total}
export interface ProjectMemberRow {
  user_handle: string
  role: string
  name?: string
  // 这个人**自己选的**头像素材 id（getAvatarUrl 拼成 /avatars/{id}）。两种情况
  // 为 null：名册行背后没有 fusion 用户档案，或者他从来没设过头像（档案还指着
  // 全局默认头像，后端已替我们判掉）。两种都用彩色首字母兜底 —— 别去取
  // /avatars/default，那会让所有没设过头像的人共用同一张脸。
  avatar_id?: number | null
  // `agent` marks 芝士 (any of its per-topic 分身), derived server-side from the
  // execution binding — never from the handle string, which differs per topic.
  agent?: boolean
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
  // 芝士那一行上，这是**这个房间现在交给的那个队友**的名字（换队友就跟着变），
  // 不是座位账号的昵称 —— 座位昵称是建号时写死的常量，永远是「芝士」。
  name?: string
  // 这个人**自己挑的**头像素材 id，同 ProjectMemberRow.avatar_id：没挑过就是
  // null，画彩色首字母。别拿它去取 /avatars/default。
  avatar_id?: number | null
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
  // null when the file must not be edited as text: `binary` (a text editor would
  // corrupt it on save) or `too_large` (never sent — it would freeze the tab).
  content: string | null
  // Content id to echo back on save; a mismatch means someone wrote in between.
  // Null only when the content was not read at all (`too_large`).
  version: string | null
  bytes: number
  binary: boolean
  too_large: boolean
}

// GET /topics/{id}/preview (spec §9.1): the artifact 芝士 pointed at as the
// topic's current preview. Null when 芝士 hasn't set one.
export interface PreviewInfo {
  // kind=file → render the file's content; kind=app → iframe straight to the app
  // the agent started on its machine, carried here over that machine's preview
  // tunnel (url, live-resolved on every fetch).
  kind?: 'file' | 'app'
  path: string
  mime: string | null
  // kind=app: the backend's reverse-proxy path (root-relative), or null when the
  // app isn't answering. `tunnel_up` separates "那台机器没有把预览通道拨出来" from
  // "通道在，但应用没在跑" — without it both look like an empty white frame.
  url?: string | null
  tunnel_up?: boolean
  // Which artifact this is, so a client can tell "芝士 pointed at something new"
  // from "the same preview, re-fetched".
  artifact_id?: string
}

// GET /projects/{id}/topics/{id}/work-summary: what work a topic is holding,
// answered without opening any of it. The 工作面板 offers a tab only where the
// thing it shows exists, and 改动 carries the count — both are questions about
// tabs that are closed.
export interface TopicWorkSummary {
  // Paths this topic's branch changes vs the base — the diff's table of contents.
  changed_files: string[]
  // The topic has run at least one turn, so there is a 现场 to open. A room
  // where only people talked has none.
  has_run: boolean
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
  // 闸门没跑成：检查没能在门禁环境里跑起来，对代码没有结论（不是「未通过」）。
  | 'gate_blocked'
  // 两阶段采纳 (2026-08-16 → #718 退役): 历史状态。采纳回到「点一下就是合并」
  // 之后不再有卡进入它，存量卡也已迁回 pending —— 这里留着只为极端残留兜底。
  | 'pr_open'
  | string

// 合并态 (#718): the card's status IS the merge state. Computed server-side
// (backend domain/review/merge_state.py) — the browser never derives it, it
// only puts words and a dot next to what the backend said.
export type MergeStateWord = 'clean' | 'unstable' | 'blocked' | 'behind' | 'dirty' | 'unknown'

// 谁的活 (#718 的表格): who moves next. `human` + no PR = the platform lane,
// where accepting is purely a human judgment and never gated by the state.
export type MergeWho = 'ci' | 'agent' | 'platform' | 'human'

export interface MergeReason {
  kind: string
  // The check names involved, ready for the card face (红了哪个要能看见).
  checks: string[]
  detail: string
}

export interface MergeStateInfo {
  state: MergeStateWord
  who: MergeWho
  // At least one entry server-side; the basis for the verdict.
  reasons: MergeReason[]
  head_sha: string | null
  checked_at: string | null
  since: string | null
}

// 绿了自动合 (#718): only meaningful when the project allows it; armed by a
// reviewer while the card is blocked/behind, merged by the platform when the
// rules are met.
export interface AutoMergeInfo {
  allowed: boolean
  armed_by: string | null
  armed_at: string | null
}

export interface AcceptCard {
  id: string
  topic_id: string
  reviewer_handle: string
  routing_reason: string
  // 提交与 PR 规范: the Conventional Commits subject + body this topic will be
  // squash-merged under. Null on a card filed without them (the platform then
  // falls back to `chore: <话题标题>`).
  change_subject: string | null
  change_body: string | null
  status: AcceptStatus
  decided_by: string | null
  decided_at: string | null
  note: string
  // 这条 note 是「停住了」(error) 还是「还在走」(info)；空 note 是 null。
  // 后端算好下发（domain/review/notes.py），别在这边按文案开头去猜。
  note_level: 'error' | 'info' | null
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
  // 合并态 (#718): what stands between this card and the trunk, and whose move
  // it is. Always present — a platform-lane card carries who="human".
  merge_state: MergeStateInfo
  auto_merge: AutoMergeInfo
  // 两阶段采纳 (PR迭代式) only: which repo the PR lives in and the commit CI is
  // being queried against.
  pr_repo: string | null
  pr_head_sha: string | null
  pr_merged_at: string | null
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
  kind: 'ai' | 'compute' | 'visibility'
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

// One compute node this deployment runs, with liveness.
export interface MarketNode {
  id: string
  label: string
  // Which compute pool this node IS — the same id the 市场 catalogue lists.
  kind: 'device' | 'cloud'
  online: boolean
  // Whether an unconfigured topic lands on this node.
  current: boolean
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
  project_id: string | null
  source_task_id: number | null
  credits_total: number
  credits_used: number
  created_at: string
}

// Shared team grants plus credits restricted to the requesting project.
export interface ProjectCredits {
  team_id: number | null
  tokens_per_credit: number
  unlimited: boolean
  credits_total: number
  credits_used: number
  credits_remaining: number
  grants: ComputeGrantRow[]
}

// ---- 题目匹配市场 (spec §13 阶段 6: Space 发布题目, 团队应征) ----

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

// #282 §四 / #358 · whether a topic's turn can see the whole machine it runs on.
// `effective` is the visibility of the device the topic is pinned to ('host' |
// 'isolated' | null when on platform compute / not yet pinned); `machine_access`
// is the one flag the room's Hosted Machine badge keys on; `notice` is the honest
// #282 UI line, used as the badge's tooltip. `options` carries the two 档 with
// their capability copy (isolated = boxed default, host = whole-machine, 申请制).
export interface TopicComputeVisibility {
  options: PoolListing[]
  effective: 'host' | 'isolated' | null
  machine_access: boolean
  notice: string
}

export interface TopicComputeDevice {
  device_id: string
  name: string
  online: boolean
}

// GET /topics/{id}/compute-profile — a topic's session-level compute选择 (v4).
// `current` is effective (room choice → project default → deployment default);
// `locked` freezes the picker once the topic has run (session started);
// `inherited` = still following the project default (no own choice yet);
// `device_id` is the self-hosted machine pinned to this topic, or null while
// 「系统挑一台」still waits for the first turn to choose one.
export interface TopicComputeProfile {
  choice: ComputeChoice
  project_default: ComputeChoice
  favorites: ComputeChoice[]
  current: string
  device_id: string | null
  devices: TopicComputeDevice[]
  locked: boolean
  inherited: boolean
  profiles: PoolListing[]
  visibility: TopicComputeVisibility
}

export interface EnvironmentConfig {
  setup_script: string
  startup_script: string
  variables: Record<string, string>
  revision: string
}
export interface ProjectEnvironmentInfo {
  config: EnvironmentConfig
  can_edit: boolean
  rooms: { id: string; title: string; revision: string | null }[]
}
export interface EnvironmentStatus {
  busy?: boolean
  state: 'pending' | 'preparing' | 'ready' | 'stopped' | 'failed' | 'offline'
  recovery_state?: 'requested' | 'retrying' | 'needs_help' | 'closed' | null
  stage?: string
  log?: string
  error?: string
  exit_code?: number | null
  pinned_revision?: string | null
  started_at?: string
  finished_at?: string | null
}

export interface ComputeChoice {
  name: string
  profile: 'cloud' | 'device'
  device_id: string | null
  cores: number | null
  memory_mb: number | null
  disk_gb: number | null
}

export interface ProjectComputeConfigs {
  default: ComputeChoice
  favorites: ComputeChoice[]
  can_manage: boolean
  devices: TopicComputeDevice[]
  cloud_available: boolean
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

// 分支保护 (#718): 平台侧的合并规则，照 GitHub 分支保护那一页配置。
// GitHub 能判定的听 GitHub，判定不了的平台按这份配置补位。
export interface RequiredCheck {
  name: string
  // 相对仓库根的 glob；缺省/为空 = 每个 PR 都要求这条检查。
  paths?: string[]
}
// 可写的规则本体。PUT partial-update：body 里出现哪个键就改哪个，返回值也是这一块。
export interface BranchProtectionRules {
  required_checks: RequiredCheck[]
  strict: boolean
  dismiss_stale: boolean
  auto_merge_allowed: boolean
  // null = 未配置：默认由项目 owner 和 lead 放行。
  override_handles: string[] | null
  approvals_required: number
  // '' = 未指定。
  default_reviewer: string
}
export type BranchProtectionPatch = Partial<BranchProtectionRules>
// GET 额外带两块只读附注，说明 GitHub 那一侧的现实。
export interface BranchProtection extends BranchProtectionRules {
  // 绑定 GitHub 的项目从仓库设置读；未绑定固定 'squash'。只读。
  merge_method: string
  github_protection: {
    enforced: boolean
    status: 'unbound' | 'enforced' | 'none' | 'unknown'
    detail?: string | null
  }
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

// ---- AI 队友 (agent 类型与实例) ----
//
// Three layers, three lifetimes (docs/topics/room-task-agent-session-设计方案.md
// §12): a TYPE is 出厂设置 and belongs to no project; an INSTANCE is that type
// working inside one project, and it owns the memory it accumulated there; a
// session is where one conversation got to and may be thrown away.
//
// So "how this agent behaves" (system prompt, skills, MCP, model, effort,
// harness) is on the TYPE, and "who it is here" (name, handle, memory) is on the
// INSTANCE. The management page shows both, which is why it reads two endpoints.

// GET /agent-types — presets merged with the project's custom types.
export interface AgentType {
  name: string
  title: string
  description: string
  // The system prompt this type runs under (角色设定).
  body: string
  skills: string[]
  mcp_servers: string[]
  model: string | null
  effort: string | null
  harness: string | null
  // Ships with the platform → read-only.
  builtin: boolean
  space_id?: number | null
  created_by?: string | null
  created_at?: string | null
}

// GET /projects/{id}/agents — one agent working in this project.
export interface ProjectAgent {
  // Null for the implicit 芝士 a project has before anyone configured one.
  id: string | null
  project_id: string
  // The memory pool key inside the project (`{project}:{handle}`).
  handle: string
  type_name: string | null
  display_name: string
  // What a new topic in this project gets.
  is_default: boolean
  // False = it resolves and owns a memory pool, but there is no row to edit.
  configured: boolean
  // False = 已停用. Still listed and still working in the topics that already
  // have it — just not offered when picking an agent for new work.
  is_active: boolean
  created_at?: string | null
}
