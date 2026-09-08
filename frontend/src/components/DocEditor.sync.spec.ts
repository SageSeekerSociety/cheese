import type { Editor } from '@tiptap/vue-3'

import { fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getDoc: vi.fn(),
  putDoc: vi.fn(),
  editor: null as Editor | null,
  socket: {} as { onConnected: () => void; onMessage: (ws: null, ev: { data: string }) => void },
}))
vi.mock('../api', () => ({
  getDoc: mocks.getDoc,
  putDoc: mocks.putDoc,
  chatWsUrl: (id: string) => `ws://test/${id}`,
  ApiError: class extends Error {},
}))
vi.mock('../me', () => ({ myHandle: () => 'editor' }))
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
vi.mock('@vueuse/core', async () => ({
  ...(await vi.importActual('@vueuse/core')),
  useWebSocket: (_url: unknown, options: typeof mocks.socket) => {
    mocks.socket = options
  },
}))
import DocEditor from './DocEditor.vue'

const wrappers: ReturnType<typeof render>[] = []
async function flushPromises() {
  for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0))
}
async function open() {
  const wrapper = render(DocEditor, {
    props: { topicId: 'root' },
    global: { stubs: { DragHandle: true, VIcon: true, VProgressCircular: true } },
  })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
function update() {
  mocks.socket.onMessage(null, { data: JSON.stringify({ type: 'state', resource: 'doc' }) })
}
beforeEach(() => {
  vi.clearAllMocks()
  mocks.getDoc.mockResolvedValue({ content: '原文', doc_version: 1 })
  mocks.putDoc.mockResolvedValue({ doc_version: 3 })
})
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount())
})

describe('charter collaboration', () => {
  it('refreshes after another member saves and uses the fresh version on the next save', async () => {
    const wrapper = await open()
    mocks.getDoc.mockResolvedValue({ content: '队友补充', doc_version: 2 })
    update()
    await flushPromises()
    expect(wrapper.container.querySelector('.ProseMirror')?.textContent).toBe('队友补充')
    mocks.editor!.commands.insertContent('我的补充')
    await fireEvent.keyDown(wrapper.container.querySelector('.ProseMirror')!, { key: 's', metaKey: true })
    await flushPromises()
    expect(mocks.putDoc.mock.calls[0][3]).toBe(2)
  })

  it('refreshes on reconnect but preserves edits typed while the fetch is in flight', async () => {
    const wrapper = await open()
    let resolve!: (value: unknown) => void
    mocks.getDoc.mockReturnValue(
      new Promise((r) => {
        resolve = r
      })
    )
    mocks.socket.onConnected()
    mocks.editor!.commands.insertContent('未保存草稿')
    resolve({ content: '外部修改', doc_version: 2 })
    await flushPromises()
    expect(wrapper.container.querySelector('.ProseMirror')?.textContent).toContain('未保存草稿')
    expect(wrapper.container.querySelector('.ProseMirror')?.textContent).not.toContain('外部修改')
  })
})
