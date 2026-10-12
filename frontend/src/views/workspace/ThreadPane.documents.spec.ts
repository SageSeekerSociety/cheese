/** A library-document card in a thread opens that document, on either layout. */
import type { Block, Topic } from '@/cx_types'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const listBlocks = vi.fn()
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => ({ markRead: vi.fn() }) }))
vi.mock('@/api/threads', () => ({ getThread: vi.fn(async () => ({ root: null })) }))
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
}))

import ThreadPane from './ThreadPane.vue'

import { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })
let sequence = 0

beforeEach(() => {
  setLocale('zh-CN')
  listBlocks.mockReset()
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

describe('documents in a thread', () => {
  it.each([
    { page: false, target: 'title' },
    { page: false, target: 'action' },
    { page: true, target: 'title' },
    { page: true, target: 'action' },
  ])('opens the document when clicking $target (page=$page)', async ({ page, target }) => {
    sequence += 1
    const threadId = `document-thread-${sequence}`
    const room = {
      id: `document-room-${sequence}`,
      project_id: 'p1',
      title: 'Room',
      status: 'active',
      kind: 'topic',
    } as Topic
    const block = {
      id: `document-card-${sequence}`,
      conversation_id: threadId,
      kind: 'event',
      author_type: 'platform',
      author: 'agent-test',
      content: 'Document created',
      created_at: '2026-10-12T00:00:00Z',
      meta: { action: 'doc', document: { id: 'library-doc', title: 'Release plan' }, doc_created: true, doc_edits: [] },
    } as Block
    listBlocks.mockResolvedValue({ data: [block], has_more: false })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { name: 'workspace-thread', path: '/thread/:threadId', component: { template: '<div />' } },
        { name: 'project-document', path: '/projects/:projectId/docs/:docId', component: { template: '<div />' } },
      ],
    })
    await router.push({ name: 'workspace-thread', params: { threadId } })
    const view = render(ThreadPane, {
      props: { room, threadId, page, members: [], topicList: [room], memberNames: {} },
      global: { plugins: [vuetify, router], stubs: { AgentFeedbackCard: true } },
    })
    await fireEvent.click(await view.findByText(target === 'title' ? 'Release plan' : '打开'))
    await waitFor(() => expect(router.currentRoute.value.name).toBe('project-document'))
    expect(router.currentRoute.value.params).toEqual({ projectId: 'p1', docId: 'library-doc' })

    router.back()
    await waitFor(() => expect(router.currentRoute.value.name).toBe('workspace-thread'))
    expect(router.currentRoute.value.params.threadId).toBe(threadId)
  })
})
