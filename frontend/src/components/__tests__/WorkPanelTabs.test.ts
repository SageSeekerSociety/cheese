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
const getTopicWorkSummary = vi.fn()
const addComment = vi.fn()
const getComments = vi.fn()

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
    getTopicWorkSummary: (...a: unknown[]) => getTopicWorkSummary(...a),
    addComment: (...a: unknown[]) => addComment(...a),
    getComments: (...a: unknown[]) => getComments(...a),
    putDoc: vi.fn().mockResolvedValue({}),
    writeFile: vi.fn().mockResolvedValue({ path: 'a.py', version: 'v2' }),
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

function mountPanel(id = 'topic-A', props: Record<string, unknown> = {}) {
  const vuetify = createVuetify({ components, directives })
  return render(WorkPanel, {
    props: { topic: topic(id), activityTick: 0, ...props },
    global: { plugins: [vuetify] },
  })
}

async function flush() {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function buttons(container: Element): HTMLButtonElement[] {
  return Array.from(container.querySelectorAll('button'))
}
function findTab(container: Element, label: string): HTMLButtonElement | undefined {
  return buttons(container).find((b) => b.getAttribute('title')?.startsWith(label) && b.closest('.tabbar'))
}
function tabButton(container: Element, label: string): HTMLButtonElement {
  const btn = findTab(container, label)
  expect(btn, `找不到 ${label} tab`).toBeTruthy()
  return btn!
}
function tabLabels(container: Element): string[] {
  return Array.from(container.querySelectorAll('.tabbar__tab')).map((b) => b.textContent?.trim() ?? '')
}
async function openTab(container: Element, label: string) {
  await fireEvent.click(tabButton(container, label))
  await flush()
}
/** A tab pane is on screen when it is not the one v-show hid.
 *
 *  文档 那一格现在是 总览 的下半边（上半边是 Task Progress），所以「文档在不在
 *  屏幕上」问的是 `.panel-overview` —— v-show 挂在它身上，里面的 `.doc` 从头到
 *  尾都在。 */
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
  // A topic 芝士 has worked in: the default for the suites about tab CONTENT.
  getTopicWorkSummary.mockResolvedValue({ changed_files: ['a.py'], has_run: true })
  getComments.mockResolvedValue({ data: [], total: 0 })
  addComment.mockResolvedValue({ id: 'c1' })
})

describe('工作面板 · Tab 容器', () => {
  it('默认停在总览', async () => {
    const { container } = mountPanel()
    await flush()
    expect(visible(container, '.panel-overview')).toBe(true)
  })

  it('每个 tab 都切得过去，而且真的渲染出了自己那一片', async () => {
    const { container } = mountPanel()
    await flush()

    await openTab(container, '现场')
    expect(visible(container, '.panel-site')).toBe(true)
    expect(visible(container, '.panel-overview')).toBe(false)
    expect(getTranscript).toHaveBeenCalled()
    expect(container.textContent).toContain('暂无现场记录')

    await openTab(container, '改动')
    expect(visible(container, '.panel-changes')).toBe(true)
    expect(getGitDiff).toHaveBeenCalled()
    expect(container.querySelector('.file-list'), '改动 tab 没渲染出文件树').toBeTruthy()

    // 回到文档：编辑器还在（它从头到尾没被卸载过，切走一趟不会重建 tiptap）。
    await openTab(container, '总览')
    expect(visible(container, '.panel-overview')).toBe(true)
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
    expect(visible(container, '.panel-overview')).toBe(false)
    // ……而且是它的文件半边，开着的正是被点的那个文件。
    expect(readFile).toHaveBeenCalledWith('p1', 'src/b.ts', 'topic-A')
    expect(container.querySelector('.file-bar__path')?.textContent?.trim()).toBe('src/b.ts')
  })

  // 规则 1: 能力不存在时，入口就不该存在。判定读的是「这个话题手上有什么」，
  // 不是它的 kind —— 后端给每个话题都建了 worktree 和分支，按 kind 分只是猜。
  it('谁也没在里面干过活的话题：只剩总览，连 tab 栏都不出现', async () => {
    getTopicWorkSummary.mockResolvedValue({ changed_files: [], has_run: false })
    const { container } = mountPanel()
    await flush()

    expect(container.querySelector('.tabbar')).toBeNull()
    expect(visible(container, '.panel-overview')).toBe(true)
  })

  it('跑过活但没产生改动：有现场，没有改动', async () => {
    getTopicWorkSummary.mockResolvedValue({ changed_files: [], has_run: true })
    const { container } = mountPanel()
    await flush()

    expect(tabLabels(container)).toEqual(['总览', '现场'])
  })

  it('第一轮还在跑、session 还没落库：现场立刻就在', async () => {
    getTopicWorkSummary.mockResolvedValue({ changed_files: [], has_run: false })
    const { container } = mountPanel('topic-A', { working: true })
    await flush()

    expect(findTab(container, '现场')).toBeTruthy()
  })

  it('芝士指定了预览，预览 tab 才出现', async () => {
    getTopicWorkSummary.mockResolvedValue({ changed_files: [], has_run: true })
    const { container } = mountPanel()
    await flush()
    expect(findTab(container, '预览')).toBeUndefined()

    getPreview.mockResolvedValue({ kind: 'file', path: 'r.html', mime: 'text/html', artifact_id: 'a1' })
    const { container: c2 } = mountPanel('topic-B')
    await flush()
    expect(findTab(c2, '预览')).toBeTruthy()
  })

  // 「信号上 Tab，不抢占视图」的另一半：正开着的 tab 不会在脚下消失。改动被
  // 采纳合走之后 changed_files 变空，此时把人正在读的 diff 关掉才是更坏的事。
  it('正开着的 tab 即使能力没了也不撤走', async () => {
    const { container, rerender } = mountPanel('topic-A')
    await flush()
    await openTab(container, '改动')

    getTopicWorkSummary.mockResolvedValue({ changed_files: [], has_run: true })
    await rerender({ topic: topic('topic-A'), activityTick: 0, working: true })
    await rerender({ topic: topic('topic-A'), activityTick: 0, working: false })
    await flush()

    expect(findTab(container, '改动')).toBeTruthy()
    expect(visible(container, '.panel-changes')).toBe(true)
  })

  // 「你在看什么」进 URL：面板只报告自己的动作，地址由 TopicView 持有——所以这里
  // 钉的是那份合同的两半（收 tab、发 update:tab），不是路由本身。
  it('地址点名了 tab 就开在那个 tab 上，不是总览', async () => {
    const { container } = mountPanel('topic-A', { tab: 'changes' })
    await flush()

    expect(visible(container, '.panel-changes')).toBe(true)
    expect(visible(container, '.panel-overview')).toBe(false)
  })

  it('地址给的 tab 不认识就退回总览，不是空面板', async () => {
    const { container } = mountPanel('topic-A', { tab: '资源' })
    await flush()
    expect(visible(container, '.panel-overview')).toBe(true)
  })

  it('切 tab 会把新的 tab 报出去，地址才跟得上', async () => {
    const { container, emitted } = mountPanel()
    await flush()
    await openTab(container, '改动')

    expect(emitted()['update:tab']).toContainEqual(['changes'])
  })

  it('从别处打开一个文件也算换 tab，一样报出去', async () => {
    getDoc.mockResolvedValue({ content: '详见 <&a.py> 这个文件\n' })
    const { container, emitted } = mountPanel()
    await flush()

    await fireEvent.click(container.querySelector<HTMLElement>('.doc-editor .mention.file-ref')!)
    await flush()

    expect(emitted()['update:tab']).toContainEqual(['changes'])
  })

  it('直接开在预览上，不会顶着一个「有新内容」的提示', async () => {
    getPreview.mockResolvedValue({ kind: 'file', path: 'r.html', mime: 'text/html', artifact_id: 'a1' })
    const { container } = mountPanel('topic-A', { tab: 'preview' })
    await flush()

    expect(tabButton(container, '预览').getAttribute('title')).toBe('预览')
  })

  // 规则 2: 信号上 Tab，不抢占视图。
  it('芝士开工时现场 tab 上有脉冲点，但视图不动', async () => {
    const { container, rerender } = mountPanel()
    await flush()
    expect(container.querySelector('.tabbar__pulse')).toBeNull()

    await rerender({ topic: topic('topic-A'), activityTick: 0, working: true })
    await flush()

    expect(container.querySelector('.tabbar__pulse')).toBeTruthy()
    expect(tabButton(container, '现场').getAttribute('title')).toContain('芝士正在工作')
    expect(visible(container, '.panel-overview')).toBe(true)
  })

  it('改动 tab 上是文件数；进话题时已有的改动不算「新」', async () => {
    getTopicWorkSummary.mockResolvedValue({ changed_files: ['a.py', 'b.py'], has_run: true })
    const { container } = mountPanel()
    await flush()

    const count = container.querySelector('.tabbar__count')
    expect(count?.textContent).toBe('2')
    expect(count?.classList.contains('tabbar__count--new')).toBe(false)
  })

  it('一轮跑完多出来的改动才是「新」的，而且不会把人切过去', async () => {
    const { container, rerender } = mountPanel()
    await flush()

    getTopicWorkSummary.mockResolvedValue({ changed_files: ['a.py', 'b.py'], has_run: true })
    await rerender({ topic: topic('topic-A'), activityTick: 0, working: true })
    await rerender({ topic: topic('topic-A'), activityTick: 0, working: false })
    await flush()

    expect(container.querySelector('.tabbar__count')?.classList.contains('tabbar__count--new')).toBe(true)
    expect(visible(container, '.panel-overview')).toBe(true)

    // 看过就不再是新的。
    await openTab(container, '改动')
    expect(container.querySelector('.tabbar__count')?.classList.contains('tabbar__count--new')).toBe(false)
  })

  // 规则 3: 打开话题那一刻不算抢占，所以这是面板唯一一次自己选 tab 的机会。
  // 阶段是异步到的（验收卡要先拉回来），所以它到之前 phase 是 undefined 而不是
  // 「没有卡」—— 否则待验收的话题会先停在文档上，再也不动。
  it('待验收的话题开在改动上', async () => {
    const { container, rerender } = mountPanel()
    await flush()
    expect(visible(container, '.panel-overview')).toBe(true)

    await rerender({ topic: topic('topic-A'), activityTick: 0, phase: 'reviewing' })
    await flush()

    expect(visible(container, '.panel-changes')).toBe(true)
  })

  it('芝士正干着的话题开在现场上', async () => {
    const { container, rerender } = mountPanel()
    await flush()
    await rerender({ topic: topic('topic-A'), activityTick: 0, working: true, phase: 'working' })
    await flush()

    expect(visible(container, '.panel-site')).toBe(true)
  })

  it('其余一律开在文档上', async () => {
    const { container, rerender } = mountPanel()
    await flush()
    await rerender({ topic: topic('topic-A'), activityTick: 0, phase: 'open' })
    await flush()

    expect(visible(container, '.panel-overview')).toBe(true)
  })

  it('地址点名了 tab 就以地址为准，阶段不许改它', async () => {
    const { container, rerender } = mountPanel('topic-A', { tab: 'doc' })
    await flush()
    await rerender({ topic: topic('topic-A'), activityTick: 0, tab: 'doc', phase: 'reviewing' })
    await flush()

    expect(visible(container, '.panel-overview')).toBe(true)
  })

  it('人已经自己选过了，晚到的阶段不许把他挪走', async () => {
    const { container, rerender } = mountPanel()
    await flush()
    await openTab(container, '现场')

    await rerender({ topic: topic('topic-A'), activityTick: 0, phase: 'reviewing' })
    await flush()

    expect(visible(container, '.panel-site')).toBe(true)
  })

  it('阶段后来变了也不动——只有打开那一刻算数', async () => {
    const { container, rerender } = mountPanel()
    await flush()
    await rerender({ topic: topic('topic-A'), activityTick: 0, phase: 'open' })
    await flush()

    await rerender({ topic: topic('topic-A'), activityTick: 0, phase: 'reviewing' })
    await flush()

    expect(visible(container, '.panel-overview')).toBe(true)
  })

  // 规则 5: 批注归批注，聊天归聊天。写评论的输入框长在评论区里，不再劫持底部那
  // 个共享输入栏——那个框在评论模式下发的不是消息，唯一的区别只是一颗 chip。
  it('写评论的输入框长在文档的评论区里，发出去的是评论', async () => {
    const { container } = mountPanel()
    await flush()

    expect(container.querySelector('.comment-draft')).toBeNull()
    const write = buttons(container).find((b) => b.getAttribute('title') === '写评论')
    expect(write, '评论区没有「写评论」入口').toBeTruthy()

    await fireEvent.click(write!)
    await flush()

    const box = container.querySelector<HTMLTextAreaElement>('.comment-draft textarea')
    expect(box, '评论区里没有输入框').toBeTruthy()
    await fireEvent.update(box!, '这段读不通')
    await fireEvent.keyDown(box!, { key: 'Enter' })
    await flush()

    expect(addComment).toHaveBeenCalledWith('topic-A', '这段读不通', expect.anything(), undefined, '')
    // 发完收起来，评论区回到只读的样子。
    expect(container.querySelector('.comment-draft')).toBeNull()
  })

  it('Esc 关掉评论输入框，不发任何东西', async () => {
    const { container } = mountPanel()
    await flush()
    await fireEvent.click(buttons(container).find((b) => b.getAttribute('title') === '写评论')!)
    await flush()

    await fireEvent.keyDown(container.querySelector('.comment-draft textarea')!, { key: 'Escape' })
    await flush()

    expect(container.querySelector('.comment-draft')).toBeNull()
    expect(addComment).not.toHaveBeenCalled()
  })

  it('换话题回到文档 tab（旧行为：切话题会把抽屉关掉）', async () => {
    const { container, rerender } = mountPanel('topic-A')
    await flush()
    await openTab(container, '改动')
    expect(visible(container, '.panel-changes')).toBe(true)

    await rerender({ topic: topic('topic-B'), activityTick: 0 })
    await flush()
    expect(visible(container, '.panel-overview')).toBe(true)
    expect(visible(container, '.panel-changes')).toBe(false)
  })
})
