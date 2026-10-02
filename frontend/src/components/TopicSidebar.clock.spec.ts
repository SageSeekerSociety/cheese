// 侧栏自己那只慢钟：「有人 @ 了芝士、等了五分钟还没回话」是**时间自己走到的**，
// 不是列表数据变出来的——列表三十分钟才刷一次，而这一刻已经等太久了。
//
// （TopicSidebar.rail.spec.ts 那条「等满五分钟亮红灯」问的是**挂载那一刻**的判断；
// 这一条问的是没有新数据、没有重新挂载时，那盏灯会不会自己亮起来。两件事。）
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { defineComponent, h, nextTick } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VLayout } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import TopicSidebar from './TopicSidebar.vue'

import { setLocale } from '@/i18n'

// 断言按中文写；测试环境默认是英文界面。
beforeEach(() => setLocale('zh-CN'))

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

const topics: Topic[] = [topic('root', null, 'root'), topic('a', 'root'), topic('b', 'root')]

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
        topics,
        selectedTopicId: null,
        loadingTopics: false,
        ...inner,
      },
    },
    global: { plugins: [vuetify, router, createPinia()] },
  })
}

function rowFor(container: Element, title: string): HTMLElement {
  const row = Array.from(container.querySelectorAll('.topic-row')).find(
    (el) => el.querySelector('.topic-title .text-truncate')?.textContent?.trim() === title
  )
  if (!row) throw new Error(`没有找到话题行: ${title}`)
  return row as HTMLElement
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

afterEach(() => vi.useRealTimers())

describe('侧栏那只慢钟', () => {
  // 已经不早了、但还没到五分钟：挂载那一刻什么都不该亮。
  function waitingTopic(minutes: number): Topic[] {
    const since = new Date(Date.now() - minutes * 60_000).toISOString()
    return topics.map((t) =>
      t.id === 'a' ? ({ ...t, waits: [{ member: 'cheese-a1', reason: 'mention', since }] } as Topic) : t
    )
  }

  it('时间自己走满五分钟，红标不用等下一次刷新就亮起来', async () => {
    // 假钟必须在挂载**之前**装上：这只钟是 onMounted 里上的发条。
    vi.useFakeTimers()
    const { container } = mount({ topics: waitingTopic(4.5) })
    await nextTick()
    expect(rowFor(container, 'a').querySelector('[data-state="stalled"]')).toBeNull()

    // 没有换过任何一份数据，只是钟往前走了 —— 灯要自己亮。
    await vi.advanceTimersByTimeAsync(60_000)
    await nextTick()
    expect(rowFor(container, 'a').querySelector('[data-state="stalled"]')).not.toBeNull()
  })

  it('还没到点就不亮：钟走过去之前，多拨几下也还是暗的', async () => {
    vi.useFakeTimers()
    const { container } = mount({ topics: waitingTopic(1) })
    await nextTick()
    // 三次滴答 = 三十秒，离五分钟还远。
    await vi.advanceTimersByTimeAsync(30_000)
    await nextTick()
    expect(rowFor(container, 'a').querySelector('[data-state="stalled"]')).toBeNull()
  })

  it('没在等的话题不受影响：拨多久都不会亮', async () => {
    vi.useFakeTimers()
    const { container } = mount({ topics: waitingTopic(4.5) })
    await vi.advanceTimersByTimeAsync(10 * 60_000)
    await nextTick()
    expect(rowFor(container, 'b').querySelector('[data-state="stalled"]')).toBeNull()
  })

  it('红灯那一句说的是谁在等、等了多久', async () => {
    vi.useFakeTimers()
    const { container } = mount({ topics: waitingTopic(10) })
    await nextTick()
    const dot = rowFor(container, 'a').querySelector('[data-state="stalled"]') as HTMLElement
    expect(dot.title).toContain('分钟')
    expect(dot.title).not.toBe('')
  })

  it('卸载之后那只钟就停了', async () => {
    vi.useFakeTimers()
    const view = mount({ topics: waitingTopic(4.5) })
    await nextTick()
    const cleared = vi.spyOn(window, 'clearInterval')
    view.unmount()
    expect(cleared).toHaveBeenCalled()
    // 也不会再有下一次滴答：再拨时间什么都不发生（没东西可断言的状态更新）。
    await vi.advanceTimersByTimeAsync(60_000)
  })
})
