/** 在频道里审阅一件任务时，预览里指出的一处要进这件任务自己的对话。
 *
 * 补这条之前：那一栏的面板没有接 `submitQuestion`，`@locate` 也没人听，指一处、写一句话、
 * 按「发送」什么都不会发生——界面也不说失败了。这里让面板替身真的把 `locate` 抛上来，
 * 再看它落在哪一条对话栏上：必须是审阅那一栏自己那条，不是频道主对话那一格。
 *
 * 挂的是调用点（`TopicView`），不是那个按任务记句柄的小模块：只喂 `useReviewChat` 的
 * 单测对「页面有没有把它的对话栏接上去」同样绿。
 */
import type { Component } from 'vue'

import { h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// 手机那一档：审阅是一整页，面板和它自己的对话栏都在，少了桌面那条交付页签的接线。
const mdAndUp = ref(false)
vi.mock('vuetify', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vuetify')>()),
  useDisplay: () => ({ mdAndUp, width: ref(390) }),
}))

const query = ref<Record<string, string>>({})
vi.mock('vue-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vue-router')>()),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useRoute: () => ({
    get query() {
      return query.value
    },
  }),
}))

const TOPIC = vi.hoisted(() => ({ id: 't1', title: '房间', kind: 'topic', project_id: 'p1' }))
vi.mock('@/stores/workspace', async () => {
  const { reactive } = await import('vue')
  const store = reactive({
    topics: [TOPIC],
    members: [],
    unreadMap: {},
    chatPct: 50,
    agentName: '芝士',
    agentHandle: 'cheese',
    loadingTopics: false,
    panelPref: null,
    placeById: () => TOPIC,
    isResolvingPlace: () => false,
    loadPlace: vi.fn(),
    markRead: vi.fn(),
    refreshTopics: vi.fn(),
    refreshUnread: vi.fn(),
    setChatPct: vi.fn(),
    describe: vi.fn(),
    reportError: vi.fn(),
  })
  return { useWorkspaceStore: () => store }
})
vi.mock('@/me', () => ({ myHandle: () => 'alice' }))
vi.mock('@/api/threads', () => ({ listThreads: async () => [], openThread: async () => ({ id: 'th' }) }))
vi.mock('@/api/pins', () => ({ listPins: async () => [], pinBlock: vi.fn(), unpinBlock: vi.fn() }))
vi.mock('@/api/chapters', () => ({ listChapters: async () => [], addChapter: vi.fn() }))
vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  listTopicMembers: vi.fn(async () => ({ data: [], total: 0 })),
  listRoomTasks: vi.fn(async () => ({ data: [], total: 0 })),
  getAcceptCards: vi.fn(async () => ({ data: [], total: 0 })),
  getHandInStanding: vi.fn(async () => ({ unhanded: false, waiting: false })),
}))
vi.mock('@/api/reviewComments', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/reviewComments')>()),
  listReviewComments: vi.fn(async () => ({ comments: [] })),
}))
// 审阅那一栏会读这件任务自己的那一行（页头、只读与否都看它）。
vi.mock('@/api/tasks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/tasks')>()),
  getTask: vi.fn(async () => ({
    id: 'k1',
    room_id: 't1',
    title: '一件任务',
    status: 'open',
    owner_handle: 'alice',
    agent_handle: 'cheese',
    branch_name: 'task/k1',
    card: { id: 'c1', status: 'pending', reviewer: 'alice', version: 1 },
  })),
  getTaskRelated: vi.fn(async () => ({ data: [], total: 0 })),
}))
vi.mock('@/composables/usePageTitle', () => ({
  usePageTitle: () => ({ setDynamicTitle: vi.fn(), clearDynamicTitle: vi.fn() }),
}))
vi.mock('@/components/TopicHeader.vue', () => ({ default: { name: 'TopicHeader', template: '<div />' } }))

// 每一格对话栏把自己收到的东西记到这儿：两条（频道的和审阅那一栏的）都挂在这个页面上，
// 断言要分辨是哪一个收到了。
const said = vi.hoisted(() => [] as { where: string; message: string }[])
vi.mock('@/views/workspace/TopicChatColumn.vue', () => ({
  default: {
    name: 'TopicChatColumn',
    props: { taskId: { type: String, default: null } },
    setup(props: { taskId: string | null }, { expose }: { expose: (api: unknown) => void }) {
      expose({
        linkDown: false,
        reloadFeedback: vi.fn(),
        say: (message: string) => {
          said.push({ where: props.taskId ?? 'channel', message })
          return true
        },
        submitQuestion: () => true,
      })
      return () => null
    },
  },
}))

// 面板替身：像真面板一样把预览里指出的那一处抛上来（`locate`），并把审阅时页面交给它
// 的那几样记下来。
const panelProps = vi.hoisted(() => [] as Record<string, unknown>[])
vi.mock('@/components/WorkPanel.vue', () => ({
  default: {
    name: 'WorkPanel',
    props: { submitQuestion: { type: Function, default: undefined }, taskId: { type: String, default: null } },
    emits: ['locate'],
    setup(
      props: { submitQuestion?: unknown; taskId: string | null },
      {
        emit,
        expose,
        slots,
      }: {
        emit: (event: 'locate', payload: { message: string }) => void
        expose: (api: unknown) => void
        slots: { chat?: () => unknown }
      }
    ) {
      expose({ activeTab: () => 'preview' })
      panelProps.push(props as unknown as Record<string, unknown>)
      // 真面板会挂着 `#chat` 那一格（切走也不卸载），这里的替身也得挂着它：页面的
      // 对话栏句柄正是从那一格来的。
      return () => [
        h('button', {
          'data-locate': '',
          onClick: () => emit('locate', { message: '这块留白收一下' }),
        }),
        slots.chat?.(),
      ]
    },
  },
}))

import TopicView from './TopicView.vue'

const View = TopicView as unknown as Component

beforeEach(() => {
  query.value = {}
  said.length = 0
  panelProps.length = 0
})

function mountReview() {
  return render(View, {
    props: { projectId: 'p1', topicId: 't1', reviewTaskId: 'k1' },
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('审阅时预览里指出的一处', () => {
  it('发进这件任务自己的对话，不进频道主对话', async () => {
    const view = mountReview()
    await waitFor(() => expect(view.container.querySelector('[data-locate]')).toBeTruthy())
    await fireEvent.click(view.container.querySelector('[data-locate]')!)
    await waitFor(() => expect(said).toHaveLength(1))
    expect(said[0]).toEqual({ where: 'k1', message: '这块留白收一下' })
  })

  it('面板拿得到审阅那一栏的提问口，指一处才有地方可去', async () => {
    const view = mountReview()
    await waitFor(() => expect(view.container.querySelector('[data-locate]')).toBeTruthy())
    expect(panelProps.some((props) => typeof props.submitQuestion === 'function')).toBe(true)
  })
})
