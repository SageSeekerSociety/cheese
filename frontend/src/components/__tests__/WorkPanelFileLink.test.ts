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

import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api'
import i18n, { setLocale } from '@/i18n'

// 断言读的是中文界面上的那一行字，语言钉在中文上。
beforeEach(() => setLocale('zh-CN'))

// The document's version history: the last edit is read on open; none here.

// 文档的链接那一侧（#3827）：打开文档时问一次被引用、打 # 时问一次能引用的文档。这里不关心。
vi.mock('@/api/docLinks', () => ({ getDocumentBacklinks: async () => ({ links_to: [], linked_from: [] }) }))
vi.mock('@/lib/linkableDocuments', () => ({ linkableDocuments: async () => [] }))
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
vi.mock('../panels/preview/PreviewPages.vue', () => ({
  default: { name: 'PreviewPages', props: ['data'], template: '<div class="stub-pages" />' },
}))
// 分页文档开在预览格里时会读这一份的评论串（页上要画已有的评论标记）。这些用例问的
// 是文件落在哪一格，不是评论，给一份空的。
vi.mock('@/api/fileThreads', () => ({ listFileThreads: async () => ({ data: [], total: 0 }) }))

const readPreviewFile = vi.fn()
const listFiles = vi.fn()
const readFile = vi.fn()
const previewDocumentPdfSnapshot = vi.fn()
const documentRevisions = vi.fn()

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
    // 概览里「进度」那一段会读它；这里不关心它，给一份空的。
    getProgress: vi.fn().mockResolvedValue({ items: [], updated_at: null }),
    readPreviewFile: (...a: unknown[]) => readPreviewFile(...a),
    listFiles: (...a: unknown[]) => listFiles(...a),
    readFile: (...a: unknown[]) => readFile(...a),
    previewDocumentPdfSnapshot: (...a: unknown[]) => previewDocumentPdfSnapshot(...a),
    documentRevisions: (...a: unknown[]) => documentRevisions(...a),
    // 房间里的文档一打开就读它的保存历史（文档条上的版本行）。这些用例问的是文件落在
    // 哪一格，不是版本：给一份空清单，不摆版本行。
    roomFileRevisions: vi.fn().mockResolvedValue({ data: [], total: 0, path: '', version: null }),
    getPreview: vi.fn().mockResolvedValue(null),
    requestPreviewSession: vi.fn().mockResolvedValue({ url: 'https://p.example/s', grant: 'g' }),
    getDocNodes: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getForgeConnection: vi.fn().mockResolvedValue({ kind: 'forgejo', connected: true, repo: 'o/r', url: null }),
    getGitDiff: vi.fn().mockResolvedValue({ diff: '' }),
    getTranscript: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getAgentControl: vi.fn().mockResolvedValue({ id: null, connected: false, tasks: {} }),
    getTopicUsage: vi.fn().mockResolvedValue(null),
    getProjectUsage: vi.fn().mockResolvedValue(null),
    listRoomOutputs: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    // 面板画的是房间 topic-A 里的任务 task-1。
    listRoomTasks: vi.fn().mockResolvedValue({
      data: [
        {
          id: 'task-1',
          project_id: 'p1',
          room_id: 'topic-A',
          title: '写报告',
          status: 'open',
          branch_name: 'task/report',
          presentation: { column: 'building', phrase: 'running' },
          blocks: [],
          created_at: '2026-09-09T00:00:00Z',
          updated_at: '2026-09-09T00:00:00Z',
        },
      ],
      total: 1,
    }),
    listRoomTrees: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTopicWorkSummary: vi.fn().mockResolvedValue({ changed_files: ['a.py'], has_run: true }),
  }
})

import { seedRoom } from '../../test/fakeDocCollab'
import PanelDocHost from '../work/PanelDocHost.vue'
import WorkPanel from '../WorkPanel.vue'

// 任务概览里的实况文档：它的正文在协同文档里，按任务的 id 认。
function docSays(content: string, taskId = 'task-1') {
  seedRoom(taskId, content)
}

function topic(id: string): Topic {
  return { id, project_id: 'p1', title: `话题 ${id}`, status: 'active' } as Topic
}

/** 任务页上的面板：概览里是任务的实况文档，文档里点的 chip 交给面板去开——和任务页
 *  接的是同一根线。 */
function mountPanel(opts: { taskId?: string; taskHasBranch?: boolean } = {}) {
  const vuetify = createVuetify({ components, directives })
  const taskId = opts.taskId ?? 'task-1'
  let panel: { openFile: (path: string) => Promise<void> } | null = null
  const room = topic('topic-A')
  const Host = defineComponent(
    () => () =>
      h(
        WorkPanel,
        {
          ref: (instance: unknown) => {
            panel = instance as typeof panel
          },
          topic: room,
          taskId,
          taskHasBranch: opts.taskHasBranch ?? true,
          activityTick: 0,
        },
        {
          overview: () =>
            h(PanelDocHost, {
              flow: true,
              topic: room,
              taskId,
              activityTick: 0,
              onOpenFile: (path: string) => void panel?.openFile(path),
            }),
        }
      )
  )
  return render(Host, { global: { plugins: [vuetify, i18n] } })
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

// 文档那一格是异步组件（编辑器不挡房间首屏），等它把 chip 画出来。
async function docChips(container: Element): Promise<NodeListOf<HTMLElement>> {
  return vi.waitFor(
    () => {
      const chips = container.querySelectorAll<HTMLElement>('.doc-editor .mention.file-ref')
      expect(chips.length, '文档里没渲染出 chip').toBeGreaterThan(0)
      return chips
    },
    { timeout: 5000 }
  )
}

async function clickChip(container: Element, path: string) {
  const chip = (await docChips(container))[0]
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
  docSays('')
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
  previewDocumentPdfSnapshot.mockResolvedValue({ bytes: new ArrayBuffer(2048), sourceVersion: null })
  documentRevisions.mockResolvedValue({ path: '', version: 'v1', revisions: [] })
})

describe('点一个文件，落在它真的在的那一格', () => {
  it('芝士交付的那份文档 → 开成它自己的页签，改动那一格没被切过去', async () => {
    docSays('初稿见 <&报告.docx>\n')
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
    docSays('详见 <&src/b.ts>\n')

    const { container } = mountPanel()
    await flush()
    await clickChip(container, 'src/b.ts')

    expect(visible(container, '.panel-changes')).toBe(true)
    // 任务页上，文件就是这件任务工作树上的那一份。
    expect(readFile).toHaveBeenCalledWith('p1', 'src/b.ts', 'topic-A', 'task-1', 'live')
    // 不是文档也不是图片，预览显示不了它，所以连问都不问。
    expect(readPreviewFile).not.toHaveBeenCalled()
  })

  it('这件任务的工作副本里没有它 → 退到「项目当前版本」那一格开它', async () => {
    // 这一支的清单里只有 src/b.ts（见 beforeEach）：老稿不是它改的。读者指名要看，
    // 就不该因为这份文件没归到这件任务而点不开 —— 换项目当前版本那一格开。
    readFile.mockImplementation(
      async (_pid: unknown, path: unknown, _tid: unknown, _task: unknown, source: unknown) => {
        // 项目当前版本里也没有它。那一格自己会说，并列出改过它的任务。
        if (source === 'committed') throw new ApiError(404, 'file not found')
        return { path, content: 'export const b = 1\n', version: 'v1', bytes: 19, binary: false, too_large: false }
      }
    )
    docSays('旧稿在 <&归档/老稿.docx>\n')

    const { container } = mountPanel()
    await flush()
    await clickChip(container, '归档/老稿.docx')

    expect(visible(container, '.panel-changes')).toBe(false)
    // 自由区那一格按文件名认。
    expect(selectedTab(container)).toBe('老稿.docx')
    expect(container.querySelector('[data-testid="project-file"]')).toBeTruthy()
    await vi.waitFor(() => expect(container.querySelector('[data-testid="project-file-missing"]')).toBeTruthy())
    // 工作副本里没有它：那次会失败的读取一次都不发。
    expect(readFile).not.toHaveBeenCalledWith('p1', '归档/老稿.docx', 'topic-A', 'task-1', 'live')
  })

  it('还没有分支的任务（没有机器打开过它）→ 也退到项目当前版本', async () => {
    // 这样的任务没有工作区，「改动」那一格只能说他打不开这件任务。文件照样要点得开。
    readFile.mockImplementation(
      async (_pid: unknown, path: unknown, _tid: unknown, _task: unknown, source: unknown) => {
        if (source === 'committed') throw new ApiError(404, 'file not found')
        return { path, content: 'export const b = 1\n', version: 'v1', bytes: 19, binary: false, too_large: false }
      }
    )
    docSays('旧稿在 <&归档/老稿.docx>\n', 'task-new')

    const { container } = mountPanel({ taskId: 'task-new', taskHasBranch: false })
    await flush()
    await clickChip(container, '归档/老稿.docx')

    expect(visible(container, '.panel-changes')).toBe(false)
    expect(container.querySelector('[data-testid="project-file"]')).toBeTruthy()
    expect(readFile).not.toHaveBeenCalledWith('p1', '归档/老稿.docx', 'topic-A', 'task-new', 'live')
  })

  it('这件任务的文件清单取不到 → 也退到项目当前版本，不停在开不出它的「改动」上', async () => {
    listFiles.mockRejectedValue(new Error('upstream unavailable'))
    readFile.mockResolvedValue({
      path: 'src/b.ts',
      content: 'export const b = 1\n',
      version: 'v1',
      bytes: 19,
      binary: false,
      too_large: false,
    })
    docSays('详见 <&src/b.ts>\n')

    const { container } = mountPanel()
    await flush()
    await clickChip(container, 'src/b.ts')

    expect(visible(container, '.panel-changes')).toBe(false)
    expect(selectedTab(container)).toBe('b.ts')
    expect(container.querySelector('[data-testid="project-file"]')).toBeTruthy()
  })

  it('仓库树里的 .html 去「改动」：那是源码，不是房间里的那一份', async () => {
    // 这一支的工作副本里有它：.html 在那边是源码，该开在改动那格看 diff，不是当网页画。
    listFiles.mockResolvedValue({ data: [{ path: 'site/index.html', bytes: 40 }], total: 1 })
    docSays('源码见 <&site/index.html>\n')

    const { container } = mountPanel()
    await flush()
    await clickChip(container, 'site/index.html')

    // 房间里没有它（读取失败），所以它落在树上。
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
    docSays('见 <&一.docx> 和 <&二.docx>\n')
    const { container } = mountPanel()
    await flush()

    const chips = await docChips(container)
    await fireEvent.click(chips[0])
    await flush()
    expect(freeTabs(container)).toEqual(['~一.docx'])

    await fireEvent.click(chips[1])
    await flush()
    expect(freeTabs(container)).toEqual(['~二.docx'])
    expect(selectedTab(container)).toBe('二.docx')
  })

  it('双击固定之后，再开一份就排在它后面', async () => {
    docSays('见 <&一.docx> 和 <&二.docx>\n')
    const { container } = mountPanel()
    await flush()
    const chips = await docChips(container)
    await fireEvent.click(chips[0])
    await flush()

    await fireEvent.dblClick(container.querySelector('.tabbar__file [role="tab"]')!)
    await fireEvent.click(chips[1])
    await flush()

    expect(freeTabs(container)).toEqual(['一.docx', '~二.docx'])
  })

  it('关掉正看着的那一格，落到它旁边那一格；都关了回概览', async () => {
    docSays('见 <&一.docx> 和 <&二.docx>\n')
    const { container } = mountPanel()
    await flush()
    const chips = await docChips(container)
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
    expect(selectedTab(container)).toContain('概览')
  })

  it('地址点名了一份文件，打开任务就开着它', async () => {
    const vuetify = createVuetify({ components, directives })
    const { container } = render(WorkPanel, {
      props: { topic: topic('topic-A'), taskId: 'task-1', activityTick: 0, tab: 'file:报告.docx' },
      global: { plugins: [vuetify, i18n] },
    })
    await flush()

    expect(freeTabs(container)).toEqual(['~报告.docx'])
    expect(selectedTab(container)).toBe('报告.docx')
    expect(readPreviewFile).toHaveBeenCalledWith('task-1', '报告.docx')
  })

  it('房间里的网页开成它自己的页签：内容域按路径画得了它', async () => {
    docSays('成品见 <&site/index.html>\n')
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
    docSays('源码见 <&site/index.html>\n')
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
    docSays('初稿见 <&报告.docx>\n')
    const { container } = mountPanel()
    await flush()

    await clickChip(container, '报告.docx')

    expect(freeTabs(container)).toEqual(['~报告.docx'])
    vi.mocked(getPreview).mockResolvedValue(null)
  })
})
