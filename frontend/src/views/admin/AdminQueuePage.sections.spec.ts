/**
 * 队列页拆开之前先钉住的那几块。
 *
 * `AdminQueuePage.vue` 1309 行，要拆进一份 composable 和 `components/admin/queue/`
 * 下面几件。`AdminQueuePage.spec.ts` 已经把「整条链子」和「切视图/切页签不发请求」
 * 钉住了；这一份补的是它没盖到的四块 —— 也就是**这次要挪走的那几块**：
 *
 *   1. 页头那排工具：未读徽标与「标记为已读」什么时候出现、刷新按钮的无障碍名、
 *      视图切换两颗的 `aria-pressed`；
 *   2. 工具行三组控件的组名、档位与选中态：栏位（radiogroup）、搜索框、状态页签；
 *   3. **总表那一档**的失败文案 —— 它和列表那一档**不是同一句**（「表格加载失败」对
 *      「队列加载失败」），而四态块外面那层 `title` 上挂的是服务端原话；
 *   4. 宽屏的**整页接管**：光标落定、详情画出来之后，队列整块从 DOM 里让位。
 *
 * 外加分诊与撤销（§9.6 / F-12）：`1` 推一格、撤销条说的是什么、`U` 撤回时写回去的
 * 是原来那一格、以及撤回那一次**不再**套出一条新的撤销条。
 *
 * 链子和 `AdminQueuePage.spec.ts` 是同一条：不 mock `@/api`、不 mock store，假数据
 * 接在 `window.fetch` 上（`proto-preview-transport.ts`），于是「api → store → 页 →
 * 组件」整条都真跑。文案断言用真 i18n（`setLocale('zh-CN')`），因为钉的一半是句子。
 *
 * 绿在拆之前的旧文件上；拆完必须原样绿。
 */
import type { Component } from 'vue'

import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest'

import AdminQueuePage from './AdminQueuePage.vue'

import i18n, { setLocale } from '@/i18n'
import { installPreviewFetch } from '@/proto-preview-transport'

/** 管理端的列表路由。详情是 `/api/admin/feedback/{id}`，所以这里按**整段相等**匹配。 */
const LIST_PATH = '/api/admin/feedback'

/** 假数据那层 fetch。**存成常量**：每次测试又包一层的话，包装器读的是个会变的变量，
 *  第二层就会调到自己，撞成栈溢出。 */
let preview: typeof window.fetch
let listCalls = 0
/** 头几次列表请求打 500，之后放行 —— 「重试能救回来」才测得到，不只是「按钮在」。 */
let failTimes = 0
/** 每一笔推状态的请求（`POST /admin/feedback/{id}/status`）。撤销要断的是**写回去的
 *  是哪一格**，那件事只在这条请求的 body 上看得见。 */
let statusPosts: { id: string; status: string }[] = []

beforeAll(() => {
  installPreviewFetch()
  preview = window.fetch
  setLocale('zh-CN')
})

beforeEach(() => {
  listCalls = 0
  failTimes = 0
  statusPosts = []
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    const url = new URL(raw, window.location.origin)
    const method = (init?.method ?? 'GET').toUpperCase()
    if (url.pathname === LIST_PATH && method === 'GET') {
      listCalls += 1
      if (failTimes > 0) {
        failTimes -= 1
        return new Response(JSON.stringify({ code: 500, message: '后端炸了', data: null }), {
          status: 500,
          headers: { 'content-type': 'application/json' },
        })
      }
    }
    const status = /^\/api\/admin\/feedback\/([^/]+)\/status$/.exec(url.pathname)
    if (status && method === 'POST') {
      const body = JSON.parse(String(init?.body ?? '{}')) as { status?: string }
      statusPosts.push({ id: status[1]!, status: String(body.status) })
    }
    return preview(input as RequestInfo, init)
  }
})

/** 套一层 `v-app`：页里那个抽屉是 `VNavigationDrawer`，它要一个 layout 才挂得上。 */
const Wrapper = { components: { AdminQueuePage }, template: '<v-app><AdminQueuePage /></v-app>' }

async function mountQueue(query: Record<string, string> = {}) {
  const router = createRouter({
    history: createWebHashHistory(),
    routes: [{ path: '/admin/queue', component: Wrapper }],
  })
  await router.push({ path: '/admin/queue', query })
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  const pinia = createPinia()
  const rendered = render(Wrapper as unknown as Component, { global: { plugins: [vuetify, pinia, router, i18n] } })
  return { ...rendered, router, pinia }
}

const rows = (container: Element) => container.querySelectorAll('.qrow')

describe('页头工具', () => {
  it('未读徽标与「标记为已读」是有未读才有的那一对', async () => {
    const { container, findByText } = await mountQueue()
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))

    // fixtures 里的未读只由别人的评论贡献（`unreadCount`），不是写死的 0。
    const badge = container.querySelector('.qpage__badge')
    expect(badge?.textContent?.trim()).toMatch(/^\d+ 未读$/)
    expect(badge?.textContent?.trim()).not.toBe('0 未读')
    expect(await findByText('标记为已读')).toBeTruthy()
  })

  it('刷新按钮报得出名字，按下去真的重拉', async () => {
    const { container } = await mountQueue()
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))

    const refresh = container.querySelector('.qpage__icon-btn')
    expect(refresh?.getAttribute('aria-label')).toBe('刷新')

    const before = listCalls
    await fireEvent.click(refresh!)
    await waitFor(() => expect(listCalls).toBeGreaterThan(before))
  })

  it('视图切换：两档的名字与 aria-pressed，切过去之后总表挂上', async () => {
    const { container } = await mountQueue()
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))

    const segs = Array.from(container.querySelectorAll<HTMLElement>('.qpage__seg-btn'))
    expect(segs.map((el) => el.textContent?.trim())).toEqual(['列表', '表格'])
    expect(segs[0]!.getAttribute('aria-pressed')).toBe('true')
    expect(segs[1]!.getAttribute('aria-pressed')).toBe('false')

    await fireEvent.click(segs[1]!)
    await waitFor(() => expect(container.querySelector('.aft')).toBeTruthy())
    expect(segs[1]!.getAttribute('aria-pressed')).toBe('true')
    expect(segs[0]!.getAttribute('aria-pressed')).toBe('false')
  })
})

describe('工具行三组控件', () => {
  it('栏位那一组的组名、四档的名字和选中态', async () => {
    const { container, getByRole } = await mountQueue()
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))

    // 组名（`radiogroup` 的 aria-label）说的是右边那四颗是什么。
    expect(getByRole('radiogroup', { name: '栏位' })).toBeTruthy()
    const lanes = Array.from(container.querySelectorAll<HTMLElement>('.qpage__lane'))
    expect(lanes.map((el) => el.textContent?.trim())).toEqual(['公开', '私密', 'AI 队友提的', '安全'])
    expect(lanes.map((el) => el.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false', 'false'])
  })

  it('搜索框的 placeholder 与无障碍名是同一句', async () => {
    const { getByRole } = await mountQueue()
    await waitFor(() => expect(rows.length).toBeDefined())

    const input = getByRole('textbox', { name: '搜索反馈' })
    expect(input.getAttribute('placeholder')).toBe('搜索反馈')
  })

  it('状态页签五档：全部 + 四个状态名', async () => {
    const { getAllByRole } = await mountQueue()
    await waitFor(() => expect(getAllByRole('tab').length).toBeGreaterThan(0))

    expect(getAllByRole('tab').map((el) => el.textContent?.trim())).toEqual([
      '全部',
      '已收录',
      '处理中',
      '已修复',
      '已上线',
    ])
  })

  it('日期窗口 chip 的 × 带的是那一颗自己的名字', async () => {
    const { getAllByRole, findByText } = await mountQueue({ since: '7d' })

    expect(await findByText('提交：近 7 天')).toBeTruthy()
    // 无障碍名里带上 chip 正文：三颗 × 一字排开时，读屏念的不能都是「清除」。
    expect(getAllByRole('button', { name: '清除这个日期筛选：提交：近 7 天' })).toHaveLength(1)
  })
})

describe('四态：总表那一档的文案', () => {
  it('总表读失败画的是「表格加载失败」，外面那层 title 是服务端原话', async () => {
    failTimes = 1
    const { container, findByText, getByText } = await mountQueue()

    // 列表那一档先说话。
    expect(await findByText('队列加载失败')).toBeTruthy()
    // 四态块里只写「读失败」，为什么读失败挂在外面那层的 title 上。
    const raw = container.querySelector('.qpage__state-raw')
    expect(raw?.getAttribute('title')).toContain('后端炸了')

    await fireEvent.click(getByText('表格'))

    // 换一档之后文案跟着换 —— 两句不是同一句，这一条就是钉这个差别的。
    expect(await findByText('表格加载失败')).toBeTruthy()
    expect(getByText('检查网络后重试。')).toBeTruthy()
    expect(getByText('重试')).toBeTruthy()
    expect(container.querySelector('.qpage__state-raw')?.getAttribute('title')).toContain('后端炸了')
  })
})

/** 宽屏那一档（§4.4 的整页接管）：`isWide` 由 `(min-width: 1280px)` 决定，而 happy-dom
 *  里那个查询的答案不由视口宽度决定（没有布局）。所以这里**替它答 `true`** —— 测的是
 *  「这个查询成立时怎么走」，不是「某块视口怎么走」。 */
describe('宽屏整页接管（§4.4）', () => {
  const originalMatchMedia = window.matchMedia

  beforeAll(() => {
    window.matchMedia = ((query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia
  })

  afterAll(() => {
    window.matchMedia = originalMatchMedia
  })

  it('光标上按 Enter：详情整页接管，队列那一块从 DOM 里让位', async () => {
    const { container, getByText } = await mountQueue()
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))

    await fireEvent.click(getByText('表格'))
    await waitFor(() => expect(container.querySelector('.aft')).toBeTruthy())

    const first = container.querySelector<HTMLElement>('.aft__row[aria-selected] .fbrow__link')
    expect(first, '总表一行都没有，这条用例等于没做').toBeTruthy()
    first!.focus()
    await fireEvent.keyDown(first!, { key: 'Enter' })

    await waitFor(() => expect(container.querySelector('.qdet')).toBeTruthy())
    // 整页接管：队列和总表都不在 DOM 里了（并排塞不下，也免得两套方向键各收一次）。
    expect(container.querySelector('.aft')).toBeNull()
    expect(container.querySelector('.qlist')).toBeNull()
    expect(container.querySelector('.qpage__inner')).toBeNull()
  })
})

describe('分诊与撤销（F-12 / §9.6）', () => {
  it('按 1 推一格：写出去的是梯子上的下一格，撤销条报的是新状态', async () => {
    const { container, findByText } = await mountQueue()
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))

    // 只筛「已收录」那一档：这一页里每一条都是 received，按 `1` 一定推得动。
    // （状态页签是本地筛，不发请求。）
    await fireEvent.click(container.querySelectorAll<HTMLElement>('.atabs__tab')[1]!)
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))

    await fireEvent.keyDown(window, { key: '1' })

    await waitFor(() => expect(statusPosts.length).toBeGreaterThan(0))
    expect(statusPosts.at(-1)!.status).toBe('in_progress')
    expect(await findByText('已改为「处理中」')).toBeTruthy()
  })

  it('U 撤回：写回去的是原来那一格，而且不再套出一条新的撤销条', async () => {
    const { container, findByText, queryByText } = await mountQueue()
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))

    await fireEvent.click(container.querySelectorAll<HTMLElement>('.atabs__tab')[1]!)
    await waitFor(() => expect(rows(container).length).toBeGreaterThan(0))

    await fireEvent.keyDown(window, { key: '1' })
    await waitFor(() => expect(statusPosts.at(-1)?.status).toBe('in_progress'))
    expect(await findByText('已改为「处理中」')).toBeTruthy()

    await fireEvent.keyDown(window, { key: 'u' })
    await waitFor(() => expect(statusPosts.at(-1)?.status).toBe('received'))
    // 撤回那一次**不**产生新的撤销条 —— 否则 `u` 会一直套下去。
    await waitFor(() => expect(queryByText('已改为「处理中」')).toBeNull())
  })
})
