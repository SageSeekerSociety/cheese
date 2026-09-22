/**
 * 「加载更多」、按类型的表单字段、以及表单落盘。
 *
 * 三件各自有失败形状的事：
 *
 * 1. **翻页不能把同一段接两遍**。`page_start` 是偏移量不是游标，翻页期间有人提了
 *    新的、或者某条换了排序，第 2 页就会把第 1 页尾巴上的那一条再带回来 —— 屏幕上
 *    两张一模一样的卡片，`:key` 还会撞，于是「多一条」和「少一条」同时发生。
 * 2. **翻页失败不许把已经画出来的清掉**。这一点和 `loadList` 正好相反（那里失败
 *    的是整页的内容）—— 用「看不见任何东西」去换一个本来就没拿到的增量，是倒贴。
 * 3. **表单按类型收的字段，只能出现在问过它的那一类上**。选了 bug 填了重现、又改
 *    成建议之后，那半段字还在 `draft` 里；一起发上去就是替用户声明了他没说过的事。
 *
 * 落盘那一组是给「页面自己刷新」（发版时 service worker 会 reload，见 pwa.ts）钉的：
 * 那一下没有「关抽屉」，写了一半的反馈只能靠盘上那份找回。
 */
import type { FeedbackCard, FeedbackDetail } from '@/cx_types'
import type { FeedbackDraft } from '@/stores/feedback'

import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const listFeedback = vi.fn()
const listMyFeedback = vi.fn()
const getFeedback = vi.fn()
const createFeedback = vi.fn()
const acceptFeedbackProposal = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listFeedback: (...a: unknown[]) => listFeedback(...a),
    listMyFeedback: (...a: unknown[]) => listMyFeedback(...a),
    getFeedback: (...a: unknown[]) => getFeedback(...a),
    createFeedback: (...a: unknown[]) => createFeedback(...a),
    acceptFeedbackProposal: (...a: unknown[]) => acceptFeedbackProposal(...a),
  }
})

import { forgetFeedbackDraft, loadFeedbackDraft } from '@/lib/feedbackDraft'
import { resetFeedbackCaches, useFeedbackStore } from '@/stores/feedback'

function card(id: string): FeedbackCard {
  return { id, title: `反馈 ${id}`, supports: 0, supported: false, comments: 0 } as unknown as FeedbackCard
}

function detail(id: string): FeedbackDetail {
  return { id, title: `反馈 ${id}`, supports: 0, supported: false, comments: 0 } as unknown as FeedbackDetail
}

/** 一页。`total` 默认等于这一页的条数（**不是**累计数）—— 用例自己给。 */
function page(items: FeedbackCard[], total = items.length) {
  return { data: items, total, counts: { all: total, hot: 0, active: 0, resolved: 0, unread: 0 } }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

const ids = (cards: { id: string }[]) => cards.map((c) => c.id)

beforeEach(() => {
  setActivePinia(createPinia())
  resetFeedbackCaches()
  // 落盘那一组用的是真 localStorage，它跨用例活着 —— 不清的话，上一个用例写进去的
  // 「打了一半的反馈」会被下一个用例的 `openSubmit()` 捞回来，看着像它自己冒出来的。
  localStorage.clear()
  for (const m of [listFeedback, listMyFeedback, getFeedback, createFeedback, acceptFeedbackProposal]) m.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('「加载更多」问的是同一个问题', () => {
  it('第 2 页带着和第一页一样的四个筛选', async () => {
    // 这一条是补的：`loadList` 接上了四个筛选，而 `loadMoreList` 当时还是手写的
    // `{tab, q, pageStart}` —— 表现是**第 2 页起筛选整个失效**，不带筛选的行接在
    // 筛选结果后面，而两份请求各自看着都对。现在两处共用 `_listQuestion()`。
    listFeedback.mockResolvedValueOnce({ data: [card('fb-1')], total: 3, counts: {} })
    const store = useFeedbackStore()
    await store.loadList()

    store.setFilter({ kind: 'bug', days: 7, author: 'ligan' })
    listFeedback.mockResolvedValueOnce({ data: [card('fb-1')], total: 3, counts: {} })
    await store.loadList()

    listFeedback.mockResolvedValueOnce({ data: [card('fb-2')], total: 3, counts: {} })
    await store.loadMoreList()

    expect(listFeedback).toHaveBeenLastCalledWith(
      expect.objectContaining({
        kind: 'bug',
        author: 'ligan',
        since: expect.any(String),
        pageStart: expect.any(Number),
      })
    )
  })
})

describe('加载更多 · 公开列表', () => {
  it('按手上这一页的长度当偏移量，接在后面', async () => {
    const store = useFeedbackStore()
    listFeedback.mockResolvedValueOnce(page([card('a'), card('b')], 4))
    await store.loadList()

    listFeedback.mockResolvedValueOnce(page([card('c'), card('d')], 4))
    await store.loadMoreList()

    // 四个筛选一起带着（不筛时是空值）—— 它们和 `tab` / `q` 同源，见 `_listQuestion`。
    expect(listFeedback).toHaveBeenLastCalledWith({
      tab: 'all',
      q: '',
      pageSize: 50,
      pageStart: 2,
      author: '',
      kind: null,
      status: null,
      since: null,
    })
    expect(ids(store.items)).toEqual(['a', 'b', 'c', 'd'])
    expect(store.listHasMore).toBe(false)
  })

  it('服务端把已经在屏幕上的一条又发回来时，只留一份', async () => {
    const store = useFeedbackStore()
    listFeedback.mockResolvedValueOnce(page([card('a'), card('b')], 4))
    await store.loadList()

    // 偏移量翻页的正常产物：中间有人提了新的，于是 b 落到了第 2 页的开头。
    listFeedback.mockResolvedValueOnce(page([card('b'), card('c')], 4))
    await store.loadMoreList()

    expect(ids(store.items)).toEqual(['a', 'b', 'c'])
  })

  it('新问题的第一页还在飞的时候按不动', async () => {
    const store = useFeedbackStore()
    listFeedback.mockResolvedValueOnce(page([card('a'), card('b')], 4))
    await store.loadList()

    // 换栏位：缓存必然未命中（键里有栏位），于是 `loading` 起来，而**手上那份数组
    // 还是上一栏的** —— 这正是那条门唯一挡不住自己的形状：`items.length` 和 `total`
    // 都还在，`listHasMore` 看着仍然是 true，偏移量却是在给两个栏位之间的一个位置
    // 编号。接上去就是屏幕上出现一段谁也不记得的空档。
    store.tab = 'hot'
    const hot = deferred<ReturnType<typeof page>>()
    listFeedback.mockReturnValueOnce(hot.promise)
    const switching = store.loadList()
    expect(store.loading).toBe(true)
    expect(store.listHasMore).toBe(true)

    await store.loadMoreList()
    expect(listFeedback).toHaveBeenCalledTimes(2)

    hot.resolve(page([card('hot-1')], 1))
    await switching
    expect(ids(store.items)).toEqual(['hot-1'])
  })

  it('连点两下只发一次', async () => {
    const store = useFeedbackStore()
    listFeedback.mockResolvedValueOnce(page([card('a')], 3))
    await store.loadList()

    const second = deferred<ReturnType<typeof page>>()
    listFeedback.mockReturnValueOnce(second.promise)
    const a = store.loadMoreList()
    const b = store.loadMoreList()
    expect(listFeedback).toHaveBeenCalledTimes(2)

    second.resolve(page([card('b'), card('c')], 3))
    await Promise.all([a, b])
    expect(ids(store.items)).toEqual(['a', 'b', 'c'])
    expect(store.loadingMore).toBe(false)
  })

  it('已经到底就不再问', async () => {
    const store = useFeedbackStore()
    listFeedback.mockResolvedValueOnce(page([card('a'), card('b')]))
    await store.loadList()

    await store.loadMoreList()
    expect(listFeedback).toHaveBeenCalledTimes(1)
  })

  it('翻页期间换了栏位，回来的那一页不接上去', async () => {
    const store = useFeedbackStore()
    listFeedback.mockResolvedValueOnce(page([card('a')], 3))
    await store.loadList()

    const stale = deferred<ReturnType<typeof page>>()
    listFeedback.mockReturnValueOnce(stale.promise)
    const more = store.loadMoreList()

    // 换栏位：这是**另一个问题**了，手上那份马上被换掉。
    listFeedback.mockResolvedValueOnce(page([card('hot-1')], 1))
    await store.setTab('hot')

    stale.resolve(page([card('b'), card('c')], 3))
    await more

    expect(ids(store.items)).toEqual(['hot-1'])
  })

  it('失败时留着已经画出来的那些，只在上面说一句', async () => {
    const store = useFeedbackStore()
    listFeedback.mockResolvedValueOnce(page([card('a'), card('b')], 4))
    await store.loadList()

    listFeedback.mockRejectedValueOnce(new Error('加载更多失败'))
    await store.loadMoreList()

    expect(ids(store.items)).toEqual(['a', 'b'])
    expect(store.error).toBe('加载更多失败')
    expect(store.loadingMore).toBe(false)
  })
})

describe('加载更多 · 我的反馈', () => {
  it('同上，偏移量按 mineItems 算', async () => {
    const store = useFeedbackStore()
    listMyFeedback.mockResolvedValueOnce(page([card('m1')], 2))
    await store.loadMine()

    listMyFeedback.mockResolvedValueOnce(page([card('m2')], 2))
    await store.loadMoreMine()

    expect(listMyFeedback).toHaveBeenLastCalledWith({ pageSize: 50, pageStart: 1 })
    expect(ids(store.mineItems)).toEqual(['m1', 'm2'])
    expect(store.mineLoadingMore).toBe(false)
  })
})

describe('热门那一栏的三个数', () => {
  it('服务端给了就用服务端的', async () => {
    const store = useFeedbackStore()
    store.meta = { hot_score: 3, hot_half_life_days: 7, hot_min_items: 4 } as never
    expect([store.hotThreshold, store.hotHalfLifeDays, store.hotMinItems]).toEqual([3, 7, 4])
  })

  it('没给时兜底和服务端常量一致，不说一个空', () => {
    const store = useFeedbackStore()
    store.meta = null
    expect([store.hotThreshold, store.hotHalfLifeDays, store.hotMinItems]).toEqual([2, 14, 5])
  })
})

describe('按类型收的那两栏', () => {
  /** 只写这一个用例关心的那几栏：`openSubmit` 接的就是 `Partial<FeedbackDraft>`，
   *  `attachments` / `visibility` 这些和本条用例无关的由它自己补默认值。 */
  async function bodyFor(draft: Partial<FeedbackDraft>): Promise<Record<string, unknown>> {
    const store = useFeedbackStore()
    createFeedback.mockResolvedValueOnce(detail('new'))
    store.openSubmit(draft)
    await store.submit()
    return createFeedback.mock.calls[0]?.[0] as Record<string, unknown>
  }

  it('bug：重现和期望都发', async () => {
    const body = await bodyFor({ kind: 'bug', title: '点不动', body: '正文', repro: '点两下', expectation: '应该打开' })
    expect(body.repro).toBe('点两下')
    expect(body.expectation).toBe('应该打开')
  })

  it('建议：只发期望', async () => {
    // 表单上没问过「怎么重现」—— 人换过类型之后留在 draft 里的那半段也不许跟着走。
    const body = await bodyFor({
      kind: 'suggestion',
      title: '加个筛选',
      body: '正文',
      repro: '改类型之前填的',
      expectation: '按作者筛',
    })
    expect(body.repro).toBeNull()
    expect(body.expectation).toBe('按作者筛')
  })

  it('其它：两栏都不发', async () => {
    const body = await bodyFor({ kind: 'other', title: '随便说', body: '正文', repro: 'x', expectation: 'y' })
    expect(body.repro).toBeNull()
    expect(body.expectation).toBeNull()
  })

  it('bug 上人没写时，用 agent 带过来的那段现场', async () => {
    const body = await bodyFor({
      kind: 'bug',
      title: 'typo',
      body: '正文',
      repro: '',
      expectation: '',
      attachContext: true,
      fromAgent: { whatHappened: '崩了', repro: '跑 cheese x', evidence: 'trace-1' },
    })
    expect(body.repro).toBe('跑 cheese x')
    expect(body.what_happened).toBe('崩了')
  })
})

describe('表单落盘', () => {
  it('关抽屉时收尾写下去，下次裸开捞回来', () => {
    const store = useFeedbackStore()
    store.openSubmit()
    store.draft.title = '写了一半'
    store.draft.body = '正文'
    store.closeSubmit()

    // 换一个 store 实例（模拟刷新之后的新页面），盘上那份照样在。
    setActivePinia(createPinia())
    const fresh = useFeedbackStore()
    fresh.openSubmit()
    expect(fresh.draft.title).toBe('写了一半')
    expect(fresh.draft.body).toBe('正文')
  })

  it('防抖窗口里那几下也算数', async () => {
    vi.useFakeTimers()
    const store = useFeedbackStore()
    store.openSubmit()
    store.draft.title = '正在打'
    store.touchDraft()
    // 还没到写盘那一刻。
    expect(loadFeedbackDraft()).toBeNull()
    await vi.advanceTimersByTimeAsync(400)
    expect(loadFeedbackDraft()?.title).toBe('正在打')
  })

  it('提交成功清掉「正在写的」那一格', async () => {
    const store = useFeedbackStore()
    createFeedback.mockResolvedValueOnce(detail('new'))
    store.openSubmit()
    store.draft.title = '要提的'
    store.draft.body = '正文'
    store.closeSubmit()
    expect(loadFeedbackDraft()).not.toBeNull()

    store.openSubmit()
    await store.submit()
    expect(loadFeedbackDraft()).toBeNull()
  })

  it('提交失败把盘上那份留着', async () => {
    const store = useFeedbackStore()
    createFeedback.mockRejectedValueOnce(new Error('提交失败'))
    store.openSubmit()
    store.draft.title = '要提的'
    store.draft.body = '正文'
    await store.submit()
    expect(loadFeedbackDraft()?.title).toBe('要提的')
  })

  it('提案卡整份替换表单时，人自己那份被收起来而不是丢掉', async () => {
    const store = useFeedbackStore()
    // 人自己写了半条。
    store.openSubmit()
    store.draft.title = '我自己那半条'
    store.draft.body = '正文'

    // 会话里那张卡按「提交反馈」：整份替换。
    store.openSubmit({ title: 'agent 发现的', body: '卡上的正文', proposal: { topicId: 't', blockId: 'b' } })
    expect(store.draft.title).toBe('agent 发现的')

    // 卡片这份在手上，所以裸开还是它（一次只回来一份，不然两份会来回顶）。
    store.openSubmit()
    expect(store.draft.title).toBe('agent 发现的')

    // 卡片那份提交掉之后，人自己那半条回来。
    acceptFeedbackProposal.mockResolvedValueOnce(detail('from-card'))
    await store.submit()
    store.openSubmit()
    expect(store.draft.title).toBe('我自己那半条')

    forgetFeedbackDraft()
    expect(loadFeedbackDraft()).toBeNull()
  })
})

describe('裸开表单不覆盖手上那份', () => {
  it('手上已经有字就接着写，不去捞盘上旧的', async () => {
    const store = useFeedbackStore()
    // 盘上先放一份「旧的」。
    store.openSubmit()
    store.draft.title = '旧的'
    store.closeSubmit()

    store.openSubmit()
    store.draft.title = '新的'
    store.openSubmit()
    expect(store.draft.title).toBe('新的')
  })

  it('空表单不会被当成「有东西」写进盘里', () => {
    const store = useFeedbackStore()
    store.openSubmit()
    store.closeSubmit()
    expect(loadFeedbackDraft()).toBeNull()
  })
})
