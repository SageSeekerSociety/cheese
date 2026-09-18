/** 改动 tab 里的一份文档。
 *
 * 一份 .docx 的差异是一句「二进制文件不同」,所以在按文件类型分派渲染器之前,这一格
 * 对一份交付的文档只能说「二进制文件,不能按文本编辑」——而它恰恰是这个房间做出来的
 * 那个东西。现在它画出这一版的页面,并把文件自己带的修订列在旁边,逐条可处理。
 *
 * 两件事钉在这里:文档不再落到那句二进制提示上;读写都带着来源(这一份在某个任务的
 * 工作树上,不是房间交付的那一份,同一个路径在两个库里可以是两份不同的文件)。
 */
import type { FileContent, Topic } from '../../cx_types'

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
  default: {
    name: 'PreviewPages',
    props: ['data'],
    template: '<div class="stub-pages" :data-bytes="data ? data.byteLength : 0" />',
  },
}))

const listFiles = vi.fn()
const readFile = vi.fn()
const getGitDiff = vi.fn()
const previewDocumentPdf = vi.fn()
const documentRevisions = vi.fn()
const decideDocumentRevisions = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    listRoomTasks: vi.fn().mockResolvedValue({
      data: [
        {
          id: 'task-1',
          title: '写合同',
          status: 'open',
          branch_name: 'task/contract',
          presentation: { column: 'building', display_status: '运行中' },
          blocks: [],
        },
      ],
      total: 1,
    }),
    listFiles: (...a: unknown[]) => listFiles(...a),
    readFile: (...a: unknown[]) => readFile(...a),
    getGitDiff: (...a: unknown[]) => getGitDiff(...a),
    previewDocumentPdf: (...a: unknown[]) => previewDocumentPdf(...a),
    documentRevisions: (...a: unknown[]) => documentRevisions(...a),
    decideDocumentRevisions: (...a: unknown[]) => decideDocumentRevisions(...a),
    writeFile: vi.fn().mockResolvedValue({ path: 'x', version: 'v2' }),
    getDoc: vi.fn().mockResolvedValue({ markdown: '', title: '' }),
    putDoc: vi.fn().mockResolvedValue({}),
    getComments: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getDocNodes: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTranscript: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getTerminal: vi.fn().mockResolvedValue({ available: false, backend: 'none' }),
    getGitLog: vi.fn().mockResolvedValue({ data: [], total: 0 }),
    getPreview: vi.fn().mockResolvedValue(null),
    getTopicUsage: vi.fn().mockResolvedValue(null),
    getProjectUsage: vi.fn().mockResolvedValue(null),
    getTopicWorkSummary: vi.fn().mockResolvedValue({ changed_files: ['合同.docx'], has_run: true }),
  }
})

import WorkPanel from '../WorkPanel.vue'

function topic(id: string): Topic {
  return { id, project_id: 'p1', title: `话题 ${id}`, status: 'active' } as Topic
}

function binaryFile(path: string): FileContent {
  return { path, content: null, version: 'v7', bytes: 38705, binary: true, too_large: false }
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

async function openChanges(container: Element) {
  const tab = Array.from(container.querySelectorAll('button')).find((b) =>
    b.getAttribute('title')?.startsWith('改动')
  )
  expect(tab, '找不到 改动 tab').toBeTruthy()
  await fireEvent.click(tab!)
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
  listFiles.mockResolvedValue({ data: [{ path: '合同.docx', bytes: 38705 }], total: 1 })
  readFile.mockResolvedValue(binaryFile('合同.docx'))
  getGitDiff.mockResolvedValue({ diff: 'diff --git a/合同.docx b/合同.docx\nBinary files differ\n' })
  previewDocumentPdf.mockResolvedValue(new ArrayBuffer(4096))
  documentRevisions.mockResolvedValue({
    path: '合同.docx',
    version: 'v7',
    revisions: [
      {
        number: 1,
        paragraph: 4,
        kind: 'replace',
        removed: '30 天',
        added: '60 天',
        author: '芝士',
        date: '2026-09-18T02:00:00Z',
      },
    ],
  })
  decideDocumentRevisions.mockResolvedValue({ path: '合同.docx', version: 'v8', revisions: [] })
})

describe('改动 tab: 一份文档', () => {
  it('画出这一版，而不是说它是二进制文件', async () => {
    const { container } = mountPanel()
    await flush()
    await openChanges(container)

    expect(container.querySelector('.stub-pages')?.getAttribute('data-bytes')).toBe('4096')
    expect(container.textContent).not.toContain('二进制文件，不能按文本编辑')
    // 来源跟着请求走：这一份在任务的工作树上，不是房间交付的那一份。
    expect(previewDocumentPdf).toHaveBeenCalledWith('topic-A', '合同.docx', 'task-1')
  })

  it('修订在这里也能逐条处理，处理的是这个任务工作树上的那一份', async () => {
    const { container } = mountPanel()
    await flush()
    await openChanges(container)

    expect(documentRevisions).toHaveBeenCalledWith('topic-A', '合同.docx', 'task-1')
    expect(container.textContent).toContain('把「30 天」改成「60 天」')

    const accept = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === '接受')
    expect(accept, '修订条目上没有接受按钮').toBeTruthy()
    await fireEvent.click(accept!)
    await flush()

    expect(decideDocumentRevisions).toHaveBeenCalledWith('topic-A', '合同.docx', 'v7', { accept: [1] }, 'task-1')
  })

  it('.md 仍然走文本差异——它的 diff 正是审阅要看的那一面', async () => {
    listFiles.mockResolvedValue({ data: [{ path: '说明.md', bytes: 120 }], total: 1 })
    readFile.mockResolvedValue({
      path: '说明.md',
      content: '# 说明\n新的一行\n',
      version: 'v1',
      bytes: 20,
      binary: false,
      too_large: false,
    })
    getGitDiff.mockResolvedValue({
      diff: 'diff --git a/说明.md b/说明.md\n@@ -1 +1,2 @@\n # 说明\n+新的一行\n',
    })

    const { container } = mountPanel()
    await flush()
    await openChanges(container)

    expect(container.querySelector('.diff-view')).toBeTruthy()
    expect(container.querySelector('.stub-pages')).toBeNull()
    expect(previewDocumentPdf).not.toHaveBeenCalled()
  })
})
