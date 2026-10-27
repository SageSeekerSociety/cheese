// 发题页：挂的是真页面、真表单、真附件那一格，接口在 API 层答着
// （真 axios 会被 `src/test/setup-network.ts` 逮住，未预期的请求直接判失败）。量的是人
// 在这一页上做一件事之后真的发出去了什么：
//
// 1. **边写边存**：停手后才建一份草稿（`POST /task-drafts`），地址栏记下它；打开地址栏里
//    的草稿，表单就是它存的样子。
// 2. **必填空着点发布**：一个请求都不发，页头说还有几项；填好了先把校验过的表单存进草稿，
//    再发布草稿，发完落到「我发布的」。
// 3. **AI 队友辅导**：本题什么都没写时不带它，空间那一份照常生效；写了就带上整份。
// 4. **模板**：选一份，表单按它填好。
//
// i18n 装真的那一份并锁到 zh-CN；tiptap 换成壳 —— 它跟这一页要量的事无关。
import type { Component } from 'vue'

import { defineComponent, h, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VApp } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/http'
import i18n, { setLocale } from '@/i18n'

const listTasks = vi.fn()
const listDrafts = vi.fn()
const startDraft = vi.fn()
const readDraft = vi.fn()
const saveDraft = vi.fn()
const publishDraft = vi.fn()
const removeDraft = vi.fn()
const uploadStarter = vi.fn()
const updateTask = vi.fn()

const spaceDetail = vi.fn()
const listCategories = vi.fn()
const listDomainGroups = vi.fn()
const listMaterials = vi.fn()

const uploadAttachment = vi.fn()
const attachmentLimits = vi.fn()
const toastError = vi.fn()

// 「默认队友」那一格自己按需拉的预设清单（走 `useAgentTypes` → `@/api`）。
const listAgentTypes = vi.fn()
// 「技能」那一格的候选：平台发货的技能目录（`useSkillCatalog`）。
const listPlatformSkills = vi.fn()
// 芝士：开对话走 `request`，问一句走事件流。事件流按 `script` 一条条念出来，照的是
// 服务端真推的那几种（`tool`、`tool_args`、`change`、`delta`、`done`）。撤销芝士的改动
// 走 `/assistant/changes`。
const apiRequest = vi.fn()
const undoChange = vi.fn()
let script: [string, Record<string, unknown>][] = []

vi.mock('@/api', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  listAgentTypes: (...a: unknown[]) => listAgentTypes(...a),
  request: (...a: unknown[]) => apiRequest(...a),
  AgentChangesApi: { undo: (...a: unknown[]) => undoChange(...a), redo: vi.fn() },
  // 回答里自动化卡片的状态：这里没有自动化。
  getAssistantRoutines: async () => ({ routines: [], limit: 20 }),
}))

vi.mock('@/api/eventStream', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  postEventStream: async (
    _path: string,
    _body: unknown,
    onEvent: (event: string, data: Record<string, unknown>, id?: string) => void,
    options?: { onOpen?: () => void }
  ) => {
    options?.onOpen?.()
    onEvent('answering', { id: 'w1' })
    let n = 0
    for (const [event, data] of script) {
      await new Promise((resolve) => setTimeout(resolve, 5))
      onEvent(event, data, String(++n))
    }
  },
}))

vi.mock('@/api/projectSkills', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  listPlatformSkills: (...a: unknown[]) => listPlatformSkills(...a),
}))

vi.mock('@/api/taskBoard', () => ({
  TaskBoardApi: {
    list: (...a: unknown[]) => listTasks(...a),
    update: (...a: unknown[]) => updateTask(...a),
  },
}))

vi.mock('@/api/taskDrafts', () => ({
  TaskDraftsApi: {
    list: (...a: unknown[]) => listDrafts(...a),
    start: (...a: unknown[]) => startDraft(...a),
    read: (...a: unknown[]) => readDraft(...a),
    save: (...a: unknown[]) => saveDraft(...a),
    publish: (...a: unknown[]) => publishDraft(...a),
    remove: (...a: unknown[]) => removeDraft(...a),
    uploadStarter: (...a: unknown[]) => uploadStarter(...a),
  },
}))

vi.mock('@/api/spaces', () => ({
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
vi.mock('@/api/attachments', () => ({
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
        expose({ editor: { getText: () => '正文（测试）。', getMarkdown: () => '正文（测试）。' } })
        return () => h('div', { class: 'tiptap-editor' })
      },
    }),
  }
})

import TaskPublish from './PublishTask.vue'

import AgentHost from '@/layouts/AgentHost.vue'
import AccountService from '@/services/account'
import { agentDocked, resetAgent } from '@/stores/agent'
import { typeInComposer } from '@/test/composerInput'

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
      { path: '/spaces/:spaceId/library', component: stub },
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

async function mount(query = '') {
  const router = makeRouter()
  await router.push(`/spaces/${SPACE_ID}/tasks/publish${query}`)
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  // 芝士的面板是外壳里那一个（layouts/AgentHost.vue），停在页面外框的右边：外框和它都要有。
  const Page = defineComponent({
    render: () => h(VApp, () => [h(TaskPublish as Component), h(AgentHost as Component)]),
  })
  const view = render(Page, {
    global: { plugins: [createVuetify({ components, directives }), router, pinia, i18n] },
  })
  // 表单是异步组件、空间也要先装 —— 等到它真画出来为止。第一条用例要冷加载
  // `TaskForm` 那一整棵依赖，CI 满载时远超默认的 1 秒（见过 40 秒那次红），所以给足。
  await waitFor(() => expect(view.container.querySelector('form')).not.toBeNull(), {
    timeout: 20_000,
  })
  return { ...view, router }
}

/** 名称那一格（芝士标在它旁边的「撤销名称」也带着「名称」两个字）。 */
function nameInput(view: ReturnType<typeof render>): HTMLInputElement {
  return view.getAllByLabelText('名称', { exact: false }).find((el) => el.tagName === 'INPUT') as HTMLInputElement
}

/** 页头那颗发布。 */
async function publish(view: ReturnType<typeof render>) {
  await fireEvent.click(view.getByTestId('publish-submit'))
  // 提交是异步的：给它一轮微任务与一次渲染。
  await new Promise((resolve) => setTimeout(resolve, 0))
}

/** 选中分类那枚下拉里的一项。选项画在浮层里，查询挂在 `document.body` 上找得到。 */
async function pickCategory(view: ReturnType<typeof render>, name = '基础题') {
  const field = view.container.querySelector('[data-agent-field="category"]') as HTMLElement
  await fireEvent.mouseDown(within(field).getAllByRole('combobox')[0])
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
  await fireEvent.update(nameInput(view), name)
  await fillSettings(view)
}

/** 「AI 的设置」那几格画出来（它们一直摊开，不收在「更多设置」里）。 */
async function ownTeaching(view: ReturnType<typeof render>) {
  await view.findByLabelText('本题补充要求')
}

/** 点一个原生单选框。走原生 click：`fireEvent.click` 派发的合成事件不翻 `checked`。 */
async function check(input: Element) {
  ;(input as HTMLInputElement).click()
  await nextTick()
}

/** 等「默认队友」那一格画出来（它在「AI 的设置」里，一直摊开）。 */
async function openMore(view: ReturnType<typeof render>) {
  await waitFor(() => expect(view.getByTestId('default-teammate')).toBeTruthy())
}

/** 在「默认队友」那一格选一个选项。选项画在浮层里，查询挂在 `document.body` 上。 */
async function pickTeammate(view: ReturnType<typeof render>, name: RegExp) {
  const picker = view.getByTestId('default-teammate')
  // Vuetify 3.13 把 `role=combobox` 同时挂在字段的壳和里面的 `<input>` 上；
  // 带 `aria-label` 的是 `<input>`，按名字找它。
  await fireEvent.mouseDown(within(picker).getByRole('combobox', { name: '默认队友' }))
  await fireEvent.click(await view.findByRole('option', { name }))
  await nextTick()
}

/** 这条板子的分类/域名组/空间那一份（`activeCategories` 按 `displayOrder` 排、
 *  滤掉已归档，`TaskForm` 那枚下拉的选项就是它）。 */
function categoriesBody() {
  return { categories: [{ id: 3, name: '基础题', displayOrder: 1, archivedAt: null }] }
}

/** 空间那一份：角色是拿登录的人跟 `admins` 对出来的。 */
function spaceBody() {
  return {
    space: {
      id: SPACE_ID,
      name: '数据结构空间',
      admins: [
        { role: 'OWNER', user: { id: 1, username: 'owner', nickname: '所有者' } },
        { role: 'ADMIN', user: MANAGER },
      ],
    },
  }
}

/** 以某个人的身份打开这一页（角色由空间 store 按登录的人算）。 */
async function boardAs(user: { id: number; username: string; nickname: string }) {
  localStorage.setItem('user', JSON.stringify(user))
  AccountService.user = user as never
  AccountService.loggedIn = true
  // 芝士是全站那一个：上一条用例的对话、开合、停靠不带进这一条。
  resetAgent()
  agentDocked.value = true
}

function pdfFile(name = '讲义.pdf', bytes = 1024): File {
  return new File([new Uint8Array(bytes)], name, { type: 'application/pdf' })
}

/** 草稿那一份：`fields` 是存下的表单。 */
function draftBody(fields: Record<string, unknown> = {}, id = 61) {
  return {
    id,
    spaceId: SPACE_ID,
    taskId: null,
    taskName: null,
    name: (fields.name as string) ?? '',
    source: '',
    conversationId: null,
    version: 1,
    updatedAt: '2026-10-20T08:00:00Z',
    missing: [],
    fields,
  }
}

/** 发布前存进草稿的那份表单：最后一次存。 */
function sent(): Record<string, unknown> {
  const calls = saveDraft.mock.calls
  return calls[calls.length - 1][1] as Record<string, unknown>
}

beforeEach(() => {
  // 芝士那一条对话：读是还没有话题，开一个新话题是 c1。
  apiRequest.mockImplementation(async (path: string, init?: { method?: string }) =>
    path.endsWith('/conversations')
      ? init?.method === 'POST'
        ? { id: 'c1', title: '', lastActiveAt: '2026-10-20T08:00:00Z' }
        : { conversations: [] }
      : {}
  )
  script = []
  spaceDetail.mockImplementation(async () => spaceBody())
  listCategories.mockImplementation(async () => categoriesBody())
  listDomainGroups.mockImplementation(async () => ({ groups: [] }))
  listMaterials.mockImplementation(async () => ({ materials: [], canManage: false }))
  listTasks.mockImplementation(async () => ({ tasks: [], distinctParticipants: 0 }))
  listDrafts.mockImplementation(async () => [])
  startDraft.mockImplementation(async (_space: number, fields: Record<string, unknown>) => draftBody(fields))
  readDraft.mockImplementation(async (id: number) => draftBody({ name: '存着的那道题' }, id))
  saveDraft.mockImplementation(async () => ({ version: 2, updatedAt: '2026-10-20T08:01:00Z' }))
  publishDraft.mockImplementation(async () => ({ task: { id: 501, approved: false }, draftId: 61 }))
  uploadAttachment.mockImplementation(async ({ file }: { file: File }) => ({ id: 41, file }))
  uploadStarter.mockImplementation(async () => ({ id: 51 }))
  // 故意不是任何「眼熟」的数（真部署那个默认是 100MB）：页面上若写了死数，下面对不上。
  attachmentLimits.mockImplementation(async () => ({ maxFileBytes: LIMIT_BYTES }))
  listPlatformSkills.mockReset().mockResolvedValue({ data: [], total: 0 })
  listAgentTypes.mockReset().mockResolvedValue({
    data: [
      {
        name: 'academic-research',
        title: '学术研究',
        description: '',
        body: '',
        skills: ['documents'],
        mcp_servers: [],
        builtin: true,
      },
    ],
    total: 1,
  })
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
    expect(publishDraft).not.toHaveBeenCalled()
    expect(view.router.currentRoute.value.name).toBe('SpacesDetailPublishTask')
  })

  it('发布被服务端拒了：页面上留着服务端那句话，人还在这一页', async () => {
    const said = '这道题的提交项指向了不存在的阶段（HTTP 400）'
    publishDraft.mockRejectedValue(new ApiError(400, said, 'BadRequestError'))
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)

    await publish(view)
    await waitFor(() => expect(publishDraft).toHaveBeenCalledTimes(1))

    // Still there after any toast would have faded: the sentence is on the page.
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(await view.findByText(said)).toBeTruthy()
    expect(view.router.currentRoute.value.name).toBe('SpacesDetailPublishTask')
  })

  it('提交项和评分要点：照填的发，空着的评分行去掉，不公开就说不公开', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)

    await fireEvent.update(view.getByLabelText('第 1 个提交项'), 'csim.c 源文件')
    await fireEvent.click(view.getByRole('button', { name: '添加提交项' }))
    await fireEvent.update(view.getByLabelText('第 2 个提交项'), '合作与参考说明')
    await fireEvent.click(view.getAllByRole('radio', { name: '文字' })[1])

    await fireEvent.click(view.getByRole('button', { name: '添加一项' }))
    await fireEvent.update(view.getByLabelText('第 1 项项目'), '正确性')
    await fireEvent.update(view.getByLabelText('第 1 项分值'), '60')
    await fireEvent.update(view.getByLabelText('第 1 项评分标准'), '8 组记录全对')
    await fireEvent.click(view.getByRole('button', { name: '添加一项' }))
    await check(view.getByTestId('task-form-rubric-public').querySelector('input')!)

    await publish(view)
    await waitFor(() => expect(publishDraft).toHaveBeenCalledTimes(1))

    expect(sent().submissionSchema).toEqual([
      { prompt: 'csim.c 源文件', type: 'FILE', stage: null },
      { prompt: '合作与参考说明', type: 'TEXT', stage: null },
    ])
    expect(sent().rubric).toEqual([{ item: '正确性', points: 60, criteria: '8 组记录全对', stage: null }])
    expect(sent().rubricPublic).toBe(false)
  })

  it('分阶段：每个阶段带名称和要求发出去，提交项和评分要点各归它所在的阶段', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)

    // The first stage takes what the form already had; the second brings a file item of its own.
    await fireEvent.click(view.getByRole('button', { name: '添加阶段' }))
    await fireEvent.update(view.getByLabelText('第 1 个阶段的名称'), '需求分析')
    await fireEvent.update(view.getByLabelText('第 1 个阶段的要求'), '交一份需求文档')
    await fireEvent.click(view.getByRole('button', { name: '添加阶段' }))
    await fireEvent.update(view.getByLabelText('第 2 个阶段的名称'), '实现')
    await fireEvent.update(view.getAllByLabelText('第 1 个提交项')[1], '代码')
    await fireEvent.click(view.getAllByRole('button', { name: '添加一项' })[1])
    await fireEvent.update(view.getByLabelText('第 1 项项目'), '正确性')

    await publish(view)
    await waitFor(() => expect(publishDraft).toHaveBeenCalledTimes(1))

    const [need, impl] = sent().stages as { id: string }[]
    expect(sent().stages).toEqual([
      { id: expect.any(String), name: '需求分析', description: '交一份需求文档', dueAt: null },
      { id: expect.any(String), name: '实现', description: '', dueAt: null },
    ])
    expect(need.id).not.toBe(impl.id)
    // Items and lines name their stage by its id, so a later reorder cannot split them.
    expect(sent().submissionSchema).toEqual([
      { prompt: '提交文件', type: 'FILE', stage: need.id },
      { prompt: '代码', type: 'FILE', stage: impl.id },
    ])
    expect(sent().rubric).toEqual([{ item: '正确性', points: 0, criteria: '', stage: impl.id }])
  })

  it('删掉一个阶段：它的提交项一起去掉，别的阶段的提交项留在原来的阶段下', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)
    for (const name of ['一', '二', '三']) {
      await fireEvent.click(view.getByRole('button', { name: '添加阶段' }))
      const fields = view.getAllByPlaceholderText('例如：需求分析')
      await fireEvent.update(fields[fields.length - 1], name)
    }
    await fireEvent.update(view.getAllByLabelText('第 1 个提交项')[2], '第三阶段的文件')

    await fireEvent.click(view.getByRole('button', { name: '删除第 2 个阶段' }))
    // 删一个阶段先问一句，答「删除」才删。
    const dialog = await waitFor(() => view.getByRole('alertdialog', { name: '删除第 2 个阶段？' }))
    await fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))
    await waitFor(() => expect(view.getAllByPlaceholderText('例如：需求分析')).toHaveLength(2))
    await publish(view)
    await waitFor(() => expect(publishDraft).toHaveBeenCalledTimes(1))

    const [one, three] = sent().stages as { id: string; name: string }[]
    expect([one.name, three.name]).toEqual(['一', '三'])
    expect(sent().submissionSchema).toEqual([
      { prompt: '提交文件', type: 'FILE', stage: one.id },
      { prompt: '第三阶段的文件', type: 'FILE', stage: three.id },
    ])
  })

  it('有阶段没写名称：不发，那一节说为什么', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)
    await fireEvent.click(view.getByRole('button', { name: '添加阶段' }))

    await publish(view)

    expect(await view.findByText('每个阶段都需要填写名称')).toBeTruthy()
    expect(publishDraft).not.toHaveBeenCalled()
  })

  it('评分行写了标准没写名称：不发，那一节说为什么', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)
    await fireEvent.click(view.getByRole('button', { name: '添加一项' }))
    await fireEvent.update(view.getByLabelText('第 1 项评分标准'), '格式规范')

    await publish(view)

    expect(await view.findByText('写了评分标准的项需要填写项目名称')).toBeTruthy()
    expect(publishDraft).not.toHaveBeenCalled()
  })

  it('填好点发布：POST /tasks 带着空间、提交表和附件，发完落到「我发布的」', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view, '用 gdb 定位一次段错误（E2E 发的）')
    const input = view.getByTestId('attachment-input') as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [pdfFile()], configurable: true })
    await fireEvent.change(input)
    await waitFor(() => expect(view.getAllByTestId('attached-file')).toHaveLength(1))

    await publish(view)
    await waitFor(() => expect(publishDraft).toHaveBeenCalledTimes(1))

    expect(publishDraft).toHaveBeenCalledWith(61)
    expect(sent().name).toBe('用 gdb 定位一次段错误（E2E 发的）')
    expect(sent().submitterType).toBe('USER')
    expect(sent().rank).toBe(1)
    expect(sent().categoryId).toBe(3)
    expect(sent().submissionSchema).toEqual([{ prompt: '提交文件', type: 'FILE', stage: null }])
    expect(sent().attachmentIds).toEqual([41])
    // 沿用空间的默认指导：不单独设置，让空间那一层生效。
    expect(sent().teachingOwn).toBe(false)

    await waitFor(() => expect(view.router.currentRoute.value.name).toBe('SpacesDetailTasksList'))
    expect(view.router.currentRoute.value.query.filter).toBe('publishing')
  })

  it('材料上传时写着传到了百分之几', async () => {
    uploadAttachment.mockImplementation(
      (_data: unknown, onProgress?: (event: { loaded: number; total?: number }) => void) =>
        new Promise(() => onProgress?.({ loaded: 40, total: 100 }))
    )
    await boardAs(MEMBER)
    const view = await mount()
    const input = view.getByTestId('attachment-input') as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [pdfFile()], configurable: true })
    await fireEvent.change(input)

    await waitFor(() => expect(view.getByRole('button', { name: /上传中 40%/ })).toBeTruthy())
  })

  it('选了起始代码当场就查：不合格的发布前说出原因、不留下，发出去不带它', async () => {
    uploadStarter.mockRejectedValue(new Error('压缩包里的 lab/../../evil.txt 指向压缩包以外的位置'))
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)
    const input = view.getByTestId('starter-input') as HTMLInputElement
    Object.defineProperty(input, 'files', {
      value: [new File([new Uint8Array(64)], 'lab.zip', { type: 'application/zip' })],
      configurable: true,
    })
    await fireEvent.change(input)

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith('起始代码上传失败：压缩包里的 lab/../../evil.txt 指向压缩包以外的位置')
    )
    expect(publishDraft).not.toHaveBeenCalled()
    expect(view.queryByTestId('starter-file')).toBeNull()

    await publish(view)
    await waitFor(() => expect(publishDraft).toHaveBeenCalledTimes(1))
    expect(sent().starterAttachmentId ?? null).toBeNull()
  })

  it('选了合格的起始代码：发出去带着它', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)
    const input = view.getByTestId('starter-input') as HTMLInputElement
    Object.defineProperty(input, 'files', {
      value: [new File([new Uint8Array(64)], 'lab.zip', { type: 'application/zip' })],
      configurable: true,
    })
    await fireEvent.change(input)
    await waitFor(() => expect(view.getByTestId('starter-file').textContent).toContain('lab.zip'))

    await publish(view)
    await waitFor(() => expect(publishDraft).toHaveBeenCalledTimes(1))
    expect(sent().starterAttachmentId).toBe(51)
  })

  it('本题写了补充要求、选了方式：整份带上', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)
    await ownTeaching(view)
    await fireEvent.click(view.getByRole('radio', { name: /先明确需求再实现/ }))
    await fireEvent.update(view.getByLabelText('本题补充要求'), '不要直接给出答案。')

    await publish(view)
    await waitFor(() => expect(publishDraft).toHaveBeenCalledTimes(1))

    const teaching = (sent() as { teaching?: { systemPrompt?: string; mode?: string } }).teaching
    expect(teaching?.systemPrompt).toBe('不要直接给出答案。')
    expect(teaching?.mode).toBe('spec')
    expect(sent().teachingOwn).toBe(true)
  })

  it('AI 的设置不用点开「更多设置」：要求、默认队友、技能都在，「更多设置」仍收着', async () => {
    await boardAs(MEMBER)
    const view = await mount()

    expect(await view.findByLabelText('本题补充要求')).toBeTruthy()
    expect(view.getByTestId('default-teammate')).toBeTruthy()
    expect(view.getByTestId('teaching-skills')).toBeTruthy()
    expect(view.getByTestId('task-form-more').getAttribute('aria-expanded')).toBe('false')
    expect(view.queryByTestId('task-form-real-name')).toBeNull()
  })

  it('选了技能：发出去的 AI 设置带上它们', async () => {
    listPlatformSkills.mockResolvedValue({ data: [{ name: 'wolfram', description: '算' }], total: 1 })
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)
    const skills = await view.findByTestId('teaching-skills')
    await fireEvent.mouseDown(within(skills).getByRole('combobox', { name: '技能' }))
    await fireEvent.click(await view.findByRole('option', { name: /wolfram/ }))

    await publish(view)
    await waitFor(() => expect(publishDraft).toHaveBeenCalledTimes(1))

    expect((sent() as { teaching?: { skills?: string[] } }).teaching?.skills).toEqual(['wolfram'])
    expect(sent().teachingOwn).toBe(true)
  })

  it('一格没填：沿用上层设置，不带这一项', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)
    await ownTeaching(view)

    await publish(view)
    await waitFor(() => expect(publishDraft).toHaveBeenCalledTimes(1))

    expect(sent().teaching).toEqual({})
  })

  it('默认队友不动选择器：发出去不带 defaultRole，让项目集 / 空间那一层生效', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)
    await openMore(view)

    await publish(view)
    await waitFor(() => expect(publishDraft).toHaveBeenCalledTimes(1))
    // 发布的那份草稿里这一格是空的：服务端不带它，上一层生效。
    expect(sent().defaultRole ?? null).toBeNull()
  })

  it('默认队友选了预设：发出去带上它的名字', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    await fillRequired(view)
    await openMore(view)
    await pickTeammate(view, /学术研究/)

    await publish(view)
    await waitFor(() => expect(publishDraft).toHaveBeenCalledTimes(1))
    expect(sent().defaultRole).toBe('academic-research')
  })

  it('选一份模板：表单按它填好', async () => {
    await boardAs(MEMBER)
    spaceDetail.mockImplementation(async () => {
      const body = spaceBody()
      ;(body.space as Record<string, unknown>).taskTemplates = JSON.stringify([
        { name: '实验题', description: '每周实验', title: '实验 N：', content: '', submitterType: 'USER', rank: 2 },
      ])
      return body
    })
    const view = await mount()

    await fireEvent.click(view.getByRole('button', { name: '使用模板' }))
    await fireEvent.click(await view.findByText('实验题'))
    await pickCategory(view)
    await publish(view)
    await waitFor(() => expect(publishDraft).toHaveBeenCalledTimes(1))

    expect(sent().name).toBe('实验 N：')
    expect(sent().rank).toBe(2)
  })
})

describe('发题页：边写边存', () => {
  it('停手后建一份草稿，地址栏记下它；之后只存改了的那几格', async () => {
    await boardAs(MEMBER)
    const view = await mount()
    expect(startDraft).not.toHaveBeenCalled()

    await fireEvent.update(nameInput(view), '缓存模拟器')
    await waitFor(() => expect(startDraft).toHaveBeenCalledTimes(1), { timeout: 3000 })
    expect((startDraft.mock.calls[0][1] as { name?: string }).name).toBe('缓存模拟器')
    await waitFor(() => expect(view.router.currentRoute.value.query.draft).toBe('61'))

    await fireEvent.update(nameInput(view), 'Lab 3 缓存模拟器')
    await waitFor(() => expect(saveDraft).toHaveBeenCalled(), { timeout: 3000 })
    expect(saveDraft.mock.calls[0]).toEqual([61, { name: 'Lab 3 缓存模拟器' }])
    expect(await view.findByText('草稿已保存')).toBeTruthy()
  })

  it('打开地址栏里的草稿：表单就是它存的样子', async () => {
    await boardAs(MEMBER)
    const view = await mount('?draft=73')

    await waitFor(() => expect(readDraft).toHaveBeenCalledWith(73))
    await waitFor(() => expect(nameInput(view).value).toBe('存着的那道题'))
  })
})

describe('发题页：和芝士一起写', () => {
  it('芝士写的几格填进表单，改动卡上能撤销；撤销由服务端以人的名义写回，表单照着填', async () => {
    await boardAs(MEMBER)
    script = [
      ['tool', { name: 'cheese_problem', call: 'c1' }],
      ['tool_args', { call: 'c1', text: '{"action":"write","draft":61,"name":"Lab 3 缓', at: 0 }],
      ['tool_args', { call: 'c1', text: '存模拟器","difficulty":"中级"}', at: 44 }],
      [
        'change',
        {
          id: 9,
          kind: 'draft',
          undone: [],
          draft: 61,
          taskId: null,
          created: false,
          version: 2,
          name: 'Lab 3 缓存模拟器',
          changed: { name: { before: '', after: 'Lab 3 缓存模拟器' }, rank: { before: null, after: 2 } },
          kept: [],
          notes: [{ field: 'deadline', text: '附件没写截止时间，需要你定' }],
        },
      ],
      ['delta', { text: '已填写名称和难度。', at: 0 }],
      ['done', { stopped: false }],
    ]
    const view = await mount()

    await typeInComposer(view.container, '出一道缓存实验题')
    await fireEvent.click(view.getByRole('button', { name: '发送' }))

    // 问之前先有草稿：芝士写的就是它。
    await waitFor(() => expect(startDraft).toHaveBeenCalledTimes(1))
    // 参数按打字的节奏放进表单（lib/typist.ts），要等一会儿。
    await waitFor(() => expect(nameInput(view).value).toBe('Lab 3 缓存模拟器'), { timeout: 4000 })
    expect(await view.findByText('已填写名称和难度。')).toBeTruthy()
    expect(view.getByText('表单改动')).toBeTruthy()
    expect(view.getByText('待你确认')).toBeTruthy()
    // 芝士写进去的不是人改的：不为它再存一遍。
    expect(saveDraft).not.toHaveBeenCalled()

    undoChange.mockResolvedValue({
      changes: [{ id: 9, kind: 'draft', undone: ['name'] }],
      draft: 61,
      version: 3,
      fields: { name: '' },
    })
    await fireEvent.click(view.getAllByRole('button', { name: '撤销名称' })[0])
    await waitFor(() => expect(nameInput(view).value).toBe(''))
    expect(undoChange).toHaveBeenCalledWith(9, 'name')
    // 服务端已经存下了；表单填回去不算人又改了一次。
    await new Promise((resolve) => setTimeout(resolve, 800))
    expect(saveDraft).not.toHaveBeenCalled()
  })
})

describe('发题页：一份文件拆出几道题', () => {
  /** 芝士从同一份文件建的两份草稿，挂在同一段对话上。 */
  function pair() {
    const lab3 = {
      ...draftBody({ name: 'Lab 3 缓存模拟器', description: '写一个缓存模拟器', submitterType: 'USER', rank: 2 }, 61),
      source: '实验3.pdf',
      conversationId: 'c9',
    }
    const lab4 = {
      ...draftBody({ name: 'Lab 4 矩阵转置', description: '优化转置', submitterType: 'TEAM' }, 62),
      source: '实验3.pdf',
      conversationId: 'c9',
    }
    listDrafts.mockImplementation(async () => [
      { ...lab3, fields: undefined, missing: [] },
      { ...lab4, fields: undefined, missing: ['difficulty'] },
    ])
    readDraft.mockImplementation(async (id: number) => (id === 61 ? lab3 : lab4))
    apiRequest.mockImplementation(async (path: string) =>
      path.endsWith('/conversations/c9')
        ? { id: 'c9', title: '出题', lastActiveAt: '2026-10-20T08:00:00Z', messages: [] }
        : path.endsWith('/conversations')
          ? { conversations: [] }
          : {}
    )
  }

  const threadReads = () =>
    apiRequest.mock.calls.filter(([path]) => String(path).endsWith('/assistant/conversations')).length

  it('表单上方排着这几道，点了切过去；对话还是那一条，不重读', async () => {
    await boardAs(MEMBER)
    pair()
    const view = await mount('?draft=61')

    const strip = await view.findByTestId('draft-strip')
    expect(strip.textContent).toContain('来自 实验3.pdf')
    expect(strip.textContent).toContain('Lab 3 缓存模拟器')
    expect(strip.textContent).toContain('Lab 4 矩阵转置')
    await waitFor(() => expect(threadReads()).toBe(1))

    await fireEvent.click(view.getByTestId('draft-strip-62'))
    await waitFor(() => expect(nameInput(view).value).toBe('Lab 4 矩阵转置'))
    expect(threadReads()).toBe(1)
  })

  it('全部发布：齐了的发出去，缺项的那道标出来、留在原处', async () => {
    await boardAs(MEMBER)
    pair()
    const view = await mount('?draft=61')

    await fireEvent.click(await view.findByTestId('draft-strip-publish-all'))

    await waitFor(() => expect(publishDraft).toHaveBeenCalledWith(61))
    expect(publishDraft).not.toHaveBeenCalledWith(62)
    expect(await view.findByText('已发布 1 道；《Lab 4 矩阵转置》缺少难度')).toBeTruthy()
    // 眼前这道发出去了：切到没发出去的那道。
    await waitFor(() => expect(nameInput(view).value).toBe('Lab 4 矩阵转置'))
  })
})

describe('发题页：右边的芝士', () => {
  it('关上以后页头多一个按钮叫回来；取消固定记在这台浏览器里', async () => {
    await boardAs(MEMBER)
    const view = await mount()

    await fireEvent.click(view.getByRole('button', { name: '关闭' }))
    await waitFor(() => expect(view.queryByTestId('agent-host')).toBeNull())

    await fireEvent.click(view.getByTestId('publish-agent-open'))
    await waitFor(() => expect(view.queryByTestId('agent-host')).not.toBeNull())
    expect(view.queryByTestId('publish-agent-open')).toBeNull()

    await fireEvent.click(view.getByRole('button', { name: '取消固定' }))
    expect(localStorage.getItem('cheesex.agentDocked')).toBe('false')
  })
})
