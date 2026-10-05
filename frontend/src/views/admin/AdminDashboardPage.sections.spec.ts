/**
 * 看板页的**每一屏**（`AdminDashboardPage.spec.ts` 管的是接线：打哪条路、切窗口、
 * 轮询、下钻）。这一份管的是「切过去以后那一屏上有什么」—— 七类里此前只有性能那一
 * 屏被钉住，其余五屏（交付 / 产品 / 集成 / 用量 / 平台 / 反馈）的每一块都只在真机上
 * 被人眼看过。
 *
 * 它存在的直接原因是一次拆分：这一页 2880 行，要按 #2130 / #2158 / #2168 那三片
 * 拆成「取数在 composable、画在各屏自己的组件里」。拆之前先把它画出来的东西钉住
 * —— 拆完这一份一个字都不用改，全绿才是「行为没变」。
 *
 * 钉的是**每屏的第一层读数**：KPI 行每一张卡上的数、每一块图表的段数、每一个块标题
 * 底下那几行原话。不钉像素、不钉 DOM 结构：拆分会动 DOM 的层级，但屏幕上的数不会。
 *
 * 假数据接在 `window.fetch` 上（和 `AdminDashboardPage.spec.ts` 同一套
 * `installPreviewFetch`），于是「api → store → 页 → 图表组件」整条链子都真跑。
 */
import type { Component } from 'vue'

import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import AdminDashboardPage from './AdminDashboardPage.vue'

import i18n, { setLocale } from '@/i18n'
import { installPreviewFetch } from '@/proto-preview-transport'
import { useFeedbackStore } from '@/stores/feedback'

let preview: typeof window.fetch

beforeAll(() => {
  installPreviewFetch()
  preview = window.fetch
  setLocale('zh-CN')
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

afterAll(() => {
  window.fetch = preview
})

const Wrapper = { components: { AdminDashboardPage }, template: '<v-app><AdminDashboardPage /></v-app>' }

/** 上列表里那一类的屏，等它到货，把容器交回来。 */
async function openTab(label: string) {
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [
      { path: '/admin/dashboard', component: Wrapper },
      // 这一页上的每一个出口都得摆出来：路由表里没有它们时 `router-link` 挂载即抛。
      { path: '/admin/queue', name: 'AdminQueue', component: { template: '<div />' } },
      { path: '/feedback/:id', name: 'FeedbackDetail', component: { template: '<div />' } },
      { path: '/projects/:projectId', name: 'ProjectFrame', component: { template: '<div />' } },
      { path: '/topics/:id', name: 'Topic', component: { template: '<div />' } },
      { path: '/admin/spaces', name: 'AdminSpaces', component: { template: '<div />' } },
      { path: '/admin', name: 'AdminHome', component: { template: '<div />' } },
    ],
  })
  await router.push('/admin/dashboard')
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  const pinia = createPinia()
  setActivePinia(pinia)
  const view = render(Wrapper as unknown as Component, { global: { plugins: [vuetify, pinia, router, i18n] } })
  const store = useFeedbackStore()
  // 默认落点是交付：先等它，再从列表里点过去。
  await waitFor(() => expect(store.stats.pipeline).not.toBeNull())
  const kinds = view.container.querySelector('.ad__kinds')!
  const button = Array.from(kinds.querySelectorAll('button')).find((b) => b.textContent?.includes(label))!
  await fireEvent.click(button)
  await waitFor(() => expect(store.stats[store.statsKind]).not.toBeNull())
  return view
}

/** KPI 行上每一张卡的**值**，从左到右。 */
function kpiValues(container: Element): string[] {
  return Array.from(container.querySelectorAll('.ad__kpis .akpi__value')).map((el) => el.textContent?.trim() ?? '')
}

/** KPI 行上每一张卡的**标题**，从左到右。 */
function kpiLabels(container: Element): string[] {
  return Array.from(container.querySelectorAll('.ad__kpis .akpi__label')).map((el) => el.textContent?.trim() ?? '')
}

/** 一个块标题底下那几行「算不出来」的原话。 */
function unavailableTexts(container: Element): string[] {
  return Array.from(container.querySelectorAll('.ad__split .ad__none-desc')).map((el) => el.textContent?.trim() ?? '')
}

describe('看板页 · 交付管线那一屏', () => {
  it('五张 KPI 是存量的那五个数，四站导轨与三列各自到位', async () => {
    const { container } = await openTab('交付')

    expect(kpiLabels(container)).toEqual(['还在走', '卡住了', '递卡到决议', '等你处理', '递卡受阻'])
    // 5 / 2 / p50 四小时 / 4+3+1 / 5 —— 全部来自 fixtures 里的存量。
    expect(kpiValues(container)).toEqual(['5', '2', '4 小时', '8', '5'])

    // 活四站导轨：一屏一条，四站都在（闸门那一站已退役，不该出现）。
    const spine = container.querySelector('.als')!
    expect(spine.textContent).toContain('建卡')
    expect(spine.textContent).toContain('决议')
    expect(spine.textContent).toContain('合并')
    expect(spine.textContent).toContain('归档')

    // 三张动作清单（等你处理 / 卡住了 / 机器连败）+ 一张失败码表。
    expect(container.querySelectorAll('.aal')).toHaveLength(3)
    expect(container.querySelectorAll('.abt')).toHaveLength(1)
    // 卡住的卡挂着它的改动标题、机器那一行挂着设备名。
    expect(container.textContent).toContain('docs: rewrite the handover doc')
    expect(container.textContent).toContain('dev-3')
  })
})

describe('看板页 · 产品健康那一屏', () => {
  it('四张 KPI、两条分布、两条「今天算不出来」', async () => {
    const { container } = await openTab('产品')

    expect(kpiLabels(container)).toEqual(['7 日验收通过的成果', '递卡走到了哪', '主动消息有用吗', '没用'])
    // 38 / 30% / 80% / 2 —— 北极星合计、退回率、有用率、提案被否。
    expect(kpiValues(container)).toEqual(['38', '30%', '80%', '2'])

    // 两条分布：退回的六个桶（值 >0 的那些）与有用/没用的四档。
    expect(container.querySelectorAll('.ash')).toHaveLength(2)

    // 「算不出来」的两条带理由，不画一个假 0。
    expect(unavailableTexts(container)).toHaveLength(2)
  })
})

describe('看板页 · 集成健康那一屏', () => {
  it('四张 KPI、两条分布、一条计量、四条「算不出来」', async () => {
    const { container } = await openTab('集成')

    expect(kpiValues(container)).toEqual(['3', '5', '30%', '1'])
    expect(container.querySelectorAll('.ash')).toHaveLength(2)
    expect(container.querySelectorAll('.amb')).toHaveLength(1)
    // OAuth 那一张的总数写在块底下的数据行上。
    expect(container.textContent).toContain('40')
    expect(unavailableTexts(container)).toHaveLength(4)
  })
})

describe('看板页 · 用量那一屏', () => {
  it('四张 KPI、额度燃尽的三个名单与燃烧速率、两张拆分表', async () => {
    const { container } = await openTab('用量')

    expect(kpiLabels(container)).toEqual(['窗口内 token', '窗口内调用', '成本', '未定价 token'])

    // 额度燃尽：已耗尽一条（容器 -10）、快烧完一条（城西社区 10），各自带着它的提示。
    const meters = Array.from(container.querySelectorAll('.amb'))
    expect(meters).toHaveLength(2)
    expect(meters[0]!.textContent).toContain('容器')
    expect(meters[0]!.textContent).toContain('已耗尽')
    expect(meters[1]!.textContent).toContain('城西社区')
    expect(meters[1]!.textContent).toContain('快烧完')

    // 数据行（不限量 / 燃烧速率 / 预计用尽）原位保留。
    expect(container.textContent).toContain('不限量 1')
    expect(container.textContent).toContain('6.0/d')

    // 两个正交的拆分各一张表。
    expect(container.querySelectorAll('.abt')).toHaveLength(2)
    // 最花 token 的项目是一张排行。
    expect(container.querySelectorAll('.abr')).toHaveLength(1)
    expect(container.textContent).toContain('知是平台后端')
  })
})

describe('看板页 · 平台那一屏', () => {
  it('五张 KPI、机器四行、健康三格、配额与缺口那几块', async () => {
    const { container } = await openTab('平台')

    expect(kpiLabels(container).slice(0, 3)).toEqual(['账号总数', '真人', 'Agent'])
    expect(kpiValues(container).slice(0, 3)).toEqual(['1,199', '1,187', '12'])
    // 平台管理员：根名单 2 + 页面上加的 1。
    expect(kpiValues(container)[4]).toBe('3')

    // 机器四行是**存量**，名字在左、数在右。
    const machineValues = Array.from(container.querySelectorAll('.ad__machine-list dd')).map(
      (el) => el.textContent?.trim() ?? ''
    )
    expect(machineValues).toEqual(['7', '5', '3', '2'])

    // 健康度三格：这一刻的，每一格带自己的状态点。
    const health = Array.from(container.querySelectorAll('.ad__health-cell'))
    expect(health).toHaveLength(3)
    expect(health.every((cell) => cell.querySelector('.ad__health-dot--ok'))).toBe(true)

    // 配额与缺口：磁盘、宿主机池的沙箱槽位、预览三条计量，加两张状态分布。
    expect(container.querySelectorAll('.amb')).toHaveLength(3)
    expect(container.textContent).toContain('沙箱槽位')
    expect(container.textContent).toContain('3 / 8')
    expect(container.textContent).toContain('51.5%')
    expect(container.textContent).toContain('274.9 GB')
    expect(container.querySelectorAll('.ash')).toHaveLength(2)
  })
})

describe('看板页 · 反馈那一屏', () => {
  it('四栏计数、状态分布、迷你列表与三条线', async () => {
    const { container } = await openTab('反馈')

    // 四栏计数：每一格一个标题一个数。
    const cells = Array.from(container.querySelectorAll('.ad__split-cell'))
    expect(cells).toHaveLength(4)
    expect(cells.map((c) => c.querySelector('.ad__split-label')?.textContent?.trim())).toEqual([
      '公开反馈',
      '私密反馈',
      'Agent 发现',
      '安全问题',
    ])
    // 每一格都**有数**（不是空串）：四栏计数的口径一直都在回。
    expect(cells.every((c) => (c.querySelector('.ad__split-value')?.textContent?.trim() ?? '') !== '')).toBe(true)

    // 状态分布一条、迷你列表一条、逐日三条线一条。
    expect(container.querySelectorAll('.abr')).toHaveLength(1)
    expect(container.querySelectorAll('.anl')).toHaveLength(1)
    expect(container.querySelectorAll('.alc')).toHaveLength(1)

    // 压着没人管的急件：fixtures 里真有，警示行必须画出来。
    expect(container.querySelector('.ad__urgent')).toBeTruthy()
  })
})
