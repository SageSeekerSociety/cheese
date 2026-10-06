// 侧栏只列我加入了的频道：没加入的、已归档的都不在这里（从「浏览频道」去找），
// 只有我此刻正看着的那个没加入的频道例外——不然人不知道自己在哪儿。
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
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import TopicSidebar from './TopicSidebar.vue'

import { setLocale } from '@/i18n'

// 断言按中文写；测试环境默认是英文界面。
beforeEach(() => setLocale('zh-CN'))

const Sidebar = TopicSidebar as unknown as Component

function topic(id: string, parentId: string | null, flags: Partial<Topic> = {}): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: parentId,
    title: id,
    kind: parentId === null ? 'root' : 'channel',
    status: 'active',
    created_by: 'u',
    created_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
    ...flags,
  } as Topic
}

// root（综合）→ mine（我加入的，带一个子频道）+ theirs（没加入）
const topics: Topic[] = [
  topic('root', null, { joined: true }),
  topic('mine', 'root', { joined: true }),
  topic('mine1', 'mine', { joined: true }),
  topic('theirs', 'root', { joined: false }),
  topic('gone', 'root', { joined: true, status: 'archived' }),
]

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
        privateActive: false,
        ...inner,
      },
    },
    global: { plugins: [vuetify, router, createPinia()] },
  })
}

function topicRows(container: Element): HTMLElement[] {
  return Array.from(container.querySelectorAll('.topic-row')) as HTMLElement[]
}
function titleOf(row: Element): string {
  return row.querySelector('.topic-title .text-truncate')?.textContent?.trim() ?? ''
}
function visibleTitles(container: Element): string[] {
  return topicRows(container).map(titleOf)
}
function rowFor(container: Element, title: string): HTMLElement {
  const row = topicRows(container).find((el) => titleOf(el) === title)
  if (!row) throw new Error(`没有找到话题行: ${title}`)
  return row
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

describe('侧栏只列我加入了的频道', () => {
  it('没加入的、已归档的频道都不在侧栏里', () => {
    const { container } = mount()
    const titles = visibleTitles(container)
    expect(titles).toContain('mine')
    expect(titles).not.toContain('theirs')
    expect(titles).not.toContain('gone')
  })

  it('正看着的那个没加入的频道照样列出来', () => {
    const { container } = mount({ selectedTopicId: 'theirs' })
    expect(visibleTitles(container)).toContain('theirs')
  })

  it('没带 joined 的载荷当作加入了：宁可多列，不把频道静默藏起来', () => {
    const { container } = mount({ topics: [topic('root', null), topic('plain', 'root')] })
    expect(visibleTitles(container)).toContain('plain')
  })

  it('「浏览频道」通到全部频道', async () => {
    const onBrowseChannels = vi.fn()
    const view = mount({ onBrowseChannels })
    await fireEvent.click(view.getByRole('button', { name: '浏览频道' }))
    expect(onBrowseChannels).toHaveBeenCalled()
  })

  it('有新消息但档位不让计数时，名字加粗、不出数字', () => {
    const { container } = mount({ unreadMap: { mine: { count: 0, new: true, messages: 4 } } })
    const row = rowFor(container, 'mine')
    expect(row.querySelector('.title-unread')).not.toBeNull()
    expect(row.querySelector('.unread-badge')).toBeNull()
  })

  it('@我的那几条在行上是数字', () => {
    const { container } = mount({ unreadMap: { mine: { count: 2, new: true, messages: 9 } } })
    expect(rowFor(container, 'mine').textContent).toContain('2')
  })
})

it('reveals and scrolls to the newly selected room immediately', async () => {
  const scroll = vi.fn()
  const original = HTMLElement.prototype.scrollIntoView
  HTMLElement.prototype.scrollIntoView = scroll
  try {
    const view = mount()
    const created = topic('new-room', 'root', { joined: true })
    await view.rerender({
      inner: {
        projects: [],
        selectedProjectId: 'p1',
        topics: [created, ...topics],
        selectedTopicId: 'new-room',
        loadingTopics: false,
      },
    })
    expect(visibleTitles(view.container)).toContain('new-room')
    await vi.waitFor(() => expect(scroll).toHaveBeenCalledWith({ block: 'nearest' }))
  } finally {
    HTMLElement.prototype.scrollIntoView = original
  }
})
