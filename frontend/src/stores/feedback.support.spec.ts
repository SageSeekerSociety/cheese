/**
 * 深链直接进详情页时，「支持」必须真的发出去。
 *
 * 「支持」按钮认的是**这一条现在在哪份数据里**（`_find`）。上一版 `_find` 只看
 * `items` 和 `adminItems`，而直接打开 `/feedback/<id>`（刷新、或者别人发来的链接）
 * 时那一页**不拉列表**，两份都是空的 —— 于是 `toggleSupport` 走到 `if (!card) return`
 * 静默返回：按钮点得动、有按下效果、数字一动不动，控制台一句话也没有。
 *
 * 从反馈中心点进去永远踩不到这个坑（列表已经在了），所以这一组钉的是**冷启动**那条路。
 */
import type { FeedbackDetail } from '@/cx_types'

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getFeedback = vi.fn()
const supportFeedback = vi.fn()
const unsupportFeedback = vi.fn()
const getFeedbackCounts = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getFeedback: (...a: unknown[]) => getFeedback(...a),
    getFeedbackCounts: (...a: unknown[]) => getFeedbackCounts(...a),
    supportFeedback: (...a: unknown[]) => supportFeedback(...a),
    unsupportFeedback: (...a: unknown[]) => unsupportFeedback(...a),
  }
})

import { useFeedbackStore } from '@/stores/feedback'

/** 只要卡片那几栏：详情页除了正文，读的就是这些。 */
function detail(supports: number, supported: boolean): FeedbackDetail {
  return {
    id: 'fb-1',
    title: '提交反馈时抽屉被手机键盘顶住',
    supports,
    supported,
    comments: 0,
  } as unknown as FeedbackDetail
}

beforeEach(() => {
  setActivePinia(createPinia())
  getFeedback.mockReset()
  getFeedbackCounts.mockReset()
  supportFeedback.mockReset()
  unsupportFeedback.mockReset()
  getFeedbackCounts.mockResolvedValue({ all: 0, hot: 0, active: 0, resolved: 0, unread: 0 })
})

describe('冷启动进详情页', () => {
  it('两份列表都空，点「支持」也要落到服务端', async () => {
    getFeedback.mockResolvedValue(detail(7, false))
    supportFeedback.mockResolvedValue({ count: 8, supported: true })

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    // 这就是深链的样子：列表一份都没有，页面靠 detail 撑着。
    expect(store.items).toEqual([])
    expect(store.adminItems).toEqual([])

    await store.toggleSupport('fb-1')

    expect(supportFeedback).toHaveBeenCalledWith('fb-1')
    expect(store.detail?.supports).toBe(8)
    expect(store.detail?.supported).toBe(true)
  })

  it('已经支持过的那条走取消，且用服务端回的计数', async () => {
    getFeedback.mockResolvedValue(detail(8, true))
    // **和本地算出来的差得远**是故意的：8-1 也是 7，用 7 就分辨不出「用了服务端
    // 回的计数」和「本地自己减了个 1」。别人也在点的时候，本地那个数从来没存在过。
    unsupportFeedback.mockResolvedValue({ count: 3, supported: false })

    const store = useFeedbackStore()
    await store.loadDetail('fb-1')
    await store.toggleSupport('fb-1')

    expect(unsupportFeedback).toHaveBeenCalledWith('fb-1')
    expect(supportFeedback).not.toHaveBeenCalled()
    expect(store.detail?.supports).toBe(3)
    // 支持数一变，「热门」那栏的门槛可能就被跨过了 —— 计数也得重新问服务端，
    // 不然列表里 6 条、Tab 上还写着 5。
    expect(getFeedbackCounts).toHaveBeenCalled()
  })
})
