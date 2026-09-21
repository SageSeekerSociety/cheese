/** 预览 tab: what it says when a running app is not reachable, what a refresh
 * must not do to the one that IS, and what a new preview has to announce.
 *
 * 三件事被钉在这里：
 *   1. 「那台机器没把预览通道拨出来」和「通道在、应用死了」是两回事。合成一句以后
 *      面板会叫人「再 @ 它一次」去等一个 @ 不回来的通道。
 *   2. 预览面板会自动刷新，但静默刷新绝不能把 iframe 拆掉重建——那会让正在看的
 *      产物每 20 秒重载一次（交互式 artifact 里攒下的状态全丢），比不刷新更糟。
 *   3. 芝士换了预览，没停在这个 tab 上的人也要看得见。
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
const requestPreviewSession = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getPreview: (...a: unknown[]) =>
      getPreview(...a).then(
        (preview: Record<string, unknown> | null) =>
          preview && { ...preview, url: preview.kind === 'app' ? preview.url : 'https://preview-topic-a.example/' }
      ),
    readPreviewFile: (...a: unknown[]) => readFile(...a),
    requestPreviewSession: (...a: unknown[]) => requestPreviewSession(...a),
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
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listRoomTrees: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    // 规则 1: the tabs a topic offers follow what it actually holds. These suites
    // are about the tabs' CONTENT, so they mount a topic that holds everything.
    getTopicWorkSummary: vi.fn().mockResolvedValue({ changed_files: ['a.py'], has_run: true }),
  }
})

import { setLocale } from '../../i18n'
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
  // 这一份按 tab 的名字点它（`previewButton` 找的是 title 以「预览」开头的那一格）。
  setLocale('zh-CN')
  vi.clearAllMocks()
  getPreview.mockResolvedValue(null)
  readFile.mockResolvedValue({ path: 'report.html', content: '<p>hi</p>' })
  requestPreviewSession.mockResolvedValue({
    url: 'https://preview-topic-a.example/_cheese/session',
    grant: 'preview-only',
  })
  vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => {})
})

describe('预览面板：运行中的应用到不了的时候说什么', () => {
  it('机器没把预览通道拨出来 → 说的是通道，不是应用', async () => {
    getPreview.mockResolvedValue({
      kind: 'app',
      path: 'Vue dev server',
      mime: 'application/x-cheesex-app',
      url: null,
      tunnel_up: false,
      artifact_id: 'a1',
    })
    const { container } = mountPanel()
    await flush()
    await openPreview(container)

    expect(container.textContent).toContain('预览通道')
    expect(container.textContent).not.toContain('服务多半已经退出')
  })

  it('通道在、应用死了 → 说的是应用，并告诉人再 @ 一次能拉起来', async () => {
    getPreview.mockResolvedValue({
      kind: 'app',
      path: 'Vue dev server',
      mime: 'application/x-cheesex-app',
      url: null,
      tunnel_up: true,
      artifact_id: 'a1',
    })
    const { container } = mountPanel()
    await flush()
    await openPreview(container)

    expect(container.textContent).toContain('应用暂时不在线')
    expect(container.textContent).toContain('服务多半已经退出')
  })

  it('应用活着 → 向独立来源提交预览授权', async () => {
    getPreview.mockResolvedValue({
      kind: 'app',
      path: 'Vue dev server',
      mime: 'application/x-cheesex-app',
      url: 'https://preview-topic-a.example/',
      tunnel_up: true,
      artifact_id: 'a1',
    })
    const { container } = mountPanel()
    await flush()
    await openPreview(container)

    const frame = container.querySelector('iframe.preview-frame') as HTMLIFrameElement | null
    expect(frame?.getAttribute('src')).toBeNull()
    expect(frame?.getAttribute('name')).toBeTruthy()
    expect(HTMLFormElement.prototype.submit).toHaveBeenCalledOnce()
    // 授权先落地，否则 iframe 的第一个请求就 404 —— 白框。
    expect(requestPreviewSession).toHaveBeenCalledWith('topic-A')
    // The named form targets an isolated content origin, so storage can work.
    expect(frame?.getAttribute('sandbox')).toContain('allow-same-origin')
  })
})

describe('预览面板：文件读回来了但没有内容', () => {
  it('大文件不再受文本编辑器读取上限限制，直接从独立来源加载', async () => {
    getPreview.mockResolvedValue({ kind: 'file', path: 'report.html', mime: 'text/html', artifact_id: 'a1' })
    readFile.mockResolvedValue({
      path: 'report.html',
      content: null,
      version: null,
      bytes: 2_113_182,
      binary: false,
      too_large: true,
    })
    const { container } = mountPanel()
    await flush()
    await openPreview(container)

    expect(container.textContent).not.toContain('太大')
    expect(container.querySelector('iframe.preview-frame')).toBeTruthy()
    expect(requestPreviewSession).toHaveBeenCalledWith('topic-A')
  })

  it('文件不是文本 → 说的是它读不了，不是它太大', async () => {
    getPreview.mockResolvedValue({ kind: 'file', path: 'shot.bin', mime: 'text/html', artifact_id: 'a1' })
    readFile.mockResolvedValue({
      path: 'shot.bin',
      content: null,
      version: 'v1',
      bytes: 2048,
      binary: true,
      too_large: false,
    })
    const { container } = mountPanel()
    await flush()
    await openPreview(container)

    expect(container.textContent).toContain('不是文本')
    expect(container.textContent).not.toContain('太大')
  })
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
