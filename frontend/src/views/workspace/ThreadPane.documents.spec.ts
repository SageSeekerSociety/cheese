/** Real thread cards and document pages; only the editor host is stubbed.
 * Web history and the address guard preserve production's slug/UUID distinction.
 */
import type { RouteLocationRaw, Router } from 'vue-router'
import type { Block, Topic } from '@/cx_types'
import type { DocReviewRequest } from '@/lib/docReview'

import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// 外壳那一刻收到了什么：哪一份文档、哪一次 review。
const host = vi.hoisted(() => ({
  reviewEdits: [] as { doc: string | null; request: DocReviewRequest }[],
}))

vi.mock('@/components/work/PanelDocHost.vue', async () => {
  const { defineComponent, h: el } = await import('vue')
  return {
    default: defineComponent({
      name: 'PanelDocHostStub',
      props: { document: { type: Object, default: null } },
      setup(props, { expose }) {
        expose({
          reviewEdits: (request: DocReviewRequest) =>
            host.reviewEdits.push({ doc: (props.document as { id?: string } | null)?.id ?? null, request }),
        })
        return () => el('div', { 'data-open-document': (props.document as { id?: string } | null)?.id ?? '' })
      },
    }),
  }
})

const listBlocks = vi.fn()
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    markRead: vi.fn(),
    agentName: '芝士',
    agentHandle: 'cheese-agent',
    members: [],
    topics: [],
  }),
}))
vi.mock('@/api/threads', () => ({ getThread: vi.fn(async () => ({ root: null })) }))
// 对话那一栏会问这段对话里有没有待存的技能提议：这里没有。
vi.mock('@/api/projectSkills', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/projectSkills')>()),
  listProjectSkills: vi.fn(async () => ({ data: [], total: 0 })),
}))
vi.mock('@/lib/libraryApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/libraryApi')>()),
  listProjectLibrary: vi.fn(async () => ({ data: [], next: null })),
}))
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  getAgentControl: vi.fn(async () => ({ id: null, connected: false })),
  listBlocks: (...args: unknown[]) => listBlocks(...args),
  listTopicMembers: vi.fn(async () => ({ data: [] })),
  listRoomTasks: vi.fn(async () => ({ data: [], total: 0 })),
  // 资料库那一页读项目那一行（`题目/` 的页头）：这个项目不是从题目来的。
  getProject: vi.fn(async (id: string) => ({ id, name: 'Project', task: null })),
}))
// 资料库那一页会读的文档接口：这一份测试只关心「打开的是哪一份、review 到没到」。
const docTitles: Record<string, string> = { 'library-doc': 'Release plan', 'other-doc': 'Weekly notes' }
vi.mock('@/api/projectDocuments', () => ({
  listProjectDocuments: vi.fn(async () => ({
    data: [
      {
        id: 'library-doc',
        project_id: PROJECT,
        kind: 'doc',
        title: docTitles['library-doc'],
        doc_version: 1,
        author: 'alice',
        created_at: '2026-10-12T00:00:00Z',
        updated_at: '2026-10-12T00:00:00Z',
        rank: '0000000000000000001',
      },
    ],
    next: null,
  })),
  searchProjectDocuments: vi.fn(async (_p: string, query: string) => ({ query, library: [], rooms: [] })),
  createProjectDocument: vi.fn(),
  deleteDocument: vi.fn(),
  getDocumentAbout: vi.fn(async (id: string) => ({
    id,
    project_id: PROJECT,
    kind: 'doc',
    title: docTitles[id] ?? 'Document',
    doc_version: 1,
    author: 'alice',
    created_at: '2026-10-12T00:00:00Z',
    updated_at: '2026-10-12T00:00:00Z',
  })),
}))

import ThreadPane from './ThreadPane.vue'

import { topBarBack } from '@/components/common/topBarBack'
import { setLocale } from '@/i18n'
import { addressProps, canonicalAddress, rememberProject } from '@/lib/addresses'
import ProjectLibraryView from '@/views/ProjectLibraryView.vue'

const PROJECT = '00000000-0000-4000-8000-000000000001'
const SLUG = 'doc-card-project'
let sequence = 0
const routers: Router[] = []

/** Vuetify 的断点读 window.innerWidth：手机上才有「顶栏 ← 接管」那一层。 */
const DESKTOP = 1280
const PHONE = 390
function widthIs(px: number) {
  Object.defineProperty(window, 'innerWidth', { value: px, writable: true, configurable: true })
}

beforeEach(() => {
  window.history.replaceState(null, '', '/')
  rememberProject(PROJECT, SLUG)
  setLocale('zh-CN')
  listBlocks.mockReset()
  host.reviewEdits = []
  widthIs(DESKTOP)
  vi.stubGlobal(
    'WebSocket',
    class {
      static OPEN = 1
      readyState = 0
      close() {}
      send() {}
    }
  )
})

afterEach(() => {
  for (const router of routers.splice(0)) router.options.history.destroy()
  topBarBack.value = null
})

function room(threadId: string): Topic {
  return { id: `topic-${threadId}`, project_id: PROJECT, title: 'Room', status: 'active', kind: 'topic' } as Topic
}

function documentCardBlock(threadId: string, threadSeq: number, edits: { old: string; new: string }[]): Block {
  return {
    id: `document-card-${threadSeq}`,
    conversation_id: threadId,
    kind: 'event',
    author_type: 'platform',
    author: 'cheese-agent',
    content: '芝士编辑了资料库文档',
    created_at: '2026-10-12T00:00:00Z',
    meta: {
      action: 'doc',
      document: { id: 'library-doc', title: 'Release plan' },
      doc_edits: edits,
    },
  } as Block
}

async function mountThread(threadId: string, block: Block, start?: RouteLocationRaw) {
  sequence += 1
  listBlocks.mockResolvedValue({ data: [block], has_more: false })
  const topic = room(threadId)
  const threadFullPath = `/projects/${SLUG}/channels/${topic.id}/threads/${threadId}`

  const ThreadRoute = defineComponent({
    setup: () => () =>
      h(ThreadPane, {
        room: topic,
        threadId,
        page: window.innerWidth < 960,
        members: [],
        topicList: [topic],
        memberNames: {},
      }),
  })

  const router = createRouter({
    history: createWebHistory(),
    routes: [
      {
        name: 'workspace-thread',
        path: '/projects/:projectId/channels/:topicId/threads/:threadId',
        component: ThreadRoute,
        meta: { projectFrame: true },
      },
      {
        name: 'project-document',
        path: '/projects/:projectId/docs/:docId',
        component: ProjectLibraryView,
        props: addressProps,
        meta: { projectFrame: true },
      },
      {
        name: 'project-library',
        path: '/projects/:projectId/library',
        component: ProjectLibraryView,
        props: addressProps,
        meta: { projectFrame: true },
      },
    ],
  })
  routers.push(router)
  router.beforeEach(canonicalAddress)
  await router.push(
    start ?? {
      name: 'workspace-thread',
      params: { projectId: PROJECT, topicId: topic.id, threadId },
    }
  )
  await router.isReady()

  const Host = defineComponent({ setup: () => () => h(components.VApp, null, () => h(RouterView)) })
  const view = render(Host, {
    // Vuetify 的断点读 window.innerWidth，且只在建实例那一刻读一次 —— 所以每份用例
    // 现建一个（widthIs 已经设好），不能用模块级共享的那一个。
    // 反馈卡自带的那个对话框要 vue-i18n 的 app.use，和这一份要钉的事无关。
    global: {
      plugins: [createVuetify({ components, directives }), router, createPinia()],
      stubs: { AgentFeedbackCard: true },
    },
  })
  return { ...view, router, threadFullPath }
}

describe('支线里的资料库文档那一行', () => {
  it.each([DESKTOP, PHONE])('%i 宽度点那一行进入对应文档页', async (width) => {
    widthIs(width)
    const threadId = `document-thread-${sequence}`
    const view = await mountThread(threadId, documentCardBlock(threadId, ++sequence, []))

    await fireEvent.click(await view.findByRole('button', { name: /Release plan/ }))

    await waitFor(() => expect(view.router.currentRoute.value.name).toBe('project-document'))
    expect(view.router.currentRoute.value.params).toEqual({ projectId: SLUG, docId: 'library-doc' })
    await waitFor(() => expect(view.container.querySelector('[data-open-document="library-doc"]')).not.toBeNull())
  })

  it('「查看改动」带的这几处，等文档取回来之后送到外壳', async () => {
    const threadId = `review-thread-${sequence}`
    const edits = [
      { old: '旧条款', new: '新条款' },
      { old: '第一版', new: '第二版' },
    ]
    const view = await mountThread(threadId, documentCardBlock(threadId, ++sequence, edits))

    // 那一行末尾是「查看改动」，点它。
    await fireEvent.click(await view.findByRole('button', { name: /查看改动/ }))

    await waitFor(() => expect(view.router.currentRoute.value.name).toBe('project-document'))
    await waitFor(() => expect(view.container.querySelector('[data-open-document="library-doc"]')).not.toBeNull())
    await waitFor(() => expect(host.reviewEdits).toHaveLength(1))
    expect(host.reviewEdits[0]).toEqual({
      doc: 'library-doc',
      request: { edits } as DocReviewRequest,
    })
  })

  it.each([DESKTOP, PHONE])('%i 宽度点改过的那一行本身，打开文档，不进改动对比', async (width) => {
    widthIs(width)
    const threadId = `card-body-thread-${sequence}`
    const view = await mountThread(
      threadId,
      documentCardBlock(threadId, ++sequence, [{ old: '旧条款', new: '新条款' }])
    )

    await fireEvent.click(await view.findByRole('button', { name: /Release plan/ }))

    await waitFor(() => expect(view.router.currentRoute.value.name).toBe('project-document'))
    await waitFor(() => expect(view.container.querySelector('[data-open-document="library-doc"]')).not.toBeNull())
    // 「查看改动」那一次的标注也是文档到了之后才送到；等过同样一段，确认什么都没来。
    await new Promise((resolve) => setTimeout(resolve, 200))
    expect(host.reviewEdits).toHaveLength(0)
  })

  it('手机上顶栏的 ← 回的是来时的支线，不是资料库列表', async () => {
    widthIs(PHONE)
    const threadId = `back-thread-${sequence}`
    const view = await mountThread(threadId, documentCardBlock(threadId, ++sequence, [{ old: 'a', new: 'b' }]))

    await fireEvent.click(await view.findByRole('button', { name: /查看改动/ }))
    await waitFor(() => expect(view.router.currentRoute.value.name).toBe('project-document'))

    // 顶上那颗 ← 由文档页接管，说法是「返回支线」（不是「返回资料库」）。
    await waitFor(() => expect(topBarBack.value?.label).toBe('返回支线'))

    const go = vi.spyOn(window.history, 'go')
    const replace = vi.spyOn(view.router, 'replace')
    topBarBack.value!.onBack()

    // 真正走的是 closeDoc：身后那一格正是来时的支线，退一格就到它。
    expect(go).toHaveBeenCalledWith(-1)
    expect(view.router.options.history.state?.back).toBe(view.threadFullPath)
    // 不是把「返回资料库」那一页 replace 上来。
    expect(replace).not.toHaveBeenCalled()
  })

  it('平常从资料库点开一份文档，← 还是回列表，也不带任何 review', async () => {
    // 手机上资料库列表和「正看着的一份」是两页，← 才有意义。
    widthIs(PHONE)
    // 冷开：直接落在资料库列表，再点开一份文档（没有卡带来的上下文）。
    const view = await mountThread(
      `plain-thread-${sequence}`,
      documentCardBlock(`plain-thread-${sequence}`, ++sequence, [])
    )

    await view.router.push({ name: 'project-library', params: { projectId: PROJECT } })
    // 列表里那一行（⋯ 那颗操作按钮的 aria-label 里也有名字，所以按行选）。
    const row = await waitFor(() => {
      const found = view.container.querySelector<HTMLElement>('.library-row__open')
      expect(found).not.toBeNull()
      return found!
    })
    await fireEvent.click(row)
    await waitFor(() => expect(view.router.currentRoute.value.name).toBe('project-document'))
    await waitFor(() => expect(view.container.querySelector('[data-open-document="library-doc"]')).not.toBeNull())

    // 没有卡带的上下文：外壳什么都没收到。
    expect(host.reviewEdits).toHaveLength(0)

    await waitFor(() => expect(topBarBack.value?.label).toBe('返回资料库'))
    // 身后那一格正是资料库列表：退一格就到它（不是退回来时的支线）。
    const go = vi.spyOn(window.history, 'go')
    const replace = vi.spyOn(view.router, 'replace')
    topBarBack.value!.onBack()
    // Existing library returns replace the UUID route before the address guard canonicalizes it.
    expect(go).not.toHaveBeenCalled()
    expect(replace).toHaveBeenCalledWith({ name: 'project-library', params: { projectId: PROJECT } })
    await waitFor(() => expect(view.router.currentRoute.value.fullPath).toBe(`/projects/${SLUG}/library`))
  })

  it('直接打开文档链接，手机返回仍进入资料库列表', async () => {
    widthIs(PHONE)
    const threadId = `direct-thread-${sequence}`
    const view = await mountThread(threadId, documentCardBlock(threadId, ++sequence, []), {
      name: 'project-document',
      params: { projectId: PROJECT, docId: 'library-doc' },
    })
    await waitFor(() => expect(view.container.querySelector('[data-open-document="library-doc"]')).not.toBeNull())
    expect(host.reviewEdits).toHaveLength(0)
    await waitFor(() => expect(topBarBack.value?.label).toBe('返回资料库'))
    topBarBack.value!.onBack()
    await waitFor(() => expect(view.router.currentRoute.value.fullPath).toBe(`/projects/${SLUG}/library`))
  })

  it('从支线打开的那一份 review，不会跟到另一份文档上', async () => {
    const threadId = `leak-thread-${sequence}`
    const view = await mountThread(threadId, documentCardBlock(threadId, ++sequence, [{ old: 'a', new: 'b' }]))

    await fireEvent.click(await view.findByRole('button', { name: /查看改动/ }))
    await waitFor(() => expect(host.reviewEdits.some((call) => call.doc === 'library-doc')).toBe(true))

    // 换一份文档：这是另一格历史，卡上的上下文不该跟过来。
    await view.router.push({ name: 'project-document', params: { projectId: PROJECT, docId: 'other-doc' } })
    await waitFor(() => expect(view.container.querySelector('[data-open-document="other-doc"]')).not.toBeNull())

    expect(host.reviewEdits.some((call) => call.doc === 'other-doc')).toBe(false)
  })
})
