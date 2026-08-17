/** 工作面板的 Tab 容器.
 *
 * 这个文件是切分那一刀的安全网。它钉的不是某个功能，而是「搬完之后每一片还在原
 * 处，片与片之间那几根线也还接着」：
 *
 *   1. 四个 tab 都切得过去、都渲染得出来（不是空壳）。
 *   2. 文档里的 <&path> chip 落到 改动 tab 并打开那个文件 —— 这是原来五个抽屉之间
 *      唯一做对了的联动（旧代码里那句 `openTool.value = 'files'`），也是切分之后
 *      唯一一根跨 tab 的线。它现在走 PanelDoc 的 open-file → 容器 → PanelChanges。
 *   3. ChatPanel 里的 <&path> chip 走同一根线（TopicView 调容器暴露的 openFile）。
 *   4. 换话题回到 文档 —— 旧行为是抽屉在切话题时关掉。
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

const getDoc = vi.fn()
const listFiles = vi.fn()
const readFile = vi.fn()
const getTranscript = vi.fn()
const getGitDiff = vi.fn()
const getPreview = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getDoc: (...a: unknown[]) => getDoc(...a),
    listFiles: (...a: unknown[]) => listFiles(...a),
    readFile: (...a: unknown[]) => readFile(...a),
    getTranscript: (...a: unknown[]) => getTranscript(...a),
    getGitDiff: (...a: unknown[]) => getGitDiff(...a),
    getPreview: (...a: unknown[]) => getPreview(...a),
    putDoc: vi.fn().mockResolvedValue({}),
    writeFile: vi.fn().mockResolvedValue({ path: 'a.py', version: 'v2' }),
    getComments: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getDocNodes: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTerminal: vi.fn().mockResolvedValue({ available: false, backend: 'none' }),
    getGitLog: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTopicUsage: vi.fn().mockResolvedValue(null),
    getProjectUsage: vi.fn().mockResolvedValue(null),
  }
})

import WorkPanel from '../WorkPanel.vue'

function topic(id: string): Topic {
  return { id, project_id: 'p1', title: `话题 ${id}`, status: 'active' } as Topic
}

function mountPanel(id = 'topic-A') {
  const vuetify = createVuetify({ components, directives })
  return render(WorkPanel, {
    props: { topic: topic(id), activityTick: 0 },
    global: { plugins: [vuetify] },
  })
}

async function flush() {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function buttons(container: Element): HTMLButtonElement[] {
  return Array.from(container.querySelectorAll('button'))
}
function tabButton(container: Element, label: string): HTMLButtonElement {
  const btn = buttons(container).find((b) => b.getAttribute('title')?.startsWith(label) && b.closest('.tabbar'))
  expect(btn, `找不到 ${label} tab`).toBeTruthy()
  return btn!
}
async function openTab(container: Element, label: string) {
  await fireEvent.click(tabButton(container, label))
  await flush()
}
/** A tab pane is on screen when it is not the one v-show hid. */
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
  getDoc.mockResolvedValue({ content: '' })
  listFiles.mockResolvedValue({
    data: [
      { path: 'a.py', bytes: 10 },
      { path: 'src/b.ts', bytes: 20 },
    ],
    total: 2,
  })
  readFile.mockResolvedValue({
    path: 'a.py',
    content: '内容\n',
    version: 'v1',
    bytes: 6,
    binary: false,
    too_large: false,
  })
  getTranscript.mockResolvedValue({ data: [], total: 0 })
  getGitDiff.mockResolvedValue({ diff: 'diff --git a/x b/x\n' })
  getPreview.mockResolvedValue(null)
})

describe('工作面板 · Tab 容器', () => {
  it('四个 tab 都在，默认停在文档', async () => {
    const { container } = mountPanel()
    await flush()
    for (const label of ['文档', '现场', '改动', '预览']) tabButton(container, label)
    expect(visible(container, '.doc')).toBe(true)
  })

  it('每个 tab 都切得过去，而且真的渲染出了自己那一片', async () => {
    const { container } = mountPanel()
    await flush()

    await openTab(container, '现场')
    expect(visible(container, '.panel-site')).toBe(true)
    expect(visible(container, '.doc')).toBe(false)
    expect(getTranscript).toHaveBeenCalled()
    expect(container.textContent).toContain('暂无现场记录')

    await openTab(container, '改动')
    expect(visible(container, '.panel-changes')).toBe(true)
    expect(getGitDiff).toHaveBeenCalled()
    expect(container.textContent).toContain('本话题改动（相对主干）')

    await openTab(container, '预览')
    expect(visible(container, '.panel-preview')).toBe(true)
    expect(container.textContent).toContain('暂无预览')

    // 回到文档：编辑器还在（它从头到尾没被卸载过，切走一趟不会重建 tiptap）。
    await openTab(container, '文档')
    expect(visible(container, '.doc')).toBe(true)
    expect(container.querySelector('.doc-editor')).toBeTruthy()
  })

  // 旧代码里 `openTool.value = 'files'` 那一句。切分之后它变成三段接力
  // (PanelDoc 的 open-file → 容器的 openFile → PanelChanges.openFile)，所以这条
  // 用例点的是文档里真实渲染出来的那颗 chip，走完整条线。
  it('文档里的 <&path> chip → 落到改动 tab 的文件半边，并打开那个文件', async () => {
    getDoc.mockResolvedValue({ content: '详见 <&src/b.ts> 这个文件\n' })
    readFile.mockResolvedValue({
      path: 'src/b.ts',
      content: 'export const b = 1\n',
      version: 'v1',
      bytes: 19,
      binary: false,
      too_large: false,
    })

    const { container } = mountPanel()
    await flush()

    const chip = container.querySelector<HTMLElement>('.doc-editor .mention.file-ref')
    expect(chip, '文档里没渲染出 <&path> chip').toBeTruthy()
    expect(chip!.dataset.file).toBe('src/b.ts')

    await fireEvent.click(chip!)
    await flush()

    // 落在改动 tab 上……
    expect(visible(container, '.panel-changes')).toBe(true)
    expect(visible(container, '.doc')).toBe(false)
    // ……而且是它的文件半边，开着的正是被点的那个文件。
    expect(readFile).toHaveBeenCalledWith('p1', 'src/b.ts', 'topic-A')
    expect(container.querySelector('.file-bar__path')?.textContent?.trim()).toBe('src/b.ts')
  })

  it('换话题回到文档 tab（旧行为：切话题会把抽屉关掉）', async () => {
    const { container, rerender } = mountPanel('topic-A')
    await flush()
    await openTab(container, '改动')
    expect(visible(container, '.panel-changes')).toBe(true)

    await rerender({ topic: topic('topic-B'), activityTick: 0 })
    await flush()
    expect(visible(container, '.doc')).toBe(true)
    expect(visible(container, '.panel-changes')).toBe(false)
  })
})
