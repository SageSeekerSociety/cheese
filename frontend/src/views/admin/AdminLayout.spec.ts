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
import { render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getFeedbackMeta = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getFeedbackMeta: (...a: unknown[]) => getFeedbackMeta(...a),
  }
})

import AdminLayout from './AdminLayout.vue'

/** 子页用替身：这一组问的是壳画不画它，不是它自己长什么样。 */
const FeedbackChild = { template: '<div>反馈管理的表</div>' }
const MembersChild = { template: '<div>成员管理的名单</div>' }

async function mountAt(path: string) {
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [
      {
        path: '/admin',
        component: AdminLayout,
        children: [
          { path: 'feedback', component: FeedbackChild },
          { path: 'members', component: MembersChild },
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
})

/** meta 慢一拍回来：`setTimeout(20)` 的那一拍里，没 await 的实现在页面上已经画过一轮了。 */
function slowMeta(payload: object) {
  getFeedbackMeta.mockImplementation(() => new Promise((resolve) => setTimeout(() => resolve(payload), 20)))
}

describe('管理后台外壳', () => {
  it('meta 还在飞的时候，画「正在确认权限」，不画子页也不说假话', async () => {
    slowMeta({ is_admin: true, hot_supports: 5 })

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
    getFeedbackMeta.mockResolvedValue({ is_admin: false, hot_supports: 5 })

    const { findByText, queryByText } = await mountAt('/admin/feedback')

    expect(await findByText('这一页是管理员后台')).toBeTruthy()
    expect(queryByText('反馈管理的表')).toBeNull()
  })

  it('是管理员就画分区，切分区换的是右边那一块', async () => {
    getFeedbackMeta.mockResolvedValue({ is_admin: true, hot_supports: 5 })

    const { findByText, queryByText, router } = await mountAt('/admin/feedback')

    expect(await findByText('反馈管理的表')).toBeTruthy()
    // 左边两项都在，用户侧那几条路由不在（列表是写死的，不从路由表算）。
    expect(queryByText('反馈管理')).toBeTruthy()
    expect(queryByText('成员管理')).toBeTruthy()
    expect(queryByText('反馈中心')).toBeNull()

    await router.push('/admin/members')

    expect(await findByText('成员管理的名单')).toBeTruthy()
    expect(queryByText('反馈管理的表')).toBeNull()
  })
})
