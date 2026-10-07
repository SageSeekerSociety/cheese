import type { FileContent } from '../../cx_types'

import { createVuetify } from 'vuetify'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
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
  }
})
// 网页那一份不在 `api.ts` 里：那一份已经超了长度上限，只能变短。
vi.mock('../../lib/previewHtml', () => ({ previewDocumentPageSnapshot: vi.fn() }))
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

it('renders nothing for the web view until the reader asks for it', async () => {
  const ui = render(PanelPreviewHost, { props: panelProps, global })
  await waitFor(() => expect(api.previewDocumentPdfSnapshot).toHaveBeenCalledTimes(1))
  // 转一页要几秒，而默认那一档是分页视图：没按就不该为网页视图渲染一次。
  expect(pageReader.previewDocumentPageSnapshot).not.toHaveBeenCalled()
  expect(ui.queryByTestId('toggle-doc-page')).not.toBeNull()
  expect(ui.container.querySelector('iframe')).toBeNull()
})

it('swaps the PDF for the page the reader asked for, in a sandbox with no same-origin', async () => {
  const ui = render(PanelPreviewHost, { props: panelProps, global })
  await waitFor(() => expect(api.previewDocumentPdfSnapshot).toHaveBeenCalledTimes(1))
  await fireEvent.click(ui.getByTestId('toggle-doc-page'))
  await waitFor(() =>
    expect(pageReader.previewDocumentPageSnapshot).toHaveBeenCalledWith('room', 'report.docx', null, 'committed')
  )
  const frame = await ui.findByTitle('网页视图')
  expect(frame.tagName).toBe('IFRAME')
  // 少了 allow-scripts，表格的多工作表标签就切不动；多了 allow-same-origin，这一页
  // 就住进应用自己的源里，它的脚本能读会话里的东西。
  expect(frame.getAttribute('sandbox')).toBe('allow-scripts')
  expect(frame.getAttribute('srcdoc')).toBe(page)
  // 换过去之后不再重复取 PDF：两种视图一次只用一种。
  expect(api.previewDocumentPdfSnapshot).toHaveBeenCalledTimes(1)
})

it('goes back to the print view when the reader asks for it again', async () => {
  const ui = render(PanelPreviewHost, { props: panelProps, global })
  await waitFor(() => expect(api.previewDocumentPdfSnapshot).toHaveBeenCalledTimes(1))
  await fireEvent.click(ui.getByTestId('toggle-doc-page'))
  await ui.findByTitle('网页视图')
  await fireEvent.click(ui.getByTestId('toggle-doc-page'))
  await waitFor(() => expect(ui.container.querySelector('iframe')).toBeNull())
  // 换回来要重新取一次字节：网页视图那一份不是字节，喂不了 PDF 阅读器。
  await waitFor(() => expect(api.previewDocumentPdfSnapshot).toHaveBeenCalledTimes(2))
})

it('offers no second reading for a format only the PDF route can read', async () => {
  vi.mocked(api.readPreviewFile).mockResolvedValue(file('old.doc'))
  const ui = render(PanelPreviewHost, { props: { ...panelProps, path: 'old.doc' }, global })
  await waitFor(() => expect(api.previewDocumentPdfSnapshot).toHaveBeenCalledTimes(1))
  expect(ui.queryByTestId('toggle-doc-page')).toBeNull()
})

it('comes back to the print view when the next file has no page to show', async () => {
  const ui = render(PanelPreviewHost, { props: panelProps, global })
  await waitFor(() => expect(api.previewDocumentPdfSnapshot).toHaveBeenCalledTimes(1))
  await fireEvent.click(ui.getByTestId('toggle-doc-page'))
  await ui.findByTitle('网页视图')

  // 挑法是粘的——换文件不翻回去——但屏幕上画得出来的只有这一份能画的那一种。这一份
  // （`.doc`）只有 PDF 那一路读得了，所以面板必须自己回到分页视图；留在网页视图里的
  // 表现不是「画错了」，是字节取回来了却没有任何一支去画它，读者拿到的是一块空白。
  vi.mocked(api.readPreviewFile).mockResolvedValue(file('old.doc'))
  await ui.rerender({ ...panelProps, path: 'old.doc' })

  await waitFor(() => expect(vi.mocked(api.previewDocumentPdfSnapshot).mock.calls.length).toBeGreaterThanOrEqual(2))
  expect(ui.queryByTestId('toggle-doc-page')).toBeNull()
  expect(ui.container.querySelector('iframe')).toBeNull()
  await waitFor(() => expect(ui.container.querySelector('.viewer')).not.toBeNull())
})

it('drops the previous page instead of leaving it under the next file name', async () => {
  vi.mocked(api.readPreviewFile).mockResolvedValue(file('a.docx'))
  const ui = render(PanelPreviewHost, { props: { ...panelProps, path: 'a.docx' }, global })
  await waitFor(() => expect(api.previewDocumentPdfSnapshot).toHaveBeenCalledTimes(1))
  await fireEvent.click(ui.getByTestId('toggle-doc-page'))
  await ui.findByTitle('网页视图')

  // 下一份网页化不了。上一份的页面不能留在屏幕上：那不是「这一份的上一次」，是另一
  // 份文件，而留下的那句提示会说成前者。
  vi.mocked(pageReader.previewDocumentPageSnapshot).mockRejectedValue(new Error('转不了'))
  vi.mocked(api.readPreviewFile).mockResolvedValue(file('b.docx'))
  await ui.rerender({ ...panelProps, path: 'b.docx' })

  await waitFor(() =>
    expect(pageReader.previewDocumentPageSnapshot).toHaveBeenCalledWith('room', 'b.docx', null, 'committed')
  )
  expect(ui.container.querySelector('iframe')).toBeNull()
})
