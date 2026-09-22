/**
 * 看板页（`/admin/dashboard`，§4.2）。
 *
 * **这一组存在的理由是一次真事故**：看板前端曾经指着一条已经没有的路由
 * （`/admin/feedback/stats` —— 服务端把它拆成了 `/admin/stats/{feedback,usage,platform}`）。
 * 那条路径落进 `/admin/feedback/{id}` 被当成一个 uuid 解析，真环境回 **400**，页面上是
 * 「看板加载失败」；而预览的假后端**替那条老路留了一个别名**，于是预览一切正常、
 * 没有一条测试碰过这一页 —— CI 全绿，dev 是坏的。
 *
 * 所以这里钉的第一件事就是**路径本身**：挂载时打的是哪一条。第二件事是分类切换**只拉
 * 切过去的那一类**（服务端就是三条接口，一次全拉就把那两张大表的读白花了）。
 *
 * 假数据接在 `window.fetch` 上（和 `AdminQueuePage.spec.ts` 同一套），于是
 * 「api → store → 页 → 图表组件」整条链子都真跑；手写 store 替身的话，断的恰好是
 * 「我调了我自己」，而这正是这次要守的那一段。
 */
import type { Component } from 'vue'
import type { StatsKind } from '@/api'

import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import AdminDashboardPage from './AdminDashboardPage.vue'

import i18n, { setLocale } from '@/i18n'
import { installPreviewFetch } from '@/proto-feedback-fixtures'
import { useFeedbackStore } from '@/stores/feedback'

/** 三条看板接口。**整段相等**匹配，不用前缀：`/api/admin/stats/feedback` 和
 *  `/api/admin/feedback`（列表）差一个词，前缀匹配会把它们混成一件事。 */
const STATS = ['/api/admin/stats/feedback', '/api/admin/stats/usage', '/api/admin/stats/platform'] as const

/** 假数据那层 fetch。**存成常量**：每次测试又包一层的话，包装器读的是个会变的变量，
 *  第二层就会调到自己，撞成栈溢出。 */
let preview: typeof window.fetch
let hits: string[] = []

beforeAll(() => {
  installPreviewFetch()
  preview = window.fetch
  // 页上的词条都是中文，而 `navigator.language` 在 happy-dom 里是 `en-US`。
  setLocale('zh-CN')
})

beforeEach(() => {
  hits = []
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    const url = new URL(raw, window.location.origin)
    if ((STATS as readonly string[]).includes(url.pathname)) hits.push(url.pathname)
    return preview(input as RequestInfo, init)
  }
})

afterAll(() => {
  window.fetch = preview
})

/** 套一层 `v-app`：图里的 `v-skeleton-loader` 之类的组件要 layout 才挂得上。 */
const Wrapper = { components: { AdminDashboardPage }, template: '<v-app><AdminDashboardPage /></v-app>' }

async function mountDashboard() {
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [
      { path: '/admin/dashboard', component: Wrapper },
      // 这一页上的出口：KPI 卡片去队列、迷你列表的每一行去详情。两边都只用 `name`
      // 定过位，**路由表里没有它们时 `router-link` 会当场抛**（不是「点了没反应」），
      // 于是整页在挂载时就红了 —— 所以这里要把它们摆出来，哪怕内容是个空壳。
      { path: '/admin/queue', name: 'AdminQueue', component: { template: '<div />' } },
      { path: '/feedback/:id', name: 'FeedbackDetail', component: { template: '<div />' } },
    ],
  })
  await router.push('/admin/dashboard')
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  const pinia = createPinia()
  setActivePinia(pinia)
  return render(Wrapper as unknown as Component, { global: { plugins: [vuetify, pinia, router, i18n] } })
}

describe('看板页', () => {
  /** 等**数据到货**，不是等请求发出去 —— 两者差一个来回，而这一组里的断言都是关于
   *  「手上有没有那一份」的。等错了一头，测试会在请求刚发出时就往下走，然后把
   *  「上一份还没到」读成「不会再拉了」。 */
  async function loaded(kind: StatsKind) {
    const store = useFeedbackStore()
    await waitFor(() => expect(store.stats[kind]).not.toBeNull())
    return store
  }

  const tab = (label: string, getAllByRole: (role: string) => HTMLElement[]) =>
    getAllByRole('button').find((b) => b.textContent?.includes(label))!

  it('挂载时打的是 /admin/stats/feedback，不是那条已经没有的老路由', async () => {
    await mountDashboard()
    await loaded('feedback')

    // 一次事故的化石：老路径没有路由之后会落进 `/admin/feedback/{id}` 被当成 uuid，
    // 真环境回 400、页面上是「看板加载失败」。这条断言把「前端指着哪条路」变成测试里
    // 看得见的东西。
    expect(hits).not.toContain('/api/admin/feedback/stats')
    // 另外两类这一帧没有人要看，不该被顺带拉一次。
    expect(hits).toEqual(['/api/admin/stats/feedback'])
  })

  it('切分类只拉切过去的那一类，切回来不重拉', async () => {
    const { findByText, getAllByRole } = await mountDashboard()
    await loaded('feedback')

    await fireEvent.click(tab('用量', getAllByRole))
    await loaded('usage')
    expect(hits).not.toContain('/api/admin/stats/platform')

    await fireEvent.click(tab('平台', getAllByRole))
    await loaded('platform')

    // 切回第一类：那一份已经在手上了，再拉一次只是重复读那两张最长的表。
    const before = hits.length
    await fireEvent.click(tab('反馈', getAllByRole))
    expect(await findByText('每天新增 / 解决 / 上线')).toBeTruthy()
    expect(hits.length).toBe(before)
  })

  it('平台那一类把机器报成存量，并写明它不是在线数', async () => {
    const { findByText, getAllByRole, getByText } = await mountDashboard()
    await loaded('feedback')

    await fireEvent.click(tab('平台', getAllByRole))
    await loaded('platform')

    expect(await findByText('机器（存量）')).toBeTruthy()
    // 「机器 5」这个数读的人默认会当成「现在有 5 台在跑」—— 那句话必须写着。
    expect(
      getByText('这四行是四张台账的存量，不是在线数 —— 在线状态住在进程内存里，库里没有可以查的那一列。')
    ).toBeTruthy()
  })
})
