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
  mocks.putDoc.mockImplementation((_topic: string, content: string, version: number) =>
    Promise.resolve(doc(content, version + 1))
  )
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
    const [topicId, content, version] = mocks.putDoc.mock.calls[0]
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

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok
    reject = fail
  })
  return { promise, resolve, reject }
}
function body(view: { container: Element }) {
  return view.container.querySelector('.doc-prose')?.textContent ?? ''
}
async function tick(view: Awaited<ReturnType<typeof mountDoc>>, value: number) {
  await view.rerender({ topic, activityTick: value, topicList: [] })
  await settle()
}
async function typeAndSave(view: Awaited<ReturnType<typeof mountDoc>>, text: string) {
  const calls = mocks.putDoc.mock.calls.length
  mocks.editor!.commands.insertContent(text)
  await fireEvent.keyDown(view.container.querySelector('.doc-prose')!, { key: 's', metaKey: true })
  await waitFor(() => expect(mocks.putDoc).toHaveBeenCalledTimes(calls + 1))
  await settle()
}

// Real PanelDoc -> DocSurface -> Editor; only HTTP responses are controlled.
// Positive schedules accompany the two counterexamples supplied at f135b3be.
describe('canonical snapshot timing', () => {
  it('ordered refresh installs latest text and saves against its version', async () => {
    const view = await mountDoc()
    mocks.getDoc.mockResolvedValue(doc('第二版\n', 2))
    await tick(view, 1)
    await waitFor(() => expect(body(view)).toBe('第二版'))
    mocks.getDoc.mockResolvedValue(doc('第三版\n', 3))
    await tick(view, 2)
    await waitFor(() => expect(body(view)).toBe('第三版'))
    await typeAndSave(view, '后续编辑')
    expect(mocks.putDoc.mock.calls[0][2]).toBe(3)
    expect(mocks.putDoc.mock.calls[0][1]).toContain('第三版')
  })

  it('settled save followed by notification refetches remote text', async () => {
    const view = await mountDoc()
    const put = deferred<Block>()
    mocks.putDoc.mockReturnValueOnce(put.promise)
    await typeAndSave(view, '本地编辑')
    const sent = mocks.putDoc.mock.calls[0][1] as string
    put.resolve(doc(sent, 2))
    await waitFor(() => expect(bar(view.container)).toContain('已保存'))
    mocks.getDoc.mockResolvedValue(doc(sent + '\n\n远端追加\n', 3))
    await tick(view, 1)
    await waitFor(() => expect(body(view)).toContain('远端追加'))
    await typeAndSave(view, '又一次编辑')
    expect(mocks.getDoc).toHaveBeenCalledTimes(2)
    expect(mocks.putDoc.mock.calls[1][2]).toBe(3)
  })

  it('late older GET cannot replace a newer displayed version or the next save base', async () => {
    const view = await mountDoc()
    const older = deferred<Block>()
    const newer = deferred<Block>()
    mocks.getDoc.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise)
    await tick(view, 1)
    await tick(view, 2)
    expect(mocks.getDoc).toHaveBeenCalledTimes(3)
    newer.resolve(doc('第三版\n', 3))
    await waitFor(() => expect(body(view)).toBe('第三版'))
    older.resolve(doc('第二版\n', 2))
    await settle()
    expect(body(view)).toBe('第三版')
    await typeAndSave(view, '后续编辑')
    expect(mocks.putDoc.mock.calls[0][2]).toBe(3)
    expect(mocks.putDoc.mock.calls[0][1]).toContain('第三版')
  })

  it('notification during PUT is reconciled when that save settles', async () => {
    const view = await mountDoc()
    const put = deferred<Block>()
    mocks.putDoc.mockReturnValueOnce(put.promise)
    await typeAndSave(view, '本地编辑')
    const sent = mocks.putDoc.mock.calls[0][1] as string
    mocks.getDoc.mockResolvedValue(doc(sent + '\n\n远端追加\n', 3))
    await tick(view, 1)
    put.resolve(doc(sent, 2))
    await waitFor(() => expect(body(view)).toContain('远端追加'))
    expect(mocks.getDoc).toHaveBeenCalledTimes(2)
    await typeAndSave(view, '又一次编辑')
    expect(mocks.putDoc.mock.calls[1][2]).toBe(3)
    expect(mocks.putDoc.mock.calls[1][1]).toContain('远端追加')
  })

  it('GET issued before PUT cannot replace the acknowledged base after PUT', async () => {
    const view = await mountDoc()
    const get = deferred<Block>()
    mocks.getDoc.mockReturnValueOnce(get.promise)
    await tick(view, 1)
    await typeAndSave(view, '我的写入')
    await waitFor(() => expect(bar(view.container)).toContain('已保存'))
    const sent = mocks.putDoc.mock.calls[0][1] as string
    mocks.getDoc.mockResolvedValue(doc(sent, 2))
    get.resolve(doc('第一段\n', 1))
    await settle()
    await typeAndSave(view, '下一次')
    expect(mocks.putDoc.mock.calls[1][2]).toBe(2)
    expect(mocks.putDoc.mock.calls[1][1]).toContain('我的写入')
  })

  it('canonical receipt becomes the next base instead of the submitted draft', async () => {
    const view = await mountDoc()
    mocks.putDoc.mockResolvedValueOnce(doc('服务端规范正文\n', 2))
    await typeAndSave(view, '提交稿')
    await waitFor(() => expect(body(view)).toBe('服务端规范正文'))
    await typeAndSave(view, '下一次')
    expect(mocks.putDoc.mock.calls[1][2]).toBe(2)
    expect(mocks.putDoc.mock.calls[1][1]).toContain('服务端规范正文')
    expect(mocks.putDoc.mock.calls[1][1]).not.toContain('提交稿')
  })

  it('typing during PUT stays dirty and survives the pending refresh as a conflict', async () => {
    const view = await mountDoc()
    const put = deferred<Block>()
    mocks.putDoc.mockReturnValueOnce(put.promise)
    await typeAndSave(view, '提交稿')
    const sent = mocks.putDoc.mock.calls[0][1] as string
    mocks.editor!.commands.insertContent('仍在输入')
    mocks.getDoc.mockResolvedValue(doc(sent + '\n\n远端追加\n', 3))
    await tick(view, 1)
    await tick(view, 2)
    put.resolve(doc(sent, 2))
    await waitFor(() => expect(view.container.querySelector('.doc-notice--conflict')).not.toBeNull())
    expect(body(view)).toContain('仍在输入')
    expect(body(view)).not.toContain('远端追加')
    expect(mocks.getDoc).toHaveBeenCalledTimes(2)
    expect(bar(view.container)).not.toContain('已保存')
  })

  it('source edits typed during PUT are not replaced by its receipt', async () => {
    const view = await mountDoc()
    await openSourceMode(view.container)
    const put = deferred<Block>()
    mocks.putDoc.mockReturnValueOnce(put.promise)
    await fireEvent.update(view.container.querySelector('.code-stub')!, '提交源码\n')
    await fireEvent.keyDown(view.container.querySelector('.doc-source')!, { key: 's', metaKey: true })
    await waitFor(() => expect(mocks.putDoc).toHaveBeenCalledTimes(1))
    await fireEvent.update(view.container.querySelector('.code-stub')!, '提交源码\n继续输入\n')
    put.resolve(doc('提交源码\n', 2))
    await settle()
    expect((view.container.querySelector('.code-stub') as HTMLTextAreaElement).value).toContain('继续输入')
    expect(bar(view.container)).toContain('编辑中')
    await fireEvent.keyDown(view.container.querySelector('.doc-source')!, { key: 's', metaKey: true })
    await waitFor(() => expect(mocks.putDoc).toHaveBeenCalledTimes(2))
    expect(mocks.putDoc.mock.calls[1][2]).toBe(2)
    expect(mocks.putDoc.mock.calls[1][1]).toContain('继续输入')
  })

  it.each([new Error('response lost'), new ApiError(409, 'conflict')])(
    'reconciles a pending notification on save failure without a retry loop: %s',
    async (failure) => {
      const view = await mountDoc()
      const put = deferred<Block>()
      mocks.putDoc.mockReturnValueOnce(put.promise)
      await typeAndSave(view, '保留的本地稿')
      mocks.getDoc.mockResolvedValue(doc('远端新版\n', 3))
      await tick(view, 1)
      put.reject(failure)
      await waitFor(() => expect(view.container.querySelector('.doc-notice--conflict')).not.toBeNull())
      expect(body(view)).toContain('保留的本地稿')
      expect(mocks.getDoc).toHaveBeenCalledTimes(2)
      expect(mocks.putDoc).toHaveBeenCalledTimes(1)
    }
  )

  it('a late PUT from a previous topic cannot set the new topic save base', async () => {
    const view = await mountDoc()
    const put = deferred<Block>()
    mocks.putDoc.mockReturnValueOnce(put.promise)
    await typeAndSave(view, '旧话题提交稿')
    const nextTopic = { ...topic, id: 't2', title: '新话题' }
    mocks.getDoc.mockResolvedValue(doc('新话题正文\n', 1))
    await view.rerender({ topic: nextTopic, activityTick: 0 })
    await waitFor(() => expect(body(view)).toContain('新话题正文'))
    put.resolve(doc('旧话题回执\n', 8))
    await settle()
    expect(body(view)).not.toContain('旧话题回执')
    mocks.editor!.commands.insertContent('新话题输入')
    await fireEvent.keyDown(view.container.querySelector('.doc-prose')!, { key: 's', metaKey: true })
    await waitFor(() => expect(mocks.putDoc).toHaveBeenCalledTimes(2))
    expect(mocks.putDoc.mock.calls[1][0]).toBe('t2')
    expect(mocks.putDoc.mock.calls[1][2]).toBe(1)
    expect(mocks.putDoc.mock.calls[1][1]).toContain('新话题正文')
  })

  it('local drafts stay with their topic when a mounted panel changes topics', async () => {
    const view = await mountDoc()
    mocks.editor!.commands.insertContent('只属旧话题的草稿')
    const nextTopic = { ...topic, id: 't2', title: '新话题' }
    mocks.getDoc.mockResolvedValue(doc('新话题正文\n', 1))
    await view.rerender({ topic: nextTopic, activityTick: 0 })
    await waitFor(() => expect(body(view)).toContain('新话题正文'))
    expect(view.container.querySelector('.doc-notice--stash')).toBeNull()
    mocks.getDoc.mockResolvedValue(doc('第一段\n', 1))
    await view.rerender({ topic, activityTick: 0 })
    await waitFor(() => expect(body(view)).toContain('第一段'))
    const restore = await screen.findByText('恢复我的改动', { selector: 'button' })
    await fireEvent.click(restore)
    await waitFor(() => expect(body(view)).toContain('只属旧话题的草稿'))
    expect(body(view)).not.toContain('新话题正文')
  })

  it('a later GET carrying a lower version cannot downgrade an accepted snapshot', async () => {
    const view = await mountDoc()
    mocks.getDoc.mockResolvedValueOnce(doc('第三版\n', 3))
    await tick(view, 1)
    await waitFor(() => expect(body(view)).toBe('第三版'))
    mocks.getDoc.mockResolvedValueOnce(doc('旧缓存\n', 2))
    await tick(view, 2)
    expect(body(view)).toBe('第三版')
    await typeAndSave(view, '新输入')
    expect(mocks.putDoc.mock.calls[0][2]).toBe(3)
  })

  it('an unmounted panel ignores the pending PUT and does not refresh', async () => {
    const view = await mountDoc()
    const put = deferred<Block>()
    mocks.putDoc.mockReturnValueOnce(put.promise)
    await typeAndSave(view, '提交稿')
    await tick(view, 1)
    view.unmount()
    put.resolve(doc('迟到回执\n', 2))
    await settle()
    expect(mocks.getDoc).toHaveBeenCalledTimes(1)
  })
})
