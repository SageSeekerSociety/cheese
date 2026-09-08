// 侧栏在话题到达之前画的是什么。
//
// 一个居中的转圈只说了「在等」；话题列表的形状是已知的（一串等高的行），所以这里
// 画的是那串行本身。这一份钉的是那个区别，以及交接：话题一到，骨架就得走干净，
// 不能和真的行一起留在屏幕上。
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VLayout } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, describe, expect, it } from 'vitest'

import TopicSidebar from './TopicSidebar.vue'

const Sidebar = TopicSidebar as unknown as Component

function topic(id: string, parentId: string | null, kind = 'topic'): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: parentId,
    title: id,
    kind,
    status: 'active',
    created_by: 'u',
    created_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
  } as Topic
}

const topics: Topic[] = [topic('root', null, 'root'), topic('甲', 'root'), topic('乙', 'root')]

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/:pathMatch(.*)*', name: 'catch-all', component: defineComponent({ setup: () => () => h('div') }) },
  ],
})

// v-navigation-drawer 必须活在一个 v-layout 里（同 TopicSidebar.rail.spec）。
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
        projects: [{ id: 'p1', name: 'P1', created_at: '2026-08-10T00:00:00Z' }],
        selectedProjectId: 'p1',
        topics: [],
        selectedTopicId: null,
        loadingTopics: true,
        privateActive: false,
        members: [],
        meHandle: 'me',
        ...inner,
      },
    },
    global: { plugins: [vuetify, router, createPinia()] },
  })
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
})

describe('话题还在路上的侧栏', () => {
  it('画的是几行的形状，不是一个转圈', () => {
    const { container } = mount()
    const skeleton = container.querySelector('[role="status"][aria-busy="true"]')
    expect(skeleton, '侧栏加载态应当画出行的形状').not.toBeNull()
    expect(skeleton!.textContent).toContain('加载中')
    expect(container.querySelector('.v-progress-circular'), '列表的形状是已知的，不该用转圈').toBeNull()
  })

  it('画的行数够填满一段列表 —— 只画一行等于说「只有一条话题」', () => {
    const { container } = mount()
    expect(container.querySelectorAll('.skel > div').length).toBeGreaterThanOrEqual(4)
  })

  it('话题到齐，骨架就走干净，屏幕上只剩真的行', async () => {
    const { container, rerender } = mount()
    expect(container.querySelectorAll('.topic-row').length).toBe(0)

    await rerender({
      inner: {
        projects: [{ id: 'p1', name: 'P1', created_at: '2026-08-10T00:00:00Z' }],
        selectedProjectId: 'p1',
        topics,
        selectedTopicId: null,
        loadingTopics: false,
        privateActive: false,
        members: [],
        meHandle: 'me',
      },
    })

    expect(container.querySelector('[role="status"][aria-busy="true"]'), '真的行来了，骨架不能还在').toBeNull()
    const titles = Array.from(container.querySelectorAll('.topic-row .v-list-item-title')).map((n) =>
      n.textContent?.trim()
    )
    expect(titles).toContain('甲')
    expect(titles).toContain('乙')
  })
})
