/** 现场 tab: it must open at the NEWEST entry, not the oldest.
 *
 * 现场 是施工日志 —— 打开它是为了看"刚刚发生了什么"。时间线按时间正序渲染
 * (和聊天一样，最早的在最上面)，所以"最新"就是滚动容器的底部。历史行为是停在
 * 顶部，于是每次打开都要手动滚到底才能看到最近的动作。
 *
 * happy-dom 不做排版，scrollHeight 恒为 0，所以这里把它桩成一个固定值——被测
 * 的是"面板有没有把滚动位置推到底"，不是浏览器的布局。
 */
import type { Block, Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

// 断言读的是中文界面上的那一行字，语言钉在中文上。
beforeEach(() => setLocale('zh-CN'))

vi.mock('../../api/docThreads', () => ({
  listDocThreads: async () => ({ data: [], total: 0 }),
  writeDocThread: async () => ({}),
}))
// The overview's document panel loads on its own and opens the room's
// document; these suites are about the site panel, so it gets an empty one.
vi.mock('../../api/docCollab', async () => ({
  ...(await vi.importActual<typeof import('../../api/docCollab')>('../../api/docCollab')),
  // 测试里房间的文档就用房间的 id 来认：fakeDocCollab 按它预置文档。
  getRoomDocument: async (topicId: string) => ({ id: topicId }),
}))
vi.mock('../../composables/useDocCollab', async () => ({
  useDocCollab: (await import('../../test/fakeDocCollab')).useFakeDocCollab,
}))
vi.mock('../../api/docHistory', () => ({
  getDocVersions: async () => ({ versions: [], cursor: null }),
  restoreDocVersion: async () => ({}),
}))
vi.mock('../CodeEditor.vue', () => ({
  default: {
    name: 'CodeEditor',
    props: ['modelValue', 'filename', 'readonly'],
    emits: ['update:modelValue', 'save'],
    template: '<textarea class="stub-editor" :value="modelValue" />',
  },
}))

const getTranscript = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    // 总览底下那一行「这个房间里的东西」一挂上就读一次。
    listRoomOutputs: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    // 总览里「进度」那一段会读它；这里不关心它，给一份空的。
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    // 面板画的是这个房间里的任务 task-1：改动那一格要在房间的任务里找到它。
    listRoomTasks: vi.fn().mockResolvedValue({
      data: [
        {
          id: 'task-1',
          project_id: 'p1',
          room_id: 'topic-A',
          title: '任务',
          status: 'open',
          branch_name: 'task/1',
          presentation: { column: 'building', phrase: 'running' },
          created_at: '2026-08-12T08:00:00Z',
          updated_at: '2026-08-12T08:00:00Z',
        },
      ],
      total: 1,
    }),
    getTranscript: (...a: unknown[]) => getTranscript(...a),
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false, tasks: {} }),
    // Everything else the panel calls on mount — quiet, empty answers.
    getDocNodes: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listFiles: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    readFile: vi.fn().mockResolvedValue(null),
    getForgeConnection: vi.fn().mockResolvedValue({ kind: 'forgejo', connected: true, repo: 'o/r', url: null }),
    getGitDiff: vi.fn().mockResolvedValue({ diff: '' }),
    getPreview: vi.fn().mockResolvedValue(null),
    getTopicUsage: vi.fn().mockResolvedValue(null),
    getProjectUsage: vi.fn().mockResolvedValue(null),
    // 规则 1: the tabs a topic offers follow what it actually holds. These suites
    // are about the tabs' CONTENT, so they mount a topic that holds everything.
    getTopicWorkSummary: vi.fn().mockResolvedValue({ changed_files: ['a.py'], has_run: true }),
  }
})

import WorkPanel from '../WorkPanel.vue'

/** Whatever the layout would have been — only "did it go to the bottom" matters. */
const SCROLL_HEIGHT = 4321

function topic(id: string): Topic {
  return { id, project_id: 'p1', title: `话题 ${id}`, status: 'active' } as Topic
}

function block(id: string, content: string): Block {
  return {
    id,
    conversation_id: 'topic-A',
    kind: 'message',
    author_type: 'participant',
    author: 'cheese',
    content,
    created_at: '2026-08-12T08:00:00Z',
  } as Block
}

// 现场、改动只长在任务上：频道那一侧没有这两格。
function mountPanel(id: string) {
  const vuetify = createVuetify({ components, directives })
  return render(WorkPanel, {
    props: { topic: topic(id), taskId: 'task-1', activityTick: 0 },
    global: { plugins: [vuetify, i18n] },
  })
}

/** Let the panel's awaits (fetch → nextTick → scroll) settle. */
async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function buttons(container: Element): HTMLButtonElement[] {
  return Array.from(container.querySelectorAll('button'))
}

async function openTab(container: Element, label: string) {
  const btn = buttons(container).find((b) => b.getAttribute('title')?.startsWith(label))
  expect(btn, `找不到 ${label} tab`).toBeTruthy()
  await fireEvent.click(btn!)
  await flush()
}

function scrollBox(container: Element, selector: string): HTMLElement {
  const el = container.querySelector<HTMLElement>(selector)
  expect(el, `找不到 ${selector} 滚动容器`).toBeTruthy()
  return el!
}

let restoreScrollHeight: (() => void) | null = null

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight')
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
    configurable: true,
    get: () => SCROLL_HEIGHT,
  })
  restoreScrollHeight = () => {
    if (original) Object.defineProperty(HTMLElement.prototype, 'scrollHeight', original)
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>).scrollHeight
  }
})

afterAll(() => restoreScrollHeight?.())

describe('现场面板', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getTranscript.mockResolvedValue({
      data: [block('b1', '最早的一条'), block('b2', '中间的一条'), block('b3', '最新的一条')],
      total: 3,
    })
  })

  it('打开现场，停在最新的一条(底部)，而不是最早的那条', async () => {
    const { container } = mountPanel('topic-A')
    await flush()
    await openTab(container, '现场')

    // 正文是懒加载的阅读器画的（common/MarkdownView），首帧那一块是空的：等它真的
    // 画出来再断言，不然读的是还没画的那一帧。
    await waitFor(() => expect(container.textContent).toContain('最新的一条'))
    expect(scrollBox(container, '.panel-site').scrollTop).toBe(SCROLL_HEIGHT)
  })

  it('只有现场这样开在末尾，别的 tab 照旧从头看', async () => {
    const { container } = mountPanel('topic-A')
    await flush()
    await openTab(container, '改动')

    expect(scrollBox(container, '.changes-scroll').scrollTop).toBe(0)
  })

  // 参数摊开后是 white-space: pre-wrap 的，所以模板里的换行和缩进会被原样画出
  // 来：只要参数和别的东西待在同一个文本流里，前面就会多出一截空白。这条钉住
  // "参数文字自己一个盒子、前后不带空白"。
  it('工具动作的参数预览：文字自己一个盒子，不带模板缩进', async () => {
    getTranscript.mockResolvedValue({
      data: [
        {
          ...block('e1', '读文件\nbackend/app/main.py'),
          kind: 'event',
          meta: { tool: 'Read', arg: 'backend/app/main.py' },
        } as Block,
      ],
      total: 1,
    })
    const { container } = mountPanel('topic-A')
    await flush()
    await openTab(container, '现场')

    const arg = container.querySelector('[data-testid="site-act-arg"]')
    expect(arg, '找不到参数预览').toBeTruthy()
    expect(arg!.textContent).toBe('backend/app/main.py')
  })
})
