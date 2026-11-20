/**
 * 窄的一格里打开一份 Word：文档那一格量到「窄」，报给取数那一层，取的就是网页那一份，并且
 * 按屏宽重排——不再是一页缩小的 A4 纸。这一份挂的是整条线（`PanelPreviewHost` 起），量到
 * 宽窄的那一层和决定取哪一份的那一层隔着三层组件，漏接一处就是手机上又回到六七像素的字。
 */
import type { FileContent } from '../../cx_types'

import { createVuetify } from 'vuetify'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import * as api from '../../api'
import * as pageReader from '../../lib/previewHtml'

import PanelPreviewHost from '@/components/work/PanelPreviewHost.vue'
import { setLocale } from '@/i18n'

const version = 'aaaaaaaaaaaaaaaa'
const page = '<html><head><title>t</title></head><body>整页</body></html>'

vi.mock('../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api')>()
  return {
    ...actual,
    readPreviewFile: vi.fn(),
    previewDocumentPdfSnapshot: vi.fn(),
    documentRevisions: vi.fn(),
    // 房间里的文档一打开就读它的保存历史（文档条上的版本行）。这个用例问的是窄处
    // Word 读哪一份、怎么重排，不是版本：给一份空清单。
    roomFileRevisions: vi.fn(async () => ({ data: [], total: 0, path: 'report.docx', version: null })),
  }
})
// 网页那一份不在 `api.ts` 里：那一份已经超了长度上限，只能变短。
vi.mock('../../lib/previewHtml', () => ({ previewDocumentPageSnapshot: vi.fn() }))
// happy-dom 量不出宽度：这一格的宽窄由这里给，接线从文档那一格量到的地方一路走到取数那一层。
const narrow = vi.hoisted(() => ({ value: true }))
vi.mock('@/lib/previewNarrow', async (orig) => {
  const { computed } = await import('vue')
  return {
    ...(await orig<typeof import('@/lib/previewNarrow')>()),
    usePreviewNarrow: () => ({ narrow: computed(() => narrow.value), measured: computed(() => true) }),
  }
})
// 分页文档现在也会读这一份的评论串（页上要画已有的评论标记），所以这一条也得打桩。
vi.mock('@/api/fileThreads', () => ({ listFileThreads: vi.fn(async () => ({ data: [], total: 0 })) }))
// 三个阅读器都换掉：它们拿到字节就会去画，而这里问的是「该不该取字节、取哪一种」。
// 留一个类名，因为「字节取回来了、却没有任何一支去画它」正是要抓的那种空白。
vi.mock('./preview/PreviewSlides.vue', () => ({ default: { props: ['data'], template: '<div class="viewer" />' } }))
vi.mock('./preview/PreviewPages.vue', () => ({
  default: {
    props: ['data'],
    // 真组件把 clearMark 交出来（抹掉指过的那一点）；替身也得有，不然面板清位置时炸
    // ——而清位置是在一次 props 更新里做的，炸掉的那一次更新会把整个面板的 DOM 留在
    // 旧那一支上（换网页视图换不过去，看到的还是分页视图）。
    setup(_props: unknown, { expose }: { expose: (api: { clearMark: () => void }) => void }) {
      expose({ clearMark: () => {} })
    },
    template: '<div class="viewer" />',
  },
}))
vi.mock('./preview/PreviewSheet.vue', () => ({ default: { props: ['data'], template: '<div class="viewer" />' } }))
vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))

beforeEach(() => {
  vi.resetAllMocks()
  setLocale('zh-CN')
  vi.mocked(api.readPreviewFile).mockResolvedValue(file('report.docx'))
  vi.mocked(api.previewDocumentPdfSnapshot).mockResolvedValue({ bytes: new ArrayBuffer(8), sourceVersion: version })
  // 面板对每一份 .docx 都会问一次修订清单（取数那一层里的 `useDocumentRevisions`）；这一条
  // 不接住，测试网络守卫就会把它算成一次没人接的请求。
  vi.mocked(api.documentRevisions).mockResolvedValue({ path: 'report.docx', version, revisions: [] })
  vi.mocked(pageReader.previewDocumentPageSnapshot).mockResolvedValue({ html: page, sourceVersion: version })
})
afterEach(cleanup)

const panelProps = { topicId: 'room', projectId: 'project', path: 'report.docx', active: true, refreshTick: 0 }
const global = {
  plugins: [createVuetify()],
  stubs: {
    VBtn: { template: '<button><slot /></button>' },
    VIcon: true,
    VSpacer: true,
    VAlert: true,
    VDialog: true,
  },
}
function file(path: string): FileContent {
  return { path, content: null, version, bytes: 8, binary: true, too_large: false, source: 'committed' }
}

it('窄处的 Word 一打开就读网页那一份，按屏宽重排', async () => {
  narrow.value = true
  render(PanelPreviewHost, { props: panelProps, global })
  await waitFor(() => expect(pageReader.previewDocumentPageSnapshot).toHaveBeenCalled())
  const frame = await waitFor(() => {
    const el = document.querySelector('iframe.page-frame')
    expect(el).not.toBeNull()
    return el as HTMLIFrameElement
  })
  expect(frame.getAttribute('srcdoc')).toContain('data-cheese-reflow')
  expect(frame.getAttribute('srcdoc')).toContain('整页')
})

it('宽处照旧是分页那一份', async () => {
  narrow.value = false
  render(PanelPreviewHost, { props: panelProps, global })
  await waitFor(() => expect(api.previewDocumentPdfSnapshot).toHaveBeenCalledTimes(1))
  expect(pageReader.previewDocumentPageSnapshot).not.toHaveBeenCalled()
})
