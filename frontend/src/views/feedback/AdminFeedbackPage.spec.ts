/**
 * `/admin/feedback` 这一页进来自动拉列表。
 *
 * 权限门**已经搬到外壳** `views/admin/AdminLayout.vue`：谁能进这块后台、以及 meta
 * 还在飞的那一帧画什么，现在都归它管，那三条用例跟着搬到了
 * `views/admin/AdminLayout.spec.ts`（原是这里的「要等 meta 回来才决定拉不拉列表」
 * 与「meta 还在飞的时候不画假话」）。**门在壳上，页上只有内容** —— 两处都判定一次
 * 的代价是两处会漂开，而漂开的表现是「同一个链接，一个人看到表格，另一个人看到
 * 一句拒绝」，两边各自都觉得自己对。
 *
 * 这里留着的是页面自己的那一条：进来的第一件事就是拉数据。少掉它，人打开后台看到的
 * 是一张空表，而一张空表和「后台里没东西」长得一模一样。
 */
import type { Component } from 'vue'

import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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
    global: { plugins: [vuetify, createPinia(), router] },
  })
}

beforeEach(() => {
  listAdminFeedback.mockReset()
})

describe('打开管理端反馈页', () => {
  it('进来就拉列表，不用等任何人先说什么', async () => {
    listAdminFeedback.mockResolvedValue({ data: [ROW], total: 1, counts: {} })

    const { findByText } = mountPage()

    expect(await findByText('导出报表偶发 502')).toBeTruthy()
    expect(listAdminFeedback).toHaveBeenCalled()
  })
})
