/** 「预览」那一格开着的时候，兜底轮询有多快、什么时候补一次。
 *
 * 芝士摆出新东西时，房间那条 socket 上的一帧会立刻把它推过来（WorkPanel 的
 * `previewShown`）。可那一帧可能在断线的一小段里丢了，所以这一格开着时还要自己每
 * 5 秒问一次指针——指针真换了才让预览重取。切回窗口 / 回到前台也立刻补一次，不等
 * 下一个 5 秒。
 *
 * 时间在这个文件里全由假时钟给：真等 5 秒等于把这份 spec 变成 5 秒。
 */
import type { Topic } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '../../i18n'

vi.mock('../../api/docThreads', () => ({
  listDocThreads: async () => ({ data: [], total: 0 }),
  writeDocThread: async () => ({}),
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

const getPreview = vi.fn()
const readFile = vi.fn()
const requestPreviewSession = vi.fn()

vi.mock('../../api/docCollab', async () => ({
  ...(await vi.importActual<typeof import('../../api/docCollab')>('../../api/docCollab')),
  // 测试里房间的文档就用房间的 id 来认：fakeDocCollab 按它预置文档。
  getRoomDocument: async (topicId: string) => ({ id: topicId }),
}))
vi.mock('../../composables/useDocCollab', async () => ({
  useDocCollab: (await import('../../test/fakeDocCollab')).useFakeDocCollab,
}))
vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    getPreview: (...a: unknown[]) =>
      getPreview(...a).then(
        (preview: Record<string, unknown> | null) =>
          preview && { ...preview, url: preview.kind === 'app' ? preview.url : 'https://preview-topic-a.example/' }
      ),
    listRoomOutputs: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    readPreviewFile: (...a: unknown[]) => readFile(...a),
    requestPreviewSession: (...a: unknown[]) => requestPreviewSession(...a),
    getDocNodes: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listFiles: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getGitDiff: vi.fn().mockResolvedValue({ diff: '' }),
    getTranscript: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTerminal: vi.fn().mockResolvedValue({ available: false }),
    getTopicUsage: vi.fn().mockResolvedValue(null),
    getProjectUsage: vi.fn().mockResolvedValue(null),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listRoomTrees: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTopicWorkSummary: vi.fn().mockResolvedValue({ changed_files: ['a.py'], has_run: true }),
  }
})

import WorkPanel from '../WorkPanel.vue'

const POLL_MS = 5_000

function topic(id: string): Topic {
  return { id, project_id: 'p1', title: `话题 ${id}`, status: 'active' } as Topic
}

// 预览只长在任务上：面板画的是房间 topic-A 里的任务 task-1，预览问的也是它。
function mountPanel() {
  const vuetify = createVuetify({ components, directives })
  return render(WorkPanel, {
    props: { topic: topic('topic-A'), taskId: 'task-1', activityTick: 0, working: false },
    global: { plugins: [vuetify, i18n] },
  })
}

/** 假时钟下把组件的 Promise 链和一帧渲染走完：setTimeout/rAF 不会自己响。每步给
 *  一帧（16ms）那么长，够 Vue 的调度器和 vuetify 的挂载都落定，又远不到 5 秒。 */
async function flush() {
  for (let i = 0; i < 12; i += 1) await vi.advanceTimersByTimeAsync(16)
}

function previewButton(container: Element): HTMLButtonElement {
  const btn = Array.from(container.querySelectorAll('button')).find((b) => b.getAttribute('title')?.startsWith('预览'))
  expect(btn, '找不到 预览 tab').toBeTruthy()
  return btn!
}

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
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
  setLocale('zh-CN')
  vi.useFakeTimers()
  vi.clearAllMocks()
  getPreview.mockResolvedValue({ path: 'report.html', mime: 'text/html', artifact_id: 'a1' })
  readFile.mockResolvedValue({ path: 'report.html', content: '<p>hi</p>' })
  requestPreviewSession.mockResolvedValue({
    url: 'https://preview-topic-a.example/_cheese/session',
    grant: 'preview-only',
  })
  vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(function (this: HTMLFormElement) {
    const frame = document.querySelector(`iframe[name="${this.target}"]`)!
    Object.defineProperty(frame, 'contentDocument', { configurable: true, get: () => null })
    queueMicrotask(() => frame.dispatchEvent(new Event('load')))
  })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  Reflect.deleteProperty(document, 'hidden')
})

describe('预览那一格开着的兜底轮询', () => {
  it('每 5 秒问一次当前预览——比它的 20 秒档快', async () => {
    const { container } = mountPanel()
    await flush()
    await fireEvent.click(previewButton(container))
    await flush()

    const before = getPreview.mock.calls.length
    // 不到 5 秒不问。
    await vi.advanceTimersByTimeAsync(POLL_MS - 500)
    expect(getPreview.mock.calls.length).toBe(before)

    await vi.advanceTimersByTimeAsync(500)
    await flush()
    expect(getPreview.mock.calls.length).toBeGreaterThan(before)
  })

  it('没开在预览这一格时不问——没人在看它', async () => {
    mountPanel()
    await flush()
    const before = getPreview.mock.calls.length
    await vi.advanceTimersByTimeAsync(POLL_MS * 3)
    await flush()
    expect(getPreview.mock.calls.length).toBe(before)
  })

  it('页面在后台时不问，回到前台立刻补一次，不等下一个 5 秒', async () => {
    const { container } = mountPanel()
    await flush()
    await fireEvent.click(previewButton(container))
    await flush()

    setHidden(true)
    const hiddenAt = getPreview.mock.calls.length
    await vi.advanceTimersByTimeAsync(POLL_MS * 2)
    await flush()
    expect(getPreview.mock.calls.length).toBe(hiddenAt)

    // 回到前台：一帧 visibilitychange 就该补一次。
    setHidden(false)
    document.dispatchEvent(new Event('visibilitychange'))
    await flush()
    expect(getPreview.mock.calls.length).toBeGreaterThan(hiddenAt)
  })

  // 兜底轮询存在的理由正是「帧丢在断线那一小段里」。这两条钉的是它不能把自己要接住
  // 的那次更换吞掉：开格头几秒里换的那一份、以及断线后回来的那一份，都要真的让预览
  // 重取（比「只问一次指针」多出一次重取），不能退到 20 秒那一档。
  it('开格头 5 秒里换的那一份：兜底那一下也要让预览重取，不能当成基线放过去', async () => {
    const { container } = mountPanel()
    await flush()
    await fireEvent.click(previewButton(container))
    await flush()
    const before = getPreview.mock.calls.length // 开格时读到的是 a1

    // 那条 WS 帧丢了（没走到 previewShown）：头一个 5 秒里芝士换成了 a2。
    getPreview.mockResolvedValue({ path: 'report.html', mime: 'text/html', artifact_id: 'a2' })
    await vi.advanceTimersByTimeAsync(POLL_MS)
    await flush()

    // 指针换了 → 兜底那一下不只要问，还要让预览那一格重取（比「只问一次」多）。
    expect(getPreview.mock.calls.length).toBeGreaterThan(before + 1)
  })

  it('兜底那一问失败，不把上一次看到的擦掉——断线后第一个看到的仍算变化', async () => {
    const { container } = mountPanel()
    await flush()
    await fireEvent.click(previewButton(container))
    await flush()

    // 第一轮兜底：看到 a1，记住它当基线。
    await vi.advanceTimersByTimeAsync(POLL_MS)
    await flush()

    // 第二轮断线：这一问失败（失败不是「没有预览」，这一轮的 id 不该拿去当基线）。
    getPreview.mockRejectedValueOnce(new Error('offline'))
    await vi.advanceTimersByTimeAsync(POLL_MS)
    await flush()
    const afterRejected = getPreview.mock.calls.length

    // 第三轮回来了，看到换成了 a2。
    getPreview.mockResolvedValue({ path: 'report.html', mime: 'text/html', artifact_id: 'a2' })
    await vi.advanceTimersByTimeAsync(POLL_MS)
    await flush()

    // 失败那一问没把 a1 擦成「没看过」——a2 仍被当成一次更换：兜底那一下不只要问，还
    // 要让预览跟着重取（比这一轮自己「只问一次」多出一次）。
    expect(getPreview.mock.calls.length).toBeGreaterThan(afterRejected + 1)
  })
})
