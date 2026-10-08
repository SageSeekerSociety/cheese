/** 页头那颗连接点和底下那条工作条必须同源。
 *
 *  截图里那一刻：工作条写着「芝士Opus正在工作… · 重试中（第 4 次）· 2 小时 06 分」，
 *  页头标题边上却写着「未连接」。原因是页头只读 socket 的那一帧（对话栏报上来的
 *  链路状态），而 socket 在重连时会断上一阵——可这一轮还在跑，工作条写着「重试中」正是
 *  因为它自己接着干。一轮没跑完，这个房间就是连着的。
 *
 *  这份用例钉的是：页头读的是「socket 那帧 **或** 有没有一轮在跑」，而不是单看前者。
 */
import type { Component } from 'vue'

import { h, ref } from 'vue'
import { render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mdAndUp = ref(true)
vi.mock('vuetify', () => ({ useDisplay: () => ({ mdAndUp }) }))

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
}))
vi.mock('@/composables/usePageTitle', () => ({
  usePageTitle: () => ({ setDynamicTitle: vi.fn(), clearDynamicTitle: vi.fn() }),
}))

// 对话栏的替身：它手里握着的正是链路断没断（`linkDown`），也能替一个正在跑的
// 轮次报「开工」——页头要看的恰恰是这两件事。
const chat: { linkDown: boolean; working: ((on: boolean) => void) | null } = {
  linkDown: false,
  working: null,
}
vi.mock('@/views/workspace/TopicChatColumn.vue', () => ({
  default: {
    name: 'TopicChatColumn',
    props: ['focusBlock'],
    setup(
      _props: unknown,
      { expose, emit }: { expose: (o: Record<string, unknown>) => void; emit: (e: string, on: boolean) => void }
    ) {
      const linkDown = ref(chat.linkDown)
      chat.working = (on: boolean) => emit('working', on)
      expose({
        linkDown,
        reloadAccept: () => {},
        reloadFeedback: () => {},
        say: () => true,
        submitQuestion: () => false,
      })
      return () => h('div')
    },
  },
}))

// 页头换成只把「你告诉我的连接状态」写出来的替身。
vi.mock('@/components/TopicHeader.vue', () => ({
  default: {
    name: 'TopicHeader',
    props: ['topic', 'members', 'me', 'connected', 'focus', 'panelOpen'],
    template: '<div data-testid="header" :data-connected="connected ? \'yes\' : \'no\'" />',
  },
}))

// 工作面板只是这一页的邻居，这份用例不关心它画了什么。
vi.mock('@/components/WorkPanel.vue', () => ({
  default: { name: 'WorkPanel', template: '<div data-testid="panel" />' },
}))

import TopicView from './TopicView.vue'

const View = TopicView as unknown as Component

beforeEach(() => {
  query = {}
  push.mockReset()
  replace.mockReset()
  chat.linkDown = false
  chat.working = null
})

function mount() {
  return render(View, { props: { projectId: 'p1', topicId: 't1' } })
}

describe('页头的连接点', () => {
  it('socket 连着就是连着', async () => {
    const { getByTestId } = mount()

    await waitFor(() => expect(getByTestId('header').getAttribute('data-connected')).toBe('yes'))
  })

  it('socket 断了、也没人在干活：才说「未连接」', async () => {
    chat.linkDown = true
    const { getByTestId } = mount()

    await waitFor(() => expect(getByTestId('header').getAttribute('data-connected')).toBe('no'))
  })

  it('一轮还在跑：socket 那一帧是断的，页头也不说「未连接」', async () => {
    chat.linkDown = true
    const { getByTestId } = mount()
    await waitFor(() => expect(getByTestId('header').getAttribute('data-connected')).toBe('no'))

    chat.working!(true)

    await waitFor(() => expect(getByTestId('header').getAttribute('data-connected')).toBe('yes'))
  })
})
