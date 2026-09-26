/**
 * Switching topics must not show the previous topic's content in the next one.
 *
 * The topic page is reached by changing one route param, so a view that is reused
 * across topics carries whatever it last loaded into the next topic until that
 * topic's own requests come back — the proposal card, the roster, an open roster
 * menu. This drives the real router, ProjectShell, TopicView, TopicHeader,
 * TopicMembers, TopicChatColumn and AgentFeedbackCard; only the network and the
 * heavy panels next to them are stubbed.
 */
import type { FeedbackProposal, Topic, TopicMemberRow } from '@/cx_types'

import { reactive } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

const TOPIC_A = { id: 'topic-a', project_id: 'p1', title: 'Room A', kind: 'topic', status: 'active' } as Topic
const TOPIC_B = { id: 'topic-b', project_id: 'p1', title: 'Room B', kind: 'topic', status: 'active' } as Topic

function member(handle: string, name: string): TopicMemberRow {
  return { id: `m-${handle}`, member_handle: handle, name, role: 'member', agent: false } as unknown as TopicMemberRow
}

function proposal(blockId: string, title: string): FeedbackProposal {
  return {
    block_id: blockId,
    author_handle: 'cheese',
    authored_at: '2026-09-19T00:00:00Z',
    payload: { kind: 'bug', title, problem: '', visibility: 'public', user_said: '用户没有就这个问题说过话' },
  } as unknown as FeedbackProposal
}

// Topic B's answers are held back until the test releases them: the window in
// which topic B is on screen but has none of its own data yet.
const pendingB = vi.hoisted(() => ({
  members: null as null | { promise: Promise<unknown>; resolve: (v: unknown) => void },
  proposals: null as null | { promise: Promise<unknown>; resolve: (v: unknown) => void },
}))

vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  listTopicMembers: vi.fn((topicId: string) =>
    topicId === 'topic-a'
      ? Promise.resolve({ data: [member('old-member', 'Old Roster Person')], total: 1 })
      : pendingB.members!.promise
  ),
  listFeedbackProposals: vi.fn((topicId: string) =>
    topicId === 'topic-a' ? Promise.resolve([proposal('b-a', 'Topic A proposal')]) : pendingB.proposals!.promise
  ),
}))

const store = vi.hoisted(() => ({ value: null as unknown }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store.value }))
vi.mock('@/me', () => ({ myHandle: () => 'alice' }))

// Neighbours of what this test is about; each owns its own loading and is not
// what carries a proposal card or a roster.
vi.mock('@/components/ChatPanel.vue', () => ({
  default: { name: 'ChatPanel', template: '<div><slot name="timeline-end" /></div>' },
}))
vi.mock('@/components/WorkPanel.vue', () => ({ default: { name: 'WorkPanel', template: '<div />' } }))
vi.mock('@/components/TopicAcceptCard.vue', () => ({ default: { name: 'TopicAcceptCard', template: '<div />' } }))
vi.mock('@/components/TopicComputePicker.vue', () => ({
  default: { name: 'TopicComputePicker', template: '<div />' },
}))
vi.mock('@/components/PushPermissionPrompt.vue', () => ({
  default: { name: 'PushPermissionPrompt', template: '<div />' },
}))
vi.mock('@/components/feedback/SubmitFeedbackDialog.vue', () => ({
  default: { name: 'SubmitFeedbackDialog', template: '<div />' },
}))

import ProjectShell from './ProjectShell.vue'
import TopicView from './TopicView.vue'

import i18n from '@/i18n'

function setup() {
  store.value = reactive({
    topics: [TOPIC_A, TOPIC_B],
    members: [],
    unreadMap: {},
    chatPct: 50,
    loadingTopics: false,
    accessDenied: null,
    error: null,
    projectName: 'Project',
    agentName: 'Cheese',
    activeTopicId: null,
    activeDmPeer: null,
    placeById: (id: string) => [TOPIC_A, TOPIC_B].find((t) => t.id === id) ?? null,
    isResolvingPlace: () => false,
    loadPlace: vi.fn(async () => {}),
    markRead: vi.fn(),
    openProject: vi.fn(),
    refreshTopics: vi.fn(),
    refreshUnread: vi.fn(),
    setChatPct: vi.fn(),
  })
  pendingB.members = deferred()
  pendingB.proposals = deferred()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/projects/:projectId',
        component: ProjectShell,
        props: true,
        children: [{ name: 'workspace-topic', path: 'topics/:topicId', component: TopicView, props: true }],
      },
    ],
  })
  const App = { components: { RouterView }, template: '<v-app><router-view /></v-app>' }
  const view = render(App, {
    global: { plugins: [router, createPinia(), createVuetify({ components, directives }), i18n] },
  })
  return { router, view }
}

// Vuetify's overlays (the roster menu) need these browser APIs, which the test DOM
// lacks — same shims as TopicMembers.spec.
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.matchMedia) {
    globalThis.matchMedia = (() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof globalThis.matchMedia
  }
  if (!globalThis.visualViewport) {
    Object.defineProperty(globalThis, 'visualViewport', {
      configurable: true,
      value: { width: 1024, height: 768, offsetLeft: 0, offsetTop: 0, addEventListener() {}, removeEventListener() {} },
    })
  }
  if (!globalThis.devicePixelRatio) {
    Object.defineProperty(globalThis, 'devicePixelRatio', { configurable: true, value: 1 })
  }
})

afterEach(() => vi.clearAllMocks())

describe('switching topics', () => {
  it("shows none of the previous topic's content while the next topic is still loading", async () => {
    const { router, view } = setup()
    await router.push('/projects/p1/topics/topic-a')

    // Topic A is fully on screen, with its roster menu open.
    await waitFor(() => expect(view.baseElement.textContent).toContain('Topic A proposal'))
    await fireEvent.click(view.baseElement.querySelector('.members-mini')!)
    await waitFor(() => expect(view.baseElement.textContent).toContain('Old Roster Person'))

    // Go to topic B; none of its own data has arrived yet.
    await router.push('/projects/p1/topics/topic-b')
    await waitFor(() => expect(view.baseElement.textContent).toContain('Room B'))

    const text = view.baseElement.textContent
    expect(text).not.toContain('Topic A proposal')
    expect(text).not.toContain('Old Roster Person')
    expect(view.baseElement.querySelector('.roster')).toBeNull()

    // When topic B answers, what shows is topic B's own.
    pendingB.members!.resolve({ data: [member('new-member', 'New Roster Person')], total: 1 })
    pendingB.proposals!.resolve([proposal('b-b', 'Topic B proposal')])
    await waitFor(() => expect(view.baseElement.textContent).toContain('Topic B proposal'))
    expect(view.baseElement.textContent).not.toContain('Topic A proposal')
  })
})
