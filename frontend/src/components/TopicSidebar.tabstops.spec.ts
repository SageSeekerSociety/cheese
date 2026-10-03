// 键盘可达性：侧栏里每一个能被 Tab 停住的东西，都必须是看得见的。
//
// Vuetify 的 VList 根元素固定带 tabindex="0"（role="listbox"），所以一个「没有行」的
// 分组列表 = 一个零高度、看不见、但 Tab 停得上的焦点点：全局焦点环套上去，就是横贯
// 侧栏的一条细线（用户 2026-10-03 的截图）。这里钉住：滚动区里凡是渲染出来的列表，
// 都至少有一行——收起来的组什么都不画，而不是画一个空壳。
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
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import TopicSidebar from './TopicSidebar.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const Sidebar = TopicSidebar as unknown as Component

function topic(id: string, parentId: string | null, flags: Partial<Topic> = {}): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: parentId,
    title: id,
    kind: parentId === null ? 'root' : 'topic',
    status: 'active',
    created_by: 'u',
    created_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
    ...flags,
  } as Topic
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/:pathMatch(.*)*', name: 'catch-all', component: defineComponent({ setup: () => () => h('div') }) },
  ],
})

const Host = defineComponent({
  props: { inner: { type: Object, required: true } },
  setup(props) {
    return () => h(VLayout, null, { default: () => [h(Sidebar, props.inner as Record<string, unknown>)] })
  },
})

function mount(inner: Record<string, unknown> = {}) {
  const vuetify = createVuetify({ components, directives })
  return render(Host, {
    props: {
      inner: {
        projects: [{ id: 'p1', name: 'P1', created_at: '2026-08-10T00:00:00Z' }],
        selectedProjectId: 'p1',
        topics: [],
        selectedTopicId: null,
        loadingTopics: false,
        privateActive: false,
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

/** 滚动区里渲染出来的列表：Vuetify 的列表自己就是一个 Tab 停靠点。 */
function railLists(container: Element): HTMLElement[] {
  return Array.from(container.querySelectorAll('.rail-scroll .v-list')) as HTMLElement[]
}

/** 里面一行都没有的列表 —— 一个个都是看不见的 Tab 停靠点。 */
function emptyListClasses(container: Element): string[] {
  return railLists(container)
    .filter((list) => list.querySelectorAll('.v-list-item').length === 0)
    .map((list) => list.className)
}

describe('侧栏的 Tab 停靠点都看得见', () => {
  beforeEach(() => localStorage.clear())

  it('没有别人的话题时，不留一个空的列表壳子', () => {
    const { container } = mount({
      topics: [topic('root', null), topic('mine', 'root', { i_participate: true })],
      selectedTopicId: 'mine',
    })
    expect(emptyListClasses(container)).toEqual([])
  })

  it('「其他话题」收着的时候，一样不留', () => {
    const { container } = mount({
      topics: [
        topic('root', null),
        topic('mine', 'root', { i_participate: true }),
        topic('theirs', 'root', { i_participate: false }),
      ],
      selectedTopicId: 'mine',
    })
    expect(emptyListClasses(container)).toEqual([])
  })
})
