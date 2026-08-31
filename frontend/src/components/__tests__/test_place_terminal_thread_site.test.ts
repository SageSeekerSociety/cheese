/** 打开一条支线，工作面板要给出「现场」，不给「改动」。
 *
 * 这两格以前被同一个 `if (onThread) return false` 一起关掉，理由是「一批活共用
 * 一棵树」。那个理由对「改动」成立，对「现场」不成立：
 *
 * - **改动属于树**。一棵树 = 一个分支 = 一个 PR = 一批活，一条支线和它的同伴写
 *   的是同一条分支，diff 是他们一起做的。摆在支线上，看的人会以为那是这条活一个
 *   人做的。所以支线上不给。
 * - **现场属于地点**。一条支线跑的是它自己的 agent、自己的会话、自己那块屏幕，
 *   问的也是自己的 id。所以支线上给。
 *
 * 文件名带 `test_place_terminal` 前缀，是派活时定的防撞规则（同一棵树上有几条
 * 支线在并行改前端）。
 */
import type { Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../CodeEditor.vue', () => ({
  default: {
    name: 'CodeEditor',
    props: ['modelValue', 'filename', 'readonly'],
    emits: ['update:modelValue', 'save'],
    template: '<textarea class="stub-editor" :value="modelValue" />',
  },
}))

const getTopicWorkSummary = vi.fn()
const getTerminal = vi.fn()
const getTranscript = vi.fn()
const getPreview = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getDoc: vi.fn().mockResolvedValue({ content: '' }),
    listFiles: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    readFile: vi.fn().mockResolvedValue({ path: 'a.py', content: '', version: 'v1', bytes: 0, binary: false, too_large: false }),
    getGitDiff: vi.fn().mockResolvedValue({ diff: '' }),
    getComments: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    addComment: vi.fn().mockResolvedValue({ id: 'c1' }),
    putDoc: vi.fn().mockResolvedValue({}),
    writeFile: vi.fn().mockResolvedValue({ path: 'a.py', version: 'v2' }),
    getDocNodes: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getGitLog: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTopicUsage: vi.fn().mockResolvedValue(null),
    getProjectUsage: vi.fn().mockResolvedValue(null),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTopicWorkSummary: (...a: unknown[]) => getTopicWorkSummary(...a),
    getTerminal: (...a: unknown[]) => getTerminal(...a),
    getTranscript: (...a: unknown[]) => getTranscript(...a),
    getPreview: (...a: unknown[]) => getPreview(...a),
  }
})

import WorkPanel from '../WorkPanel.vue'

/** 一条支线，形状和 `threadAsPlace()` 给出来的一样：`kind: 'thread'`，房间在
 *  `parent_id` 上。 */
function thread(id = 'thread-1'): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: 'room-1',
    title: '一件活',
    kind: 'thread',
    status: 'open',
  } as Topic
}

function room(id = 'room-1'): Topic {
  return { id, project_id: 'p1', title: '房间', kind: 'topic', status: 'active' } as Topic
}

function mountPanel(place: Topic, props: Record<string, unknown> = {}) {
  const vuetify = createVuetify({ components, directives })
  return render(WorkPanel, {
    props: { topic: place, activityTick: 0, ...props },
    global: { plugins: [vuetify] },
  })
}

async function flush() {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 哪几格在 tab 栏上。取的是名字，不是整段文字 —— 「改动」那一格后面跟着文件数
 *  （`改动 1`），拿整段去比会让 `not.toContain('改动')` 永远成立，测不出任何东西。 */
function tabNames(container: Element): string[] {
  return Array.from(container.querySelectorAll('.tabbar__tab')).map(
    (b) => (b.textContent ?? '').trim().split(/\s+/)[0] ?? ''
  )
}

function visible(container: Element, selector: string): boolean {
  const el = container.querySelector<HTMLElement>(selector)
  return !!el && el.style.display !== 'none'
}

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
  vi.clearAllMocks()
  // 一条跑过、而且它那棵树上有改动的支线 —— 「两格一起给」和「只给现场」在这里
  // 才分得出来。
  getTopicWorkSummary.mockResolvedValue({ changed_files: ['a.py'], has_run: true })
  getTerminal.mockResolvedValue({ available: false, backend: 'none' })
  getTranscript.mockResolvedValue({ data: [], total: 0 })
  getPreview.mockResolvedValue(null)
})

describe('工作面板 · 一条支线的现场', () => {
  it('支线上有「现场」这一格', async () => {
    const { container } = mountPanel(thread())
    await flush()

    expect(tabNames(container)).toContain('现场')
  })

  it('支线上没有「改动」这一格 —— 那份 diff 是一批活共有的', async () => {
    const { container } = mountPanel(thread())
    await flush()

    expect(tabNames(container)).not.toContain('改动')
  })

  it('切过去渲染的是这条支线自己的现场，问的也是它自己的 id', async () => {
    const { container } = mountPanel(thread('thread-42'), { tab: 'site' })
    await flush()

    expect(visible(container, '.panel-site')).toBe(true)
    expect(getTranscript).toHaveBeenCalledWith('thread-42', expect.anything())
    expect(getTerminal).toHaveBeenCalledWith('thread-42')
  })

  it('第一轮刚开跑、会话还没落库：现场立刻就在', async () => {
    getTopicWorkSummary.mockResolvedValue({ changed_files: [], has_run: false })
    const { container } = mountPanel(thread(), { working: true })
    await flush()

    expect(tabNames(container)).toContain('现场')
  })

  it('一步没跑过的支线不摆一个空的现场', async () => {
    getTopicWorkSummary.mockResolvedValue({ changed_files: [], has_run: false })
    const { container } = mountPanel(thread())
    await flush()

    expect(tabNames(container)).not.toContain('现场')
  })

  it('支线正干着活的时候开在现场上', async () => {
    const { container, rerender } = mountPanel(thread())
    await flush()
    await rerender({ topic: thread(), activityTick: 0, working: true, phase: 'working' })
    await flush()

    expect(visible(container, '.panel-site')).toBe(true)
  })

  it('支线待验收时留在总览 —— 「改动」那一格在这里根本不存在，切过去就是一片空白', async () => {
    const { container, rerender } = mountPanel(thread())
    await flush()
    await rerender({ topic: thread(), activityTick: 0, phase: 'reviewing' })
    await flush()

    expect(visible(container, '.panel-overview')).toBe(true)
    expect(visible(container, '.panel-changes')).toBe(false)
  })

  it('回归：房间那一侧两格都还在，待验收照旧开在改动上', async () => {
    const { container, rerender } = mountPanel(room())
    await flush()
    expect(tabNames(container)).toEqual(expect.arrayContaining(['现场', '改动']))

    await rerender({ topic: room(), activityTick: 0, phase: 'reviewing' })
    await flush()
    expect(visible(container, '.panel-changes')).toBe(true)
  })
})
