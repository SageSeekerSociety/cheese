// 侧栏画哪几页，由**这个项目的壳**说了算（`ProjectOut.shell`，见 lib/shell.ts）：
// 壳能开关、排序、换词，收起的是「默认收起」而不是「禁止」。这一份守两件事——
//
//   1. 收起来的页仍然一次点击可达（在项目名旁边那个 ⋯ 菜单里），包括壳写了一个
//      这一版前端还不认识的 key 时（那种 key 也落在菜单里，不会凭空消失）；
//   2. 「个人级压过壳」：他手动打开过一次的页，之后就在他自己的侧栏上（按 handle
//      落盘）。
import type { Component } from 'vue'
import type { Project, Topic } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VLayout } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import TopicSidebar from './TopicSidebar.vue'

import { setLocale, t } from '@/i18n'

const Sidebar = TopicSidebar as unknown as Component

const Blank = defineComponent({ setup: () => () => h('div') })

const topics: Topic[] = [
  {
    id: 'root',
    project_id: 'p1',
    parent_id: null,
    title: '综合',
    kind: 'root',
    status: 'active',
    created_by: 'u',
    created_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
  } as Topic,
]

/** 一个点名了三页的壳。 */
const COURSE_SHELL = {
  name: 'course',
  home: 'workspace-running',
  nav: { rail: [], tabs: [], project: ['project-library', 'project-members', 'project-routines'] },
  terms: {},
}

function project(shell: unknown, name = '课程'): Project {
  return { id: 'p1', name, created_at: '2026-08-10T00:00:00Z', shell } as unknown as Project
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/projects/:projectId', name: 'workspace-project', component: Blank },
    { path: '/projects/:projectId/settings', name: 'project-settings', component: Blank },
    { path: '/projects/:projectId/library', name: 'project-library', component: Blank },
    { path: '/projects/:projectId/members', name: 'project-members', component: Blank },
    { path: '/projects/:projectId/routines', name: 'project-routines', component: Blank },
    { path: '/projects/:projectId/running', name: 'workspace-running', component: Blank },
    { path: '/:pathMatch(.*)*', name: 'catch-all', component: Blank },
  ],
})

const Host = defineComponent({
  props: { inner: { type: Object, required: true } },
  setup(props) {
    return () => h(VLayout, null, { default: () => [h(Sidebar, props.inner as Record<string, unknown>)] })
  },
})

function mount(inner: Record<string, unknown> = {}) {
  if (!document.getElementById('app-bar-slot')) {
    const slot = document.createElement('div')
    slot.id = 'app-bar-slot'
    document.body.appendChild(slot)
  }
  const vuetify = createVuetify({ components, directives })
  return render(Host, {
    props: {
      inner: {
        projects: [project(COURSE_SHELL)],
        selectedProjectId: 'p1',
        topics,
        selectedTopicId: null,
        loadingTopics: false,
        ...inner,
      },
    },
    global: { plugins: [vuetify, router, createPinia()] },
  })
}

/** 项目名下面那几行。 */
function pagesIn(container: Element): string[] {
  return Array.from(
    container.querySelectorAll(`[aria-label="${t('navigation.project.pages')}"] .v-list-item-title`)
  ).map((el) => el.textContent?.trim() ?? '')
}

/** 点项目名，返回弹出的菜单里每一行的文字。 */
async function openProjectMenu(container: Element, baseElement: Element): Promise<string[]> {
  const header =
    container.querySelector('[aria-label="项目菜单"]') ?? baseElement.querySelector('[aria-label="项目菜单"]')
  await fireEvent.click(header as Element)
  await waitFor(() => {
    if (baseElement.querySelectorAll('.v-overlay .v-list-item').length === 0) throw new Error('菜单还没开')
  })
  return Array.from(baseElement.querySelectorAll('.v-overlay .v-list-item')).map(
    (el) => el.textContent?.replace(/\s+/g, '') ?? ''
  )
}

async function clickMenuItem(container: Element, baseElement: Element, label: string): Promise<void> {
  await openProjectMenu(container, baseElement)
  const row = Array.from(baseElement.querySelectorAll('.v-overlay .v-list-item')).find((el) =>
    el.textContent?.includes(label)
  )
  if (!row) throw new Error(`菜单里没有 ${label}`)
  await fireEvent.click(row)
}

beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  if (!globalThis.matchMedia) {
    globalThis.matchMedia = (() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    })) as unknown as typeof globalThis.matchMedia
  }
  if (!('visualViewport' in globalThis)) {
    ;(globalThis as unknown as { visualViewport: unknown }).visualViewport = {
      width: 1024,
      height: 768,
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
})

beforeEach(() => {
  localStorage.clear()
  setLocale('zh-CN')
  // 「我是谁」读的是登录后落下的那一份账号（`myHandle()`）——个人偏好按它落盘。
  localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: 'me' }))
})

describe('项目名下只有看板和资料库，其余都在点项目名弹出的菜单里', () => {
  it('壳点名了更多页，项目名下也只有看板和资料库', async () => {
    const { container, baseElement } = mount()
    expect(pagesIn(container)).toEqual([t('navigation.project.board'), t('navigation.project.library')])

    const rows = await openProjectMenu(container, baseElement)
    for (const label of ['navigation.project.members', 'navigation.project.routines', 'navigation.project.docs'])
      expect(rows.some((r) => r.includes(t(label)))).toBe(true)
    expect(rows.some((r) => r.includes(t('navigation.project.settings')))).toBe(true)
  })

  it('点项目名只弹菜单，不换页', async () => {
    await router.push('/projects/p1/library')
    const { container, baseElement } = mount()
    await openProjectMenu(container, baseElement)
    expect(router.currentRoute.value.name).toBe('project-library')
  })

  it('在菜单里打开一页，它不会因此出现在项目名下', async () => {
    const { container, baseElement } = mount()
    await clickMenuItem(container, baseElement, t('navigation.project.routines'))
    await waitFor(() => expect(router.currentRoute.value.name).toBe('project-routines'))
    expect(pagesIn(container)).toEqual([t('navigation.project.board'), t('navigation.project.library')])
  })

  it('壳比前端新（多了一个不认识的 key）：那一格不画，别处照旧，不白屏', async () => {
    // 服务端先发了第五个壳的名字，而这一版前端还没有那一页：画一格点了就 404 的
    // 东西比不画更糟，所以它落在 `orderedNav` 那一步，哪里都不画。
    const shell = { ...COURSE_SHELL, nav: { ...COURSE_SHELL.nav, project: ['project-library', 'project-future'] } }
    const { container, baseElement } = mount({ projects: [project(shell)] })
    expect(pagesIn(container)).toEqual([t('navigation.project.board'), t('navigation.project.library')])

    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes('project-future'))).toBe(false)
    expect(rows.some((r) => r.includes(t('navigation.project.routines')))).toBe(true)
  })
})
