/**
 * 评论点赞和删评论，钉两件本地算不出来的事。
 *
 * 一、**点赞的计数是服务端回的**，不是本地 ±1。本地加减在两个人同时点的时候会各自
 * 渲染出一个从来没存在过的数字 —— 和 `toggleSupport` 同一条理由（那边已经有一组
 * 测试了）。而且这里**不该**顺手刷新 Tab 计数：评论赞不进任何一栏，「热门」看的是
 * 反馈级的支持数，白发一次请求只是多一次可能失败的东西。
 *
 * 二、**删顶层评论要连它下面的回复一起从本地拿掉**。服务端是这么做的（否则那些回复
 * 会变成查不到父亲的孤儿），前端少做这一步，屏幕上就正是那个形状：父亲没了、回复还
 * 挂着，再刷新它们又都不见了 —— 一个只在两次刷新之间存在的假象。
 */
import type { FeedbackComment, FeedbackDetail } from '@/cx_types'

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getFeedback = vi.fn()
const getFeedbackCounts = vi.fn()
const likeFeedbackComment = vi.fn()
const unlikeFeedbackComment = vi.fn()
const deleteFeedbackComment = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getFeedback: (...a: unknown[]) => getFeedback(...a),
    getFeedbackCounts: (...a: unknown[]) => getFeedbackCounts(...a),
    likeFeedbackComment: (...a: unknown[]) => likeFeedbackComment(...a),
    unlikeFeedbackComment: (...a: unknown[]) => unlikeFeedbackComment(...a),
    deleteFeedbackComment: (...a: unknown[]) => deleteFeedbackComment(...a),
  }
})

import { useFeedbackStore } from '@/stores/feedback'

function c(id: string, extra: Partial<FeedbackComment> = {}): FeedbackComment {
  return {
    id,
    parent_id: null,
    author_handle: 'andy',
    author_is_agent: false,
    author_avatar_id: null,
    body: `正文 ${id}`,
    reply_to_handle: null,
    likes: 0,
    liked: false,
    can_delete: true,
    reply_count: 0,
    replies_next_cursor: null,
    created_at: '2026-09-20T00:00:00Z',
    ...extra,
  }
}

function detail(id: string, thread: FeedbackComment[]): FeedbackDetail {
  return {
    id,
    title: '提交反馈时抽屉被手机键盘顶住',
    supports: 0,
    supported: false,
    comments: thread.length,
    thread,
  } as unknown as FeedbackDetail
}

beforeEach(() => {
  setActivePinia(createPinia())
  getFeedback.mockReset()
  getFeedbackCounts.mockReset()
  likeFeedbackComment.mockReset()
  unlikeFeedbackComment.mockReset()
  deleteFeedbackComment.mockReset()
  getFeedbackCounts.mockResolvedValue({ all: 0, hot: 0, active: 0, resolved: 0, unread: 0 })
})

describe('评论点赞', () => {
  it('用服务端回的计数，不是本地 +1', async () => {
    getFeedback.mockResolvedValue(detail('fb-1', [c('c-1', { likes: 3 })]))
    // 3+1 也是 4：用 4 就分辨不出「用了服务端回的计数」和「本地自己加了个 1」。
    // 别人也在点的时候，本地那个数从来没存在过。
    likeFeedbackComment.mockResolvedValue({ count: 41, liked: true })

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    await store.toggleCommentLike('fb-1', 'c-1')

    expect(likeFeedbackComment).toHaveBeenCalledWith('fb-1', 'c-1')
    expect(store.detail?.thread[0].likes).toBe(41)
    expect(store.detail?.thread[0].liked).toBe(true)
  })

  it('赞过的那条走取消，且不刷新 Tab 计数', async () => {
    getFeedback.mockResolvedValue(detail('fb-1', [c('c-1', { likes: 1, liked: true })]))
    unlikeFeedbackComment.mockResolvedValue({ count: 0, liked: false })

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    await store.toggleCommentLike('fb-1', 'c-1')

    expect(unlikeFeedbackComment).toHaveBeenCalledWith('fb-1', 'c-1')
    expect(likeFeedbackComment).not.toHaveBeenCalled()
    expect(store.detail?.thread[0].likes).toBe(0)
    expect(store.detail?.thread[0].liked).toBe(false)
    // 评论赞不进任何一栏 —— 「热门」看的是反馈级的支持数。顺手刷一次计数是白跑。
    expect(getFeedbackCounts).not.toHaveBeenCalled()
  })

  it('评论不在当前这条详情里就什么都不发', async () => {
    // 详情还没加载（或者已经换成别的反馈了）：这里必须静默返回，不能拿一个不在
    // 手上的评论去发请求。
    getFeedback.mockResolvedValue(detail('fb-2', [c('c-9')]))

    const store = useFeedbackStore()
    await store.loadDetail('fb-2')
    await store.toggleCommentLike('fb-2', 'c-1')

    expect(likeFeedbackComment).not.toHaveBeenCalled()
    expect(unlikeFeedbackComment).not.toHaveBeenCalled()
  })

  it('点赞失败要说出来，而不是安静地什么都不发生', async () => {
    getFeedback.mockResolvedValue(detail('fb-1', [c('c-1', { likes: 3 })]))
    likeFeedbackComment.mockRejectedValue(new Error('只能删除自己的评论'))

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    await store.toggleCommentLike('fb-1', 'c-1')

    expect(store.error).toBeTruthy()
    // 失败了就不该动屏幕上那个数字。
    expect(store.detail?.thread[0].likes).toBe(3)
    expect(store.detail?.thread[0].liked).toBe(false)
  })
})

describe('删评论', () => {
  it('删顶层评论时，它下面的回复一起从本地拿掉', async () => {
    const thread = [
      c('c-1'),
      c('c-2', { parent_id: 'c-1' }),
      c('c-3', { parent_id: 'c-1' }),
      c('c-4'), // 另一栋楼，不能跟着没
    ]
    getFeedback.mockResolvedValue(detail('fb-1', thread))
    deleteFeedbackComment.mockResolvedValue({ deleted: true })

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    expect(store.detail?.comments).toBe(4)

    await store.deleteComment('fb-1', 'c-1')

    expect(deleteFeedbackComment).toHaveBeenCalledWith('fb-1', 'c-1')
    expect(store.detail?.thread.map((x) => x.id)).toEqual(['c-4'])
    // 计数跟着实际拿掉的行数走（3 行），不是「删了一条就减一」。
    expect(store.detail?.comments).toBe(1)
  })

  it('删一条回复只拿掉它自己', async () => {
    const thread = [c('c-1'), c('c-2', { parent_id: 'c-1' }), c('c-3', { parent_id: 'c-1' })]
    getFeedback.mockResolvedValue(detail('fb-1', thread))
    deleteFeedbackComment.mockResolvedValue({ deleted: true })

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    await store.deleteComment('fb-1', 'c-2')

    expect(store.detail?.thread.map((x) => x.id)).toEqual(['c-1', 'c-3'])
    expect(store.detail?.comments).toBe(2)
  })

  it('服务端拒绝时本地一行都不动', async () => {
    getFeedback.mockResolvedValue(detail('fb-1', [c('c-1', { can_delete: false })]))
    deleteFeedbackComment.mockRejectedValue(new Error('只能删除自己的评论'))

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    await store.deleteComment('fb-1', 'c-1')

    expect(store.error).toBe('只能删除自己的评论')
    expect(store.detail?.thread.map((x) => x.id)).toEqual(['c-1'])
    expect(store.detail?.comments).toBe(1)
  })
})
