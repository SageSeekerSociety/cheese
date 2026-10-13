import type {
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
} from '../cx_types'

import { request } from './http'
import { feedbackQuery } from './query'

// ---- 反馈 (feedback) ----
//
// 平台级的收件箱，前缀是 `/feedback`，**不在任何项目或话题下面**（理由见
// `backend/app/api/routes/feedback.py`）。只有提案卡那三个函数挂在话题上 ——
// 提案是一句在某话题里说的话，配额和鉴权都挂在那一边。
//
// 路由按「新模块」写：`/feedback/meta`、`/feedback/counts`、`/feedback/mine` 这类
// 固定段在 `/{feedback_id}` 之前注册，所以不会被当成一个 uuid 吃掉。

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
