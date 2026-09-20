// 文档还在路上的时候，那一格上写什么。
//
// 这一份钉的是一个具体的谎：文档面板的编辑器空着的时候会亮出一句「芝士会在这里
// 维护文档」——那句话的意思是「这篇文档是空的」。可它在**加载期间**也照样亮着，
// 于是每一篇有内容的文档，在到达之前都先被说成空的。所以加载期间摆的必须是骨架，
// 编辑器让位。
import type { Component } from 'vue'
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getDoc = vi.fn()
const getComments = vi.fn()
const getDocNodes = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getDoc: (...a: unknown[]) => getDoc(...a),
    getComments: (...a: unknown[]) => getComments(...a),
    getDocNodes: (...a: unknown[]) => getDocNodes(...a),
  }
})

import PanelDoc from './PanelDoc.vue'

const Doc = PanelDoc as unknown as Component

const topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: '话题',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
} as Topic

function doc(content: string): Block {
  return { id: 'd1', kind: 'doc', content, doc_version: 3 } as unknown as Block
}

/** 一次拿得住的请求：先挂着，等测试自己决定什么时候让文档到达。 */
function pending<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  getDoc.mockReset()
  getComments.mockResolvedValue({ data: [], total: 0 })
  getDocNodes.mockResolvedValue({ data: [], total: 0 })
})

function open() {
  return render(Doc, {
    props: { topic, activityTick: 0, topicList: [] },
    global: { plugins: [vuetify] },
  })
}

describe('文档还在路上', () => {
  it('画的是文档的形状，编辑器让位 —— 空编辑器会说这篇文档是空的', async () => {
    const gate = pending<Block | null>()
    getDoc.mockReturnValue(gate.promise)
    const { container } = open()

    await waitFor(() => expect(container.querySelector('[role="status"][aria-busy="true"]')).not.toBeNull())
    expect(container.querySelector('.doc-skel .skel__bone--h2'), '文档的节奏是小标题带着几段字').not.toBeNull()
    const editor = container.querySelector('.doc-editor') as HTMLElement | null
    expect(editor && editor.style.display, '这一刻编辑器不能在屏幕上').toBe('none')

    gate.resolve(doc('## 一段\n\n正文'))
    await waitFor(() => expect(container.textContent).toContain('正文'))
  })

  it('文档到了，骨架走干净，编辑器回来', async () => {
    const gate = pending<Block | null>()
    getDoc.mockReturnValue(gate.promise)
    const { container } = open()
    await waitFor(() => expect(container.querySelector('[role="status"][aria-busy="true"]')).not.toBeNull())

    gate.resolve(doc('## 一段\n\n正文'))
    await waitFor(() => expect(container.textContent).toContain('正文'))
    expect(container.querySelector('.doc-skel'), '真文档来了，骨架不能还在').toBeNull()
    const editor = container.querySelector('.doc-editor') as HTMLElement | null
    expect(editor && editor.style.display).not.toBe('none')
  })
})
