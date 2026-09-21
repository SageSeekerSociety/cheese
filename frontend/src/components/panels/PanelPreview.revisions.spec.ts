import type { DocumentRevision, PreviewInfo } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

const getPreview = vi.fn()
const readPreviewFile = vi.fn()
const requestPreviewSession = vi.fn()
const downloadFile = vi.fn()
const attachmentRawUrl = vi.fn()
const previewFileBytes = vi.fn()
const previewDocumentPdf = vi.fn()
const documentRevisions = vi.fn()
const decideDocumentRevisions = vi.fn()

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
  documentRevisions: (...args: unknown[]) => documentRevisions(...args),
  decideDocumentRevisions: (...args: unknown[]) => decideDocumentRevisions(...args),
  PreviewRendererUnavailable,
}))

vi.mock('./preview/PreviewPages.vue', () => ({
  default: {
    name: 'PreviewPages',
    props: ['data'],
    emits: ['quote'],
    template: '<div data-testid="pages" :data-bytes="data ? data.byteLength : 0" />',
  },
}))
vi.mock('./preview/PreviewSheet.vue', () => ({
  default: {
    name: 'PreviewSheet',
    props: ['data', 'kind'],
    emits: ['cell'],
    template: '<div data-testid="sheet" />',
  },
}))

import PanelPreview from './PanelPreview.vue'

const DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
const PATH = 'output/合同.docx'

function artifact(path: string, mime: string): PreviewInfo {
  return { kind: 'file', path, mime, url: null, artifact_id: `artifact-${path}`, tunnel_up: false }
}

function fileContent(path: string) {
  return { path, content: null, version: 'v1', bytes: 38705, binary: true, too_large: false }
}

function revision(over: Partial<DocumentRevision> = {}): DocumentRevision {
  return {
    number: 1,
    paragraph: 4,
    kind: 'replace',
    removed: '30 天',
    added: '60 天',
    author: '芝士',
    date: '2026-09-18T02:00:00Z',
    ...over,
  }
}

function mount() {
  return render(PanelPreview, {
    props: { topicId: 'topic-a', projectId: 'project-a', active: true },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  // 这一份断言的是中文界面（每处修订那句话、序号、接受/拒绝都在内）。默认语言是
  // en，所以先钉住语言：清单的文字进了词表之后，屏幕上出现哪一版词不再由写死的
  // 中文决定。
  setLocale('zh-CN')
  vi.clearAllMocks()
  getPreview.mockResolvedValue(artifact(PATH, DOCX))
  readPreviewFile.mockResolvedValue(fileContent(PATH))
  attachmentRawUrl.mockReturnValue('/api/topics/topic-a/attachments/raw?path=x')
  previewDocumentPdf.mockResolvedValue(new ArrayBuffer(4096))
  previewFileBytes.mockResolvedValue(new ArrayBuffer(2048))
  documentRevisions.mockResolvedValue({ path: PATH, version: 'doc-1', revisions: [] })
  decideDocumentRevisions.mockResolvedValue({ path: PATH, version: 'doc-2', revisions: [] })
})
afterEach(cleanup)

it('每处修订读成一句话，新旧文字都在同一条上', async () => {
  // 一次替换在 XML 里是插入加删除两个元素；分成两条就可以只接受一半。
  documentRevisions.mockResolvedValue({ path: PATH, version: 'doc-1', revisions: [revision()] })

  mount()

  await waitFor(() => expect(screen.getByTestId('revisions')).toBeTruthy())
  expect(screen.getByText('把「30 天」改成「60 天」')).toBeTruthy()
  expect(screen.getByText('修订 1 处')).toBeTruthy()
})

it('每条都说这是谁改的', async () => {
  // 用户传来的文档里可能本来就有别人未接受的修订。接受它是平常事，
  // 不知不觉接受了才不是——所以作者必须写在条目上。
  documentRevisions.mockResolvedValue({
    path: PATH,
    version: 'doc-1',
    revisions: [revision({ author: '张伟' })],
  })

  mount()

  await waitFor(() => expect(screen.getByText(/张伟/)).toBeTruthy())
  expect(screen.getByText(/第 4 段/)).toBeTruthy()
})

it('接受一条只提交那一条', async () => {
  documentRevisions.mockResolvedValue({
    path: PATH,
    version: 'doc-1',
    revisions: [revision(), revision({ number: 2, kind: 'delete', removed: '（暂定）' })],
  })
  decideDocumentRevisions.mockResolvedValue({
    path: PATH,
    version: 'doc-2',
    revisions: [revision({ number: 1, kind: 'delete', removed: '（暂定）' })],
  })

  mount()

  await waitFor(() => expect(screen.getByText('删了「（暂定）」')).toBeTruthy())
  await fireEvent.click(screen.getAllByText('接受')[0])

  await waitFor(() =>
    expect(decideDocumentRevisions).toHaveBeenCalledWith('topic-a', PATH, 'doc-1', { accept: [1] }, null)
  )
})

it('处理完之后重新取一次 PDF，页面上看到的才是处理过的那一版', async () => {
  // 文件版本没变（是这里改的，不是芝士改的），所以缓存不会自己失效。
  documentRevisions.mockResolvedValue({ path: PATH, version: 'doc-1', revisions: [revision()] })

  mount()

  await waitFor(() => expect(previewDocumentPdf).toHaveBeenCalledTimes(1))
  await fireEvent.click(screen.getByText('拒绝'))

  await waitFor(() => expect(previewDocumentPdf).toHaveBeenCalledTimes(2))
})

it('全部接受把每一条的序号都带上', async () => {
  documentRevisions.mockResolvedValue({
    path: PATH,
    version: 'doc-1',
    revisions: [revision(), revision({ number: 2 }), revision({ number: 3 })],
  })

  mount()

  await waitFor(() => expect(screen.getByText('修订 3 处')).toBeTruthy())
  await fireEvent.click(screen.getByText('全部接受'))

  await waitFor(() =>
    expect(decideDocumentRevisions).toHaveBeenCalledWith('topic-a', PATH, 'doc-1', { accept: [1, 2, 3] }, null)
  )
})

it('没有修订时不显示这块地方', async () => {
  mount()

  await waitFor(() => expect(screen.getByTestId('pages')).toBeTruthy())
  expect(screen.queryByTestId('revisions')).toBeNull()
})

it('表格不去问修订', async () => {
  // Excel 不带 Word 那种修订，问了只会拿回一句错误。
  getPreview.mockResolvedValue(artifact('output/预算表.xlsx', XLSX))
  readPreviewFile.mockResolvedValue(fileContent('output/预算表.xlsx'))

  mount()

  await waitFor(() => expect(screen.getByTestId('sheet')).toBeTruthy())
  expect(documentRevisions).not.toHaveBeenCalled()
})

it('清单读不出来时文档照旧显示', async () => {
  documentRevisions.mockRejectedValue(new Error('读不出这份文档的修订'))

  mount()

  // 丢掉的是清单，不是这份交付物——它仍然是用户现在能看到的最新状态。
  await waitFor(() => expect(screen.getByText('读不出这份文档的修订')).toBeTruthy())
  expect(screen.getByTestId('pages')).toBeTruthy()
})

it('每次处理带的都是刚读到的那一版文件', async () => {
  // 房间随时会重新交付同一个产出。带着旧版本去处理，写回去就把芝士刚交付的那份盖掉了。
  documentRevisions
    .mockResolvedValueOnce({ path: PATH, version: 'doc-1', revisions: [revision(), revision({ number: 2 })] })
    .mockResolvedValue({ path: PATH, version: 'doc-3', revisions: [revision()] })
  decideDocumentRevisions.mockResolvedValue({
    path: PATH,
    version: 'doc-2',
    revisions: [revision()],
  })

  mount()

  await waitFor(() => expect(screen.getByText('修订 2 处')).toBeTruthy())
  await fireEvent.click(screen.getAllByText('接受')[0])
  await waitFor(() => expect(documentRevisions).toHaveBeenCalledTimes(2))
  await fireEvent.click(screen.getAllByText('接受')[0])

  await waitFor(() =>
    expect(decideDocumentRevisions).toHaveBeenLastCalledWith('topic-a', PATH, 'doc-3', { accept: [1] }, null)
  )
})

it('处理失败时说一句，清单留在原地', async () => {
  documentRevisions.mockResolvedValue({ path: PATH, version: 'doc-1', revisions: [revision()] })
  decideDocumentRevisions.mockRejectedValue(new Error('清单可能已经变了，重新读一次。'))

  mount()

  await waitFor(() => expect(screen.getByText('把「30 天」改成「60 天」')).toBeTruthy())
  await fireEvent.click(screen.getByText('接受'))

  await waitFor(() => expect(screen.getByText(/重新读一次/)).toBeTruthy())
  expect(screen.getByText('把「30 天」改成「60 天」')).toBeTruthy()
  // 写不进去多半是文件已经变了，所以清单要换成现在这份——而那句话得留着。
  expect(documentRevisions).toHaveBeenCalledTimes(2)
})

describe('讲英文', () => {
  it('清单整块一个汉字都不剩：三种形态、序号、没署名的作者、四个按钮', async () => {
    setLocale('en')
    // 这一条走的是英文界面，所以来回改的文字和作者名都用 ASCII——它们是**用户
    // 的内容**，会和界面自己的字一起落进 textContent，用中文就分不清是谁漏翻的。
    documentRevisions.mockResolvedValue({
      path: PATH,
      version: 'doc-1',
      revisions: [
        revision({ removed: '30 days', added: '60 days', author: 'Zhang Wei' }),
        revision({ number: 2, kind: 'insert', added: 'Deposit', paragraph: 7, author: 'Li Na' }),
        revision({ number: 3, kind: 'delete', removed: 'draft', paragraph: 9, author: '' }),
      ],
    })

    mount()

    const list = await screen.findByTestId('revisions')
    expect(screen.getByText('Changed "30 days" to "60 days"')).toBeTruthy()
    expect(screen.getByText('Inserted "Deposit"')).toBeTruthy()
    expect(screen.getByText('Deleted "draft"')).toBeTruthy()
    expect(screen.getByText('3 revisions')).toBeTruthy()
    expect(screen.getByText('Paragraph 4 · Zhang Wei')).toBeTruthy()
    // 没署名的作者落到词表里那一个词：留空看不出是谁改的，漏回中文又只在这一档露馅。
    expect(screen.getByText('Paragraph 9 · Unnamed')).toBeTruthy()
    expect(screen.getByText('Accept all')).toBeTruthy()
    expect(screen.getByText('Reject all')).toBeTruthy()
    expect(screen.getAllByText('Accept')).toHaveLength(3)
    expect(screen.getAllByText('Reject')).toHaveLength(3)

    const said = (list.textContent ?? '').replace(/\s+/g, ' ')
    expect(CJK.test(said), said).toBe(false)
  })
})
