// 「退出项目」的第二个入口：项目名旁边那个 ⋯ 菜单。
//
// 这一份守的是 #6 的回归——那颗按钮本来只长在成员页右上角，而成员页刚被壳收进
// 同一个菜单（catalog.py 的 hidden），按钮跟着一起藏了两层深，项目 lead 都找不到。
// 所以「项目头那一层就有一颗」这件事本身必须钉住；顺带钉住所有者不显示（后端会
// 拒他，前端不给一个必定失败的按钮），以及点下去是先确认、不会直接退出去。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VLayout } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, screen } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const leaveProject = vi.fn()
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    leaveProject: (...a: unknown[]) => leaveProject(...a),
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

const projects = [
  { id: 'p1', name: '知是', created_at: '2026-08-10T00:00:00Z', owner_handle: 'alice' },
]

const Blank = defineComponent({ setup: () => () => h('div') })

function makeRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/projects/:projectId', name: 'workspace-project', component: Blank },
      { path: '/projects/:projectId/settings', name: 'project-settings', component: Blank },
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
  leaveProject.mockReset().mockResolvedValue({ deleted: true })
  refreshMembers.mockReset().mockResolvedValue(undefined)
  refreshProjects.mockReset().mockResolvedValue(undefined)
  meHandle = 'ligan'
})

describe('项目菜单里的退出项目', () => {
  it('非所有者在项目头那个菜单里就看得到退出项目——不用先进成员页', async () => {
    meHandle = 'ligan'
    const { container, baseElement } = mount()
    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes('退出项目'))).toBe(true)
  })

  it('所有者看不到：后端会拒他，不给一个必定失败的按钮', async () => {
    meHandle = 'alice'
    const { container, baseElement } = mount()
    const rows = await openProjectMenu(container, baseElement)
    expect(rows.some((r) => r.includes('退出项目'))).toBe(false)
  })

  it('点下去先确认——一下点不会直接退出去', async () => {
    meHandle = 'ligan'
    const { container, baseElement } = mount()
    await openProjectMenu(container, baseElement)
    const item = Array.from(baseElement.querySelectorAll('.v-overlay .v-list-item')).find((el) =>
      el.textContent?.includes('退出项目')
    )
    await fireEvent.click(item as Element)
    expect(leaveProject).not.toHaveBeenCalled()
    expect(await screen.findByRole('button', { name: '退出' })).toBeTruthy()
  })
})
