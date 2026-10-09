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

import i18n, { setLocale } from '../../i18n'

// The document's version history: the last edit is read on open; none here.
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
    // 总览里「进度」那一段会读它；这里不关心它，给一份空的。
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
    // 规则 1: the tabs a topic offers follow what it actually holds. These suites
    // are about the tabs' CONTENT, so they mount a topic that holds everything.
    getTopicWorkSummary: vi.fn().mockResolvedValue({ changed_files: ['a.py'], has_run: true }),
  }
})

import WorkPanel from '../WorkPanel.vue'

import { prefetchPreview } from '@/query/room'

function topic(id: string): Topic {
  return { id, project_id: 'p1', title: `话题 ${id}`, status: 'active' } as Topic
}

// 预览只长在任务上：面板画的是房间 topic-A 里的任务 task-1，预览问的也是它。
function mountPanel(working = false) {
  const vuetify = createVuetify({ components, directives })
  return render(WorkPanel, {
    props: { topic: topic('topic-A'), taskId: 'task-1', activityTick: 0, working },
    global: { plugins: [vuetify, i18n] },
  })
}

/** 拿到面板暴露出去的那几个方法（`defineExpose`）。根元素上就挂着它的实例。 */
function panelApi(container: Element): { previewShown?: () => void } {
  const inst = (
    container.firstElementChild as HTMLElement & {
      __vueParentComponent?: { exposed: { previewShown?: () => void } }
    }
  ).__vueParentComponent
  return inst?.exposed ?? {}
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

describe('预览面板：运行中的应用到不了的时候说什么', () => {
  beforeEach(() => setLocale('zh-CN'))
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

    expect(container.textContent).toContain('预览连接尚未建立或已断开')
    expect(container.textContent).toContain('不代表芝士已停止工作')
    expect(container.textContent).not.toContain('再 @')
  })

  it('通道在、应用没有响应 → 请芝士检查应用', async () => {
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

    expect(container.textContent).toContain('应用预览暂不可用')
    expect(container.textContent).toContain('预览连接正常，但应用没有响应')
    expect(container.textContent).not.toContain('再 @')
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
    expect(requestPreviewSession).toHaveBeenCalledWith('task-1', undefined)
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
    expect(requestPreviewSession).toHaveBeenCalledWith('task-1', {
      artifact_id: 'a1',
      path: 'report.html',
      version: undefined,
    })
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
      version: 'v1',
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
    await rerender({ topic: topic('topic-A'), taskId: 'task-1', activityTick: 0, working: false })
    await flush()

    expect(getPreview.mock.calls.length).toBeGreaterThan(callsBefore)
    // 同一个 DOM 节点 = 没有卸载重建 = 产物没有重载。
    expect(container.querySelector('iframe.preview-frame')).toBe(frame)
    expect(HTMLFormElement.prototype.submit).toHaveBeenCalledOnce()
  })

  it('unknown static versions navigate again rather than claiming unchanged content', async () => {
    getPreview.mockResolvedValue({ path: 'report.html', mime: 'text/html', artifact_id: 'a1' })
    const { container, rerender } = mountPanel(true)
    await flush()
    await openPreview(container)
    const frame = container.querySelector('iframe.preview-frame')
    await rerender({ topic: topic('topic-A'), taskId: 'task-1', activityTick: 0, working: false })
    await flush()
    expect(container.querySelector('iframe.preview-frame')).not.toBe(frame)
    expect(HTMLFormElement.prototype.submit).toHaveBeenCalledTimes(2)
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
    await rerender({ topic: topic('topic-A'), taskId: 'task-1', activityTick: 0, working: false })
    await flush()

    expect(previewButton(container).getAttribute('title')).toContain('有新内容')
  })
})

describe('预览面板：芝士摆出来时立刻跟上', () => {
  it('对话栏报了一声 → 不等轮询，马上重看一眼当前预览，提示就冒出来', async () => {
    getPreview.mockResolvedValue({ path: 'report.html', mime: 'text/html', artifact_id: 'a1' })
    const { container } = mountPanel(true)
    await flush()
    expect(previewButton(container).getAttribute('title')).toBe('预览')

    // 芝士 `cheese show` 了一份新的：对话栏从 socket 上收到那块卡，往上报这一声。
    getPreview.mockResolvedValue({ path: 'report.html', mime: 'text/html', artifact_id: 'a2' })
    panelApi(container).previewShown?.()
    await flush()
    expect(previewButton(container).getAttribute('title')).toContain('有新内容')
  })

  it('路由守卫已经问过的那一份，挂上来时先读它——不再多发一条请求', async () => {
    // 守卫先起头的效果：面板挂上来时，答案已经在缓存里。
    getPreview.mockResolvedValue({ path: 'report.html', mime: 'text/html', artifact_id: 'a1' })
    await prefetchPreview('task-1')
    getPreview.mockClear()
    const { container } = mountPanel()
    await flush()
    // 面板读到了缓存里那一份：没有为它再问一次。
    expect(getPreview).not.toHaveBeenCalled()
    // 有预览在，但它是「来之前就有的」——开场不该顶着提示。
    expect(previewButton(container).getAttribute('title')).toBe('预览')
  })

  it('守卫先问过的那一份，开预览时直接拿来渲染——不再等一轮网络', async () => {
    // 路由守卫已经替这个房间把指针取回来了（router/index.ts）：答案在手边。
    getPreview.mockResolvedValue({ path: 'report.html', mime: 'text/html', artifact_id: 'a1' })
    await prefetchPreview('task-1')
    getPreview.mockClear()
    readFile.mockResolvedValue({ path: 'report.html', content: '<p>cached</p>' })
    const { container } = mountPanel()
    await flush()
    await openPreview(container)

    // 渲染用的是那份现成的答案——没有为它再发一条 /preview（否则那轮网络又压回挂载
    // 之后，守卫先起头就白起了）。
    expect(getPreview).not.toHaveBeenCalled()
    // 而且它真按缓存里那份指针去读了内容，不是空态。
    expect(readFile).toHaveBeenCalledWith('task-1')
  })

  it('同一份东西被重复摆一次：指针没换就不多取一次预览、不惊动别的格', async () => {
    getPreview.mockResolvedValue({ path: 'report.html', mime: 'text/html', artifact_id: 'a1' })
    const { container } = mountPanel(true)
    await flush()
    await openPreview(container)

    const previewCalls = getPreview.mock.calls.length
    // 指针还是 a1（同一份被又摆了一次）：再报一声。previewShown 只问一次才可能知道
    // 它没换。
    panelApi(container).previewShown?.()
    await flush()

    // 就多那一次「问一下指针」——指针没换，预览那一格不该被 refreshTick 叫去重取。
    expect(getPreview.mock.calls.length).toBe(previewCalls + 1)
  })
})
