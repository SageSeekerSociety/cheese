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
 * 重设计之后新钉的四件事（每一件都是一种能悄悄上线的坏法）：
 *
 * * **窗口切换是真重拉**（7/30/90）：已加载的窗口类带着新的 `days` 重打接口，未加载
 *   的类一趟都不多发；KPI 标签跟着窗口变（「30 日新增」）。
 * * **错误重试是真重拉**：错误块显示服务端原话（不改写），「重试」按下去重新打接口，
 *   不是把错误状态清掉装没事。
 * * **轮询只覆盖「这一刻」的两类**（平台/性能，60s），窗口类不轮询。
 * * **下钻是真的目的地**：top_projects 指向项目页、主机健康**不是**链接（平台没有
 *   设备列表页，假 affordance 比不点更糟）、性能表 chevron 展开分钟级 spark。
 *
 * 假数据接在 `window.fetch` 上（和 `AdminQueuePage.spec.ts` 同一套），于是
 * 「api → store → 页 → 图表组件」整条链子都真跑；手写 store 替身的话，断的恰好是
 * 「我调了我自己」，而这正是这次要守的那一段。
 */
import type { Component } from 'vue'
import type { StatsKind } from '@/api'

import { nextTick } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import AdminDashboardPage from './AdminDashboardPage.vue'

import i18n, { setLocale } from '@/i18n'
import { installPreviewFetch } from '@/proto-feedback-fixtures'
import { useFeedbackStore } from '@/stores/feedback'

/** 三条看板接口。**整段相等**匹配，不用前缀：`/api/admin/stats/feedback` 和
 *  `/api/admin/feedback`（列表）差一个词，前缀匹配会把它们混成一件事。 */
const STATS = [
  '/api/admin/stats/pipeline',
  '/api/admin/stats/product',
  '/api/admin/stats/integrations',
  '/api/admin/stats/feedback',
  '/api/admin/stats/usage',
  '/api/admin/stats/platform',
  '/api/admin/stats/performance',
] as const

/** 假数据那层 fetch。**存成常量**：每次测试又包一层的话，包装器读的是个会变的变量，
 *  第二层就会调到自己，撞成栈溢出。 */
let preview: typeof window.fetch
let hits: string[] = []
/** 带 query 的完整路径（`/api/admin/stats/usage?days=30`）—— 窗口切换钉的是它。 */
let full: string[] = []

beforeAll(() => {
  installPreviewFetch()
  preview = window.fetch
  // 页上的词条都是中文，而 `navigator.language` 在 happy-dom 里是 `en-US`。
  setLocale('zh-CN')
  // happy-dom 没有 ResizeObserver（AdminLineChart 的真实像素渲染要它）。stub 成
  // no-op：画布停在设计宽 628，图照样渲染（范本：AdminModelsPage.spec.ts）。
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  hits = []
  full = []
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    const url = new URL(raw, window.location.origin)
    if ((STATS as readonly string[]).includes(url.pathname)) {
      hits.push(url.pathname)
      full.push(`${url.pathname}${url.search}`)
    }
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
      // 这一页上的出口：KPI 卡片去队列、迷你列表的每一行去详情、top_projects 的
      // 横条去项目页。两边都只用 `name` 定过位，**路由表里没有它们时 `router-link`
      // 会当场抛**（不是「点了没反应」），于是整页在挂载时就红了 —— 所以这里要把
      // 它们摆出来，哪怕内容是个空壳。
      { path: '/admin/queue', name: 'AdminQueue', component: { template: '<div />' } },
      { path: '/feedback/:id', name: 'FeedbackDetail', component: { template: '<div />' } },
      { path: '/projects/:projectId', name: 'ProjectFrame', component: { template: '<div />' } },
      // 新板块的下钻出口：卡住的卡 / 等你处理 去话题。
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

  /** 只在**分类开关**里找按钮。页面上别处的按钮（错误块的重试、表里的 chevron）
   *  也带 button 角色 —— 不收窄范围就可能点错控件。 */
  const tab = (label: string, getAllByRole: (role: string) => HTMLElement[]) => {
    const kinds = document.querySelector('.ad__kinds')
    const buttons = kinds ? Array.from(kinds.querySelectorAll('button')) : getAllByRole('button')
    return buttons.find((b) => b.textContent?.includes(label))!
  }

  it('挂载时打的是 /admin/stats/pipeline（默认落点是交付），不是老路由', async () => {
    await mountDashboard()
    await loaded('pipeline')

    // 一次事故的化石：老路径没有路由之后会落进 `/admin/feedback/{id}` 被当成 uuid，
    // 真环境回 400、页面上是「看板加载失败」。这条断言把「前端指着哪条路」变成测试里
    // 看得见的东西。
    expect(hits).not.toContain('/api/admin/feedback/stats')
    // 其余六类这一帧没有人要看，不该被顺带拉一次；**默认那一类也只该拉一次**。
    // 挂载时控件同步（`selectKind` 顺手拉一次）和结尾那句「切片还是 null 就补一次」
    // 曾经各拉一遍，于是同一分类两个并发请求，而 `loadStats` 完成前不写 `stats`，
    // 那句必然成立 —— 序号守卫丢掉一个响应，白拉一趟。
    expect(hits).toEqual(['/api/admin/stats/pipeline'])
    // 默认窗口 7 天。
    expect(full).toEqual(['/api/admin/stats/pipeline?days=7'])
  })

  it('切分类只拉切过去的那一类，切回来不重拉', async () => {
    const { findByText, getAllByRole } = await mountDashboard()
    await loaded('pipeline')

    await fireEvent.click(tab('用量', getAllByRole))
    await loaded('usage')
    expect(hits).not.toContain('/api/admin/stats/platform')

    await fireEvent.click(tab('平台', getAllByRole))
    await loaded('platform')

    // 切回第一类：那一份已经在手上了，再拉一次只是重复读那两张最长的表。
    const before = hits.length
    await fireEvent.click(tab('交付', getAllByRole))
    expect(await findByText('交付主链')).toBeTruthy()
    expect(hits.length).toBe(before)
  })

  it('第四类「性能」：只拉它那一条，按 p95 列路由，没样本的分位数画「—」', async () => {
    const { findByText, getAllByRole, queryByText } = await mountDashboard()
    await loaded('pipeline')

    await fireEvent.click(tab('性能', getAllByRole))
    const store = await loaded('performance')

    // 它和另外几类共用那一条路径规则：切过去才拉，而且只拉它。
    expect(hits).toContain('/api/admin/stats/performance')
    expect(store.stats.pipeline).not.toBeNull() // 手上那一份没被顶掉

    // 表里列的是**路由模板**（带 `{}`），不是带 uuid 的原始路径 —— 那一列是
    // 「哪一条慢」的答案，而答案必须能被人念出来。
    expect(await findByText('/feedback/{feedback_id}')).toBeTruthy()

    // **没有样本的分位数画「—」，不是 0**：0 是一个读数（「真的很快」），null 是
    // 「这一格没有数据」。画成同一个数，会让一条从没人访问过的路由以 0ms 排在最前。
    expect(queryByText('0 ms')).toBeNull()

    // 这一类是唯一读**进程内存**的，口径必须写在页面上 —— 少了它，这些数会被读成
    // 「有历史的、整个平台的」。钉的是路由表底下那句的原话，不是 `/进程内存/`：
    // 网速面板和机器台账也有这四个字，宽匹配会命中多条、findByText 直接抛。
    expect(await findByText(/数在进程内存里/)).toBeTruthy()
  })

  it('性能屏：表按 p95 降序、「此刻最慢」横幅与最慢那行一致、量级条按全表归一', async () => {
    const { findByText, getAllByRole, container } = await mountDashboard()
    await loaded('pipeline')

    await fireEvent.click(tab('性能', getAllByRole))
    await loaded('performance')

    // fixture 是未排序的（/feedback 在前，/topics/{topic_id}/messages 的 p95 更高）——
    // 「哪条慢」的读法从上往下，排序是页面的责任，不重信后端排好的序。
    expect(await findByText('此刻最慢')).toBeTruthy()
    const banner = container.querySelector('.ad__slowest')!
    expect(banner.textContent).toContain('/topics/{topic_id}/messages')
    expect(banner.textContent).toContain('123')

    // 表里第一行就是横幅里那条。
    const rows = Array.from(container.querySelectorAll('.ad__perf-table tbody tr'))
    const first = rows[0]!
    expect(first.textContent).toContain('/topics/{topic_id}/messages')
    // 最慢那行的量级条满宽；p95 更高（122.6）的那条排在 p95 61.2 之前。
    const fill = first.querySelector('.ad__perf-p95fill')! as HTMLElement
    expect(fill.style.width).toBe('100%')
  })

  it('性能屏：路由多于 Top N 时折叠并给出被折部分的 p95 上限，筛选不受折叠限制', async () => {
    const { getAllByRole, getByPlaceholderText, getByRole, container } = await mountDashboard()
    await loaded('pipeline')
    const store = useFeedbackStore()

    await fireEvent.click(tab('性能', getAllByRole))
    await loaded('performance')

    // 在 fixture 的 7 条上追加 6 条快路由（总数 13 > Top 8）。排序降序，所以折掉的
    // 是尾部那些「真的很快」的 —— 折叠行要说出它们的 p95 上限。
    const perfStats = store.stats.performance as { routes: Record<string, unknown>[] }
    for (let i = 0; i < 6; i++) {
      perfStats.routes.push({
        method: 'GET',
        route: `/extra/route-${i}`,
        count: 10 + i,
        error_count: 0,
        status: { '2xx': 10 + i, '3xx': 0, '4xx': 0, '5xx': 0 },
        p50: 1,
        p95: 10 + i,
        p99: 20 + i,
        spark: [],
      })
    }
    await nextTick()

    // 默认只画 8 行；折叠行写着「其余 5 条 · p95 最高 12 ms」—— 被折部分里 p95
    // 最高的是 route-2（12）；route-3..5（13–15）排在有样本的几条 fixture 之前、
    // 不在折叠区里。这句话本身就是结论：被折掉的最快也就这么多，不看也罢。
    expect(container.querySelectorAll('.ad__perf-table tbody tr')).toHaveLength(8)
    const foldBtn = getByRole('button', { name: /展开其余 5 条/ })
    expect(foldBtn.textContent).toContain('12')

    // 展开后 13 行全在；再点收起。
    await fireEvent.click(foldBtn)
    expect(container.querySelectorAll('.ad__perf-table tbody tr')).toHaveLength(13)
    await fireEvent.click(getByRole('button', { name: '收起' }))
    expect(container.querySelectorAll('.ad__perf-table tbody tr')).toHaveLength(8)

    // 被折着的那条也能被筛出来：筛选作用于全部路由，不受 Top N 限制。
    await fireEvent.update(getByPlaceholderText('筛路由…'), 'route-5')
    const filtered = Array.from(container.querySelectorAll('.ad__perf-table tbody tr'))
    expect(filtered).toHaveLength(1)
    expect(filtered[0]!.textContent).toContain('/extra/route-5')
  })

  it('平台那一类把机器报成存量，并写明它不是在线数', async () => {
    const { findByText, getAllByRole, getByText } = await mountDashboard()
    await loaded('pipeline')

    await fireEvent.click(tab('平台', getAllByRole))
    await loaded('platform')

    expect(await findByText('机器（存量）')).toBeTruthy()
    // 「机器 5」这个数读的人默认会当成「现在有 5 台在跑」—— 那句话必须写着。
    expect(
      getByText('这四行是四张台账的存量，不是在线数 —— 在线状态住在进程内存里，库里没有可以查的那一列。')
    ).toBeTruthy()
  })

  it('切窗口（7→30→7）重拉已加载的窗口类，未加载的类一趟都不多发', async () => {
    const { findByText, getAllByRole, getByRole } = await mountDashboard()
    const store = await loaded('pipeline')

    // 切 30 天：只有 pipeline（此刻唯一已加载的窗口类）重拉，带着 days=30。
    await fireEvent.click(getByRole('button', { name: '30 天' }))
    await waitFor(() => expect(full).toContain('/api/admin/stats/pipeline?days=30'))
    expect(store.statsDays).toBe(30)
    // 未加载的类一趟都不该发 —— 「切窗口 = 重拉全部」会把没人看的几类也读一遍。
    expect(full.filter((u) => !u.startsWith('/api/admin/stats/pipeline'))).toEqual([])

    // 切到反馈：按新窗口拉（days=30），KPI 标签跟着窗口变。
    await fireEvent.click(tab('反馈', getAllByRole))
    await loaded('feedback')
    expect(full).toContain('/api/admin/stats/feedback?days=30')
    expect(await findByText('30 日新增')).toBeTruthy()

    // 切回 7 天：两个已加载的窗口类都重拉（缓存键=窗口，不命中 7 天的旧副本）。
    await fireEvent.click(getByRole('button', { name: '7 天' }))
    await waitFor(() => expect(full).toContain('/api/admin/stats/feedback?days=7'))
    expect(full.filter((u) => u === '/api/admin/stats/pipeline?days=7')).toHaveLength(2)
    expect(full.filter((u) => u === '/api/admin/stats/feedback?days=7')).toHaveLength(1)
  })

  it('拉取失败显示服务端原话，点「重试」真重拉', async () => {
    // 第一趟 pipeline 回 500（带服务端的原话），之后放行。直接换掉这层 fetch —
    // beforeEach 那层记 hits 的包装这里不需要（这条用例断言的是「原话」和「再拉一次」）。
    let failedOnce = false
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const raw = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
      const url = new URL(raw, window.location.origin)
      if (url.pathname === '/api/admin/stats/pipeline' && !failedOnce) {
        failedOnce = true
        return new Response(JSON.stringify({ code: 500, message: '数据库连接池满了', data: null }), {
          status: 500,
          headers: { 'content-type': 'application/json' },
        })
      }
      return preview(input as RequestInfo, init)
    }

    const { findByText, getByRole } = await mountDashboard()

    // 错误块：标题 + **服务端原话**（不改写 —— 「检查网络后重试」那种静态文案会把
    // 「连接池满了」说成另一种病）。
    expect(await findByText('看板加载失败')).toBeTruthy()
    expect(await findByText('数据库连接池满了')).toBeTruthy()

    // 重试**真重拉**：第二次放行之后，交付那一屏正常渲染。
    await fireEvent.click(getByRole('button', { name: '重试' }))
    expect(await findByText('交付主链')).toBeTruthy()
  })

  it('平台/性能两类 60s 轮询，窗口类不轮询', async () => {
    // fake timers 必须先于挂载：页面的 `setInterval(pollTick, 60s)` 在 onMounted 里
    // 排上，先挂载再换假钟，那个间隔还排在真钟上，`advanceTimersByTime` 够不着它。
    vi.useFakeTimers()
    try {
      const { getAllByRole } = await mountDashboard()
      await vi.advanceTimersByTimeAsync(1)
      const store = useFeedbackStore()
      expect(store.stats.pipeline).not.toBeNull()

      // 平台：60s 后同一类来第二趟。
      await fireEvent.click(tab('平台', getAllByRole))
      await vi.advanceTimersByTimeAsync(1)
      expect(store.stats.platform).not.toBeNull()
      expect(hits.filter((h) => h === '/api/admin/stats/platform')).toHaveLength(1)
      await vi.advanceTimersByTimeAsync(60_000)
      expect(hits.filter((h) => h === '/api/admin/stats/platform')).toHaveLength(2)

      // 窗口类（反馈）：切过去的初次加载有一趟，再过 60s 轮询不找它（窗口类有
      // 手动 R 和切窗口已经够新；轮询只覆盖「这一刻」的两类）。
      await fireEvent.click(tab('反馈', getAllByRole))
      await vi.advanceTimersByTimeAsync(1)
      expect(store.stats.feedback).not.toBeNull()
      expect(hits.filter((h) => h === '/api/admin/stats/feedback')).toHaveLength(1)
      await vi.advanceTimersByTimeAsync(60_000)
      expect(hits.filter((h) => h === '/api/admin/stats/feedback')).toHaveLength(1)
      expect(hits.filter((h) => h === '/api/admin/stats/platform')).toHaveLength(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('下钻：top_projects 是项目链接、主机健康不是链接、性能表展开 spark', async () => {
    const { findByText, getAllByRole, container } = await mountDashboard()
    await loaded('pipeline')

    // 主机健康行：**没有目的地**（平台没有设备列表页），不装成链接 —— 它曾经
    // 是跳回 `/admin` 的自链，假 affordance 比不点更糟。
    const hostTitle = await findByText('机器连败')
    const hostBlock = hostTitle.closest('.aal')!
    expect(hostBlock.querySelector('a')).toBeNull()

    // 用量：top_projects 的横条整行是指向 `/projects/{project_id}` 的链接
    // （`project_id` 一直在响应里，注释明说留着给钻取用）。
    await fireEvent.click(tab('用量', getAllByRole))
    await loaded('usage')
    const projectLink = container.querySelector('.abr a[href*="/projects/"]')
    expect(projectLink).toBeTruthy()
    expect(projectLink!.getAttribute('href')).toContain('/projects/4a1c0f6e-7b52-4d9a-9c31-2f8d5a0b7e11')

    // 性能：第一行的 chevron 展开这条路由的分钟级 spark（响应里一直回、此前
    // 没人读的那 24 个点）；spark 全 null 的行 chevron 禁用。
    // （happy-dom 不支持 `:disabled` 伪类，选择器走 `[disabled]` 属性。）
    await fireEvent.click(tab('性能', getAllByRole))
    await loaded('performance')
    const toggle = container.querySelector('.ad__perf-toggle:not([disabled])')!
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    await fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(await findByText('近 24 个分钟点的平均耗时')).toBeTruthy()
    // 禁用的 chevron（没样本可展开的行）也存在，不是被藏起来。
    expect(container.querySelectorAll('.ad__perf-toggle[disabled]').length).toBeGreaterThan(0)
  })

  it('数据到货后页头出现「更新于」时间戳；注册图是真人/Agent 双系列', async () => {
    const { findByText, getAllByRole } = await mountDashboard()
    await loaded('pipeline')

    // 时间戳跟着「这一类成功到货」走 —— 它是「这份数据有多旧」的读数。
    expect(await findByText(/更新于/)).toBeTruthy()

    // 平台注册图：真人 / Agent 两条线（拆分列一直在响应里，此前没人读）。
    await fireEvent.click(tab('平台', getAllByRole))
    await loaded('platform')
    const legend = document.querySelector('.alc__legend')!
    expect(legend.textContent).toContain('真人')
    expect(legend.textContent).toContain('Agent')
  })
})
