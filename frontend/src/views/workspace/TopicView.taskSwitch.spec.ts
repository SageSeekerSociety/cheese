/** 在一个频道里从一件任务换到另一件：换过去的那一件在频道的任务清单里已经读过，
 * 页面就照着那一行先画出来，任务自己那一读回来再原地换——不先清空成一个转圈。
 */
import type { Component } from 'vue'

import { ref } from 'vue'

const listed = vi.hoisted(() => ({
  id: 'k2',
  room_id: 't1',
  title: '清单里那一件',
  status: 'open',
  presentation: { column: 'building' },
}))
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mdAndUp = ref(true)
vi.mock('vuetify', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vuetify')>()),
  useDisplay: () => ({ mdAndUp }),
}))

let query: Record<string, string> = {}
const push = vi.fn()
const replace = vi.fn()
// 只换掉这两个取用点，模块其余部分照旧：这个页面底下会摸到 api 层的
// `network/Interceptors/hooks/refreshToken.ts`，它 `import router from '@/router'`
// —— 整个替掉 vue-router 的话，那棵真路由树在导入时就炸了（见 proto-router-stub.ts
// 对同一条链的说明）。
vi.mock('vue-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vue-router')>()),
  useRouter: () => ({ push, replace }),
  useRoute: () => ({
    get query() {
      return query
    },
  }),
}))

const TOPIC = { id: 't1', title: '房间', kind: 'topic' }
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    topics: [TOPIC],
    members: [],
    unreadMap: {},
    chatPct: 50,
    rememberTopic: vi.fn(),
    loadingTopics: false,
    placeById: () => TOPIC,
    isResolvingPlace: () => false,
    loadPlace: vi.fn(),
    markRead: vi.fn(),
    refreshTopics: vi.fn(),
    refreshUnread: vi.fn(),
    setChatPct: vi.fn(),
  }),
}))
vi.mock('@/me', () => ({ myHandle: () => 'alice' }))
// 频道的支线清单：这几条测试不看它。
vi.mock('@/api/threads', () => ({ listThreads: async () => [], openThread: async () => ({ id: 'th' }) }))
// 频道概览的置顶：这几条测试不看它。
vi.mock('@/api/pins', () => ({ listPins: async () => [], pinBlock: vi.fn(), unpinBlock: vi.fn() }))
vi.mock('@/api', () => ({
  listTopicMembers: vi.fn(async () => ({ data: [], total: 0 })),
  getRoomEnvironment: vi.fn(async () => ({ state: 'pending' })),
  // 频道的任务清单：进过这个频道，里面这件任务那一行已经读过。
  listRoomTasks: vi.fn(async () => ({ data: [listed], total: 1 })),
}))
// 任务自己的那一读一直没回来：这一刻屏幕上能画的只有清单里那一行。
vi.mock('@/api/tasks', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/tasks')>()),
  getTask: vi.fn(() => new Promise(() => {})),
  // 任务概览那一格的「相关」：这里不看它。
  getTaskRelated: vi.fn(() => new Promise(() => {})),
}))
vi.mock('@/composables/usePageTitle', () => ({
  usePageTitle: () => ({ setDynamicTitle: vi.fn(), clearDynamicTitle: vi.fn() }),
}))

// 三个重组件只是这一页的邻居，这份用例不关心它们画了什么。
vi.mock('@/components/TopicHeader.vue', () => ({
  default: { name: 'TopicHeader', template: '<div />' },
}))
vi.mock('@/views/workspace/TopicChatColumn.vue', () => ({
  default: { name: 'TopicChatColumn', template: '<div data-testid="conversation" />' },
}))
// 工作面板换成一个只把「我收到了什么」写出来的替身：这一页交给它的那两个 prop，
// 就是地址栏里那两个查询串的去向。
vi.mock('@/components/WorkPanel.vue', () => ({
  default: {
    name: 'WorkPanel',
    props: ['tab'],
    template: '<div data-testid="panel" :data-tab="tab ?? \'\'" />',
  },
}))

import TopicView from './TopicView.vue'

import { fetchRoomTasks } from '@/lib/topicPanelCache'

const View = TopicView as unknown as Component

beforeEach(() => {
  query = {}
})

describe('在频道里换一件任务', () => {
  it('清单里已有的那一件马上画出来，不等它自己那一读', async () => {
    await fetchRoomTasks('t1')
    const view = render(View, {
      props: { projectId: 'p1', topicId: 't1', taskId: 'k1' },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    await view.rerender({ projectId: 'p1', topicId: 't1', taskId: 'k2' })

    expect(view.queryByTestId('conversation')).not.toBeNull()
  })

  it('清单里没有的那一件，等它自己那一读', async () => {
    await fetchRoomTasks('t1')
    const view = render(View, {
      props: { projectId: 'p1', topicId: 't1', taskId: 'k1' },
      global: { plugins: [createVuetify({ components, directives })] },
    })
    await view.rerender({ projectId: 'p1', topicId: 't1', taskId: 'k3' })

    expect(view.queryByTestId('conversation')).toBeNull()
  })
})
