/** 右侧面板放在哪、开不开，看的是主区（对话加面板）有多宽，不是整个窗口：
 *
 * - 主区窄于 840px：两栏放不下，面板是一只浮在对话上的浮层。页头上点哪一样（概览、支线……）
 *   浮层就开在哪一样上；再点正显示的那一样收起，把地址里那一格清掉。浮层不记这个人的选择。
 * - 840px 起：面板和对话并排。1000px 起默认开着，840–1000 默认收着；页头上的开合记下
 *   这个人自己的选择，下次照它来。
 */
import type { Component } from 'vue'

import { h, ref, watch } from 'vue'
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
// token 刷新那条线，它 `import router from '@/router'`
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
vi.mock('@/api/chapters', () => ({
  listChapters: async () => [],
  addChapter: vi.fn(),
  removeChapter: vi.fn(),
  renameChapter: vi.fn(),
}))
vi.mock('@/api', () => ({
  listTopicMembers: vi.fn(async () => ({ data: [], total: 0 })),
  listRoomAttachments: vi.fn(async () => ({ data: [], total: 0 })),
  listRoomFiles: vi.fn(async () => ({ data: [], total: 0, truncated: false })),
  listRoomOutputs: vi.fn(async () => ({ data: [], total: 0 })),
  listRoomDocuments: vi.fn(async () => ({ data: [], total: 0 })),
}))
vi.mock('@/api/routines', () => ({ listProjectRoutines: vi.fn(async () => ({ data: [], total: 0 })) }))
vi.mock('@/composables/usePageTitle', () => ({
  usePageTitle: () => ({ setDynamicTitle: vi.fn(), clearDynamicTitle: vi.fn() }),
}))

// 页头那一组按钮：打开、换、收起都由它们走，按下的那颗说着面板此刻显示的是哪一样。
vi.mock('@/components/TopicHeader.vue', () => ({
  default: {
    name: 'TopicHeader',
    props: { blocks: { type: Array, default: () => [] } },
    emits: ['toggle-block'],
    template: `<div>
      <button v-for="block in blocks" :key="block.key" :aria-pressed="String(block.on)" @click="$emit('toggle-block', block.key)">{{ block.label }}</button>
    </div>`,
  },
}))
vi.mock('@/views/workspace/TopicChatColumn.vue', () => ({
  default: { name: 'TopicChatColumn', template: '<div />' },
}))
// 面板替身：把地址给它的 tab 画在 DOM 上；像真面板那样记着自己在画哪一格（地址换了跟着换），
// 换一格时说出来（`update:tab`）。
vi.mock('@/components/WorkPanel.vue', () => ({
  default: {
    name: 'WorkPanel',
    props: {
      tab: { type: String, default: undefined },
      compact: { type: Boolean, default: false },
      openCardId: { type: String, default: null },
    },
    emits: ['update:tab'],
    setup(
      props: { tab?: string },
      { expose, emit }: { expose: (api: unknown) => void; emit: (e: 'update:tab', key: string) => void }
    ) {
      const active = ref('overview')
      watch(
        () => props.tab,
        (tab) => tab && (active.value = tab)
      )
      expose({
        activeTab: () => active.value,
        select: (key: string) => {
          active.value = key
          emit('update:tab', key)
        },
      })
      return () => h('div', { 'data-testid': 'panel', 'data-tab': props.tab ?? '' })
    },
  },
}))

import TopicView from './TopicView.vue'

import { setLocale } from '@/i18n'

setLocale('zh-CN')

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

/** 页头上那一样的按钮是不是按下的（那一处开着、显示的是它）。 */
function pressed(getByText: (text: string) => HTMLElement, label = '概览'): boolean {
  return getByText(label).getAttribute('aria-pressed') === 'true'
}

describe('主区窄于 840px：面板是浮层', () => {
  it('点页头上的一样：浮层开在它上面', async () => {
    const { getByText, getByTestId } = mountView()
    await waitFor(() => expect(getByTestId('panel')).toBeTruthy())

    // 还没人打开：地址里没有 tab，浮层收着。
    expect(query.value.tab).toBeUndefined()
    expect(pressed(getByText)).toBe(false)

    await fireEvent.click(getByText('支线'))

    await waitFor(() => expect(getByTestId('panel').getAttribute('data-tab')).toBe('threads'))
    expect(pressed(getByText, '支线')).toBe(true)
    expect(pressed(getByText, '概览')).toBe(false)
    // 浮在对话上面：背后有一层点了就收起的遮罩。
    expect(document.querySelector('.panel-scrim')).toBeTruthy()
  })

  it('再点一下收起：地址里的 tab 清掉，浮层不再开着', async () => {
    const { getByText, getByTestId } = mountView()
    await waitFor(() => expect(getByTestId('panel')).toBeTruthy())

    await fireEvent.click(getByText('概览'))
    await waitFor(() => expect(getByTestId('panel').getAttribute('data-tab')).toBe('overview'))

    await fireEvent.click(getByText('概览'))
    await waitFor(() => expect(getByTestId('panel').getAttribute('data-tab')).toBe(''))
    expect(pressed(getByText)).toBe(false)
  })

  it('开着时点另一样：换过去，浮层还开着', async () => {
    const { getByText, getByTestId } = mountView()
    await waitFor(() => expect(getByTestId('panel')).toBeTruthy())

    await fireEvent.click(getByText('概览'))
    await waitFor(() => expect(pressed(getByText, '概览')).toBe(true))
    await fireEvent.click(getByText('支线'))

    await waitFor(() => expect(getByTestId('panel').getAttribute('data-tab')).toBe('threads'))
    expect(pressed(getByText, '支线')).toBe(true)
    expect(pressed(getByText, '概览')).toBe(false)
  })
})

describe('主区 840px 起：面板和对话并排', () => {
  it('1000px 起默认开着，并排而不是浮在对话上', async () => {
    mainWidth.value = 1200
    const { getByText, getByTestId } = mountView()
    await waitFor(() => expect(getByTestId('panel')).toBeTruthy())

    expect(pressed(getByText)).toBe(true)
    expect(document.querySelector('.panel-scrim')).toBeNull()
  })

  it('840–1000 之间默认收着；打开后并排，不写地址', async () => {
    mainWidth.value = 900
    const { getByText, getByTestId } = mountView()
    await waitFor(() => expect(getByTestId('panel')).toBeTruthy())
    expect(pressed(getByText)).toBe(false)

    await fireEvent.click(getByText('概览'))

    await waitFor(() => expect(pressed(getByText)).toBe(true))
    expect(document.querySelector('.panel-scrim')).toBeNull()
    expect(query.value.tab).toBeUndefined()
  })

  it('并排时自己选过一次，下次进来照这个选择', async () => {
    mainWidth.value = 1200
    const first = mountView()
    await waitFor(() => expect(first.getByTestId('panel')).toBeTruthy())
    await fireEvent.click(first.getByText('概览'))
    await waitFor(() => expect(pressed(first.getByText)).toBe(false))
    first.unmount()

    const again = mountView()
    await waitFor(() => expect(again.getByTestId('panel')).toBeTruthy())
    expect(pressed(again.getByText)).toBe(false)
  })
})
