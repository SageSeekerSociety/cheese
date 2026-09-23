// 「退出项目 / 转让项目」的入口：项目名旁边那个 ⋯ 菜单里各一行。
//
// 这一份守两件事。一是**只有菜单这一个入口**：项目头那一行只放「回首页的名字」和
// 「项目菜单」两个按钮，退出和转让都不在那一行上——同一件事两个入口，只会让人猜
// 哪个才算数。
// 二是菜单里那两行的规矩：所有者是「转让」不是「退出」（后端会拒他退，交出手才是
// 他那条路）；项目行还没到货时两行都不长（没行 ≠ 不是所有者，那正是递 403 的那条
// 缝）；点下去是先确认，确认之后退出、刷新、回首页——不会直接退出去，也不会退完
// 停在原地。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { defineComponent, h, ref } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VLayout } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

const leaveProject = vi.fn()
const setProjectOwner = vi.fn()
const listProjectMembers = vi.fn()
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    leaveProject: (...a: unknown[]) => leaveProject(...a),
    setProjectOwner: (...a: unknown[]) => setProjectOwner(...a),
    listProjectMembers: (...a: unknown[]) => listProjectMembers(...a),
  }
})

const refreshMembers = vi.fn()
const refreshProjects = vi.fn()
vi.mock('@/stores/workspace', () => ({
  useWorkspaceStore: () => ({ refreshMembers, refreshProjects }),
}))

let meHandle = 'ligan'
vi.mock('@/me', () => ({ myHandle: () => meHandle }))

import TopicSidebar from './TopicSidebar.vue'

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

const projects = [{ id: 'p1', name: '知是', created_at: '2026-08-10T00:00:00Z', owner_handle: 'alice' }]

const Blank = defineComponent({ setup: () => () => h('div') })

function makeRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/projects/:projectId', name: 'workspace-project', component: Blank },
      { path: '/projects/:projectId/settings', name: 'project-settings', component: Blank },
      { path: '/projects/:projectId/members', name: 'project-members', component: Blank },
      { path: '/', name: 'HomeSpaces', component: Blank },
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
        ...inner,
      },
    },
    global: { plugins: [vuetify, router, createPinia()] },
  })
  return { ...utils, router }
}

/** 打开项目头那个菜单，返回菜单里的行文字。 */
async function openProjectMenu(container: Element, baseElement: Element): Promise<string[]> {
  const header = container.querySelector('[title="项目菜单"]') ?? baseElement.querySelector('[title="项目菜单"]')
  await fireEvent.click(header as Element)
  return Array.from(baseElement.querySelectorAll('.v-overlay .v-list-item')).map(
    (el) => el.textContent?.replace(/\s+/g, '') ?? ''
  )
}

function menuItem(baseElement: Element, text: string): Element | undefined {
  return Array.from(baseElement.querySelectorAll('.v-overlay .v-list-item')).find((el) =>
    el.textContent?.includes(text)
  )
}

function headerBtn(container: Element, label: string): Element | null {
  return container.querySelector(`.rail-header [aria-label="${label}"]`)
}

/** 项目头那一行除了名字和菜单按钮，不该再有别的按钮。 */
function headerButtons(container: Element): string[] {
  return Array.from(container.querySelectorAll('.rail-header button')).map(
    (el) => el.getAttribute('aria-label') ?? el.textContent?.replace(/\s+/g, '') ?? ''
  )
}

function pinnedTitles(container: Element): string[] {
  return Array.from(container.querySelectorAll('.pinned-row')).map((el) => el.textContent?.replace(/\s+/g, '') ?? '')
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
  // 置顶行的文案走 i18n（壳能换词），而 happy-dom 的 navigator.language 是 en-US
  // ——不定住的话「成员」那条断言问的是英文那套词。和 rail.spec 同一行。
  setLocale('zh-CN')
  leaveProject.mockReset().mockResolvedValue({ deleted: true })
  setProjectOwner.mockReset().mockResolvedValue({})
  listProjectMembers.mockReset().mockResolvedValue({ data: [], total: 0 })
  refreshMembers.mockReset().mockResolvedValue(undefined)
  refreshProjects.mockReset().mockResolvedValue(undefined)
  meHandle = 'ligan'
})

describe('退出项目的入口只有 ⋯ 菜单一个', () => {
  it('非所有者：项目头上没有退出那颗，菜单里有一行', async () => {
    meHandle = 'ligan'
    const { container, baseElement } = mount()
    // 项目头这一行只有「回首页的名字」和「项目菜单」两个按钮。
    expect(headerButtons(container)).toEqual(['知是', '项目菜单'])
    expect(headerBtn(container, '退出项目')).toBeNull()
    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes('退出项目'))).toBe(true)
    // 多余的一行也不要：非所有者菜单里没有转让。
    expect(rows.some((r) => r.includes('转让项目'))).toBe(false)
  })

  it('名册仍然在侧栏上——它是置顶行之一，不用开菜单就看得见', () => {
    meHandle = 'ligan'
    const { container } = mount()
    expect(pinnedTitles(container).some((t) => t.includes('成员'))).toBe(true)
  })
})

describe('所有者的路是转让，不是退出', () => {
  it('所有者：项目头上没有转让那颗（那行只有名字和菜单），菜单里有转让一行', async () => {
    meHandle = 'alice'
    const { container, baseElement } = mount()
    expect(headerButtons(container)).toEqual(['知是', '项目菜单'])
    expect(headerBtn(container, '转让项目')).toBeNull()
    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes('转让项目'))).toBe(true)
    expect(rows.some((r) => r.includes('退出项目'))).toBe(false)
  })

  it('点菜单里的转让打开选人弹窗，确认之后走 PUT /projects/{id}/owner 并刷新', async () => {
    meHandle = 'alice'
    listProjectMembers.mockResolvedValue({
      data: [
        { user_handle: 'ligan', role: 'member', name: '李干' },
        { user_handle: 'alice', role: 'lead', name: '爱丽丝', source: 'owner' },
        { user_handle: 'cheese-x', role: 'member', name: '芝士', agent: true },
      ],
      total: 3,
    })
    const { container, baseElement } = mount()
    await openProjectMenu(container, baseElement)
    await fireEvent.click(menuItem(baseElement, '转让项目') as Element)
    // 可接手的人列出来；自己、所有者补的那行、AI 队友都不列。
    await fireEvent.click(await screen.findByText('李干'))
    await fireEvent.click(await screen.findByRole('button', { name: '转让' }))
    await waitFor(() => expect(setProjectOwner).toHaveBeenCalledWith('p1', 'ligan'))
    await waitFor(() => expect(refreshProjects).toHaveBeenCalled())
    expect(refreshMembers).toHaveBeenCalled()
  })
})

describe('项目行没到货时不递 403 的入口', () => {
  it('清单里没有这个项目 → 菜单里退出和转让都不长', async () => {
    // 所有者一旦行到货就不该看到退出——而行没到货时更不能先按「不是所有者」给他
    // 退出：那一下点下去是必定 403。
    meHandle = 'alice'
    const { container, baseElement } = mount({ projects: [], selectedProjectId: 'p1' })
    expect(headerBtn(container, '退出项目')).toBeNull()
    expect(headerBtn(container, '转让项目')).toBeNull()
    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes('退出项目'))).toBe(false)
    expect(rows.some((r) => r.includes('转让项目'))).toBe(false)
  })

  it('行到货了才按行上的 owner 说话', async () => {
    meHandle = 'alice'
    // 行没到货：菜单里谁都不给。
    const { container, baseElement } = mount({ projects: [], selectedProjectId: 'p1' })
    const none = await openProjectMenu(container, baseElement)
    expect(none.some((r) => r.includes('退出项目'))).toBe(false)
    expect(none.some((r) => r.includes('转让项目'))).toBe(false)
    // 行一到货，所有者那一行就是「转让项目」，不是退出。
    const { container: c2, baseElement: b2 } = mount()
    const rows = await openProjectMenu(c2, b2)
    expect(rows.some((r) => r.includes('转让项目'))).toBe(true)
    expect(rows.some((r) => r.includes('退出项目'))).toBe(false)
  })
})

describe('退出：先确认，确认之后退出、刷新、回首页', () => {
  it('点下去先确认——一下点不会直接退出去', async () => {
    meHandle = 'ligan'
    const { container, baseElement } = mount()
    await openProjectMenu(container, baseElement)
    await fireEvent.click(menuItem(baseElement, '退出项目') as Element)
    expect(leaveProject).not.toHaveBeenCalled()
    expect(await screen.findByRole('button', { name: '退出' })).toBeTruthy()
  })

  it('确认之后按当前选中的那个项目退，刷新名册和项目列表，回首页', async () => {
    meHandle = 'ligan'
    const { container, baseElement, router } = mount()
    const push = vi.spyOn(router, 'push').mockResolvedValue(undefined)
    await openProjectMenu(container, baseElement)
    await fireEvent.click(menuItem(baseElement, '退出项目') as Element)
    await fireEvent.click(await screen.findByRole('button', { name: '退出' }))
    // :project-id 绑的是 selectedProjectId——绑错了就是退错项目，所以连值一起钉。
    await waitFor(() => expect(leaveProject).toHaveBeenCalledWith('p1'))
    await waitFor(() => expect(refreshMembers).toHaveBeenCalled())
    expect(refreshProjects).toHaveBeenCalled()
    await waitFor(() => expect(push).toHaveBeenCalledWith({ name: 'HomeSpaces' }))
  })

  it('换一个选中项目，退的跟着换', async () => {
    meHandle = 'ligan'
    const two = [...projects, { id: 'p2', name: '第二', created_at: '2026-08-10T00:00:00Z', owner_handle: 'bob' }]
    const { container, baseElement, router } = mount({ projects: two, selectedProjectId: 'p2' })
    vi.spyOn(router, 'push').mockResolvedValue(undefined)
    await openProjectMenu(container, baseElement)
    await fireEvent.click(menuItem(baseElement, '退出项目') as Element)
    await fireEvent.click(await screen.findByRole('button', { name: '退出' }))
    await waitFor(() => expect(leaveProject).toHaveBeenCalledWith('p2'))
    expect(leaveProject).not.toHaveBeenCalledWith('p1')
  })
})

describe('转让之后：刷新 → 重判，不重挂', () => {
  it('setProjectOwner 之后行上的 owner 换了人，退出长出来、转让消失', async () => {
    meHandle = 'alice'
    // 可变的项目行——生产里 ProjectSidebar 把 store.projects 传给这一层，而
    // refreshProjects 是整份换掉它（`projects.value = (await listProjects()).data`）。
    // host 按当前值往里传（同 ProjectSidebar 的绑定），所以换值之后这一层必须自己
    // 重判——转让完还能退出，靠的就是这根线。
    const storeProjects = ref([{ id: 'p1', name: '知是', created_at: '2026-08-10T00:00:00Z', owner_handle: 'alice' }])
    refreshProjects.mockImplementation(async () => {
      storeProjects.value = [{ id: 'p1', name: '知是', created_at: '2026-08-10T00:00:00Z', owner_handle: 'bobby' }]
    })
    listProjectMembers.mockResolvedValue({
      data: [
        { user_handle: 'bobby', role: 'member', name: '鲍比' },
        { user_handle: 'alice', role: 'lead', name: '爱丽丝', source: 'owner' },
      ],
      total: 2,
    })

    const HostArc = defineComponent({
      setup: () => () =>
        h(VLayout, null, {
          default: () => [
            h(Sidebar, {
              projects: storeProjects.value,
              selectedProjectId: 'p1',
              topics,
              selectedTopicId: null,
              loadingTopics: false,
            }),
          ],
        }),
    })
    if (!document.getElementById('app-bar-slot')) {
      const slot = document.createElement('div')
      slot.id = 'app-bar-slot'
      document.body.appendChild(slot)
    }
    const vuetify = createVuetify({ components, directives })
    const router = makeRouter()
    const { container, baseElement } = render(HostArc, {
      global: { plugins: [vuetify, router, createPinia()] },
    })

    let rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes('转让项目'))).toBe(true)
    expect(rows.some((r) => r.includes('退出项目'))).toBe(false)

    await fireEvent.click(menuItem(baseElement, '转让项目') as Element)
    await fireEvent.click(await screen.findByText('鲍比'))
    await fireEvent.click(await screen.findByRole('button', { name: '转让' }))
    await waitFor(() => expect(setProjectOwner).toHaveBeenCalledWith('p1', 'bobby'))
    await waitFor(() => expect(refreshProjects).toHaveBeenCalled())
    await waitFor(() => expect(storeProjects.value[0].owner_handle).toBe('bobby'))

    // 同一棵树，没有重挂：菜单里那一行从「转让」换成「退出」——靠的就是这一层跟着
    // host 换进去的新行自己重判。
    await waitFor(async () => {
      rows = await openProjectMenu(container, baseElement)
      expect(rows.some((r) => r.includes('退出项目'))).toBe(true)
    })
    expect(rows.some((r) => r.includes('转让项目'))).toBe(false)
  })
})
