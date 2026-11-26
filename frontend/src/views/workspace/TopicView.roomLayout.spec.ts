/** 桌面上频道页交给外壳布局区的那几块：对话一块，工作面板的每一格各一块。
 *
 * 对话看不见的时候（别的格子放大了、它那一格收着），这件事里等人做的决定不能跟着消失：
 * 采纳那一条贴到读者在看的那一格工作面板底下，同一时刻只有一份。页头那一组按钮开合对话旁边
 * 的那几块：进来是概览，开别的概览就让开，几块能同时开着；全关掉时对话占满，下一次进来又是
 * 概览。布局区本身（拖、放大、记住摆法）是外壳的事，这里用一个只记「交来了哪几块、哪几块在
 * 屏幕上」的外壳。
 */
import type { Component } from 'vue'
import type { PagePanelsHost } from '@/layouts/pagePanels'

import { defineComponent, h, nextTick, ref } from 'vue'
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
    projects: [],
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
// 频道的讨论串清单：这几条测试不看它。
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

// 页头那一组按钮（⋯ 里的也摊开成一颗）：按下的那几颗说着哪几块开着。
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
// 面板替身：把贴在底下的那一条（插槽）照样画出来；像真面板那样，换到一格时请布局区显示它。
vi.mock('@/components/WorkPanel.vue', () => ({
  default: {
    name: 'WorkPanel',
    props: { layout: { type: Object, default: null } },
    setup(
      props: { layout: { show: (key: string) => void } | null },
      { expose, slots }: { expose: (api: unknown) => void; slots: Record<string, () => unknown> }
    ) {
      let active = 'overview'
      expose({
        activeTab: () => active,
        select: (key: string) => {
          active = key
          props.layout?.show(key)
        },
        closeTab: () => {},
      })
      return () => h('div', { 'data-testid': 'panel' }, slots.dock?.() as never)
    },
  },
}))

import TopicView from './TopicView.vue'

import { setLocale } from '@/i18n'
import { providePagePanels } from '@/layouts/pagePanels'

const View = TopicView as unknown as Component

beforeEach(() => {
  setLocale('zh-CN')
  query.value = {}
  if (held.store) held.store.panelPref = true
})

let shell: PagePanelsHost

function mountView() {
  const Shell = defineComponent({
    setup() {
      shell = providePagePanels()
      return () => h(View, { projectId: 'p1', topicId: 't1' })
    },
  })
  return render(Shell)
}

const ids = () => shell.claim.value?.spec.panels.map((panel) => panel.id) ?? []
const panel = (id: string) => shell.claim.value?.spec.panels.find((p) => p.id === id)

describe('频道页在外壳布局区里', () => {
  it('交给外壳的是对话和工作面板的每一格', async () => {
    mountView()
    await waitFor(() => expect(ids()).toEqual(['conversation', 'overview', 'threads', 'files', 'routines']))
  })

  it('对话在屏幕上时，采纳那一条在对话里，只有一份', async () => {
    const { findAllByTestId, getByTestId } = mountView()
    shell.visible.value = ['conversation', 'overview']
    await nextTick()
    const accept = await findAllByTestId('accept')
    expect(accept).toHaveLength(1)
    expect(getByTestId('chat').contains(accept[0])).toBe(true)
  })

  it('对话看不见时，采纳那一条贴到工作面板底下，仍只有一份', async () => {
    const { findAllByTestId, getByTestId } = mountView()
    shell.visible.value = ['overview']
    await waitFor(async () => {
      const accept = await findAllByTestId('accept')
      expect(accept).toHaveLength(1)
      expect(getByTestId('panel').contains(accept[0])).toBe(true)
    })
  })

  it('标题栏上的 ✕ 收起工作面板那一处，对话不动', async () => {
    mountView()
    await waitFor(() => expect(panel('overview')?.hidden).toBe(false))

    shell.claim.value!.spec.onClose!('overview')

    await waitFor(() => expect(panel('overview')?.hidden).toBe(true))
    expect(panel('threads')?.hidden).toBe(true)
    expect(panel('conversation')?.hidden).toBeFalsy()
  })
})

describe('频道页页头那一组按钮', () => {
  /** 此刻在屏幕上的那几块（没藏起来的），对话除外。 */
  const inSight = () =>
    (shell.claim.value?.spec.panels ?? [])
      .filter((one) => one.id !== 'conversation' && !one.hidden)
      .map((one) => one.id)
  const pressed = (button: HTMLElement) => button.getAttribute('aria-pressed') === 'true'

  it('进来是概览：一栏窄的目录，没有标题栏；只有「概览」是按下的', async () => {
    const { getByText } = mountView()
    await waitFor(() => expect(inSight()).toEqual(['overview']))
    expect(panel('overview')).toMatchObject({ bare: true })
    expect(panel('overview')?.titled).toBeFalsy()
    expect(pressed(getByText('概览'))).toBe(true)
    expect(pressed(getByText('讨论串'))).toBe(false)
    expect(pressed(getByText('文件'))).toBe(false)
  })

  it('点讨论串，概览让开；再点文件，两块都开着，两颗都按下', async () => {
    const { getByText } = mountView()
    await waitFor(() => expect(inSight()).toEqual(['overview']))
    await fireEvent.click(getByText('讨论串'))
    await waitFor(() => expect(inSight()).toEqual(['threads']))
    expect(pressed(getByText('概览'))).toBe(false)
    await fireEvent.click(getByText('文件'))
    await waitFor(() => expect(inSight()).toEqual(['threads', 'files']))
    expect(pressed(getByText('讨论串'))).toBe(true)
    expect(pressed(getByText('文件'))).toBe(true)
    // 再点一下开着的那一颗：只关它那一块。
    await fireEvent.click(getByText('讨论串'))
    await waitFor(() => expect(inSight()).toEqual(['files']))
  })

  it('⋯ 里的例行事务开在文件那一处，「文件」那一颗不再按下', async () => {
    const { getByText } = mountView()
    await waitFor(() => expect(inSight()).toEqual(['overview']))
    await fireEvent.click(getByText('文件'))
    await waitFor(() => expect(inSight()).toEqual(['files']))
    await fireEvent.click(getByText('例行事务'))
    await waitFor(() => expect(inSight()).toEqual(['routines']))
    expect(pressed(getByText('文件'))).toBe(false)
  })

  it('全关掉时对话占满；离开再进来，旁边又是概览', async () => {
    const first = mountView()
    await waitFor(() => expect(inSight()).toEqual(['overview']))
    await fireEvent.click(first.getByText('概览'))
    await waitFor(() => expect(inSight()).toEqual([]))
    expect(panel('conversation')?.hidden).toBeFalsy()
    first.unmount()

    mountView()
    await waitFor(() => expect(inSight()).toEqual(['overview']))
  })
})
