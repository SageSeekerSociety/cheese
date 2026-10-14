// 反馈中心一域的形状（平台级收件箱，`backend/app/api/routes/feedback.py`，不属于
// 任何项目），从 `@/cx_types` 里整块搬来的一节：cx_types.ts 顶在自己的行数上限上，
// 只能变短。名字照旧从 `@/cx_types` 转出去，所以引它的人一个都不用改。
import type { ListPayload } from '@/cx_types'
import type { components } from '../api/openapi'

type Schemas = components['schemas']

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
//
// 下面这些是后端 OpenAPI 生成类型（`src/api/openapi.d.ts`）的别名：字段、可空和
// 必有与否都以 `backend/app/domain/feedback/schemas.py` 为准，改字段改那边，再跑
// 后端导出；这份类型由类型检查现场生成（`frontend/scripts/typecheck.mjs`）。读字段要记住两件事：
//   * `author_avatar_id` 为 null 的意思是「画彩色首字母」，不要退回
//     `/avatars/default`，那会让没挑过头像的人共用一张脸。
//   * 评论的 `reply_count` 是服务端数的整栋楼回复数，不是这一页带回来几条；
//     「展开更多」靠它和手上条数比较。
export type FeedbackKind = Schemas['FeedbackKind']
/** 四级：收录 → 处理 → 解决 → 部署。权威顺序在服务端 `STATUS_LADDER`。 */
export type FeedbackStatus = Schemas['FeedbackStatus']
export type FeedbackVisibility = Schemas['FeedbackVisibility']
export type FeedbackPriority = Schemas['FeedbackPriority']
/** 列表里的一行。 */
export type FeedbackCard = Schemas['FeedbackCard']
export type FeedbackTimelineEntry = Schemas['TimelineOut']
/** 一条评论。`parent_id` 只指向顶层评论，层级恒为两层。 */
export type FeedbackComment = Schemas['CommentOut']
/** 管理员之间的内部备注，只增不改。 */
export type FeedbackNote = Schemas['NoteOut']
export type FeedbackDetail = Schemas['FeedbackDetail']
/** 标签页上的数。`unassigned` 只在管理端列表里有（`FeedbackAdminCounts`）。 */
export type FeedbackCounts = Schemas['FeedbackCounts'] & Partial<Pick<Schemas['FeedbackAdminCounts'], 'unassigned'>>
export interface FeedbackListPayload extends ListPayload<FeedbackCard> {
  counts: FeedbackCounts
}
/** `GET /feedback/meta` —— 词表。颜色不在这里（那是前端的视觉决定，见 lib/feedbackMeta.ts）。 */
export type FeedbackMeta = Schemas['FeedbackMeta']
/** 写完之后的计数，不是增量。 */
export type FeedbackSupportResult = Schemas['SupportOut']
export type FeedbackCommentLikeResult = Schemas['CommentLikeOut']

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
