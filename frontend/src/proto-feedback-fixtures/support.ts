/**
 * proto-feedback-fixtures/support.ts — 夹具共用的那层原语：假数据自己的几个常量、
 * 两条造数据的工厂（`row` / `comment`），以及详情页那条线程的分页与时间线。
 *
 * 这一份不认识 `ROWS`，也不认识任何路由，是别的分片都往下踩的那一层。别的分片从它这里
 * 取什么，各自文件顶上的 import 里点着名写。
 */
import type {
  FeedbackComment,
  FeedbackDetail,
  FeedbackKind,
  FeedbackMeta,
  FeedbackNote,
  FeedbackPriority,
  FeedbackStatus,
  FeedbackVisibility,
} from '@/cx_types'

/** 预览里「我」是谁。管理端入口和「我的」那一栏都看它。 */
export const ME = 'andy'
/** 「热门」的三个数与后端那三个常量（`repositories.HOT_SCORE` 等）取同一个值 ——
 *  预览要能照真话把这一栏的规则说出来（「两周前的一票算今天半票 · 至少 5 条」）。 */
const HOT_SCORE = 2.0
const HOT_HALF_LIFE_DAYS = 14
export const HOT_MIN_ITEMS = 5
/** 预览一律按管理员看：私密条目、安全标记、管理端那一屏都要能点到。 */
export const IS_ADMIN = true

/**
 * 「谁挑过头像」——接口的 `author_avatar_id` 在真实现里来自 `UserProfile.avatar_id`，
 * 而且**只装真的挑过的人**：注册时会给人人都写上那张全局默认头像，所以「有 avatar_id」
 * 不等于「挑过」，后端专门有一处把这两种分开（`chosen_avatar_ids`）。
 *
 * 这份假数据照同一件事造：名单里没有的返回 null，界面就画那个按 handle 派生的彩色
 * 首字母。**不要**图省事一律给个 id —— 那正是「所有没挑过头像的人共用同一张脸」，
 * 而区分人正是头像唯一的活。
 */
const CHOSEN_AVATARS: Record<string, number> = {
  andy: 3,
  andylizf: 2,
  chiruotong: 4,
}

export const avatarOf = (handle: string): number | null => CHOSEN_AVATARS[handle] ?? null

/** 相对时间按**打开页面的那一刻**算：预览是拿来看「长什么样」的，把一个日期写死，
 *  过几天再打开就全是「8 天前」，而那不是任何人的真实体验。 */
export const BASE_MS = Date.now()
export function ago(minutes: number): string {
  return new Date(BASE_MS - minutes * 60_000).toISOString()
}

export const META: FeedbackMeta = {
  kinds: ['bug', 'suggestion', 'other'],
  statuses: ['received', 'in_progress', 'resolved', 'deployed'],
  priorities: ['low', 'normal', 'high', 'urgent'],
  visibilities: ['public', 'private'],
  status_ladder: ['received', 'in_progress', 'resolved', 'deployed'],
  tabs: ['all', 'hot', 'resolved'],
  admin_tabs: ['public', 'private', 'agent', 'security'],
  hot_score: HOT_SCORE,
  hot_half_life_days: HOT_HALF_LIFE_DAYS,
  hot_min_items: HOT_MIN_ITEMS,
  is_admin: IS_ADMIN,
  is_platform_admin: IS_ADMIN,
  // 附图的那两个上限跟着服务端报的数（`GET /feedback/meta`）。
  max_attachments: 10,
  max_attachment_bytes: 10 * 1024 * 1024,
}

/** 造一条反馈。卡片字段和详情字段放在一份数据里 —— 详情页要的就是卡片 + 正文，
 *  分成两张表只会让「详情里的支持数」和「列表里的支持数」有对不上的机会。 */
export function row(spec: {
  id: string
  display: string
  kind: FeedbackKind
  title: string
  summary: string
  status: FeedbackStatus
  priority?: FeedbackPriority
  visibility?: FeedbackVisibility
  security?: boolean
  author: string
  authorIsAgent?: boolean
  submittedBy?: string | null
  assignee?: string | null
  tags?: string[]
  supports: number
  supported?: boolean
  minutesAgo: number
  problem: string
  why?: string
  expectation?: string
  whatHappened?: string
  repro?: string
  evidence?: string
  environment?: string
  thread?: FeedbackComment[]
  notes?: FeedbackNote[]
  /** 默认 true —— 理由写在下面那一行旁边。 */
  can_delete?: boolean
}): FeedbackDetail {
  const created = ago(spec.minutesAgo)
  const thread = threadOf(spec.thread ?? [])
  return {
    reward: null,
    attachments: [],
    id: spec.id,
    display_id: spec.display,
    kind: spec.kind,
    title: spec.title,
    summary: spec.summary,
    status: spec.status,
    priority: spec.priority ?? 'normal',
    visibility: spec.visibility ?? 'public',
    security: spec.security ?? false,
    author_handle: spec.author,
    author_is_agent: spec.authorIsAgent ?? false,
    author_avatar_id: avatarOf(spec.author),
    submitted_by_handle: spec.submittedBy ?? null,
    assignee_handle: spec.assignee ?? null,
    tags: spec.tags ?? [],
    supports: spec.supports,
    comments: thread.length,
    supported: spec.supported ?? false,
    last_activity_at: created,
    created_at: created,
    problem: spec.problem,
    why: spec.why ?? null,
    expectation: spec.expectation ?? null,
    what_happened: spec.whatHappened ?? null,
    repro: spec.repro ?? null,
    evidence: spec.evidence ?? null,
    logs: null,
    session_id: null,
    environment: spec.environment ?? null,
    topic_id: null,
    project_id: null,
    timeline: ladderUpTo(spec.status, created),
    thread,
    // 详情默认给第一页；下一页的游标由 `detailPage` 按当时那一条算 —— 写死在这里
    // 就等于「谁都一页装得下」，那个按钮在预览里根本不会出现。
    thread_next_cursor: null,
    notes: spec.notes ?? [],
    // 预览里的人**就是这几条的提交者**（假后端没有登录态可言），按服务端的判据他
    // 正是能删的那一位。这一格不能写死 `false`：那会让「删除」这个入口在预览里一次
    // 都不出现，而预览正是拿来看这类东西的地方。
    can_delete: spec.can_delete ?? true,
    can_claim: false,
    can_release: false,
  }
}

/** 预览里每一页**顶层评论**给几条。真实现是 `THREAD_PAGE = 50`，一屏放不下 50 条，
 *  于是「加载更多评论」这个按钮在预览里永远不出现 —— 而它正是这一版新加的东西之一。
 *  压到 2 条是为了让人点得到它。**分页的边界是这份假数据自己的事**，store 那边一行
 *  都不知道（它只认服务端发下来的那个游标）。
 *
 *  一栋楼里的回复**一次给全**，所以 `replies_next_cursor` 恒为 null、楼内那个「加载
 *  更多回复（还有 N 条）」在预览里点不出来；「展开更多 N 条回复」那一个是本地折的，
 *  和它无关，照常能点。 */
const PROTO_THREAD_PAGE = 2

/** 顶层评论带上「这一栋一共几条回复」。服务端那个数是 `page_comments` 一次查出来
 *  的（不是这一页里有几条），客户端靠它决定「展开更多」是摊开手上这几条、还是去取
 *  下一页。假数据这边整条线程都在手上，数一遍就行。 */
function threadOf(thread: FeedbackComment[]): FeedbackComment[] {
  const replies = new Map<string, number>()
  for (const c of thread) {
    if (c.parent_id) replies.set(c.parent_id, (replies.get(c.parent_id) ?? 0) + 1)
  }
  for (const c of thread) {
    if (c.parent_id === null) c.reply_count = replies.get(c.id) ?? 0
  }
  return thread
}

/** 一页顶层评论 + **它们各自那一栋的全部回复**（和 `page_comments` 同一个形状：
 *  回复跟着它那栋楼走，不单独分页）。游标在这一份里就是顶层评论的下标 —— 真实实现
 *  是一对「时间戳 + uuid」，那是为了让翻页在时间戳相同时仍然有序；假数据不需要。
 *  游标带着值（而不是「第几页」）这一点是一样的：数到哪就是哪，中间删了也不会跳。 */
export function threadPage(
  item: FeedbackDetail,
  after: string | null
): { thread: FeedbackComment[]; next: string | null } {
  const tops = item.thread.filter((c) => c.parent_id === null)
  const start = Math.max(0, Number(after) || 0)
  const page = tops.slice(start, start + PROTO_THREAD_PAGE)
  const ids = new Set(page.map((c) => c.id))
  const thread = [...page, ...item.thread.filter((c) => c.parent_id !== null && ids.has(c.parent_id))]
  const more = start + PROTO_THREAD_PAGE < tops.length
  return { thread, next: more ? String(start + PROTO_THREAD_PAGE) : null }
}

/** 详情响应：正文是整条，评论只给一页。`comments` 仍然是**总数**（`row` 里按整条
 *  线程算的）—— 分页不许让卡片上那个计数变小，那正是这一版修掉的一个错。 */
export function detailPage(item: FeedbackDetail, after: string | null): FeedbackDetail {
  const { thread, next } = threadPage(item, after)
  return { ...item, thread, thread_next_cursor: next }
}

export const rung = (status: FeedbackStatus, by_handle: string | null, at: string) => ({
  status,
  by_handle,
  at,
  note: null,
})

/** 时间线：从提交到当前状态每一步都留一条，和真实实现一样是 append-only。 */
function ladderUpTo(status: FeedbackStatus, created: string): FeedbackDetail['timeline'] {
  const ladder: FeedbackStatus[] = ['received', 'in_progress', 'resolved', 'deployed']
  const end = ladder.indexOf(status)
  return ladder.slice(0, end + 1).map((step, index) => rung(step, index === 0 ? null : 'andy', created))
}

/** 一条评论。`reply_to_handle` / `likes` / `liked` / `can_delete` 这四列是这一版
 *  新加的（服务端算出来的），默认值集中写在这里而不是在每个字面量里重复一遍 ——
 *  真实实现只有一处（`services.comments_out`），fixture 这边抄五遍就等着漂。 */
export function comment(spec: {
  id: string
  /** 折楼之后的父亲：顶层恒为 null，回复指向**顶层**那条（服务端折过）。 */
  parent?: string | null
  author: string
  authorIsAgent?: boolean
  body: string
  minutesAgo: number
  /** 只在这一条是回复、且它回的那条本身也是回复时有值。顶层不出现。 */
  replyTo?: string
  likes?: number
  liked?: boolean
  canDelete?: boolean
}): FeedbackComment {
  return {
    id: spec.id,
    parent_id: spec.parent ?? null,
    author_handle: spec.author,
    author_is_agent: spec.authorIsAgent ?? false,
    author_avatar_id: avatarOf(spec.author),
    body: spec.body,
    reply_to_handle: spec.replyTo ?? null,
    likes: spec.likes ?? 0,
    liked: spec.liked ?? false,
    // 预览里就是「我写的能删、别人的不能」：服务端的规则是作者本人或管理员，
    // 这里按同一个规则抄一份，别演成「谁都能删」。
    can_delete: spec.canDelete ?? spec.author === ME,
    // 这两个都由 `threadOf` 在整条线程拿齐之后补上：它们是「这一栋有多少条」和
    // 「下一页从哪开始」，单条评论自己算不出来。
    reply_count: 0,
    replies_next_cursor: null,
    created_at: ago(spec.minutesAgo),
  }
}
