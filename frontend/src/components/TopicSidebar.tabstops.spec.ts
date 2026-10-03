// 键盘可达性：侧栏里每一个 Tab 停得住的地方，用户都得看得见东西。
//
// Vuetify 的 VList 根元素固定带 tabindex="0"（role="listbox"），所以一个「没有行」的
// 分组列表就是一个零高度、看不见、Tab 却停得上的焦点点：焦点环套在零高度的盒子上，
// 看起来是横贯侧栏的一条细线（用户 2026-10-03 的截图）。这里不钉 DOM 形状，只钉用户
// 按 Tab 时的那条序列：滚动区里停得住的每一处，要么本身是个控件，要么有内容。
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

/** 滚动区里 Tab 停得住的地方：显式写了 tabindex >= 0 的，加没被标 -1 的原生控件。
 *  Vuetify 给列表项标的 -2/-1 是列表内部的焦点管理，Tab 走不到，不算。 */
function tabStops(container: Element): HTMLElement[] {
  return Array.from(container.querySelectorAll('.rail-scroll *')).filter((el) => {
    const attr = el.getAttribute('tabindex')
    return attr === null ? el.matches('button, a[href], input, select, textarea') : Number(attr) >= 0
  }) as HTMLElement[]
}

/** 停在这儿，用户看得见东西吗：控件本身，或者一个装着内容的盒子。 */
function showsSomethingToUser(el: HTMLElement): boolean {
  return el.matches('button, a[href], input, select, textarea') || (el.textContent ?? '').trim() !== ''
}

/** Tab 停上去却什么都看不见的那些（按类名报出来，红的时候知道是哪个）。 */
function stopsShowingNothing(container: Element): string[] {
  return tabStops(container)
    .filter((el) => !showsSomethingToUser(el))
    .map((el) => el.className)
}

describe('侧栏里 Tab 停得住的地方都看得见', () => {
  beforeEach(() => localStorage.clear())

  it('没有别人的话题时，空出来的那一组不留停靠点', () => {
    const { container } = mount({
      topics: [topic('root', null), topic('mine', 'root', { i_participate: true })],
      selectedTopicId: 'mine',
    })
    expect(stopsShowingNothing(container)).toEqual([])
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
    expect(stopsShowingNothing(container)).toEqual([])
  })
})

describe('Tab 逐行走得进话题行', () => {
  beforeEach(() => localStorage.clear())

  it('每一行都是一个停靠点，不是只有第一行', () => {
    const { container } = mount({
      topics: [
        topic('root', null),
        topic('mine', 'root', { i_participate: true }),
        topic('second', 'root', { i_participate: true }),
        topic('third', 'root', { i_participate: true }),
      ],
      selectedTopicId: 'mine',
    })
    const rows = Array.from(container.querySelectorAll('.rail-scroll .topic-row')) as HTMLElement[]
    expect(rows.length).toBeGreaterThan(1)
    const stops = tabStops(container)
    expect(rows.filter((row) => !stops.includes(row))).toEqual([])
  })
})

describe('行尾那颗 ⋯ 只在选中行上进 Tab 序列', () => {
  beforeEach(() => localStorage.clear())

  it('选中的行连 ⋯ 一起停，没选中的行只停行本身', () => {
    const { container } = mount({
      topics: [
        topic('root', null),
        topic('mine', 'root', { i_participate: true }),
        topic('second', 'root', { i_participate: true }),
        topic('third', 'root', { i_participate: true }),
      ],
      selectedTopicId: 'mine',
    })
    const actionsOf = (id: string) =>
      container.querySelector(`.topic-row[data-room-id="${id}"] .row-actions__btn`) as HTMLElement
    const stops = tabStops(container)
    expect(stops).toContain(actionsOf('mine'))
    expect(stops).not.toContain(actionsOf('second'))
    expect(stops).not.toContain(actionsOf('third'))
  })
})
