// 侧栏头：老鼠图标 + 当前项目。左边那一列方块栏拿掉后（appNav.ts），首页和项目都从这
// 一行进：项目名写全，别的项目里等你的件数汇总在旁边，点开是项目列表。
import type { Project } from '@/cx_types'
import type { NavGenericItem } from './types'

import { computed, defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { provideAppNav } from './appNav'
import ProjectSwitcher from './ProjectSwitcher.vue'

import { t } from '@/i18n'

vi.mock('@/me', () => ({ myHandle: () => 'me', myId: () => '1' }))

const vuetify = createVuetify({ components, directives })
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/teams/:handle', name: 'TeamsDetailDefault', component: { template: '<div />' } },
    { path: '/my/archived-projects', name: 'my-archived-projects', component: { template: '<div />' } },
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
    { key: 'Home', type: 'item', title: '首页', icon: 'cheese', to: '/inbox', badge: 5, shortcut: 1 },
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

async function mount(path: string, reorder = vi.fn()) {
  await router.push(path)
  await router.isReady()
  const Host = defineComponent({
    setup() {
      provideAppNav({
        items: computed(railOf),
        projects: computed(() => projects),
        reorder,
        loggedIn: computed(() => true),
      })
      return () => h(ProjectSwitcher)
    },
  })
  return render(Host, { global: { plugins: [vuetify, router] } })
}

describe('侧栏头的项目切换器', () => {
  it('写当前项目的全名和团队，别的项目里等你的件数汇总在旁边', async () => {
    const view = await mount('/projects/p1/topics/t1')
    const button = view.getByTestId('project-switcher')
    expect(button.textContent).toContain('CS 课程助教')
    expect(button.textContent).toContain('人大课程')
    // 机器学习导论 3 件 + Agent 评测 1 件；当前项目自己的不算。
    expect(button.textContent).toContain('4')
    // 首页那一格（老鼠图标）就在它左边。
    expect(view.container.querySelector('a[aria-label="首页（5）"], a[aria-label^="首页"]')).not.toBeNull()
  })

  it('在首页时写「选择项目」', async () => {
    const view = await mount('/inbox')
    expect(view.getByTestId('project-switcher').textContent).toContain(t('work.sidebar.chooseProject'))
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
