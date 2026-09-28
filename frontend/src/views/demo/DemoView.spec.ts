// 演示页挂起来就能演：真的消息行和真的现场都拿到剧本里的东西，而且一个请求都
// 不发 —— 它嵌在文档的 iframe 里，后端在不在都得能看。
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DemoView from './DemoView.vue'

import { setLocale } from '@/i18n'

const View = DemoView as unknown as Component

describe('DemoView', () => {
  const fetchSpy = vi.fn()

  beforeEach(() => {
    setLocale('zh-CN')
    vi.stubGlobal('fetch', fetchSpy)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  function mount(url: string) {
    const [path, search = ''] = url.split('?')
    return render(View, {
      props: { path, search: search ? `?${search}` : '' },
      global: { plugins: [createVuetify({ components, directives })] },
    })
  }

  it('embedded, jumps to the step the docs ask for and shows it finished', async () => {
    const posted: unknown[] = []
    const parent = { postMessage: (m: unknown) => posted.push(m) }
    vi.spyOn(window, 'parent', 'get').mockReturnValue(parent as unknown as Window)
    const view = mount('/demo/seats?embed=1')
    expect(posted).toContainEqual(expect.objectContaining({ cheeseDemo: 'ready', steps: 6 }))

    window.dispatchEvent(new MessageEvent('message', { data: { cheeseDemo: 'go', step: 2, play: false } }))
    await waitFor(() => expect(view.getByText('按钮用主色，别用描边', { exact: false })).toBeTruthy())
    // 现场是真的 PanelSite：队友切换栏出来了，叙述那一行也在。
    await waitFor(() => expect(view.getByText('收到补充：登录按钮改成主色实心。')).toBeTruthy())
    expect(view.getAllByRole('tab').map((t) => t.textContent?.trim())).toEqual(['全部', '芝士', '芝士K'])
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('stepping back replays the site from scratch instead of keeping later rows', async () => {
    vi.spyOn(window, 'parent', 'get').mockReturnValue({ postMessage: () => {} } as unknown as Window)
    const view = mount('/demo/seats?embed=1')
    window.dispatchEvent(new MessageEvent('message', { data: { cheeseDemo: 'go', step: 4, play: false } }))
    await waitFor(() => expect(view.getByText('uv run pytest tests/api -x -q')).toBeTruthy())
    window.dispatchEvent(new MessageEvent('message', { data: { cheeseDemo: 'go', step: 1, play: false } }))
    await waitFor(() => expect(view.queryByText('uv run pytest tests/api -x -q')).toBeNull())
  })
})
