/** 预览 tab: what a refresh must not do to the artifact someone is looking at,
 * and what a new one has to announce.
 *
 * 两件事被钉在这里：
 *   1. 预览面板会自动刷新，但静默刷新绝不能把 iframe 拆掉重建——那会让正在看的
 *      产物每 20 秒重载一次（交互式 artifact 里攒下的状态全丢），比不刷新更糟。
 *   2. 芝士换了预览，没停在这个 tab 上的人也要看得见。
 */
import type { Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../CodeEditor.vue', () => ({
  default: {
    name: 'CodeEditor',
    props: ['modelValue', 'filename', 'readonly'],
    emits: ['update:modelValue', 'save'],
    template: '<textarea class="stub-editor" :value="modelValue" />',
  },
}))

const getPreview = vi.fn()
const readFile = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getPreview: (...a: unknown[]) => getPreview(...a),
    readFile: (...a: unknown[]) => readFile(...a),
    getDoc: vi.fn().mockResolvedValue({ markdown: '', title: '' }),
    putDoc: vi.fn().mockResolvedValue({}),
    getComments: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getDocNodes: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listFiles: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getGitLog: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getGitDiff: vi.fn().mockResolvedValue({ diff: '' }),
    getTranscript: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTerminal: vi.fn().mockResolvedValue({ available: false }),
    getTopicUsage: vi.fn().mockResolvedValue(null),
    getProjectUsage: vi.fn().mockResolvedValue(null),
    // 规则 1: the tabs a topic offers follow what it actually holds. These suites
    // are about the tabs' CONTENT, so they mount a topic that holds everything.
    getTopicWorkSummary: vi.fn().mockResolvedValue({ changed_files: ['a.py'], has_run: true }),
  }
})

import WorkPanel from '../WorkPanel.vue'

function topic(id: string): Topic {
  return { id, project_id: 'p1', title: `话题 ${id}`, status: 'active' } as Topic
}

function mountPanel(working = false) {
  const vuetify = createVuetify({ components, directives })
  return render(WorkPanel, {
    props: { topic: topic('topic-A'), activityTick: 0, working },
    global: { plugins: [vuetify] },
  })
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function previewButton(container: Element): HTMLButtonElement {
  // startsWith, not includes: the tab's own title is 预览 / 预览（有新内容）,
  // while the panel inside carries a 全屏预览 button that would also match.
  const btn = Array.from(container.querySelectorAll('button')).find((b) => b.getAttribute('title')?.startsWith('预览'))
  expect(btn, '找不到 预览 tab').toBeTruthy()
  return btn!
}

async function openPreview(container: Element) {
  await fireEvent.click(previewButton(container))
  await flush()
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
  getPreview.mockResolvedValue(null)
  readFile.mockResolvedValue({ path: 'report.html', content: '<p>hi</p>' })
})

describe('预览面板：刷新', () => {
  it('静默刷新不重建 iframe——正在看的产物不会被重载', async () => {
    getPreview.mockResolvedValue({
      path: 'report.html',
      mime: 'text/html',
      artifact_id: 'a1',
    })
    const { container, rerender } = mountPanel(true)
    await flush()
    await openPreview(container)

    const frame = container.querySelector('iframe.preview-frame')
    expect(frame, '产物应该已经嵌进来了').toBeTruthy()
    const callsBefore = getPreview.mock.calls.length

    // 一轮结束 → 面板重新拉一次。
    await rerender({ topic: topic('topic-A'), activityTick: 0, working: false })
    await flush()

    expect(getPreview.mock.calls.length).toBeGreaterThan(callsBefore)
    // 同一个 DOM 节点 = 没有卸载重建 = 产物没有重载。
    expect(container.querySelector('iframe.preview-frame')).toBe(frame)
  })
})

describe('预览面板：有新内容', () => {
  it('停在别的 tab 时芝士换了预览 → 预览 tab 上出现提示', async () => {
    getPreview.mockResolvedValue({
      path: 'report.html',
      mime: 'text/html',
      artifact_id: 'a1',
    })
    const { container, rerender } = mountPanel(true)
    await flush()
    // 一进来就有的预览不算「新」——不该顶着一个提示开场。
    expect(previewButton(container).getAttribute('title')).toBe('预览')

    getPreview.mockResolvedValue({
      path: 'report.html',
      mime: 'text/html',
      artifact_id: 'a2',
    })
    await rerender({ topic: topic('topic-A'), activityTick: 0, working: false })
    await flush()

    expect(previewButton(container).getAttribute('title')).toContain('有新内容')
  })
})
