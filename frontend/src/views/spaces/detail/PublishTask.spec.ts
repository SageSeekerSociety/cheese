// 发题页 —— **这一页自己画的那一版**（第十批），挂的是真页面、真表单、真附件卡片。
//
// 第五批到第九批里这一页是「新外壳那一层裹着老发题页」，所以那时候的这一份量的是
// 那一层（接缝另有 `wrappers.spec.ts`，PDF 那一半替掉表单）。这一批底下不再有老页，
// 那份接缝连同 `wrappers.spec.ts` 一起没了，玩法也跟着换：**一个替身都不留**，
// 量的是真跑起来会发生什么 ——
//
// 1. **三栏必填空着**：清单列的就是真表单现在拦的那几条（不是这一页另写一份规则），
//    两颗提交按钮都按不动，点真表单那颗「提交」一个请求都不发。
// 2. **选满之后**：清单空了、按钮能点，点下去真的 `POST /tasks`，带的是**这一页自己
//    装配**的那份参数（空间、提交表、附件 id），发完落到新外壳自己的「我的」。
// 3. **PDF 那条路**：`preview` → `confirm` 都是真接口，确认之后**就地给回执**、
//    不跳走；附件那两颗勾只画接口真落了文件行的那几个。
// 4. **材料**：附件卡片也只画接口真给了 id 的那几个 —— 传失败的那份不画、也不跟着发。
//
// 接口在 `@/network/api/*` 那一层换掉：真 axios 会被 `src/test/setup-network.ts` 逮住
// （未预期的 fetch 直接判失败），所以这一页会碰的每一个接口这里都答着。
//
// i18n 装**真的那一份**并锁到 zh-CN：这一页与它底下那张表单的标签都是中文文案，
// 契约点（`题目名称`、`提交`）量的就是那几句话本身。tiptap 那一整块换成壳 —— 与
// `components/tasks/__tests__/TaskFormPublishChecks.test.ts` 同一个理由：它跟这一页
// 要量的事无关，真挂起来只是把 happy-dom 拖垮。
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

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

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

/** 从挂载结果里读一个 testid 的文字 —— 断言走这只小手，免得满篇 querySelector。 */
function textOf(container: Element, testId: string): string | null {
  return container.querySelector(`[data-testid="${testId}"]`)?.textContent?.replace(/\s+/g, ' ').trim() ?? null
}

/** 「提交前」那颗按钮。灰不灰看它原生的 `disabled`，不猜 class 也不猜颜色。 */
function checklistButton(view: ReturnType<typeof render>): HTMLButtonElement {
  return view.getByRole('button', { name: '提交审核' }) as HTMLButtonElement
}

/** 表格里的提交。vee-validate 挂在 `<v-form>` 的 `@submit.prevent` 上，所以走
 *  `fireEvent.submit` 与人在页面里敲 Enter 是同一条路（`tasks/detail/Submit.spec.ts`
 *  也是这么按的）。 */
async function submitForm(view: ReturnType<typeof render>) {
  await fireEvent.submit(view.container.querySelector('form')!)
  // `handleSubmit` 是异步的：给它一轮微任务与一次渲染。
  await new Promise((resolve) => setTimeout(resolve, 0))
}

/** 点一个单选框。走**原生 click**：`fireEvent.click` 派发的是合成事件，单选框的
 *  `checked` 不会跟着翻，Vuetify 那次 `onInput` 读到的就还是旧值。 */
async function check(input: Element) {
  ;(input as HTMLInputElement).click()
  await nextTick()
}

/** 选中分类那枚下拉里的一项。下拉是 `v-select`（`role="combobox"`），选项画在浮层里
 *  —— `getByRole('option')` 找得到，因为 testing-library 的查询挂在 `document.body` 上。 */
async function pickCategory(view: ReturnType<typeof render>, name = '基础题') {
  await fireEvent.mouseDown(view.getAllByRole('combobox')[0])
  await fireEvent.click(await view.findByRole('option', { name }))
  await nextTick()
}

/** 把三栏必填都填上（名字、参与者类型、题目难度、所属分类）。 */
async function fillRequired(view: ReturnType<typeof render>, name = '用 gdb 定位一次段错误') {
  await fireEvent.update(view.getByLabelText('题目名称'), name)
  await check(view.getByRole('radio', { name: '个人' }))
  await check(view.getByRole('radio', { name: '初级' }))
  await pickCategory(view)
}

/** 展开「给 AI 队友的指导」那一节。它默认收起，里面的格子要点开才在。 */
async function openTeaching(view: ReturnType<typeof render>) {
  await fireEvent.click(view.getByTestId('publish-teaching-toggle'))
  await nextTick()
}

/** 摊开「参考资料」那一格里的资料库清单。它默认收起，勾选框点开才在。 */
async function openMaterials(view: ReturnType<typeof render>) {
  await fireEvent.click(view.getByTestId('teaching-materials-toggle'))
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

/** 从写一道那条路切到 PDF 那条路。 */
async function switchToPdf(view: ReturnType<typeof render>) {
  await fireEvent.click(view.getByRole('button', { name: '从 PDF 生成' }))
  await waitFor(() => expect(view.container.querySelector('[data-testid="pdf-file"]')).not.toBeNull())
}

/** 勾 / 取消勾一条草稿。走**原生 click**：`fireEvent.click` 派发的是合成事件，
 *  勾选框的 `checked` 不会跟着翻，Vuetify 那次 `onInput` 读到的就还是旧值。 */
async function toggle(input: HTMLElement) {
  input.click()
  await nextTick()
}

/** 选中一份文件 —— Vuetify 的 `v-file-input` 读的是 `e.target.files`。 */
async function pick(input: HTMLInputElement, file: File) {
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

/** 走完整条 PDF 前半程：切过去、选文件、解析。 */
async function parsed(view: Awaited<ReturnType<typeof mount>>) {
  await switchToPdf(view)
  await pick(view.getByLabelText('上传题目 PDF') as HTMLInputElement, pdfFile())
  await fireEvent.click(view.getByRole('button', { name: '解析成题目草稿' }))
  await waitFor(() => expect(view.container.querySelector('[data-testid="pdf-meta"]')).not.toBeNull())
  return view
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

describe('发题页：手写一道', () => {
  it('四块都在：材料、发布参数那张表单，以及右栏两张卡（第二句跟着身份变）', async () => {
    await boardAs(MANAGER)
    const view = await mount()

    // 契约点（e2e 也认这三条）：PDF 快速发布那张卡、材料那张卡、发布参数那张表单。
    expect(view.getByText('PDF 快速发布')).toBeTruthy()
    expect(view.getByText('附件（可选）')).toBeTruthy()
    expect(view.getByLabelText('题目名称')).toBeTruthy()

    // 右栏第一张：四站，一站一句；第二句是**按登录的人算出来的** —— 这块板的管理员
    // 名单里有 alice（`spaceBody`），所以她看到的是「自己发的题自己审」。
    expect(view.getByRole('heading', { name: '发出去之后' })).toBeTruthy()
    expect(textOf(view.container, 'publish-audience')).toContain('你可以直接通过（自己发的题自己审）。')
    await waitFor(() =>
      expect(textOf(view.container, 'publish-lifecycle')).toContain('「我的 → 我发布的」里有这道题的领取走势')
    )
    expect(view.getByText('被驳回会带原因退回，改完可以重新提交，不用重写一遍。')).toBeTruthy()
  })

  it('三栏必填空着：清单列的是真表单拦的那几条，按钮灰着，点提交一个请求都不发', async () => {
    await boardAs(MEMBER)
    const view = await mount()

    // 清单里每一条都由底下那张真表单报上来（`lib/taskPublishChecks.ts` 那份规则表
    // 对着 `TaskForm` 的 zod schema）—— 这一页不加戏，也不漏。
    const listed = Array.from(view.container.querySelectorAll('[data-testid="publish-checks"] li')).map((li) =>
      li.textContent?.trim()
    )
    expect(listed).toEqual([
      '标题：必填，最多 100 个字',
      '参与者类型：必选一个（个人 / 团队）',
      '题目难度：必选一个（初级 / 中级 / 高级）',
      '所属分类：必选一个（这块板的分类）',
    ])
    expect(view.queryByTestId('publish-ok')).toBeNull()
    expect(checklistButton(view).disabled).toBe(true)

    // 点真表单那颗「提交」：校验不过，一个请求都不发，地址栏也不动。
    await submitForm(view)
    expect(createTask).not.toHaveBeenCalled()
    expect(view.router.currentRoute.value.name).toBe('SpacesDetailPublishTask')
  })

  it('选满三栏：清单空了、按钮能点，点下去真的 POST /tasks，发完落到「我的」', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view, '用 gdb 定位一次段错误（E2E 发的）')

    // 三栏都选上之后，清单空了：一句「看起来没问题。」+ 两颗按钮都能点。
    await waitFor(() => expect(textOf(view.container, 'publish-ok')).toBe('看起来没问题。'))
    expect(view.container.querySelector('[data-testid="publish-checks"]')).toBeNull()
    expect(checklistButton(view).disabled).toBe(false)

    await submitForm(view)
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1))

    // 发出去的那一份：空间、字段、提交表 —— 还有「附件一个没传，就不带这一项」。
    const sent = createTask.mock.calls[0][0] as Record<string, unknown>
    expect(sent.space).toBe(SPACE_ID)
    expect(sent.name).toBe('用 gdb 定位一次段错误（E2E 发的）')
    expect(sent.submitterType).toBe('USER')
    expect(sent.rank).toBe(1)
    expect(sent.categoryId).toBe(3)
    expect(sent.submissionSchema).toEqual([{ prompt: '提交文件', type: 'FILE' }])
    // 「附件一个没传，就不带这一项」：老页那一版传的是 `undefined`（键在、值是空），
    // 后端读到的与「没有这一项」是同一个意思，这一页照旧。
    expect(sent.attachmentIds).toBeUndefined()

    // 发完落到题目列表的「我发布的」：还没过审的题只在那里看得到。
    await waitFor(() => expect(view.router.currentRoute.value.name).toBe('SpacesDetailTasksList'))
    expect(view.router.currentRoute.value.query.filter).toBe('publishing')
  })

  it('「提交审核」那颗按钮走的是真表单的提交：拦着的时候点不动，放行了才发出去', async () => {
    await boardAs(MEMBER)
    const view = await mount()

    await fillRequired(view)
    await waitFor(() => expect(textOf(view.container, 'publish-ok')).toBe('看起来没问题。'))

    await fireEvent.click(checklistButton(view))
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1))
    expect((createTask.mock.calls[0][0] as Record<string, unknown>).space).toBe(SPACE_ID)
  })

  it('「PDF 快速发布」：解析走真接口，草稿逐条摆出来，清空预览把表单还回原样', async () => {
    await boardAs(MEMBER)
    const view = await mount()

    await pick(view.getByLabelText('上传题目 PDF') as HTMLInputElement, pdfFile())
    await fireEvent.click(view.getByRole('button', { name: '解析预览' }))
    await waitFor(() => expect(textOf(view.container, 'quick-drafts')).not.toBeNull())

    // 发出去的那条请求：这份文件、这块板、后端写死的上限 20、地址栏没模板就是 -1。
    const sent = previewFromPdf.mock.calls[0][0] as {
      spaceId: number
      file: File
      maxTasks: number
      templateIndex: number
    }
    expect(sent.spaceId).toBe(SPACE_ID)
    expect(sent.file.name).toBe('计算机系统基础-第五次作业.pdf')
    expect(sent.maxTasks).toBe(20)
    expect(sent.templateIndex).toBe(-1)

    // 草稿逐条摆出来，名字就是接口回来的那两条。
    expect(textOf(view.container, 'quick-drafts')).toContain('用 gdb 定位一次段错误')
    expect(textOf(view.container, 'quick-drafts')).toContain('手写一个最简内存分配器')
    // 这条路上**只给看不给改**（参数在下面那张表单里填），所以一条 `.pdf__row` 都没有。
    expect(view.container.querySelectorAll('.pdf__row')).toHaveLength(0)

    await fireEvent.click(view.getByRole('button', { name: '清空预览' }))
    await waitFor(() => expect(textOf(view.container, 'quick-drafts')).toBeNull())
  })

  it('附件卡上的上限是接口报的那个数；问不到就不写这句话', async () => {
    await boardAs(MEMBER)
    const view = await mount()

    // 这句话只有一种来源：`GET /attachments/limits`（与上传那条路拦下超限文件读的是
    // **同一个上限**）。接口这里答的是一个别处没出现过的数，写死的字面量对不上。
    await waitFor(() => expect(textOf(view.container, 'attachment-limit')).toBe('单个文件不超过 11.77 MB'))
    expect(attachmentLimits).toHaveBeenCalledTimes(1)
    // 卡片本身照旧：这句话是建议，不是闸门。
    expect(view.getByLabelText('选择要随题一起发出的材料')).toBeTruthy()
    view.unmount()

    // 问不到（这里是 503）：少说一句就是，不猜一个数出来，也不弹错 —— 用户到这一步还
    // 什么都没要求做。
    attachmentLimits.mockImplementation(async () => {
      throw new Error('503 Service Unavailable')
    })
    const offline = await mount()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(offline.container.querySelector('[data-testid="attachment-limit"]')).toBeNull()
    expect(offline.getByLabelText('选择要随题一起发出的材料')).toBeTruthy()
  })

  it('材料：只画接口真给了 id 的那几个，传失败的那份不画也不跟着发', async () => {
    await boardAs(MEMBER)
    uploadAttachment.mockImplementation(async ({ file }: { file: File }) => {
      if (file.name === '能传上去的.pdf') return { data: { id: 41 } }
      throw new Error('503 Service Unavailable')
    })
    const view = await mount()

    const input = view.getByLabelText('选择要随题一起发出的材料') as HTMLInputElement
    const good = new File([new Uint8Array(8)], '能传上去的.pdf', { type: 'application/pdf' })
    const bad = new File([new Uint8Array(8)], '传不上去的.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
    Object.defineProperty(input, 'files', { value: [good, bad], configurable: true })
    await fireEvent.change(input)

    // 服务端给了 id 的那一份才有那一行 —— 另一份点了也带不走，不画。
    await waitFor(() => expect(view.getAllByTestId('attached-file')).toHaveLength(1))
    expect(view.getByTestId('attached-file').textContent).toContain('能传上去的.pdf')

    // 发题请求带的是那串真回来的 id。
    await fillRequired(view)
    await waitFor(() => expect(textOf(view.container, 'publish-ok')).toBe('看起来没问题。'))
    await submitForm(view)
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1))
    expect((createTask.mock.calls[0][0] as { attachmentIds?: number[] }).attachmentIds).toEqual([41])
  })
})

// ============ 给 AI 队友的指导（#944）============
//
// 这道题自己的那一层覆盖：六格全空 = 不设，仍旧听空间（与项目集）的默认；写了一格
// 就整份带上。两条发题路都要带它 —— 手写一道走 `POST /tasks`，PDF 批量走
// `taskOptions`。

describe('发题页：给 AI 队友的指导', () => {
  it('默认收起：那一栏在，里面的格子不在，点一下才出来', async () => {
    await boardAs(MEMBER)
    const view = await mount()

    expect(view.getByTestId('publish-teaching')).toBeTruthy()
    expect(view.queryByTestId('teaching-system-prompt')).toBeNull()

    await openTeaching(view)
    expect(view.getByTestId('teaching-system-prompt')).toBeTruthy()
  })

  it('这一栏在页面上；六格全空就不带它 —— 让空间的默认生效', async () => {
    await boardAs(MEMBER)
    const view = await mount()

    expect(view.getByTestId('publish-teaching')).toBeTruthy()

    await fillRequired(view)
    await waitFor(() => expect(textOf(view.container, 'publish-ok')).toBe('看起来没问题。'))
    await submitForm(view)
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1))

    // 键在、值是 `undefined`（与 `attachmentIds` 同一个写法）：后端读到的与「没有
    // 这一项」一样，所以这道题没有覆盖，仍旧用空间的默认。
    expect((createTask.mock.calls[0][0] as { teaching?: unknown }).teaching).toBeUndefined()
  })

  it('参考资料列的是这块板资料库里的文件：勾一份，「仅管理员」那一档不列出来', async () => {
    listMaterials.mockImplementation(async () => ({
      data: {
        materials: [
          {
            id: 161,
            name: '第03讲-红黑树.pdf',
            visibility: 'members',
            type: 'file',
            size: null,
            mime: null,
            uploaderId: null,
            createdAt: 0,
            downloadCount: 0,
          },
          {
            id: 162,
            name: '参考答案-红黑树.pdf',
            visibility: 'admins',
            type: 'file',
            size: null,
            mime: null,
            uploaderId: null,
            createdAt: 0,
            downloadCount: 0,
          },
        ],
        canManage: false,
      },
    }))
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)
    await openTeaching(view)
    await openMaterials(view)

    expect(await view.findByLabelText('第03讲-红黑树.pdf')).toBeTruthy()
    expect(view.queryByLabelText('参考答案-红黑树.pdf')).toBeNull()

    // Vuetify 的勾选框绑的是 input 的 `input` 事件（`e.target.checked`），点它没用。
    await fireEvent.input(view.getByLabelText('第03讲-红黑树.pdf'), { target: { checked: true } })

    await waitFor(() => expect(textOf(view.container, 'publish-ok')).toBe('看起来没问题。'))
    await submitForm(view)
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1))

    expect((createTask.mock.calls[0][0] as { teaching: { materialIds: number[] } }).teaching.materialIds).toEqual([161])
  })

  it('写了对 AI 的要求与周次：整份 POST 出去，空格子落成空数组', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)
    await openTeaching(view)

    await fireEvent.update(view.getByLabelText('对 AI 的要求'), '第 {current_week} 周：讲完链表了。')
    await fireEvent.update(view.getByLabelText('当前周次'), '3')

    await waitFor(() => expect(textOf(view.container, 'publish-ok')).toBe('看起来没问题。'))
    await submitForm(view)
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1))

    expect((createTask.mock.calls[0][0] as { teaching?: unknown }).teaching).toEqual({
      systemPrompt: '第 {current_week} 周：讲完链表了。',
      currentWeek: 3,
      allowedTopics: [],
      avoidInCode: [],
      materialIds: [],
      knowledgeIds: [],
    })
  })

  it('高级选项里的清单也一起走：逗号分隔、中英文都认', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)
    await openTeaching(view)

    await fireEvent.click(view.getByText('高级选项'))
    const topics = await view.findByLabelText('目前的内容范围')
    await fireEvent.update(topics, '链表，栈, 队列')

    await waitFor(() => expect(textOf(view.container, 'publish-ok')).toBe('看起来没问题。'))
    await submitForm(view)
    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1))

    expect((createTask.mock.calls[0][0] as { teaching: { allowedTopics: string[] } }).teaching.allowedTopics).toEqual([
      '链表',
      '栈',
      '队列',
    ])
  })

  it('PDF 批量那条路带着同一份指导（taskOptions 里）', async () => {
    await boardAs(MEMBER)
    const view = await mount()

    // 写在切换之前：PDF 那一态里没有这张卡（它属于「手写一道」那一半），但这一页的
    // 状态活着，切过去照样带得走。
    await openTeaching(view)
    await fireEvent.update(view.getByLabelText('当前周次'), '5')
    await switchToPdf(view)
    await pick(view.getByLabelText('上传题目 PDF') as HTMLInputElement, pdfFile())
    await fireEvent.click(view.getByRole('button', { name: '解析成题目草稿' }))
    await waitFor(() => expect(view.container.querySelector('[data-testid="pdf-meta"]')).not.toBeNull())

    await fireEvent.click(view.getByRole('button', { name: '确认发布 2 道' }))
    await waitFor(() => expect(confirmFromPdf).toHaveBeenCalledTimes(1))

    const sent = confirmFromPdf.mock.calls[0][0] as { taskOptions: { teaching?: { currentWeek?: number } } }
    expect(sent.taskOptions.teaching?.currentWeek).toBe(5)
  })
})

// ============ 从 PDF 生成 ============
//
// 这一条路是原型那一版：解析、逐条改、勾着发、就地给回执。断言量与第五批同一套
// （那时它在裹着老页的那一层上跑），量的是「浏览器实际收到什么」与「用户看到什么」，
// 不量实现长什么样。

describe('发题页：从 PDF 生成', () => {
  it('切过去之后手写那道那一半整个让位', async () => {
    await boardAs(MEMBER)
    const view = await mount()

    await switchToPdf(view)

    // 这一页自己那几块：PDF 快速发布、发布参数那张表单、右栏两张卡 —— 一块都不在。
    expect(view.queryByText('PDF 快速发布')).toBeNull()
    expect(view.queryByLabelText('题目名称')).toBeNull()
    expect(view.queryByTestId('publish-lifecycle')).toBeNull()
    expect(view.queryByRole('button', { name: '提交审核' })).toBeNull()
  })

  it('解析：浏览器真的把这份 PDF 发出去了，结果区摆的是接口回来的那三件事', async () => {
    await boardAs(MEMBER)
    const view = await parsed(await mount())

    // 发出去的那条请求 —— 文件、空间、上限，一样不少。
    expect(previewFromPdf).toHaveBeenCalledTimes(1)
    const sent = previewFromPdf.mock.calls[0][0] as { spaceId: number; file: File; maxTasks: number }
    expect(sent.spaceId).toBe(SPACE_ID)
    expect(sent.file.name).toBe('计算机系统基础-第五次作业.pdf')
    expect(sent.maxTasks).toBe(20)

    // 结果区：模板、插图数（正文里那张图，1 张）、token —— 都是接口回来的那几个值。
    expect(textOf(view.container, 'pdf-template')).toBe('模板：计算机系统基础 · 标准题模板')
    expect(textOf(view.container, 'pdf-images')).toBe('抽出插图 1 张')
    expect(textOf(view.container, 'pdf-tokens')).toBe('消耗 18,742 tokens')
    // 并且说清楚：草稿还不是题目。
    expect(textOf(view.container, 'pdf-meta') ?? '').toContain('还没有成为题目')

    // 两条草稿都在屏幕上，标题与题干就是接口给的那份。
    const titles = view.getAllByLabelText('标题') as HTMLInputElement[]
    expect(titles.map((i) => i.value)).toEqual(['用 gdb 定位一次段错误', '手写一个最简内存分配器'])
    const bodies = view.getAllByLabelText('题干') as HTMLTextAreaElement[]
    expect(bodies[0].value).toContain('给定一段会崩的程序')
    // 出处页摆在每一条上。
    expect(view.getAllByTestId('draft-origin').map((e) => e.textContent?.trim())).toEqual([
      'PDF · 第 1 页',
      'PDF · 第 2 页',
    ])
  })

  it('勾掉一条、改两处：确认发走的就是屏幕上那几条，而且不跳走', async () => {
    await boardAs(MEMBER)
    const view = await parsed(await mount())

    // 两条都勾着 —— 按钮跟着数字走。
    expect(view.getByRole('button', { name: '确认发布 2 道' })).toBeTruthy()
    await toggle(view.getByLabelText('勾选「手写一个最简内存分配器」'))
    await waitFor(() => expect(view.getByRole('button', { name: '确认发布 1 道' })).toBeTruthy())

    // 就地改标题与题干。
    await fireEvent.update(view.getAllByLabelText('标题')[0], '用 gdb 定位一次段错误（改过）')
    await fireEvent.update(view.getAllByLabelText('题干')[0], '改过的题干：找出崩在哪一行，并把寄存器状态截图交上来。')

    const before = view.router.currentRoute.value.fullPath
    await fireEvent.click(view.getByRole('button', { name: '确认发布 1 道' }))
    await waitFor(() => expect(confirmFromPdf).toHaveBeenCalledTimes(1))

    // 发出去的草稿：只有勾中的那一条，文字是改过的那份，出处标记加上去了。
    const sent = confirmFromPdf.mock.calls[0][0] as {
      drafts: { name: string; intro: string; description: string; space: number; categoryId?: number }[]
      taskOptions: { space: number; attachmentIds?: number[] }
    }
    expect(sent.drafts).toHaveLength(1)
    expect(sent.drafts[0].name).toBe('用 gdb 定位一次段错误（改过）')
    expect(sent.drafts[0].description).toBe('改过的题干：找出崩在哪一行，并把寄存器状态截图交上来。')
    expect(sent.drafts[0].space).toBe(SPACE_ID)
    expect(sent.drafts[0].categoryId).toBe(3)
    // 出处标记在简介里（题目模型没有来源这一列）—— 队列那一行显示的就是简介。
    expect(sent.drafts[0].intro).toBe('【PDF · 第 1 页】用 gdb 找出崩在哪一行。')
    // 两颗勾默认都勾着，所以这条请求里带着两份文件的行号：原 PDF 在前、插图在后。
    expect(sent.taskOptions.attachmentIds).toEqual([911, 912])

    // 不跳走：地址栏还是发题这一页。
    expect(view.router.currentRoute.value.fullPath).toBe(before)

    // 就地给回执，回执里两个去处都指向新外壳那棵树。
    expect(textOf(view.container, 'pdf-receipt') ?? '').toContain('刚发的 1 道题已经进了待审核队列')
    const hrefs = Array.from(view.container.querySelectorAll('a')).map((a) => a.getAttribute('href'))
    expect(hrefs).toContain(`/spaces/${SPACE_ID}/manage/audit`)
    expect(hrefs).toContain(`/spaces/${SPACE_ID}/tasks?filter=publishing`)
  })

  it('附件：两颗勾默认都勾着，标签写的是哪一份、几张，取消勾的那一份就不跟着走', async () => {
    await boardAs(MEMBER)
    const view = await parsed(await mount())

    // 拉起来就是原型那两颗勾，默认都勾着。
    const pdfBox = view.getByLabelText('原 PDF') as HTMLInputElement
    const imgBox = view.getByLabelText('抽出的插图（1 张）') as HTMLInputElement
    expect(pdfBox.checked).toBe(true)
    expect(imgBox.checked).toBe(true)
    // 勾的是什么摆出来给人看：文件名字是接口回来的那一份。
    expect(textOf(view.container, 'pdf-attach-pdf-file')).toBe('原 PDF：计算机系统基础-第五次作业.pdf')
    expect(textOf(view.container, 'pdf-attach-image-files')).toBe('插图：input.pdf-0001-01.png')
    expect(textOf(view.container, 'pdf-attach-count')).toBe('这 2 个文件会附在每一道生成出来的题上')

    // 取消勾插图：跟着走的只剩原 PDF 那一份，数字也跟着掉。
    await toggle(imgBox)
    await waitFor(() =>
      expect(textOf(view.container, 'pdf-attach-count')).toBe('这 1 个文件会附在每一道生成出来的题上')
    )

    await fireEvent.click(view.getByRole('button', { name: '确认发布 2 道' }))
    await waitFor(() => expect(confirmFromPdf).toHaveBeenCalledTimes(1))
    const sent = confirmFromPdf.mock.calls[0][0] as { taskOptions: { attachmentIds?: number[] } }
    // 发出去的就是屏幕上勾着的那一份，行号原样 —— 原 PDF 在前。
    expect(sent.taskOptions.attachmentIds).toEqual([911])
  })

  it('附件：一样都不勾的时候，请求里没有 attachmentIds 这一项（与从前一致）', async () => {
    await boardAs(MEMBER)
    const view = await parsed(await mount())

    await toggle(view.getByLabelText('原 PDF') as HTMLInputElement)
    await toggle(view.getByLabelText('抽出的插图（1 张）') as HTMLInputElement)
    await waitFor(() =>
      expect(textOf(view.container, 'pdf-attach-count')).toBe('这 0 个文件会附在每一道生成出来的题上')
    )

    await fireEvent.click(view.getByRole('button', { name: '确认发布 2 道' }))
    await waitFor(() => expect(confirmFromPdf).toHaveBeenCalledTimes(1))
    const sent = confirmFromPdf.mock.calls[0][0] as { taskOptions: Record<string, unknown> }
    expect('attachmentIds' in sent.taskOptions).toBe(false)
  })

  it('附件：接口没落成文件行的那一样不画勾，页面上说清为什么', async () => {
    await boardAs(MEMBER)
    previewFromPdf.mockImplementation(async () => ({
      data: { ...previewBody(), attachments: { pdf: null, images: [] } },
    }))
    const view = await parsed(await mount())

    // 一颗勾都不画 —— 点了也带不走的东西，不画成勾。
    expect(view.container.querySelector('[data-testid="pdf-attach-pdf"]')).toBeNull()
    expect(view.container.querySelector('[data-testid="pdf-attach-images"]')).toBeNull()
    expect(view.queryByLabelText('原 PDF')).toBeNull()
    // 但要说清是哪一样、为什么。
    expect(textOf(view.container, 'pdf-attach-pdf-why') ?? '').toContain('没画')
    expect(textOf(view.container, 'pdf-attach-images-why') ?? '').toContain('没画')

    // 确认发布照样走得通，请求形状与从前一样。
    await fireEvent.click(view.getByRole('button', { name: '确认发布 2 道' }))
    await waitFor(() => expect(confirmFromPdf).toHaveBeenCalledTimes(1))
    const sent = confirmFromPdf.mock.calls[0][0] as { taskOptions: Record<string, unknown> }
    expect('attachmentIds' in sent.taskOptions).toBe(false)
  })

  it('超限与错类型：一个请求都不发出去，屏幕上说清为什么', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await switchToPdf(view)

    const oversized = pdfFile('太大了.pdf', 15 * 1024 * 1024 + 1)
    await pick(view.getByLabelText('上传题目 PDF') as HTMLInputElement, oversized)
    await fireEvent.click(view.getByRole('button', { name: '解析成题目草稿' }))
    await waitFor(() => expect(textOf(view.container, 'pdf-error') ?? '').toContain('不能超过 15MB'))
    expect(previewFromPdf).not.toHaveBeenCalled()

    // 换一份不是 PDF 的：同样是本地就拦下来。
    const wrong = new File([new Uint8Array(10)], '题目.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
    await pick(view.getByLabelText('上传题目 PDF') as HTMLInputElement, wrong)
    await fireEvent.click(view.getByRole('button', { name: '解析成题目草稿' }))
    await waitFor(() => expect(textOf(view.container, 'pdf-error') ?? '').toContain('只收 PDF'))
    expect(previewFromPdf).not.toHaveBeenCalled()
  })

  it('后端拒绝的时候，屏幕上就是它那句话，而且不会凭空画出草稿', async () => {
    await boardAs(MEMBER)
    previewFromPdf.mockImplementation(async () => {
      throw { response: { data: { message: 'LLM is not configured' } }, message: 'Request failed with status code 400' }
    })

    const view = await mount()
    await switchToPdf(view)
    await pick(view.getByLabelText('上传题目 PDF') as HTMLInputElement, pdfFile())
    await fireEvent.click(view.getByRole('button', { name: '解析成题目草稿' }))

    await waitFor(() => expect(textOf(view.container, 'pdf-error')).toBe('解析失败：LLM is not configured'))
    // 没有解析结果、没有草稿、没有确认按钮 —— 一个都没画。
    expect(view.container.querySelector('[data-testid="pdf-meta"]')).toBeNull()
    expect(view.container.querySelector('[data-testid="pdf-drafts"]')).toBeNull()
    expect(view.queryByRole('button', { name: /确认发布/ })).toBeNull()
  })
})
