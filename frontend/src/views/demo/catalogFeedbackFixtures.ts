/**
 * 预览站喂给「反馈」那一组（`catalogFeedback.ts` 的条目）的数据。
 *
 * 全是反馈详情页真会出现的形状，照着各组件自己的 `.spec.ts` 造：评论抄
 * `FeedbackCommentItem.spec.ts` 的 `c()`（顶层评论和楼内回复共用一份形状），时间线抄
 * `FeedbackStatusTimeline.spec.ts` 的 `FeedbackTimelineEntry[]`，梯子就是那几个状态。
 * 评论的 `reply_count` / `replies_next_cursor` 是服务端随每条一起发下来的，这里也照发：
 * 「展开更多」和「加载更多」的岔路全由「手上几条 vs 一共几条」这两个数决定。
 *
 * 这里只出数据（外加一个把顶层评论和回复拼成一栋楼的小函数），不引任何运行时依赖：
 * 哪一份配哪个组件、每一格看什么，在 `catalogFeedback.ts`。
 */
import type { FeedbackComment, FeedbackStatus, FeedbackTimelineEntry } from '@/cx_types'

// ---- 一条评论（FeedbackCommentItem）------------------------------------------

/** 一条评论：没写的字段照接口给的「空」补齐。顶层评论和楼内回复共用这一个形状。 */
export function comment(over: Partial<FeedbackComment> & Pick<FeedbackComment, 'id'>): FeedbackComment {
  const { id, ...rest } = over
  return {
    id,
    parent_id: null,
    author_handle: 'andylizf',
    author_is_agent: false,
    author_avatar_id: null,
    body: `正文 ${id}`,
    reply_to_handle: null,
    likes: 0,
    liked: false,
    can_delete: false,
    reply_count: 0,
    replies_next_cursor: null,
    created_at: '2026-09-20T00:00:00Z',
    ...rest,
  }
}

/** 顶层评论：别人赞过三条，这个读者还没点过。 */
export const TOP_COMMENT: FeedbackComment = comment({
  id: 'c-1',
  author_handle: 'alice',
  body: '复现了：导出到一半切走就全没了，回来得从头开始。',
  likes: 3,
})

/** 同一个读者已经点过的那一条：图标实心、文案变「已赞」、底色起来。 */
export const LIKED_COMMENT: FeedbackComment = comment({
  id: 'c-2',
  author_handle: 'bob',
  body: '我们这边也是，三十秒就开始转圈。',
  likes: 4,
  liked: true,
})

/** 楼内一条：回的是楼里的另一个人，所以带着服务端存下的 `reply_to_handle`。 */
export const REPLY_COMMENT: FeedbackComment = comment({
  id: 'c-3',
  parent_id: 'c-1',
  author_handle: 'carol',
  body: '先按数据量分页导，能绕过那一次全量聚合。',
  reply_to_handle: 'alice',
})

// ---- 一栋楼（FeedbackCommentsThread）-----------------------------------------

/**
 * 一栋楼：顶层 `t` 的 `reply_count` 是**服务端说的总数**，手上拿到的回复是 `replies`
 * 条。两个数一比就决定楼下那个按钮是什么（展开更多 / 加载更多 / 收起 / 不画）。
 */
export function building(total: number, replies: number): FeedbackComment[] {
  return [
    comment({ id: 't', author_handle: 'alice', body: '导出慢这件事从上周就开始了。', reply_count: total }),
    ...Array.from({ length: replies }, (_, i) => comment({ id: `r-${i}`, parent_id: 't', body: `第 ${i + 1} 条回复` })),
  ]
}

// ---- 时间线（FeedbackStatusTimeline）----------------------------------------

/** 服务端给的梯子（`GET /feedback/meta` 的 `status_ladder`）。 */
export const LADDER: FeedbackStatus[] = ['received', 'in_progress', 'resolved', 'deployed']

/** 刚收录：只有第一步，后面几档还没到。 */
export const JUST_RECEIVED: FeedbackTimelineEntry[] = [
  { status: 'received', by_handle: 'alice', at: '2026-09-28T09:12:00Z', note: null },
]

/** 一步按到「已上线」：部署管线推的那一步没有人，靠一句说明说清是哪次改动。 */
export const DEPLOYED_WITH_NOTE: FeedbackTimelineEntry[] = [
  { status: 'received', by_handle: 'alice', at: '2026-09-28T09:12:00Z', note: null },
  {
    status: 'deployed',
    by_handle: null,
    at: '2026-10-01T10:00:00Z',
    note: '已由 PR #4321 修复并上线：https://github.com/example/repo/pull/4321',
  },
]

/** 不修复：它是另一种结局，不在梯子上，画到它就停。 */
export const DECLINED: FeedbackTimelineEntry[] = [
  { status: 'received', by_handle: 'alice', at: '2026-09-28T09:12:00Z', note: null },
  { status: 'in_progress', by_handle: 'admin', at: '2026-09-29T09:12:00Z', note: null },
  { status: 'declined', by_handle: 'admin', at: '2026-09-30T09:12:00Z', note: null },
]
