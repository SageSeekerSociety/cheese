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
  failed: null as 'no_session' | 'unavailable' | null,
  prepareFails: false,
  prepares: 0,
}
let versions = 0
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
    props: ['modelValue', 'filename', 'readonly'],
    emits: ['update:modelValue', 'save', 'quick-open'],
    template:
      '<textarea data-testid="editor" :value="modelValue" :readonly="readonly" @input="$emit(\'update:modelValue\', $event.target.value)" />',
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
    await screen.findByText('src/queue.c', { selector: '.task-files__path' })
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

describe('a file link opens in the panel when the task has the file', () => {
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

// Keep the import used: the fake refuses with the same class the client raises.
void ApiError
