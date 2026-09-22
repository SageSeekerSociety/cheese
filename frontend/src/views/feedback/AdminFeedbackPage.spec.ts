/**
 * `/admin/feedback` 这个**旧地址**还进得来，而且进来就是队列。
 *
 * 权限门**已经搬到外壳** `views/admin/AdminLayout.vue`：谁能进这块后台、以及 meta
 * 还在飞的那一帧画什么，现在都归它管，那三条用例跟着搬到了
 * `views/admin/AdminLayout.spec.ts`（原是这里的「要等 meta 回来才决定拉不拉列表」
 * 与「meta 还在飞的时候不画假话」）。**门在壳上，页上只有内容** —— 两处都判定一次
 * 的代价是两处会漂开，而漂开的表现是「同一个链接，一个人看到表格，另一个人看到
 * 一句拒绝」，两边各自都觉得自己对。
 *
 * 这一页本身现在是六行的薄壳（`AdminQueuePage` 是队列的真身），所以这组用例值钱的地方
 * 换了：从「页面进来会拉数据」变成「**老链接不会死**」。老书签、老通知、别人贴在聊天里
 * 的链接都还指着这个地址，而它一旦变成 404 或空页，看到的人会以为是「后台坏了」。
 */
import type { Component } from 'vue'

import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

const listAdminFeedback = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listAdminFeedback: (...a: unknown[]) => listAdminFeedback(...a),
  }
})

import AdminFeedbackPage from './AdminFeedbackPage.vue'

const ROW = {
  id: 'fb-3',
  display_id: 'FB-1040',
  title: '导出报表偶发 502',
  author_handle: 'maxiaoyu',
  author_is_agent: false,
  visibility: 'private',
  kind: 'bug',
  priority: 'normal',
  status: 'accepted',
  created_at: '2026-09-19T10:00:00Z',
}

function mountPage() {
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
  })
  const vuetify = createVuetify({ components, directives })
  // 页面里有个 `v-navigation-drawer`，它要拿到 `v-app` provide 的 layout；裸挂页面会
  // 在 setup 里直接抛 `Could not find injected layout`。
  const Wrapper = {
    components: { AdminFeedbackPage },
    template: '<v-app><AdminFeedbackPage /></v-app>',
  }
  return render(Wrapper as unknown as Component, {
    // i18n 是页面这一层的依赖（薄壳里的队列用 `t()` 取词），真词表比一份假 `t` 更接近
    // 线上：英文漏词、键名写错这类事，它会在这里就露出来。
    global: { plugins: [vuetify, createPinia(), router, i18n] },
  })
}

beforeAll(() => {
  // 页面标题取的是中文词条，而 happy-dom 的 `navigator.language` 是 `en-US`。
  setLocale('zh-CN')
})

beforeEach(() => {
  listAdminFeedback.mockReset()
})

describe('打开管理端反馈页（旧地址）', () => {
  it('旧地址进来就是队列：拉了列表，行也画出来了', async () => {
    listAdminFeedback.mockResolvedValue({ data: [ROW], total: 1, counts: {} })

    const { findByText } = mountPage()

    expect(await findByText('反馈队列')).toBeTruthy()
    expect(await findByText('导出报表偶发 502')).toBeTruthy()
    expect(listAdminFeedback).toHaveBeenCalled()
  })
})
