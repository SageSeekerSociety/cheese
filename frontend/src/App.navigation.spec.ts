import type { Topic } from '@/cx_types'

import { createApp, h, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { createPinia } from 'pinia'
import { afterAll, beforeAll, expect, it, vi } from 'vitest'

import TopicHeader from './components/TopicHeader.vue'
import TopicSidebar from './components/TopicSidebar.vue'
import Home from './layouts/home/Home.vue'
import { createDialogPlugin } from './plugins/dialog'
import App from './App.vue'
import i18n from './i18n'

vi.mock('@/api', async (original) => ({
  ...(await original<typeof import('@/api')>()),
  listProjects: vi.fn(async () => ({ data: [] })),
  listProjectAgents: vi.fn(async () => ({ data: [] })),
}))
vi.mock('@/components/TopicComputePicker.vue', () => ({ default: { template: '<span />' } }))
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

const topic: Topic = {
  id: 't1',
  project_id: 'p1',
  parent_id: null,
  title: 'Synthetic room',
  kind: 'root',
  status: 'active',
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
}
// Keep the actual shell and Teleports; unrelated page content is synthetic.
const Room = {
  render: () => h(TopicHeader, { topic, members: [], me: 'tester', connected: true, focus: false }),
}
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
      { path: '/projects/p1', name: 'workspace-project', component: List, meta: { barSlot: true } },
      {
        path: '/projects/p1/topics/t1',
        name: 'workspace-topic',
        component: Room,
        meta: { barSlot: true, backTo: 'workspace-project' },
      },
      { path: '/projects/p1/agents', name: 'project-agents', component: blank },
      { path: '/projects/p1/dm/cheese', name: 'workspace-dm', component: blank },
      {
        path: '/home',
        component: Home,
        meta: { barSlot: true },
        children: [
          { path: 'spaces', name: 'HomeSpaces', component: blank },
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
    expect(document.querySelector('#app-bar-slot')?.textContent).toContain('Synthetic room')
    await app.resize(1280)
    expect(app.failures).toEqual([])
    expect(app.container.querySelector('.topic-header')?.textContent).toContain('Synthetic room')
    await app.resize(390)
    expect(app.failures).toEqual([])
    expect(document.querySelector('#app-bar-slot')?.textContent).toContain('Synthetic room')
  } finally {
    app.dispose()
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
        '/projects/p1/dm/cheese',
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

it('mounts the home tabs into the mobile bar after a breakpoint change', async () => {
  const app = await mountApp('/home/spaces', 1280)
  try {
    await app.resize(390)
    expect(app.failures).toEqual([])
    expect(document.querySelector('#app-bar-slot')?.textContent).toContain('空间')
    await app.resize(1280)
    await app.resize(390)
    expect(app.failures).toEqual([])
    expect(document.querySelector('#app-bar-slot')?.textContent).toContain('小队')
  } finally {
    app.dispose()
  }
})
