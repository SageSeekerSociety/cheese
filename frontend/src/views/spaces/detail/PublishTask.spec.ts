// 发题页：挂的是真页面、真表单、真附件那一格，接口在 `@/network/api/*` 那一层答着
// （真 axios 会被 `src/test/setup-network.ts` 逮住，未预期的请求直接判失败）。量的是人
// 在这一页上做一件事之后真的发出去了什么：
//
// 1. **必填空着点发布**：一个请求都不发，页头说还有几项；填好了才 `POST /tasks`，带的
//    是这一页装配的那份参数（空间、提交表、附件 id），发完落到「我发布的」。
// 2. **AI 指导**：沿用空间默认时不带它；为本题单独设置才带上整份。
// 3. **从文件导入**：读出一道就填进这张表；读出几道就逐道列出来，勾上的、改过的那几道
//    原样走批量发布，共用设置和原文件一起带上。不是 PDF、太大的，一个请求都不发。
// 4. **模板**：选一份，表单按它填好。
//
// i18n 装真的那一份并锁到 zh-CN；tiptap 换成壳 —— 它跟这一页要量的事无关。
import type { Component } from 'vue'

import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

const previewFromPdf = vi.fn()
const confirmFromPdf = vi.fn()
const createTask = vi.fn()
const listTasks = vi.fn()

const spaceDetail = vi.fn()
const listCategories = vi.fn()
const listDomainGroups = vi.fn()
const listMaterials = vi.fn()

const uploadAttachment = vi.fn()
const attachmentLimits = vi.fn()
const toastError = vi.fn()

vi.mock('@/network/api/tasks', () => ({
  TasksApi: {
    previewFromPdf: (...a: unknown[]) => previewFromPdf(...a),
    confirmFromPdf: (...a: unknown[]) => confirmFromPdf(...a),
    create: (...a: unknown[]) => createTask(...a),
    list: (...a: unknown[]) => listTasks(...a),
  },
}))

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spaceDetail(...a),
    listCategories: (...a: unknown[]) => listCategories(...a),
    listDomainGroups: (...a: unknown[]) => listDomainGroups(...a),
    // 参考资料那一格的候选。给空清单时那一格说的是「资料库里还没有文件」。
    listMaterials: (...a: unknown[]) => listMaterials(...a),
  },
}))

// 附件卡片选中即传（`POST /attachments`），发题请求带的是它回来的那串 id；卡片上那句
// 「单个文件不超过…」问的是 `GET /attachments/limits`（与上传同一道门）。
vi.mock('@/network/api/attachments', () => ({
  AttachmentsApi: {
    upload: (...a: unknown[]) => uploadAttachment(...a),
    limits: (...a: unknown[]) => attachmentLimits(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: (...a: unknown[]) => toastError(...a) } }))

// 富文本编辑器：跟这一页要量的四件事都无关，留一个能 `getText` 的壳。
vi.mock('@/components/common/Editor/TipTapEditor.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'TipTapEditorStub',
      setup(_, { expose }) {
        expose({ editor: { getText: () => '正文（测试）。' } })
        return () => h('div', { class: 'tiptap-editor' })
      },
    }),
  }
})

import TaskPublish from './PublishTask.vue'

import AccountService from '@/services/account'

const SPACE_ID = 7

/** 接口报的单份文件上限。**不是**任何一版页面里写过的数：卡片上那句话若对得上它，
 *  就只可能是照着接口报的写的。 */
const LIMIT_BYTES = 12_345_678

const MANAGER = { id: 2, username: 'alice', nickname: '爱丽丝' }
const MEMBER = { id: 3, username: 'bob', nickname: '鲍勃' }

const stub = { render: () => null }

// 转场都靠命名路由，路由得把这一页会指向的每一条都认得出来。
function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/tasks/publish', name: 'SpacesDetailPublishTask', component: stub },
      { path: '/spaces/:spaceId/tasks', name: 'SpacesDetailTasksList', component: stub },
      { path: '/spaces/:spaceId/manage/audit', name: 'SpacesDetailAuditTasks', component: stub },
      { path: '/spaces/:spaceId/tasks/:taskId', name: 'TasksDetail', component: stub },
      // 指导那一节里的「上传到资料库」照这个地址跳；不登记它，vue-router 每次都抱怨一句。
      { path: '/spaces/:spaceId/manage/settings/materials', component: stub },
    ],
  })
}

// Vuetify 的浮层摆放要读这几样，happy-dom 里没有（少一个就是一条未处理的 rejection）。
beforeAll(() => {
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    pageLeft: 0,
    pageTop: 0,
    scale: 1,
    addEventListener() {},
    removeEventListener() {},
  })
  setLocale('zh-CN')
})

afterAll(() => vi.unstubAllGlobals())

async function mount() {
  const router = makeRouter()
  await router.push(`/spaces/${SPACE_ID}/tasks/publish`)
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const view = render(TaskPublish as Component, {
    global: { plugins: [createVuetify({ components, directives }), router, pinia, i18n] },
  })
  // 表单是异步组件、空间也要先装 —— 等到它真画出来为止。第一条用例要冷加载
  // `TaskForm` 那一整棵依赖，CI 满载时远超默认的 1 秒（见过 40 秒那次红），所以给足。
  await waitFor(() => expect(view.container.querySelector('form')).not.toBeNull(), {
    timeout: 20_000,
  })
  return { ...view, router }
}

/** 页头那颗发布。 */
async function publish(view: ReturnType<typeof render>) {
  await fireEvent.click(view.getByTestId('publish-submit'))
  // 提交是异步的：给它一轮微任务与一次渲染。
  await new Promise((resolve) => setTimeout(resolve, 0))
}

/** 选中分类那枚下拉里的一项。选项画在浮层里，查询挂在 `document.body` 上找得到。 */
async function pickCategory(view: ReturnType<typeof render>, name = '基础题') {
  await fireEvent.mouseDown(view.getAllByRole('combobox')[0])
  await fireEvent.click(await view.findByRole('option', { name }))
  await nextTick()
}

/** 共用的那几项：参与方式、难度、分类。 */
async function fillSettings(view: ReturnType<typeof render>) {
  await fireEvent.click(view.getByRole('radio', { name: '个人' }))
  await fireEvent.click(view.getByRole('radio', { name: '初级' }))
  await pickCategory(view)
}

/** 必填都填上（名称加上共用的那几项）。 */
async function fillRequired(view: ReturnType<typeof render>, name = '用 gdb 定位一次段错误') {
  await fireEvent.update(view.getByLabelText('名称', { exact: false }), name)
  await fillSettings(view)
}

/** 点开「更多设置」，选「为本题单独设置」AI 指导。 */
async function ownTeaching(view: ReturnType<typeof render>) {
  await fireEvent.click(view.getByTestId('task-form-more'))
  await check(await view.findByRole('radio', { name: '为本题单独设置' }))
  await view.findByLabelText('对 AI 的要求')
}

/** 点一个原生单选框。走原生 click：`fireEvent.click` 派发的合成事件不翻 `checked`。 */
async function check(input: Element) {
  ;(input as HTMLInputElement).click()
  await nextTick()
}

/** 这条板子的分类/域名组/空间那一份（`activeCategories` 按 `displayOrder` 排、
 *  滤掉已归档，`TaskForm` 那枚下拉的选项就是它）。 */
function categoriesBody() {
  return { data: { categories: [{ id: 3, name: '基础题', displayOrder: 1, archivedAt: null }] } }
}

/** 空间那一份：角色是拿登录的人跟 `admins` 对出来的。 */
function spaceBody() {
  return {
    data: {
      space: {
        id: SPACE_ID,
        name: '数据结构空间',
        admins: [
          { role: 'OWNER', user: { id: 1, username: 'owner', nickname: '所有者' } },
          { role: 'ADMIN', user: MANAGER },
        ],
      },
    },
  }
}

/** 以某个人的身份打开这一页（角色由空间 store 按登录的人算）。 */
async function boardAs(user: { id: number; username: string; nickname: string }) {
  localStorage.setItem('user', JSON.stringify(user))
  AccountService.user = user as never
}

/** 从文件导入：选中就读。 */
async function importFile(view: ReturnType<typeof render>, file: File) {
  const input = view.getByTestId('publish-import-input') as HTMLInputElement
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  await fireEvent.change(input)
}

function pdfFile(name = '计算机系统基础-第五次作业.pdf', bytes = 1024): File {
  return new File([new Uint8Array(bytes)], name, { type: 'application/pdf' })
}

/** 真接口那一版返回：三样东西 + 每条草稿那几项（`PdfTaskDraftData`）。
 *  每次现造一份，免得用例之间互相改到同一份对象。 */
function previewBody() {
  return {
    drafts: [
      {
        name: '用 gdb 定位一次段错误',
        intro: '用 gdb 找出崩在哪一行。',
        description: '给定一段会崩的程序。\n\n![第 2 页-图 1](https://storage.test/task-images/a.png)',
        space: SPACE_ID,
        categoryId: 3,
      },
      {
        name: '手写一个最简内存分配器',
        intro: '实现 malloc / free 的最简版本。',
        description: '实现 malloc / free 的最简版本，说明碎片是怎么来的。',
        space: SPACE_ID,
        categoryId: 3,
      },
    ],
    templateUsed: { title: '计算机系统基础 · 标准题模板' },
    tokenUsed: 18742,
    // 预览这一步服务端顺带落好的附件行：原 PDF 一份、抽出的插图一张。id 是后端
    // 数据库里的号，前端只负责在勾中的时候原样报回去。
    attachments: {
      pdf: {
        id: 911,
        name: '计算机系统基础-第五次作业.pdf',
        size: 1024,
        contentType: 'application/pdf',
      },
      images: [
        {
          id: 912,
          name: 'input.pdf-0001-01.png',
          size: 2048,
          contentType: 'image/png',
        },
      ],
    },
  }
}

beforeEach(() => {
  spaceDetail.mockImplementation(async () => spaceBody())
  listCategories.mockImplementation(async () => categoriesBody())
  listDomainGroups.mockImplementation(async () => ({ data: { groups: [] } }))
  listMaterials.mockImplementation(async () => ({ data: { materials: [], canManage: false } }))
  listTasks.mockImplementation(async () => ({ data: { tasks: [], distinctParticipants: 0 } }))
  previewFromPdf.mockImplementation(async () => ({ data: previewBody() }))
  confirmFromPdf.mockImplementation(async () => ({
    data: { tasks: [{ id: 101, name: '用 gdb 定位一次段错误' }], count: 1 },
  }))
  createTask.mockImplementation(async () => ({ data: { task: { id: 501, approved: false } } }))
  uploadAttachment.mockImplementation(async ({ file }: { file: File }) => ({ data: { id: 41, file } }))
  // 故意不是任何「眼熟」的数（真部署那个默认是 100MB）：页面上若写了死数，下面对不上。
  attachmentLimits.mockImplementation(async () => ({ data: { maxFileBytes: LIMIT_BYTES } }))
})

afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.clearAllMocks()
})

describe('发题页：发一道', () => {
  it('必填空着：点发布一个请求都不发，页头说还有几项', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    expect(view.queryByTestId('publish-blocking')).toBeNull()

    await publish(view)

    await waitFor(() => expect(view.getByTestId('publish-blocking').textContent).toMatch(/\d+ 项未填写或填写有误/))
    expect(createTask).not.toHaveBeenCalled()
    expect(view.router.currentRoute.value.name).toBe('SpacesDetailPublishTask')
  })

  it('填好点发布：POST /tasks 带着空间、提交表和附件，发完落到「我发布的」', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view, '用 gdb 定位一次段错误（E2E 发的）')
    const input = view.getByTestId('attachment-input') as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [pdfFile('讲义.pdf')], configurable: true })
    await fireEvent.change(input)
    await waitFor(() => expect(view.getAllByTestId('attached-file')).toHaveLength(1))

    await publish(view)
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1))

    const sent = createTask.mock.calls[0][0] as Record<string, unknown>
    expect(sent.space).toBe(SPACE_ID)
    expect(sent.name).toBe('用 gdb 定位一次段错误（E2E 发的）')
    expect(sent.submitterType).toBe('USER')
    expect(sent.rank).toBe(1)
    expect(sent.categoryId).toBe(3)
    expect(sent.submissionSchema).toEqual([{ prompt: '提交文件', type: 'FILE' }])
    expect(sent.attachmentIds).toEqual([41])
    // 沿用空间的默认指导：不带这一项，让空间那一层生效。
    expect(sent.teaching).toBeUndefined()

    await waitFor(() => expect(view.router.currentRoute.value.name).toBe('SpacesDetailTasksList'))
    expect(view.router.currentRoute.value.query.filter).toBe('publishing')
  })

  it('为本题单独写了 AI 指导：整份带上', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)
    await ownTeaching(view)
    await fireEvent.update(view.getByLabelText('对 AI 的要求'), '不要直接给出答案。')

    await publish(view)
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1))

    expect((createTask.mock.calls[0][0] as { teaching?: { systemPrompt?: string } }).teaching?.systemPrompt).toBe(
      '不要直接给出答案。'
    )
  })

  it('选了「为本题单独设置」却一格没填：当作沿用默认，不带这一项', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)
    await ownTeaching(view)

    await publish(view)
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1))

    expect((createTask.mock.calls[0][0] as { teaching?: unknown }).teaching).toBeUndefined()
  })

  it('选一份模板：表单按它填好', async () => {
    await boardAs(MEMBER)
    spaceDetail.mockImplementation(async () => {
      const body = spaceBody()
      ;(body.data.space as Record<string, unknown>).taskTemplates = JSON.stringify([
        { name: '实验题', description: '每周实验', title: '实验 N：', content: '', submitterType: 'USER', rank: 2 },
      ])
      return body
    })
    const view = await mount()

    await fireEvent.click(view.getByRole('button', { name: '使用模板' }))
    await fireEvent.click(await view.findByText('实验题'))
    await pickCategory(view)
    await publish(view)
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1))

    const sent = createTask.mock.calls[0][0] as Record<string, unknown>
    expect(sent.name).toBe('实验 N：')
    expect(sent.rank).toBe(2)
  })
})

describe('发题页：从文件导入', () => {
  it('读出一道：就是这一道题的内容，接着在同一张表上发', async () => {
    await boardAs(MEMBER)
    previewFromPdf.mockImplementation(async () => {
      const body = previewBody()
      body.drafts = body.drafts.slice(0, 1)
      return { data: body }
    })
    const view = await mount()

    await importFile(view, pdfFile())
    await waitFor(() => expect(previewFromPdf).toHaveBeenCalledTimes(1))
    expect((previewFromPdf.mock.calls[0][0] as { file: File }).file.name).toBe('计算机系统基础-第五次作业.pdf')
    await waitFor(() =>
      expect((view.getByLabelText('名称', { exact: false }) as HTMLInputElement).value).toBe('用 gdb 定位一次段错误')
    )
    expect(view.queryByTestId('publish-drafts')).toBeNull()

    await fillSettings(view)
    await publish(view)
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1))
    const sent = createTask.mock.calls[0][0] as Record<string, unknown>
    expect(sent.name).toBe('用 gdb 定位一次段错误')
    // 描述带着从 PDF 里抽出的那张插图。
    const description = JSON.parse(sent.description as string)
    expect(JSON.stringify(description)).toContain('"type":"image"')
    expect(JSON.stringify(description)).toContain('https://storage.test/task-images/a.png')
    // 原 PDF 与插图放进了附件。
    expect(sent.attachmentIds).toEqual([911, 912])
  })

  it('读出几道：勾掉的不发，改过的按改过的发，共用设置和原文件一起带上', async () => {
    await boardAs(MEMBER)
    previewFromPdf.mockImplementation(async () => {
      const body = previewBody()
      body.drafts.push({ ...body.drafts[1], name: '附录：评分细则', intro: '评分细则。' })
      return { data: body }
    })
    const view = await mount()

    await importFile(view, pdfFile())
    await waitFor(() => expect(view.getByTestId('publish-drafts')).toBeTruthy())
    expect(view.getByTestId('publish-drafts').textContent).toContain('识别出 3 道题，已选 3 道')

    // 第三道不要。
    await check(view.getByRole('checkbox', { name: '选中 附录：评分细则' }))
    // 改第二道的名称。
    await fireEvent.click(view.getByText('手写一个最简内存分配器'))
    await fireEvent.update(view.getByLabelText('名称', { exact: false }), '手写一个内存分配器')

    await fillSettings(view)
    expect(view.getByTestId('publish-submit').textContent).toContain('发布 2 道题')
    await publish(view)
    await waitFor(() => expect(confirmFromPdf).toHaveBeenCalledTimes(1))

    const sent = confirmFromPdf.mock.calls[0][0] as {
      drafts: { name: string; intro: string }[]
      taskOptions: Record<string, unknown>
    }
    expect(sent.drafts.map((draft) => draft.name)).toEqual(['用 gdb 定位一次段错误', '手写一个内存分配器'])
    // 出处还写在简介里（审核队列与题目页认它）。
    expect(sent.drafts[0].intro.startsWith('【PDF · 第 1 页】')).toBe(true)
    expect(sent.taskOptions.space).toBe(SPACE_ID)
    expect(sent.taskOptions.rank).toBe(1)
    expect(sent.taskOptions.categoryId).toBe(3)
    expect(sent.taskOptions.attachmentIds).toEqual([911, 912])
    expect(createTask).not.toHaveBeenCalled()
    await waitFor(() => expect(view.router.currentRoute.value.query.filter).toBe('publishing'))
  })

  it('勾上的那几道里有一道没有名称：一个请求都不发，那一道标出来', async () => {
    await boardAs(MEMBER)
    previewFromPdf.mockImplementation(async () => {
      const body = previewBody()
      body.drafts[1].name = ''
      return { data: body }
    })
    const view = await mount()
    await importFile(view, pdfFile())
    await waitFor(() => expect(view.getByTestId('publish-drafts')).toBeTruthy())
    await fillSettings(view)

    await publish(view)

    await waitFor(() => expect(view.getByTestId('publish-blocking')).toBeTruthy())
    expect(view.getByTestId('publish-drafts').textContent).toContain('缺少名称')
    expect(confirmFromPdf).not.toHaveBeenCalled()
  })

  it('不是 PDF、或者太大：一个请求都不发，说清为什么', async () => {
    await boardAs(MEMBER)
    const view = await mount()

    await importFile(view, new File(['x'], '作业.docx', { type: 'application/msword' }))
    await importFile(view, pdfFile('太大.pdf', 16 * 1024 * 1024))

    expect(previewFromPdf).not.toHaveBeenCalled()
    expect(toastError.mock.calls.map(([message]) => message)).toEqual(['只能导入 PDF 文件', '文件超过 15 MB'])
  })
})
