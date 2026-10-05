/**
 * 表格那一支的指认：指着单元格说一句话，交出去的是「文件 + 地址 + 这一格当时的内容」。
 *
 * 和幻灯片、页上一点走的是同一条出口（`PanelPreviewView` 的 `sendLocator`），区别只在
 * 位置长什么样：表格的位置是文件自己给的地址，不需要一次转换，也没有页码。这一条守的
 * 就是地址、内容、文件身份三样都在，以及拿不到文件身份时退回拼一句话那条老路。
 */
import type { SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PanelPreviewView from './PanelPreviewView.vue'

import { setLocale } from '@/i18n'
import { previewBundles } from '@/test/panelBundles'

// 真查看器用 exceljs 读字节，不属于这一格要证明的事。替身报出的是读者点一格时真查看器
// 会发的那一个事件：工作簿带工作表名，CSV 没有（和 `PreviewSheet` 一致）。
vi.mock('./preview/PreviewSheet.vue', () => ({
  default: {
    props: ['data', 'kind'],
    emits: ['cell'],
    template:
      '<div data-testid="sheet">' +
      "<button @click=\"$emit('cell', { address: 'B7', value: '1200', sheet: kind === 'csv' ? '' : 'Sheet1' })\">cell</button>" +
      '</div>',
  },
}))
vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))
vi.mock('./preview/RoomOutputs.vue', () => ({ default: { template: '<div />' } }))

const docBytes = new ArrayBuffer(8)
const context = {
  topicId: 'room',
  path: 'budget.xlsx',
  source: 'committed' as const,
  taskId: 'task',
  version: 'v7',
}

function props(overrides: Record<string, unknown> = {}) {
  return {
    ...previewBundles(),
    topicId: 'room',
    projectId: 'project',
    frameName: 'frame',
    loading: false,
    refreshing: false,
    previewFile: {
      path: 'budget.xlsx',
      content: null,
      version: 'v7',
      bytes: 8,
      binary: true,
      too_large: false,
      source: context.source,
    },
    previewMime: '',
    previewNamed: true,
    previewUrl: null,
    previewAppNote: '',
    previewTunnelUp: false,
    previewNamedPath: 'budget.xlsx',
    previewError: null,
    previewReadError: null,
    documentSuffix: 'xlsx',
    documentType: {
      view: 'sheet' as const,
      label: 'spreadsheet',
      icon: 'mdi-file-excel-outline',
      sheet: 'workbook' as const,
    },
    documentName: 'budget.xlsx',
    isImageArtifact: false,
    downloadError: '',
    docBytes,
    docLoading: false,
    docError: '',
    docRendererMissing: false,
    docIdentity: context,
    docSnapshot: { bytes: docBytes, identity: context, sourceVersion: context.version },
    ...overrides,
  }
}

beforeEach(() => setLocale('zh-CN'))
afterEach(cleanup)

function mount(submitQuestion?: SubmitPreviewQuestion, overrides: Record<string, unknown> = {}) {
  return render(PanelPreviewView, {
    props: { ...props(), submitQuestion, ...overrides },
    global: {
      plugins: [createVuetify({ components, directives })],
      stubs: {
        VBtn: { template: '<button><slot /></button>' },
        VIcon: true,
        VSpacer: true,
        VAlert: true,
        VDialog: true,
      },
    },
  })
}

it('点一格发出去时带的是带文件身份的 sheet-cell 引用', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount(submit as unknown as SubmitPreviewQuestion)
  await fireEvent.click(ui.getByText('cell'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), '这个数字按季度摊')
  await fireEvent.click(ui.getByText('发送'))

  const request = submit.mock.calls[0]?.[0]
  expect(request).toMatchObject({ topicId: 'room', intent: 'ask-agent', content: '这个数字按季度摊' })
  expect(request.quotedContext).toEqual({
    kind: 'sheet-cell',
    path: 'budget.xlsx',
    source: 'committed',
    version: 'v7',
    task_id: 'task',
    sheet: 'Sheet1',
    address: 'B7',
    value: '1200',
  })
  // 走的是结构化那条路，不再额外拼一句话。
  expect(ui.emitted().locate).toBeUndefined()
})

it('CSV 没有工作表名，引用里 sheet 是空串，地址就是它自己', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount(submit as unknown as SubmitPreviewQuestion, {
    documentSuffix: 'csv',
    documentType: { view: 'sheet', label: 'spreadsheet', icon: 'mdi-file-delimited-outline', sheet: 'csv' },
  })
  await fireEvent.click(ui.getByText('cell'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), '这一行重复了')
  await fireEvent.click(ui.getByText('发送'))

  expect(submit.mock.calls[0]?.[0].quotedContext).toMatchObject({
    kind: 'sheet-cell',
    sheet: '',
    address: 'B7',
    value: '1200',
  })
})

it('拿不到文件身份时退回拼一句话那条老路', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount(submit as unknown as SubmitPreviewQuestion, { docIdentity: null })
  await fireEvent.click(ui.getByText('cell'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), '这个数字按季度摊')
  await fireEvent.click(ui.getByText('发送'))

  expect(submit).not.toHaveBeenCalled()
  const [payload] = ui.emitted().locate as { message: string }[][]
  expect(payload[0].message).toContain('B7')
  expect(payload[0].message).toContain('这个数字按季度摊')
})

it('提问出口不收（房间里没有芝士）时，这句话照旧作为一句话发进房间', async () => {
  const submit = vi.fn().mockReturnValue(false)
  const ui = mount(submit as unknown as SubmitPreviewQuestion)
  await fireEvent.click(ui.getByText('cell'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), '这个数字按季度摊')
  await fireEvent.click(ui.getByText('发送'))

  expect(submit).toHaveBeenCalledTimes(1)
  const [payload] = ui.emitted().locate as { message: string }[][]
  expect(payload[0].message).toContain('B7')
  expect(payload[0].message).toContain('这个数字按季度摊')
})
