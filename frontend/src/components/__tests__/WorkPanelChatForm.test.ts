/**
 * 任务页「对话」那一种摆法里的「文件」那一块：一次一份文件，没有树和页签；标题栏上的路径点开是
 * 同目录的文件和最近打开过的几份，挑一份就换；「在编辑器里打开」请页面换到编辑器。对话里点一个
 * 文件链接，打开的就是这一块，停在那几行，概览让开。
 *
 * 布局区是一个最小的替身：它记着哪几块在屏幕上（概览让开别的、别的让开概览），给每一块一个标题栏
 * 落点。后端是一个只认这件任务几份文件的假后端。断言的是读者看到的东西。
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
  'README.md': '# 数据结构\n',
  'src/queue.c': 'int queue(void) { return 0; }\n',
  'src/stack.c': 'int pop(void) {\n  return 1;\n}\n',
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
    getTaskMachineStatus: vi.fn(async () => ({ machine: 'ready', failed: null })),
    getTopicWorkSummary: vi.fn(async () => ({ changed_files: [], has_run: true })),
    listTaskTerminals: vi.fn(async () => ({ terminals: [] })),
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
      '<textarea data-testid="editor" :data-file="filename" :data-lines="lines ? `${lines.start}-${lines.end}` : undefined" :value="modelValue" />',
  },
}))

import WorkPanel from '../WorkPanel.vue'

const room = { id: 'topic-A', project_id: 'p1', title: '综合', status: 'active' } as Topic

beforeAll(() => {
  // The file list is a menu: Vuetify places it against the visual viewport.
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: Object.assign(new EventTarget(), { width: 1440, height: 900, offsetTop: 0, offsetLeft: 0, scale: 1 }),
    })
  }
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
  document.querySelectorAll('[data-head]').forEach((el) => el.remove())
})

/** The task page's work panel in the 对话 form, on a layout that keeps 概览 apart from the rest. */
function mountChatForm() {
  const inSight = ref<Set<string>>(new Set(['overview']))
  const heads = new Map<string, HTMLElement>()
  /** Each block's title bar, as the layer gives one. */
  function head(key: string): HTMLElement {
    let el = heads.get(key)
    if (!el) {
      el = document.createElement('div')
      el.dataset.head = key
      document.body.appendChild(el)
      heads.set(key, el)
    }
    return el
  }
  const panel = ref<{ openFile: (path: string) => Promise<void>; select: (key: string) => void } | null>(null)
  const onOpenEditor = vi.fn()
  const Host = defineComponent(() => () => {
    const layout: WorkLayout = {
      outlet: () => undefined,
      visible: inSight.value,
      show: (key) => {
        if (key === 'overview') inSight.value = new Set(['overview'])
        else inSight.value = new Set([...inSight.value].filter((one) => one !== 'overview').concat(key))
      },
      single: false,
      blocks: true,
      head: (key) => (inSight.value.has(key) ? head(key) : undefined),
    }
    return h(
      WorkPanel,
      { ref: panel, topic: room, taskId: 'task-1', activityTick: 0, layout, onOpenEditor },
      { overview: () => h('p', '概览的内容') }
    )
  })
  render(Host, { global: { plugins: [createVuetify({ components, directives }), i18n] } })
  return { panel, inSight, head, onOpenEditor }
}

const visible = (el: Element | null) => {
  const pane = el?.closest('.work-pane') as HTMLElement | null
  return !!pane && pane.style.display !== 'none'
}
const shownEditor = () => screen.queryAllByTestId('editor').find((el) => visible(el)) as HTMLTextAreaElement | undefined

describe('the 文件 block of the 对话 form', () => {
  it('a file link in the conversation opens the 文件 block on that file at its lines, and 概览 makes way', async () => {
    const { panel, inSight } = mountChatForm()
    await waitFor(() => expect(visible(screen.getByText('概览的内容'))).toBe(true))
    await panel.value!.openFile('src/stack.c:2-3')
    await waitFor(() => expect(shownEditor()?.dataset.file).toBe('src/stack.c'))
    expect(shownEditor()!.dataset.lines).toBe('2-3')
    expect([...inSight.value]).toEqual(['code'])
    expect(visible(screen.getByText('概览的内容'))).toBe(false)
  })

  it('its path lists the files beside the open one and those opened recently, and picking one shows it', async () => {
    const { panel, head } = mountChatForm()
    await panel.value!.openFile('README.md')
    await panel.value!.openFile('src/stack.c')
    await waitFor(() => expect(shownEditor()?.dataset.file).toBe('src/stack.c'))
    const bar = within(head('code'))
    // No tab strip in this block: the path in its title bar is how another file is reached.
    expect(screen.queryByRole('group', { name: '打开的文件' })).toBeNull()
    await fireEvent.click(bar.getByRole('button', { name: /src\/stack\.c/ }))
    const list = await screen.findByRole('list', { name: '换一份文件' })
    const rows = within(list)
    expect(rows.getByText('queue.c')).toBeTruthy()
    expect(rows.getByText('README.md')).toBeTruthy()
    await fireEvent.click(rows.getByText('queue.c'))
    await waitFor(() => expect(shownEditor()?.dataset.file).toBe('src/queue.c'))
  })

  it('with no file open it says so, and its path reads 文件 and lists the root of the task', async () => {
    const { panel, head } = mountChatForm()
    panel.value!.select('code')
    await waitFor(() => expect(screen.getByText('暂无打开的文件')).toBeTruthy())
    await fireEvent.click(within(head('code')).getByRole('button', { name: /文件/ }))
    const list = await screen.findByRole('list', { name: '换一份文件' })
    expect(within(list).getByText('src')).toBeTruthy()
    expect(within(list).getByText('README.md')).toBeTruthy()
    expect(within(list).queryByText('queue.c')).toBeNull()
  })

  it('「在编辑器里打开」 asks the page for the 编辑器 form, on this file', async () => {
    const { panel, head, onOpenEditor } = mountChatForm()
    await panel.value!.openFile('src/stack.c')
    await waitFor(() => expect(shownEditor()?.dataset.file).toBe('src/stack.c'))
    await fireEvent.click(within(head('code')).getByRole('button', { name: '在编辑器里打开' }))
    expect(onOpenEditor).toHaveBeenCalledWith('code')
  })
})
