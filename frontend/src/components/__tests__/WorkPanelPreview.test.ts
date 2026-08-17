/** 预览 tab: what it says when the app is not reachable, and what a refresh must
 * not do to the app that IS.
 *
 * 三件事被钉在这里：
 *   1. 「运行环境到不了」和「运行环境挂了」是两回事。前者以前被当成后者，于是
 *      面板叫人「再 @ 它一次」去等一个永远不会出现的运行环境。
 *   2. 预览面板会自动刷新了，但静默刷新绝不能把 iframe 拆掉重建——那会让正在
 *      看的应用每 20 秒重载一次，比不刷新更糟。
 *   3. 芝士换了预览，没停在这个 tab 上的人也要看得见。
 *
 * (Was DocPanelPreview.test.ts. 抽屉 → tab，断言一条没改。)
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
const primeAppPreview = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getPreview: (...a: unknown[]) => getPreview(...a),
    primeAppPreview: (...a: unknown[]) => primeAppPreview(...a),
    getDoc: vi.fn().mockResolvedValue({ markdown: '', title: '' }),
    putDoc: vi.fn().mockResolvedValue({}),
    getComments: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getDocNodes: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listFiles: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    readFile: vi.fn().mockResolvedValue(null),
    getGitLog: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getGitDiff: vi.fn().mockResolvedValue({ diff: '' }),
    getTranscript: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTerminal: vi.fn().mockResolvedValue({ available: false, backend: 'none' }),
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
  primeAppPreview.mockResolvedValue({ ready: true })
  getPreview.mockResolvedValue(null)
})

describe('预览面板：应用到不了的时候说什么', () => {
  it('运行环境不在平台这边 → 不叫人再 @ 一次去等一个不会来的容器', async () => {
    getPreview.mockResolvedValue({
      kind: 'app',
      path: 'Vue dev server',
      mime: 'application/x-cheesex-app',
      url: null,
      container_up: false,
      supported: false,
      artifact_id: 'a1',
    })
    const { container } = mountPanel()
    await flush()
    await openPreview(container)

    expect(container.textContent).toContain('这里看不到运行中的应用')
    expect(container.textContent).not.toContain('再 @ 它一次')
  })

  it('运行环境在、应用死了 → 还是那句「再 @ 它一次」，这条没变', async () => {
    getPreview.mockResolvedValue({
      kind: 'app',
      path: 'Vue dev server',
      mime: 'application/x-cheesex-app',
      url: null,
      container_up: true,
      supported: true,
      artifact_id: 'a1',
    })
    const { container } = mountPanel()
    await flush()
    await openPreview(container)

    expect(container.textContent).toContain('应用暂时不在线')
    expect(container.textContent).toContain('再 @ 它一次')
  })

  it('后端没有 supported 字段（旧版本）→ 沿用原来的说法，不误判成到不了', async () => {
    getPreview.mockResolvedValue({
      kind: 'app',
      path: 'Vue dev server',
      mime: 'application/x-cheesex-app',
      url: null,
      container_up: true,
      artifact_id: 'a1',
    })
    const { container } = mountPanel()
    await flush()
    await openPreview(container)

    expect(container.textContent).toContain('应用暂时不在线')
  })
})

describe('预览面板：刷新', () => {
  it('静默刷新不重建 iframe——正在看的应用不会被重载', async () => {
    const app = {
      kind: 'app',
      path: 'Vue dev server',
      mime: 'application/x-cheesex-app',
      url: '/api/topics/topic-A/app/',
      container_up: true,
      supported: true,
      artifact_id: 'a1',
    }
    getPreview.mockResolvedValue(app)
    const { container, rerender } = mountPanel(true)
    await flush()
    await openPreview(container)

    const frame = container.querySelector('iframe.preview-frame')
    expect(frame, '应用应该已经嵌进来了').toBeTruthy()
    const callsBefore = getPreview.mock.calls.length

    // 一轮结束 → 面板重新拉一次。
    await rerender({ topic: topic('topic-A'), activityTick: 0, working: false })
    await flush()

    expect(getPreview.mock.calls.length).toBeGreaterThan(callsBefore)
    // 同一个 DOM 节点 = 没有卸载重建 = 应用没有重载。
    expect(container.querySelector('iframe.preview-frame')).toBe(frame)
    // 授权 cookie 也不用再要一次：反代每转发一次就续一次。
    expect(primeAppPreview).toHaveBeenCalledTimes(1)
  })
})

describe('预览面板：有新内容', () => {
  it('停在别的 tab 时芝士换了预览 → 预览 tab 上出现提示', async () => {
    getPreview.mockResolvedValue({
      kind: 'file',
      path: 'report.html',
      mime: 'text/html',
      artifact_id: 'a1',
    })
    const { container, rerender } = mountPanel(true)
    await flush()
    // 一进来就有的预览不算「新」——不该顶着一个提示开场。
    expect(previewButton(container).getAttribute('title')).toBe('预览')

    getPreview.mockResolvedValue({
      kind: 'file',
      path: 'report.html',
      mime: 'text/html',
      artifact_id: 'a2',
    })
    await rerender({ topic: topic('topic-A'), activityTick: 0, working: false })
    await flush()

    expect(previewButton(container).getAttribute('title')).toContain('有新内容')
  })
})
