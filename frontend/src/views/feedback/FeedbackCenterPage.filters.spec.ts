/**
 * 作者筛选框被清掉时不能炸。
 *
 * 搜索框旁边那个「作者」是 `clearable` 的，而 Vuetify 的清除按钮把 v-model 置成
 * **null**（不是空串）。所以 watch 里那句 `value.trim()` 会在用户点 × 的那一刻抛
 * —— 表现是筛选清不掉，而控制台之外什么都看不见。
 *
 * 这一条钉的就是那一刻：清空之后筛选回到「不限作者」，并且没有任何异常。
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

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getFeedbackMeta: (...a: unknown[]) => getFeedbackMeta(...a),
    listFeedback: (...a: unknown[]) => listFeedback(...a),
  }
})

import FeedbackCenterPage from './FeedbackCenterPage.vue'

import FeedbackRoutes from '@/router/feedback'
import { resetFeedbackCaches, useFeedbackStore } from '@/stores/feedback'

const CARD = {
  id: 'fb-1',
  title: '导出报表偶发 502',
  supports: 3,
  comments: 0,
  author_handle: 'alice',
} as unknown as FeedbackCard

let pinia: ReturnType<typeof createPinia>

beforeEach(() => {
  vi.useRealTimers()
  pinia = createPinia()
  setActivePinia(pinia)
  // 列表缓存是**模块级**的，跨用例活着（见 feedback.cache.spec.ts 的文件头）。不清的
  // 话，后一个用例挂载时命中前一个用例留下的那一页，`items` 一开始就不是空的 ——
  // 于是「空列表说什么」这类用例量到的是上一个用例的世界。
  resetFeedbackCaches()
  listFeedback.mockReset()
  getFeedbackMeta.mockReset()
  getFeedbackMeta.mockResolvedValue({ is_admin: false, hot_min_items: 5 })
  listFeedback.mockResolvedValue({
    data: [CARD],
    total: 1,
    counts: { all: 1, hot: 0, active: 0, resolved: 0, unread: 0 },
  })
})

async function mount() {
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

describe('反馈中心的筛选', () => {
  it('清掉作者筛选之后不炸，筛选真的回到不限作者', async () => {
    vi.useFakeTimers()
    const { baseElement, findByText } = await mount()
    vi.useRealTimers()

    await findByText('导出报表偶发 502')

    // 「作者」按 aria-label 取 —— 两个 v-select 也各自渲染一个 `input[type="text"]`
    // （Vuetify 的可搜索输入框），按标签才是稳的。
    const field = baseElement.querySelector('.fb-filters input[aria-label="作者"]')
    expect(field, '筛选行里应当有一个作者输入框').toBeTruthy()

    vi.useFakeTimers()
    // 先填上，坐实 store 真的收到了这个筛选（否则下面「清掉之后回到 null」可能
    // 只是因为压根没生效过）。
    await fireEvent.update(field as Element, 'alice')
    vi.advanceTimersByTime(400)
    vi.useRealTimers()
    await waitFor(() => expect(useFeedbackStore().filterAuthor).toBe('alice'))

    // Vuetify 的清除按钮走的是 `model.value = null`，不是 `''` —— 这一下正是以前炸的地方。
    // 它不是一颗 `<button>`，而是 `.v-field__clearable` 里那颗带 `role="button"` 的图标。
    const clear = baseElement.querySelector('.fb-filters .v-field__clearable [role="button"]') as HTMLElement | null
    expect(clear, '作者框是 clearable 的，应当有一颗清除按钮').toBeTruthy()

    vi.useFakeTimers()
    await fireEvent.click(clear as Element)
    vi.advanceTimersByTime(400)
    vi.useRealTimers()

    await waitFor(() => expect(useFeedbackStore().filterAuthor).toBe(''))
  })

  /** 筛空之后**说的那句话**必须对得上为什么空。
   *
   * 这一页有三种「没有」：一条都没有 / 搜索没结果 / 筛选之后没有。判据以前只算了
   * 搜索词和栏位，把这四个筛选漏在外面 —— 于是被「类型」筛空的人看到的是「暂无反馈。
   * 你提交的反馈会出现在这里。」，读起来就是**平台一条反馈都没有**，而空块里那颗
   * 清除筛选的按钮也一起不画（工具栏那颗还在，所以还不至于走不出去）。
   */
  it('被筛选筛空时说「没有符合条件的反馈」，不说「暂无反馈」', async () => {
    const { baseElement, findByText } = await mount()
    await findByText('导出报表偶发 502')

    // 服务端按筛选回空：筛选是真的生效了，不是平台没有反馈。
    listFeedback.mockResolvedValue({
      data: [],
      total: 0,
      counts: { all: 1, hot: 0, active: 0, resolved: 0, unread: 0 },
    })
    useFeedbackStore().setFilter({ kind: 'other' })

    await waitFor(() => expect(baseElement.querySelector('.fb-empty__title')?.textContent).toBe('No feedback matches'))
    // 空块里那颗按钮说的也是筛选 —— 否则「清除筛选」只挂在工具栏上。
    await waitFor(() =>
      expect(baseElement.querySelector('.fb-empty__action')?.textContent?.trim()).toBe('Clear filters')
    )
  })

  it('什么筛都没加、列表真的空时，仍然说「暂无反馈」', async () => {
    listFeedback.mockResolvedValue({
      data: [],
      total: 0,
      counts: { all: 0, hot: 0, active: 0, resolved: 0, unread: 0 },
    })
    const { baseElement } = await mount()

    // 挂载时用的 i18n 是**测试环境默认那一档**（英文），所以这里比的是英文文案 ——
    // 两个语言里这一对都得是**两句不同的话**，而这条与上一条合起来钉的正是「选对了
    // 哪一句」。
    await waitFor(() => expect(baseElement.querySelector('.fb-empty__title')?.textContent).toBe('No feedback yet'))
  })
})
