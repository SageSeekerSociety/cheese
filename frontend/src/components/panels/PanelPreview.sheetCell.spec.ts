/**
 * 表格那一支的指认：指着单元格说一句话，交出去的是「文件 + 地址 + 这一格当时的内容」。
 *
 * 和幻灯片、页上一点走的是同一条出口（`preview/usePreviewLocator.ts` 的 `sendLocator`），区别只在
 * 位置长什么样：表格的位置是文件自己给的地址，不需要一次转换，也没有页码。这一条守的
 * 就是地址、内容、文件身份三样都在，以及拿不到文件身份时退回拼一句话那条老路。
 */
import type { SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { computed, h, reactive, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PanelPreviewView from './PanelPreviewView.vue'

import { setLocale } from '@/i18n'
import { previewBundles } from '@/test/panelBundles'

// 真查看器用 exceljs 读字节，不属于这一格要证明的事。替身报出的是读者点一格时真查看器
// 会发的那一个事件：工作簿带工作表名，CSV 没有（和 `PreviewSheet` 一致）。
vi.mock('./preview/PreviewSheet.vue', () => ({
  default: {
    props: ['data', 'kind', 'marks', 'activeMark'],
    emits: ['cell', 'mark'],
    setup(
      props: { kind?: string; marks?: readonly { id: string }[]; activeMark?: string | null },
      { emit }: { emit: (e: string, p?: unknown) => void }
    ) {
      return () =>
        h('div', { 'data-testid': 'sheet' }, [
          h(
            'button',
            {
              onClick: () =>
                emit('cell', {
                  address: 'B7',
                  value: '1200',
                  sheet: props.kind === 'csv' ? '' : 'Sheet1',
                }),
            },
            'cell'
          ),
          h('span', {
            'data-testid': 'marks',
            'data-marks': JSON.stringify(props.marks ?? []),
            'data-active': props.activeMark ?? '',
          }),
          h('button', { 'data-testid': 'open-mark', onClick: () => emit('mark', props.marks?.[0]?.id ?? '') }, 'mark'),
        ])
    },
  },
}))
vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))

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
    slideContext: context,
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
  await fireEvent.update(ui.getByPlaceholderText('就这一处说点什么'), '这个数字按季度摊')
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
  await fireEvent.update(ui.getByPlaceholderText('就这一处说点什么'), '这一行重复了')
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
  await fireEvent.update(ui.getByPlaceholderText('就这一处说点什么'), '这个数字按季度摊')
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
  await fireEvent.update(ui.getByPlaceholderText('就这一处说点什么'), '这个数字按季度摊')
  await fireEvent.click(ui.getByText('发送'))

  expect(submit).toHaveBeenCalledTimes(1)
  const [payload] = ui.emitted().locate as { message: string }[][]
  expect(payload[0].message).toContain('B7')
  expect(payload[0].message).toContain('这个数字按季度摊')
})

/** 评论那一包：几条线程各挂一个锚点，栏关着（点标记才该把它打开）。 */
function commentsWith(anchors: (Record<string, unknown> | null)[], state: (at: number) => string = () => 'open') {
  const open = ref(false)
  return {
    target: computed(() => ({ topicId: 'room', path: 'budget.xlsx', version: 'v7' })),
    open,
    toggle: () => (open.value = !open.value),
    author: 'me',
    threads: {
      state: reactive({
        threads: anchors.map((anchor, at) => ({
          comment: {
            id: `c${at + 1}`,
            author: 'li',
            content: `第 ${at + 1} 条`,
            anchor_quote: null,
            anchor,
            created_at: '',
          },
          revision: 1,
          state: state(at),
          replies: [],
        })),
        activity: {},
        errors: {},
        busy: false,
        unknown: null,
      }),
      actions: {},
      refresh: async () => {},
    },
    post: async () => {},
    nameOf: (h: string) => h,
    avatarOf: () => '',
  }
}

/** 递给表格查看器的那几条标记（桩把它们写成 JSON 了）。 */
function marksOn(ui: ReturnType<typeof mount>): { id: string; sheet: string; address: string }[] {
  const el = ui.container.querySelector('[data-testid="marks"]') as HTMLElement | null
  return el ? JSON.parse(el.dataset.marks ?? '[]') : []
}

const SHEET_CELL = {
  kind: 'sheet-cell',
  path: 'budget.xlsx',
  source: 'committed',
  task_id: 'task',
  version: 'v7',
  sheet: 'Sheet1',
  address: 'B7',
  value: '1200',
}

it('只把这版上没解决的格子评论交给表格，别的锚点和别的版本都不给', async () => {
  // 三条：一条是这一版上的格子评论（给），一条记在上一版（不给——B7 在那一版可能是
  // 别的东西），一条是别的种类的锚点（不给：它说的可能是正文里一句话，画到格子上是
  // 凭空多出来的东西）。
  const comments = commentsWith([
    SHEET_CELL,
    { ...SHEET_CELL, version: 'v6' },
    { kind: 'text-range', version: 'v7', text: '一句话', heading: null, prefix: '', suffix: '' },
  ])
  const ui = mount(undefined, { comments: comments as never })
  await waitFor(() => expect(marksOn(ui)).toHaveLength(1))
  expect(marksOn(ui)[0]).toEqual({ id: 'c1', sheet: 'Sheet1', address: 'B7' })
})

it('屏上还是上一版的字节时不画：新一版记下的地址摆在旧一版上会指到别处', async () => {
  // 换版时文件元数据先变，屏幕上的字节还在路上（或者读不出来，旧的那份就那样留着）。
  // 照文件那一版放行的话，新一版记下的 B7 会画在旧一版的格子上。
  const comments = commentsWith([SHEET_CELL])
  const ui = mount(undefined, { comments: comments as never, slideContext: undefined })
  await Promise.resolve()
  expect(marksOn(ui)).toEqual([])
})

it('已解决的评论不交给表格：那一条说完了，不该还压在格子上', async () => {
  const comments = commentsWith([SHEET_CELL], () => 'resolved')
  const ui = mount(undefined, { comments: comments as never })
  await Promise.resolve()
  expect(marksOn(ui)).toEqual([])
})

it('点了格子上某一颗标记，把评论栏开在那一条上', async () => {
  const comments = commentsWith([SHEET_CELL])
  const ui = mount(undefined, { comments: comments as never })
  await waitFor(() => expect(marksOn(ui)).toHaveLength(1))
  expect(comments.open.value).toBe(false)

  await fireEvent.click(ui.getByTestId('open-mark'))

  await waitFor(() => expect(comments.open.value).toBe(true))
  const el = ui.container.querySelector('[data-testid="marks"]') as HTMLElement
  await waitFor(() => expect(el.dataset.active).toBe('c1'))
})
