// ---- 反馈 (feedback) ----
//
// 这一节原来长在 `@/cx_types` 里，搬出来是因为那个文件顶在 frontend/src 的 1000 行
// 上限上；`@/cx_types` 把它原样转出去，对外一个名字都没变，导入路径照旧写 `@/cx_types`。
import type { components } from '../api/openapi'
import type { ListPayload } from '../cx_types'

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

type Schemas = components['schemas']

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
/** 报告带着的一份文件。**不含字节、也没有地址** —— 图片和下载都由
 *  `feedbackAttachmentUrl` 现取，那一条路上服务端会再判一次可见性。 */
export type FeedbackAttachment = FeedbackDetail['attachments'][number]
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
  /** 已经传上去、要挂到这条反馈上的附件 id，**按要显示的顺序**。字节早在选文件/
   *  粘贴那一刻就上传了（见 `uploadFeedbackAttachment`），这里只是把 id 交给服务端；
   *  不是本人上传的 id 会被拒（服务端 `attach` 的说明）。 */
  attachment_ids?: string[]
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
  /** 用户原话；用户没说过话时为 null，并且 `user_said_absent` 为 true。 */
  user_said: string | null
  /**
   * 用户没有就这个问题说过话。比这个字段早的卡没有它，那时「没说过」写成
   * `user_said` 里的一句约定好的话 —— 读的时候认那句话，见 AgentFeedbackCard。
   */
  user_said_absent?: boolean
  /** 服务端算的指纹，「不用」和去重都认它。 */
  fingerprint: string
}

export interface FeedbackProposal {
  block_id: string
  author_handle: string
  authored_at: string
  payload: FeedbackProposalPayload
}
