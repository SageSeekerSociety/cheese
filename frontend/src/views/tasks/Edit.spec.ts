// 改题页：挂的是真页面、真表单，接口在 API 层答着（真 axios 会被
// `src/test/setup-network.ts` 逮住，未预期的请求直接判失败）。量的是：
//
// 1. 「默认队友」选「沿用上层设置」时写出的那一位，跟着表单上现在显示的项目集走 ——
//    从第一次画出来就是：项目集来自题目本身、来自打开的修改草稿，都一样。
// 2. 草稿：只打开、不改一格就走，这个人的草稿一份都不多；第一次改动才建这道题的修改。
//    草稿接口答在一份内存里的「这个人的草稿」上（`drafts`），量的是走了以后它里面有什么。
//
// i18n 装真的那一份并锁到 zh-CN；tiptap 换成壳 —— 它跟这一页要量的事无关。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VApp } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/api/http'
import { TaskBoardApi } from '@/api/taskBoard'
import { TaskDraftsApi } from '@/api/taskDrafts'
import i18n, { setLocale } from '@/i18n'

const taskDetail = vi.fn()
const listCategories = vi.fn()
const listAgentTypes = vi.fn()
const apiRequest = vi.fn()
/** 问芝士发出去的那几次请求体（事件流答一句就完）。 */
const asked: Record<string, unknown>[] = []

vi.mock('@/api/eventStream', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  postEventStream: async (
    _path: string,
    body: Record<string, unknown>,
    onEvent: (event: string, data: unknown) => void,
    options?: { onOpen?: () => void }
  ) => {
    asked.push(body)
    options?.onOpen?.()
    onEvent('done', {})
  },
}))

vi.mock('@/api', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  listAgentTypes: (...a: unknown[]) => listAgentTypes(...a),
  request: (...a: unknown[]) => apiRequest(...a),
  AgentChangesApi: { undo: vi.fn(), redo: vi.fn() },
  getAssistantRoutines: async () => ({ routines: [], limit: 20 }),
}))

vi.mock('@/api/projectSkills', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  listPlatformSkills: async () => ({ data: [], total: 0 }),
}))

vi.mock('@/api/taskBoard', () => ({
  TaskBoardApi: {
    detail: (...a: unknown[]) => taskDetail(...a),
    stageEffect: vi.fn(),
  },
}))

type Draft = {
  id: number
  spaceId: number
  taskId: number | null
  version: number
  fields: Record<string, unknown>
} & Record<string, unknown>

/** 这个人在服务端的草稿，草稿接口照它答（同 `routes/tasks/drafts.py`）。 */
const drafts = new Map<number, Draft>()
let nextDraftId = 61
/** 题目现在的样子，作为改题表单的起点（服务端 `_task_as_form`）。 */
let taskForm: Record<string, unknown> = {}

function keep(row: Omit<Draft, 'id'> & { id?: number }): Draft {
  const id = row.id ?? nextDraftId++
  const kept = { ...row, id } as Draft
  drafts.set(id, kept)
  return kept
}
/** 不为空时，建修改草稿的请求停在这里，等测试放行（量建的那一阵里人又改了什么）。 */
let holdCreate: Promise<void> | null = null
const editDraft = (taskId: number) => [...drafts.values()].find((d) => d.taskId === taskId)

vi.mock('@/api/taskDrafts', () => ({
  TaskDraftsApi: {
    changes: async (taskId: number) => {
      const row = editDraft(taskId)
      return { draft: row ?? null, form: row?.fields ?? taskForm }
    },
    forTask: async (taskId: number) => {
      if (holdCreate) await holdCreate
      return editDraft(taskId) ?? keep({ spaceId: SPACE_ID, taskId, version: 0, fields: { ...taskForm } })
    },
    start: async (spaceId: number, fields: Record<string, unknown>) =>
      keep({ spaceId, taskId: null, version: 0, fields }),
    save: vi.fn(async (id: number, fields: Record<string, unknown>) => {
      const row = drafts.get(id)!
      row.fields = { ...row.fields, ...fields }
      row.version += 1
      return { version: row.version, updatedAt: '2026-10-20T08:01:00Z' }
    }),
    publish: vi.fn(),
    remove: vi.fn(async (id: number) => {
      drafts.delete(id)
      return {}
    }),
  },
}))

vi.mock('@/api/taskFiles', () => ({
  listTaskFiles: async () => ({ files: [], starter: null, canDownload: true }),
  addTaskFile: vi.fn(),
  removeTaskFile: vi.fn(),
  replaceTaskFile: vi.fn(),
  setTaskStarter: vi.fn(),
  removeTaskStarter: vi.fn(),
}))

vi.mock('@/api/spaces', () => ({
  SpacesApi: {
    listCategories: (...a: unknown[]) => listCategories(...a),
    listDomainGroups: async () => ({ groups: [] }),
    listMaterials: async () => ({ materials: [], canManage: false }),
  },
}))

vi.mock('@/api/attachments', () => ({
  AttachmentsApi: { upload: vi.fn(), limits: async () => ({ maxFileBytes: 100 * 1024 * 1024 }) },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

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

import TaskEdit from './Edit.vue'

import { askAgent, resetAgent } from '@/stores/agent'

const SPACE_ID = 7
const TASK_ID = 9
/** 题目发布时的项目集（设了「学术研究」）和修改草稿里改成的那个（设了「编程陪练」）。 */
const CATEGORY_A = { id: 3, name: '基础题', displayOrder: 1, archivedAt: null, defaultRole: 'academic-research' }
const CATEGORY_B = { id: 4, name: '进阶题', displayOrder: 2, archivedAt: null, defaultRole: 'coding-tutor' }

const stub = { render: () => null }

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId/tasks/:taskId/edit', name: 'TasksEdit', component: stub },
      { path: '/spaces/:spaceId/tasks/:taskId', name: 'TasksDetail', component: stub },
      { path: '/spaces/:spaceId/tasks', name: 'SpacesDetailTasksList', component: stub },
      { path: '/spaces/:spaceId/library', component: stub },
    ],
  })
}

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

function preset(name: string, title: string) {
  return { name, title, description: '', body: '', skills: [], mcp_servers: [], builtin: true }
}

function task(categoryId: number) {
  return {
    id: TASK_ID,
    name: 'Lab 3 缓存模拟器',
    submitterType: 'USER',
    approved: 'APPROVED',
    creator: { id: 1, username: 'teacher', nickname: '王老师' },
    category: { id: categoryId },
    space: { id: SPACE_ID, name: '数据结构空间', admins: [], classificationTopics: [] },
    teaching: {},
    defaultRole: null,
  }
}

function draftOf(categoryId: number): Draft {
  return {
    id: 61,
    spaceId: SPACE_ID,
    taskId: TASK_ID,
    taskName: 'Lab 3 缓存模拟器',
    name: 'Lab 3 缓存模拟器',
    source: '',
    conversationId: null,
    version: 1,
    updatedAt: '2026-10-20T08:00:00Z',
    missing: [],
    fields: {
      name: 'Lab 3 缓存模拟器',
      submitterType: 'USER',
      rank: 1,
      categoryId,
      submissionSchema: [{ prompt: '提交文件', type: 'FILE', stage: null }],
    },
  }
}

async function mount() {
  const router = makeRouter()
  await router.push(`/spaces/${SPACE_ID}/tasks/${TASK_ID}/edit`)
  await router.isReady()
  setActivePinia(createPinia())
  const Page = defineComponent({ render: () => h(VApp, () => h(TaskEdit as Component)) })
  const view = render(Page, {
    global: { plugins: [createVuetify({ components, directives }), router, i18n] },
  })
  await waitFor(() => expect(view.container.querySelector('form')).not.toBeNull(), { timeout: 20_000 })
  return view
}

beforeEach(() => {
  apiRequest.mockImplementation(async (path: string, init?: { method?: string }) =>
    path.endsWith('/conversations')
      ? init?.method === 'POST'
        ? { id: 'c1', title: '', lastActiveAt: '2026-10-20T08:00:00Z' }
        : { conversations: [] }
      : {}
  )
  listCategories.mockResolvedValue({ categories: [CATEGORY_A, CATEGORY_B] })
  listAgentTypes.mockResolvedValue({
    data: [preset('academic-research', '学术研究'), preset('coding-tutor', '编程陪练')],
    total: 2,
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  drafts.clear()
  nextDraftId = 61
  taskForm = {}
  holdCreate = null
  asked.length = 0
})

describe('改题页：默认队友沿用的那一位', () => {
  it('打开的修改草稿换了项目集：一画出来就写草稿那个项目集的队友，不是题目原来那个的', async () => {
    taskDetail.mockResolvedValue({ task: task(CATEGORY_A.id) })
    keep(draftOf(CATEGORY_B.id))

    const view = await mount()

    await waitFor(() => expect(view.getByTestId('default-teammate-inherited').textContent).toContain('编程陪练'))
    expect(view.getByTestId('default-teammate-inherited').textContent).not.toContain('学术研究')
  })

  it('草稿没动项目集：写题目所在项目集的队友', async () => {
    taskDetail.mockResolvedValue({ task: task(CATEGORY_A.id) })
    keep(draftOf(CATEGORY_A.id))

    const view = await mount()

    await waitFor(() => expect(view.getByTestId('default-teammate-inherited').textContent).toContain('学术研究'))
  })
})

describe('改题页：保存', () => {
  it('保存被服务端拒了：页面上留着服务端那句话，人还在这一页', async () => {
    const said = '这道题的提交项指向了不存在的阶段（HTTP 400）'
    taskDetail.mockResolvedValue({ task: task(CATEGORY_A.id) })
    keep(draftOf(CATEGORY_A.id))
    // No stage changes, so nothing for the stage check to ask about.
    vi.mocked(TaskBoardApi.stageEffect).mockResolvedValue({ failing: 0, reopening: 0, failingNames: [] } as never)
    vi.mocked(TaskDraftsApi.publish).mockRejectedValue(new ApiError(400, said, 'BadRequestError'))
    const view = await mount()

    await fireEvent.click(view.getByRole('button', { name: '保存更改' }))
    await waitFor(() => expect(TaskDraftsApi.publish).toHaveBeenCalledTimes(1))

    // Still there after any toast would have faded: the sentence is on the page.
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(await view.findByText(said)).toBeTruthy()
    expect(view.container.querySelector('form')).not.toBeNull()
  })

  it('保存被拒以后放弃修改：重开的那份修改上不再挂着上一份的拒绝原因', async () => {
    const said = '这道题的提交项指向了不存在的阶段（HTTP 400）'
    taskDetail.mockResolvedValue({ task: task(CATEGORY_A.id) })
    // The changes as they were; once discarded, the page reopens on the task as it is.
    keep(draftOf(CATEGORY_A.id))
    taskForm = draftOf(CATEGORY_A.id).fields
    vi.mocked(TaskBoardApi.stageEffect).mockResolvedValue({ failing: 0, reopening: 0, failingNames: [] } as never)
    vi.mocked(TaskDraftsApi.publish).mockRejectedValue(new ApiError(400, said, 'BadRequestError'))
    const view = await mount()
    await fireEvent.click(view.getByRole('button', { name: '保存更改' }))
    expect(await view.findByText(said)).toBeTruthy()

    await fireEvent.click(view.getByRole('button', { name: '放弃修改' }))

    await waitFor(() => expect(view.queryByRole('button', { name: '放弃修改' })).toBeNull())
    expect(view.queryByText(said)).toBeNull()
  })
})

describe('改题页：修改草稿', () => {
  /** 名称那一格。 */
  function nameInput(view: ReturnType<typeof render>): HTMLInputElement {
    return view.getAllByLabelText('名称', { exact: false }).find((el) => el.tagName === 'INPUT') as HTMLInputElement
  }

  it('只打开、一格没改就走：这个人的草稿一份都不多', async () => {
    taskDetail.mockResolvedValue({ task: task(CATEGORY_A.id) })
    taskForm = draftOf(CATEGORY_A.id).fields
    // On dev the task, its categories and its changes took longer to load than the
    // autosave waits (600 ms); the page's own loading must not count as a change then.
    listCategories.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve({ categories: [CATEGORY_A, CATEGORY_B] }), 800))
    )

    const view = await mount()
    await waitFor(() => expect(nameInput(view).value).toBe('Lab 3 缓存模拟器'))
    await new Promise((resolve) => setTimeout(resolve, 1000))
    cleanup()
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect([...drafts.values()]).toEqual([])
  })

  it('第一次改动才建这道题的修改，改的那一格存进去；不建空间里的新题目', async () => {
    taskDetail.mockResolvedValue({ task: task(CATEGORY_A.id) })
    taskForm = draftOf(CATEGORY_A.id).fields
    const view = await mount()
    await waitFor(() => expect(nameInput(view).value).toBe('Lab 3 缓存模拟器'))

    await fireEvent.update(nameInput(view), 'Lab 3 缓存模拟器（修订）')

    await waitFor(() => expect(drafts.size).toBe(1), { timeout: 3000 })
    const [row] = [...drafts.values()]
    expect(row.taskId).toBe(TASK_ID)
    await waitFor(() => expect(row.fields.name).toBe('Lab 3 缓存模拟器（修订）'))
    expect(await view.findByRole('button', { name: '放弃修改' })).toBeTruthy()
  })

  it('建这份修改的那一阵里又改了：改的停在最后那一版，离开时也存下', async () => {
    taskDetail.mockResolvedValue({ task: task(CATEGORY_A.id) })
    taskForm = draftOf(CATEGORY_A.id).fields
    let release = () => {}
    holdCreate = new Promise((resolve) => (release = resolve))
    const view = await mount()
    await waitFor(() => expect(nameInput(view).value).toBe('Lab 3 缓存模拟器'))

    await fireEvent.update(nameInput(view), '第一版')
    // Autosave has started creating the change draft and is waiting on the server.
    await new Promise((resolve) => setTimeout(resolve, 800))
    await fireEvent.update(nameInput(view), '第二版')
    release()
    await waitFor(() => expect(drafts.size).toBe(1))
    cleanup()

    const [row] = [...drafts.values()]
    await waitFor(() => expect(row.fields.name).toBe('第二版'), { timeout: 3000 })
  })

  it('还没有修改时先问芝士：这一问挂在刚建的这份修改上', async () => {
    resetAgent()
    taskDetail.mockResolvedValue({ task: task(CATEGORY_A.id) })
    taskForm = draftOf(CATEGORY_A.id).fields
    await mount()

    await askAgent('请检查这道题')

    const [row] = [...drafts.values()]
    expect(row.taskId).toBe(TASK_ID)
    expect(asked.at(-1)).toMatchObject({ question: '请检查这道题', page: { kind: 'draft', id: row.id } })
    expect(asked.at(-1)).not.toHaveProperty('no_page')
  })
})
