// @vitest-environment jsdom
// 大纲与文档内查找在面板这一层的接线：点了顶栏的按钮，能不能真的列出标题、查到字、
// 上下跳、Esc 关闭。阈值以上的逻辑（抽取、匹配、下标）由 lib/docOutline.spec 与
// lib/docFind.spec 钉住；这里证明它们接到了界面上。
import { defineComponent, h, nextTick } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DOC_TOPIC, docPanelProps, docSession } from '../../views/demo/catalogFixtures'

import PanelDocView from './PanelDocView.vue'

import { setLocale } from '@/i18n'

vi.mock('@tiptap/extension-drag-handle-vue-3', () => ({ DragHandle: { render: () => null } }))

const MARKDOWN = '# 章程\n\n开头一段话，这里有一个词：部署。\n\n## 部署\n\n再说一次部署。\n\n### 收尾\n\n结束。'

beforeEach(() => {
  setLocale('zh-CN')
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
      unobserve() {}
    }
  )
  // Vuetify 的浮层定位读它；jsdom 里没有。
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const width = this.classList.contains('doc-pane') ? 1000 : 300
    return { x: 0, y: 0, left: 0, top: 0, width, right: width, height: 600, bottom: 600, toJSON: () => ({}) }
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function mountDoc(markdown = MARKDOWN, editable = true) {
  const view = render(
    defineComponent({
      setup: () => () =>
        h(PanelDocView, docPanelProps({ topic: DOC_TOPIC, session: docSession(markdown), editable, loading: false })),
    }),
    { global: { plugins: [createVuetify({ components, directives })] } }
  )
  await waitFor(() => expect(document.querySelector('.doc-prose')).toBeTruthy())
  await nextTick()
  return view
}

async function openOutline() {
  await fireEvent.click(screen.getByRole('button', { name: '大纲' }))
  // 标题文字在正文里也在大纲里，所以按大纲容器取，别让 screen 撞上多份。
  return waitFor(() => {
    const el = document.querySelector('.doc-outline') as HTMLElement | null
    expect(el).toBeTruthy()
    return el as HTMLElement
  })
}

describe('文档大纲', () => {
  it('顶栏打开大纲，把正文里的标题列出来', async () => {
    await mountDoc()
    const menu = await openOutline()
    const text = menu.textContent ?? ''
    expect(text).toContain('章程')
    expect(text).toContain('部署')
    expect(text).toContain('收尾')
    expect(menu.querySelectorAll('.v-list-item')).toHaveLength(3)
  })

  it('只读文档里大纲照样列得出来', async () => {
    await mountDoc(MARKDOWN, false)
    const menu = await openOutline()
    const text = menu.textContent ?? ''
    expect(text).toContain('章程')
    expect(text).toContain('收尾')
    expect(menu.querySelectorAll('.v-list-item')).toHaveLength(3)
  })

  it('没有标题的文档给一句空态', async () => {
    await mountDoc('只有一段普通的话。')
    await fireEvent.click(screen.getByRole('button', { name: '大纲' }))
    expect(await screen.findByText('这篇文档还没有标题')).toBeTruthy()
  })
})

describe('文档内查找', () => {
  it('点顶栏打开查找条，输入后显示第几处 / 共几处，上下跳与 Esc 关闭', async () => {
    const view = await mountDoc()
    await fireEvent.click(view.getByRole('button', { name: '在文档中查找' }))
    const input = view.getByRole('textbox', { name: '在文档中查找' }) as HTMLInputElement
    await fireEvent.update(input, '部署')
    await waitFor(() => expect(view.container.querySelector('.doc-find__count')?.textContent?.trim()).toBe('1/3'))

    await fireEvent.keyDown(input, { key: 'Enter' })
    expect(view.container.querySelector('.doc-find__count')?.textContent?.trim()).toBe('2/3')
    await fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(view.container.querySelector('.doc-find__count')?.textContent?.trim()).toBe('1/3')

    await fireEvent.keyDown(input, { key: 'Escape' })
    expect(view.container.querySelector('.doc-find')).toBeNull()
  })

  it('查不到时说明没有结果', async () => {
    const view = await mountDoc()
    await fireEvent.click(view.getByRole('button', { name: '在文档中查找' }))
    const input = view.getByRole('textbox', { name: '在文档中查找' })
    await fireEvent.update(input, '橘子')
    await waitFor(() => expect(view.container.querySelector('.doc-find__count')?.textContent?.trim()).toBe('没有找到'))
  })
})
