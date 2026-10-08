// 演示页挂起来就能演：真的消息行和真的现场都拿到剧本里的东西，而且一个请求都
// 不发 —— 它嵌在文档的 iframe 里，后端在不在都得能看。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// happy-dom 里模块内的 `fetch` 不走测试替换的那一份，所以验收卡读卡的两个函数
// 直接接到演示后端的路由表上——答的内容和页面里 fetch 拿到的是同一份。
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  const { answerFor } = await import('./demoBackend')
  return {
    ...actual,
    getAcceptCards: async (topicId: string) => answerFor(`/topics/${topicId}/accept-card`) ?? { data: [], total: 0 },
    getPrChecks: async (topicId: string) => answerFor(`/topics/${topicId}/pr-checks`) ?? { available: false },
  }
})

import { demoRouter } from './demoRouter'
import DemoView from './DemoView.vue'

import i18n, { setLocale } from '@/i18n'

const View = DemoView as unknown as Component

describe('DemoView', () => {
  const fetchSpy = vi.fn()
  let original: typeof fetch

  beforeEach(() => {
    setLocale('zh-CN')
    // 直接换掉 fetch（不用 stubGlobal）：演示后端要能包在它外面，没走演示后端的请求
    // 才会落到这个替身上。
    original = globalThis.fetch
    fetchSpy.mockClear()
    globalThis.fetch = fetchSpy as unknown as typeof fetch
  })
  afterEach(() => {
    globalThis.fetch = original
    vi.restoreAllMocks()
  })

  function mount(url: string) {
    const [path, search = ''] = url.split('?')
    return render(View, {
      props: { path, search: search ? `?${search}` : '' },
      global: { plugins: [createVuetify({ components, directives }), createPinia(), demoRouter(), i18n] },
    })
  }

  it('embedded, jumps to the step the docs ask for and shows it finished', async () => {
    const posted: unknown[] = []
    const parent = { postMessage: (m: unknown) => posted.push(m) }
    vi.spyOn(window, 'parent', 'get').mockReturnValue(parent as unknown as Window)
    const view = mount('/demo/seats?embed=1')
    expect(posted).toContainEqual(expect.objectContaining({ cheeseDemo: 'ready', steps: 6 }))

    window.dispatchEvent(
      new MessageEvent('message', { data: { cheeseDemo: 'go', step: 2, play: false }, origin: location.origin })
    )
    await waitFor(() => expect(view.getByText('按钮用主色，别用描边', { exact: false })).toBeTruthy())
    // 现场是真的 PanelSite：队友切换栏出来了，叙述那一行也在。
    await waitFor(() => expect(view.getByText('收到补充：登录按钮改成主色实心。')).toBeTruthy())
    const tabs = view.getAllByRole('tab').map((t) => t.textContent?.trim())
    // 右边那条是产品的工作面板页签（`panelTabs`，和桌面上的工作面板同一张表）。
    expect(tabs.slice(0, 4)).toEqual(['概览', '现场', '改动', '预览'])
    // 后面那一组是座位切换栏（演示自己画的），和页签条混在一个无障碍树里。
    expect(tabs.slice(4)).toEqual(['全部', '芝士', '芝士K'])
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('talks only to the docs page: posts to its origin, ignores any other', async () => {
    vi.stubEnv('VITE_DOCS_ORIGIN', 'https://docs.example.test')
    try {
      const sent: [unknown, string][] = []
      const parent = { postMessage: (m: unknown, origin: string) => sent.push([m, origin]) }
      vi.spyOn(window, 'parent', 'get').mockReturnValue(parent as unknown as Window)
      const view = mount('/demo/seats?embed=1')
      expect(sent.every(([, origin]) => origin === 'https://docs.example.test')).toBe(true)
      expect(sent.length).toBeGreaterThan(0)

      // A page on this origin, or anywhere else, does not get to drive it.
      for (const origin of [location.origin, 'https://elsewhere.example']) {
        window.dispatchEvent(new MessageEvent('message', { data: { cheeseDemo: 'go', step: 4, play: false }, origin }))
      }
      await new Promise((r) => setTimeout(r, 50))
      expect(view.queryByText('uv run pytest tests/api -x -q')).toBeNull()

      window.dispatchEvent(
        new MessageEvent('message', {
          data: { cheeseDemo: 'go', step: 4, play: false },
          origin: 'https://docs.example.test',
        })
      )
      await waitFor(() => expect(view.getByText('uv run pytest tests/api -x -q')).toBeTruthy())
    } finally {
      vi.unstubAllEnvs()
    }
  })

  it('stepping back replays the site from scratch instead of keeping later rows', async () => {
    vi.spyOn(window, 'parent', 'get').mockReturnValue({ postMessage: () => {} } as unknown as Window)
    const view = mount('/demo/seats?embed=1')
    window.dispatchEvent(
      new MessageEvent('message', { data: { cheeseDemo: 'go', step: 4, play: false }, origin: location.origin })
    )
    await waitFor(() => expect(view.getByText('uv run pytest tests/api -x -q')).toBeTruthy())
    window.dispatchEvent(
      new MessageEvent('message', { data: { cheeseDemo: 'go', step: 1, play: false }, origin: location.origin })
    )
    await waitFor(() => expect(view.queryByText('uv run pytest tests/api -x -q')).toBeNull())
  })

  it('draws the real accept card, checklist and turn summary from the scene', async () => {
    vi.spyOn(window, 'parent', 'get').mockReturnValue({ postMessage: () => {} } as unknown as Window)
    const view = mount('/demo/quickstart?embed=1')
    window.dispatchEvent(
      new MessageEvent('message', { data: { cheeseDemo: 'go', step: 4, play: false }, origin: location.origin })
    )
    // 验收卡是真的 TopicAcceptCard，数据来自演示后端。
    await waitFor(() => expect(view.getAllByRole('button').some((b) => /采纳/.test(b.textContent ?? ''))).toBe(true))
    expect(view.getAllByText('docs: add a welcome note', { exact: false }).length).toBeGreaterThan(0)
    // 本轮摘要由 collapseNotices 折出来：文件数和增删。
    expect(view.getByText(/改动了 1 个文件/)).toBeTruthy()
    // 步骤清单：三项都打勾，结果那一句在。
    expect(view.getByText('README.md 写好了，验收卡已递给王长鑫', { exact: false })).toBeTruthy()
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('shows the panel a step declares, and 现场 when it declares none', async () => {
    vi.spyOn(window, 'parent', 'get').mockReturnValue({ postMessage: () => {} } as unknown as Window)
    const view = mount('/demo/quickstart?embed=1')
    const selected = () =>
      view.container.querySelector('[data-region="tabs"] [role="tab"][aria-selected="true"]')?.textContent?.trim()

    // 第三步（这张剧本里没写 panel）：右边还是现场，改动那几格连挂都没挂上。
    window.dispatchEvent(
      new MessageEvent('message', { data: { cheeseDemo: 'go', step: 2, play: false }, origin: location.origin })
    )
    await waitFor(() => expect(selected()).toBe('现场'))
    expect(view.container.querySelectorAll('[data-region="panel"] > *').length).toBe(1)

    // 第四步写了 panel: changes：选中的换成改动，格子里是产品自己的 PanelChanges，
    // 画的是剧本里那份 diff（README.md，+6）。
    window.dispatchEvent(
      new MessageEvent('message', { data: { cheeseDemo: 'go', step: 3, play: false }, origin: location.origin })
    )
    await waitFor(() => expect(selected()).toContain('改动'))
    // 页签上带着改动的规模（剧本里那一个文件）。
    expect(view.container.querySelector('.tabbar__count')?.textContent).toBe('1')
    await waitFor(() => expect(view.container.textContent).toContain('+这个项目放本课程的课件和作业。'))
    expect(view.container.textContent).toContain('README.md')
    // 现场那一格还挂着，只是藏起来了（和产品一样：切走不卸）。
    expect(view.container.querySelector<HTMLElement>('.panel-site')?.style.display).toBe('none')
    expect(view.container.querySelector<HTMLElement>('.panel-changes')?.style.display).not.toBe('none')
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})
