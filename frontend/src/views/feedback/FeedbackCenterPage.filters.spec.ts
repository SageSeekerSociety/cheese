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
    counts: { all: 1, hot: 0, resolved: 0, unread: 0 },
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
    const { baseElement, findByText, getByRole } = await mount()
    vi.useRealTimers()

    await findByText('导出报表偶发 502')

    // 按可访问名称找控件，同时验证外置标签确实关联了作者输入框。
    const field = getByRole('textbox', { name: /^(作者|Author)$/ })

    vi.useFakeTimers()
    // 先填上，坐实 store 真的收到了这个筛选（否则下面「清掉之后回到 null」可能
    // 只是因为压根没生效过）。
    await fireEvent.update(field as Element, 'alice')
    vi.advanceTimersByTime(400)
    vi.useRealTimers()
    await waitFor(() => expect(useFeedbackStore().filterAuthor).toBe('alice'))

    // 清掉那一下正是以前炸的地方：清除按钮以前走 `model.value = null`（不是 `''`），
    // 而 watch 里那句 `value.trim()` 就在那一刻抛。现在这颗叉是手写的，判据是「空串」——
    // 这一条继续钉住「清空之后不炸，并且真的回到不限作者」。
    const clear = baseElement.querySelector('.fb-author__x') as HTMLElement | null
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
      counts: { all: 1, hot: 0, resolved: 0, unread: 0 },
    })
    useFeedbackStore().setFilter({ kind: 'other' })

    await waitFor(() => expect(baseElement.querySelector('.bes__title')?.textContent).toBe('No feedback matches'))
    // 空块里那颗按钮说的也是筛选 —— 否则「清除筛选」只挂在工具栏上。
    await waitFor(() => expect(baseElement.querySelector('.bes__btn')?.textContent?.trim()).toBe('Clear filters'))
  })

  it('什么筛都没加、列表真的空时，仍然说「暂无反馈」', async () => {
    listFeedback.mockResolvedValue({
      data: [],
      total: 0,
      counts: { all: 0, hot: 0, resolved: 0, unread: 0 },
    })
    const { baseElement } = await mount()

    // 挂载时用的 i18n 是**测试环境默认那一档**（英文），所以这里比的是英文文案 ——
    // 两个语言里这一对都得是**两句不同的话**，而这条与上一条合起来钉的正是「选对了
    // 哪一句」。
    await waitFor(() => expect(baseElement.querySelector('.bes__title')?.textContent).toBe('No feedback yet'))
  })
})

describe('状态药丸跟着栏位走', () => {
  it('已完成栏里不再给出「已收录」「处理中」这两颗', async () => {
    const { findByText, findByRole, queryByRole } = await mount()
    await findByText('导出报表偶发 502')

    await fireEvent.click(await findByRole('tab', { name: /Completed/ }))

    // 这一栏只装三种结局；前两颗选中必然空，收起来。
    await waitFor(() => expect(queryByRole('radio', { name: 'Received' })).toBeNull())
    expect(queryByRole('radio', { name: 'In progress' })).toBeNull()
    // 三种结局还在 —— 收起来的是「不可能出现」，不是「这一排不要了」。
    expect(queryByRole('radio', { name: 'Resolved' })).not.toBeNull()
    expect(queryByRole('radio', { name: 'Deployed' })).not.toBeNull()
    expect(queryByRole('radio', { name: "Won't fix" })).not.toBeNull()
  })

  it('从「全部」带着「处理中」切到「已完成」，筛选自动回到不限', async () => {
    const { findByText, findByRole } = await mount()
    await findByText('导出报表偶发 502')

    await fireEvent.click(await findByRole('radio', { name: 'In progress' }))
    await waitFor(() => expect(useFeedbackStore().filterStatus).toBe('in_progress'))

    await fireEvent.click(await findByRole('tab', { name: /Completed/ }))

    // 不清的话会先拉回一个必然空的页，而那颗药丸已经不在选项里 —— 屏幕上是
    // 一个空列表，没有任何东西解释为什么。
    await waitFor(() => expect(useFeedbackStore().filterStatus).toBeNull())
  })

  it('点进一个空栏位时说「这一栏还没有反馈」，不说「没有符合条件」', async () => {
    const { baseElement, findByText, findByRole } = await mount()
    await findByText('导出报表偶发 502')

    // 切到「已完成」时服务端回空：这一栏自己没有东西，而人一个筛选都没加。
    listFeedback.mockResolvedValue({
      data: [],
      total: 0,
      counts: { all: 1, hot: 0, resolved: 0, unread: 0 },
    })
    await fireEvent.click(await findByRole('tab', { name: /Completed/ }))

    // 一个筛选都没加，所以不能说「没有符合条件的反馈」—— 那是替人编了一句他没做过
    // 的筛选。按钮说的是「看全部」，也确实带回全部。
    await waitFor(() => expect(baseElement.querySelector('.bes__title')?.textContent).toBe('Nothing in this tab yet'))
    await waitFor(() => expect(baseElement.querySelector('.bes__btn')?.textContent?.trim()).toBe('See all'))
  })
})
