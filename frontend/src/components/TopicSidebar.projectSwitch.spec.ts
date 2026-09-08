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
import { fireEvent, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, describe, expect, it } from 'vitest'

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
  const header = container.querySelector('[title="项目菜单"]') ?? baseElement.querySelector('[title="项目菜单"]')
  await fireEvent.click(header as Element)
  return Array.from(baseElement.querySelectorAll('.v-overlay .v-list-item')).map(
    (el) => el.textContent?.replace(/\s+/g, '') ?? ''
  )
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
    const target = Array.from(baseElement.querySelectorAll('.v-overlay .v-list-item')).find((el) =>
      el.textContent?.includes('推荐算法')
    )
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
    expect(rows).toEqual(['项目设置'])
  })
})
