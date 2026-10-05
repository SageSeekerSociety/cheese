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
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import TopicSidebar from './TopicSidebar.vue'

import { setLocale, t } from '@/i18n'

const Sidebar = TopicSidebar as unknown as Component

const Blank = defineComponent({ setup: () => () => h('div') })

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

/** 一个把「定时与触发」收起来的壳：它仍然找得到，只是不占每天都要扫一遍的那条竖线。 */
const COURSE_SHELL = {
  name: 'course',
  home: 'workspace-running',
  nav: { rail: [], tabs: [], project: ['project-library', 'project-members', 'project-routines'] },
  hidden: ['project-routines'],
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

function titlesIn(root: Element, selector: string): string[] {
  return Array.from(root.querySelectorAll(selector)).map(
    (el) => el.querySelector('.v-list-item-title')?.textContent?.trim() ?? ''
  )
}

/** 打开项目名旁边那个 ⌄ 菜单，返回菜单里每一行的文字。 */
async function openProjectMenu(container: Element, baseElement: Element): Promise<string[]> {
  const header = container.querySelector('[title="项目菜单"]') ?? baseElement.querySelector('[title="项目菜单"]')
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

describe('侧栏画哪几页由壳说了算', () => {
  it('壳收起来的页不在侧栏上，但在项目名旁边那个菜单里', async () => {
    const { container, baseElement } = mount()
    expect(titlesIn(container, '.pinned-row')).toEqual(['全局', '资料库', '成员', '项目文档'])

    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes(t('navigation.project.routines')))).toBe(true)
  })

  it('首页不进菜单：项目名那一行就是它的入口，同一个地方不要两个入口', async () => {
    const { container, baseElement } = mount()
    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes(t('navigation.project.board')))).toBe(false)
    expect(rows.some((r) => r.includes(t('navigation.project.settings')))).toBe(true)
  })

  it('壳比前端新（多了一个不认识的 key）：那一格不画，别处照旧，不白屏', async () => {
    // 服务端先发了第五个壳的名字，而这一版前端还没有那一页：画一格点了就 404 的
    // 东西比不画更糟，所以它落在 `orderedNav` 那一步，不进侧栏也不进菜单。
    const shell = { ...COURSE_SHELL, nav: { ...COURSE_SHELL.nav, project: ['project-library', 'project-future'] } }
    const { container, baseElement } = mount({ projects: [project(shell)] })
    // 这份壳只点名了资料库；成员没被点名，于是它落进「更多」（菜单里那一格）。
    expect(titlesIn(container, '.pinned-row')).toEqual(['全局', '资料库', '项目文档'])

    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes('project-future'))).toBe(false)
    expect(rows.some((r) => r.includes(t('navigation.project.routines')))).toBe(true)
  })
})

describe('个人级压过壳：打开过一次的页就回到侧栏上', () => {
  it('在菜单里点开一次，这一页当场就回到侧栏上', async () => {
    const { container, baseElement } = mount()
    await clickMenuItem(container, baseElement, t('navigation.project.routines'))

    expect(titlesIn(container, '.pinned-row')).toEqual(['全局', '资料库', '成员', '定时与触发', '项目文档'])
    expect(localStorage.getItem('cheesex.shellRevealed.v1:me')).toContain('project-routines')
  })

  it('记住的是这个人：换一个 handle 进来，壳的默认照旧', async () => {
    const first = mount()
    await clickMenuItem(first.container, first.baseElement, t('navigation.project.routines'))
    first.unmount()

    localStorage.setItem('user', JSON.stringify({ id: 2, username: 'someone-else', nickname: '别人' }))
    const { container } = mount()
    expect(titlesIn(container, '.pinned-row')).toEqual(['全局', '资料库', '成员', '项目文档'])
  })

  it('重新挂载之后仍然是展开的', async () => {
    const first = mount()
    await clickMenuItem(first.container, first.baseElement, t('navigation.project.routines'))
    first.unmount()

    const { container } = mount()
    expect(titlesIn(container, '.pinned-row')).toContain('定时与触发')
  })
})

/** 在侧栏上右键这一行，点弹出来的「从侧栏隐藏」。 */
async function hideFromRail(container: Element, baseElement: Element, label: string): Promise<void> {
  const row = Array.from(container.querySelectorAll('.pinned-row')).find(
    (el) => el.querySelector('.v-list-item-title')?.textContent?.trim() === label
  )
  if (!row) throw new Error(`侧栏上没有 ${label}`)
  await fireEvent.contextMenu(row)
  const item = await waitFor(() => {
    const found = Array.from(baseElement.querySelectorAll('.v-overlay .v-list-item')).find((el) =>
      el.textContent?.includes(t('work.sidebar.hideFromRail'))
    )
    if (!found) throw new Error('右键菜单还没开')
    return found
  })
  await fireEvent.click(item)
}

/** 打开项目菜单，点某一格行尾的「在侧栏显示」。 */
async function showOnRail(container: Element, baseElement: Element, label: string): Promise<void> {
  await openProjectMenu(container, baseElement)
  const button = baseElement.querySelector(
    `.v-overlay button[aria-label="${t('work.sidebar.showOnRailNamed', { page: label })}"]`
  )
  if (!button) throw new Error(`菜单里 ${label} 没有「在侧栏显示」`)
  await fireEvent.click(button)
}

describe('他用不着的页，能从自己的侧栏上拿掉（FB-49）', () => {
  it('右键一行点「从侧栏隐藏」：这一行离开侧栏，落进项目菜单', async () => {
    const { container, baseElement } = mount()
    await hideFromRail(container, baseElement, '资料库')

    await waitFor(() => expect(titlesIn(container, '.pinned-row')).toEqual(['全局', '成员', '项目文档']))
    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes('资料库'))).toBe(true)
  })

  it('拿掉的页重新挂载之后仍然不在侧栏上，换个账号进来是壳的默认', async () => {
    const first = mount()
    await hideFromRail(first.container, first.baseElement, '成员')
    first.unmount()

    const again = mount()
    expect(titlesIn(again.container, '.pinned-row')).toEqual(['全局', '资料库', '项目文档'])
    again.unmount()

    localStorage.setItem('user', JSON.stringify({ id: 2, username: 'someone-else', nickname: '别人' }))
    const other = mount()
    expect(titlesIn(other.container, '.pinned-row')).toEqual(['全局', '资料库', '成员', '项目文档'])
  })

  it('从菜单里点开一个拿掉的页：这一次打开了它，但它不回到侧栏上', async () => {
    const { container, baseElement } = mount()
    await hideFromRail(container, baseElement, '资料库')
    await clickMenuItem(container, baseElement, '资料库')

    await waitFor(() => expect(router.currentRoute.value.name).toBe('project-library'))
    expect(titlesIn(container, '.pinned-row')).not.toContain('资料库')
  })

  it('打开过一次而留在侧栏上的页，也能再拿掉', async () => {
    const { container, baseElement } = mount()
    await clickMenuItem(container, baseElement, t('navigation.project.routines'))
    await waitFor(() => expect(titlesIn(container, '.pinned-row')).toContain('定时与触发'))

    await hideFromRail(container, baseElement, '定时与触发')
    await waitFor(() => expect(titlesIn(container, '.pinned-row')).not.toContain('定时与触发'))
  })

  it('菜单里那一格的「在侧栏显示」把它放回原来的位置', async () => {
    const { container, baseElement } = mount()
    await hideFromRail(container, baseElement, '资料库')
    await waitFor(() => expect(titlesIn(container, '.pinned-row')).not.toContain('资料库'))

    await showOnRail(container, baseElement, '资料库')
    await waitFor(() => expect(titlesIn(container, '.pinned-row')).toEqual(['全局', '资料库', '成员', '项目文档']))
  })

  it('壳默认收起的页也能直接从菜单放上侧栏', async () => {
    const { container, baseElement } = mount()
    await showOnRail(container, baseElement, '定时与触发')
    await waitFor(() =>
      expect(titlesIn(container, '.pinned-row')).toEqual(['全局', '资料库', '成员', '定时与触发', '项目文档'])
    )
  })

  it('项目文档那一行也能拿掉：菜单里那一格点下去照旧打开项目文档', async () => {
    const onSelectDocs = vi.fn()
    const { container, baseElement } = mount({ onSelectDocs })
    await hideFromRail(container, baseElement, '项目文档')
    await waitFor(() => expect(titlesIn(container, '.pinned-row')).toEqual(['全局', '资料库', '成员']))

    await clickMenuItem(container, baseElement, '项目文档')
    expect(onSelectDocs).toHaveBeenCalledWith('charter')

    await showOnRail(container, baseElement, '项目文档')
    await waitFor(() => expect(titlesIn(container, '.pinned-row')).toEqual(['全局', '资料库', '成员', '项目文档']))
  })
})
