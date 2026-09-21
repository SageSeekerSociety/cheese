/**
 * 直接打开 `/admin/feedback`（刷新、或者别人发来的链接）必须拉列表。
 *
 * 「我是不是管理员」由服务端答（`GET /feedback/meta` 的 `is_admin`）。上一版
 * `onMounted` 没 `await store.loadMeta()`，于是紧接着那行 `if (store.isAdmin)` 读到的
 * 永远是「还不是管理员」，列表**一次都不拉** —— 页面画出来的是那句「你的账号不在
 * 管理员名单里」，一个管理员看到这句话，比一张空表更难查。
 *
 * 从反馈中心点进来反而正常（那边已经把 meta 拉过了），所以这一组把 meta 的响应**拖慢**
 * 一拍：只有 await 过的实现才过得去。
 */
import type { Component } from 'vue'

import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getFeedbackMeta = vi.fn()
const listAdminFeedback = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getFeedbackMeta: (...a: unknown[]) => getFeedbackMeta(...a),
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
  getFeedbackMeta.mockReset()
  listAdminFeedback.mockReset()
})

describe('冷启动打开管理端', () => {
  it('要等 meta 回来才决定拉不拉列表', async () => {
    // 慢一拍：不 await 的实现在这一拍里已经把 isAdmin 读成 false 了。
    getFeedbackMeta.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve({ is_admin: true, hot_supports: 5 }), 20))
    )
    listAdminFeedback.mockResolvedValue({ data: [ROW], total: 1, counts: {} })

    const { findByText, queryByText } = mountPage()

    expect(await findByText('导出报表偶发 502')).toBeTruthy()
    expect(listAdminFeedback).toHaveBeenCalled()
    expect(queryByText('这一页是管理员后台')).toBeNull()
  })

  it('不是管理员就停在门口，不发那次必然全 403 的请求', async () => {
    getFeedbackMeta.mockResolvedValue({ is_admin: false, hot_supports: 5 })

    const { findByText } = mountPage()

    expect(await findByText('这一页是管理员后台')).toBeTruthy()
    expect(listAdminFeedback).not.toHaveBeenCalled()
  })

  it('meta 还在飞的时候，不画「你不在名单里」这句假话', async () => {
    // await 只解决了「拉不拉列表」，解决不了这一帧画什么：这段时间里 isAdmin 是
    // false，两段式的模板会先给一个真管理员看「你的账号不在管理员名单里」。
    getFeedbackMeta.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve({ is_admin: true, hot_supports: 5 }), 20))
    )
    listAdminFeedback.mockResolvedValue({ data: [ROW], total: 1, counts: {} })

    const { findByText, queryByText } = mountPage()

    expect(await findByText('正在确认权限…')).toBeTruthy()
    expect(queryByText('这一页是管理员后台')).toBeNull()
    // 结论到了之后，画的是表格。
    expect(await findByText('导出报表偶发 502')).toBeTruthy()
  })
})
