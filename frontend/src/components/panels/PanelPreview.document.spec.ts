import type { PreviewInfo } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const getPreview = vi.fn()
const readPreviewFile = vi.fn()
const requestPreviewSession = vi.fn()
const downloadFile = vi.fn()
const attachmentRawUrl = vi.fn()
const previewFileBytes = vi.fn()
const previewDocumentPdf = vi.fn()
const previewDocumentXlsx = vi.fn()
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
  previewDocumentPdf: (...args: unknown[]) => previewDocumentPdf(...args),
  previewDocumentXlsx: (...args: unknown[]) => previewDocumentXlsx(...args),
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
  getPreview.mockResolvedValue(artifact('output/评审简报.docx', DOCX))
  readPreviewFile.mockResolvedValue(fileContent('output/评审简报.docx'))
  attachmentRawUrl.mockReturnValue('/api/topics/topic-a/attachments/raw?path=x')
  downloadFile.mockResolvedValue(undefined)
  previewDocumentPdf.mockResolvedValue(new ArrayBuffer(4096))
  previewDocumentXlsx.mockResolvedValue(new ArrayBuffer(3072))
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
  expect(previewDocumentPdf).toHaveBeenCalledWith('topic-a', 'output/评审简报.docx', null)
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
  expect(previewDocumentPdf).not.toHaveBeenCalled()
})

it('turns a legacy .xls into a workbook the sheet reader can read', async () => {
  getPreview.mockResolvedValue(artifact('output/台账.xls', 'application/vnd.ms-excel'))
  readPreviewFile.mockResolvedValue(fileContent('output/台账.xls'))

  mount()

  // `.xls` 不是 zip，阅读器读不出其中的单元格——它得先变成 xlsx。转的不是 PDF，
  // 理由和上面那条 xlsx 一样：分页会把列拆散，而 `B7` 是读者在表里唯一能指的东西。
  const sheet = await screen.findByTestId('sheet')
  expect(sheet.getAttribute('data-bytes')).toBe('3072')
  expect(previewDocumentXlsx).toHaveBeenCalledWith('topic-a', 'output/台账.xls', null)
  // 原始字节是读不出单元格的那一份，谁都不该拿它去画。
  expect(previewFileBytes).not.toHaveBeenCalled()
  expect(previewDocumentPdf).not.toHaveBeenCalled()
})

it('says the deployment has no renderer for a legacy sheet too', async () => {
  getPreview.mockResolvedValue(artifact('output/台账.xls', 'application/vnd.ms-excel'))
  readPreviewFile.mockResolvedValue(fileContent('output/台账.xls'))
  previewDocumentXlsx.mockRejectedValue(new PreviewRendererUnavailable('这个部署没有启用格式转换'))

  mount()

  await waitFor(() => expect(screen.getByText('文档预览未启用')).toBeTruthy())
  expect(screen.queryByText('无法显示这个文件')).toBeNull()
})

it('says the deployment has no renderer, and still hands the file over', async () => {
  previewDocumentPdf.mockRejectedValue(new PreviewRendererUnavailable('这个部署没有启用文档预览'))

  mount()

  await waitFor(() => expect(screen.getByText('文档预览未启用')).toBeTruthy())
  // Not the same sentence as a file that cannot be converted — and the way out
  // is still there.
  expect(screen.queryByText('无法显示这个文件')).toBeNull()
  expect(screen.getByText('下载')).toBeTruthy()
})

it('separates a file it cannot convert from a deployment that cannot convert', async () => {
  previewDocumentPdf.mockRejectedValue(new Error('转换超时'))

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
  expect((emitted().locate as unknown[][])[0][0]).toBe(
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
  expect((emitted().locate as unknown[][])[0][0]).toBe('在 output/报名名单.csv 的 B7（「1200」）：这一行重复了')
})

it('turns a selected sentence into a message naming the page it came from', async () => {
  const { emitted } = mount()

  await fireEvent.click(await screen.findByTestId('pages'))
  await fireEvent.update(screen.getByPlaceholderText('说明要改什么'), '这句话和摘要对不上')
  await fireEvent.click(screen.getByText('发送'))

  await waitFor(() => expect(emitted().locate).toBeTruthy())
  expect((emitted().locate as unknown[][])[0][0]).toBe(
    '在 output/评审简报.docx 的 第 2 页（「平台在真实课程中完成了部署」）：这句话和摘要对不上'
  )
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
