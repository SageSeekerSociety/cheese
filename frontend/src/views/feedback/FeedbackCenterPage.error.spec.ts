/**
 * 列表里有东西的时候，失败也要说话。
 *
 * 「支持」失败的两种情形都真实存在：办完了的条目回 412、别人删掉的条目回 404。
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

import FeedbackRoutes from '@/router/feedback'
import { useFeedbackStore } from '@/stores/feedback'

// 只写这条用例真正要用的字段，其余靠 `as unknown as` 补 —— 但 `author_handle` 得给：
// 卡片会把它交给头像组件，缺了 Vue 会在控制台喊一句 prop 类型不对的警告，而这条用例
// 盯的正是「错误画不出来」，最不该被这种噪声淹掉。
const CARD = {
  id: 'fb-1',
  title: '导出报表偶发 502',
  supports: 3,
  comments: 0,
  author_handle: 'alice',
} as unknown as FeedbackCard

const ERR = '这条反馈已经解决了，不能再支持'

/** 组件和测试必须共用这一个 pinia：各拿各的实例时，这边写进去的错误组件根本看不见，
 *  测试就永远绿。 */
let pinia: ReturnType<typeof createPinia>

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  listFeedback.mockReset()
  getFeedbackMeta.mockReset()
  getFeedbackMeta.mockResolvedValue({ is_admin: false, hot_min_items: 5 })
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
      // 用应用真的那张表：卡片里那条去详情的链接是按**路由名**解析的，名字不在表里
      // Vue Router 会在渲染那一刻抛「No match for ...」——这条用例并不点它，但卡片
      // 一画出来就会踩到。
      routes: [...FeedbackRoutes, { path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
    })
    // 套一层 `v-app`：Vuetify 的主题变量与排版挂在它渲染出来的 `.v-application` 上，
    // 少了这一层，组件还在、主题却不在（底色、字号都不对）。
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

    // 钉的是页面里**这一条**的位置，不是「树上某处有这句话」：这条用例要验的正是
    //  「失败画在列表这一页上」，而不是被谁吸收了。
    await waitFor(() => {
      expect(baseElement.querySelector('.fb-page__inner > .v-alert')?.textContent).toContain(ERR)
    })
  })
})
