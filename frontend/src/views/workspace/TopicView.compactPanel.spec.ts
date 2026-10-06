/** 右侧面板放在哪、开不开，看的是主区（对话加面板）有多宽，不是整个窗口：
 *
 * - 主区窄于 840px：两栏放不下，面板是一只浮在对话上的浮层。「概览」开关打开它时
 *   回到面板此刻正在画的那一格（包括进房间时自动挑中、没写进地址的「现场」），收起时
 *   把地址里那一格清掉。浮层不记这个人的选择。
 * - 840px 起：面板和对话并排。1000px 起默认开着，840–1000 默认收着；「概览」开关记下
 *   这个人自己的选择，下次照它来。
 */
import type { Component } from 'vue'

import { h, ref } from 'vue'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// 桌面：有并排这回事。窗口多宽不重要，量的是主区那一块。
const mdAndUp = ref(true)
vi.mock('vuetify', () => ({ useDisplay: () => ({ mdAndUp, width: ref(1280) }) }))
// happy-dom 不排版，主区的宽度在这里直接给。
const mainWidth = ref(800)
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
  mainWidth.value = 800
  if (held.store) held.store.panelPref = null
})

function mountView() {
  return render(View, { props: { projectId: 'p1', topicId: 't1' } })
}

/** 「概览」开关说面板开着没有。 */
function toggleSaysOpen(getByText: (text: string) => HTMLElement): boolean {
  return getByText('概览').getAttribute('aria-pressed') === 'true'
}

describe('主区窄于 840px：面板是浮层', () => {
  it('打开浮层：回到面板正在画的那一格（自动挑中的「现场」也在内）', async () => {
    const { getByText, getByTestId } = mountView()
    await waitFor(() => expect(getByTestId('panel')).toBeTruthy())

    // 还没人打开：地址里没有 tab，浮层收着。
    expect(query.value.tab).toBeUndefined()
    expect(toggleSaysOpen(getByText)).toBe(false)

    await fireEvent.click(getByText('概览'))

    await waitFor(() => expect(getByTestId('panel').getAttribute('data-tab')).toBe('site'))
    expect(toggleSaysOpen(getByText)).toBe(true)
    // 浮在对话上面：背后有一层点了就收起的遮罩。
    expect(document.querySelector('.panel-scrim')).toBeTruthy()
  })

  it('再点一下收起：地址里的 tab 清掉，浮层不再开着', async () => {
    const { getByText, getByTestId } = mountView()
    await waitFor(() => expect(getByTestId('panel')).toBeTruthy())

    await fireEvent.click(getByText('概览'))
    await waitFor(() => expect(getByTestId('panel').getAttribute('data-tab')).toBe('site'))

    await fireEvent.click(getByText('概览'))
    await waitFor(() => expect(getByTestId('panel').getAttribute('data-tab')).toBe(''))
    expect(toggleSaysOpen(getByText)).toBe(false)
  })
})

describe('主区 840px 起：面板和对话并排', () => {
  it('1000px 起默认开着，并排而不是浮在对话上', async () => {
    mainWidth.value = 1200
    const { getByText, getByTestId } = mountView()
    await waitFor(() => expect(getByTestId('panel')).toBeTruthy())

    expect(toggleSaysOpen(getByText)).toBe(true)
    expect(document.querySelector('.panel-scrim')).toBeNull()
  })

  it('840–1000 之间默认收着；打开后并排，不写地址', async () => {
    mainWidth.value = 900
    const { getByText, getByTestId } = mountView()
    await waitFor(() => expect(getByTestId('panel')).toBeTruthy())
    expect(toggleSaysOpen(getByText)).toBe(false)

    await fireEvent.click(getByText('概览'))

    await waitFor(() => expect(toggleSaysOpen(getByText)).toBe(true))
    expect(document.querySelector('.panel-scrim')).toBeNull()
    expect(query.value.tab).toBeUndefined()
  })

  it('并排时自己选过一次，下次进来照这个选择', async () => {
    mainWidth.value = 1200
    const first = mountView()
    await waitFor(() => expect(first.getByTestId('panel')).toBeTruthy())
    await fireEvent.click(first.getByText('概览'))
    await waitFor(() => expect(toggleSaysOpen(first.getByText)).toBe(false))
    first.unmount()

    const again = mountView()
    await waitFor(() => expect(again.getByTestId('panel')).toBeTruthy())
    expect(toggleSaysOpen(again.getByText)).toBe(false)
  })
})
