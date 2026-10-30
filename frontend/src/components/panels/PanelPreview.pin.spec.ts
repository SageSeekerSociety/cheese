/**
 * 「在 PDF 页面上指一点」这条出口。
 *
 * 交出去的不是一句「第 3 页 42% 处」那么简单：受话人拿这句话回原文件里找，找到的是
 * 同一页，可上一版和这一版之间那一处可能整个挪过位。所以这条出口交出的是
 * 「带版本的那一点」加一张当时那一页的样子——这一条守的就是两样都在，以及版本对不上
 * 时宁可不发。
 */
import type { PropType } from 'vue'
import type { UploadAnnotation } from '../../composables/usePanelPreview'
import type { SubmitPreviewQuestion } from '../../lib/previewQuestion'

import { computed, defineComponent, h, reactive, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
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
    props: {
      data: { type: Object as PropType<ArrayBuffer | null>, default: null },
      context: { type: Object as PropType<typeof context | undefined>, default: undefined },
      marks: { type: Array as PropType<readonly { id: string }[]>, default: () => [] },
      activeMark: { type: String as PropType<string | null>, default: null },
    },
    emits: ['quote', 'pin', 'dropped', 'mark'],
    setup(
      props: { context: { version: string }; marks: readonly { id?: string }[]; activeMark?: string | null },
      { emit, expose }
    ) {
      page.drop = () => emit('dropped')
      expose({
        snapshot: async () => (page.blob ? { blob: page.blob, width: 1200, height: 1600 } : null),
        clearMark: () => {
          page.cleared += 1
        },
      })
      return () =>
        h('div', { 'data-testid': 'pages' }, [
          // 外面递进来的评论标记原样写出来，好让面板那一层的用例读得到（真组件里
          // 它们画成页上的小点）。
          h('span', {
            'data-testid': 'marks',
            'data-marks': JSON.stringify(props.marks ?? []),
            'data-active': props.activeMark ?? '',
          }),
          // 读者点了页上某一颗评论标记。
          h(
            'button',
            {
              'data-testid': 'open-mark',
              onClick: () => emit('mark', (props.marks?.[0] as { id?: string } | undefined)?.id ?? ''),
            },
            'mark'
          ),
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
      // 文档条上「编辑」开不开看屏幕宽窄（`PreviewDocStage` 用了 `useDisplay`），
      // 这一层得接上它。
      plugins: [createVuetify({ components, directives })],
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

/** 挂整格，评论那一包按给的那一份来（`mount` 给的是默认那份，没有评论）。
 *
 *  这一格带着评论栏，它用到了 Vuetify，所以这里要接上（`mount` 那条路没有评论栏）。 */
function mountWithComments(comments: ReturnType<typeof commentsWith>, over: Record<string, unknown> = {}) {
  return render(PanelPreviewView, {
    props: { ...props, comments: comments as never, ...over },
    global: {
      plugins: [createVuetify({ components, directives })],
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
  await fireEvent.update(ui.getByPlaceholderText('输入修改要求或批注内容…'), '这里不对')
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
  await fireEvent.update(ui.getByPlaceholderText('输入修改要求或批注内容…'), '这里不对')
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

  expect(ui.queryByPlaceholderText('输入修改要求或批注内容…')).toBeNull()
  expect(submit).not.toHaveBeenCalled()
})

it('发出去之后让页面查看器把记号撤掉，屏上不留已经交出去的那一点', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const upload = vi.fn().mockResolvedValue({ id: 'a1', name: 'page-3.png' })
  const ui = mount(submit as unknown as SubmitPreviewQuestion, upload as unknown as UploadAnnotation)

  const before = page.cleared
  await fireEvent.click(ui.getByText('pin'))
  await fireEvent.update(ui.getByPlaceholderText('输入修改要求或批注内容…'), '这里不对')
  await fireEvent.click(ui.getByText('发送'))

  await waitFor(() => expect(submit).toHaveBeenCalledTimes(1))
  expect(page.cleared).toBeGreaterThan(before)
})

it('页面重排把记号撤了，正在编的那句话也跟着收起来', async () => {
  const submit = vi.fn().mockReturnValue(true)
  const ui = mount(submit as unknown as SubmitPreviewQuestion)

  await fireEvent.click(ui.getByText('pin'))
  expect(ui.queryByPlaceholderText('输入修改要求或批注内容…')).not.toBeNull()

  page.drop?.()

  await waitFor(() => expect(ui.queryByPlaceholderText('输入修改要求或批注内容…')).toBeNull())
  expect(submit).not.toHaveBeenCalled()
})

/** 评论那一包：几条线程各挂一个锚点，栏关着（点标记才该把它打开）。 */
function commentsWith(anchors: (Record<string, unknown> | null)[], state: (at: number) => string = () => 'open') {
  const open = ref(false)
  return {
    target: computed(() => ({ topicId: 'room', path: 'deck.pdf', version: 'v7' })),
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

/** 递给页面查看器的那几条标记（桩把它们写成 JSON 了）。 */
function marksOn(ui: ReturnType<typeof mount>): { id: string; page: number; x: number; y: number }[] {
  const el = ui.container.querySelector('[data-testid="marks"]') as HTMLElement | null
  return el ? JSON.parse(el.dataset.marks ?? '[]') : []
}

const PAGE_PIN = {
  kind: 'page-pin',
  path: 'deck.pdf',
  source: 'committed',
  task_id: 'task',
  version: 'v7',
  page: 3,
  x: 0.42,
  y: 0.17,
}

it('只把这版上没解决的页钉交给页面查看器，别的锚点和别的版本都不给', async () => {
  // 三条线程：一条是这一版上的页钉（给），一条记在上一版（不给——第 3 页那一处在那一版
  // 可能是别的东西），一条是**别的种类**的锚点（不给：它说的是「第 3 页整页」，不是页上
  // 的一点；按页码和比例把它画成一颗小点，读者会以为有人评论了那一页上的某一处）。
  const comments = commentsWith([
    PAGE_PIN,
    { ...PAGE_PIN, version: 'v6' },
    // 别的种类、但也带着页码和比例：以后新加一种锚点、正好也带着这两样时，别把
    // 它当页上的评论点画出来。
    { kind: 'slide-page', version: 'v7', page: 3, x: 0.42, y: 0.17, text: '第三页整页' },
  ])
  const ui = mountWithComments(comments)
  await waitFor(() => expect(marksOn(ui)).toHaveLength(1))
  expect(marksOn(ui)[0]).toEqual({ id: 'c1', page: 3, x: 0.42, y: 0.17 })
})

it('屏上还是上一版的字节时不画：新一版记下的位置摆在旧一版的页上会指到别处', async () => {
  // 换了一版时文件元数据先变，页上的字节还在路上（或者它转不出来，旧的那份就那样留着）。
  // 照文件那一版放行的话，新一版记下的「第 3 页 42%」会画在旧一版的第 3 页上——那是完全
  // 不同的地方。`slideContext` 只在屏幕上那份字节核过时给得出，所以它不给就不画。
  const comments = commentsWith([PAGE_PIN])
  const ui = mountWithComments(comments, { slideContext: undefined })
  await Promise.resolve()
  expect(marksOn(ui)).toEqual([])
})

it('已解决的评论不交给页面查看器：那一条说完了，不该还压在页上', async () => {
  const comments = commentsWith([PAGE_PIN], () => 'resolved')
  const ui = mountWithComments(comments)
  await Promise.resolve()
  expect(marksOn(ui)).toEqual([])
})

it('正在看的那一条，一直传到页视图那一层', async () => {
  // 面板 → 文档档 → 页视图这条线：少接一环，「正在看的那条画得重些」就永远不会
  // 发生，而页视图那一层自己的用例测不出这个（它只喂 prop）。
  const comments = commentsWith([PAGE_PIN, { ...PAGE_PIN, page: 1, x: 0.1, y: 0.1 }])
  const ui = mountWithComments(comments)
  await waitFor(() => expect(marksOn(ui)).toHaveLength(2))

  await fireEvent.click(ui.getByTestId('open-mark'))

  const el = ui.container.querySelector('[data-testid="marks"]') as HTMLElement
  await waitFor(() => expect(el.dataset.active).toBe('c1'))
})

it('点了页上某一处标记，把评论栏开在那一条上', async () => {
  const comments = commentsWith([PAGE_PIN])
  const ui = mountWithComments(comments)
  await waitFor(() => expect(marksOn(ui)).toHaveLength(1))
  expect(comments.open.value).toBe(false)

  await fireEvent.click(ui.getByText('pin'))
  // 点了标记（页视图桩把这件事直接发上来，等于读者点了页上那一颗）。
  await fireEvent.click(ui.getByTestId('open-mark'))

  await waitFor(() => expect(comments.open.value).toBe(true))
  // 开的是**被点的**那一条，不是笼统地把栏打开：一屏好几条评论时，只开栏读者还得自己找。
  const active = ui.container.querySelector('[data-testid="marks"]') as HTMLElement
  await waitFor(() => expect(active.dataset.active).toBe('c1'))
})
