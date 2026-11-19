import type { Topic } from '@/cx_types'

import { createApp, defineComponent, h, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify, useDisplay } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { createPinia } from 'pinia'
import { afterAll, beforeAll, beforeEach, expect, it, vi } from 'vitest'

import TopicHeader from './components/TopicHeader.vue'
import TopicSidebar from './components/TopicSidebar.vue'
import { useScreenHeader } from './composables/useScreenHeader'
import Home from './layouts/home/Home.vue'
import { createDialogPlugin } from './plugins/dialog'
import App from './App.vue'
import i18n, { setLocale } from './i18n'

vi.mock('@/api', async (original) => ({
  ...(await original<typeof import('@/api')>()),
  getAppVersion: vi.fn(async () => ({ sha: '', short: '', badge: false })),
  getFeedbackCounts: vi.fn(async () => ({ all: 0, hot: 0, resolved: 0, unread: 0 })),
  getFeedbackMeta: vi.fn(async () => ({ is_admin: false, hot_min_items: 5 })),
  listProjects: vi.fn(async () => ({ data: [] })),
  listProjectAgents: vi.fn(async () => ({ data: [] })),
}))
vi.mock('@/components/TopicMembers.vue', () => ({ default: { template: '<span />' } }))
vi.mock('@/components/common/VersionBadge.vue', () => ({ default: { template: '<span />' } }))

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
})
afterAll(() => vi.unstubAllGlobals())
beforeEach(() => setLocale('zh-CN'))

const topic: Topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: 'Synthetic room',
  kind: 'topic',
  status: 'active',
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
}
// Keep the actual shell and the room header; unrelated page content is synthetic. Like
// TopicView, the header sits in the page on desktop and is the phone's top bar row.
const Room = defineComponent({
  setup() {
    const { mdAndUp } = useDisplay()
    const header = { topic, members: [], me: 'tester', connected: true }
    useScreenHeader(() => (mdAndUp.value ? null : { content: { component: TopicHeader, props: header } }))
    return () => (mdAndUp.value ? h(TopicHeader, header) : null)
  },
})
const List = {
  render: () =>
    h(TopicSidebar, {
      page: true,
      projects: [{ id: 'p1', name: 'Synthetic project', created_at: topic.created_at }],
      selectedProjectId: 'p1',
      topics: [topic],
      selectedTopicId: null,
      loadingTopics: false,
      privateActive: false,
      members: [],
      meHandle: 'tester',
    }),
}
const blank = { template: '<div>Other page</div>' }

async function settle() {
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await nextTick()
}

async function mountApp(path: string, width: number) {
  Object.defineProperty(window, 'innerWidth', { value: width, writable: true, configurable: true })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: blank },
      { path: '/spaces', component: blank },
      { path: '/inbox', component: blank },
      { path: '/projects/p1', name: 'workspace-project', component: List },
      {
        path: '/projects/p1/topics/t1',
        name: 'workspace-topic',
        component: Room,
        meta: { backTo: 'workspace-project' },
      },
      { path: '/projects/p1/dm/agent:cheese', name: 'workspace-dm', component: blank },
      {
        path: '/home',
        component: Home,
        children: [
          { path: 'space-applications/:spaceId', name: 'HomeSpaceApplication', component: blank },
          { path: 'teams', name: 'HomeTeams', component: blank },
        ],
      },
      { path: '/account/signin', name: 'signin', component: blank, meta: { hideAppBar: true } },
    ],
  })
  const vuetify = createVuetify({ components, directives })
  const container = document.createElement('div')
  document.body.append(container)
  const failures: string[] = []
  const app = createApp(App)
  app.config.errorHandler = (error) => {
    failures.push(String(error))
  }
  app.use(vuetify).use(createPinia()).use(router).use(i18n).use(createDialogPlugin)
  // Match main.ts: mount before the first route finishes resolving.
  app.mount(container)
  await router.push(path)
  await settle()
  return {
    router,
    container,
    failures,
    resize: async (next: number) => {
      window.innerWidth = next
      vuetify.display.update()
      await settle()
    },
    dispose: () => {
      app.unmount()
      container.remove()
    },
  }
}

it('keeps the room header usable when the app changes between desktop and mobile', async () => {
  const app = await mountApp('/projects/p1/topics/t1', 1280)
  try {
    expect(app.failures).toEqual([])
    await app.resize(390)
    expect(app.failures).toEqual([])
    expect(document.querySelector('.v-app-bar')?.textContent).toContain('Synthetic room')
    await app.resize(1280)
    expect(app.failures).toEqual([])
    expect(app.container.querySelector('.topic-header')?.textContent).toContain('Synthetic room')
    await app.resize(390)
    expect(app.failures).toEqual([])
    expect(document.querySelector('.v-app-bar')?.textContent).toContain('Synthetic room')
  } finally {
    app.dispose()
  }
})

it('外壳里没有去 /inbox 的那一行「首页」：侧栏头和顶栏都没有', async () => {
  // 2026-11-19 蔡松洋定：侧栏头下面那一行「首页」四处侧栏一起删，顶栏那一处也去掉。
  // 钉住「删干净了」，免得它从别处又长回来。回首页走项目切换器菜单里那一行。
  // 侧栏头上剩下的两样（头、搜索）由 SidebarFrameTop.spec.ts 钉。
  for (const path of ['/projects/p1/topics/t1', '/projects/p1']) {
    const app = await mountApp(path, 1280)
    try {
      expect(app.failures).toEqual([])
      expect(app.container.querySelector('[data-testid="sidebar-home"]')).toBeNull()
      expect(app.container.querySelector('[data-testid="bar-home"]')).toBeNull()
      const toInbox = Array.from(app.container.querySelectorAll('a[href="/inbox"]'))
      expect(toInbox, path).toHaveLength(0)
    } finally {
      app.dispose()
    }
  }
})

it.each(['/projects/p1', '/projects/p1/topics/t1'])(
  'keeps initial mobile entry at %s and normal navigation working',
  async (path) => {
    const app = await mountApp(path, 390)
    try {
      expect(app.failures).toEqual([])
      for (const path of [
        '/projects/p1/agents',
        '/projects/p1/dm/agent:cheese',
        '/projects/p1',
        '/projects/p1/topics/t1',
        '/home/spaces',
        '/home/teams',
      ]) {
        await app.router.push(path)
        await settle()
        expect(app.failures).toEqual([])
      }
    } finally {
      app.dispose()
    }
  }
)
