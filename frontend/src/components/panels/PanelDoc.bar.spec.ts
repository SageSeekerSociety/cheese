// 文档那一条横条：平常只说保存到哪了。只读和源码是偶尔才进的两种状态——入口在
// ⋯ 里，进去之后这一条上写着你在哪，点它就回来。
import type { Component } from 'vue'
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getDoc = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getDoc: (...a: unknown[]) => getDoc(...a),
    getComments: vi.fn(async () => ({ data: [], total: 0 })),
    getDocNodes: vi.fn(async () => ({ data: [], total: 0 })),
  }
})

import PanelDoc from './PanelDoc.vue'

const Doc = PanelDoc as unknown as Component

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: '房间',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
} as Topic

beforeAll(() => {
  // 桌面宽度：源码模式只在桌面上提供。
  Object.defineProperty(window, 'innerWidth', { value: 1280, writable: true, configurable: true })
  // happy-dom 没有这两样，而菜单打开时 Vuetify 要读它们来摆位置。
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1280,
    height: 800,
    offsetLeft: 0,
    offsetTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
})

beforeEach(() => {
  getDoc.mockReset()
  getDoc.mockResolvedValue({ id: 'd1', kind: 'doc', content: '第一段\n', doc_version: 1 } as unknown as Block)
})

afterEach(cleanup)

async function mountDoc() {
  const view = render(Doc, {
    props: { topic, activityTick: 0, topicList: [] },
    // 源码模式里是一个 Monaco，这里只关心进没进得去、出没出得来。
    global: { plugins: [createVuetify({ components, directives })], stubs: { CodeEditor: true } },
  })
  await waitFor(() => expect(view.container.textContent).toContain('第一段'))
  return view
}

function bar(container: Element): HTMLElement {
  return container.querySelector('.doc-bar') as HTMLElement
}

async function fromMenu(container: Element, name: string) {
  await fireEvent.click(bar(container).querySelector('[aria-label="更多"]')!)
  await fireEvent.click(await screen.findByText(name, { selector: '.v-list-item-title' }))
}

describe('文档横条', () => {
  it('平常这一条上没有只读和源码两颗按钮', async () => {
    const { container } = await mountDoc()

    const labels = Array.from(bar(container).querySelectorAll('button')).map((b) => b.textContent?.trim())
    expect(labels).not.toContain('只读')
    expect(labels).not.toContain('源码')
  })

  it('从 ⋯ 设为只读后，这一条上写着只读，点它回到编辑', async () => {
    const { container } = await mountDoc()

    await fromMenu(container, '设为只读')
    const readOnly = Array.from(bar(container).querySelectorAll('button')).find((b) => b.textContent?.trim() === '只读')
    expect(readOnly, '只读了却看不出来').toBeTruthy()
    expect(container.querySelector('.doc-body')?.classList.contains('readonly')).toBe(true)

    await fireEvent.click(readOnly!)
    expect(container.querySelector('.doc-body')?.classList.contains('readonly')).toBe(false)
  })

  it('从 ⋯ 进源码模式后，这一条上写着源码，点它退出', async () => {
    const { container } = await mountDoc()

    await fromMenu(container, '源码模式')
    const source = Array.from(bar(container).querySelectorAll('button')).find((b) => b.textContent?.trim() === '源码')
    expect(source, '进了源码模式却看不出来').toBeTruthy()
    expect(container.querySelector('.doc-source')).toBeTruthy()

    await fireEvent.click(source!)
    await waitFor(() => expect(container.querySelector('.doc-source')).toBeNull())
  })
})
