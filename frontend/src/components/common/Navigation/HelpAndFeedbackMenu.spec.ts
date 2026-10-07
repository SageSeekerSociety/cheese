/**
 * 顶栏那颗「帮助与反馈」（`HelpAndFeedbackMenu`）。
 *
 * 它钉四件**只有真挂载才看得见**的事 —— 三件来自设计稿复核时提出的风险，一件来自
 * 这个仓库已经付出过代价的形状问题：
 *
 *   1. 未读为 0 不画点、未读大于 0 画点。这个数（`counts.unread`）服务端一直在算，
 *      而在这一轮之前**没有任何模板读过它** —— 所以它坏掉的方式是「点永远不出现」。
 *   2. 状态同时进**可访问名字**。只靠一个色点的话，读屏和色觉障碍读者在入口上收不到
 *      「有未读更新」这件事 —— 设计稿的复核特意点了这一条（照抄通知铃铛会把铃铛自己
 *      的同一个缺陷一起搬过来）。
 *   3. **非管理员不渲染「管理后台」那一项**，而这一项由服务端（`/feedback/meta` 的
 *      `is_admin`）说了算，不是前端按 handle 猜。
 *   4. `compact`（手机）不画文字标签，但仍然是一颗**按钮**、仍然点得开 —— 手机上那颗
 *      以前是一条 `<a>`，形状换了之后最容易顺手丢的就是「点得开」。
 */
import { createRouter, createWebHashHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getFeedbackMeta = vi.fn()
const getFeedbackCounts = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getFeedbackMeta: (...a: unknown[]) => getFeedbackMeta(...a),
    getFeedbackCounts: (...a: unknown[]) => getFeedbackCounts(...a),
  }
})

import { useDesktopApp } from '@/composables/useDesktopApp'

import HelpAndFeedbackMenu from './HelpAndFeedbackMenu.vue'

import i18n, { setLocale } from '@/i18n'

/** 挂一次，`meta` 与 `counts` 由调用方给的桩决定。 */
async function mount(
  props: { compact?: boolean } = {},
  opts: { admin?: boolean; platformAdmin?: boolean; unread?: number } = {}
) {
  getFeedbackMeta.mockResolvedValue({
    is_admin: !!opts.admin,
    is_platform_admin: !!opts.platformAdmin,
    tabs: ['all'],
    kinds: ['bug'],
    hot_supports: 5,
  })
  getFeedbackCounts.mockResolvedValue({ all: 0, hot: 0, active: 0, resolved: 0, unread: opts.unread ?? 0 })

  const router = createRouter({
    history: createWebHashHistory(),
    routes: [
      { path: '/feedback', name: 'FeedbackCenter', component: { template: '<div />' } },
      { path: '/feedback/mine', name: 'FeedbackMine', component: { template: '<div />' } },
      { path: '/admin/feedback', name: 'AdminFeedback', component: { template: '<div />' } },
      { path: '/admin/overview', name: 'AdminOverview', component: { template: '<div />' } },
    ],
  })
  await router.push('/')
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  return render(HelpAndFeedbackMenu, { props, global: { plugins: [vuetify, router, createPinia(), i18n] } })
}

// 菜单是一个 `VOverlay`，而 happy-dom 没有 `visualViewport` —— 不补这一样，点开之后
// 菜单根本挂不上去，测到的就成了「点了没反应」（同 `ProjectLibraryView.spec.ts` 那份）。
beforeAll(() => {
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
  // 还有 `devicePixelRatio`：菜单定位时 Vuetify 拿它做像素取整，happy-dom 没有这一项，
  // 少了它每个用例都会抛一条**未处理的 rejection**（用例本身还是绿的，但 vitest 退出码
  // 是 1 —— 而「绿着退出 1」正是最难查的那种）。
  if (!('devicePixelRatio' in globalThis)) {
    Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
  }
})

beforeEach(() => {
  // 词条是中文的，而 happy-dom 的 `navigator.language` 是 `en-US`。
  setLocale('zh-CN')
  getFeedbackMeta.mockReset()
  getFeedbackCounts.mockReset()
})

describe('帮助与反馈入口', () => {
  it('未读为 0 时不画点，未读大于 0 时画点', async () => {
    const none = await mount({}, { unread: 0 })
    const entry0 = none.container.querySelector('.help-entry') as HTMLElement
    await waitFor(() => expect(getFeedbackCounts).toHaveBeenCalled())
    expect(entry0.querySelector('.v-badge')).toBeNull()

    const some = await mount({}, { unread: 3 })
    const entry1 = some.container.querySelector('.help-entry') as HTMLElement
    await waitFor(() => expect(entry1.querySelector('.v-badge')).not.toBeNull())
  })

  it('未读状态进可访问名字 —— 不靠色点', async () => {
    const { container } = await mount({}, { unread: 2 })
    const entry = container.querySelector('.help-entry') as HTMLElement
    await waitFor(() => expect(entry.getAttribute('aria-label')).toBe('帮助与反馈，有未读更新'))

    const quiet = await mount({}, { unread: 0 })
    const quietEntry = quiet.container.querySelector('.help-entry') as HTMLElement
    await waitFor(() => expect(quietEntry.getAttribute('aria-label')).toBe('帮助与反馈'))
  })

  it('管理后台那一项只对管理员出现，而且由服务端说了算', async () => {
    const plain = await mount({}, { admin: false })
    await fireEvent.click(plain.container.querySelector('.help-entry') as HTMLElement)
    await waitFor(() => expect(document.body.textContent).toContain('反馈中心'))
    expect(document.body.textContent).toContain('我的反馈')
    expect(document.body.textContent).not.toContain('管理后台')

    const admin = await mount({}, { admin: true })
    await fireEvent.click(admin.container.querySelector('.help-entry') as HTMLElement)
    await waitFor(() => expect(document.body.textContent).toContain('管理后台'))
  })

  it('只是平台管理员：管理后台那一项落在平台总览上，不落在他进不去的队列上', async () => {
    const { container } = await mount({}, { admin: false, platformAdmin: true })
    await fireEvent.click(container.querySelector('.help-entry') as HTMLElement)
    await waitFor(() => expect(document.body.textContent).toContain('管理后台'))
    const link = Array.from(document.querySelectorAll('a')).find((a) => a.textContent?.includes('管理后台'))
    expect(link?.getAttribute('href')).toContain('/admin/overview')
  })

  it('使用文档那一项整页跳到 /docs/，不交给应用的路由', async () => {
    const { container } = await mount()
    await fireEvent.click(container.querySelector('.help-entry') as HTMLElement)
    await waitFor(() => expect(document.body.textContent).toContain('使用文档'))
    const link = Array.from(document.querySelectorAll('a')).find((a) => a.textContent?.includes('使用文档'))
    expect(link?.getAttribute('href')).toBe('/docs/')
  })

  it('文档站有自己的域名时，使用文档那一项直接去那里', async () => {
    vi.stubEnv('VITE_DOCS_ORIGIN', 'https://docs.example.test')
    try {
      const { container } = await mount()
      await fireEvent.click(container.querySelector('.help-entry') as HTMLElement)
      await waitFor(() => expect(document.body.textContent).toContain('使用文档'))
      const link = Array.from(document.querySelectorAll('a')).find((a) => a.textContent?.includes('使用文档'))
      expect(link?.getAttribute('href')).toBe('https://docs.example.test/')
    } finally {
      vi.unstubAllEnvs()
    }
  })

  it('compact（手机）不画文字标签，但仍然是一颗点得开的按钮', async () => {
    const { container } = await mount({ compact: true }, { admin: true })
    const entry = container.querySelector('.help-entry') as HTMLElement
    expect(entry.tagName).toBe('BUTTON')
    expect(entry.querySelector('.help-entry__label')).toBeNull()

    await fireEvent.click(entry)
    await waitFor(() => expect(document.body.textContent).toContain('反馈中心'))
  })
})

// 桌面 app 里「了解知是」是「关于」：推广页只在浏览器里开；文档站在 app 的窗口里没有
// 回来的路，也交给浏览器。
describe('帮助与反馈，在桌面 app 里', () => {
  type AppWindow = { __TAURI__?: unknown; __CHEESE_APP__?: unknown }
  beforeEach(() => {
    ;(window as AppWindow).__TAURI__ = { core: { invoke: vi.fn() } }
    ;(window as AppWindow).__CHEESE_APP__ = { origin: 'https://okcheese.com', can: ['links', 'updates'] }
  })
  afterEach(() => {
    delete (window as AppWindow).__TAURI__
    delete (window as AppWindow).__CHEESE_APP__
    vi.restoreAllMocks()
  })

  it('「关于知是」打开关于对话框，不去推广页', async () => {
    const { container } = await mount()
    await fireEvent.click(container.querySelector('.help-entry') as HTMLElement)
    await waitFor(() => expect(document.body.textContent).toContain('关于知是'))
    expect(document.body.textContent).not.toContain('了解知是')
    expect(Array.from(document.querySelectorAll('a')).some((a) => a.getAttribute('href')?.includes('/about'))).toBe(
      false
    )
    const { aboutOpen } = useDesktopApp()
    const item = Array.from(document.querySelectorAll('.v-list-item')).find((el) =>
      el.textContent?.includes('关于知是')
    ) as HTMLElement
    await fireEvent.click(item)
    expect(aboutOpen.value).toBe(true)
  })

  it('使用文档在浏览器里打开', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    const { container } = await mount()
    await fireEvent.click(container.querySelector('.help-entry') as HTMLElement)
    await waitFor(() => expect(document.body.textContent).toContain('使用文档'))
    const item = Array.from(document.querySelectorAll('.v-list-item')).find((el) =>
      el.textContent?.includes('使用文档')
    ) as HTMLElement
    await fireEvent.click(item)
    expect(open).toHaveBeenCalledWith(`${window.location.origin}/docs/`, '_blank')
  })

  it('文档站有自己的域名时，浏览器打开的是那个域名', async () => {
    vi.stubEnv('VITE_DOCS_ORIGIN', 'https://docs.example.test')
    try {
      const open = vi.spyOn(window, 'open').mockReturnValue(null)
      const { container } = await mount()
      await fireEvent.click(container.querySelector('.help-entry') as HTMLElement)
      await waitFor(() => expect(document.body.textContent).toContain('使用文档'))
      const item = Array.from(document.querySelectorAll('.v-list-item')).find((el) =>
        el.textContent?.includes('使用文档')
      ) as HTMLElement
      await fireEvent.click(item)
      expect(open).toHaveBeenCalledWith('https://docs.example.test/', '_blank')
    } finally {
      vi.unstubAllEnvs()
    }
  })
})
