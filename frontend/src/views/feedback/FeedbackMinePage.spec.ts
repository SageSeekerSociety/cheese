/**
 * 「我的反馈」这一页有没有真的接上。
 *
 * 三件事各自钉一条，都是「不钉就会悄悄坏、而且坏得像没坏」的那类：
 *
 *   1. **挂载就拉**。`/feedback/mine` 的接口和 api.ts 的 `listMyFeedback` 上一轮
 *      就在了，只是没有任何页面调它 —— 这一页存在的理由就是那一下调用。
 *   2. **失败不冒充空**。拉挂了画「你还没有提过反馈」是把一次网络故障说成
 *      「平台没有你的数据」，这正是反馈中心那条错误用例修过的同一类 bug。
 *   3. **支持按钮在这一页也认账**。store 的 `_find` / `_patch` 要覆盖 `mineItems`：
 *      漏掉的表现是按钮按得下去、有按下效果、数字一动不动、控制台一句话也没有。
 */
import type { FeedbackCard } from '@/cx_types'

import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const listMyFeedback = vi.fn()
const supportFeedback = vi.fn()
const getFeedbackCounts = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getFeedbackCounts: (...a: unknown[]) => getFeedbackCounts(...a),
    listMyFeedback: (...a: unknown[]) => listMyFeedback(...a),
    supportFeedback: (...a: unknown[]) => supportFeedback(...a),
  }
})

import FeedbackMinePage from './FeedbackMinePage.vue'

import FeedbackRoutes from '@/router/feedback'

// `author_handle` 得给：卡片会把它交给头像组件，缺了 Vue 会在控制台喊一句 prop
// 类型不对的警告。其余字段靠 `as unknown as` 补。
const CARD = {
  id: 'fb-1',
  title: '导出报表偶发 502',
  supports: 3,
  comments: 0,
  author_handle: 'alice',
  status: 'accepted',
} as unknown as FeedbackCard

function mountPage() {
  const router = createRouter({
    history: createWebHashHistory(),
    // 用**应用真的那张表**：卡片里是一条 `<router-link :to="{ name: 'FeedbackDetail' }">`，
    // 名字解析不到时 Vue Router 是在**渲染那一刻**抛「No match for ...」的（不是导航
    // 时抛），而这条用例根本不点那条链接。手抄一条同名路由也能过，但路由改名时它不会
    // 跟着改 —— 抄一份就是给「用例和路由表悄悄分家」留一个后门。
    routes: [...FeedbackRoutes, { path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
  })
  const vuetify = createVuetify({ components, directives })
  // 提交抽屉是个 `v-navigation-drawer`，它要 `v-app` provide 的 layout。
  const Wrapper = {
    components: { FeedbackMinePage },
    template: '<v-app><FeedbackMinePage /></v-app>',
  }
  return render(Wrapper, { global: { plugins: [vuetify, router, createPinia()] } })
}

beforeEach(() => {
  listMyFeedback.mockReset()
  supportFeedback.mockReset()
  getFeedbackCounts.mockReset()
  getFeedbackCounts.mockResolvedValue({ all: 1, hot: 0, active: 0, resolved: 0, unread: 0 })
})

describe('我的反馈', () => {
  it('挂载就拉那一页，并把这一页的 `counts` 之外的列表画出来', async () => {
    listMyFeedback.mockResolvedValue({ data: [CARD], total: 1, counts: {} })

    const { findByText } = mountPage()

    expect(await findByText('导出报表偶发 502')).toBeTruthy()
    // 一页拉完：这一版没有翻页 UI，20 条默认值会让清单看起来「就只有这些」。
    expect(listMyFeedback).toHaveBeenCalledWith({ pageSize: 50 })
    expect(await findByText('共 1 条。')).toBeTruthy()
  })

  it('拉失败画的是那句话，不冒充「你还没有提过反馈」', async () => {
    listMyFeedback.mockRejectedValue(new Error('「我的反馈」加载失败'))

    const { queryByText, baseElement } = mountPage()

    // 不用 `findByText`：关着的提交抽屉也在 DOM 里，它画的正是同一个 `store.error`，
    // 于是会命中两个元素、一路重试到超时。钉页面里**这一处** —— 那才是这次要验的。
    await waitFor(() => {
      expect(baseElement.querySelector('.fb-page__inner .fb-empty')?.textContent).toContain('「我的反馈」加载失败')
    })
    expect(queryByText('你还没有提过反馈，也没有指派给你的')).toBeNull()
  })

  it('没有内容时才说「还没有」，并且就地给一条提交的路', async () => {
    listMyFeedback.mockResolvedValue({ data: [], total: 0, counts: {} })

    const { findByText } = mountPage()

    expect(await findByText('你还没有提过反馈，也没有指派给你的')).toBeTruthy()
    expect(await findByText('提交一条')).toBeTruthy()
  })

  it('在这一页点支持，计数跟着服务端回的数走', async () => {
    listMyFeedback.mockResolvedValue({ data: [CARD], total: 1, counts: {} })
    // 服务端回的是**写完之后**的计数，不是增量：客户端 +1 会在两个同时点的人那里
    // 渲染出一个从来没存在过的数字。
    supportFeedback.mockResolvedValue({ count: 4, supported: true })

    const { findByText, getByRole, baseElement } = mountPage()
    await findByText('导出报表偶发 502')

    getByRole('button', { name: '支持这个反馈' }).click()

    await waitFor(() => {
      expect(supportFeedback).toHaveBeenCalledWith('fb-1')
    })
    await waitFor(() => {
      expect(baseElement.querySelector('.fb-card__count')?.textContent).toBe('4')
    })
  })
})
