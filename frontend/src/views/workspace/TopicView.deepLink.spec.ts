/** 「你来看一眼这条活」是一条发得出去的链接。
 *
 * 交付出去的提交里那条 `Cheese-Task:` 带的就是这个地址（`backend` 的
 * `pr_text.task_trailer`）。所以这份用例问的是：**照着那个地址打开这一页，工作面板
 * 是不是真的停在总览那一格、并且下钻到了地址点名的那条活**。拼出字符串不算数——一
 * 个查询串名字写错、或者少了 `tab`，链接照样"看起来像个链接"，点开却落在别处。
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
vi.mock('@/api', () => ({ getTopicAgent: vi.fn(async () => null) }))
vi.mock('@/composables/usePageTitle', () => ({
  usePageTitle: () => ({ setDynamicTitle: vi.fn(), clearDynamicTitle: vi.fn() }),
}))

// 三个重组件只是这一页的邻居，这份用例不关心它们画了什么。
vi.mock('@/components/TopicHeader.vue', () => ({
  default: { name: 'TopicHeader', template: '<div />' },
}))
vi.mock('@/components/RoomEnvironmentStatus.vue', () => ({
  default: { name: 'RoomEnvironmentStatus', template: '<div />' },
}))
vi.mock('@/views/workspace/TopicChatColumn.vue', () => ({
  default: { name: 'TopicChatColumn', template: '<div />' },
}))
// 工作面板换成一个只把「我收到了什么」写出来的替身：这一页交给它的那两个 prop，
// 就是地址栏里那两个查询串的去向。
vi.mock('@/components/WorkPanel.vue', () => ({
  default: {
    name: 'WorkPanel',
    props: ['tab', 'openCardId'],
    template: '<div data-testid="panel" :data-tab="tab ?? \'\'" :data-open-card="openCardId ?? \'\'" />',
  },
}))

import TopicView from './TopicView.vue'

const View = TopicView as unknown as Component

beforeEach(() => {
  query = {}
  push.mockReset()
  replace.mockReset()
})

/** 提交里那条 trailer 带的地址，原样拆成路由看到的查询串。 */
function openTheLinkFromTheCommit(url: string) {
  const parsed = new URL(url)
  query = Object.fromEntries(parsed.searchParams.entries())
  return render(View, { props: { projectId: 'p1', topicId: 't1' } })
}

describe('Cheese-Task 那条地址', () => {
  it('打开后停在总览那一格，并且下钻到地址点名的那条活', async () => {
    const task = '2caa58e3-77d8-4129-b20b-055a8e521828'

    const { getByTestId } = openTheLinkFromTheCommit(
      `https://cheese.example/projects/p1/topics/t1?tab=overview&card=${task}`
    )

    await waitFor(() => {
      expect(getByTestId('panel').getAttribute('data-open-card')).toBe(task)
      expect(getByTestId('panel').getAttribute('data-tab')).toBe('overview')
    })
  })

  it('只有 card 没有 tab 时，读的人停在他上次待着的那一格', async () => {
    // 这就是 `tab=overview` 必须一起写进链接的原因：`card` 只说打开哪条活，说不了
    // 停在哪一格，而工作面板的格子是这一页从地址里读的。
    const task = '2caa58e3-77d8-4129-b20b-055a8e521828'

    const { getByTestId } = openTheLinkFromTheCommit(`https://cheese.example/projects/p1/topics/t1?card=${task}`)

    await waitFor(() => {
      expect(getByTestId('panel').getAttribute('data-open-card')).toBe(task)
      expect(getByTestId('panel').getAttribute('data-tab')).toBe('')
    })
  })

  it('地址里没有这两个查询串时，什么也不下钻', async () => {
    const { getByTestId } = openTheLinkFromTheCommit('https://cheese.example/projects/p1/topics/t1')

    await waitFor(() => {
      expect(getByTestId('panel').getAttribute('data-open-card')).toBe('')
      expect(getByTestId('panel').getAttribute('data-tab')).toBe('')
    })
  })
})
