// 侧栏最上面：侧栏头写你在哪儿（项目里是这个项目，首页一带是「首页」），点开是项目列表；
// 下面一整条搜索；项目、空间、后台里再下面「首页」一行，带等你处理的总件数，由它回首页。
import type { Project } from '@/cx_types'
import type { NavGenericItem } from './types'

import { computed, defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { provideAppNav } from './appNav'
import SidebarFrameTop from './SidebarFrameTop.vue'

import { t } from '@/i18n'

vi.mock('@/me', () => ({ myHandle: () => 'me', myId: () => '1' }))

const vuetify = createVuetify({ components, directives })
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/teams/:handle', name: 'TeamsDetailDefault', component: { template: '<div />' } },
    { path: '/my/archived-projects', name: 'my-archived-projects', component: { template: '<div />' } },
    { path: '/inbox', component: { template: '<div />' } },
    { path: '/:rest(.*)', component: { template: '<div />' } },
  ],
})

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: () => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} }),
  })
  if (!('visualViewport' in globalThis)) {
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
  if (!('devicePixelRatio' in globalThis)) {
    ;(globalThis as unknown as { devicePixelRatio: number }).devicePixelRatio = 1
  }
  if (!document.elementFromPoint) document.elementFromPoint = () => null
})

afterEach(cleanup)

const projects = [
  { id: 'p1', name: 'CS 课程助教', team_handle: 'ruc', team_name: '人大课程', owner_handle: 'teacher' },
  { id: 'p2', name: '机器学习导论', team_handle: 'ruc', team_name: '人大课程', owner_handle: 'teacher' },
  { id: 'p3', name: 'Agent 评测', team_handle: 'me', owner_handle: 'me' },
] as unknown as Project[]

function railOf(): NavGenericItem[] {
  return [
    {
      key: 'Home',
      type: 'item',
      title: '首页',
      icon: 'cheese',
      to: '/inbox',
      match: (path: string) => path === '/inbox' || path.startsWith('/teams'),
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

async function mount(path: string, reorder = vi.fn(), props: { homeArea?: boolean } = {}) {
  await router.push(path)
  await router.isReady()
  const Host = defineComponent({
    setup() {
      provideAppNav({
        items: computed(railOf),
        projects: computed(() => projects),
        reorder,
        loggedIn: computed(() => true),
        frame: { top: SidebarFrameTop, foot: SidebarFrameTop },
        foldSidebar: computed(() => null),
      })
      return () => h(SidebarFrameTop, props)
    },
  })
  return render(Host, { global: { plugins: [vuetify, router] } })
}

describe('侧栏头写你在哪儿', () => {
  it('项目里写当前项目的全名和团队，别的项目里等你的件数汇总在旁边', async () => {
    const view = await mount('/projects/p1/topics/t1')
    const button = view.getByTestId('project-switcher')
    expect(button.textContent).toContain('CS 课程助教')
    expect(button.textContent).toContain('人大课程')
    // 机器学习导论 3 件 + Agent 评测 1 件；当前项目自己的不算。
    expect(button.textContent).toContain('4')
    // 侧栏头里只有你在哪儿，回首页的链接不在头里。
    expect(button.querySelector('a[href="/inbox"]')).toBeNull()
  })

  it('首页一带写「首页」，别处写「选择项目」', async () => {
    const home = await mount('/teams/ruc')
    expect(home.getByTestId('project-switcher').textContent?.trim()).toBe('首页')
    cleanup()
    const elsewhere = await mount('/admin')
    expect(elsewhere.getByTestId('project-switcher').textContent).toContain(t('work.sidebar.chooseProject'))
  })

  it('点开是项目列表，能按名字筛', async () => {
    const view = await mount('/projects/p1')
    await fireEvent.click(view.getByTestId('project-switcher'))
    const rows = await screen.findAllByTestId('project-switcher-row')
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining('CS 课程助教'),
      expect.stringContaining('机器学习导论'),
      expect.stringContaining('Agent 评测'),
    ])
    // 当前项目那一行是选中态。
    expect(rows[0].getAttribute('aria-current')).toBe('page')
    await fireEvent.update(screen.getByTestId('project-switcher-search'), '机器')
    const filtered = await screen.findAllByTestId('project-switcher-row')
    expect(filtered).toHaveLength(1)
    expect(filtered[0].textContent).toContain('机器学习导论')
  })
})

describe('「首页」那一行', () => {
  // 项目、空间、后台的侧栏头写的是你在哪儿，回首页要一下就够得着，等你处理的总件数也得一直看得见。
  it('项目里：一下回首页，带等你处理的总件数', async () => {
    const view = await mount('/projects/p1/topics/t1')
    const row = within(view.container as HTMLElement).getByRole('link', { name: /^首页/ })
    expect(row.getAttribute('href')).toBe('/inbox')
    expect(row.textContent).toContain('5')
    await fireEvent.click(row)
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe('/inbox'))
  })

  it('空间、后台的侧栏也有', async () => {
    for (const path of ['/spaces/s1/announcements', '/admin']) {
      const view = await mount(path)
      expect(
        within(view.container as HTMLElement)
          .getByRole('link', { name: /^首页/ })
          .getAttribute('href'),
        path
      ).toBe('/inbox')
      cleanup()
    }
  })

  it('首页那一格的侧栏没有这一行：侧栏头已经写着「首页」', async () => {
    const view = await mount('/teams/ruc', vi.fn(), { homeArea: true })
    expect(view.getByTestId('project-switcher').textContent?.trim()).toBe('首页')
    expect(view.container.querySelector('a[href="/inbox"]')).toBeNull()
  })
})
