// 桌面顶栏（`AppBar`）：首页、你所在的去处、侧栏开关、搜索、帮助、头像都在这一条上。
//
// 钉的是几条产品规则：「首页」一下回待办、带等你处理的总件数；在项目或空间里，首页后面写
// 着这个去处，点开能换到别的项目和空间；不在去处里（首页一带、后台）那里什么也不写；有侧栏
// 的页面最左边一颗开关收起、放出侧栏；没登录的人语言开关在顶栏上，登录之后它在「我」的菜单
// 里。顶栏在 `src/components/**` 下，登录态和导航数据只能听外壳给的（appNav），所以这里给它的
// 就是外壳会给的那几样。
import type { Project } from '@/cx_types'
import type { NavSidebar, NavSpace } from './appNav'
import type { NavGenericItem } from './types'

import { computed, defineComponent, h, ref } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import AppBar from './AppBar.vue'
import { provideAppNav } from './appNav'

import i18n, { setLocale, t } from '@/i18n'
import { closeFirstPaint } from '@/test/firstPaint'

setLocale('zh-CN')

vi.mock('@/me', () => ({ myHandle: () => 'me', myId: () => '1' }))

// 顶栏里的帮助入口会去问未读数和自己的管理员身份；这两个回答跟这一组用例无关。
vi.mock('@/api', async (original) => ({
  ...(await original<typeof import('@/api')>()),
  getFeedbackCounts: vi.fn(async () => ({ all: 0, hot: 0, resolved: 0, unread: 0 })),
  getFeedbackMeta: vi.fn(async () => ({ is_admin: false, is_platform_admin: false, tabs: [], kinds: [] })),
}))

const blank = { template: '<div />' }
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/teams/:handle', name: 'TeamsDetailDefault', component: blank },
    { path: '/my/archived-projects', name: 'my-archived-projects', component: blank },
    { path: '/spaces/:spaceId/tasks', name: 'SpacesDetailTasksList', component: blank },
    { path: '/:rest(.*)', component: blank },
  ],
})

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!('visualViewport' in globalThis) || !globalThis.visualViewport) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1280,
      height: 800,
      offsetLeft: 0,
      offsetTop: 0,
      scale: 1,
      addEventListener() {},
      removeEventListener() {},
    }
  }
  if (!document.elementFromPoint) document.elementFromPoint = () => null
})

afterEach(cleanup)

const projects = [
  { id: 'p1', name: 'CS 课程助教', team_handle: 'ruc', team_name: '人大课程', owner_handle: 'teacher' },
  { id: 'p2', name: '机器学习导论', team_handle: 'ruc', team_name: '人大课程', owner_handle: 'teacher' },
  { id: 'p3', name: 'Agent 评测', team_handle: 'me', owner_handle: 'me' },
] as unknown as Project[]

const spaces: NavSpace[] = [
  { id: 7, title: 'ICS 2026', avatar: '', to: { name: 'SpacesDetailTasksList', params: { spaceId: 7 } } },
]

function railOf(): NavGenericItem[] {
  return [
    {
      key: 'Home',
      type: 'item',
      title: '首页',
      icon: 'cheese',
      to: '/inbox',
      match: (path: string) => path === '/inbox' || path.startsWith('/teams') || path.startsWith('/spaces'),
      badge: 5,
      shortcut: 1,
    },
    { key: 'cx-divider', type: 'divider' },
    ...projects.map((p, i) => ({
      key: `cx-${p.id}`,
      type: 'item' as const,
      title: p.name,
      projectId: p.id,
      to: `/projects/${p.id}`,
      match: (path: string) => path.startsWith(`/projects/${p.id}`),
      badge: [0, 3, 1][i],
      shortcut: i + 2,
    })),
    { key: 'cx-add', type: 'item', title: '新建项目', icon: 'mdi-plus', add: true, action: () => {} },
  ]
}

interface MountOptions {
  loggedIn?: boolean
  sidebar?: NavSidebar | null
  inSpace?: boolean
}

async function mount(path: string, { loggedIn = true, sidebar = null, inSpace = false }: MountOptions = {}) {
  // 首帧那道门关着：这一组用例看的是顶栏画什么，不需要它去问任何东西。
  closeFirstPaint()
  await router.push(path)
  await router.isReady()
  const Host = defineComponent({
    setup() {
      provideAppNav({
        items: computed(railOf),
        projects: computed(() => projects),
        spaces: computed(() => spaces),
        currentSpace: computed(() => (inSpace ? spaces[0] : null)),
        reorder: vi.fn(),
        loggedIn: computed(() => loggedIn),
        sidebar: computed(() => sidebar),
      })
      // 挂在 `v-layout` 里：顶栏是一条 `v-system-bar`，它要问注入进来的布局算自己的高度。
      return () => h(components.VLayout, null, () => h(AppBar, { loggedIn }))
    },
  })
  return render(Host, {
    global: { plugins: [createVuetify({ components, directives }), router, createPinia(), i18n] },
  })
}

describe('顶栏的「首页」', () => {
  it('一下回待办，带等你处理的总件数；启动画面落在它这一处', async () => {
    const view = await mount('/projects/p1/topics/t1')
    const home = within(view.container as HTMLElement).getByRole('link', { name: /^首页/ })
    expect(home.getAttribute('href')).toBe('/inbox')
    expect(home.textContent).toContain('5')
    expect(view.container.querySelectorAll('[data-home-slot]')).toHaveLength(1)
    await fireEvent.click(home)
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/inbox'))
  })
})

describe('顶栏上你所在的去处', () => {
  it('项目里写这个项目，点开是项目和空间的列表，能按名字筛', async () => {
    const view = await mount('/projects/p1/topics/t1')
    const place = view.getByTestId('project-switcher')
    expect(place.textContent).toContain('CS 课程助教')
    await fireEvent.click(place)
    const rows = await screen.findAllByTestId('project-switcher-row')
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('CS 课程助教'),
      expect.stringContaining('机器学习导论'),
      expect.stringContaining('Agent 评测'),
    ])
    expect(rows[0].getAttribute('aria-current')).toBe('page')
    expect(screen.getAllByTestId('space-switcher-row').map((r) => r.textContent)).toEqual([
      expect.stringContaining('ICS 2026'),
    ])
    expect(screen.getByText('新建项目')).toBeTruthy()

    await fireEvent.update(screen.getByTestId('project-switcher-search'), '机器')
    const filtered = await screen.findAllByTestId('project-switcher-row')
    expect(filtered).toHaveLength(1)
    expect(filtered[0].textContent).toContain('机器学习导论')
    expect(screen.queryAllByTestId('space-switcher-row')).toHaveLength(0)
  })

  it('空间里写这个空间', async () => {
    const view = await mount('/spaces/7/tasks', { inSpace: true })
    expect(view.getByTestId('project-switcher').textContent).toContain('ICS 2026')
  })

  it('不在项目或空间里时，首页后面什么也不写', async () => {
    for (const path of ['/inbox', '/admin/queue']) {
      const view = await mount(path)
      expect(view.queryByTestId('project-switcher'), path).toBeNull()
      cleanup()
    }
  })
})

describe('顶栏最左那颗侧栏开关', () => {
  it('有侧栏的页面才有；按下去收起侧栏，再按放出来，读屏听得到现在的状态', async () => {
    const shown = ref(true)
    const sidebar = {
      get shown() {
        return shown.value
      },
      toggle: () => (shown.value = !shown.value),
    }
    const view = await mount('/projects/p1/topics/t1', { sidebar })
    const toggle = view.getByRole('button', { name: t('navigation.sidebar.collapse') })
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    await fireEvent.click(toggle)
    expect(shown.value).toBe(false)
    cleanup()

    const none = await mount('/settings')
    expect(none.queryByRole('button', { name: t('navigation.sidebar.collapse') })).toBeNull()
    expect(none.queryByRole('button', { name: t('navigation.sidebar.expand') })).toBeNull()
  })
})

describe('顶栏右边', () => {
  it('没登录的人语言开关留在顶栏上', async () => {
    const { container } = await mount('/inbox', { loggedIn: false })
    expect(container.querySelector('.language-toggle')).not.toBeNull()
  })

  it('登录之后顶栏不再画语言开关：它在「我」的菜单里', async () => {
    const { container } = await mount('/inbox')
    expect(container.querySelector('.language-toggle')).toBeNull()
  })
})
