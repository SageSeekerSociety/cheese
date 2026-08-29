/** 侧栏那棵树：房间 → 房间里派出去的活 → 那件活骑的 PR。
 *
 * 「现在 task 不可见，thread 不可见」——一件活在界面上只在它被派出去的那一刻、
 * 房间时间线上一条标记里出现过一次，往下滚就没了。房间因此答不出「有哪些活在跑、
 * 谁在做、交付到哪一步」，于是照着自己那份清单又做一遍（8-11 两套迁移就是这么来的）。
 *
 * 这一份挂真实的 TopicSidebar、喂真实的一棵树，从 DOM 上读结果——要钉的是
 * 「人打开侧栏，眼睛能不能看见这个房间里在做什么」。
 */
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { createI18n } from 'vue-i18n'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    listProjectAgents: vi.fn().mockResolvedValue({ data: [] }),
    listProjects: vi.fn().mockResolvedValue({ data: [] }),
  }
})

vi.mock('vue-router', async () => {
  const { ref } = await import('vue')
  const currentRoute = ref({ query: {}, params: {}, name: 'workspace-topic', matched: [], fullPath: '/' })
  return {
    useRoute: () => currentRoute.value,
    useRouter: () => ({ push: vi.fn(), replace: vi.fn(), currentRoute }),
    RouterLink: { template: '<a><slot /></a>' },
  }
})

import TopicSidebar from '../TopicSidebar.vue'

const Sidebar = TopicSidebar as unknown as Component

const ROOM: Topic = {
  id: 'room-1',
  project_id: 'p1',
  parent_id: null,
  title: '运维',
  kind: 'topic',
  status: 'active',
  created_at: '2026-08-23T00:00:00Z',
}

/** 一件活，长成侧栏要的那个「地点」形状（见 lib/place.ts）。 */
function thread(over: Partial<Topic> = {}): Topic {
  return {
    id: 'thread-1',
    project_id: 'p1',
    // 支线挂在**房间**下面，不嵌套 —— 树就是靠这一条边建出来的。
    parent_id: 'room-1',
    title: '查一下分页接口',
    kind: 'thread',
    status: 'open',
    created_at: '2026-08-23T01:00:00Z',
    ...over,
  }
}

let vuetify: ReturnType<typeof createVuetify>
const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh: {} } })

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
})

beforeEach(() => {
  setActivePinia(createPinia())
  // 展开状态是按项目落盘的，不清掉就会漏到下一条测试里 —— 上一条点开的房间，
  // 下一条再点一下就变成收起来。
  localStorage.clear()
  vi.stubGlobal('fetch', async () => ({ ok: true, json: async () => ({}), text: async () => '' }))
})

// 侧栏是一条 v-navigation-drawer，Vuetify 要求它长在一个 v-layout 里面。
const Host = {
  components: { VLayout: components.VLayout, Sidebar },
  props: ['sidebarProps'],
  template: '<v-layout><Sidebar v-bind="sidebarProps" /></v-layout>',
}

function mount(topics: Topic[]) {
  return render(Host as unknown as Component, {
    props: {
      sidebarProps: {
        page: null,
        width: 280,
        projects: [],
        selectedProjectId: 'p1',
        topics,
        selectedTopicId: null,
        loadingTopics: false,
        privateActive: false,
        members: [],
        meHandle: 'alice',
        activePeer: null,
        activeDocs: null,
        unreadMap: {},
        privateUnreadMap: {},
      },
    },
    global: { plugins: [vuetify, i18n] },
  })
}

/** 房间那一行的展开开关。侧栏默认收着——一个跑久了的房间有近两百条活，全摊在
 *  主导航上没人读得完，所以看它派出去了什么是一个动作。 */
async function openRoom(container: Element): Promise<void> {
  const toggle = container.querySelector('button.subtree-toggle') as HTMLElement | null
  if (!toggle) throw new Error('房间那一行没有展开开关')
  await fireEvent.click(toggle)
}

/** 屏幕上从上到下真的画出来的那些行。 */
function rowTitles(container: Element): string[] {
  return Array.from(container.querySelectorAll('.topic-row')).map(
    (el) => el.querySelector('.topic-title .text-truncate')?.textContent?.trim() ?? ''
  )
}

describe('侧栏画得出房间里在做什么', () => {
  it('房间默认收着，点开才看得见它派出去的活', async () => {
    const { container, queryByText, findAllByText } = mount([ROOM, thread()])
    expect(queryByText('查一下分页接口')).toBeNull()
    await openRoom(container)
    expect((await findAllByText('查一下分页接口')).length).toBeGreaterThan(0)
  })

  it('它骑的那个 PR 就写在这一行上', async () => {
    const { container, findAllByText } = mount([
      ROOM,
      thread({ card: { id: 'c1', status: 'pr_open', pr_number: 611, pr_url: 'https://x/611' } }),
    ])
    await openRoom(container)
    // 「交付」这一段在树上唯一看得见的东西：一件活现在骑在哪个 PR 上。
    expect((await findAllByText('#611')).length).toBeGreaterThan(0)
  })

  it('还没递卡的活不硬造一个交付状态出来', async () => {
    const { container, queryByText, findAllByText } = mount([ROOM, thread()])
    await openRoom(container)
    await findAllByText('查一下分页接口')
    expect(queryByText('待验收')).toBeNull()
    expect(queryByText('已采纳')).toBeNull()
  })

  it('做完的活说自己做完了，不跟在跑的长一个样', async () => {
    const { container, findAllByText } = mount([ROOM, thread({ status: 'closed' })])
    await openRoom(container)
    expect((await findAllByText('已完成')).length).toBeGreaterThan(0)
  })
})

/** 一件活没有「已归档」这个状态（只有 open/closed），所以房间归档时它自己一个字
 *  都不变。侧栏要是只按行自己的状态过滤，房间那一行进了底部的「已归档」，挂在它
 *  下面的活却全留在活跃列表里——而拍平的树是按 depth 认父子的，这些活于是被画到
 *  前面最近的那个房间下面。真实项目里这是 143 个归档房间带出来的两百多行。 */
describe('房间归档了，它派出去的活跟着走', () => {
  const ARCHIVED: Topic = {
    id: 'room-0',
    project_id: 'p1',
    parent_id: null,
    title: '去年的活',
    kind: 'topic',
    status: 'archived',
    archived_at: '2026-08-01T00:00:00Z',
    created_at: '2026-08-01T00:00:00Z',
  } as Topic

  it('归档房间里的活不留在活跃列表里', () => {
    const { container } = mount([
      ARCHIVED,
      thread({ id: 'old-1', parent_id: 'room-0', title: '去年那件活', status: 'closed' }),
      ROOM,
    ])
    expect(rowTitles(container)).toEqual(['运维'])
  })

  it('也不会被画到隔壁那个房间下面', async () => {
    // 归档房间排在活跃房间**后面**：拍平的数组里，它那些 depth 1 的活紧跟在
    // 「运维」后面，正是会被认成「运维」的下级的位置。
    const { container } = mount([
      ROOM,
      thread({ id: 'mine-1', title: '查一下分页接口' }),
      ARCHIVED,
      thread({ id: 'old-1', parent_id: 'room-0', title: '去年那件活', status: 'closed' }),
    ])
    await openRoom(container)
    expect(rowTitles(container)).toEqual(['运维', '查一下分页接口'])
  })

  it('房间根本不在这份列表里的活也不画（私聊里派出去的、房间已经不在了）', () => {
    const { container } = mount([
      ROOM,
      thread({ id: 'stray', parent_id: 'room-gone', title: '解决同步上游冲突', status: 'closed' }),
    ])
    expect(rowTitles(container)).toEqual(['运维'])
  })

  it('子话题不一样：父话题归档了它还活着，照旧留着，只是不再缩进', () => {
    const child: Topic = {
      id: 'child-1',
      project_id: 'p1',
      parent_id: 'room-0',
      title: '没做完的子话题',
      kind: 'topic',
      status: 'active',
      created_at: '2026-08-02T00:00:00Z',
    } as Topic
    const { container } = mount([ROOM, ARCHIVED, child])
    expect(rowTitles(container)).toEqual(['运维', '没做完的子话题'])
    // 父行不在了，缩进就没有参照——顶到 depth 0，而不是挂在「运维」下面。
    const row = Array.from(container.querySelectorAll('.topic-row')).find(
      (el) => el.querySelector('.topic-title .text-truncate')?.textContent?.trim() === '没做完的子话题'
    ) as HTMLElement
    expect(row.style.paddingInlineStart).toBe('8px')
    expect(row.classList.contains('is-sub')).toBe(false)
  })
})

describe('一个房间与我相关不相关，由房间自己回答', () => {
  it('它派出去的活不能代它回答', async () => {
    // 活身上没有 i_participate（那是房间的字段，见 lib/place.ts），而分组把
    // "字段缺失"当相关——照这个算，任何派过活的房间都会被自己的活顶进
    // 「我参与的」，「其他话题」这一组就名存实亡了。
    const { container } = mount([{ ...ROOM, i_participate: false } as Topic, thread()])
    expect(rowTitles(container)).toEqual([])

    const head = Array.from(container.querySelectorAll('.group-toggle')).find((el) =>
      el.textContent?.includes('其他话题')
    ) as HTMLElement | undefined
    if (!head) throw new Error('没有找到「其他话题」组头')
    // 展开这一组：房间和它派出去的活都在这一组里，一个都没丢。
    await fireEvent.click(head)
    expect(rowTitles(container)).toEqual(['运维'])
    await openRoom(container)
    expect(rowTitles(container)).toEqual(['运维', '查一下分页接口'])
  })

  it('房间与我相关时，它派出去的活跟着它一起在上面那一组', async () => {
    const { container } = mount([{ ...ROOM, i_participate: true } as Topic, thread()])
    expect(rowTitles(container)).toEqual(['运维'])
    await openRoom(container)
    expect(rowTitles(container)).toEqual(['运维', '查一下分页接口'])
  })
})
