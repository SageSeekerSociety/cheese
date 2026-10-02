// 文档那一格打开一篇协同文档：正文从协同文档来，别人改的实时出现，评论和节点照旧
// 从接口读，打不开时说原因。
//
// 协同服务由 src/test/fakeDocCollab.ts 代替：每个房间一份「服务端」文档，面板打开
// 时拿到一份和它保持同步的客户端文档 —— 没有 socket，其余和真的一样。
import type { Component } from 'vue'
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { exportMarkdown } from '../../lib/docSchema'
import { remoteEdit, resetRooms, seedRoom, serverDoc } from '../../test/fakeDocCollab'

const mocks = vi.hoisted(() => ({
  getComments: vi.fn(),
  getDocNodes: vi.fn(),
}))

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getComments: (...a: unknown[]) => mocks.getComments(...a),
    getDocNodes: (...a: unknown[]) => mocks.getDocNodes(...a),
  }
})
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
  mocks.getComments.mockReset()
  mocks.getDocNodes.mockReset()
  mocks.getComments.mockResolvedValue({ data: [], total: 0 })
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

    const paragraph = container.querySelector('.doc-prose p') as HTMLElement
    paragraph.textContent = '第一段，我改的'
    await waitFor(() => expect(exportMarkdown(serverDoc('t1'))).toContain('我改的'))
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

  it('锚在某一句话上的评论，在正文里画一条下划线', async () => {
    seedRoom('t1', '第一段')
    mocks.getDocNodes.mockResolvedValue({
      data: [{ id: 'n1', kind: 'doc_node', content: '第一段' } as unknown as Block],
      total: 1,
    })
    mocks.getComments.mockResolvedValue({
      data: [
        {
          id: 'c1',
          kind: 'comment',
          reply_to: 'n1',
          anchor_quote: '第一段',
          content: '这里再说一句',
        } as unknown as Block,
      ],
      total: 1,
    })
    const { container } = open(room('t1'))

    await waitFor(() => expect(container.querySelector('.comment-anchor')).not.toBeNull())
    const anchor = container.querySelector('.comment-anchor') as HTMLElement
    expect(anchor.textContent, '下划线要正好压在被引用的那几个字上').toBe('第一段')
    expect(anchor.dataset.comment).toBe('c1')
  })
})
