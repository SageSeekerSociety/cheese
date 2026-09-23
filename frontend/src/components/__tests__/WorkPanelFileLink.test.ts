/** 消息里点一个文件，落在哪一格。
 *
 * 一枚 `<&路径>` chip 只是一个路径，不带它在哪个库，而一个房间有三个库：房间自己的
 * 文件（芝士交付的、人传上来的，没有分支也没有历史）、某个任务的工作树、项目当前
 * 代码。所以要先弄清文件在哪，再决定开哪一格。
 *
 * 反过来做——先切到改动再去找——读者点一个芝士刚做出来的文件，看到的是改动那一格
 * 凭空出现、画出一份不含这个文件的列表，然后被一句读取失败顶掉。那个文件从来没有
 * 进过任何一棵树。
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
vi.mock('../panels/preview/PreviewPages.vue', () => ({
  default: { name: 'PreviewPages', props: ['data'], template: '<div class="stub-pages" />' },
}))

const getDoc = vi.fn()
const readPreviewFile = vi.fn()
const listFiles = vi.fn()
const readFile = vi.fn()
const previewDocumentPdf = vi.fn()
const documentRevisions = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getDoc: (...a: unknown[]) => getDoc(...a),
    readPreviewFile: (...a: unknown[]) => readPreviewFile(...a),
    listFiles: (...a: unknown[]) => listFiles(...a),
    readFile: (...a: unknown[]) => readFile(...a),
    previewDocumentPdf: (...a: unknown[]) => previewDocumentPdf(...a),
    documentRevisions: (...a: unknown[]) => documentRevisions(...a),
    getPreview: vi.fn().mockResolvedValue(null),
    requestPreviewSession: vi.fn().mockResolvedValue({ url: 'https://p.example/s', grant: 'g' }),
    putDoc: vi.fn().mockResolvedValue({}),
    getComments: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getDocNodes: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getGitLog: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getGitDiff: vi.fn().mockResolvedValue({ diff: '' }),
    getTranscript: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTerminal: vi.fn().mockResolvedValue({ available: false }),
    getTopicUsage: vi.fn().mockResolvedValue(null),
    getProjectUsage: vi.fn().mockResolvedValue(null),
    listRoomOutputs: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listRoomTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    listRoomTrees: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTopicWorkSummary: vi.fn().mockResolvedValue({ changed_files: ['a.py'], has_run: true }),
  }
})

import WorkPanel from '../WorkPanel.vue'

function topic(id: string): Topic {
  return { id, project_id: 'p1', title: `话题 ${id}`, status: 'active' } as Topic
}

function mountPanel() {
  const vuetify = createVuetify({ components, directives })
  return render(WorkPanel, {
    props: { topic: topic('topic-A'), activityTick: 0 },
    global: { plugins: [vuetify] },
  })
}

async function flush() {
  for (let i = 0; i < 12; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 选中的那一格叫什么。 */
function selectedTab(container: Element): string | undefined {
  return container.querySelector('[role="tab"][aria-selected="true"]')?.textContent?.trim()
}

/** 自由区里开着哪几份，按页签顺序；临时的那一格带个 `~`。 */
function freeTabs(container: Element): string[] {
  return Array.from(container.querySelectorAll('.tabbar__file')).map(
    (el) => (el.classList.contains('tabbar__file--temp') ? '~' : '') + el.querySelector('.tabbar__name')?.textContent
  )
}

function visible(container: Element, selector: string): boolean {
  const el = container.querySelector<HTMLElement>(selector)
  return !!el && el.style.display !== 'none'
}

async function clickChip(container: Element, path: string) {
  const chip = container.querySelector<HTMLElement>('.doc-editor .mention.file-ref')
  expect(chip, '文档里没渲染出 chip').toBeTruthy()
  expect(chip!.dataset.file).toBe(path)
  await fireEvent.click(chip!)
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
  getDoc.mockResolvedValue({ content: '' })
  listFiles.mockResolvedValue({ data: [{ path: 'src/b.ts', bytes: 20 }], total: 1 })
  readFile.mockResolvedValue({
    path: 'src/b.ts',
    content: 'export const b = 1\n',
    version: 'v1',
    bytes: 19,
    binary: false,
    too_large: false,
  })
  readPreviewFile.mockRejectedValue(new Error('file not found'))
  previewDocumentPdf.mockResolvedValue(new ArrayBuffer(2048))
  documentRevisions.mockResolvedValue({ path: '', version: 'v1', revisions: [] })
})

describe('点一个文件，落在它真的在的那一格', () => {
  it('芝士交付的那份文档 → 开成它自己的页签，改动那一格没被切过去', async () => {
    getDoc.mockResolvedValue({ content: '初稿见 <&报告.docx>\n' })
    readPreviewFile.mockResolvedValue({
      path: '报告.docx',
      content: null,
      version: 'v1',
      bytes: 38705,
      binary: true,
      too_large: false,
    })

    const { container } = mountPanel()
    await flush()
    await clickChip(container, '报告.docx')

    expect(readPreviewFile).toHaveBeenCalledWith('topic-A', '报告.docx')
    expect(selectedTab(container)).toBe('报告.docx')
    expect(visible(container, '.panel-preview')).toBe(true)
    expect(visible(container, '.panel-changes')).toBe(false)
    // 房间文件不在任何一棵树上，所以这条路上一次树的读取都不该发生。
    expect(readFile).not.toHaveBeenCalled()
  })

  it('工作树里的源文件 → 照旧开在改动上', async () => {
    getDoc.mockResolvedValue({ content: '详见 <&src/b.ts>\n' })

    const { container } = mountPanel()
    await flush()
    await clickChip(container, 'src/b.ts')

    expect(visible(container, '.panel-changes')).toBe(true)
    expect(readFile).toHaveBeenCalledWith('p1', 'src/b.ts', 'topic-A', null, 'committed')
    // 不是文档也不是图片，预览显示不了它，所以连问都不问。
    expect(readPreviewFile).not.toHaveBeenCalled()
  })

  it('哪个库里都没有 → 说一句它不在这里，不发那次会失败的读取', async () => {
    getDoc.mockResolvedValue({ content: '旧稿在 <&归档/老稿.docx>\n' })

    const { container } = mountPanel()
    await flush()
    await clickChip(container, '归档/老稿.docx')

    expect(visible(container, '.panel-changes')).toBe(true)
    expect(container.querySelector('[data-testid="missing-file"]')?.textContent).toContain('老稿.docx')
    // 面板还在用：文件树照常显示，读者可以换来源或者挑别的文件。
    expect(container.querySelector('.file-list')).toBeTruthy()
    expect(readFile).not.toHaveBeenCalled()
  })

  it('仓库树里的 .html 去「改动」：那是源码，不是房间里的那一份', async () => {
    getDoc.mockResolvedValue({ content: '源码见 <&site/index.html>\n' })

    const { container } = mountPanel()
    await flush()
    await clickChip(container, 'site/index.html')

    // 房间里没有它（读取失败），所以它落在树上——.html 在那边是 diff，不是网页。
    expect(visible(container, '.panel-changes')).toBe(true)
    expect(selectedTab(container)).toContain('改动')
  })
})

// 自由区：读者自己开的那几份。固定区的几格永远在；这几格是他开的，也由他关。
describe('自由区', () => {
  function doc(path: string) {
    return { path, content: null, version: 'v1', bytes: 1024, binary: true, too_large: false }
  }

  beforeEach(() => {
    readPreviewFile.mockImplementation(async (_t: string, path: string) => doc(path))
  })

  it('单击开的是临时位：下一份换掉它，而不是再开一格', async () => {
    getDoc.mockResolvedValue({ content: '见 <&一.docx> 和 <&二.docx>\n' })
    const { container } = mountPanel()
    await flush()

    const chips = container.querySelectorAll<HTMLElement>('.doc-editor .mention.file-ref')
    await fireEvent.click(chips[0])
    await flush()
    expect(freeTabs(container)).toEqual(['~一.docx'])

    await fireEvent.click(chips[1])
    await flush()
    expect(freeTabs(container)).toEqual(['~二.docx'])
    expect(selectedTab(container)).toBe('二.docx')
  })

  it('双击固定之后，再开一份就排在它后面', async () => {
    getDoc.mockResolvedValue({ content: '见 <&一.docx> 和 <&二.docx>\n' })
    const { container } = mountPanel()
    await flush()
    const chips = container.querySelectorAll<HTMLElement>('.doc-editor .mention.file-ref')
    await fireEvent.click(chips[0])
    await flush()

    await fireEvent.dblClick(container.querySelector('.tabbar__file [role="tab"]')!)
    await fireEvent.click(chips[1])
    await flush()

    expect(freeTabs(container)).toEqual(['一.docx', '~二.docx'])
  })

  it('关掉正看着的那一格，落到它旁边那一格；都关了回总览', async () => {
    getDoc.mockResolvedValue({ content: '见 <&一.docx> 和 <&二.docx>\n' })
    const { container } = mountPanel()
    await flush()
    const chips = container.querySelectorAll<HTMLElement>('.doc-editor .mention.file-ref')
    await fireEvent.click(chips[0])
    await flush()
    await fireEvent.dblClick(container.querySelector('.tabbar__file [role="tab"]')!)
    await fireEvent.click(chips[1])
    await flush()

    await fireEvent.click(container.querySelector('[aria-label="关闭 二.docx"]')!)
    await flush()
    expect(freeTabs(container)).toEqual(['一.docx'])
    expect(selectedTab(container)).toBe('一.docx')

    await fireEvent.click(container.querySelector('[aria-label="关闭 一.docx"]')!)
    await flush()
    expect(freeTabs(container)).toEqual([])
    expect(selectedTab(container)).toContain('总览')
  })

  it('地址点名了一份文件，打开房间就开着它', async () => {
    const vuetify = createVuetify({ components, directives })
    const { container } = render(WorkPanel, {
      props: { topic: topic('topic-A'), activityTick: 0, tab: 'file:报告.docx' },
      global: { plugins: [vuetify] },
    })
    await flush()

    expect(freeTabs(container)).toEqual(['~报告.docx'])
    expect(selectedTab(container)).toBe('报告.docx')
    expect(readPreviewFile).toHaveBeenCalledWith('topic-A', '报告.docx')
  })

  it('房间里的网页开成它自己的页签：内容域按路径画得了它', async () => {
    getDoc.mockResolvedValue({ content: '成品见 <&site/index.html>\n' })
    readPreviewFile.mockResolvedValue({
      path: 'site/index.html',
      content: '<h1>成品</h1>',
      version: 'v1',
      bytes: 15,
      binary: false,
      too_large: false,
    })
    const { container } = mountPanel()
    await flush()

    await clickChip(container, 'site/index.html')

    expect(freeTabs(container)).toEqual(['~index.html'])
    expect(selectedTab(container)).toBe('index.html')
  })

  it('仓库树里的 .html 仍然去「改动」那格：那是源码，不是房间里的那一份', async () => {
    getDoc.mockResolvedValue({ content: '源码见 <&site/index.html>\n' })
    const { container } = mountPanel()
    await flush()

    await clickChip(container, 'site/index.html')

    expect(freeTabs(container)).toEqual(['~index.html'])
    expect(selectedTab(container)).toBe('index.html')
  })

  it('能画出来的文件就算是当前预览，也开成自己的页签', async () => {
    const { getPreview } = await import('../../api')
    vi.mocked(getPreview).mockResolvedValue({
      kind: 'file',
      path: '报告.docx',
      mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      url: 'https://p.example/',
      artifact_id: 'a1',
      version: 'v1',
    } as Awaited<ReturnType<typeof getPreview>>)
    getDoc.mockResolvedValue({ content: '初稿见 <&报告.docx>\n' })
    const { container } = mountPanel()
    await flush()

    await clickChip(container, '报告.docx')

    expect(freeTabs(container)).toEqual(['~报告.docx'])
    vi.mocked(getPreview).mockResolvedValue(null)
  })
})
