// 侧栏的话题列表变化时，文档那一格做了什么。
//
// 两件事各钉一条，都是改之前会红的：
//   1. 侧栏每 30 秒把整个话题数组换成一批新对象（字段一个都没变），文档那一格不该
//      因此再问一次服务端——原来的 `deep: true` 监听盯的是数组，于是每半分钟白拉
//      两条请求。
//   2. 支线改了标题（或跑完收了工），文档正文里那颗徽章得跟着改字。原来它永远不
//      更新：装饰的 key 只有 topicId，prosemirror-view 的 `WidgetType.eq` 一看
//      见 key 相等就短路，DOM 于是停在第一次渲染的样子。
//
// 「只重写变了的那一段」那条不在这里：它管的是位置（光标 / 装饰）而不是 DOM 重建，
// 测在 lib/docReplaceRange.spec.ts。
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
  title: '房间',
  kind: 'topic',
  status: 'active',
  created_by: 'u',
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
} as Topic

function sub(id: string, title: string, status: string): Topic {
  return { ...topic, id, parent_id: 't1', title, status } as Topic
}

function doc(content: string, version = 1): Block {
  return { id: 'd1', kind: 'doc', content, doc_version: version } as unknown as Block
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  getDoc.mockReset()
  getComments.mockReset()
  getDocNodes.mockReset()
  getComments.mockResolvedValue({ data: [], total: 0 })
  getDocNodes.mockResolvedValue({ data: [], total: 0 })
})

/** 找到正文里含这段字的那个顶层元素。 */
function para(container: HTMLElement, text: string): HTMLElement {
  const hit = Array.from(container.querySelectorAll('.doc-prose > *')).find((el) => el.textContent?.includes(text))
  if (!hit) throw new Error(`找不到含「${text}」的段落`)
  return hit as HTMLElement
}

describe('实况文档被服务端更新时', () => {
  it('内容一个字都没变时，编辑器不动', async () => {
    const same = '# 标题\n\n第一段\n'
    getDoc.mockResolvedValue(doc(same, 1))
    const { container, rerender } = render(Doc, {
      props: { topic, activityTick: 0, topicList: [] },
      global: { plugins: [vuetify] },
    })
    await waitFor(() => expect(container.textContent).toContain('第一段'))
    const before = para(container as HTMLElement, '第一段')

    getDoc.mockResolvedValue(doc(same, 2))
    await rerender({ topic, activityTick: 1, topicList: [] })
    await waitFor(() => expect(getDoc).toHaveBeenCalledTimes(2))

    expect(para(container as HTMLElement, '第一段')).toBe(before)
  })
})

describe('侧栏话题列表变化时', () => {
  it('列表换了新数组但内容没变，不再重拉文档节点和评论', async () => {
    getDoc.mockResolvedValue(doc('第一段\n', 1))
    const list = [sub('s1', '一件活', 'active')]
    const { container, rerender } = render(Doc, {
      props: { topic, activityTick: 0, topicList: list },
      global: { plugins: [vuetify] },
    })
    await waitFor(() => expect(container.textContent).toContain('第一段'))
    await waitFor(() => expect(getDocNodes).toHaveBeenCalled())
    const calls = getDocNodes.mock.calls.length

    // 侧栏每 30 秒把整个数组换成一批新对象，字段一个都没变。
    await rerender({ topic, activityTick: 0, topicList: [sub('s1', '一件活', 'active')] })
    await new Promise((r) => setTimeout(r, 0))

    expect(getDocNodes.mock.calls.length, '内容没变就不该再问一次服务端').toBe(calls)
  })

  it('支线换了标题就重新读，徽章跟着改字', async () => {
    getDoc.mockResolvedValue(doc('第一段\n', 1))
    getDocNodes.mockResolvedValue({
      data: [{ id: 'n1', kind: 'doc_node', content: '第一段', upgraded_to_topic_id: 's1' } as unknown as Block],
      total: 1,
    })
    const { container, rerender } = render(Doc, {
      props: { topic, activityTick: 0, topicList: [sub('s1', '旧名字', 'active')] },
      global: { plugins: [vuetify] },
    })
    await waitFor(() => expect(container.querySelector('.doc-liveref__label')?.textContent).toBe('旧名字'))

    await rerender({ topic, activityTick: 0, topicList: [sub('s1', '新名字', 'active')] })

    await waitFor(() => expect(container.querySelector('.doc-liveref__label')?.textContent).toBe('新名字'))
  })
})
