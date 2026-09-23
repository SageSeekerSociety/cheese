/**
 * 管理后台的外壳：门开在**这里**，子页只管自己的内容。
 *
 * 这三条原来长在 `views/feedback/AdminFeedbackPage.spec.ts` 上，随门一起搬过来（门
 * 搬到壳上的那一轮）。搬的理由不是「测试跟着代码走」：门只要还留在子页里，加第二块
 * 模块时就得把同一段判断再抄一遍，而抄漏的那一块是**没有门**的 —— 页面照常渲染、
 * 照常发请求，只有服务端 403 挡着，界面上表现为一整页空白。门在壳上时，「子页被
 * 画出来」和「这个人过了门」是同一件事，构造不出「画了但没鉴权」。
 *
 * 三条各自钉住一个状态：
 *
 * 1. meta 还在飞 → 「正在确认权限…」，**子页一次都不画**。这条是真正会被踩的那条：
 *    `isAdmin` 在 meta 到之前是 false，少掉「还在问」这一档，一个真管理员打开页面
 *    看到的第一句话是「你的账号不在管理员名单里」—— 一句假话，而且它自己会变成表格，
 *    所以看起来只是「闪了一下」。
 * 2. 不是管理员 → 一句人话 + 回去的路，且**不发那次必然全 403 的请求**。
 * 3. 是管理员 → 分区画出来、子页画出来、切分区能换页。
 */
import type { Component } from 'vue'

import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getFeedbackMeta = vi.fn()
// 未读徽标挂在导航上，而它的数只有壳自己去要这一种来源（成员页不拉 counts）——
// 所以这一层替身是必要的，不然那颗数字在测试里是发不出来的。
const getFeedbackCounts = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getFeedbackMeta: (...a: unknown[]) => getFeedbackMeta(...a),
    getFeedbackCounts: (...a: unknown[]) => getFeedbackCounts(...a),
  }
})

// 壳上两处文案走 i18n（刷新按钮的名字、未读徽标的读屏名），而这一组问的是「画不画」，
// 不是「画的是哪句中文」，所以只换掉取词入口、键原样返回 —— 仓库里既有的做法（见
// `views/spaces/Index.spec.ts`）。词条本身对不对由 `i18n/catalog.spec.ts` 管。
vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import AdminLayout from './AdminLayout.vue'

/** 子页用替身：这一组问的是壳画不画它，不是它自己长什么样。队列那个带一个输入框，
 *  用来验「光标在输入框里时全局键不算」这条守卫。 */
const FeedbackChild = { template: '<div>反馈管理的表</div>' }
const MembersChild = { template: '<div>成员管理的名单</div>' }
const QueueChild = { template: '<div>队列内容<input /></div>' }
const DashboardChild = { template: '<div>看板内容</div>' }

async function mountAt(path: string) {
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [
      {
        path: '/admin',
        component: AdminLayout,
        children: [
          { path: 'feedback', name: 'AdminFeedback', component: FeedbackChild },
          { path: 'queue', name: 'AdminQueue', component: QueueChild },
          { path: 'dashboard', name: 'AdminDashboard', component: DashboardChild },
          { path: 'members', name: 'AdminMembers', component: MembersChild },
        ],
      },
      { path: '/feedback', component: { template: '<div>反馈中心</div>' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  const Wrapper = { template: '<v-app><RouterView /></v-app>' }
  const utils = render(Wrapper as unknown as Component, {
    global: { plugins: [vuetify, createPinia(), router] },
  })
  return { ...utils, router }
}

beforeEach(() => {
  getFeedbackMeta.mockReset()
  getFeedbackCounts.mockReset()
  getFeedbackCounts.mockResolvedValue({ unread: 12 })
})

// `?` 打开的那张表是一个 VOverlay，而 happy-dom 两样都没有：不补上的话弹窗挂不上来，
// 测到的就成了「按了 `?` 什么也没发生」—— 一条冤枉产品的红。仓库里同样的补法见
// `views/ProjectLibraryView.spec.ts`。
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

/** meta 慢一拍回来：`setTimeout(20)` 的那一拍里，没 await 的实现在页面上已经画过一轮了。 */
function slowMeta(payload: object) {
  getFeedbackMeta.mockImplementation(() => new Promise((resolve) => setTimeout(() => resolve(payload), 20)))
}

describe('管理后台外壳', () => {
  it('meta 还在飞的时候，画「正在确认权限」，不画子页也不说假话', async () => {
    slowMeta({ is_admin: true, hot_min_items: 5 })

    const { findByText, queryByText } = await mountAt('/admin/feedback')

    expect(await findByText('正在确认权限…')).toBeTruthy()
    expect(queryByText('这一页是管理员后台')).toBeNull()
    // 关键的一条：这一帧里子页**不在 DOM 里**。它在的话，那块内容就是一个还没
    // 确认过身份的人画出来的。
    expect(queryByText('反馈管理的表')).toBeNull()

    // 结论到了之后换成子页 —— 门口那句话同时消失，不是叠在表格上面。
    expect(await findByText('反馈管理的表')).toBeTruthy()
    expect(queryByText('正在确认权限…')).toBeNull()
  })

  it('不是管理员就停在门口，不画子页', async () => {
    getFeedbackMeta.mockResolvedValue({ is_admin: false, hot_min_items: 5 })

    const { findByText, queryByText } = await mountAt('/admin/feedback')

    expect(await findByText('这一页是管理员后台')).toBeTruthy()
    expect(queryByText('反馈管理的表')).toBeNull()
  })

  it('是管理员就画分区，切分区换的是右边那一块', async () => {
    getFeedbackMeta.mockResolvedValue({ is_admin: true, hot_min_items: 5 })

    const { findByText, queryByText, router } = await mountAt('/admin/feedback')

    expect(await findByText('反馈管理的表')).toBeTruthy()
    // 三块都在，用户侧那几条路由不在（列表是写死的，不从路由表算）。
    expect(queryByText('队列')).toBeTruthy()
    expect(queryByText('看板')).toBeTruthy()
    expect(queryByText('成员')).toBeTruthy()
    expect(queryByText('反馈中心')).toBeNull()
    // 未读数来自 `counts`，不是从列表长度推的 —— 列表那一页只有 20 条。
    expect(await findByText('12')).toBeTruthy()

    await router.push('/admin/members')

    expect(await findByText('成员管理的名单')).toBeTruthy()
    expect(queryByText('反馈管理的表')).toBeNull()
  })
})

describe('外壳上的全局键', () => {
  beforeEach(() => {
    getFeedbackMeta.mockResolvedValue({ is_admin: true, hot_min_items: 5 })
  })

  it('`?` 打开那张快捷键表', async () => {
    const { findByText, queryByText } = await mountAt('/admin/queue')
    await findByText('队列内容')
    expect(queryByText('键盘快捷键')).toBeNull()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: '?' }))

    // 对话框走传送门，落在 `<body>` 而不是容器里，所以按整页找。
    await waitFor(() => expect(document.body.textContent).toContain('键盘快捷键'))
  })

  it('`G` 之后 `Q` 换到队列，当前那一项带 aria-current', async () => {
    const { findByText, router } = await mountAt('/admin/dashboard')
    await findByText('看板内容')

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'g' }))
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'q' }))

    expect(await findByText('队列内容')).toBeTruthy()
    expect(router.currentRoute.value.name).toBe('AdminQueue')
    // 选中态是画在链接上的 `aria-current="page"`（左那道 2px 条挂在它上面），
    // 不是靠类名 —— 类名只是皮肤，读屏和眼睛看到的是同一处。
    const current = document.body.querySelector('[aria-current="page"]')
    expect(current?.textContent).toContain('队列')
  })

  it('光标在输入框里时，单键一个都不算', async () => {
    const { container, findByText, queryByText } = await mountAt('/admin/queue')
    await findByText('队列内容')

    const input = container.querySelector('input') as HTMLInputElement
    input.dispatchEvent(new KeyboardEvent('keydown', { key: '?', bubbles: true }))
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'g', bubbles: true }))
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'q', bubbles: true }))

    expect(queryByText('键盘快捷键')).toBeNull()
    expect(queryByText('队列内容')).toBeTruthy()
  })
})
