// 手机上换项目的唯一入口：整页形态的项目头菜单。
//
// 一个项目一格的那条竖 rail 只在桌面渲染，手机底栏「工作区」那一格只落到一个
// 项目——所以这个菜单要是不列项目，手机上进了一个项目就再也走不到别的项目去。
// 这一份守的就是「列出来了、点了真的换过去了」，以及桌面上它不重复出现。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VLayout } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import TopicSidebar from './TopicSidebar.vue'

import { setLocale, t } from '@/i18n'

// 断言按中文写；测试环境默认是英文界面。下面几处标签在模块顶层就取了词，
// 所以语言要在 import 之前定下来。
vi.hoisted(() => localStorage.setItem('cheese:locale', 'zh-CN'))
beforeEach(() => setLocale('zh-CN'))

const Sidebar = TopicSidebar as unknown as Component

const topics: Topic[] = [
  {
    id: 'root',
    project_id: 'p1',
    parent_id: null,
    title: '全局',
    kind: 'root',
    status: 'active',
    created_by: 'u',
    created_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
  } as Topic,
]

const projects = [
  { id: 'p1', name: '知是', created_at: '2026-08-10T00:00:00Z' },
  { id: 'p2', name: '芝士', created_at: '2026-08-10T00:00:00Z' },
  { id: 'p3', name: '推荐算法', created_at: '2026-08-10T00:00:00Z' },
]

const Blank = defineComponent({ setup: () => () => h('div') })

function makeRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/projects/:projectId', name: 'workspace-project', component: Blank },
      { path: '/projects/:projectId/settings', name: 'project-settings', component: Blank },
      { path: '/projects/:projectId/library', name: 'project-library', component: Blank },
      { path: '/projects/:projectId/members', name: 'project-members', component: Blank },
      { path: '/:pathMatch(.*)*', name: 'catch-all', component: Blank },
    ],
  })
}

const Host = defineComponent({
  props: { inner: { type: Object, required: true } },
  setup(props) {
    return () => h(VLayout, null, { default: () => [h(Sidebar, props.inner as Record<string, unknown>)] })
  },
})

function mount(inner: Record<string, unknown> = {}) {
  // 整页形态下项目头 Teleport 进顶栏那一格（真实环境里由 MobileAppBar 画）。
  if (!document.getElementById('app-bar-slot')) {
    const slot = document.createElement('div')
    slot.id = 'app-bar-slot'
    document.body.appendChild(slot)
  }
  const vuetify = createVuetify({ components, directives })
  const router = makeRouter()
  const utils = render(Host, {
    props: {
      inner: {
        projects,
        selectedProjectId: 'p1',
        topics,
        selectedTopicId: null,
        loadingTopics: false,
        privateActive: false,
        members: [],
        meHandle: 'me',
        ...inner,
      },
    },
    global: { plugins: [vuetify, router, createPinia()] },
  })
  return { ...utils, router }
}

/** 打开项目头那个菜单，返回菜单里的行文字。 */
async function openProjectMenu(container: Element, baseElement: Element): Promise<string[]> {
  const header =
    container.querySelector('[aria-label="项目菜单"]') ?? baseElement.querySelector('[aria-label="项目菜单"]')
  await fireEvent.click(header as Element)
  return menuRows(baseElement).map((el) => el.textContent?.replace(/\s+/g, '') ?? '')
}

/** 菜单里能点的每一行：桌面是下拉菜单的行，手机是底部面板里的按钮。 */
function menuRows(baseElement: Element): Element[] {
  return Array.from(baseElement.querySelectorAll('.v-overlay .v-list-item, .v-overlay button'))
}

beforeAll(() => {
  // happy-dom 缺件，同 TopicSidebar.rail.spec.ts：不补的话 overlay 定位直接炸。
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
      width: 390,
      height: 780,
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

describe('手机上的切换项目', () => {
  it('整页形态的项目头菜单里列出我的每一个项目', async () => {
    const { container, baseElement } = mount({ page: true })
    const rows = await openProjectMenu(container, baseElement)
    for (const p of projects) expect(rows.some((r) => r.includes(p.name))).toBe(true)
  })

  it('点一个项目就换到那个项目的地址上去', async () => {
    const { container, baseElement, router } = mount({ page: true })
    await openProjectMenu(container, baseElement)
    const target = menuRows(baseElement).find((el) => el.textContent?.includes('推荐算法'))
    await fireEvent.click(target as Element)
    await router.isReady()
    expect(router.currentRoute.value.fullPath).toBe('/projects/p3')
  })

  it('桌面形态不列项目：那条竖 rail 已经是那个入口，同一件事不要两个入口', async () => {
    const { container, baseElement } = mount({ page: false })
    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes('推荐算法'))).toBe(false)
    expect(rows.some((r) => r.includes('项目设置'))).toBe(true)
  })

  it('只有一个项目时不列：没有可换的地方', async () => {
    const { container, baseElement } = mount({ page: true, projects: [projects[0]] })
    const rows = await openProjectMenu(container, baseElement)
    // 菜单里仍然有那几页（定时与触发、成员、项目设置），但没有一行是项目 —— 没得换。
    expect(rows).not.toContain('P1')
    expect(rows).toContain('项目设置')
  })
})

// 「转让项目」现在也在这个菜单里（成员页那颗按钮保留）。谁能转，项目行自己说得出：
// 所有者本人，或者管得了成员的团队管理员。判据读不出来的人（比如只是团队成员）
// 不该看到这一行 —— 递到退不掉/转不动的人手里，就是把人骗进一个会被拒的弹窗。
describe('项目菜单里的「转让项目」', () => {
  const current = (extra: Record<string, unknown>) => [{ ...projects[0], ...extra }, ...projects.slice(1)]
  // 菜单行按当前语言渲染，别把中文写进断言里（测试环境的语言跟着 navigator 走）；
  // `openProjectMenu` 把行文字里的空白都压掉了，比对的那一份也要一样压。
  const label = t('work.members.transfer').replace(/\s+/g, '')

  // 「我是谁」读的是登录后落下的那一份账号（`myHandle()`），照 DmView.spec.ts 那样种上。
  beforeEach(() => {
    localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: 'me' }))
  })

  it('我自己的项目：看得到', async () => {
    const { container, baseElement } = mount({ page: false, projects: current({ owner_handle: 'me' }) })
    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes(label))).toBe(true)
  })

  it('不是我、也管不了成员：看不到', async () => {
    const { container, baseElement } = mount({ page: false, projects })
    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes(label))).toBe(false)
  })

  it('管得了成员的团队管理员：看得到', async () => {
    const { container, baseElement } = mount({
      page: false,
      projects: current({ owner_handle: 'someone-else', can_manage_members: true }),
    })
    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes(label))).toBe(true)
  })
})

// 另一半：「退出项目」也在这个菜单里（成员页那颗按钮保留）。判据只有「我不是所有
// 者」——所有者看到的上一条就是它的替代，他退不掉，只能先把项目交出去；其余的人
// 都能退，退的是这个项目的成员身份，不是小队。
describe('项目菜单里的「退出项目」', () => {
  const current = (extra: Record<string, unknown>) => [{ ...projects[0], ...extra }, ...projects.slice(1)]
  const leave = t('work.members.leave').replace(/\s+/g, '')
  const transfer = t('work.members.transfer').replace(/\s+/g, '')

  beforeEach(() => {
    localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: 'me' }))
  })

  it('不是我自己的项目：看得到', async () => {
    const { container, baseElement } = mount({ page: false, projects: current({ owner_handle: 'alice' }) })
    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes(leave))).toBe(true)
    expect(rows.some((r) => r.includes(transfer))).toBe(false)
  })

  it('我自己的项目：看不到——换给我是「转让项目」，不是「退出项目」', async () => {
    const { container, baseElement } = mount({ page: false, projects: current({ owner_handle: 'me' }) })
    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes(leave))).toBe(false)
    expect(rows.some((r) => r.includes(transfer))).toBe(true)
  })
})

// 手机上话题列表只留「全局」和话题：资料库、成员、项目文档收进项目菜单。它们得在那里
// 点得到，「有人找你」的私聊未读也得跟着「成员」进去。
describe('手机上项目的几页收进项目菜单', () => {
  const library = t('navigation.project.library')
  const members = t('navigation.project.members')

  it('列表上不再有那几行，菜单里点「资料库」去资料库', async () => {
    const { container, baseElement, router } = mount({ page: true })
    const listed = Array.from(container.querySelectorAll('.v-list-item')).map((el) => el.textContent ?? '')
    expect(listed.some((text) => text.includes(library))).toBe(false)

    await openProjectMenu(container, baseElement)
    const row = menuRows(baseElement).find((el) => el.textContent?.includes(library))
    await fireEvent.click(row as Element)
    await router.isReady()
    await waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/projects/p1/library'))
  })

  it('私聊未读跟着「成员」进了菜单', async () => {
    const { container, baseElement } = mount({ page: true, privateUnreadMap: { zhang: 2, li: 1 } })
    await openProjectMenu(container, baseElement)
    const row = menuRows(baseElement).find((el) => el.textContent?.includes(members))
    expect(row?.textContent).toContain('3')
  })
})
