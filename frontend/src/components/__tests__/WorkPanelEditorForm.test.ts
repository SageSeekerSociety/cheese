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
    // 搜索: the lines of the task's files holding the text, case ignored.
    searchTaskFiles: vi.fn(async (_p: string, _t: string, q: string) => ({
      files: Object.entries(FILES).flatMap(([path, text]) => {
        const matches = text.split('\n').flatMap((line, i) => {
          const at = line.toLowerCase().indexOf(q.toLowerCase())
          return at < 0
            ? []
            : [{ line: i + 1, text: line, columns: [[at, at + q.length]], cut_before: false, cut_after: false }]
        })
        return matches.length ? [{ path, matches }] : []
      }),
      truncated: false,
    })),
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
      '<textarea data-testid="editor" :data-file="filename" :data-lines="lines ? `${lines.start}-${lines.end}` : undefined" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
}))

import WorkPanel from '../WorkPanel.vue'

import { searchTaskFiles } from '@/api'

const room = { id: 'topic-A', project_id: 'p1', title: '综合', status: 'active' } as Topic

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  // Vuetify's menus place themselves against it; happy-dom has none.
  if (!('visualViewport' in globalThis)) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = Object.assign(new EventTarget(), {
      width: 1280,
      height: 800,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
    })
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
  const panel = ref<{ openFile: (path: string) => Promise<void>; searchFiles: () => void } | null>(null)
  const Host = defineComponent(() => () => {
    const layout: WorkLayout = {
      outlet: () => undefined,
      visible: front.value,
      show: (key) => (front.value = new Set([...(editor.value ? ['navigator'] : []), key])),
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

describe('搜索 in the 编辑器 form', () => {
  async function openSearch() {
    const mounted = mountTask()
    mounted.toEditor()
    await fireEvent.click(await screen.findByRole('button', { name: '搜索' }))
    const box = await screen.findByRole('searchbox', { name: '搜索' })
    return { ...mounted, box }
  }
  const results = () => within(screen.getByRole('list', { name: '搜索' }))

  it('lists the matching lines by file, with how many, and a line opens its file at that line', async () => {
    const { box } = await openSearch()
    await fireEvent.update(box, 'INT')

    const list = await screen.findByRole('list', { name: '搜索' }, { timeout: 2000 })
    expect(screen.getByText('2 处匹配')).toBeTruthy()
    expect(screen.getByText('2 个文件')).toBeTruthy()
    const queue = within(list).getByRole('button', { name: /queue\.c/ })
    expect(within(queue).getByLabelText('1 处匹配')).toBeTruthy()
    // The matched text is marked within its line.
    const line = within(list).getByRole('button', { name: 'int queue(void) { return 0; }' })
    expect(line.querySelector('mark')?.textContent).toBe('int')

    await fireEvent.click(within(list).getByRole('button', { name: 'int pop(void) { return 1; }' }))
    await waitFor(() => expect(shownEditor()?.dataset.file).toBe('src/stack.c'))
    expect(shownEditor()?.dataset.lines).toBe('1-1')
  })

  it('a file row opens that file at its first match', async () => {
    const { box } = await openSearch()
    await fireEvent.update(box, 'return')
    await screen.findByRole('list', { name: '搜索' }, { timeout: 2000 })
    await fireEvent.click(results().getByRole('button', { name: /queue\.c/ }))
    await waitFor(() => expect(shownEditor()?.dataset.file).toBe('src/queue.c'))
    expect(shownEditor()?.dataset.lines).toBe('1-1')
  })

  it('says 暂无匹配 when nothing holds the text', async () => {
    const { box } = await openSearch()
    await fireEvent.update(box, 'nowhere')
    expect(await screen.findByText('暂无匹配', {}, { timeout: 2000 })).toBeTruthy()
  })

  it('says the search failed, and 重试 searches again', async () => {
    vi.mocked(searchTaskFiles).mockRejectedValueOnce(new Error('环境暂时无法响应'))
    const { box } = await openSearch()
    await fireEvent.update(box, 'pop')
    expect(await screen.findByText('搜索失败', {}, { timeout: 2000 })).toBeTruthy()

    await fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await screen.findByRole('list', { name: '搜索' }, { timeout: 2000 })
    expect(screen.queryByText('搜索失败')).toBeNull()
    expect(results().getByRole('button', { name: /stack\.c/ })).toBeTruthy()
  })

  it('an answer to an older text that arrives late does not replace the newer one', async () => {
    let answerOld: (value: Awaited<ReturnType<typeof searchTaskFiles>>) => void = () => {}
    vi.mocked(searchTaskFiles).mockImplementationOnce(() => new Promise((resolve) => (answerOld = resolve)))
    const { box } = await openSearch()
    await fireEvent.update(box, 'queue')
    await waitFor(() => expect(searchTaskFiles).toHaveBeenLastCalledWith('p1', 'task-1', 'queue', expect.anything()))
    // Enter searches at once.
    await fireEvent.update(box, 'pop')
    await fireEvent.keyDown(box, { key: 'Enter' })
    await screen.findByRole('list', { name: '搜索' }, { timeout: 2000 })

    answerOld({
      files: [
        {
          path: 'src/queue.c',
          matches: [{ line: 1, text: 'queue', columns: [[0, 5]], cut_before: false, cut_after: false }],
        },
      ],
      truncated: false,
    })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(results().getByRole('button', { name: /stack\.c/ })).toBeTruthy()
    expect(results().queryByRole('button', { name: /queue\.c/ })).toBeNull()
  })

  it('asking for the search from the page shows the 搜索 view with its box focused', async () => {
    const { panel, toEditor } = mountTask()
    toEditor()
    await screen.findByRole('button', { name: '新建文件' })
    panel.value!.searchFiles()
    const box = await screen.findByRole('searchbox', { name: '搜索' })
    await waitFor(() => expect(document.activeElement).toBe(box))
  })
})

describe("the left column's one row in the 文件 view", () => {
  it('reaches every action of the tree: new file, new folder, find by name, fold all', async () => {
    const { toEditor } = mountTask()
    toEditor()
    const pane = await waitFor(() => {
      const el = document.getElementById('work-pane-navigator')
      expect(el).not.toBeNull()
      return el!
    })
    const column = within(pane)

    /** The dialog asking for a name, by the action its button will take. */
    const naming = async (action: string) => {
      const field = await screen.findByRole('textbox', { name: '名称' })
      const dialog = field.closest('.v-overlay__content') as HTMLElement
      expect(within(dialog).getByRole('button', { name: action })).toBeTruthy()
      await fireEvent.click(within(dialog).getByRole('button', { name: '取消' }))
      await waitFor(() => expect(screen.queryByRole('textbox', { name: '名称' })).toBeNull(), { timeout: 3000 })
    }

    await fireEvent.click(await column.findByRole('button', { name: '新建文件' }))
    await naming('新建文件')

    const more = () => column.getByRole('button', { name: '更多操作' })
    await fireEvent.click(more())
    await fireEvent.click(await screen.findByRole('menuitem', { name: '新建文件夹' }))
    await naming('新建文件夹')

    await fireEvent.click(more())
    await fireEvent.click(await screen.findByRole('menuitem', { name: '按名称找文件' }))
    const finder = await screen.findByRole('textbox', { name: '搜索文件' })
    await fireEvent.click(
      within(finder.closest('.v-overlay__content') as HTMLElement).getByRole('button', { name: '取消' })
    )
    await waitFor(() => expect(screen.queryByRole('textbox', { name: '搜索文件' })).toBeNull(), { timeout: 3000 })

    const folder = await column.findByRole('button', { name: 'src', expanded: false })
    await fireEvent.click(folder)
    await waitFor(() => expect(column.getByRole('button', { name: 'src' }).getAttribute('aria-expanded')).toBe('true'))
    await fireEvent.click(more())
    await fireEvent.click(await screen.findByRole('menuitem', { name: '全部收起' }))
    await waitFor(() => expect(column.getByRole('button', { name: 'src' }).getAttribute('aria-expanded')).toBe('false'))
  })
})
