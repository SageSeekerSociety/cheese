/**
 * The task page's surfaces — the 文件 panel, the 搜索 column, a 终端 tab — meet the task's
 * environment as one thing: one bring-up serves them all, what one of them learns the others show,
 * and each says which copy it shows.
 *
 * The page is mounted as the work panel wires it (one files bundle and one search for the page),
 * against a small fake backend that keeps the environment's state as the real one does: looking
 * reads the committed version off the forge while no environment holds the copy, and a save, a
 * search or a terminal brings one up. The assertions are on what each surface shows and on what
 * the fake ended up holding.
 */
import type { Component } from 'vue'
import type { TaskFilesBundle } from '../../composables/useTaskFiles'

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

const server = {
  content: 'int main(void) { return 0; }\n',
  version: 'v1',
  machine: null as 'ready' | 'preparing' | null,
  /** The person may edit the task's files, so a search brings the environment up for them. */
  mayEdit: true,
  /** Bring-ups started, whoever asked. */
  bringUps: 0,
  /** `POST machine/prepare` requests. */
  prepares: 0,
  searches: 0,
}

function bringUp() {
  if (server.machine === null) {
    server.bringUps += 1
    server.machine = 'preparing'
  }
}

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  const preparing = () => new actual.ApiError(503, '任务的环境正在准备，准备好后再试', 'MachinePreparing')
  return {
    ...actual,
    listFiles: async () => ({
      data: [{ path: 'src/queue.c', bytes: server.content.length }],
      total: 1,
      source: server.machine === 'ready' ? 'live' : 'committed',
      dirs: [],
    }),
    readFile: async (_p: string, path: string) => ({
      path,
      content: server.content,
      version: server.version,
      bytes: server.content.length,
      binary: false,
      too_large: false,
      // Off the forge while no environment holds the copy: the committed version, taking no save.
      source: server.machine === 'ready' ? 'live' : 'committed',
      editable: server.machine === 'ready',
    }),
    writeFile: async (_p: string, _path: string, content: string) => {
      if (server.machine !== 'ready') {
        bringUp()
        throw preparing()
      }
      server.content = content
      server.version = `v${Number(server.version.slice(1)) + 1}`
      return { path: 'src/queue.c', version: server.version, merged: false }
    },
    searchTaskFiles: async (_p: string, _t: string, query: string) => {
      server.searches += 1
      if (server.machine !== 'ready') {
        if (!server.mayEdit) {
          throw new actual.ApiError(404, '任务的环境运行时才能搜索文件', 'SearchNeedsEnvironment')
        }
        bringUp()
        throw preparing()
      }
      const at = server.content.indexOf(query)
      return {
        files:
          at < 0
            ? []
            : [
                {
                  path: 'src/queue.c',
                  matches: [
                    {
                      line: 1,
                      text: server.content.trim(),
                      columns: [[at, at + query.length]],
                      cut_before: false,
                      cut_after: false,
                    },
                  ],
                },
              ],
        truncated: false,
      }
    },
    prepareTaskMachine: async () => {
      server.prepares += 1
      bringUp()
      return { started: true, reason: null }
    },
    getTaskMachineStatus: async () => ({ machine: server.machine, failed: null }),
    taskTerminalWsUrl: (task: string, id: string) => `ws://test/${task}/${id}`,
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
      '<textarea data-testid="editor" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
}))
// xterm draws on a canvas a DOM test does not have.
vi.mock('../panels/PanelTerminal.vue', () => ({
  default: defineComponent({
    name: 'PanelTerminalStub',
    props: {
      placeholder: { type: String, default: null },
      covered: Boolean,
      note: { type: String, default: null },
      active: Boolean,
    },
    setup(props, { slots, expose }) {
      expose({
        write: (_b: Uint8Array, done: () => void) => done(),
        reset: () => {},
        size: () => ({ cols: 80, rows: 24 }),
      })
      return () => h('div', props.covered ? slots.cover?.() : props.placeholder)
    },
  }),
}))

import { useTaskFiles } from '../../composables/useTaskFiles'
import { useTaskFileTree } from '../../composables/useTaskFileTree'
import { useTaskSearch } from '../../composables/useTaskSearch'
import TaskNavigator from '../panels/TaskNavigator.vue'

import TaskFilesHost from './TaskFilesHost.vue'
import TerminalHost from './TerminalHost.vue'

class FakeSocket {
  static all: FakeSocket[] = []
  static OPEN = 1
  readyState = 1
  binaryType = 'blob'
  onmessage: ((event: { data: unknown }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  constructor(public url: string) {
    FakeSocket.all.push(this)
  }
  send() {}
  close() {
    this.readyState = 3
  }
}

let vuetify: ReturnType<typeof createVuetify>
let files: TaskFilesBundle
beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})
beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
  Object.assign(server, {
    content: 'int main(void) { return 0; }\n',
    version: 'v1',
    machine: null,
    mayEdit: true,
    bringUps: 0,
    prepares: 0,
    searches: 0,
  })
  FakeSocket.all = []
  vi.stubGlobal('WebSocket', FakeSocket)
  vi.useFakeTimers({ shouldAdvanceTime: true })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

async function settle(ms = 0) {
  await vi.advanceTimersByTimeAsync(ms)
  for (let i = 0; i < 8; i += 1) await Promise.resolve()
}

/** The task page's surfaces, wired as the work panel wires them. */
function mountPage(opts: { terminal?: boolean } = {}) {
  const Page = defineComponent({
    setup() {
      files = useTaskFiles({
        projectId: () => 'p1',
        taskId: () => 'task-1',
        readOnly: () => false,
        active: () => true,
        refreshTick: () => 0,
      })
      const tree = useTaskFileTree(files)
      const search = useTaskSearch({ projectId: () => 'p1', taskId: () => 'task-1' })
      return () =>
        h('div', [
          h('section', { 'data-surface': 'search' }, [
            h(TaskNavigator as unknown as Component, { view: 'search', files, tree, search }),
          ]),
          h('section', { 'data-surface': 'files' }, [
            h(TaskFilesHost as unknown as Component, { projectId: 'p1', taskId: 'task-1', active: true, files }),
          ]),
          opts.terminal
            ? h('section', { 'data-surface': 'terminal' }, [
                h(TerminalHost as unknown as Component, { taskId: 'task-1', terminalId: 't1', active: true }),
              ])
            : null,
        ])
    },
  })
  const view = render(Page, { global: { plugins: [vuetify] } })
  const surface = (name: string) => within(view.container.querySelector<HTMLElement>(`[data-surface="${name}"]`)!)
  return { ...view, surface }
}

async function typeSearch(text: string) {
  await fireEvent.update(screen.getByRole('searchbox'), text)
  await settle(400)
}

describe('one environment for the whole task page', () => {
  it('the 文件 panel and a terminal show one bring-up, asked for once', async () => {
    const page = mountPage({ terminal: true })
    await settle()
    await files.open('src/queue.c')
    await settle()
    expect(page.surface('terminal').getByText('正在准备环境')).toBeTruthy()
    expect(page.surface('files').getByText('正在准备环境')).toBeTruthy()

    // Typing in the file while the terminal's bring-up is under way asks for nothing more.
    await fireEvent.update(page.surface('files').getByTestId('editor'), 'int main(void) { return 1; }\n')
    await settle()
    expect(server.prepares).toBe(1)
    expect(server.bringUps).toBe(1)

    server.machine = 'ready'
    await settle(2500)
    await waitFor(() => expect(FakeSocket.all).toHaveLength(1))
    await settle(1000)
    expect(server.content).toBe('int main(void) { return 1; }\n')
    expect(page.surface('files').queryByText('正在准备环境')).toBeNull()
  })

  it('a surface that believed the environment ready shows it being prepared once another learns it is gone', async () => {
    server.machine = 'ready'
    const page = mountPage()
    await settle()
    await files.open('src/queue.c')
    await settle()
    expect(page.surface('files').queryByText('正在准备环境')).toBeNull()

    // The environment is released while the page is open; the search is the first to hear of it.
    server.machine = null
    await typeSearch('main')
    expect(page.surface('files').getByText('正在准备环境')).toBeTruthy()
    expect(page.surface('search').getByText('正在准备环境')).toBeTruthy()
    expect(server.bringUps).toBe(1)
  })

  it('a search typed while no environment holds the copy says it is being prepared, then shows the matches', async () => {
    const page = mountPage()
    await settle()
    await typeSearch('main')
    expect(page.surface('search').getByText('正在准备环境')).toBeTruthy()

    server.machine = 'ready'
    await settle(2500)
    // Searched again by itself: the person typed it once.
    expect(await page.surface('search').findByText('queue.c')).toBeTruthy()
    expect(page.surface('search').queryByText('正在准备环境')).toBeNull()
    expect(server.searches).toBe(2)
  })

  it('a reader who may not start the environment is told the search needs it, with nothing to retry', async () => {
    server.mayEdit = false
    const page = mountPage()
    await settle()
    await typeSearch('main')
    expect(page.surface('search').getByText('任务的环境运行时才能搜索文件')).toBeTruthy()
    expect(page.surface('search').queryByRole('button', { name: '重试' })).toBeNull()
    expect(server.bringUps).toBe(0)
  })
})

describe('the 文件 panel says which copy it shows', () => {
  it('marks a file read off the forge as the committed version, and drops the mark once the live copy loads', async () => {
    const page = mountPage({ terminal: true })
    await settle()
    await files.open('src/queue.c')
    await settle()
    expect(page.surface('files').getByText('已提交版本')).toBeTruthy()

    // The terminal brought the environment up: the file is read again from it.
    server.content = 'int main(void) { return 2; }\n'
    server.machine = 'ready'
    await settle(2500)
    await waitFor(() => expect(page.surface('files').queryByText('已提交版本')).toBeNull())
    expect((page.surface('files').getByTestId('editor') as HTMLTextAreaElement).value).toBe(
      'int main(void) { return 2; }\n'
    )
  })

  it('an edit to the committed version is saved into the environment, and the mark goes', async () => {
    const page = mountPage()
    await settle()
    await files.open('src/queue.c')
    await settle()
    expect(page.surface('files').getByText('已提交版本')).toBeTruthy()

    await fireEvent.update(page.surface('files').getByTestId('editor'), 'int main(void) { return 3; }\n')
    await settle()
    server.machine = 'ready'
    await settle(2500)
    expect(server.content).toBe('int main(void) { return 3; }\n')
    expect(page.surface('files').queryByText('已提交版本')).toBeNull()
  })
})
