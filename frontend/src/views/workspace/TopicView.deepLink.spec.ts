/** 已经交付出去的提交里，`Cheese-Task:` 带的是房间页加 `?card=<任务>` 的旧地址。
 * 提交历史改不了，所以这份用例问的是：**照着那个旧地址打开房间页，读的人是不是被
 * 送到了地址点名的那个任务的页面**。
 */
import type { Component } from 'vue'

import { ref } from 'vue'
import { render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mdAndUp = ref(true)
vi.mock('vuetify', () => ({ useDisplay: () => ({ mdAndUp }) }))

let query: Record<string, string> = {}
const push = vi.fn()
const replace = vi.fn()
vi.mock('vue-router', () => ({
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
vi.mock('@/api', () => ({
  listTopicMembers: vi.fn(async () => ({ data: [], total: 0 })),
  getRoomEnvironment: vi.fn(async () => ({ state: 'pending' })),
}))
vi.mock('@/composables/usePageTitle', () => ({
  usePageTitle: () => ({ setDynamicTitle: vi.fn(), clearDynamicTitle: vi.fn() }),
}))

// 三个重组件只是这一页的邻居，这份用例不关心它们画了什么。
vi.mock('@/components/TopicHeader.vue', () => ({
  default: { name: 'TopicHeader', template: '<div />' },
}))
vi.mock('@/views/workspace/TopicChatColumn.vue', () => ({
  default: { name: 'TopicChatColumn', template: '<div />' },
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

import { getRoomEnvironment } from '@/api'

const View = TopicView as unknown as Component

beforeEach(() => {
  query = {}
  push.mockReset()
  replace.mockReset()
  vi.mocked(getRoomEnvironment).mockClear()
})

/** 提交里那条 trailer 带的地址，原样拆成路由看到的查询串。 */
function openTheLinkFromTheCommit(url: string) {
  const parsed = new URL(url)
  query = Object.fromEntries(parsed.searchParams.entries())
  return render(View, { props: { projectId: 'p1', topicId: 't1' } })
}

describe('Cheese-Task 那条地址', () => {
  it('leaves environment status to agent notices instead of polling a room-wide banner', async () => {
    const { container } = openTheLinkFromTheCommit('https://cheese.example/projects/p1/topics/t1')

    await waitFor(() => expect(container.querySelector('[data-testid="panel"]')).not.toBeNull())

    expect(getRoomEnvironment).not.toHaveBeenCalled()
    expect(container.textContent).not.toContain('正在准备工作电脑')
  })

  it.each([
    ['带 tab 的', '?tab=overview&card='],
    ['只有 card 的', '?card='],
  ])('%s旧地址把人送到那个任务的页面', async (_label, prefix) => {
    const task = '2caa58e3-77d8-4129-b20b-055a8e521828'

    openTheLinkFromTheCommit(`https://cheese.example/projects/p1/topics/t1${prefix}${task}`)

    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith({
        name: 'workspace-task',
        params: { projectId: 'p1', topicId: 't1', taskId: task },
      })
    )
  })

  it('地址里没有 card 时，留在房间', async () => {
    const { getByTestId } = openTheLinkFromTheCommit('https://cheese.example/projects/p1/topics/t1')

    await waitFor(() => expect(getByTestId('panel').getAttribute('data-tab')).toBe(''))
    expect(replace).not.toHaveBeenCalled()
  })
})
