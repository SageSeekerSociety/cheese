// 文档还在路上的时候，那一格上写什么。
//
// 这一份钉的是一个具体的谎：文档面板的编辑器空着的时候会亮出一句「芝士会在这里
// 维护文档」——那句话的意思是「这篇文档是空的」。可它在**加载期间**也照样亮着，
// 于是每一篇有内容的文档，在到达之前都先被说成空的。所以加载期间摆的必须是骨架，
// 编辑器让位。
import type { Component } from 'vue'
import type { Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const getDocNodes = vi.fn()

// The document's version history: the last edit is read on open; none here.
vi.mock('../../api/docThreads', () => ({
  listDocThreads: async () => ({ data: [], total: 0 }),
  writeDocThread: async () => ({}),
}))
vi.mock('../../api/docHistory', () => ({
  getDocVersions: async () => ({ versions: [], cursor: null }),
  restoreDocVersion: async () => ({}),
}))
vi.mock('../../api/docCollab', async () => ({
  ...(await vi.importActual<typeof import('../../api/docCollab')>('../../api/docCollab')),
  // 测试里房间的文档就用房间的 id 来认：fakeDocCollab 按它预置文档。
  getRoomDocument: async (topicId: string) => ({ id: topicId }),
}))
vi.mock('../../composables/useDocCollab', async () => ({
  useDocCollab: (await import('../../test/fakeDocCollab')).useFakeDocCollab,
}))
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getDocNodes: (...a: unknown[]) => getDocNodes(...a),
  }
})

import { arrive, resetRooms, seedRoom } from '../../test/fakeDocCollab'

import PanelDoc from './PanelDoc.vue'

import { setLocale } from '@/i18n'

// 断言按中文文案写：默认 locale 是 en，这里钉回 zh-CN。
beforeEach(() => setLocale('zh-CN'))

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

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  resetRooms()
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
    seedRoom(topic.id, '## 一段\n\n正文', { pending: true })
    const { container } = open()

    await waitFor(() => expect(container.querySelector('[role="status"][aria-busy="true"]')).not.toBeNull())
    expect(container.querySelector('.doc-skel .skel__bone--h2'), '文档的节奏是小标题带着几段字').not.toBeNull()
    const editor = container.querySelector('.doc-editor') as HTMLElement | null
    expect(!editor || editor.style.display === 'none', '这一刻编辑器不能在屏幕上').toBe(true)

    arrive(topic.id)
    await waitFor(() => expect(container.querySelector('.doc-editor')?.textContent).toContain('正文'))
  })

  it('文档到了，骨架走干净，编辑器回来', async () => {
    seedRoom(topic.id, '## 一段\n\n正文', { pending: true })
    const { container } = open()
    await waitFor(() => expect(container.querySelector('[role="status"][aria-busy="true"]')).not.toBeNull())

    arrive(topic.id)
    await waitFor(() => {
      const editor = container.querySelector('.doc-editor') as HTMLElement | null
      expect(editor?.textContent).toContain('正文')
      expect(container.querySelector('.doc-skel'), '真文档来了，骨架不能还在').toBeNull()
      expect(editor?.style.display).not.toBe('none')
    })
  })
})
