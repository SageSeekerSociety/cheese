/**
 * A task's「文件」panel: a person edits the task's copy while its machine is
 * being brought back, and nothing they typed is lost on the way.
 *
 * The backend is a small fake that keeps the task's files and versions the
 * way the real one does: a save carries the version it was made against, a
 * save against a moved file is refused (409), and a save while the machine is
 * still being brought back is refused as `MachinePreparing`. The assertions
 * are on what the person sees and on what the fake ended up holding.
 */
import type { Component } from 'vue'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/http'
import { setLocale } from '@/i18n'

const server = {
  files: new Map<string, { content: string; version: string }>(),
  machine: null as 'ready' | 'preparing' | null,
  failed: null as 'unavailable' | null,
  prepareFails: false,
  prepares: 0,
}
let versions = 0
/** Every save the fake took, in order. */
const saves: string[] = []
function put(path: string, content: string) {
  server.files.set(path, { content, version: `v${++versions}` })
}

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listFiles: async () => ({
      data: [...server.files.keys()].map((path) => ({ path, bytes: server.files.get(path)!.content.length })),
      total: server.files.size,
      source: 'live',
      dirs: [],
    }),
    readFile: async (_p: string, path: string) => {
      const f = server.files.get(path)
      if (!f) throw new actual.ApiError(404, 'missing')
      return {
        path,
        content: f.content,
        version: f.version,
        bytes: f.content.length,
        binary: false,
        too_large: false,
        source: 'live',
        // Read off the forge while no machine holds the copy: it takes no save.
        editable: server.machine === 'ready',
      }
    },
    writeFile: async (_p: string, path: string, content: string, _t: unknown, version: string | null) => {
      if (server.machine !== 'ready') {
        server.machine = 'preparing'
        throw new actual.ApiError(503, '任务的环境正在准备，稍后再保存', 'MachinePreparing')
      }
      const f = server.files.get(path)!
      if (f.version !== version) throw new actual.ApiError(409, '文件已有更新，请重新读取后再保存', 'ConflictError')
      put(path, content)
      saves.push(path)
      return { path, version: server.files.get(path)!.version, merged: false }
    },
    prepareTaskMachine: async () => {
      server.prepares += 1
      if (server.prepareFails) throw new actual.ApiError(502, '保存失败：任务的环境未能准备好，稍后重试')
      server.machine = 'preparing'
      server.failed = null
      return { started: true, reason: null }
    },
    getTaskMachineStatus: async () => ({ machine: server.machine, failed: server.failed }),
    arrangeTaskFile: async () => ({ path: '' }),
  }
})
// Monaco does not run in a DOM test: a textarea stands in for the editor.
vi.mock('../CodeEditor.vue', () => ({
  default: {
    name: 'CodeEditorStub',
    props: ['modelValue', 'filename', 'readonly', 'lines', 'language', 'keep'],
    emits: ['update:modelValue', 'save', 'quick-open'],
    template:
      '<textarea data-testid="editor" :value="modelValue" :readonly="readonly" :data-lines="lines ? `${lines.start}-${lines.end}` : undefined" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
}))

import TaskFilesHost from '../work/TaskFilesHost.vue'

let vuetify: ReturnType<typeof createVuetify>
beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
  server.files.clear()
  server.machine = null
  server.failed = null
  server.prepareFails = false
  server.prepares = 0
  saves.length = 0
  put('src/queue.c', 'int main(void) { return 0; }\n')
  vi.useFakeTimers({ shouldAdvanceTime: true })
})
afterEach(() => {
  vi.useRealTimers()
})

async function settle(ms = 0) {
  await vi.advanceTimersByTimeAsync(ms)
  for (let i = 0; i < 6; i += 1) await Promise.resolve()
}

function mount(opts: { readOnly?: boolean } = {}) {
  const panel = ref<{ openFile: (path: string) => Promise<boolean> } | null>(null)
  const Host = defineComponent({
    setup: () => () =>
      h(TaskFilesHost as unknown as Component, {
        ref: panel,
        projectId: 'p1',
        taskId: 'task-1',
        active: true,
        readOnly: opts.readOnly ?? false,
      }),
  })
  const view = render(Host, { global: { plugins: [vuetify] } })
  return { ...view, open: (path: string) => panel.value!.openFile(path) }
}

async function type(text: string) {
  const editor = (await screen.findByTestId('editor')) as HTMLTextAreaElement
  await fireEvent.update(editor, text)
}

const MINE = 'int main(void) { return 1; }\n'

describe('a person edits a task file while its machine is being brought back', () => {
  it('keeps typing, and the text survives a reload before the machine is ready', async () => {
    const first = mount()
    await settle()
    await first.open('src/queue.c')
    await type(MINE)
    await settle()
    expect(screen.getByText('正在准备环境')).toBeTruthy()
    expect(server.prepares).toBe(1)
    expect(server.files.get('src/queue.c')!.content).not.toBe(MINE)

    // The tab is reloaded while the machine is still coming up.
    first.unmount()
    mount()
    await settle()
    await screen.findByRole('button', { name: '关闭 queue.c' })
    expect(((await screen.findByTestId('editor')) as HTMLTextAreaElement).value).toBe(MINE)
  })

  it('saves the held text once the machine is ready', async () => {
    const view = mount()
    await settle()
    await view.open('src/queue.c')
    await type(MINE)
    await settle()
    server.machine = 'ready'
    await settle(2500)
    expect(server.files.get('src/queue.c')!.content).toBe(MINE)
    expect(await screen.findByText('已保存')).toBeTruthy()
  })

  it('keeps the text after a failed bring-up and offers a retry', async () => {
    server.prepareFails = true
    const view = mount()
    await settle()
    await view.open('src/queue.c')
    await type(MINE)
    await settle()
    expect(screen.getByRole('alert').textContent).toContain('未能准备好')
    expect(((await screen.findByTestId('editor')) as HTMLTextAreaElement).value).toBe(MINE)

    server.prepareFails = false
    await fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await settle()
    server.machine = 'ready'
    await settle(2500)
    expect(server.files.get('src/queue.c')!.content).toBe(MINE)
  })

  it('does not overwrite a file that changed underneath', async () => {
    server.machine = 'ready'
    const view = mount()
    await settle()
    await view.open('src/queue.c')
    // The teammate saves the file after the person opened it.
    put('src/queue.c', 'int main(void) { return 2; }\n')
    await type(MINE)
    await settle(1000)
    expect(server.files.get('src/queue.c')!.content).toBe('int main(void) { return 2; }\n')
    expect(((await screen.findByTestId('editor')) as HTMLTextAreaElement).value).toBe(MINE)
    expect(screen.getByRole('button', { name: '载入最新版本' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '仍要保存' })).toBeTruthy()
  })

  it('shows the file read-only when the person may not edit it', async () => {
    server.machine = 'ready'
    const view = mount({ readOnly: true })
    await settle()
    await view.open('src/queue.c')
    expect(((await screen.findByTestId('editor')) as HTMLTextAreaElement).readOnly).toBe(true)
    expect(screen.getByText('只读')).toBeTruthy()
  })
})

describe('the same file open in two cells', () => {
  it('shows the same text in both and is saved once', async () => {
    server.machine = 'ready'
    const main = ref<{ openFile: (path: string) => Promise<boolean> } | null>(null)
    const props = { projectId: 'p1', taskId: 'task-1', active: true }
    const Two = defineComponent({
      setup: () => () =>
        h('div', [
          h('div', { class: 'cell-a' }, [h(TaskFilesHost as unknown as Component, { ...props, ref: main })]),
          h('div', { class: 'cell-b' }, [h(TaskFilesHost as unknown as Component, { ...props, path: 'src/queue.c' })]),
        ]),
    })
    const view = render(Two, { global: { plugins: [vuetify] } })
    await settle()
    await main.value!.openFile('src/queue.c')
    await settle()
    const editors = () => [...view.container.querySelectorAll<HTMLTextAreaElement>('[data-testid="editor"]')]
    expect(editors()).toHaveLength(2)

    await fireEvent.update(editors()[0], MINE)
    await settle()
    expect(editors()[1].value).toBe(MINE)
    await settle(1000)
    expect(server.files.get('src/queue.c')!.content).toBe(MINE)
    expect(saves).toEqual(['src/queue.c'])

    // Typing on in the other cell is made against what the first one saved.
    const more = MINE + '// more\n'
    await fireEvent.update(editors()[1], more)
    await settle(1000)
    expect(editors()[0].value).toBe(more)
    expect(server.files.get('src/queue.c')!.content).toBe(more)
    expect(saves).toEqual(['src/queue.c', 'src/queue.c'])
  })

  it('closing a cell with text not saved yet loses nothing', async () => {
    const view = mount()
    await settle()
    await view.open('src/queue.c')
    await type(MINE)
    await settle()
    view.unmount()
    server.machine = 'ready'
    mount()
    await settle(2500)
    expect(server.files.get('src/queue.c')!.content).toBe(MINE)
  })
})

describe('a file link opens in the panel when the task has the file', () => {
  it('reveals the lines the link points at', async () => {
    const panel = ref<{ openFile: (path: string, lines?: { start: number; end: number }) => Promise<boolean> } | null>(
      null
    )
    const Host = defineComponent({
      setup: () => () =>
        h(TaskFilesHost as unknown as Component, { ref: panel, projectId: 'p1', taskId: 'task-1', active: true }),
    })
    render(Host, { global: { plugins: [vuetify] } })
    await settle()
    await panel.value!.openFile('src/queue.c', { start: 1, end: 1 })
    expect(((await screen.findByTestId('editor')) as HTMLElement).dataset.lines).toBe('1-1')
  })

  it('opens a file of the task', async () => {
    const view = mount()
    await settle()
    expect(await view.open('src/queue.c')).toBe(true)
  })

  it('answers that a file the task does not have was not opened', async () => {
    const view = mount()
    await settle()
    expect(await view.open('docs/old.md')).toBe(false)
  })
})

describe('files open as tabs in the panel', () => {
  const STACK = 'void push(int x) {}\n'
  const editorText = async () => ((await screen.findByTestId('editor')) as HTMLTextAreaElement).value

  it('opening a second file keeps the first as a tab, and closing the second shows the first again', async () => {
    put('src/stack.c', STACK)
    const view = mount()
    await settle()
    await view.open('src/queue.c')
    await view.open('src/stack.c')
    await settle()
    expect(screen.getByRole('button', { name: '关闭 queue.c' })).toBeTruthy()
    expect(await editorText()).toBe(STACK)

    await fireEvent.click(screen.getByRole('button', { name: '关闭 stack.c' }))
    await settle()
    expect(screen.queryByRole('button', { name: '关闭 stack.c' })).toBeNull()
    expect(await editorText()).toBe('int main(void) { return 0; }\n')
  })

  it('closing the last tab leaves no file open', async () => {
    const view = mount()
    await settle()
    await view.open('src/queue.c')
    await fireEvent.click(await screen.findByRole('button', { name: '关闭 queue.c' }))
    await settle()
    expect(screen.queryByTestId('editor')).toBeNull()
    expect(screen.getByText('暂无打开的文件')).toBeTruthy()
  })

  it('closing a tab before its text is saved does not lose the text', async () => {
    server.machine = 'ready'
    const view = mount()
    await settle()
    await view.open('src/queue.c')
    await type(MINE)
    await fireEvent.click(screen.getByRole('button', { name: '关闭 queue.c' }))
    await settle(1000)
    expect(server.files.get('src/queue.c')!.content).toBe(MINE)
  })

  it('closing a tab while the machine is still coming up saves its text once the machine is ready', async () => {
    put('src/stack.c', STACK)
    const view = mount()
    await settle()
    await view.open('src/queue.c')
    await type(MINE)
    await settle()
    await view.open('src/stack.c')
    await fireEvent.click(screen.getByRole('button', { name: '关闭 queue.c' }))
    server.machine = 'ready'
    await settle(2500)
    expect(server.files.get('src/queue.c')!.content).toBe(MINE)
  })

  it('reopening the task brings back its tabs and the file that was shown', async () => {
    put('src/stack.c', STACK)
    const first = mount()
    await settle()
    await first.open('src/stack.c')
    await first.open('src/queue.c')
    await first.open('src/stack.c')
    await settle()
    first.unmount()

    mount()
    await settle()
    expect(await screen.findByRole('button', { name: '关闭 queue.c' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '关闭 stack.c' })).toBeTruthy()
    expect(await editorText()).toBe(STACK)
  })

  it('a remembered tab whose file is gone is dropped', async () => {
    put('src/stack.c', STACK)
    const first = mount()
    await settle()
    await first.open('src/queue.c')
    await first.open('src/stack.c')
    await settle()
    first.unmount()
    server.files.delete('src/stack.c')

    mount()
    await settle()
    expect(await editorText()).toBe('int main(void) { return 0; }\n')
    expect(screen.getByRole('button', { name: '关闭 queue.c' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '关闭 stack.c' })).toBeNull()
  })
})

// Keep the import used: the fake refuses with the same class the client raises.
void ApiError
