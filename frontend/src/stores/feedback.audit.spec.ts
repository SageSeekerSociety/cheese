/**
 * 对抗审计查出来的两条翻页缺陷（2026-09-22）。
 *
 * 两条都是**读代码看不出来**的那种：单独看 `loadMoreList` 写得头头是道，看
 * `appendPage` 也对，错在两者接起来的地方 —— 而且错出来的样子不是报错，是一颗
 * 看着好好的按钮。
 *
 * 1. **偏移量取的是去重之后的长度**。`page_start` 是服务端的**位置偏移**，不是游标；
 *    中间有人提了一条新的，`items.length` 就比服务端眼里「我们已经翻到哪儿」小，
 *    于是下一页回的全是手上已有的行、被 `appendPage` 当重复丢掉 —— 长度不动，而
 *    「还有没有下一页」正是拿长度比的（`items.length < total`）。屏幕上是「已显示
 *    51 / 共 52 条」加一颗点得下去、点了什么也不发生的按钮。
 * 2. **缓存命中那次背后的重拉没上锁**。命中缓存时 `loading` 故意留 false（缓存先画，
 *    不闪骨架），于是这段窗口里 `loadMoreList` 的门是开的：它按**旧的长列表**算
 *    偏移量，而重拉马上把 `items` 换成短的第一页，两个响应接起来就漏掉中间一整段，
 *    并且再也补不上（下一次的偏移量已经走过那一段了）。
 */
import type { FeedbackCard } from '@/cx_types'

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const listFeedback = vi.fn()
const listMyFeedback = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listFeedback: (...a: unknown[]) => listFeedback(...a),
    listMyFeedback: (...a: unknown[]) => listMyFeedback(...a),
  }
})

import { resetFeedbackCaches, useFeedbackStore } from '@/stores/feedback'

function card(id: string): FeedbackCard {
  return {
    id,
    title: `反馈 ${id}`,
    supports: 0,
    supported: false,
    comments: 0,
  } as unknown as FeedbackCard
}

/** 连续 n 条，id 是 `c0..c{n-1}`。 */
const range = (n: number, from = 0) => Array.from({ length: n }, (_, i) => card(`c${from + i}`))

function page(items: FeedbackCard[], total = items.length) {
  return {
    data: items,
    total,
    counts: { all: total, hot: 0, active: 0, resolved: 0, unread: 0 },
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

beforeEach(() => {
  setActivePinia(createPinia())
  resetFeedbackCaches()
  listFeedback.mockReset()
  listMyFeedback.mockReset()
})

describe('有人插队时「加载更多」仍然往下走', () => {
  it('偏移量按「服务端交回来过的行数」走，按钮也会收敛', async () => {
    const store = useFeedbackStore()
    const held = range(50)
    listFeedback.mockResolvedValueOnce(page(held, 52))
    await store.loadList()
    expect(store.listHasMore).toBe(true)

    // 有人新提了一条：服务端说 53 条，`page_start=50` 这一页于是回旧的 49、50、51
    // （全被那条新反馈往下顶了一格）—— 头一条是重复，后两条是新的。
    listFeedback.mockResolvedValueOnce(page([held[49]!, card('c50'), card('c51')], 53))
    await store.loadMoreList()
    expect(store.items).toHaveLength(52)
    expect(listFeedback.mock.calls[1]![0]).toMatchObject({ pageStart: 50 })

    // **修复前这里会红**：`items.length`(52) < `total`(53) 恒成立，按钮永远在，再点
    // 也只是从 stale 的偏移量上把窗口里最后那一条重复取回来 —— 长度不动，于是永远
    // 卡在那里。修复后服务端那个窗口已经交完（取回 53 == 总数 53），按钮自己消失。
    expect(store.listHasMore).toBe(false)
    await store.loadMoreList()
    expect(listFeedback).toHaveBeenCalledTimes(2)
  })

  it('「我的反馈」那一份是同一副药', async () => {
    const store = useFeedbackStore()
    const held = range(50)
    listMyFeedback.mockResolvedValueOnce(page(held, 52))
    await store.loadMine()
    expect(store.mineHasMore).toBe(true)

    listMyFeedback.mockResolvedValueOnce(page([held[48]!, held[49]!], 53))
    await store.loadMoreMine()
    expect(listMyFeedback.mock.calls[1]![0]).toMatchObject({ pageStart: 50 })

    listMyFeedback.mockResolvedValueOnce(page([card('tail')], 53))
    await store.loadMoreMine()
    expect(listMyFeedback.mock.calls[2]![0]).toMatchObject({ pageStart: 52 })
    expect(store.mineHasMore).toBe(false)
  })
})

describe('缓存命中那次背后的重拉不许被「加载更多」插队', () => {
  it('重拉在飞的时候，翻页请求根本不发', async () => {
    const store = useFeedbackStore()
    listFeedback.mockResolvedValueOnce(page(range(50), 100))
    await store.loadList()
    expect(listFeedback).toHaveBeenCalledTimes(1)

    // 重新挂载：命中缓存 → `loading` 保持 false（缓存先画），背后重拉第一页。
    const refresh = deferred<ReturnType<typeof page>>()
    listFeedback.mockReturnValueOnce(refresh.promise)
    const pending = store.loadList()
    expect(store.loading).toBe(false)
    expect(store.items).toHaveLength(50)

    // 这段窗口里点「加载更多」：偏移量按旧的长列表算是错的，那一次重拉马上会把
    // `items` 换成第一页 —— 两个响应接起来会让中间那一整段永久消失。
    await store.loadMoreList()
    expect(listFeedback).toHaveBeenCalledTimes(2)

    refresh.resolve(page(range(50), 100))
    await pending
    // 重拉落地之后偏移量跟着第一页走，用户接着翻是干净的。
    expect(store.listRequested).toBe(50)
  })
})
