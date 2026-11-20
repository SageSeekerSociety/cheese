/**
 * 桌面版里幻灯片的字也能选、能复制（右键菜单和选区用的是同一张名单，菜单在就是选得中）。
 *
 * 名单里那份 pdf 的文字层已经在了，幻灯片的这一份当时没跟着加：名单问的是同一件事
 * ——「文件里的文字是人读的东西」——少一个，读者在桌面版里就选不中、也复制不走。
 * 修复前这一条会红（名单里补上 `.slide-text-layer` 才绿）。
 */
import type { Component } from 'vue'

import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PreviewSlides from '@/components/panels/preview/PreviewSlides.vue'
import { setLocale } from '@/i18n'

type AppWindow = { __TAURI__?: unknown }

vi.mock('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url', () => ({ default: '/worker.mjs' }))
vi.mock('pdfjs-dist/legacy/build/pdf.mjs', () => ({
  GlobalWorkerOptions: {},
  getDocument: () => ({
    promise: Promise.resolve({
      numPages: 1,
      getPage: async () => ({
        getViewport: ({ scale }: { scale: number }) => ({ width: 960 * scale, height: 540 * scale }),
        render: () => ({ promise: Promise.resolve(), cancel: vi.fn() }),
        streamTextContent: () => ({}),
      }),
      destroy: vi.fn(),
    }),
    destroy: vi.fn().mockResolvedValue(undefined),
  }),
  // pdf.js 把文字填进文字层的方式：一层里面一个 span 装一行。这里只放一个。
  TextLayer: class {
    constructor(private options: { container: HTMLElement }) {}
    cancel() {}
    async render() {
      const span = document.createElement('span')
      span.textContent = '第一页的原话'
      this.options.container.appendChild(span)
    }
  },
}))

beforeEach(async () => {
  vi.resetModules()
  vi.stubEnv('DEV', false)
  setLocale('zh-CN')
  ;(window as AppWindow).__TAURI__ = { core: { invoke: async () => undefined } }
  const { behaveAsDesktopApp } = await import('./desktopNative')
  behaveAsDesktopApp()
})

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
  delete (window as AppWindow).__TAURI__
  delete document.documentElement.dataset.desktopApp
  document.head.querySelectorAll('style').forEach((s) => s.remove())
})

it('幻灯片的文字层是可选中的文本', async () => {
  const ui = render(PreviewSlides as unknown as Component, { props: { data: new ArrayBuffer(8) } })

  const span = await waitFor(() => {
    const found = ui.container.querySelector('.slide-text-layer span') as HTMLElement | null
    expect(found).toBeTruthy()
    return found as HTMLElement
  })

  // 名单同时管两件事，这里读的是能看出来的一件：右键菜单没有被拦，说明这一层在名单上，
  // 也就说明它和 pdf 那一层一样是可选的文本（旁边那份 checklist 的用例同一条读法）。
  const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true })
  span.dispatchEvent(event)
  expect(event.defaultPrevented).toBe(false)
})
