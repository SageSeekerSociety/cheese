/**
 * A feedback proposal card follows the room while the page is open.
 *
 * The room tells an open page that its proposal cards changed (a `state` frame
 * with resource `feedback`) when one lands, is sent or is dismissed. The card at
 * the end of the conversation must follow that frame: without it a card proposed
 * while someone watches never shows up, and a card sent from another tab never
 * goes away, until the page is reloaded.
 *
 * This drives the real router, ProjectShell, TopicView, TopicChatColumn and
 * AgentFeedbackCard. The chat panel is replaced by a stand-in that can raise the
 * frame the real one raises for a `state` message on the socket.
 */
import type { FeedbackProposal, Topic } from '@/cx_types'

import { reactive } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const TOPIC = { id: 'topic-a', project_id: 'p1', title: 'Room A', kind: 'topic', status: 'active' } as Topic

function proposal(blockId: string, title: string): FeedbackProposal {
  return {
    block_id: blockId,
    author_handle: 'cheese',
    authored_at: '2026-09-19T00:00:00Z',
    payload: { kind: 'bug', title, problem: '', visibility: 'public', user_said: '用户没有就这个问题说过话' },
  } as unknown as FeedbackProposal
}

// What the server lists as live right now; the test changes it between frames.
const server = vi.hoisted(() => ({ live: [] as unknown[] }))

// 聊天栏底部的技能提议卡也会读一次；这里没有提议。
// 项目推送在 query/projectFeed.spec 里测；这里的页面不需要那条连接。
vi.mock('@/query/projectFeed', () => ({ useProjectFeed: () => {} }))
vi.mock('@/api/tasks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/tasks')>()),
}))
vi.mock('@/api/projectSkills', () => ({
  listProjectSkills: vi.fn(() => Promise.resolve({ data: [], total: 0 })),
  confirmProjectSkill: vi.fn(),
  declineProjectSkill: vi.fn(),
}))
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  getTopicComputeProfile: vi.fn(() => new Promise(() => {})),
  // 页面取一份采纳卡（对话栏那一条和「改动」页顶部共用）：这里没有卡。
  getAcceptCards: vi.fn(async () => ({ data: [], has_more: false })),
  listTopicMembers: vi.fn(() => Promise.resolve({ data: [], total: 0 })),
  listFeedbackProposals: vi.fn(() => Promise.resolve([...server.live])),
  // 频道概览里的任务：这里没有。
  listRoomTasks: vi.fn(async () => ({ data: [], total: 0 })),
}))

const store = vi.hoisted(() => ({ value: null as unknown }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store.value }))
vi.mock('@/me', () => ({ myHandle: () => 'alice' }))
// 频道的支线清单：这几条测试不看它。
vi.mock('@/api/threads', () => ({ listThreads: async () => [], openThread: async () => ({ id: 'th' }) }))
// 频道概览的置顶：这几条测试不看它。
vi.mock('@/api/pins', () => ({ listPins: async () => [], pinBlock: vi.fn(), unpinBlock: vi.fn() }))

// The real panel turns a `{type: 'state', resource}` socket message into this
// event; the button stands in for that message arriving.
vi.mock('@/components/ChatPanel.vue', () => ({
  default: {
    name: 'ChatPanel',
    emits: ['state-changed'],
    template: `<div>
      <button data-testid="feedback-frame" @click="$emit('state-changed', 'feedback')" />
      <slot name="timeline-end" />
    </div>`,
  },
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
import { seedProject } from '@/test/seedQueries'

function setup() {
  // 项目框每 30 秒问一次的那几份：刚读过，这一刻不问。
  seedProject('p1', { topics: [TOPIC], unread: {}, privateUnread: {}, notifyLevels: {} }, 'alice')
  store.value = reactive({
    topics: [TOPIC],
    members: [],
    unreadMap: {},
    chatPct: 50,
    rememberTopic: vi.fn(),
    loadingTopics: false,
    accessDenied: null,
    error: null,
    projectName: 'Project',
    agentName: 'Cheese',
    activeTopicId: null,
    activeDmPeer: null,
    markRead: vi.fn(),
    openProject: vi.fn(),
    refreshTopics: vi.fn(),
    refreshUnread: vi.fn(),
    setChatPct: vi.fn(),
  })
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

// Browser APIs Vuetify's layout reads, which the test DOM lacks — same shims as
// ProjectShell.topicSwitch.spec.
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

afterEach(() => {
  server.live = []
  vi.clearAllMocks()
})

describe('feedback proposal cards on an open page', () => {
  it('appear when proposed and go away when sent elsewhere, without a reload', async () => {
    const { router, view } = setup()
    await router.push('/projects/p1/topics/topic-a')
    await waitFor(() => expect(view.baseElement.textContent).toContain('Room A'))
    expect(view.baseElement.textContent).not.toContain('Sandbox cannot resolve hosts')

    // The teammate proposes a card while the page is open.
    server.live = [proposal('b-1', 'Sandbox cannot resolve hosts')]
    await fireEvent.click(view.getByTestId('feedback-frame'))
    await waitFor(() => expect(view.baseElement.textContent).toContain('Sandbox cannot resolve hosts'))

    // Someone sends it from another tab: it is no longer live.
    server.live = []
    await fireEvent.click(view.getByTestId('feedback-frame'))
    await waitFor(() => expect(view.baseElement.textContent).not.toContain('Sandbox cannot resolve hosts'))
  })
})
