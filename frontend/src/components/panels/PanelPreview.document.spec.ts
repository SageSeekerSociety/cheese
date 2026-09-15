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
vi.mock('../../api', () => ({
  getPreview: (...args: unknown[]) => getPreview(...args),
  readPreviewFile: (...args: unknown[]) => readPreviewFile(...args),
  requestPreviewSession: (...args: unknown[]) => requestPreviewSession(...args),
  downloadFile: (...args: unknown[]) => downloadFile(...args),
  attachmentRawUrl: (...args: unknown[]) => attachmentRawUrl(...args),
}))

import PanelPreview from './PanelPreview.vue'

const DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

/** 一份芝士交出来的 Word 报告：工作区里的真实文件，读不成文本。 */
function wordReport(path = 'output/评审简报.docx'): PreviewInfo {
  return { kind: 'file', path, mime: DOCX, url: null, artifact_id: 'artifact-doc', tunnel_up: false }
}

function mount() {
  return render(PanelPreview, {
    props: { topicId: 'topic-a', projectId: 'project-a', active: true },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  getPreview.mockResolvedValue(wordReport())
  readPreviewFile.mockResolvedValue({
    path: 'output/评审简报.docx',
    content: null,
    version: null,
    bytes: 38705,
    binary: true,
    too_large: false,
  })
  attachmentRawUrl.mockReturnValue('/api/topics/topic-a/attachments/raw?path=x')
  downloadFile.mockResolvedValue(undefined)
})
afterEach(cleanup)

it('offers a Word report by name and type, not as a broken web page', async () => {
  mount()

  // 交付物的名字是人认得的那个，不是工作区里的完整路径。
  await waitFor(() => expect(screen.getByText('评审简报.docx')).toBeTruthy())
  expect(screen.getByText('Word 文档')).toBeTruthy()
  // 「这个文件不是文本」说的是诊断，而这份文件正是用户要的东西。
  expect(screen.queryByText('这个文件不是文本')).toBeNull()
})

it('hands the file over when asked', async () => {
  mount()
  await waitFor(() => expect(screen.getByText('下载')).toBeTruthy())

  await fireEvent.click(screen.getByText('下载'))

  await waitFor(() => expect(downloadFile).toHaveBeenCalled())
  expect(attachmentRawUrl).toHaveBeenCalledWith('topic-a', 'output/评审简报.docx')
  expect(downloadFile.mock.calls[0][1]).toBe('评审简报.docx')
})

it('says so when the download fails instead of looking like nothing happened', async () => {
  downloadFile.mockRejectedValue(new Error('下载失败（HTTP 502）'))
  mount()
  await waitFor(() => expect(screen.getByText('下载')).toBeTruthy())

  await fireEvent.click(screen.getByText('下载'))

  await waitFor(() => expect(screen.getByText('下载失败（HTTP 502）')).toBeTruthy())
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
  expect(screen.queryByText('下载')).toBeNull()
})
