/**
 * 「在 PDF 页面上指一点」这条出口。
 *
 * 交出去的不是一句「第 3 页 42% 处」那么简单：受话人拿这句话回原文件里找，找到的是
 * 同一页，可上一版和这一版之间那一处可能整个挪过位。所以这条出口交出的是
 * 「带版本的那一点」加一张当时那一页的样子——这一条守的就是两样都在，以及版本对不上
 * 时宁可不发。
 */
import type { UploadAnnotation } from '../../composables/usePanelPreview'
import type { SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { defineComponent, h } from 'vue'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PanelPreviewView from './PanelPreviewView.vue'

import { setLocale } from '@/i18n'
import { previewBundles } from '@/test/panelBundles'

const page = vi.hoisted(() => ({
  blob: null as Blob | null,
  version: null as string | null,
  /** 页面查看器被要求撤掉记号几次。 */
  cleared: 0,
  /** 让页面查看器从外面「重排」一次。 */
  drop: null as (() => void) | null,
}))

vi.mock('./preview/PreviewPages.vue', () => ({
  default: defineComponent({
    props: ['data', 'context'],
    emits: ['quote', 'pin', 'dropped'],
    setup(props: { context: { version: string } }, { emit, expose }) {
      page.drop = () => emit('dropped')
      expose({
        snapshot: async () => (page.blob ? { blob: page.blob, width: 1200, height: 1600 } : null),
        clearMark: () => {
          page.cleared += 1
        },
      })
      return () =>
        h('div', { 'data-testid': 'pages' }, [
          h(
            'button',
            {
              onClick: () =>
                emit('pin', {
                  page: 3,
                  x: 0.42,
                  y: 0.17,
                  // 版本对不上的那一版：读者手里的页面已经过期了。
                  context: { ...props.context, version: page.version ?? props.context.version },
                }),
            },
            'pin'
          ),
        ])
    },
  }),
}))
vi.mock('./preview/RevisionList.vue', () => ({ default: { template: '<div />' } }))
vi.mock('./preview/RoomOutputs.vue', () => ({ default: { template: '<div />' } }))

const docBytes = new ArrayBuffer(8)
const context = {
  topicId: 'room',
  path: 'deck.pdf',
  source: 'committed' as const,
  taskId: 'task',
  version: 'v7',
}
const props = {
  ...previewBundles(),
  topicId: 'room',
  projectId: 'project',
  path: 'deck.pdf',
  frameName: 'frame',
  loading: false,
  refreshing: false,
  previewFile: {
    path: 'deck.pdf',
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
  previewNamedPath: 'deck.pdf',
  previewError: null,
  previewReadError: null,
  documentSuffix: 'pdf',
  documentType: { view: 'pages' as const, label: 'document', icon: 'mdi-file-pdf-box' },
  documentName: 'deck.pdf',
  isImageArtifact: false,
  downloadError: '',
  docBytes,
  docIdentity: context,
  docSnapshot: { bytes: docBytes, identity: context, sourceVersion: context.version },
  docLoading: false,
  docError: '',
  docRendererMissing: false,
  slideContext: context,
}

beforeEach(() => {
  setLocale('zh-CN')
  page.blob = new Blob(['png'], { type: 'image/png' })
  page.version = null
  page.cleared = 0
  page.drop = null
})
afterEach(cleanup)

function mount(submitQuestion?: SubmitPreviewQuestion, uploadAnnotation?: UploadAnnotation) {
  return render(PanelPreviewView, {
    props: { ...props, submitQuestion, uploadAnnotation },
    global: {
      stubs: {
        VBtn: { template: '<button><slot /></button>' },
        VIcon: true,
        VSpacer: true,
        VAlert: true,
        VChip: true,
        VDialog: true,
        VProgressCircular: true,
      },
    },
  })
}

it('指的一点连同那一页的截图发出去，带的是已确认的文件身份', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const upload = vi.fn().mockResolvedValue({ id: 'a1', name: 'page-3.png' })
  const ui = mount(submit as unknown as SubmitPreviewQuestion, upload as unknown as UploadAnnotation)

  await fireEvent.click(ui.getByText('pin'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), '这里不对')
  await fireEvent.click(ui.getByText('发送'))

  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1))
  expect(upload).toHaveBeenCalledWith('room', { blob: page.blob, filename: 'page-3.png' })
  const request = submit.mock.calls[0]?.[0]
  expect(request.content).toBe('这里不对')
  expect(request.attachments).toHaveLength(1)
  expect(request.quotedContext).toEqual({
    kind: 'page-pin',
    path: 'deck.pdf',
    source: 'committed',
    task_id: 'task',
    version: 'v7',
    page: 3,
    x: 0.42,
    y: 0.17,
  })
})

it('截图没传上去也照发：位置那句话自己站得住', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const upload = vi.fn().mockRejectedValue(new Error('上传失败'))
  const ui = mount(submit as unknown as SubmitPreviewQuestion, upload as unknown as UploadAnnotation)

  await fireEvent.click(ui.getByText('pin'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), '这里不对')
  await fireEvent.click(ui.getByText('发送'))

  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1))
  expect(submit.mock.calls[0]?.[0].attachments).toBeUndefined()
  expect(submit.mock.calls[0]?.[0].quotedContext.kind).toBe('page-pin')
})

it('指的那一版跟屏上这一份对不上时，连输入框都不给开', async () => {
  page.version = 'v6'
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount(submit as unknown as SubmitPreviewQuestion)

  await fireEvent.click(ui.getByText('pin'))

  expect(ui.queryByPlaceholderText('说明要改什么')).toBeNull()
  expect(submit).not.toHaveBeenCalled()
})

it('发出去之后让页面查看器把记号撤掉，屏上不留已经交出去的那一点', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const upload = vi.fn().mockResolvedValue({ id: 'a1', name: 'page-3.png' })
  const ui = mount(submit as unknown as SubmitPreviewQuestion, upload as unknown as UploadAnnotation)

  const before = page.cleared
  await fireEvent.click(ui.getByText('pin'))
  await fireEvent.update(ui.getByPlaceholderText('说明要改什么'), '这里不对')
  await fireEvent.click(ui.getByText('发送'))

  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1))
  expect(page.cleared).toBeGreaterThan(before)
})

it('页面重排把记号撤了，正在编的那句话也跟着收起来', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount(submit as unknown as SubmitPreviewQuestion)

  await fireEvent.click(ui.getByText('pin'))
  expect(ui.queryByPlaceholderText('说明要改什么')).not.toBeNull()

  page.drop?.()

  await waitFor(() => expect(ui.queryByPlaceholderText('说明要改什么')).toBeNull())
  expect(submit).not.toHaveBeenCalled()
})
