import type { PreviewInfo } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen, waitFor } from '@testing-library/vue'
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
const previewSlideNotes = vi.fn()
const roomFileRevisions = vi.fn()
const restoreRoomFileRevision = vi.fn()

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
  roomFileRevisions: (...args: unknown[]) => roomFileRevisions(...args),
  restoreRoomFileRevision: (...args: unknown[]) => restoreRoomFileRevision(...args),
  decideDocumentRevisions: (...args: unknown[]) => decideDocumentRevisions(...args),
  PreviewRendererUnavailable,
}))
vi.mock('../../api/roomFiles', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  previewSlideNotes: (...args: unknown[]) => previewSlideNotes(...args),
}))

// The two viewers draw with pdf.js and exceljs, neither of which belongs in a
// unit test of this panel. They are replaced by stubs that report what they were
// handed and can fire the events a reader's gesture produces.
// 幻灯片阅读器换成替身：只报它收到了几页备注、第一页写的是什么。
vi.mock('./preview/PreviewSlides.vue', () => ({
  default: {
    name: 'PreviewSlides',
    props: ['data', 'notes'],
    template:
      '<div data-testid="slides" :data-notes="notes ? notes.length : -1"' +
      " :data-first=\"notes && notes[0] ? notes[0].paragraphs.map(p => p.runs.map(r => r.text).join('')).join('|') : ''\" />",
  },
}))
vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))

import PanelPreviewHost from '@/components/work/PanelPreviewHost.vue'

const PPTX = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'

beforeEach(() => {
  vi.clearAllMocks()
  Object.defineProperty(window, 'visualViewport', {
    configurable: true,
    value: { addEventListener() {}, removeEventListener() {} },
  })
  getPreview.mockResolvedValue({
    kind: 'file',
    path: 'output/汇报.pptx',
    mime: PPTX,
    url: null,
    artifact_id: 'artifact-deck',
    tunnel_up: false,
  } satisfies PreviewInfo)
  readPreviewFile.mockResolvedValue({
    path: 'output/汇报.pptx',
    content: null,
    version: 'v1',
    bytes: 38705,
    binary: true,
    too_large: false,
  })
  attachmentRawUrl.mockReturnValue('/api/topics/topic-a/attachments/raw?path=x')
  previewDocumentPdfSnapshot.mockResolvedValue({ bytes: new ArrayBuffer(4096), sourceVersion: null })
  documentRevisions.mockResolvedValue({ path: 'output/汇报.pptx', revisions: [] })
  roomFileRevisions.mockResolvedValue({ data: [], total: 0, version: 'v1' })
  previewSlideNotes.mockResolvedValue({
    notes: ['先讲结论', ''],
    slides: [
      { hidden: false, paragraphs: [{ runs: [{ text: '先讲' }, { text: '结论', bold: true }] }] },
      { hidden: true, paragraphs: [] },
    ],
    readable: true,
    version: 'v1',
  })
})
afterEach(cleanup)

// 备注上线时取回来了，却从没递到阅读器上：这一格摊 props 时漏了它，阅读器自己的单测挂的
// 是阅读器本身，看不见。这一条挂整条链（取数 → 这一格 → 展示层 → 文档档 → 阅读器）。
it('a deck’s speaker notes reach the slide reader through the whole panel', async () => {
  render(PanelPreviewHost, {
    props: { topicId: 'topic-a', projectId: 'project-a', active: true },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  const slides = await screen.findByTestId('slides')
  await waitFor(() => expect(slides.getAttribute('data-notes')).toBe('2'))
  expect(slides.getAttribute('data-first')).toBe('先讲结论')
  expect(previewSlideNotes).toHaveBeenCalledWith('topic-a', 'output/汇报.pptx', null, 'live')
})
