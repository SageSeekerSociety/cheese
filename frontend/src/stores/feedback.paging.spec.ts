/**
 * 评论翻页的四个接缝。每一条都是「不钉住就会悄悄坏、而且坏得不像坏」的那种：
 *
 *   1. **同一个游标取两遍 = 屏幕上两条一模一样的评论**。`loadMoreComments` 的重入闸
 *      不是防连点的手感问题：`thread` 是追加的，第二次请求把同一段 `items` 再接一遍，
 *      连 `:key` 都会撞。所以这里发一个**卡住的**请求，在它回来的路上再点一次。
 *   2. **游标是服务端给的，客户端不许自己编**。下一页从哪开始只有服务端知道（真实
 *      实现是一个「时间戳 + uuid」的不透明串），前端原样送回去就行 —— 自己按 index 或
 *      按时间算一套，等服务端换了编码方式就悄悄错位。
 *   3. **迟到的页不许落到换了的那条详情上**。翻页请求在飞的这几百毫秒里，人可能已经
 *      点开了另一条反馈；那一页评论属于上一条，落进来就是「这条反馈下面挂着别人的
 *      评论」。
 *   4. **失败不改手上的东西**。游标留在原地，人再点一次就是重试；把游标清了，这一栋
 *      楼剩下的回复就再也取不回来了。
 */
import type { FeedbackComment, FeedbackDetail } from '@/cx_types'

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getFeedback = vi.fn()
const getFeedbackCounts = vi.fn()
const listFeedbackComments = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getFeedback: (...a: unknown[]) => getFeedback(...a),
    getFeedbackCounts: (...a: unknown[]) => getFeedbackCounts(...a),
    listFeedbackComments: (...a: unknown[]) => listFeedbackComments(...a),
  }
})

import { useFeedbackStore } from '@/stores/feedback'

function c(id: string, extra: Partial<FeedbackComment> = {}): FeedbackComment {
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
    ...extra,
  }
}

function detail(id: string, thread: FeedbackComment[], extra: Partial<FeedbackDetail> = {}): FeedbackDetail {
  return {
    id,
    title: '提交反馈时抽屉被手机键盘顶住',
    supports: 0,
    supported: false,
    // 卡片上那个计数是**总数**，不是这一页的条数 —— 翻页不许让它变小。
    comments: thread.length,
    thread,
    thread_next_cursor: null,
    ...extra,
  } as unknown as FeedbackDetail
}

/** 一个手动挡的 promise：请求「发出去、还没回来」这个中间态是这几条用例的全部内容，
 *  所以回来的时机得攥在测试手里。 */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

beforeEach(() => {
  setActivePinia(createPinia())
  getFeedback.mockReset()
  getFeedbackCounts.mockReset()
  listFeedbackComments.mockReset()
  getFeedbackCounts.mockResolvedValue({ all: 0, hot: 0, active: 0, resolved: 0, unread: 0 })
})

describe('顶层评论的下一页', () => {
  it('把服务端那一段接在后面，并把新的游标记下来', async () => {
    getFeedback.mockResolvedValue(detail('fb-1', [c('c-1')], { thread_next_cursor: 'CUR-1' }))
    listFeedbackComments.mockResolvedValue({ items: [c('c-2')], next_cursor: 'CUR-2' })

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    await store.loadMoreComments('fb-1')

    expect(listFeedbackComments).toHaveBeenCalledWith('fb-1', { after: 'CUR-1' })
    expect(store.detail?.thread.map((x) => x.id)).toEqual(['c-1', 'c-2'])
    expect(store.detail?.thread_next_cursor).toBe('CUR-2')
    // 卡片上那个数是服务端算的总数，翻出来的这一条不许把它顶掉。
    expect(store.detail?.comments).toBe(1)
  })

  it('同一个游标取两遍只会发一次请求', async () => {
    getFeedback.mockResolvedValue(detail('fb-1', [c('c-1')], { thread_next_cursor: 'CUR-1' }))
    const pending = deferred<{ items: FeedbackComment[]; next_cursor: string | null }>()
    listFeedbackComments.mockReturnValue(pending.promise)

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    const first = store.loadMoreComments('fb-1')
    const second = store.loadMoreComments('fb-1')
    expect(listFeedbackComments).toHaveBeenCalledTimes(1)
    expect(store.moreCommentsLoading).toBe(true)

    pending.resolve({ items: [c('c-2')], next_cursor: null })
    await Promise.all([first, second])

    // 两次请求的话这里会是 ['c-1','c-2','c-2']。
    expect(store.detail?.thread.map((x) => x.id)).toEqual(['c-1', 'c-2'])
    expect(store.moreCommentsLoading).toBe(false)
  })

  it('游标走到底之后不再发请求', async () => {
    getFeedback.mockResolvedValue(detail('fb-1', [c('c-1')]))

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    await store.loadMoreComments('fb-1')

    expect(listFeedbackComments).not.toHaveBeenCalled()
  })

  it('取失败要说出来，游标留在原地等着重试', async () => {
    getFeedback.mockResolvedValue(detail('fb-1', [c('c-1')], { thread_next_cursor: 'CUR-1' }))
    listFeedbackComments.mockRejectedValue(new Error('评论加载失败'))

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    await store.loadMoreComments('fb-1')

    expect(store.error).toBeTruthy()
    expect(store.detail?.thread.map((x) => x.id)).toEqual(['c-1'])
    expect(store.detail?.thread_next_cursor).toBe('CUR-1')
  })

  it('迟到的这一页不会落到换过去的那条详情上', async () => {
    getFeedback.mockResolvedValueOnce(detail('fb-1', [c('c-1')], { thread_next_cursor: 'CUR-1' }))
    getFeedback.mockResolvedValueOnce(detail('fb-2', [c('x-1')]))
    const pending = deferred<{ items: FeedbackComment[]; next_cursor: string | null }>()
    listFeedbackComments.mockReturnValue(pending.promise)

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    const inFlight = store.loadMoreComments('fb-1')
    // 人在这几百毫秒里点开了另一条反馈。
    await store.loadDetail('fb-2')
    pending.resolve({ items: [c('c-2')], next_cursor: 'CUR-2' })
    await inFlight

    // 上一条反馈的第二页落进来的话，屏幕上就是「这条反馈下面挂着别人的评论」。
    expect(store.detail?.id).toBe('fb-2')
    expect(store.detail?.thread.map((x) => x.id)).toEqual(['x-1'])
    expect(store.detail?.thread_next_cursor).toBeNull()
  })
})

describe('楼内的下一页回复', () => {
  it('只接在它那一栋楼后面，并记住那一栋自己的游标', async () => {
    const thread = [
      c('t-1', { reply_count: 3, replies_next_cursor: 'R-1' }),
      c('r-1', { parent_id: 't-1' }),
      c('t-2', { reply_count: 1 }),
      c('r-9', { parent_id: 't-2' }),
    ]
    getFeedback.mockResolvedValue(detail('fb-1', thread))
    listFeedbackComments.mockResolvedValue({
      items: [c('r-2', { parent_id: 't-1' })],
      next_cursor: null,
    })

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    await store.loadMoreReplies('fb-1', 't-1')

    // 游标是那一栋楼自己发的，客户端原样送回去，也不去动别的楼。
    expect(listFeedbackComments).toHaveBeenCalledWith('fb-1', { parentId: 't-1', after: 'R-1' })
    const ids = store.detail?.thread.map((x) => x.id)
    expect(ids).toContain('r-2')
    expect(store.detail?.thread.find((x) => x.id === 't-1')?.replies_next_cursor).toBeNull()
    expect(store.moreRepliesLoading['t-1']).toBeFalsy()
    // 另一栋楼的游标不该被顺手改掉（它本来就是 null，取完这一栋还得是 null）。
    expect(store.detail?.thread.find((x) => x.id === 't-2')?.replies_next_cursor).toBeNull()
  })

  it('这一栋正在取的时候，第二下不发请求', async () => {
    getFeedback.mockResolvedValue(detail('fb-1', [c('t-1', { reply_count: 3, replies_next_cursor: 'R-1' })]))
    const pending = deferred<{ items: FeedbackComment[]; next_cursor: string | null }>()
    listFeedbackComments.mockReturnValue(pending.promise)

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    const first = store.loadMoreReplies('fb-1', 't-1')
    const second = store.loadMoreReplies('fb-1', 't-1')
    expect(listFeedbackComments).toHaveBeenCalledTimes(1)

    pending.resolve({ items: [c('r-1', { parent_id: 't-1' })], next_cursor: null })
    await Promise.all([first, second])
    expect(store.detail?.thread.filter((x) => x.id === 'r-1').length).toBe(1)
  })

  it('这一栋楼整个被删了，取回来的那几条就不再挂上去', async () => {
    getFeedback.mockResolvedValue(detail('fb-1', [c('t-1', { reply_count: 3, replies_next_cursor: 'R-1' })]))
    const pending = deferred<{ items: FeedbackComment[]; next_cursor: string | null }>()
    listFeedbackComments.mockReturnValue(pending.promise)

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    const inFlight = store.loadMoreReplies('fb-1', 't-1')
    // 请求在飞的时候这栋楼没了（这里直接把它从详情里摘掉，和删除落下来是同一个形状）。
    store.detail!.thread = []
    pending.resolve({ items: [c('r-1', { parent_id: 't-1' })], next_cursor: null })
    await inFlight

    // 落进来就是一条查不到父亲的孤儿回复：界面上它是「不属于任何一栋楼」的。
    expect(store.detail?.thread).toEqual([])
  })
})
