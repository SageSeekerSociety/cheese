// 文档那一格的写：# 什么时候算脏、多久落一次盘、写不进去的时候说什么。
//
// 这一份是「把取数搬进组合式函数」那一步（#2143）的安全网。保存这条路上有防抖、有
// 版本号、有 409 冲突条、有源码模式的另一条落盘路径 —— 拆的时候接错了不会崩，只会
// 安静地少写一次、多写一次，或者把本地那一版冲掉。所以先在这里把「现在是什么样」
// 钉死，改完必须一模一样。
import type { Editor as CoreEditor } from '@tiptap/core'
import type { Component } from 'vue'
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getDoc: vi.fn(),
  putDoc: vi.fn(),
  getComments: vi.fn(),
  getDocNodes: vi.fn(),
  editor: null as CoreEditor | null,
}))

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getDoc: (...a: unknown[]) => mocks.getDoc(...a),
    putDoc: (...a: unknown[]) => mocks.putDoc(...a),
    getComments: (...a: unknown[]) => mocks.getComments(...a),
    getDocNodes: (...a: unknown[]) => mocks.getDocNodes(...a),
  }
})

// 面板里只有一个编辑器，所以把它抓下来就等于拿到了「人在编辑器里做了什么」那一
// 端。同一招见 DocEditor.sync.spec.ts。
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

import { ApiError } from '../../api'

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

// 源码模式里的 Monaco 在 jsdom 里立不起来，站着的是一个会发 update:modelValue 的
// 替身 —— 源码模式这条路上我们只关心「写回的是不是那段原文」。
const CodeEditorStub = {
  name: 'CodeEditor',
  props: ['modelValue', 'filename', 'readonly'],
  emits: ['update:modelValue', 'save'],
  template:
    '<textarea class="code-stub" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
}

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  // 桌面宽度：源码模式只在桌面上提供。
  Object.defineProperty(window, 'innerWidth', { value: 1280, writable: true, configurable: true })
  // happy-dom 没有这两样，而 ⋯ 菜单打开时 Vuetify 要读它们来摆位置。
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
  mocks.editor = null
  mocks.getDoc.mockReset()
  mocks.putDoc.mockReset()
  mocks.getComments.mockResolvedValue({ data: [], total: 0 })
  mocks.getDocNodes.mockResolvedValue({ data: [], total: 0 })
  mocks.getDoc.mockResolvedValue(doc('第一段\n', 1))
  mocks.putDoc.mockResolvedValue(doc('第一段\n', 2))
  vuetify = createVuetify({ components, directives })
})

afterEach(cleanup)

async function mountDoc() {
  const view = render(Doc, {
    props: { topic, activityTick: 0, topicList: [] },
    global: { plugins: [vuetify], stubs: { CodeEditor: CodeEditorStub } },
  })
  await waitFor(() => expect(view.container.textContent).toContain('第一段'))
  await waitFor(() => expect(mocks.editor).not.toBeNull())
  return view
}

/** 打完字之后的反应都是微任务级的：把队列趟干净，而不是等时钟。 */
async function settle() {
  for (let i = 0; i < 8; i++) await Promise.resolve()
}

function bar(container: Element): string {
  return (container.querySelector('.doc-bar') as HTMLElement).textContent ?? ''
}

async function openSourceMode(container: Element) {
  await fireEvent.click(container.querySelector('.doc-bar [aria-label="更多"]')!)
  await fireEvent.click(await screen.findByText('源码模式', { selector: '.v-list-item-title' }))
  await waitFor(() => expect(container.querySelector('.code-stub')).not.toBeNull())
}

describe('写完一篇文档', () => {
  it('打完字先写「编辑中…」，2.5 秒后自己落盘一次，横条改说「已保存」', async () => {
    const { container } = await mountDoc()

    vi.useFakeTimers()
    mocks.editor!.commands.insertContent('新写的一段')
    await settle()
    expect(bar(container), '改了东西横条要写在编辑中').toContain('编辑中…')

    await vi.advanceTimersByTimeAsync(2500)
    vi.useRealTimers()

    await waitFor(() => expect(mocks.putDoc).toHaveBeenCalledTimes(1))
    const [topicId, content, , version] = mocks.putDoc.mock.calls[0]
    expect(topicId, '存回的是这个房间').toBe('t1')
    expect(content, '存的是编辑器里那一版').toContain('新写的一段')
    expect(version, '带上读到的那一版，服务端才拦得住覆盖').toBe(1)
    await waitFor(() => expect(bar(container)).toContain('已保存'))
  })

  it('⌘S 不等那 2.5 秒', async () => {
    const { container } = await mountDoc()

    mocks.editor!.commands.insertContent('急着存的一段')
    await fireEvent.keyDown(container.querySelector('.doc-prose')!, { key: 's', metaKey: true })

    await waitFor(() => expect(mocks.putDoc).toHaveBeenCalledTimes(1))
    expect(mocks.putDoc.mock.calls[0][1]).toContain('急着存的一段')
  })

  it('保存失败时说的是服务端那句话，改动留在编辑器里', async () => {
    mocks.putDoc.mockRejectedValue(new Error('磁盘满了'))
    const { container } = await mountDoc()

    mocks.editor!.commands.insertContent('没存上的一段')
    await fireEvent.keyDown(container.querySelector('.doc-prose')!, { key: 's', metaKey: true })

    await waitFor(() => expect(container.querySelector('.doc-error-toast')?.textContent).toContain('磁盘满了'))
    expect(container.querySelector('.doc-prose')?.textContent, '没存上也不能把人的字丢掉').toContain('没存上的一段')
    expect(bar(container), '没存上就不该说已保存').toContain('编辑中…')
  })

  it('服务端说 409 时摆出冲突条：两个出口都在，本地那一版没被冲掉', async () => {
    mocks.putDoc.mockRejectedValue(new ApiError(409, 'conflict'))
    const { container } = await mountDoc()

    mocks.editor!.commands.insertContent('我的改动')
    await fireEvent.keyDown(container.querySelector('.doc-prose')!, { key: 's', metaKey: true })

    const conflict = await waitFor(() => {
      const el = container.querySelector('.doc-notice--conflict')
      expect(el, '409 要摆出冲突条，而不是只说一句保存失败').not.toBeNull()
      return el as HTMLElement
    })
    expect(mocks.getDoc.mock.calls.length, '为了拿出服务端那一版再读一次').toBeGreaterThanOrEqual(2)
    const labels = Array.from(conflict.querySelectorAll('button')).map((b) => b.textContent?.trim())
    expect(labels).toContain('查看芝士的版本')
    expect(labels).toContain('保留我的版本')
    expect(container.querySelector('.doc-prose')?.textContent).toContain('我的改动')
  })

  it('源码模式里改的是原文，落盘写回的就是那段原文', async () => {
    const { container } = await mountDoc()

    await openSourceMode(container)
    await fireEvent.update(container.querySelector('.code-stub')!, '# 房间\n\n源码里改的一段\n')
    expect(bar(container)).toContain('编辑中…')

    await fireEvent.keyDown(container.querySelector('.doc-source')!, { key: 's', metaKey: true })

    await waitFor(() => expect(mocks.putDoc).toHaveBeenCalledTimes(1))
    expect(mocks.putDoc.mock.calls[0][1]).toContain('源码里改的一段')
  })
})
