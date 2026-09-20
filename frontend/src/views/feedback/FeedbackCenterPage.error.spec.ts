/**
 * 列表里有东西的时候，失败也要说话。
 *
 * 「支持」失败的两种情形都真实存在：已解决的条目回 412、别人删掉的条目回 404。
 * 错误以前只画在「一条也没有」那块空态里，于是**列表非空时它没有地方可去**：
 * 按钮按得下去、有按下效果、数字一动不动、控制台一句话也没有 —— 和「这个按钮
 * 坏了」长得一模一样。
 */
import type { FeedbackCard } from '@/cx_types'

import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
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

import { useFeedbackStore } from '@/stores/feedback'

const CARD = { id: 'fb-1', title: '导出报表偶发 502', supports: 3, comments: 0 } as unknown as FeedbackCard

const ERR = '这条反馈已经解决了，不能再支持'

/** 组件和测试必须共用这一个 pinia：各拿各的实例时，这边写进去的错误组件根本看不见，
 *  测试就永远绿。 */
let pinia: ReturnType<typeof createPinia>

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  listFeedback.mockReset()
  getFeedbackMeta.mockReset()
  getFeedbackMeta.mockResolvedValue({ is_admin: false, hot_supports: 5 })
  listFeedback.mockResolvedValue({
    data: [CARD],
    total: 1,
    counts: { all: 1, hot: 0, active: 0, resolved: 0, unread: 0 },
  })
})

describe('反馈中心的错误', () => {
  it('列表非空时，服务端那句话也画得出来', async () => {
    const vuetify = createVuetify({ components, directives })
    const router = createRouter({
      history: createWebHashHistory(),
      routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
    })
    // 提交抽屉是个 `v-navigation-drawer`，它要 `v-app` provide 的 layout。
    const Wrapper = {
      components: { FeedbackCenterPage },
      template: '<v-app><FeedbackCenterPage /></v-app>',
    }
    const { findByText, baseElement } = render(Wrapper, {
      global: { plugins: [vuetify, router, pinia] },
    })

    // 先把「列表里有东西」坐实，否则下面钉到的可能是那块空态。
    expect(await findByText('导出报表偶发 502')).toBeTruthy()

    // 这一条是「点了支持、服务端拒了」之后 store 里的样子。
    useFeedbackStore().error = ERR

    // 这里不用 `findByText(ERR)`：关着的提交抽屉也在 DOM 里，它画的是同一个
    // `store.error`，于是会命中两个元素、报 "Found multiple elements"（findByText
    // 会一路重试到超时）。钉的是页面里**这一条**的位置 —— 那才是这次修的东西。
    await waitFor(() => {
      expect(baseElement.querySelector('.fb-page__inner > .v-alert')?.textContent).toContain(ERR)
    })
  })
})
