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
import { render } from '@testing-library/vue'
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

describe('侧栏画得出房间里在做什么', () => {
  it('一件活在树上有自己的一行', async () => {
    const { findAllByText } = mount([ROOM, thread()])
    expect((await findAllByText('查一下分页接口')).length).toBeGreaterThan(0)
  })

  it('它骑的那个 PR 就写在这一行上', async () => {
    const { findAllByText } = mount([
      ROOM,
      thread({ card: { id: 'c1', status: 'pr_open', pr_number: 611, pr_url: 'https://x/611' } }),
    ])
    // 「交付」这一段在树上唯一看得见的东西：一件活现在骑在哪个 PR 上。
    expect((await findAllByText('#611')).length).toBeGreaterThan(0)
  })

  it('还没递卡的活不硬造一个交付状态出来', async () => {
    const { queryByText, findAllByText } = mount([ROOM, thread()])
    await findAllByText('查一下分页接口')
    expect(queryByText('待验收')).toBeNull()
    expect(queryByText('已采纳')).toBeNull()
  })

  it('做完的活说自己做完了，不跟在跑的长一个样', async () => {
    const { findAllByText } = mount([ROOM, thread({ status: 'closed' })])
    expect((await findAllByText('已完成')).length).toBeGreaterThan(0)
  })
})
