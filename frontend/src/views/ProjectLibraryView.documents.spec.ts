/**
 * 资料库里的文档：和文件摆在同一张列表里，点「新建文档」直接建好并打开；对话自带的
 * 文档不在列表里，但搜得到，可以另存一份进来；删一份先问一句。
 */
import type { ProjectDocument } from '../api/projectDocuments'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '../i18n'

import ProjectLibraryView from './ProjectLibraryView.vue'

vi.mock('../api', () => ({
  listProjectLibrary: vi.fn(),
  deleteLibraryFile: vi.fn(),
  downloadFile: vi.fn(),
  libraryFileRawUrl: () => '',
}))
vi.mock('../lib/libraryApi', () => ({
  uploadLibraryFile: vi.fn(),
  replaceLibraryFile: vi.fn(),
  libraryFileBytes: vi.fn(),
}))
vi.mock('../api/projectDocuments', () => ({
  listProjectDocuments: vi.fn(),
  searchProjectDocuments: vi.fn(),
  createProjectDocument: vi.fn(),
  deleteDocument: vi.fn(),
  getDocumentAbout: vi.fn(),
}))
// 打开的那一份文档是协同面板（取数在它的接线外壳里），它自己的事在它自己的测试里；
// 这里只要知道开的是哪一份，以及它的「删除」交给了这一页。
vi.mock('../components/work/PanelDocHost.vue', async () => {
  const { defineComponent: define, h: el } = await import('vue')
  return {
    default: define({
      props: { document: { type: Object, default: null } },
      emits: ['delete'],
      setup:
        (props, { emit }) =>
        () =>
          el('div', { 'data-open-document': props.document?.id }, [
            el('button', { type: 'button', onClick: () => emit('delete') }, 'delete-from-doc'),
          ]),
    }),
  }
})

const { listProjectLibrary } = await import('../api')
const { createProjectDocument, deleteDocument, getDocumentAbout, listProjectDocuments, searchProjectDocuments } =
  await import('../api/projectDocuments')

afterEach(cleanup)

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!('devicePixelRatio' in globalThis)) {
    Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
})

function doc(id: string, title: string, updated: string, extra: Partial<ProjectDocument> = {}): ProjectDocument {
  return {
    id,
    project_id: 'p1',
    kind: 'doc',
    title,
    doc_version: 1,
    author: 'alice',
    created_at: updated,
    updated_at: updated,
    ...extra,
  }
}

beforeEach(() => {
  setLocale('zh-CN')
  vi.clearAllMocks()
  vi.mocked(listProjectLibrary).mockResolvedValue({
    data: [
      {
        path: '预算表.xlsx',
        bytes: 120,
        modified: Date.parse('2026-09-20T10:00:00Z') / 1000,
        added_by: 'alice',
        added_at: '2026-09-20T10:00:00Z',
        room: null,
        replaced: 0,
        references: 0,
      },
    ],
    total: 1,
  })
  vi.mocked(listProjectDocuments).mockResolvedValue({
    data: [doc('d1', '竞品定价对比', '2026-09-21T10:00:00Z'), doc('d0', '旧方案', '2026-09-19T10:00:00Z')],
    total: 2,
  })
  vi.mocked(searchProjectDocuments).mockImplementation(async (_, query) => ({ query, library: [], rooms: [] }))
  vi.mocked(deleteDocument).mockResolvedValue({})
  vi.mocked(getDocumentAbout).mockImplementation(async (id) => doc(id, '', '2026-09-22T10:00:00Z'))
})

const Blank = defineComponent({ render: () => h('div') })

async function mount(url = '/projects/p1/library') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/projects/:projectId/library', name: 'project-library', component: ProjectLibraryView, props: true },
      { path: '/projects/:projectId/topics/:topicId', name: 'workspace-topic', component: Blank },
      { path: '/projects/:projectId/topics/:topicId/tasks/:taskId', name: 'workspace-task', component: Blank },
    ],
  })
  await router.push(url)
  await router.isReady()
  const Host = defineComponent({ setup: () => () => h(components.VApp, null, () => h(RouterView)) })
  const view = render(Host, {
    global: { plugins: [createVuetify({ components, directives }), router, createPinia()] },
  })
  return { ...view, router }
}

const rowNames = (container: Element) =>
  Array.from(container.querySelectorAll('.library-row__name')).map((el) => el.textContent)

describe('资料库里的文档', () => {
  it('文档和文件在同一张列表里，最近改过的在前', async () => {
    const { container } = await mount()
    await waitFor(() => expect(rowNames(container)).toEqual(['竞品定价对比', '预算表.xlsx', '旧方案']))
  })

  it('新建文档直接建好一份空的并打开它', async () => {
    vi.mocked(createProjectDocument).mockResolvedValue(doc('d2', '', '2026-09-22T10:00:00Z'))
    const { container, router } = await mount()
    await waitFor(() => expect(rowNames(container)).toContain('竞品定价对比'))

    await fireEvent.click(screen.getByRole('button', { name: '新建文档' }))

    await waitFor(() => expect(router.currentRoute.value.query.doc).toBe('d2'))
    expect(vi.mocked(createProjectDocument)).toHaveBeenCalledWith('p1', {})
    await waitFor(() => expect(container.querySelector('[data-open-document="d2"]')).not.toBeNull())
  })

  it('对话里的文档搜得到，另存一份进资料库并打开那一份副本', async () => {
    vi.mocked(searchProjectDocuments).mockImplementation(async (_, query) => ({
      query,
      library: [],
      rooms: [
        {
          ...doc('room-doc', '', '2026-09-21T10:00:00Z'),
          room_id: 't1',
          task_id: 'k1',
          snippet: '团队版的定价按人数算',
          room_title: '定价页调研',
          room_title_source: 'human',
        },
      ],
    }))
    vi.mocked(createProjectDocument).mockResolvedValue(doc('copy', '定价页调研', '2026-09-22T10:00:00Z'))
    const { router } = await mount()

    await fireEvent.update(screen.getByRole('searchbox'), '定价')
    await fireEvent.click(await screen.findByRole('button', { name: '另存为文档' }))

    expect(vi.mocked(createProjectDocument)).toHaveBeenCalledWith('p1', { copy_of: 'room-doc' })
    await waitFor(() => expect(router.currentRoute.value.query.doc).toBe('copy'))
  })

  it('搜到的任务文档点开就进那个任务', async () => {
    vi.mocked(searchProjectDocuments).mockImplementation(async (_, query) => ({
      query,
      library: [],
      rooms: [
        {
          ...doc('task-doc', '', '2026-09-21T10:00:00Z'),
          room_id: 't1',
          task_id: 'k1',
          snippet: '团队版的定价按人数算',
          room_title: '定价页调研',
        },
      ],
    }))
    const { router } = await mount()

    await fireEvent.update(screen.getByRole('searchbox'), '定价')
    await fireEvent.click(await screen.findByText('团队版的定价按人数算'))

    await waitFor(() => expect(router.currentRoute.value.path).toBe('/projects/p1/topics/t1/tasks/k1'))
  })

  it('从打开的文档里删除先问一句，答应了才删，删完回到列表', async () => {
    vi.mocked(getDocumentAbout).mockResolvedValue(doc('d1', '竞品定价对比', '2026-09-21T10:00:00Z'))
    const { router } = await mount('/projects/p1/library?doc=d1')

    await fireEvent.click(await screen.findByRole('button', { name: 'delete-from-doc' }))
    await fireEvent.click(await screen.findByRole('button', { name: '取消' }))
    expect(vi.mocked(deleteDocument)).not.toHaveBeenCalled()

    await fireEvent.click(screen.getByRole('button', { name: 'delete-from-doc' }))
    await fireEvent.click(await screen.findByRole('button', { name: '删除' }))
    await waitFor(() => expect(vi.mocked(deleteDocument)).toHaveBeenCalledWith('d1'))
    await waitFor(() => expect(router.currentRoute.value.query.doc).toBeUndefined())
  })
})
