// 门口页面（官网、方案、关于）不在应用外框里：App.vue 在两者之间选。外框在两者之间来回
// 时卸下、重挂（layouts/AppShell.vue），它登记的东西不能来回一趟就多一份。
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, expect, it, vi } from 'vitest'

import { createDialogPlugin } from './plugins/dialog'
import App from './App.vue'
import i18n from './i18n'

import { activeCommands } from '@/commands'

vi.mock('@/api', async (original) => ({
  ...(await original<typeof import('@/api')>()),
  getAppVersion: vi.fn(async () => ({ sha: '', short: '', badge: false })),
  getFeedbackCounts: vi.fn(async () => ({ all: 0, hot: 0, resolved: 0, unread: 0 })),
  getFeedbackMeta: vi.fn(async () => ({ is_admin: false, hot_min_items: 5 })),
  listProjects: vi.fn(async () => ({ data: [] })),
}))
vi.mock('@/components/common/VersionBadge.vue', () => ({ default: { template: '<span />' } }))
// 外框一挂上就去取这两个（异步组件），用例比它们取完结束得早，换成空壳。
vi.mock('@/commands/palette/CommandPalette.vue', () => ({ __esModule: true, default: { template: '<span />' } }))
vi.mock('@/views/desktop/DeviceConnect.vue', () => ({ __esModule: true, default: { template: '<span />' } }))

beforeAll(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  Object.defineProperty(window, 'innerWidth', { value: 1280, writable: true, configurable: true })
})

const page = (name: string, body: string) => defineComponent({ name, setup: () => () => h('div', body) })

it('going out to a public page and back leaves the app frame as it was, not doubled', async () => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/about', component: page('AboutView', '了解知是'), meta: { publicLanding: true } },
      { path: '/inbox', component: page('InboxView', '待办内容') },
      { path: '/', component: page('HomeView', '首页内容') },
      { path: '/spaces', component: page('SpacesView', '空间内容') },
    ],
  })
  render(App, {
    global: {
      plugins: [createVuetify({ components, directives }), createPinia(), router, i18n, createDialogPlugin],
    },
  })
  const newProject = () => activeCommands.value.filter((command) => command.id === 'project.new').length

  await router.push('/inbox')
  await waitFor(() => expect(screen.getByText('待办内容')).toBeTruthy())
  expect(newProject()).toBe(1)

  await router.push('/about')
  await waitFor(() => expect(screen.getByText('了解知是')).toBeTruthy())
  // A public page is a document of its own: no app frame around it.
  expect(document.querySelector('#main-content')).toBeNull()

  await router.push('/inbox')
  await waitFor(() => expect(screen.getByText('待办内容')).toBeTruthy())
  expect(newProject()).toBe(1)
})
