// 文档那一格打开一篇协同文档：正文从协同文档来，别人改的实时出现，评论和节点照旧
// 从接口读，打不开时说原因。
//
// 协同服务由 src/test/fakeDocCollab.ts 代替：每个房间一份「服务端」文档，面板打开
// 时拿到一份和它保持同步的客户端文档 —— 没有 socket，其余和真的一样。
import type { Component } from 'vue'
import type { Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { exportMarkdown } from '../../lib/docSchema'
import { remoteEdit, resetRooms, seedRoom, serverDoc } from '../../test/fakeDocCollab'

const mocks = vi.hoisted(() => ({
  getDocNodes: vi.fn(),
}))

// The document's comment threads: none here.
vi.mock('../../api/docThreads', () => ({
  listDocThreads: async () => ({ data: [], total: 0 }),
  writeDocThread: async () => ({}),
}))
// The document's version history: the last edit is read on open; none here.
vi.mock('../../api/docHistory', () => ({
  getDocVersions: async () => ({ versions: [], cursor: null }),
  restoreDocVersion: async () => ({}),
}))
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getDocNodes: (...a: unknown[]) => mocks.getDocNodes(...a),
  }
})
vi.mock('../../api/docCollab', async () => ({
  ...(await vi.importActual<typeof import('../../api/docCollab')>('../../api/docCollab')),
  // 测试里房间的文档就用房间的 id 来认：fakeDocCollab 按它预置文档。
  getRoomDocument: async (topicId: string) => ({ id: topicId }),
}))
vi.mock('../../composables/useDocCollab', async () => ({
  useDocCollab: (await import('../../test/fakeDocCollab')).useFakeDocCollab,
}))

import PanelDoc from './PanelDoc.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const Doc = PanelDoc as unknown as Component

function room(id: string): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: null,
    title: `房间 ${id}`,
    kind: 'topic',
    status: 'active',
    created_by: 'u',
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-01T00:00:00Z',
  } as Topic
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  resetRooms()
  mocks.getDocNodes.mockReset()
  mocks.getDocNodes.mockResolvedValue({ data: [], total: 0 })
})

afterEach(cleanup)

function open(topic: Topic) {
  return render(Doc, {
    props: { topic, activityTick: 0, topicList: [] },
    global: { plugins: [vuetify] },
  })
}

function prose(container: Element): string {
  return container.querySelector('.doc-prose')?.textContent ?? ''
}

describe('打开一篇协同文档', () => {
  it('打不开时说原因，不摆一个空编辑器冒充空文档', async () => {
    seedRoom('t1', '', { error: '你不是这个房间的成员' })
    const { container } = open(room('t1'))

    await waitFor(() =>
      expect(container.querySelector('.doc-error-toast')?.textContent).toContain('不是这个房间的成员')
    )
    expect(container.querySelector('.doc-prose')).toBeNull()
  })

  it('协同服务换了文档格式时，不摆编辑器，点刷新重新载入页面', async () => {
    seedRoom('t1', '第一段', { outdated: true })
    const reload = vi.fn()
    vi.stubGlobal('location', { ...window.location, reload })
    const { container, findByRole } = open(room('t1'))

    ;(await findByRole('button', { name: '刷新' })).click()
    expect(reload).toHaveBeenCalled()
    expect(container.querySelector('.doc-prose')).toBeNull()
    vi.unstubAllGlobals()
  })

  it('别人写的字实时出现在编辑器里', async () => {
    seedRoom('t1', '第一段')
    const { container } = open(room('t1'))
    await waitFor(() => expect(prose(container)).toContain('第一段'))

    remoteEdit('t1', '第一段\n\n别人刚写的第二段')

    await waitFor(() => expect(prose(container)).toContain('别人刚写的第二段'))
  })

  it('在编辑器里打的字进了协同文档', async () => {
    seedRoom('t1', '第一段')
    const { container } = open(room('t1'))
    await waitFor(() => expect(prose(container)).toContain('第一段'))

    // While the editor is still registering its plugins (the block handles come
    // last), the sync plugin redraws the document and a DOM edit made in that
    // moment is drawn over; type again until it sticks, as a person would.
    await waitFor(() => {
      const paragraph = container.querySelector('.doc-prose p') as HTMLElement
      if (!paragraph.textContent?.includes('我改的')) paragraph.textContent = '第一段，我改的'
      expect(exportMarkdown(serverDoc('t1'))).toContain('我改的')
    })
  })

  it('换一个房间就换一篇文档，上一篇的字不留在编辑器里', async () => {
    seedRoom('a', '甲房间的文档')
    seedRoom('b', '乙房间的文档')
    const view = open(room('a'))
    await waitFor(() => expect(prose(view.container)).toContain('甲房间'))

    await view.rerender({ topic: room('b'), activityTick: 0, topicList: [] })

    await waitFor(() => expect(prose(view.container)).toContain('乙房间'))
    expect(prose(view.container)).not.toContain('甲房间')
  })
})
