/**
 * 这一包什么时候去读一份文件上的评论串。
 *
 * 判据只有一个：**正文上要画标记的那些文件，栏关着也得读**。标记是从线程列表算出来的
 * （`PanelPreviewView` 的 `threadAnchors` → `PreviewMarkdown`），等读者自己去点开评论栏
 * 才读，就等于「哪儿有人评过」要他先想到去开栏才看得见，而那正是标记要替他省掉的一步。
 *
 * 反过来，不画标记的读法（docx、表格、图片……）仍然等栏打开再读：预览每一格打开都多打
 * 一次接口，不值。
 */
import { createApp, defineComponent, ref } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { usePanelPreviewComments } from './comments'
import { createPreviewState } from './previewState'

const api = vi.hoisted(() => ({ listFileThreads: vi.fn(), postFileThread: vi.fn() }))
vi.mock('../../api/fileThreads', () => api)

const THREAD = {
  comment: {
    id: 'c1',
    author: 'li',
    content: '这句话的用时要不要一起写进去？',
    anchor_quote: null,
    anchor: null,
    created_at: '',
  },
  revision: 1,
  state: 'open',
  replies: [],
}

const apps: ReturnType<typeof createApp>[] = []

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('user', JSON.stringify({ id: 'human', username: 'human' }))
  vi.resetAllMocks()
  api.listFileThreads.mockResolvedValue({ data: [THREAD] })
})

afterEach(() => apps.splice(0).forEach((app) => app.unmount()))

/** 一份摆进预览格里的文件，和它那个评论包。 */
function setup(path: string, docPage = () => false) {
  const state = createPreviewState()
  state.previewFile.value = {
    path,
    content: '# 配置\n\n失败以后重试 3 次。\n',
    version: 'v1',
    bytes: 20,
    binary: false,
    too_large: false,
  } as never
  let bundle!: ReturnType<typeof usePanelPreviewComments>
  const app = createApp(
    defineComponent({
      setup() {
        bundle = usePanelPreviewComments({
          props: { topicId: 'room', projectId: 'project', members: [] } as never,
          state,
          people: { names: ref({}), avatars: ref({}) } as never,
          docPage,
        })
        return () => null
      },
    })
  )
  app.mount(document.createElement('div'))
  apps.push(app)
  return bundle
}

it('markdown：评论栏还关着，也把这一份的评论读回来——正文上的标记要从它来', async () => {
  const bundle = setup('output/说明.md')

  await vi.waitFor(() => expect(bundle.threads.state.threads).toHaveLength(1))
  expect(bundle.open.value).toBe(false)
  expect(api.listFileThreads).toHaveBeenCalledWith('room', 'output/说明.md')
})

it('表格：评论栏还关着，也把这一份的评论读回来——格子上的标记要从它来', async () => {
  // 和 markdown、分页那两档同一条理由：格子上那几颗评论标记是从线程列表算出来的。
  // CSV 没有工作表名，指一格用的就是 `B7` 这种地址。
  const bundle = setup('output/报价.csv')

  await vi.waitFor(() => expect(bundle.threads.state.threads).toHaveLength(1))
  expect(bundle.open.value).toBe(false)
  expect(api.listFileThreads).toHaveBeenCalledWith('room', 'output/报价.csv')
})

it('幻灯片：页上画不出标记，栏关着就别读——那一次请求换不来任何看得见的东西', async () => {
  // 幻灯片也是 `view === 'pages'`（转成 PDF 按页看），但页视图那一档不画评论标记
  // （它上面评的是整页，不是页上的一点）。按 `view` 判就会把每一份 pptx 都圈进来，
  // 每打开一次白打一次评论接口。
  const bundle = setup('output/汇报.pptx')
  await Promise.resolve()
  expect(api.listFileThreads).not.toHaveBeenCalled()

  // 栏打开时照读：那一栏里该有的评论还在。
  bundle.toggle()
  await vi.waitFor(() => expect(bundle.threads.state.threads).toHaveLength(1))
})

it('这一份读成了网页：页上画不出标记（它在沙箱帧里），栏关着就别读', async () => {
  const bundle = setup('output/报价.docx', () => true)
  await Promise.resolve()
  expect(api.listFileThreads).not.toHaveBeenCalled()

  // 切回分页那一档，标记又要画了：这时候该读。
  const paged = setup('output/报价.docx', () => false)
  await vi.waitFor(() => expect(paged.threads.state.threads).toHaveLength(1))
  expect(bundle.open.value).toBe(false)
})

it('资料库原件不是这个房间的文件：哪一档都不读', async () => {
  setup('library/说明.md')
  await Promise.resolve()
  expect(api.listFileThreads).not.toHaveBeenCalled()
})

it('分页文档：评论栏还关着，也把这一份的评论读回来——页上的标记要从它来', async () => {
  // 和 markdown 同一条理由：页上那几颗评论标记是从线程列表算出来的。只读 markdown
  // 那一档的话，PDF / 转成 PDF 的 Office 上就一个标记都不会出现，而这一档恰恰是
  // 页钉锚点所在的地方。
  const bundle = setup('output/报价.pdf')

  await vi.waitFor(() => expect(bundle.threads.state.threads).toHaveLength(1))
  expect(bundle.open.value).toBe(false)
  expect(api.listFileThreads).toHaveBeenCalledWith('room', 'output/报价.pdf')
})

it('表格读成了网页：格子上画不出来（那一支挂的是网页），栏关着就别读', async () => {
  // `.xlsx` 有网页这一种读法（`PAGE_FORMATS` 认它）。切过去之后上屏的是网页那一支，
  // 表格查看器根本不在——先按「表格画得出标记」放行的写法会让每打开一份这样的表
  // 白打一次评论接口。
  const bundle = setup('output/预算.xlsx', () => true)
  await Promise.resolve()
  expect(api.listFileThreads).not.toHaveBeenCalled()

  // 切回表格那一档，该读。
  const sheet = setup('output/预算.xlsx', () => false)
  await vi.waitFor(() => expect(sheet.threads.state.threads).toHaveLength(1))
  expect(bundle.open.value).toBe(false)
})
