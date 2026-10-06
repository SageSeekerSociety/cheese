/** 平板横放（960–1180）里「打开面板浮层」这一下：浮层收起后再打开，要回到面板刚才在
 * 画的哪一格——包括进房间时自动挑中的那一格（芝士在干活 → 现场）。那一格没写进地址
 * （见 WorkPanel.tabs.spec.ts），所以只能问面板自己。这里钉的就是这根线：打开浮层，
 * 它落在面板此刻的那一格上，而不是浮层自己另挑一个。
 */
import type { Component } from 'vue'

import { h, ref } from 'vue'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// 宽度量得出：1024 落在平板横放那一档，面板是一只浮层。
const mdAndUp = ref(true)
const width = ref(1024)
vi.mock('vuetify', () => ({ useDisplay: () => ({ mdAndUp, width }) }))

// 地址用一个会跟着 replace 变的 ref 装着，route 读的就是它——这样「打开浮层写了什么
// 地址、面板因此收到哪个 tab」能一路对到底。
const query = ref<Record<string, string>>({})
const replace = vi.fn(async (to: { query?: Record<string, unknown> }) => {
  if (!to.query) return
  const next: Record<string, string> = {}
  for (const [k, v] of Object.entries(to.query)) if (typeof v === 'string') next[k] = v
  query.value = next
})
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), replace }),
  useRoute: () => ({
    get query() {
      return query.value
    },
  }),
}))

const TOPIC = { id: 't1', title: '房间', kind: 'topic', project_id: 'p1' }
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({
    topics: [TOPIC],
    members: [],
    unreadMap: {},
    chatPct: 50,
    agentName: '芝士',
    agentHandle: 'cheese',
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
vi.mock('@/api', () => ({
  listTopicMembers: vi.fn(async () => ({ data: [], total: 0 })),
}))
vi.mock('@/composables/usePageTitle', () => ({
  usePageTitle: () => ({ setDynamicTitle: vi.fn(), clearDynamicTitle: vi.fn() }),
}))

// 页头只留面板那颗开关——打开 / 收起都由它走 TopicView 的 togglePanel。
vi.mock('@/components/TopicHeader.vue', () => ({
  default: {
    name: 'TopicHeader',
    emits: ['toggle-panel'],
    template: '<button data-toggle-panel @click="$emit(\'toggle-panel\')">面板</button>',
  },
}))
vi.mock('@/views/workspace/TopicChatColumn.vue', () => ({
  default: { name: 'TopicChatColumn', template: '<div />' },
}))
// 面板替身：把「正在画的哪一格」当作它此刻的 tab 画在 DOM 上，并像真面板那样把它暴露
// 出来（真面板暴露的是内部 active；这里模拟「自动挑中的那一格 = site」）。
vi.mock('@/components/WorkPanel.vue', () => ({
  default: {
    name: 'WorkPanel',
    props: {
      tab: { type: String, default: undefined },
      compact: { type: Boolean, default: false },
      openCardId: { type: String, default: null },
    },
    setup(props: { tab?: string }, { expose }: { expose: (api: unknown) => void }) {
      expose({ activeTab: () => 'site' })
      return () => h('div', { 'data-testid': 'panel', 'data-tab': props.tab ?? '' })
    },
  },
}))

import TopicView from './TopicView.vue'

const View = TopicView as unknown as Component

beforeEach(() => {
  query.value = {}
  replace.mockClear()
})

function mountView() {
  return render(View, { props: { projectId: 'p1', topicId: 't1' } })
}

describe('平板横放：打开面板浮层落在面板的那一格', () => {
  it('打开浮层：回到面板正在画的那一格（自动挑中的「现场」也在内）', async () => {
    const { getByText, getByTestId } = mountView()
    await waitFor(() => expect(getByTestId('panel')).toBeTruthy())

    // 还没人打开：地址里没有 tab，面板画的是这一格，但浮层收着（panelOpen 由地址说）。
    expect(query.value.tab).toBeUndefined()

    await fireEvent.click(getByText('面板'))

    await waitFor(() => expect(getByTestId('panel').getAttribute('data-tab')).toBe('site'))
    expect(replace).toHaveBeenCalled()
  })

  it('再点一下收起：地址里的 tab 清掉，浮层不再开着', async () => {
    const { getByText, getByTestId } = mountView()
    await waitFor(() => expect(getByTestId('panel')).toBeTruthy())

    await fireEvent.click(getByText('面板'))
    await waitFor(() => expect(getByTestId('panel').getAttribute('data-tab')).toBe('site'))

    await fireEvent.click(getByText('面板'))
    await waitFor(() => expect(getByTestId('panel').getAttribute('data-tab')).toBe(''))
  })
})
