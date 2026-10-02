// 文档那一条横条：平常只有谁也在这儿。只读是偶尔才进的状态——入口在 ⋯ 里，进去之后
// 这一条上写着只读，点它就回来；没有编辑权限时它只是说明，回不去。
import type { Component } from 'vue'
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getDoc = vi.fn()

vi.mock('../../composables/useDocCollab', async () => ({
  useDocCollab: (await import('../../test/fakeDocCollab')).useFakeDocCollab,
}))
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getDoc: (...a: unknown[]) => getDoc(...a),
    getComments: vi.fn(async () => ({ data: [], total: 0 })),
    getDocNodes: vi.fn(async () => ({ data: [], total: 0 })),
  }
})

import { resetRooms, seedRoom } from '../../test/fakeDocCollab'

import PanelDoc from './PanelDoc.vue'

import { setLocale } from '@/i18n'

// 断言按中文文案写：默认 locale 是 en，这里钉回 zh-CN。
beforeEach(() => setLocale('zh-CN'))

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
  resetRooms()
  seedRoom(topic.id, '第一段\n')
  getDoc.mockReset()
  getDoc.mockResolvedValue({ id: 'd1', kind: 'doc', content: '第一段\n', doc_version: 1 } as unknown as Block)
})

afterEach(cleanup)

async function mountDoc() {
  const view = render(Doc, {
    props: { topic, activityTick: 0, topicList: [] },
    global: { plugins: [createVuetify({ components, directives })] },
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
  it('formats loaded content through the real panel editor and blocks formatting in read-only mode', async () => {
    const { container } = await mountDoc()
    await fireEvent.click(screen.getByRole('button', { name: /^标题 1$/ }))
    await waitFor(() => expect(container.querySelector('.doc-prose h1')?.textContent).toBe('第一段'))
    await fromMenu(container, '设为只读')
    await fireEvent.click(screen.getByRole('button', { name: /^标题 2$/ }))
    expect(container.querySelector('.doc-prose h1')?.textContent).toBe('第一段')
    expect(container.querySelector('.doc-prose h2')).toBeNull()
  })

  it('平常这一条上没有只读按钮', async () => {
    const { container } = await mountDoc()

    const labels = Array.from(bar(container).querySelectorAll('button')).map((b) => b.textContent?.trim())
    expect(labels).not.toContain('只读')
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

  it('没有编辑权限时，正文改不了，只读也切不回编辑', async () => {
    seedRoom(topic.id, '第一段\n', { readOnly: true })
    const { container } = await mountDoc()

    expect(container.querySelector('.doc-prose')?.getAttribute('contenteditable')).toBe('false')
    const readOnly = Array.from(bar(container).querySelectorAll('button')).find((b) => b.textContent?.trim() === '只读')
    expect(readOnly?.hasAttribute('disabled'), '没有权限还能点回编辑').toBe(true)
    expect(bar(container).querySelector('[aria-label="更多"]'), '⋯ 里只有切换只读这一项，没有权限时不给').toBeNull()
  })
})
