/**
 * 列表 / 详情的**先画后刷**，以及 `counts` 那一个写入口。
 *
 * 三件要钉住的事，每一件都有它自己的失败形状：
 *
 * 1. **命中缓存只许「先画」，不许「当答案」** —— 回来之后仍然要重拉一次。少了
 *    重拉，屏幕上是上一次打开时的世界，而它没有任何东西能知道自己旧了。
 * 2. **缓存里不许放 counts**。一个存下来的数字回答不了「它现在还是不是真的」，
 *    而计数正是「别人刚刚提了一条」唯一会变的地方。
 * 3. **号码大的赢，与到达顺序无关**（`_commitCounts`）。少了它，进详情推一次
 *    已读、同时一个更早发出的列表请求后到，未读数就跳回推之前的值 —— 而下一轮
 *    刷新又自己好了，是最难查的那一种。
 *
 * 这个文件里的模块级缓存（`listCache` / `mineCache` / `detailCache`）**跨用例活
 * 着**，所以每个用例开头都要 `resetFeedbackCaches()`。这不是测试的权宜之计：真实
 * 的换人登录走的也是它。
 */
import type { FeedbackCard, FeedbackCounts, FeedbackDetail } from '@/cx_types'

import { watch } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const listFeedback = vi.fn()
const listMyFeedback = vi.fn()
const listAdminFeedback = vi.fn()
const getFeedback = vi.fn()
const getFeedbackCounts = vi.fn()
const markFeedbackRead = vi.fn()
const createFeedback = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listFeedback: (...a: unknown[]) => listFeedback(...a),
    listMyFeedback: (...a: unknown[]) => listMyFeedback(...a),
    listAdminFeedback: (...a: unknown[]) => listAdminFeedback(...a),
    getFeedback: (...a: unknown[]) => getFeedback(...a),
    getFeedbackCounts: (...a: unknown[]) => getFeedbackCounts(...a),
    markFeedbackRead: (...a: unknown[]) => markFeedbackRead(...a),
    createFeedback: (...a: unknown[]) => createFeedback(...a),
  }
})

import { ApiError } from '@/api'
import { resetFeedbackCaches, useFeedbackStore } from '@/stores/feedback'

function card(id: string, supports = 0): FeedbackCard {
  return { id, title: `反馈 ${id}`, supports, supported: false, comments: 0 } as unknown as FeedbackCard
}

function count(over: Partial<FeedbackCounts> = {}): FeedbackCounts {
  return { all: 0, hot: 0, active: 0, resolved: 0, unread: 0, ...over }
}

function page(items: FeedbackCard[], counts: Partial<FeedbackCounts> = {}) {
  return { data: items, total: items.length, counts: count({ all: items.length, ...counts }) }
}

function detail(id: string, over: Partial<FeedbackDetail> = {}): FeedbackDetail {
  return { id, title: `反馈 ${id}`, supports: 0, supported: false, comments: 0, ...over } as unknown as FeedbackDetail
}

/** 一个由用例决定什么时候 resolve 的 promise。「先发的后到」只能这么测。 */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

/** 记下某个值在这一次调用里出现过的所有取值。要的是「**没翻过** true」这种话 ——
 *  只看首尾两帧是看不出来的，中间翻一下再翻回来同样是错的。 */
function record<T>(read: () => T): { values: T[]; stop: () => void } {
  const values: T[] = []
  const stop = watch(read, (v) => values.push(v as T), { flush: 'sync' })
  return { values, stop }
}

beforeEach(() => {
  setActivePinia(createPinia())
  resetFeedbackCaches()
  for (const m of [
    listFeedback,
    listMyFeedback,
    listAdminFeedback,
    getFeedback,
    getFeedbackCounts,
    markFeedbackRead,
    createFeedback,
  ]) {
    m.mockReset()
  }
  getFeedbackCounts.mockResolvedValue(count())
  markFeedbackRead.mockResolvedValue(undefined)
})

describe('列表：命中先画，背后照拉', () => {
  it('同一个 Tab 再进来，不翻 loading，也不拿缓存当答案', async () => {
    listFeedback.mockResolvedValueOnce(page([card('fb-1')]))
    const store = useFeedbackStore()
    await store.loadList()
    expect(store.loading).toBe(false)

    // 第二次：请求卡住不回来。这时候屏幕上**应该已经**是上一次那一页。
    const second = deferred<ReturnType<typeof page>>()
    listFeedback.mockReturnValueOnce(second.promise)
    const seen = record(() => store.loading)
    const pending = store.loadList()
    expect(seen.values).not.toContain(true)
    expect(store.loading).toBe(false)
    expect(store.items.map((i) => i.id)).toEqual(['fb-1'])
    seen.stop()

    // 卡住的那次终于回来，而且**带了一条新的** —— 缓存只是先画，答案仍是它。
    second.resolve(page([card('fb-1'), card('fb-2')]))
    await pending
    expect(store.items.map((i) => i.id)).toEqual(['fb-1', 'fb-2'])
    expect(store.total).toBe(2)
  })

  it('换了 Tab 就是另一个问题，拿缓存顶上去就是答非所问', async () => {
    listFeedback.mockResolvedValueOnce(page([card('fb-1')]))
    const store = useFeedbackStore()
    await store.loadList()

    const hot = deferred<ReturnType<typeof page>>()
    listFeedback.mockReturnValueOnce(hot.promise)
    store.tab = 'hot'
    const pending = store.loadList()
    // 指纹里带 Tab，所以这一次是**冷**的：骨架该出来。
    expect(store.loading).toBe(true)
    hot.resolve(page([card('fb-9')]))
    await pending
    expect(store.items.map((i) => i.id)).toEqual(['fb-9'])
  })

  it('搜索词也算指纹的一部分', async () => {
    listFeedback.mockResolvedValueOnce(page([card('fb-1')]))
    const store = useFeedbackStore()
    await store.loadList()

    const searching = deferred<ReturnType<typeof page>>()
    listFeedback.mockReturnValueOnce(searching.promise)
    store.query = '头像'
    const pending = store.loadList()
    expect(store.loading).toBe(true)
    searching.resolve(page([card('fb-2')]))
    await pending
    // 四个筛选也在请求体里（默认全空 = 不筛）。它们和 `q` 一样是**指纹的一部分**：
    // 少了这一半，换一个筛选时列表会画回上一份缓存。
    expect(listFeedback).toHaveBeenLastCalledWith({
      tab: 'all',
      q: '头像',
      pageSize: 50,
      author: '',
      kind: null,
      status: null,
      since: null,
    })
  })

  it('换一个筛选也算指纹的一部分，而且会真的发给服务端', async () => {
    listFeedback.mockResolvedValueOnce(page([card('fb-1')]))
    const store = useFeedbackStore()
    await store.loadList()

    listFeedback.mockResolvedValueOnce(page([card('fb-2')]))
    store.setFilter({ kind: 'bug' })
    await Promise.resolve()

    expect(listFeedback).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'bug' }))
    // 再切回不筛 —— 指纹跟着变，所以不会画回上面那一份。
    listFeedback.mockResolvedValueOnce(page([card('fb-1')]))
    store.setFilter({ kind: null })
    await Promise.resolve()
    expect(listFeedback).toHaveBeenLastCalledWith(expect.objectContaining({ kind: null }))
  })

  it('刷新失败回到「拉失败」那一态，旧内容不留在屏幕上冒充', async () => {
    listFeedback.mockResolvedValueOnce(page([card('fb-1')]))
    const store = useFeedbackStore()
    await store.loadList()

    // 命中缓存的那一次失败：缓存先画了 fb-1，然后这一次请求挂了。屏幕必须回到
    // 「一条都没有 + 一句错」——「重试」那个按钮就长在空态里，留着旧列表等于把
    // 它一起藏起来。
    listFeedback.mockRejectedValueOnce(new Error('网断了'))
    await store.loadList()
    expect(store.error).toBe('网断了')
    expect(store.items).toEqual([])
    expect(store.total).toBe(0)
  })

  it('冷启动失败仍然是「一条都没有 + 一句错」', async () => {
    listFeedback.mockRejectedValueOnce(new Error('网断了'))
    const store = useFeedbackStore()
    await store.loadList()
    expect(store.items).toEqual([])
    expect(store.total).toBe(0)
    expect(store.error).toBe('网断了')
  })
})

describe('详情：命中先画，背后照拉', () => {
  it('同一条再打开不置空，回来之后换成新的', async () => {
    getFeedback.mockResolvedValueOnce(detail('fb-1', { supports: 3 }))
    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    const first = store.detail
    expect(first).not.toBeNull()

    const second = deferred<FeedbackDetail>()
    getFeedback.mockReturnValueOnce(second.promise)
    const seen = record(() => store.detailLoading)
    const pending = store.loadDetail('fb-1')
    // 还是那一份对象：没被 `this.detail = null` 抹掉。
    expect(store.detail).toBe(first)
    expect(seen.values).not.toContain(true)
    seen.stop()

    second.resolve(detail('fb-1', { supports: 4 }))
    await pending
    expect(store.detail?.supports).toBe(4)
  })

  it('没缓存过的详情仍然是骨架，不是空白', async () => {
    const cold = deferred<FeedbackDetail>()
    getFeedback.mockReturnValueOnce(cold.promise)
    const store = useFeedbackStore()
    const pending = store.loadDetail('fb-2')
    expect(store.detailLoading).toBe(true)
    expect(store.detail).toBeNull()
    cold.resolve(detail('fb-2'))
    await pending
    expect(store.detailLoading).toBe(false)
    expect(store.detail?.id).toBe('fb-2')
  })

  it('服务端说这条看不见了：缓存和屏幕一起清掉，而且报错', async () => {
    getFeedback.mockResolvedValueOnce(detail('fb-1'))
    const store = useFeedbackStore()
    await store.loadDetail('fb-1')

    getFeedback.mockRejectedValueOnce(new ApiError(404, '这条反馈打不开'))
    await store.loadDetail('fb-1')
    expect(store.detail).toBeNull()
    expect(store.error).toBe('这条反馈打不开')

    // 再进一次必须**重新发请求**：缓存里的那份已经删了。判据是同步那一刻的
    // detail —— 命中缓存的话它当场就是那份对象，而不是 null。
    const again = deferred<FeedbackDetail>()
    getFeedback.mockReturnValueOnce(again.promise)
    const pending = store.loadDetail('fb-1')
    expect(store.detail).toBeNull()
    again.resolve(detail('fb-1'))
    await pending
    expect(store.detail).not.toBeNull()
  })

  it('网络抖了一下的那次失败：页面回到「打不开」，但缓存留着，下次还能先画', async () => {
    getFeedback.mockResolvedValueOnce(detail('fb-1'))
    const store = useFeedbackStore()
    await store.loadDetail('fb-1')

    getFeedback.mockRejectedValueOnce(new Error('网断了'))
    await store.loadDetail('fb-1')
    expect(store.detail).toBeNull()
    expect(store.error).toBe('网断了')

    // 缓存没被这次失败牵连掉（它只是网断了，那份内容没被否定）：再进一次仍然
    // 是「先画后拉」，不是骨架。
    const again = deferred<FeedbackDetail>()
    getFeedback.mockReturnValueOnce(again.promise)
    const pending = store.loadDetail('fb-1')
    expect(store.detailLoading).toBe(false)
    expect(store.detail?.id).toBe('fb-1')
    again.resolve(detail('fb-1'))
    await pending
  })

  it('换一条看时，上一条的「正在取回复」不会跟过来', async () => {
    getFeedback.mockResolvedValueOnce(detail('fb-1'))
    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    store.moreRepliesLoading = { 'comment-1': true }

    getFeedback.mockResolvedValueOnce(detail('fb-2'))
    await store.loadDetail('fb-2')
    expect(store.moreRepliesLoading).toEqual({})
  })
})

describe('counts：一个写入口，号码大的赢', () => {
  it('一个更早发出的列表响应，盖不回已读之后的未读数', async () => {
    listFeedback.mockResolvedValueOnce(page([card('fb-1')], { unread: 7 }))
    const store = useFeedbackStore()
    await store.loadList()
    expect(store.counts.unread).toBe(7)

    // 第二次列表请求发出去就卡住。
    const stuck = deferred<ReturnType<typeof page>>()
    listFeedback.mockReturnValueOnce(stuck.promise)
    const pending = store.loadList()

    await store.markReadOnce('fb-read-1')
    expect(store.counts.unread).toBe(0)

    // 那次卡住的列表终于回来，带的却是**推游标之前**的 7。
    stuck.resolve(page([card('fb-1')], { unread: 7 }))
    await pending

    // 它比推游标早发出，所以它说了不算。
    expect(store.counts.unread).toBe(0)
  })

  it('缓存只回放列表，不回放计数', async () => {
    listFeedback.mockResolvedValueOnce(page([card('fb-1')], { unread: 5 }))
    const store = useFeedbackStore()
    await store.loadList()
    expect(store.counts.unread).toBe(5)

    await store.markRead()
    expect(store.counts.unread).toBe(0)

    // 再进同一个 Tab：列表命中缓存先画，但计数**不许**跟着回来。
    const stuck = deferred<ReturnType<typeof page>>()
    listFeedback.mockReturnValueOnce(stuck.promise)
    const pending = store.loadList()
    expect(store.items.map((i) => i.id)).toEqual(['fb-1'])
    expect(store.counts.unread).toBe(0)
    stuck.resolve(page([card('fb-1')], { unread: 0 }))
    await pending
    expect(store.counts.unread).toBe(0)
  })

  it('管理端那一页带的计数也走同一个门', async () => {
    listFeedback.mockResolvedValueOnce(page([card('fb-1')], { unread: 4 }))
    const store = useFeedbackStore()
    await store.loadList()
    expect(store.counts.unread).toBe(4)

    // 管理端那一页回的 counts 是同一份东西，它也得能赢。
    listAdminFeedback.mockResolvedValueOnce({
      data: [card('fb-1')],
      total: 1,
      counts: count({ unread: 0 }),
    })
    await store.loadAdmin()
    expect(store.counts.unread).toBe(0)
  })
})

describe('换人登录', () => {
  it('清掉之后，三份都不会先画上一个人的东西', async () => {
    listFeedback.mockResolvedValueOnce(page([card('fb-1')]))
    listMyFeedback.mockResolvedValueOnce({ data: [card('fb-mine')], total: 1 })
    getFeedback.mockResolvedValueOnce(detail('fb-1'))
    const store = useFeedbackStore()
    await store.loadList()
    await store.loadMine()
    await store.loadDetail('fb-1')

    resetFeedbackCaches()

    // 三份都该回到「冷」：出骨架、发请求，而不是把上一个人的内容画出来。
    const hot = deferred<ReturnType<typeof page>>()
    listFeedback.mockReturnValueOnce(hot.promise)
    const listPending = store.loadList()
    expect(store.loading).toBe(true)

    const mine = deferred<{ data: FeedbackCard[]; total: number }>()
    listMyFeedback.mockReturnValueOnce(mine.promise)
    const minePending = store.loadMine()
    expect(store.mineLoading).toBe(true)

    const one = deferred<FeedbackDetail>()
    getFeedback.mockReturnValueOnce(one.promise)
    const detailPending = store.loadDetail('fb-1')
    expect(store.detailLoading).toBe(true)
    expect(store.detail).toBeNull()

    hot.resolve(page([]))
    mine.resolve({ data: [], total: 0 })
    one.resolve(detail('fb-1'))
    await Promise.all([listPending, minePending, detailPending])
  })
})

describe('提交', () => {
  it('列表缓存一起丢：刚提的那条不会被提交之前那一页挡住', async () => {
    listFeedback.mockResolvedValueOnce(page([card('fb-1')]))
    const store = useFeedbackStore()
    await store.loadList()

    createFeedback.mockResolvedValueOnce(detail('fb-new'))
    // 正文是必填的第二栏（见 stores/feedback.ts 的 `submit`）：只给标题的话提交会直接
    // 被挡回来，而这两条用例要的正是「提交真的落地了」之后的行为。
    store.openSubmit({ title: '新的一条', body: '点了没反应' })
    await store.submit()

    // 下次挂载必须是**真的重拉**：只清屏幕上那份而留着缓存的话，这里会先画回
    // 提交之前那一页（没有 fb-new），看上去就是「我刚提的反馈不见了」。
    const stuck = deferred<ReturnType<typeof page>>()
    listFeedback.mockReturnValueOnce(stuck.promise)
    const pending = store.loadList()
    expect(store.loading).toBe(true)
    expect(store.items).toEqual([])
    stuck.resolve(page([card('fb-new'), card('fb-1')]))
    await pending
    expect(store.items.map((i) => i.id)).toEqual(['fb-new', 'fb-1'])
  })

  it('落地的那条顺手进了详情缓存：紧接着打开它不闪骨架', async () => {
    createFeedback.mockResolvedValueOnce(detail('fb-new'))
    const store = useFeedbackStore()
    // 正文是必填的第二栏（见 stores/feedback.ts 的 `submit`）：只给标题的话提交会直接
    // 被挡回来，而这两条用例要的正是「提交真的落地了」之后的行为。
    store.openSubmit({ title: '新的一条', body: '点了没反应' })
    expect(await store.submit()).toBe('fb-new')

    const stuck = deferred<FeedbackDetail>()
    getFeedback.mockReturnValueOnce(stuck.promise)
    const pending = store.loadDetail('fb-new')
    expect(store.detailLoading).toBe(false)
    expect(store.detail?.id).toBe('fb-new')
    stuck.resolve(detail('fb-new'))
    await pending
  })
})
