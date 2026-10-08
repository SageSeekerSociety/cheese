/** 专注模式：面板铺满工作区，对话收成左边一条窄边。
 *
 * 对话让开了，可这件事里等人做的决定不能跟着消失：采纳那一条跟到面板底部，同一时刻
 * 只有一份。入口在面板页签栏右端，出口在那里和对话窄边上各一个。
 */
import type { Component } from 'vue'

import { h, ref } from 'vue'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// 桌面：有并排这回事。窗口多宽不重要，量的是主区那一块。
const mdAndUp = ref(true)
vi.mock('vuetify', () => ({ useDisplay: () => ({ mdAndUp, width: ref(1280) }) }))
// happy-dom 不排版，主区的宽度在这里直接给。
const mainWidth = ref(1280)
vi.mock('@vueuse/core', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@vueuse/core')>()),
  useElementSize: () => ({ width: mainWidth, height: ref(600) }),
}))

// 地址用一个会跟着 replace 变的 ref 装着，route 读的就是它——这样「打开浮层写了什么
// 地址、面板因此收到哪个 tab」能一路对到底。
const query = ref<Record<string, string>>({})
const replace = vi.fn(async (to: { query?: Record<string, unknown> }) => {
  if (!to.query) return
  const next: Record<string, string> = {}
  for (const [k, v] of Object.entries(to.query)) if (typeof v === 'string') next[k] = v
  query.value = next
})
// 只换掉这两个取用点，模块其余部分照旧：这个页面底下会摸到 api 层的
// `network/Interceptors/hooks/refreshToken.ts`，它 `import router from '@/router'`
// —— 整个替掉 vue-router 的话，那棵真路由树在导入时就炸了（见 proto-router-stub.ts
// 对同一条链的说明）。
vi.mock('vue-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vue-router')>()),
  useRouter: () => ({ push: vi.fn(), replace }),
  useRoute: () => ({
    get query() {
      return query.value
    },
  }),
}))

const TOPIC = vi.hoisted(() => ({ id: 't1', title: '房间', kind: 'topic', project_id: 'p1' }))
// 「概览」开关记下的选择在 store 上；它得是响应式的，面板开不开跟着它变。
const held = vi.hoisted(() => ({ store: null as null | { panelPref: boolean | null } }))
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
    panelPref: null as boolean | null,
    setPanelPref(open: boolean) {
      store.panelPref = open
    },
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
  held.store = store
  return { useWorkspaceStore: () => store }
})
vi.mock('@/me', () => ({ myHandle: () => 'alice' }))
// 频道的支线清单：这几条测试不看它。
vi.mock('@/api/threads', () => ({ listThreads: async () => [], openThread: async () => ({ id: 'th' }) }))
// 频道概览的置顶：这几条测试不看它。
vi.mock('@/api/pins', () => ({ listPins: async () => [], pinBlock: vi.fn(), unpinBlock: vi.fn() }))
vi.mock('@/api', () => ({
  listTopicMembers: vi.fn(async () => ({ data: [], total: 0 })),
}))
vi.mock('@/composables/usePageTitle', () => ({
  usePageTitle: () => ({ setDynamicTitle: vi.fn(), clearDynamicTitle: vi.fn() }),
}))

// 页头只留「概览」那颗开关——打开 / 收起都由它走，它也说着面板此刻开没开。
vi.mock('@/components/TopicHeader.vue', () => ({
  default: {
    name: 'TopicHeader',
    props: { panelOpen: { type: Boolean, default: false } },
    emits: ['toggle-panel'],
    template:
      '<button data-toggle-panel :aria-pressed="String(panelOpen)" @click="$emit(\'toggle-panel\')">概览</button>',
  },
}))
// 按钮只要能点、带着说法；Vuetify 在这份测试里没有装。
vi.mock('@/components/base/BaseButton.vue', () => ({
  default: { name: 'BaseButton', emits: ['click'], template: '<button @click="$emit(\'click\')"><slot /></button>' },
}))
vi.mock('@/views/workspace/TopicChatColumn.vue', () => ({
  default: {
    name: 'TopicChatColumn',
    props: { acceptElsewhere: { type: Boolean, default: false } },
    template: '<div data-testid="chat"><div v-if="!acceptElsewhere" data-testid="accept">采纳</div></div>',
  },
}))
vi.mock('@/components/TopicAcceptCard.vue', () => ({
  default: { name: 'TopicAcceptCard', template: '<div data-testid="accept">采纳</div>' },
}))
// 面板替身：把页签栏右端那几颗按钮（插槽）照样画出来。
vi.mock('@/components/WorkPanel.vue', () => ({
  default: {
    name: 'WorkPanel',
    setup(_: unknown, { expose, slots }: { expose: (api: unknown) => void; slots: Record<string, () => unknown> }) {
      expose({ activeTab: () => 'changes' })
      return () => h('div', { 'data-testid': 'panel' }, slots['tab-actions']?.() as never)
    },
  },
}))

import TopicView from './TopicView.vue'

import { setLocale } from '@/i18n'

const View = TopicView as unknown as Component

beforeEach(() => {
  setLocale('zh-CN')
  query.value = {}
  if (held.store) held.store.panelPref = true
})

function mountView() {
  return render(View, { props: { projectId: 'p1', topicId: 't1' } })
}

describe('专注模式', () => {
  it('铺满之后，采纳那一条还在，而且只有一份', async () => {
    const { getByTitle, findAllByTestId } = mountView()
    await waitFor(() => expect(getByTitle('专注模式')).toBeTruthy())
    expect(await findAllByTestId('accept')).toHaveLength(1)

    await fireEvent.click(getByTitle('专注模式'))

    await waitFor(async () => expect(await findAllByTestId('accept')).toHaveLength(1))
  })

  it('对话那条窄边能回到并排', async () => {
    const { getByTitle, getAllByTitle, queryAllByTitle } = mountView()
    await waitFor(() => expect(getByTitle('专注模式')).toBeTruthy())
    await fireEvent.click(getByTitle('专注模式'))
    await waitFor(() => expect(getAllByTitle('退出专注模式').length).toBe(2))

    await fireEvent.click(getAllByTitle('退出专注模式')[0])

    await waitFor(() => expect(queryAllByTitle('退出专注模式')).toHaveLength(0))
  })
})
