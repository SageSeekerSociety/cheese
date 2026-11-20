/**
 * 反馈中心那一页把画面报上来的动作**真的接到了 store 上**。
 *
 * 列表、筛选条和每一行现在都是同目录 `FeedbackCenterPageView.vue` 画的（它只收 props、
 * 只发事件），页面当容器。于是「按下去到底有没有发生什么」这件事从「组件自己读 store」
 * 变成了「容器把事件接对了吗」—— 接错的坏法正好是安静的：按钮画得出来、有按下效果、
 * 屏幕上什么都不动，而没有一行报错。
 *
 * 这里钉三件，都是接错就悄悄坏的那类：
 *   1. 某一行点了支持，请求带上的是**那一条**的 id，计数回到服务端回的数；
 *   2. 拉挂了之后「重试」真的再问一次服务端；
 *   3. 搜索词是**防抖之后**才发请求（每敲一个字发一次是另一类毛病：请求风暴）。
 *
 * 筛选、空态文案、清筛选那几条在 `FeedbackCenterPage.filters.spec.ts` /
 * `.error.spec.ts` 里，这里不重复。
 */
import type { FeedbackCard } from '@/cx_types'

import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const listFeedback = vi.fn()
const getFeedbackMeta = vi.fn()
const supportFeedback = vi.fn()
const getFeedbackCounts = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getFeedbackCounts: (...a: unknown[]) => getFeedbackCounts(...a),
    getFeedbackMeta: (...a: unknown[]) => getFeedbackMeta(...a),
    listFeedback: (...a: unknown[]) => listFeedback(...a),
    supportFeedback: (...a: unknown[]) => supportFeedback(...a),
  }
})

import FeedbackCenterPage from './FeedbackCenterPage.vue'

import { setLocale } from '@/i18n'
import FeedbackRoutes from '@/router/feedback'
import { resetFeedbackCaches, useFeedbackStore } from '@/stores/feedback'

const CARD = {
  id: 'fb-1',
  title: '导出报表偶发 502',
  supports: 3,
  supported: false,
  comments: 0,
  author_handle: 'alice',
  status: 'received',
} as unknown as FeedbackCard

const COUNTS = { all: 1, hot: 0, resolved: 0, unread: 0 }

let pinia: ReturnType<typeof createPinia>

beforeEach(() => {
  vi.useRealTimers()
  setLocale('zh-CN')
  pinia = createPinia()
  setActivePinia(pinia)
  // 列表缓存是模块级的（见 feedback.ts 里 `listCache` 那句）：不清的话，后一个用例会
  // 先画前一个用例留下的那一页。
  resetFeedbackCaches()
  listFeedback.mockReset()
  getFeedbackMeta.mockReset()
  supportFeedback.mockReset()
  getFeedbackCounts.mockReset()
  getFeedbackMeta.mockResolvedValue({ is_admin: false, hot_min_items: 5 })
  getFeedbackCounts.mockResolvedValue(COUNTS)
  listFeedback.mockResolvedValue({ data: [CARD], total: 1, counts: COUNTS })
})

function mount() {
  const vuetify = createVuetify({ components, directives })
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [...FeedbackRoutes, { path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
  })
  const Wrapper = {
    components: { FeedbackCenterPage },
    template: '<v-app><FeedbackCenterPage /></v-app>',
  }
  return render(Wrapper, { global: { plugins: [vuetify, router, pinia] } })
}

describe('反馈中心把画面的动作接到 store 上', () => {
  it('点某一行的支持，请求带的是那一条的 id，计数回到服务端回的数', async () => {
    // 服务端回的是**写完之后**的计数，不是增量：本地 ±1 会在两个人同时点的时候渲染出
    // 一个从来没存在过的数字。
    supportFeedback.mockResolvedValue({ count: 10, supported: true })

    const { findByText, getByRole, baseElement } = mount()
    await findByText('导出报表偶发 502')

    getByRole('button', { name: /支持这个反馈/ }).click()

    // 接错的表现：请求带的是别的 id（或者根本不发）—— 于是另一条反馈的数字动了。
    await waitFor(() => expect(supportFeedback).toHaveBeenCalledWith('fb-1'))
    await waitFor(() => {
      expect(baseElement.querySelector('.fb-card__count')?.textContent).toBe('10')
    })
  })

  it('拉挂了之后「重试」真的再问一次服务端', async () => {
    listFeedback.mockRejectedValueOnce(new Error('网络不通'))

    const { findByText, baseElement } = mount()

    // 失败那一态：空态里那颗按钮说的是「重试」，头一句说的是「反馈加载失败」。
    const retry = await findByText('重试')
    expect(baseElement.querySelector('.fb-page__inner .bes')?.textContent).toContain('反馈加载失败')

    // 下一次服务端是好的。
    listFeedback.mockResolvedValue({ data: [CARD], total: 1, counts: COUNTS })
    const before = listFeedback.mock.calls.length
    await fireEvent.click(retry)

    // 重试真的又发了一次请求，而不是把那颗按钮画在那儿却没有接线。
    await waitFor(() => expect(listFeedback.mock.calls.length).toBeGreaterThan(before))
    expect(await findByText('导出报表偶发 502')).toBeTruthy()
  })

  it('搜索是防抖之后才发请求，不是每敲一个字发一次', async () => {
    const { findByText, baseElement } = mount()
    await findByText('导出报表偶发 502')

    const input = baseElement.querySelector('.fb-search__input') as HTMLInputElement
    expect(input, '筛选条里应当有一个搜索框').toBeTruthy()

    const before = listFeedback.mock.calls.length
    vi.useFakeTimers()
    // 连敲两下：store 里的搜索词当场就变，但请求要等手停下来。
    await fireEvent.update(input, '导')
    await fireEvent.update(input, '导出')
    expect(listFeedback.mock.calls.length).toBe(before)

    // 手停下来之后只发**一次**，带的是最后那句。
    await vi.advanceTimersByTimeAsync(400)
    vi.useRealTimers()

    await waitFor(() => expect(listFeedback.mock.calls.length).toBe(before + 1))
    const last = listFeedback.mock.calls[listFeedback.mock.calls.length - 1]?.[0] as { q?: string }
    expect(last?.q).toBe('导出')
    expect(useFeedbackStore().query).toBe('导出')
  })
})
