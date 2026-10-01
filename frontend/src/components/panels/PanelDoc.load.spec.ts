// 文档那一格的读：文档、评论、节点分别什么时候来，来了画成什么，读不到时说什么。
//
// 和 PanelDoc.save.spec.ts 一样，是「把取数搬进组合式函数」那一步（#2143）的安全
// 网。这一格一次读三样东西（正文 / 评论 / 节点），其中后两样还要喂给编辑器里的
// 两种装饰：评论下划线、支线徽章。拆的时候接错了不会崩 —— 只是下划线不再出现，
// 或者服务端的新版本被本地草稿无声地顶掉。
import type { Editor as CoreEditor } from '@tiptap/core'
import type { Component } from 'vue'
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getDoc: vi.fn(),
  getComments: vi.fn(),
  getDocNodes: vi.fn(),
  editor: null as CoreEditor | null,
}))

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getDoc: (...a: unknown[]) => mocks.getDoc(...a),
    getComments: (...a: unknown[]) => mocks.getComments(...a),
    getDocNodes: (...a: unknown[]) => mocks.getDocNodes(...a),
  }
})

vi.mock('@tiptap/vue-3', async () => {
  const actual = await vi.importActual<typeof import('@tiptap/vue-3')>('@tiptap/vue-3')
  return {
    ...actual,
    useEditor: (...args: Parameters<typeof actual.useEditor>) => {
      const result = actual.useEditor(...args)
      setTimeout(() => {
        mocks.editor = result.value ?? null
      }, 0)
      return result
    },
  }
})

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

function doc(content: string, version = 1): Block {
  return { id: 'd1', kind: 'doc', content, doc_version: version } as unknown as Block
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  mocks.editor = null
  mocks.getDoc.mockReset()
  mocks.getComments.mockReset()
  mocks.getDocNodes.mockReset()
  mocks.getComments.mockResolvedValue({ data: [], total: 0 })
  mocks.getDocNodes.mockResolvedValue({ data: [], total: 0 })
})

afterEach(cleanup)

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((ok) => {
    resolve = ok
  })
  return { promise, resolve }
}
async function settle() {
  for (let i = 0; i < 12; i++) await Promise.resolve()
}

function open(props: Record<string, unknown> = {}) {
  return render(Doc, {
    props: { topic, activityTick: 0, topicList: [], ...props },
    global: { plugins: [vuetify] },
  })
}

describe('文档读进来', () => {
  it('文档读不到时，错误条上是服务端的原话，评论也就不再去问', async () => {
    mocks.getDoc.mockRejectedValue(new Error('502 坏网关'))
    const { container } = open()

    await waitFor(() => expect(container.querySelector('.doc-error-toast')?.textContent).toContain('502 坏网关'))
    expect(container.querySelector('.doc-skel'), '读完了就不该还摆着骨架').toBeNull()
    // 评论跟在正文后面读；正文都没读到，那两条请求没有理由发出去。
    expect(mocks.getComments).not.toHaveBeenCalled()
    expect(mocks.getDocNodes).not.toHaveBeenCalled()
  })

  it('锚在某一句话上的评论，在正文里画一条下划线', async () => {
    mocks.getDoc.mockResolvedValue(doc('第一段\n', 1))
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
    const { container } = open()

    await waitFor(() => expect(container.querySelector('.comment-anchor')).not.toBeNull())
    const anchor = container.querySelector('.comment-anchor') as HTMLElement
    expect(anchor.textContent, '下划线要正好压在被引用的那几个字上').toBe('第一段')
    expect(anchor.dataset.comment).toBe('c1')
  })

  it('AI 动过一次、服务端内容也变了，而本地没改：编辑器换成新的那一版', async () => {
    mocks.getDoc.mockResolvedValue(doc('第一段\n', 1))
    const view = open()
    await waitFor(() => expect(view.container.querySelector('.doc-prose')?.textContent).toContain('第一段'))

    mocks.getDoc.mockResolvedValue(doc('第二段\n', 2))
    await view.rerender({ topic, activityTick: 1, topicList: [] })

    await waitFor(() => expect(view.container.querySelector('.doc-prose')?.textContent).toContain('第二段'))
    expect(view.container.querySelector('.doc-prose')?.textContent, '旧的正文该退场').not.toContain('第一段')
    expect(view.container.querySelector('.doc-notice--conflict'), '没人有未保存的改动，就没有冲突可言').toBeNull()
  })

  it('服务端和本地都改了：两个版本都留着，等人来选', async () => {
    mocks.getDoc.mockResolvedValue(doc('第一段\n', 1))
    const view = open()
    await waitFor(() => expect(mocks.editor).not.toBeNull())

    mocks.editor!.commands.insertContent('我的话')
    mocks.getDoc.mockResolvedValue(doc('芝士换掉的一段\n', 2))
    await view.rerender({ topic, activityTick: 1, topicList: [] })

    await waitFor(() => expect(view.container.querySelector('.doc-notice--conflict')).not.toBeNull())
    expect(view.container.querySelector('.doc-prose')?.textContent, '没选之前轮不到服务端那一版上屏').toContain(
      '我的话'
    )
    expect(view.container.querySelector('.doc-prose')?.textContent).not.toContain('芝士换掉的一段')
  })

  it('a slow initial GET cannot replace a newer activity snapshot', async () => {
    const initial = deferred<Block>()
    mocks.getDoc.mockReturnValueOnce(initial.promise).mockResolvedValue(doc('新版\n', 3))
    const view = open()
    await view.rerender({ topic, activityTick: 1 })
    await waitFor(() => expect(view.container.querySelector('.doc-prose')?.textContent).toContain('新版'))
    initial.resolve(doc('旧版\n', 1))
    await settle()
    expect(view.container.querySelector('.doc-prose')?.textContent).toContain('新版')
    expect(view.container.querySelector('.doc-prose')?.textContent).not.toContain('旧版')
  })

  it('local input made while GET is pending remains beside the incoming conflict', async () => {
    mocks.getDoc.mockResolvedValue(doc('第一段\n', 1))
    const view = open()
    await waitFor(() => expect(mocks.editor).not.toBeNull())
    const refresh = deferred<Block>()
    mocks.getDoc.mockReturnValueOnce(refresh.promise)
    await view.rerender({ topic, activityTick: 1 })
    mocks.editor!.commands.insertContent('请求中输入')
    refresh.resolve(doc('远端新版\n', 2))
    await waitFor(() => expect(view.container.querySelector('.doc-notice--conflict')).not.toBeNull())
    expect(view.container.querySelector('.doc-prose')?.textContent).toContain('请求中输入')
  })

  it('topic identity fences pending content and comments without requiring unmount', async () => {
    const initial = deferred<Block>()
    mocks.getDoc.mockReturnValueOnce(initial.promise).mockResolvedValue(doc('新话题正文\n', 1))
    const view = open()
    const nextTopic = { ...topic, id: 't2', title: '新话题' }
    await view.rerender({ topic: nextTopic, activityTick: 0 })
    await waitFor(() => expect(view.container.querySelector('.doc-prose')?.textContent).toContain('新话题正文'))
    initial.resolve(doc('旧话题正文\n', 9))
    await settle()
    expect(view.container.querySelector('.doc-prose')?.textContent).not.toContain('旧话题正文')
    expect(mocks.getComments.mock.calls.every(([id]) => id === 't2')).toBe(true)
  })

  it('unmounting before the initial GET settles prevents follow-up requests', async () => {
    const initial = deferred<Block>()
    mocks.getDoc.mockReturnValueOnce(initial.promise)
    const view = open()
    view.unmount()
    initial.resolve(doc('迟到正文\n', 1))
    await settle()
    expect(mocks.getComments).not.toHaveBeenCalled()
    expect(mocks.getDocNodes).not.toHaveBeenCalled()
  })
})
