/**
 * 任务页的两种摆法（对话、编辑器）是同一件任务：在一种摆法里打开的文件，换到另一种还是那一份，
 * 打的字也还在；编辑器里左栏「改动」点一份，中间翻到它的改动。
 *
 * 布局区是一个最小的替身：它只记着每一格此刻翻到前面的是哪一块，面板照它决定显示哪一块。
 * 后端是一个只认这件任务几份文件的假后端。断言的是读者看到的东西。
 */
import type { Topic } from '../../cx_types'
import type { WorkLayout } from '../work/workLayout'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

const FILES: Record<string, string> = {
  'src/queue.c': 'int queue(void) { return 0; }\n',
  'src/stack.c': 'int pop(void) { return 1; }\n',
}

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listFiles: vi.fn(async () => ({
      data: Object.keys(FILES).map((path) => ({ path, bytes: FILES[path].length })),
      total: Object.keys(FILES).length,
      source: 'live',
      dirs: ['src'],
    })),
    readFile: vi.fn(async (_p: string, path: string) => ({
      path,
      content: FILES[path] ?? '',
      version: 'v1',
      bytes: (FILES[path] ?? '').length,
      binary: false,
      too_large: false,
      source: 'live',
      editable: true,
    })),
    writeFile: vi.fn(async (_p: string, path: string) => ({ path, version: 'v2', merged: false })),
    getTaskMachineStatus: vi.fn(async () => ({ machine: 'ready', failed: null })),
    prepareTaskMachine: vi.fn(async () => ({ started: true, reason: null })),
    getTopicWorkSummary: vi.fn(async () => ({ changed_files: ['src/stack.c'], has_run: true })),
    listRoomTasks: vi.fn(async () => ({
      data: [{ id: 'task-1', status: 'open', branch_name: 'task/1', card: null, presentation: { phrase: 'working' } }],
      total: 1,
    })),
    getForgeConnection: vi.fn(async () => ({ connected: true })),
    getGitDiff: vi.fn(async () => ({
      diff: 'diff --git a/src/stack.c b/src/stack.c\n--- a/src/stack.c\n+++ b/src/stack.c\n@@ -1 +1,2 @@\n-int pop(void) { return 0; }\n+int pop(void) { return 1; }\n+// checked\n',
    })),
    listTaskTerminals: vi.fn(async () => ({ terminals: [] })),
    getAgentControl: vi.fn(async () => ({ id: null, connected: false, tasks: {} })),
    getPreview: vi.fn(async () => null),
    readPreviewFile: vi.fn(async () => {
      throw new Error('file not found')
    }),
  }
})
// Monaco does not run in a DOM test: a textarea stands in for the editor.
vi.mock('../CodeEditor.vue', () => ({
  default: {
    name: 'CodeEditorStub',
    props: ['modelValue', 'filename', 'readonly', 'lines', 'language', 'keep'],
    emits: ['update:modelValue', 'save', 'quick-open'],
    template:
      '<textarea data-testid="editor" :data-file="filename" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
}))

import WorkPanel from '../WorkPanel.vue'

const room = { id: 'topic-A', project_id: 'p1', title: '综合', status: 'active' } as Topic

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})
beforeEach(() => {
  setLocale('zh-CN')
  localStorage.clear()
})

/** The task page's work panel on a layout that is in one form or the other. */
function mountTask() {
  const editor = ref(false)
  // What each cell has in front: in the 对话 form the work panel's cell; in the 编辑器 form the
  // left column, the centre, and nothing else here.
  const front = ref<Set<string>>(new Set(['code']))
  const panel = ref<{ openFile: (path: string) => Promise<void> } | null>(null)
  const Host = defineComponent(() => () => {
    const layout: WorkLayout = {
      outlet: () => undefined,
      visible: front.value,
      show: (key) => (front.value = new Set([...(editor.value ? ['navigator'] : []), key])),
      single: false,
      head: () => undefined,
      editor: editor.value,
      blocks: !editor.value,
    }
    return h(
      WorkPanel,
      { ref: panel, topic: room, taskId: 'task-1', activityTick: 0, tab: 'code', layout },
      { overview: () => h('p', '概览的内容') }
    )
  })
  render(Host, {
    global: {
      plugins: [createVuetify({ components, directives }), i18n],
      stubs: {
        // The changes view in the centre says which file's changes it shows.
        PanelChangesHost: defineComponent({
          props: ['changes'],
          setup: (p: { changes?: { openPath: { value: string | null } } }) => () =>
            h('p', `改动：${p.changes?.openPath.value ?? '全部'}`),
        }),
      },
    },
  })
  function toEditor() {
    editor.value = true
    front.value = new Set(['navigator', 'code'])
  }
  function toChat() {
    editor.value = false
    front.value = new Set(['code'])
  }
  return { panel, toEditor, toChat, front }
}

const visible = (el: Element | null) => {
  const pane = el?.closest('.work-pane') as HTMLElement | null
  return !!pane && pane.style.display !== 'none'
}
const shownEditor = () => screen.queryAllByTestId('editor').find((el) => visible(el)) as HTMLTextAreaElement | undefined

describe('the same task in both forms', () => {
  it('a file opened in the 对话 form is still the open file, with what was typed, in the 编辑器 form and back', async () => {
    const { panel, toEditor, toChat } = mountTask()
    await waitFor(() => expect(panel.value).not.toBeNull())
    await panel.value!.openFile('src/stack.c')
    await waitFor(() => expect(shownEditor()?.dataset.file).toBe('src/stack.c'))
    await fireEvent.update(shownEditor()!, 'int pop(void) { return 2; }\n')

    toEditor()
    await waitFor(() => expect(shownEditor()?.dataset.file).toBe('src/stack.c'))
    expect(shownEditor()!.value).toBe('int pop(void) { return 2; }\n')
    // The left column's tree marks the same file as the one open.
    const tree = within(document.getElementById('work-pane-navigator')!)
    const current = await tree.findByRole('button', { name: /^stack\.c/, current: true })
    expect(visible(current)).toBe(true)

    toChat()
    await waitFor(() => expect(shownEditor()?.dataset.file).toBe('src/stack.c'))
    expect(shownEditor()!.value).toBe('int pop(void) { return 2; }\n')
  })

  it('in the 编辑器 form, a file picked in the left column opens in the centre', async () => {
    const { toEditor } = mountTask()
    toEditor()
    const pane = await waitFor(() => {
      const el = document.getElementById('work-pane-navigator')
      expect(el).not.toBeNull()
      return el!
    })
    const tree = within(pane)
    await fireEvent.click(await tree.findByRole('button', { name: 'src', expanded: false }))
    await fireEvent.click(await tree.findByRole('button', { name: 'queue.c' }))
    await waitFor(() => expect(shownEditor()?.dataset.file).toBe('src/queue.c'))
  })

  it('in the 编辑器 form, a file picked under 改动 in the left column shows its changes in the centre', async () => {
    const { toEditor, front } = mountTask()
    toEditor()
    await fireEvent.click(await screen.findByRole('button', { name: '改动' }))
    const list = await screen.findByRole('list', { name: '改动' })
    const row = await within(list).findByRole('button', { name: /stack\.c/ }, { timeout: 3000 })
    await fireEvent.click(row)
    await waitFor(() => expect(visible(screen.queryByText('改动：src/stack.c'))).toBe(true))
    expect([...front.value]).toContain('changes')
  })
})
