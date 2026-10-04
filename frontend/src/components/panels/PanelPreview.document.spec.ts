import type { PreviewInfo } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

// 断言读的是中文界面上的那一行字，语言钉在中文上。
beforeEach(() => setLocale('zh-CN'))

const getPreview = vi.fn()
const readPreviewFile = vi.fn()
const requestPreviewSession = vi.fn()
const downloadFile = vi.fn()
const attachmentRawUrl = vi.fn()
const previewFileBytes = vi.fn()
const previewDocumentPdfSnapshot = vi.fn()
const documentRevisions = vi.fn()
const decideDocumentRevisions = vi.fn()

// Declared through vi.hoisted: the vi.mock factory below is lifted above every
// other statement in this file, so a plain `class` here is still in its temporal
// dead zone when the factory runs.
const { PreviewRendererUnavailable } = vi.hoisted(() => ({
  PreviewRendererUnavailable: class extends Error {},
}))

vi.mock('../../api', () => ({
  getPreview: (...args: unknown[]) => getPreview(...args),
  readPreviewFile: (...args: unknown[]) => readPreviewFile(...args),
  requestPreviewSession: (...args: unknown[]) => requestPreviewSession(...args),
  downloadFile: (...args: unknown[]) => downloadFile(...args),
  attachmentRawUrl: (...args: unknown[]) => attachmentRawUrl(...args),
  previewFileBytes: (...args: unknown[]) => previewFileBytes(...args),
  previewDocumentPdfSnapshot: (...args: unknown[]) => previewDocumentPdfSnapshot(...args),
  documentRevisions: (...args: unknown[]) => documentRevisions(...args),
  decideDocumentRevisions: (...args: unknown[]) => decideDocumentRevisions(...args),
  PreviewRendererUnavailable,
}))

// The two viewers draw with pdf.js and exceljs, neither of which belongs in a
// unit test of this panel. They are replaced by stubs that report what they were
// handed and can fire the events a reader's gesture produces.
vi.mock('./preview/PreviewPages.vue', () => ({
  default: {
    name: 'PreviewPages',
    props: ['data'],
    emits: ['quote'],
    // 真组件把 clearMark 交出来（抹掉指过的那一点）；替身也得有，不然面板清位置时炸。
    setup(_props: unknown, { expose }: { expose: (api: { clearMark: () => void }) => void }) {
      expose({ clearMark: () => {} })
    },
    template:
      '<div data-testid="pages" :data-bytes="data ? data.byteLength : 0"' +
      " @click=\"$emit('quote', { text: '平台在真实课程中完成了部署', page: 2 })\" />",
  },
}))
vi.mock('./preview/PreviewSheet.vue', () => ({
  default: {
    name: 'PreviewSheet',
    props: ['data', 'kind'],
    emits: ['cell'],
    // A workbook's cell carries a sheet name; a CSV has no sheet, and the stub
    // reports that the same way the real viewer does.
    template:
      '<div data-testid="sheet" :data-bytes="data ? data.byteLength : 0" :data-kind="kind"' +
      " @click=\"$emit('cell', { address: 'B7', value: '1200', sheet: kind === 'csv' ? '' : 'Sheet1' })\" />",
  },
}))

// 编辑器（OnlyOffice）和草稿历史各有自己的 spec，这里要的只是它们和文档字节之间
// 那个约定：改完、恢复完，面板按新版本重取这一页。两个替身各自把手势变成一个事件。
//
// 这两个是异步装上的（defineAsyncComponent），Vue 只有在载入结果上认出 ESM 时才会
// 取它的 default —— 所以 `__esModule` 不是装饰，少了它这一格拿到的是整个模块对象。
vi.mock('./preview/RoomFileHistory.vue', () => ({
  __esModule: true,
  default: {
    name: 'RoomFileHistory',
    props: ['topicId', 'path', 'version'],
    emits: ['restored'],
    template: '<button data-testid="restore" @click="$emit(\'restored\', { id: 1 })">恢复</button>',
  },
}))
vi.mock('./preview/RoomFileEditor.vue', () => ({
  __esModule: true,
  default: {
    name: 'RoomFileEditor',
    props: ['topicId', 'path'],
    emits: ['close', 'opened'],
    template:
      '<div data-testid="editor">' +
      '<button data-testid="editor-opened" @click="$emit(\'opened\', path)">已打开</button>' +
      '<button data-testid="editor-close" @click="$emit(\'close\')">关闭</button>' +
      '</div>',
  },
}))

import PanelPreview from './PanelPreview.vue'

const DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

/** A deliverable in the worktree: real bytes, and not readable as text. */
function artifact(path: string, mime: string): PreviewInfo {
  return { kind: 'file', path, mime, url: null, artifact_id: `artifact-${path}`, tunnel_up: false }
}

function fileContent(path: string) {
  return { path, content: null, version: 'v1', bytes: 38705, binary: true, too_large: false }
}

function mount() {
  return render(PanelPreview, {
    props: { topicId: 'topic-a', projectId: 'project-a', active: true },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  // 编辑器装在一个全屏对话框里，而 Vuetify 那层浮出物会挂 visualViewport 的
  // resize/scroll —— happy-dom 没有这个对象（它连引用都会抛），补一个空壳。
  Object.defineProperty(window, 'visualViewport', {
    configurable: true,
    value: { addEventListener() {}, removeEventListener() {} },
  })
  getPreview.mockResolvedValue(artifact('output/评审简报.docx', DOCX))
  readPreviewFile.mockResolvedValue(fileContent('output/评审简报.docx'))
  attachmentRawUrl.mockReturnValue('/api/topics/topic-a/attachments/raw?path=x')
  downloadFile.mockResolvedValue(undefined)
  previewDocumentPdfSnapshot.mockResolvedValue({ bytes: new ArrayBuffer(4096), sourceVersion: null })
  previewFileBytes.mockResolvedValue(new ArrayBuffer(2048))
  documentRevisions.mockResolvedValue({ path: 'output/评审简报.docx', revisions: [] })
})
afterEach(cleanup)

it('shows a Word report rather than offering it as a download', async () => {
  mount()

  const pages = await screen.findByTestId('pages')
  // The file the room produced is on screen, converted, not described.
  expect(pages.getAttribute('data-bytes')).toBe('4096')
  // 末尾那个 null 是来源：房间自己的文件，不是某个任务分支上的那一份。
  expect(previewDocumentPdfSnapshot).toHaveBeenCalledWith('topic-a', 'output/评审简报.docx', null, 'live')
  expect(screen.getByText('评审简报.docx')).toBeTruthy()
})

it('reads a spreadsheet from its own bytes, not from a converted copy', async () => {
  getPreview.mockResolvedValue(artifact('output/预算表.xlsx', XLSX))
  readPreviewFile.mockResolvedValue(fileContent('output/预算表.xlsx'))

  mount()

  // Paginating a sheet would throw away the cell addresses, so it is never sent
  // for conversion.
  const sheet = await screen.findByTestId('sheet')
  expect(sheet.getAttribute('data-bytes')).toBe('2048')
  expect(previewDocumentPdfSnapshot).not.toHaveBeenCalled()
})

it('says the deployment has no renderer, and still hands the file over', async () => {
  previewDocumentPdfSnapshot.mockRejectedValue(new PreviewRendererUnavailable('这个部署没有启用文档预览'))

  mount()

  await waitFor(() => expect(screen.getByText('文档预览未启用')).toBeTruthy())
  // Not the same sentence as a file that cannot be converted — and the way out
  // is still there.
  expect(screen.queryByText('无法显示这个文件')).toBeNull()
  expect(screen.getByText('下载')).toBeTruthy()
})

it('separates a file it cannot convert from a deployment that cannot convert', async () => {
  previewDocumentPdfSnapshot.mockRejectedValue(new Error('转换超时'))

  mount()

  await waitFor(() => expect(screen.getByText('无法显示这个文件')).toBeTruthy())
  expect(screen.getByText('转换超时')).toBeTruthy()
  expect(screen.queryByText('文档预览未启用')).toBeNull()
})

it('turns a pointed-at cell into a message naming the file and the address', async () => {
  getPreview.mockResolvedValue(artifact('output/预算表.xlsx', XLSX))
  readPreviewFile.mockResolvedValue(fileContent('output/预算表.xlsx'))
  const { emitted } = mount()

  await fireEvent.click(await screen.findByTestId('sheet'))
  await fireEvent.update(screen.getByPlaceholderText('说明要改什么'), '这个数字应该按季度摊')
  await fireEvent.click(screen.getByText('发送'))

  await waitFor(() => expect(emitted().locate).toBeTruthy())
  expect((emitted().locate as { message: string }[][])[0][0].message).toBe(
    '在 output/预算表.xlsx 的 Sheet1!B7（「1200」）：这个数字应该按季度摊'
  )
})

it('a CSV has no sheet, so its address is the cell alone', async () => {
  getPreview.mockResolvedValue(artifact('output/报名名单.csv', 'text/csv'))
  readPreviewFile.mockResolvedValue(fileContent('output/报名名单.csv'))
  const { emitted } = mount()

  await fireEvent.click(await screen.findByTestId('sheet'))
  await fireEvent.update(screen.getByPlaceholderText('说明要改什么'), '这一行重复了')
  await fireEvent.click(screen.getByText('发送'))

  await waitFor(() => expect(emitted().locate).toBeTruthy())
  expect((emitted().locate as { message: string }[][])[0][0].message).toBe(
    '在 output/报名名单.csv 的 B7（「1200」）：这一行重复了'
  )
})

it('turns a selected sentence into a message naming the page it came from', async () => {
  const { emitted } = mount()

  await fireEvent.click(await screen.findByTestId('pages'))
  await fireEvent.update(screen.getByPlaceholderText('说明要改什么'), '这句话和摘要对不上')
  await fireEvent.click(screen.getByText('发送'))

  await waitFor(() => expect(emitted().locate).toBeTruthy())
  expect((emitted().locate as { message: string }[][])[0][0].message).toBe(
    '在 output/评审简报.docx 的 第 2 页（「平台在真实课程中完成了部署」）：这句话和摘要对不上'
  )
})

it('turns a selected markdown sentence into a message naming its section', async () => {
  getPreview.mockResolvedValue(artifact('output/说明.md', 'text/markdown'))
  readPreviewFile.mockResolvedValue({
    path: 'output/说明.md',
    content: '# 配置\n\n失败以后重试 3 次。\n',
    version: 'v1',
    bytes: 24,
    binary: false,
    too_large: false,
  })
  const { emitted } = mount()

  // markdown 没有页也没有单元格，位置只能说是哪一节：标题来自这段之前最近的那个标题。
  const md = await screen.findByTestId('markdown')
  // 正文的读法第一次用到才加载，画出来要等一下。
  const node = await waitFor(() => md.querySelector('p')!.firstChild!)
  const range = document.createRange()
  range.setStart(node, 4)
  range.setEnd(node, 10)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  await fireEvent.mouseUp(md)

  await fireEvent.update(screen.getByPlaceholderText('说明要改什么'), '这里也要写清楚')
  await fireEvent.click(screen.getByText('发送'))

  await waitFor(() => expect(emitted().locate).toBeTruthy())
  const [message] = emitted().locate as { message: string }[][]
  const [sentence, context] = message[0].message.split('\n')
  expect(sentence).toBe('在 output/说明.md 的 标题「配置」（「重试 3 次」）：这里也要写清楚')
  // 同一句话在一份文档里往往不止一处，两侧各 32 字是受话人分辨它的东西。
  expect(context).toMatch(/^上文「.*失败以后」下文「。」$/)
  selection.removeAllRanges()
})

it('writes only the near side when the quote runs to the end of the document', async () => {
  getPreview.mockResolvedValue(artifact('output/说明.md', 'text/markdown'))
  readPreviewFile.mockResolvedValue({
    path: 'output/说明.md',
    content: '# 配置\n\n重试 3 次。\n',
    version: 'v1',
    bytes: 20,
    binary: false,
    too_large: false,
  })
  const { emitted } = mount()

  // 拖满整段：它前面是标题，后面什么都没有。后面那一侧写「下文「」」是没话找话。
  const md = await screen.findByTestId('markdown')
  // 正文的读法第一次用到才加载，画出来要等一下。
  const node = await waitFor(() => md.querySelector('p')!.firstChild!)
  const range = document.createRange()
  range.setStart(node, 0)
  range.setEnd(node, node.textContent!.length)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  await fireEvent.mouseUp(md)

  await fireEvent.update(screen.getByPlaceholderText('说明要改什么'), '这一整段都要重写')
  await fireEvent.click(screen.getByText('发送'))

  await waitFor(() => expect(emitted().locate).toBeTruthy())
  const [message] = emitted().locate as { message: string }[][]
  const [, context] = message[0].message.split('\n')
  expect(context).toBe('上文「配置」')
  selection.removeAllRanges()
})

it('says the quote is at the top of the file when nothing precedes it', async () => {
  getPreview.mockResolvedValue(artifact('output/说明.md', 'text/markdown'))
  readPreviewFile.mockResolvedValue({
    path: 'output/说明.md',
    content: '重试 3 次。\n\n还有别的。\n',
    version: 'v1',
    bytes: 20,
    binary: false,
    too_large: false,
  })
  const { emitted } = mount()

  const md = await screen.findByTestId('markdown')
  // 正文的读法第一次用到才加载，画出来要等一下。
  const node = await waitFor(() => md.querySelector('p')!.firstChild!)
  const range = document.createRange()
  range.setStart(node, 0)
  range.setEnd(node, 2)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  await fireEvent.mouseUp(md)

  await fireEvent.update(screen.getByPlaceholderText('说明要改什么'), '这句话要改')
  await fireEvent.click(screen.getByText('发送'))

  await waitFor(() => expect(emitted().locate).toBeTruthy())
  const [message] = emitted().locate as { message: string }[][]
  const [sentence, context] = message[0].message.split('\n')
  // 这段之前没有标题，位置就说是文件开头；前侧同样什么都没有，只有下文。
  expect(sentence).toBe('在 output/说明.md 的 文件开头（「重试」）：这句话要改')
  expect(context).toBe('下文「3 次。 还有别的。」')
  selection.removeAllRanges()
})

it('says nothing until the reader has written what is wrong', async () => {
  const { emitted } = mount()

  await fireEvent.click(await screen.findByTestId('pages'))
  await fireEvent.click(screen.getByText('发送'))

  expect(emitted().locate).toBeFalsy()
})

it('still renders a web page as a page', async () => {
  getPreview.mockResolvedValue({
    kind: 'file',
    path: 'report.html',
    mime: 'text/html',
    url: 'https://preview.example/',
    artifact_id: 'artifact-html',
    tunnel_up: true,
  })
  readPreviewFile.mockResolvedValue({
    path: 'report.html',
    content: '<h1>hi</h1>',
    version: 'v1',
    bytes: 11,
    binary: false,
    too_large: false,
  })
  requestPreviewSession.mockResolvedValue({ url: 'https://preview.example/_cheese/session', grant: 'g' })

  mount()

  await waitFor(() => expect(requestPreviewSession).toHaveBeenCalled())
  expect(screen.queryByTestId('pages')).toBeNull()
})

it('sends a .pdf whose bytes read as text to the document viewer, not a blank frame', async () => {
  getPreview.mockResolvedValue({
    kind: 'file',
    path: 'output/报告.pdf',
    mime: 'application/pdf',
    url: 'https://preview.example/',
    artifact_id: 'artifact-pdf',
    tunnel_up: true,
  })
  readPreviewFile.mockResolvedValue({
    path: 'output/报告.pdf',
    content: 'this is not a pdf',
    version: 'v1',
    bytes: 17,
    binary: false,
    too_large: false,
  })

  mount()

  // The viewer is what can say the file is broken; a frame just stays empty.
  expect(await screen.findByTestId('pages')).toBeTruthy()
  expect(requestPreviewSession).not.toHaveBeenCalled()
})

// 在线编辑和草稿历史各改一次文件本身。这一页的字节是按文件版本缓存的，而这两件事
// 改的都是同一份文件、版本却由别的路径带回来——所以得有人按一下，让它重取。
it('关掉编辑器之后这一页按新版本重取，期间把这份文件交给房间开成页签', async () => {
  const { emitted } = mount()
  expect(await screen.findByTestId('pages')).toBeTruthy()
  expect(previewDocumentPdfSnapshot).toHaveBeenCalledTimes(1)

  await fireEvent.click(screen.getByTestId('edit-file'))
  await fireEvent.click(await screen.findByTestId('editor-opened'))
  // 编辑器里改的是房间里那一份，所以这一格之外也得有一份它的页签。
  expect((emitted()['open-file'] as unknown[][])[0][0]).toBe('output/评审简报.docx')

  await fireEvent.click(screen.getByTestId('editor-close'))
  await waitFor(() => expect(previewDocumentPdfSnapshot).toHaveBeenCalledTimes(2))
})

it('恢复了一版之后这一页按新版本重取', async () => {
  mount()
  expect(await screen.findByTestId('pages')).toBeTruthy()
  expect(previewDocumentPdfSnapshot).toHaveBeenCalledTimes(1)

  await fireEvent.click(screen.getByTestId('file-history'))
  await fireEvent.click(await screen.findByTestId('restore'))

  await waitFor(() => expect(previewDocumentPdfSnapshot).toHaveBeenCalledTimes(2))
})
