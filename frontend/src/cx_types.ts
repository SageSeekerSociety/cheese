// Shared types matching the backend API contract (CheeseX Phase 0).

import type { AgentControlState } from './types/agentControl'
import type { AskBlockMeta } from './types/ask'
import type { DeviceScreen } from './types/deviceSessions'
import type { EnvironmentFailure } from './types/environment'
export type { AgentControlState } from './types/agentControl'
export type { AskAnswerEntry, AskOption } from './types/ask'
export type { DeviceScreen } from './types/deviceSessions'
export type { WaitingItem } from './types/waiting'

import type { MemberActivity, MemberWait } from '@/lib/memberActivity'
import type { Shell } from '@/lib/shell'

export interface Project {
  id: string
  name: string
  // 地址里的项目名：`/projects/<slug>/...`。
  slug: string
  created_at: string
  // 建项目的人自己写的「打算做什么」（#946 片 C）。空串 = 建的时候没答，或跳过了。
  intent?: string
  // The project's root topic (= 本体 / 大本营). Its living doc is the 章程.
  root_topic_id?: string
  /** 建这个项目的人。名册上他那一行不带任何管理动作——没人能把他降职或移出。 */
  owner_handle?: string | null
  /** 这个项目归哪个团队。顶栏那颗 ← 在没记到来路时拿它当兜底。 */
  team_id?: number | null
  /** 所属团队的 handle，团队页的地址（`/teams/<handle>`）。 */
  team_handle?: string | null
  /**
   * 当前这个人能不能管理这个项目的外部成员（邀请、撤回、移出）：项目所有者，或者
   * 所属团队的所有者、管理员。后端按同一条规则再判一次，这里只决定给不给按钮。
   */
  can_manage_members?: boolean
  /** 所有者归档这个项目的时间；没归档是 null。 */
  archived_at?: string | null
  [key: string]: unknown
  /** 这个项目是从哪道赛题创建的（1.0 `task` 的整数 id）；不来自赛题时为 null。 */
  external_task_id?: number | null
  /**
   * 这个项目生效的壳，服务端已经解析好（项目级设置 → 赛题 整键覆盖 → 项目集 →
   * default）。**是解析后的声明，不是那个名字**——前端按它画，不自己维护一份
   * catalog，所以服务端加第五个壳不需要前端发版。见 `@/lib/shell`。
   */
  shell?: Shell
}

export interface ProjectSite {
  id: string
  url: string
  source_revision: string
  directory: string
  published_at: string
  published_by: string
}

export interface ProjectSiteInfo {
  can_publish: boolean
  source_revision: string | null
  candidates: { directory: string; entry_file: string }[]
  site: ProjectSite | null
  unavailable_reason?: string | null
}

export interface Topic {
  id: string
  project_id: string
  parent_id: string | null
  // 在项目里的编号，地址 `/projects/<短名>/channels/<编号>` 用它；私聊没有。
  number?: number | null
  title: string
  // 频道是做什么的（管理者写，没写是 null）；members_only 是私密频道，只有频道里的人看得到。
  description?: string | null
  members_only?: boolean
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
  cleanup_due_at?: string | null
  // 我管不管这个频道（创建者或项目管理员）：改名、写说明、归档、加人移人。
  can_manage?: boolean
  // 这个话题是从哪一块「升级」出来的（讨论升级 / 文档 🧩）。非空 = 它的来源 block
  // 上已经有一条「已升级为话题」的活引用了，时间线不必再标一次「已派出」。
  upgraded_from_block_id?: string | null
  // 此刻谁在这个房间里忙：在输入框里打字的人、有一轮在跑的 AI 队友。房间自己没有
  // 状态，有的是成员在做什么（backend `agent/activity.py`）。只有 list/get 话题时才带。
  activity?: MemberActivity[]
  // 我在不在这个频道里（「综合」总在）。侧栏只列加入了的，加入了才能在主线说话。
  joined?: boolean
  // 这个话题在等我拍板：有点名给我的待办验收卡、没答的决策请求，或芝士停在
  // 只有我能答的问题上（未读的 @ 不算，未读有自己的数字）。只有 list/get 话题时才带。
  awaits_me?: boolean
  // 这个房间在等哪几位成员、为什么（backend `block/waits.py`）。多久算太久由侧栏按
  // 当下的钟判（`lib/replyWait.ts`）。只有 list/get 话题时才带。
  waits?: MemberWait[]
  // 这个房间在看板那套词里处在哪一列。侧栏房间行的色点读它。
  //
  // 和上面 `activity` / `awaits_me` / `joined` 一样是「只有 list/get 话题时
  // 才带」的字段——`Topic` 同时也是私聊和项目本体的形状，那些地方没有列可言。所以
  // 拿不到就**不画点**，而不是退回前端自己算一个：一旦有了退路，两个算法会同时活
  // 着，而屏幕上那个颜色是哪一个算出来的，谁也说不清。
  presentation?: Presentation
}

// 一条事件的作者只有两档：一个参与者，或者平台自己。「是人还是芝士」问 `author`
// ——见 `lib/authorship.ts`。
export type AuthorType = 'participant' | 'platform'

// One aggregated emoji reaction group on a block (Slack-style chip):
// e.g. {emoji: '👀', count: 2, authors: ['cheese', 'alice']}.
export interface ReactionAgg {
  emoji: string
  count: number
  authors: string[]
}

export interface BlockMeta extends AskBlockMeta {
  [key: string]: unknown
  tool?: string
  arg?: string
  // 参数原文，给摊开这一行的人看。和 `arg` 并存而不是替掉它：那一行为了能扫而
  // 重写过、剪短过，摊开的人要的正是被剪掉的那截。
  detail?: string
  // 这一步的工具报错回来了，以及它最后说的那截。成功的步骤两个都没有。
  failed?: boolean
  error?: string
  // 这一步打印了多少（字节，抹掉凭据之后）。有它就说明后端留了一截末尾可看，
  // 那一截本身不在这里，摊开时再取（api.getStepOutput）。
  output_bytes?: number
  platform?: boolean
  // 现场工具的显示名；action 则表示平台动作指向的资源。
  as_tool?: string
  action?: string
  event_type?: string
  code?: string
  severity?: string
  title?: string
  retryable?: boolean
  // 一份周报讲的那一周（kind=weekly）。并排摆着的几份周报，是它把它们分开的。
  since?: string
  until?: string
  // 作者改过这条消息（ISO 时间）。有它，消息就标「已编辑」。
  edited_at?: string
  // 这条是队友的步骤清单（`todo_write`）：房间照它画清单，正文是给别的读者的同一份话。
  // 更早的清单消息这里只有一个 `true`，照普通消息画。
  checklist?: ChecklistMeta | boolean
  quoted_context?: import('./lib/quotedContext').QuotedContext
}

export interface ChecklistMeta {
  items: TodoItem[]
  /** 做完时队友写的一句结果。 */
  result: string | null
}

export interface Block {
  id: string
  // The conversation it was said in: a room's id, or a task's.
  conversation_id: string
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
  // 这一块转成了哪个任务（频道里的「转为任务」走这条）。两者只会有一个非空。
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

/** 一份 .docx 里的一处修订（后端 `documents/revisions.py`）。
 *
 *  一次替换在 XML 里是一个 `<w:ins>` 加一个 `<w:del>`，这里是**一条**：读者要判断的
 *  是「这处改动要不要」，分成两条就可以只接受一半——新句子进来了，旧句子还留着。
 *  `paragraph` 是段号，和 `office.py text` 报的是同一个坐标；XML 里没有页的概念，
 *  页要等排版之后才存在。 */
export interface DocumentRevision {
  number: number
  paragraph: number
  kind: 'replace' | 'insert' | 'delete'
  added: string
  removed: string
  author: string
  date: string
}

// A working-log task item (the agent's `todo_write`, rendered as a checklist
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
  room_id: string // 它挂在哪个房间里；任务不嵌套
  // 在项目里的编号，地址 `/projects/<短名>/tasks/<编号>` 用它。
  number?: number | null
  title: string
  // 标题是谁定的：placeholder = 还叫「新任务」；auto = 平台或芝士起的（方向变了
  // 会再改）；human = 人定的（平台不再动它）。见 backend room_task/naming.py。
  title_source?: 'placeholder' | 'auto' | 'human'
  status: string
  owner_handle?: string | null
  contributor_handles?: string[] // 协作者：负责人拉进来的人，也能在任务里和 AI 队友对话
  reviewer_handle?: string | null // 谁审阅它的改动，开始时定下
  created_by?: string | null
  branch_name?: string | null
  agent_handle?: string | null // 做它的 AI 队友；空的时候是项目的
  document_id?: string | null // 实况文档；第一次打开任务时才建
  started_at?: string | null // 开始的时刻、人和文档版本：审阅时与它相比
  started_by?: string | null
  started_doc_version?: number | null
  conclusion?: string | null
  base_branch?: string | null
  base_task_id?: string | null
  historical_delivery_id?: string | null
  pr_number?: number | null
  pr_url?: string | null
  delivered_head?: string | null
  // 采纳会关闭任务；单独关闭任务不代表已交付。
  accepted_by?: string | null
  accepted_at?: string | null
  closed_at?: string | null
  upgraded_from_block_id?: string | null
  created_at: string
  updated_at: string
  last_activity_at?: string // 最后一次有人或芝士说话（项目级列表才带）：侧栏按它排
  // 项目级（`/projects/{id}/tasks`）和房间级（`/topics/{id}/tasks`）列表都带它：没有它「等人验收」和「闲着」一样安静。
  card?: ThreadCard | null
  // 这条活在看板上落哪一列、卡上写哪句话。**必有字段，不是可选的**：状态从今往后
  // 只在后端算一次，前端没有一条退回本地推导的路——留一条兜底路，两个算法就会同时
  // 存在，而且谁也说不清屏幕上那个词是哪一个算出来的。
  presentation: Presentation
  /** 从讨论转出来的任务，第一轮整理文档到哪了；文档有内容后是 null。只在单个任务上。 */
  opening?: 'drafting' | 'waiting' | 'failed' | null
  /** 这件任务里最后说的一句：频道概览上的「最新进展」。只在频道的任务列表里。 */
  last_message?: Block | null
}

/** 看板的一列。判据是「**该谁动**」，不是「事情进行到哪一步」——同一个客观事实，
 *  下一步在平台手上还是在人手上，落在不同的列里。
 *    not_started 未开始 —— 还在讨论，负责人还没点「开始」
 *    building   进行中 —— 已开始，还没递出交付
 *    delivering 检查中 —— 下一步在平台/芝士手上
 *    needs_you  待处理 —— 下一步在人手上
 *    done       已完成 —— 已采纳，或已关闭且没交付
 *    archived   已归档 —— 房间才有；活不归档
 */
export type BoardColumn = 'not_started' | 'building' | 'delivering' | 'needs_you' | 'done' | 'archived'

type BuildingPhrase = 'running' | 'started' | 'idle' | 'draft'
type DeliveringPhrase = 'gate_running' | 'awaiting_checks' | 'fixing_checks' | 'resolving_conflict' | 'updating_branch'
type NeedsYouPhrase = 'checks_failed' | 'awaiting_review' | 'bounced' | 'awaiting_answer'
type DonePhrase = 'accepted' | 'completed' | 'closed' | 'archived'
export type BoardPhrase = 'discussing' | BuildingPhrase | DeliveringPhrase | NeedsYouPhrase | DonePhrase

/** 后端算好的呈现（`room_task/presentation.py`），前端不推状态。`phrase` 是码，由 `lib/board.ts` 按读者的语言画。 */
export interface Presentation {
  column: BoardColumn
  phrase: BoardPhrase
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

// WebSocket server -> client frames. What 芝士 says lands as discrete blocks; the
// backend also sends `live` frames, what it is writing meanwhile (live_frames.py).
export type WsServerFrame =
  | { type: 'user_block'; block: Block }
  // A block's reactions changed (someone toggled / 芝士's 👀 receipt landed).
  | { type: 'reaction'; block_id: string; reactions: ReactionAgg[] }
  // A 分身's checklist, on its card's channel (the room's own list is a message).
  | { type: 'todo'; items: TodoItem[] }
  | { type: 'state'; resource: string; project_ids?: string[] }
  | { type: 'event_block'; block: Block }
  | { type: 'assistant_block'; block: Block }
  // persisted=true → the failure already landed in the timeline as an event
  // block; the client must not double-show it as a floating banner.
  | { type: 'error'; message: string; persisted?: boolean; code?: string; i18n?: { key: string; params?: object } }
  | { type: 'done' }
  // `agent`：这一轮在哪个座位上跑（块署名的那个 handle）。一间房几个队友并行
  // 在干时，「谁在干活」靠它区分；老后端没有这个字段，界面退回默认名字。
  | { type: 'turn_started'; turn_id: string; agent?: string }
  | { type: 'turn_finished'; turn_id: string; agent?: string }
  // Sent once on WS connect when a turn is already mid-stream on this topic,
  // so a re-entering client rebuilds the 正在思考 indicator.
  // `since`: when each of them started, epoch seconds.
  // `agents`：每个进行中的轮次在哪个座位上，键是 turn_id。
  | { type: 'turn_active'; turn_ids?: string[]; since?: Record<string, number>; agents?: Record<string, string> }
  // A just-persisted block turned out to be a provider-error echo — remove it.
  | { type: 'retract_block'; block_id: string }
  // 一位成员开始 / 停下打字或干活；连上时有人在忙，先来一帧此刻的全部。
  | ({ type: 'activity'; active: boolean } & MemberActivity)
  | { type: 'activity_snapshot'; members: MemberActivity[] }
  // An existing block's data changed in place (an option question got answered): replace it in the timeline.
  | { type: 'block_updated'; block: Block }
  | { type: 'pong' } // answer to the client's liveness ping; carries nothing
  // The room's session state moved (a task started or finished, or the session
  // reported its model); the same shape `GET /topics/{id}/agent/control` answers.
  | { type: 'agent_control'; state: AgentControlState }
  | import('./types/live').LiveFrame
  | import('./types/threads').ThreadActivityFrame

// An uploaded worktree file the message carries. `path` comes from
// POST /topics/{id}/attachments; the WS frame only references it (no binary).
export interface ChatAttachment {
  path: string
  mime: string
}

// The client sends only the liveness probe and `typing` (I am composing here;
// `active: false` = stopped; who is the socket's credential) — a message is a POST.
export type WsClientMessage = { type: 'ping' } | { type: 'typing'; active?: boolean }

// POST /topics/{id}/messages. 请求体上没有「叫不叫芝士」这一位：这条消息点了谁的名，
// 由后端从正文里的 @ 解析（私聊是两席的房间，说话就是对着对方说的）。前端要叫它，
// 就把 @ 写进正文 —— 时间线上那条消息必须自己说明它叫了谁。
export interface ChatMessageBody {
  content: string
  // No `author`: the backend takes it from the request's token.
  // 这一次发送的 id（UUID）。重发带同一个 id，落库的还是那一条；后端把它原样戳回
  // 块的 meta.client_id 上，乐观显示的那一条靠它对上账——靠文本对账是不行的，落库
  // 那一步会把 @名字 改写成 <@handle>。
  request_id: string
  reply_to?: string // B3: thread this message under another
  attachments?: ChatAttachment[] // Uploaded first, referenced here.
  quoted_context?: import('./lib/quotedContext').QuotedContext
}

// ---- 项目总览 / 收件箱 (eval G2/G3) ----

// A lightweight topic reference used inside overview payloads.
export interface TopicRef {
  id: string
  title: string
  kind: string
}

export interface ProjectMember {
  handle: string
  role: string
}

// GET /api/projects/{id}/members → {data:[{user_handle, role, name, avatar_id, agent, active}], total}
// 一张名册，AI 队友也在上面：请一个队友进房间和请一个人是同一件事，所以界面不该
// 再自己把「人」和「队友」两份拼起来——拼出来的那份就是第二份声明。
export interface ProjectMemberRow {
  user_handle: string
  // 这个人是怎么在项目里的：项目的所有者、所属团队的成员，或者被邀请进来的外部成员
  // （团队以外、只参与这一个项目的人）。只有外部成员能从项目里移出——团队成员的去
  // 留在团队里定。AI 队友那几行是 `agent`。
  source?: 'owner' | 'team' | 'external' | 'agent'
  team_id?: number
  // source 为 team 时，带他进来的那个团队的 handle（团队页 `/teams/<handle>`）。
  team_handle?: string
  // That team's name, which the 「来自团队」 link reads.
  team_name?: string
  name?: string
  name_source?: 'default' | 'human'
  // 这个人**自己选的**头像素材 id（getAvatarUrl 拼成 /avatars/{id}）。两种情况
  // 为 null：名册行背后没有 fusion 用户档案，或者他从来没设过头像（档案还指着
  // 全局默认头像，后端已替我们判掉）。两种都用彩色首字母兜底 —— 别去取
  // /avatars/default，那会让所有没设过头像的人共用同一张脸。
  avatar_id?: number | null
  // `agent` marks an AI teammate — server-side it is a row that came from the
  // project's agent instances, never a guess at the handle string.
  agent?: boolean
  // 只有队友会是 false：已停用的队友还在名册上（它在已经接手的房间里照常工作），
  // 只是派新活、请进新房间的地方不该再列出来。
  active?: boolean
  // 这个项目的**默认**队友，也就是一间没有 AI 席位的老房间会落到谁身上。名册上
  // 第一个带 `agent` 的不是这个答案（那是建得最早的那一位），所以要问「这个房间
  // 归谁」的地方只能读这一位。
  project_default?: boolean
  // 队友自己的 handle（`cheese-kimi` 这种）：它的会话、轮次按这个记，消息的收件人也
  // 写这个。只知道这个 handle 的地方靠它找到这一行。人没有这一项。
  instance_handle?: string
  [key: string]: unknown
}

/** 一张「请你加入这个项目」的邀请，等对方回答。 */
export interface ProjectInvitation {
  id: string
  project_id: string
  invitee_handle: string
  inviter_handle: string
  status: 'pending' | 'accepted' | 'declined' | 'revoked'
  created_at: string
  responded_at?: string | null
  /** 后端不回项目名，界面上要显示得自己从项目列表里配；配不到就退成 id。 */
  [key: string]: unknown
}

// 话题成员名册 (fusion-design §3): a topic's group-room roster. Roles are
// owner/admin/member (distinct from ProjectMemberRow's lead/member/mentor);
// `agent` marks 芝士 (the AI member) so the UI can badge it.
export interface TopicMemberRow {
  topic_id: string
  member_handle: string
  // `owner` 是建这个频道的人；「综合」里的人都是 `member`。
  role: 'owner' | 'member'
  // 芝士那一行上，这是**这个房间现在交给的那个队友**的名字（换队友就跟着变），
  // 不是座位账号的昵称 —— 座位昵称是建号时写死的常量，永远是「芝士」。
  name?: string
  name_source?: 'default' | 'human'
  // 这个人**自己挑的**头像素材 id，同 ProjectMemberRow.avatar_id：没挑过就是
  // null，画彩色首字母。别拿它去取 /avatars/default。
  avatar_id?: number | null
  agent?: boolean
}

// GET /api/projects/{id}/inbox?target_handle=
// 等你处理的那几条：还没拍板的决策请求、点名给你的验收卡，以及还没读、又不是
// silent 的变更提醒（`level=silent` 的意思是「记下来别打扰」，它本来也不点亮角标）。
// 字段照抄后端的 NotificationOut —— 自己另起一套界面上顺口的名字，收到的就永远是
// undefined，而界面会把它读成「一条都没读过」。
// `id` 是数字：两张通知表并成一张之后主键跟的是收件箱那条序列，不再是 uuid。
export interface InboxItem {
  id: number
  project_id: string | null
  topic_id: string | null
  level: string | null
  kind: string
  target_handle: string | null
  title: string
  body: string
  // 决策请求的选项放在 payload.options 里：有选项才答得了。
  payload: { options?: unknown; [key: string]: unknown }
  read: boolean
  resolved_at: string | null
  feedback: 'up' | 'down' | null
  created_at: string
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
  ai_mode: string
  owner_handle: string
  topic_count: number
  topics_by_status: Record<string, number>
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
export type MemberTopic = Pick<Topic, 'id' | 'title' | 'status'>

// GET /api/projects/{id}/members/{handle}/summary
export interface MemberSummary {
  handle: string
  /** 他在这个项目里的来路：所有者、团队成员、外部成员；查不到是 null。 */
  source?: 'owner' | 'team' | 'external' | null
  topics_started: MemberTopic[]
  topics_active?: MemberTopic[]
  weekly_contributions?: number
  waiting_on_you: TopicRef[]
  [key: string]: unknown
}

// ---- 个人主页 / LinkedIn-GitHub profile (spec §1, §7.2, §8.4) ----

/** How the person is in a project: its owner, through its team, or invited alone. */
export type ProfileProjectRole = 'owner' | 'team' | 'external'

// One project the person is in, as the viewer may see it.
export interface ProfileProject {
  project_id: string
  name: string
  source: ProfileProjectRole
  topics_started: number
  contributions: number
  /** Their newest contribution there; null when they have none. */
  last_active_at: string | null
  /** Contributions in the last 12 trailing 7-day spans, oldest first. */
  weekly: number[]
}

// A team the person is in that the viewer may see (the TeamSummary payload).
export interface ProfileTeam {
  id: number
  /** Null only for a team that no longer exists. */
  handle: string | null
  name: string
  intro: string
  avatarId: number | null
}

/** One UTC day of the activity year. `date` is `YYYY-MM-DD`. */
export interface ProfileActivityDay {
  date: string
  count: number
}

// One thing an agent noted about this person, and where it was noted. The
// project is null once the person can no longer read it.
export interface ProfileUnderstanding {
  id: string
  content: string
  created_at: string
  project_id: string | null
  project_name: string | null
  agent_handle: string | null
  agent_name: string | null
  agent_name_source?: string | null
}

// GET /api/users/{handle}/profile — cut to what the viewer may see.
// `understanding` = what 芝士 has learned about this person (个人记忆, §8.4);
// only the person themselves receives it, everyone else gets [].
export interface UserProfile {
  handle: string
  name: string
  bio: string
  /** The raw profile avatar, which may be the platform default (see useChosenAvatar). */
  avatar_id: number | null
  /** Null when no account holds this handle. */
  joined_at: string | null
  teams: ProfileTeam[]
  /** The last 365 UTC days, oldest first, today last. */
  activity: { days: ProfileActivityDay[]; total: number }
  projects: ProfileProject[]
  understanding: ProfileUnderstanding[]
}

// GET /api/users/{handle}/topics — a topic the person wrote in.
export interface ProfileTopic {
  id: string
  title: string
  status: string
  project_id: string
  project_name: string
  /** Their contributions in it, inside the requested dates when given. */
  contributions: number
  last_participated_at: string
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
export type FileSource = 'live' | 'committed'

export interface FileContent {
  path: string
  // null when the file must not be edited as text: `binary` (a text editor would
  // corrupt it on save) or `too_large` (never sent — it would freeze the tab).
  content: string | null
  // Content id to echo back on save; a mismatch means someone wrote in between.
  // Present even for `too_large`; null means the file is gone.
  version: string | null
  bytes: number
  binary: boolean
  too_large: boolean
  source?: FileSource
  editable?: boolean
}

export type { PreviewInfo } from './types/preview'

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
  // Tokens that burned real capacity but carry NO USD price (their model has
  // no rate in the price table). Non-zero means the cost figure is
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

// 托管方的能力位 (ARCH §4.5)。「这个项目接没接 GitHub」不是一个可读的布尔：
// 界面上每一处判断读它要用的那一位。`declaration` 是这个托管方在人点采纳之前就
// 该写在卡上的一句话，GitHub 那一档为空（卡上有提案页链接）。
export interface ForgeInfo {
  // 'unknown' 不是第四种托管方，是「这张卡这会儿读不出自己的托管方」：所有能力位
  // 都是 false，采纳按钮灰着，卡上那句话说的就是这件事。
  kind: 'github_app' | 'forgejo' | 'unknown'
  reports_checks: boolean
  hosts_proposals: boolean
  can_write_remote: boolean
  pushes_to_external_remote: boolean
  identity: 'user' | 'platform'
  declaration: string
}

export interface AcceptCard {
  id: string
  task_id?: string | null
  topic_id: string
  reviewer_handle: string
  routing_reason: string
  // 提交与 PR 规范: the commit subject + body this topic will be squash-merged
  // under. Null on a card filed without them (the platform then falls back to
  // the topic title).
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
  // 托管方是谁、它能做什么 —— 卡生成的那一刻就带着（后端 domain/review/forge.py）。
  // 这是卡上唯一的一份：别从别的字段推「这个项目接没接 GitHub」。
  forge: ForgeInfo
  auto_merge: AutoMergeInfo
  // 两阶段采纳 (PR迭代式) only: which repo the PR lives in and the commit CI is
  // being queried against.
  pr_repo: string | null
  pr_head_sha: string | null
  pr_merged_at: string | null
  // 这次交付更新的是哪一项产物，以及这张卡自己是它的第几版 (#1085 结论三/五)。
  // 版本号是后端按卡的状态算好的（还没采纳的那一张算的是它采纳之后的号），前端
  // 一个都不推。落地之前递的那些卡没有这一项，所以是 null。
  artifact: { id: string; name: string; version: number } | null
  // 这一版交出去的是什么：一份文件（`filename`，字节在递卡那一刻落了快照）、一个
  // 地址（`url`），或者这次合并本身（`merge`，没有可下载的东西）。
  deliverable: { kind: 'file' | 'link' | 'merge'; filename: string | null; url: string | null } | null
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
  unlimited: boolean
  credits_total: number
  credits_used: number
  credits_remaining: number
  grants: ComputeGrantRow[]
}

// ---- 题目匹配市场 (spec §13 阶段 6: Space 发布题目, 团队应征) ----

export interface EnvironmentConfig {
  setup_script: string
  startup_script: string
  variables: Record<string, string>
  revision: string
}
export interface ProjectEnvironmentInfo {
  config: EnvironmentConfig
  can_edit: boolean
  rooms: { id: string; title: string; revision: string | null; failure?: EnvironmentFailure }[]
}
export interface EnvironmentStatus {
  busy?: boolean
  state: 'unbound' | 'pending' | 'preparing' | 'ready' | 'stopped' | 'failed' | 'offline'
  stage?: string
  log?: string
  error?: string
  exit_code?: number | null
  pinned_revision?: string | null
  started_at?: string
  finished_at?: string | null
}

// 上游仓库 (spec §6.3): a project can bind an existing git repo (关联已有 repo)
// and keep pulling its history in via 同步上游.
export interface UpstreamInfo {
  url: string | null
}

// GitHub App install flow (#192): a project connects to one repo via
// cheesex-app, replacing the classic 上游仓库 URL entry for repos it manages.
export interface GithubConnection {
  connected: boolean
  repo?: string
  account?: string
}

export interface ForgeConnection {
  kind: 'forgejo' | 'github_app'
  connected: boolean
  repo: string | null
  url: string | null
}

export interface ForgeAttribution {
  requester_coauthor: boolean | null
  effective: boolean
  deployment_default: boolean
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
  // Who works on this machine now (each session whose work computer it is), for its owner only.
  in_use?: DeviceUser[] | null
  // On a team's device list: the team's projects this machine is attached to
  // directly, rather than through the team.
  attached_projects?: { id: string; name: string }[]
}

export interface DeviceUser {
  project_id: string
  project_name: string
  topic_id: string
  topic_title: string
  agent_handle: string
  agent_name: string
  agent_name_source?: string
}

// A team the signed-in user belongs to (GET /teams/my-teams) — trimmed to what
// the device pages need. Includes the auto-provisioned personal team (个人 =
// 单人真团队), which the backend sorts first and flags `personal`.
export interface MyTeam {
  id: number
  handle: string
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
// GET /agent-types: built-in starting configurations for new agents.
export interface AgentType {
  name: string
  title: string
  description: string
  // The system prompt this type runs under (角色设定).
  body: string
  skills: string[]
  // Claude Code's subagent `mcpServers`: a server name, or { name: definition }.
  mcp_servers: (string | Record<string, Record<string, unknown>>)[]
  // Ships with the platform → read-only.
  builtin: boolean
  space_id?: number | null
  created_by?: string | null
  created_at?: string | null
}

// GET /projects/{id}/agents — a teammate's role, model override, thinking effort and compaction share (50–90).
export interface AgentConfiguration {
  body: string
  skills: string[]
  model?: string | null
  effort?: 'low' | 'medium' | 'high' | 'max' | null
  compact_percent?: number | null
}

export interface ProjectAgent {
  configuration: AgentConfiguration
  id: string
  project_id: string
  handle: string
  // 它坐在房间名册上时用的 handle —— 把它请进一个房间就是往名册上加这个。
  seat_handle: string
  type_name: string | null
  display_name: string
  name_source?: 'default' | 'human'
  // What a new topic in this project gets.
  is_default: boolean
  // False = 已停用. Still listed and still working in the topics that already
  // have it — just not offered when picking an agent for new work.
  is_active: boolean
  created_at?: string | null
}

// ---- 反馈 (feedback) ----
//
// 平台级的收件箱（`backend/app/api/routes/feedback.py`），不属于任何项目。字段名
// 与后端 schema 逐字对应 —— 不在这里发明第二个名字，那会让「这个字段到底是哪个」
// 变成每次读前端代码都要回去查一遍的事。
//
// 三处**故意**不叫原型里的名字：
//   * `supports` 是计数、`comments` 也是计数（原型里 `comments` 是数组），
//     详情页的评论在 `FeedbackDetail.thread`。
//   * 作者是 `author_handle`、时间是 `created_at`：蛇形是这一层的约定。
//   * 原型那个 `source: 'user' | 'agent'` 不存在 —— 它是 `author_is_agent`。
//     一个由人来发、但由 agent 发现的反馈（提案卡）不是「agent 提交的」，
//     它有两个字段（`author_handle` + `submitted_by_handle`）才说得清。
export type FeedbackKind = 'bug' | 'suggestion' | 'other'
/** 四级：收录 → 处理 → 解决 → 部署。权威顺序在服务端 `STATUS_LADDER`。 */
export type FeedbackStatus = 'received' | 'in_progress' | 'resolved' | 'deployed' | 'declined'
export type FeedbackVisibility = 'public' | 'private'
export type FeedbackPriority = 'low' | 'normal' | 'high' | 'urgent'

/** 列表里的一行。计数由后端一并算好（见 `schemas.FeedbackCard`）。 */
export interface FeedbackCard {
  id: string
  /** 「FB-1042」。人念的和粘贴的是这个，`id` 是 uuid，只用来发请求。 */
  display_id: string
  kind: FeedbackKind
  title: string
  summary: string
  status: FeedbackStatus
  priority: FeedbackPriority
  visibility: FeedbackVisibility
  /** 安全问题：管理员标的标记，比 private 更窄（见 services.may_see）。 */
  security: boolean
  author_handle: string
  author_is_agent: boolean
  /** 作者**自己挑过**的头像素材 id，前端用 `getAvatarUrl` 拼成 `/avatars/{id}`。
   *  没挑过是 null —— 后端已经判掉了全局默认头像那一种（`chosen_avatar_ids`），
   *  所以 null 的含义就是「画彩色首字母」，**不要**退回 `/avatars/default`：
   *  那会让所有没挑过头像的人共用同一张脸。 */
  author_avatar_id: number | null
  /** 提案被发出去时，按发送的人。人直接提的那条是 null。 */
  submitted_by_handle: string | null
  assignee_handle: string | null
  tags: string[]
  supports: number
  comments: number
  supported: boolean
  last_activity_at: string | null
  created_at: string
}

export interface FeedbackTimelineEntry {
  status: FeedbackStatus
  by_handle: string | null // 谁推的；部署管线推的那一步没有人，是 null
  at: string
  note?: string | null // 部署管线那一步写「由 PR #N 修复并上线」和链接；人推的没有
}

/** 一条评论。`parent_id` 只指向**顶层**评论 —— 回复的回复由服务端折上来，所以
 *  层级恒为两层，前端不需要自己判断「这算第几层」。 */
export interface FeedbackComment {
  id: string
  parent_id: string | null
  author_handle: string
  author_is_agent: boolean
  /** 同 `FeedbackCard.author_avatar_id`。 */
  author_avatar_id: number | null
  body: string
  /** 这条在回答谁。**只在被回复的那条本身也是回复时才有值**，因为折到顶层这个动作
   *  把指向弄丢了 —— 楼中楼里唯一猜不出来的信息。值为 null 有两种情形（顶层评论、
   *  回楼主的回复），两者渲染方式相同，所以客户端不需要区分它们。
   *  恒为 handle 而不是 id：要回答的问题只有一个「在回谁」，答案是个名字。 */
  reply_to_handle: string | null
  /** 点赞总数。 */
  likes: number
  /** **按读者**算：当前登录的人点过没有。 */
  liked: boolean
  /** **按读者**算：服务端说这条删得掉吗。按钮画不画由它决定，不由前端猜 ——
   *  猜的结果是「按钮画得出来、点下去 403」。 */
  can_delete: boolean
  /** **服务端数得出来的**这一条（顶层评论才有意义）下面一共几条回复 —— 不是这一页
   *  带回来几条。评论是分页取的，所以这两件事不一样，而「展开更多」该摊开手上已经
   *  有的、还是去取下一页，全靠这个数和手上条数的比较。回复恒为 0。
   *  少了它，客户端只能把「已经取回来的」当成全部：一栋 60 条回复的楼，界面上永远
   *  只有前 50 条，而「展开更多」会当场消失。 */
  reply_count: number
  /** 这一栋楼**楼内**的下一页游标，null = 楼里的回复已经取完了。回复自己恒为 null。
   *  不透明的字符串，和 `thread_next_cursor` 同一套：原样送回
   *  `GET /feedback/{id}/comments?parent_id=…&after=…`，不解析、不自己拼。 */
  replies_next_cursor: string | null
  created_at: string
}

/** 管理员之间的内部备注。**只增不改**，所以是行不是列。 */
export interface FeedbackNote {
  id: string
  author_handle: string
  /** 同 `FeedbackCard.author_avatar_id`。 */
  author_avatar_id: number | null
  body: string
  created_at: string
}

export interface FeedbackDetail extends FeedbackCard {
  problem: string
  why: string | null
  expectation: string | null
  what_happened: string | null
  repro: string | null
  evidence: string | null
  logs: string | null
  session_id: string | null
  environment: string | null
  /** 这条反馈是从哪个话题来的。没有话题（harness 在沙箱里撞的墙）时为 null。 */
  topic_id: string | null
  project_id: string | null
  timeline: FeedbackTimelineEntry[]
  /** 顶层评论的**第一页**，每栋楼跟着它的前若干条回复走（见 `FeedbackComment`）。 */
  thread: FeedbackComment[]
  /** 顶层评论的下一页游标，null = 底层这一层已经取完了。和列表接口的 `page_start`
   *  不同：这是**值承载**的不透明游标，锚点那一行在这中间被删掉也照样能接着往下走。 */
  thread_next_cursor: string | null
  /** 只有管理员拿得到内容；不是管理员时是空数组（同一个形状）。 */
  notes: FeedbackNote[]
  /** 调用者能不能删掉**整条反馈**。**服务端算**（作者 —— 写它的那个 handle 或按下
   *  发送的那个 —— 或平台管理员），和 `DELETE /feedback/{id}` 共用一处判据；客户端
   *  照它画按钮，不自己拼一遍，否则就是「按钮画得出来、点下去 403」。 */
  can_delete: boolean
}

export interface FeedbackCounts {
  all: number
  hot: number
  active: number
  resolved: number
  /** 「我的反馈」里未读的条数。 */
  unread: number
  /** 管理端才有：还没指派给任何人的条数。 */
  unassigned?: number
  /** 管理端才有：已经上线的累计条数。它和 `resolved` 是两条不同的数 —— 解决了不等于
   *  上线了，看板把这两件事分开显示。 */
  deployed?: number
}

export interface FeedbackListPayload extends ListPayload<FeedbackCard> {
  counts: FeedbackCounts
}

/** `GET /feedback/meta` —— 词表。
 *
 *  **颜色不在这里**（那是前端的视觉决定，见 lib/feedbackMeta.ts），这里回答的是
 *  「有哪些取值、按什么顺序流动」。加一个状态是后端改一处的事，前端靠这一份跟上，
 *  不需要发版。`is_admin` 同理：它由服务端算，前端不猜。 */
export interface FeedbackMeta {
  kinds: FeedbackKind[]
  statuses: FeedbackStatus[]
  priorities: FeedbackPriority[]
  visibilities: FeedbackVisibility[]
  /** 状态梯子：时间线把还没到的步骤也画出来，靠的就是它。 */
  status_ladder: FeedbackStatus[]
  tabs: string[]
  admin_tabs: string[]
  /** 「热门」的规则是**三个数**，不是一个：「热门」按**热度分**排，而热度是衰减的
   *  （一条三个月前攒够票的反馈不该一直占着这一栏）。三个数各管一件事 —— 门槛多少
   *  分、一个支持几天打对折、不够线时至少补几条。
   *  前端**不拿它们算排序**：筛选和排序都在服务端，客户端拿到的已经是排好的行，
   *  再算一遍屏幕上就有两套热度。它们留在这里是为了把这一栏的规则**说给人听**
   *  ——「两周前的一票算今天半票 · 至少 5 条」，一个数字说不出这句话。 */
  hot_score: number
  hot_half_life_days: number
  hot_min_items: number
  is_admin: boolean // 反馈管理员（队列、私密反馈）
  is_platform_admin: boolean // 平台管理员（管理台其余各块）；两份名单互不包含
}

export interface FeedbackSupportResult {
  /** **写完之后**的计数，不是增量。 */
  count: number
  supported: boolean
}

/** 评论点赞的返回，`FeedbackSupportResult` 往下一层。字段叫 `liked` 不叫
 *  `supported`：两件事在界面上是两种表态，共用一个词的话下一个读代码的人会以为
 *  它们是同一条记录。 */
export interface FeedbackCommentLikeResult {
  /** **写完之后**的计数，不是增量。 */
  count: number
  liked: boolean
}

/** `POST /feedback` 的请求体。作者不在里面 —— 它是验证过的调用者。 */
export interface FeedbackCreateBody {
  kind: FeedbackKind
  title: string
  summary?: string
  problem?: string
  visibility: FeedbackVisibility
  priority?: FeedbackPriority
  why?: string | null
  expectation?: string | null
  what_happened?: string | null
  repro?: string | null
  evidence?: string | null
  logs?: string | null
  session_id?: string | null
  environment?: string | null
  tags?: string[]
}

/** 提案卡上的那份 payload（`Block.meta.feedback_proposal`）。 */
export interface FeedbackProposalPayload {
  kind: FeedbackKind
  title: string
  summary: string
  problem: string
  visibility: FeedbackVisibility
  why: string | null
  expectation: string | null
  what_happened: string | null
  repro: string | null
  evidence: string | null
  logs: string | null
  session_id: string | null
  environment: string | null
  tags: string[]
  /** 用户原话，或者那句「用户没有就这个问题说过话」。**必填**，见方案稿 §5.0。 */
  user_said: string
  /** 服务端算的指纹，「不用」和去重都认它。 */
  fingerprint: string
}

export interface FeedbackProposal {
  block_id: string
  author_handle: string
  authored_at: string
  payload: FeedbackProposalPayload
}
