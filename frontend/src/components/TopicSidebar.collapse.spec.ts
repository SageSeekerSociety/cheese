// 组件级行为测试：真的把左侧话题列表挂起来，点折叠开关，看哪些行还在 DOM 里。
// （lib/topicTree.spec.ts 测的是可见性算法本身；这一份测的是"点了以后屏幕上
// 发生了什么"，两条验收要求——折叠后后代不可见、当前选中始终可见——都在这里。）
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
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import TopicSidebar from './TopicSidebar.vue'

// h() 对 SFC 的具名 props 类型太严，这里只需要它是个组件。
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

// root(本体，不占行) → a → a1 → a1x，外加 a2 和 b
const topics: Topic[] = [
  topic('root', null, 'root'),
  topic('a', 'root'),
  topic('a1', 'a'),
  topic('a1x', 'a1'),
  topic('a2', 'a'),
  topic('b', 'root'),
]

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/:pathMatch(.*)*', name: 'catch-all', component: defineComponent({ setup: () => () => h('div') }) },
  ],
})

// v-navigation-drawer 必须活在一个 v-layout 里，否则 Vuetify 直接抛
// "Could not find injected layout"——所以挂一个最小宿主把它包起来。
const Host = defineComponent({
  props: { inner: { type: Object, required: true } },
  setup(props) {
    return () => h(VLayout, null, { default: () => [h(Sidebar, props.inner as Record<string, unknown>)] })
  },
})

function mount(inner: Record<string, unknown>) {
  const vuetify = createVuetify({ components, directives })
  // 侧栏走全站的 SecondaryNavigation 外壳（背景层 + 圆角 + 移动端 temporary），
  // 它读 navigation store，所以这里得有 pinia。
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

// 话题行（不含项目文档/私聊等固定行）及其标题。
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
function toggleFor(container: Element, title: string): HTMLElement {
  const btn = rowFor(container, title).querySelector('button.subtree-toggle') as HTMLElement | null
  if (!btn) throw new Error(`话题行没有折叠开关: ${title}`)
  return btn
}

beforeAll(() => {
  // Vuetify 的 layout/overlay 会摸这两个浏览器 API，happy-dom 没有。
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

describe('左侧话题列表：子话题折叠', () => {
  beforeEach(() => localStorage.clear())

  it('默认全部展开', () => {
    const { container } = mount({})
    expect(visibleTitles(container)).toEqual(['a', 'a1', 'a1x', 'a2', 'b'])
  })

  it('点一下收起，后代（含孙子）都从列表里消失；再点一下回来', async () => {
    const { container } = mount({})
    await fireEvent.click(toggleFor(container, 'a'))
    expect(visibleTitles(container)).toEqual(['a', 'b'])

    await fireEvent.click(toggleFor(container, 'a'))
    expect(visibleTitles(container)).toEqual(['a', 'a1', 'a1x', 'a2', 'b'])
  })

  it('当前选中的话题始终可见：祖先事先被收起来，它和通往它的路径照样在', () => {
    // 上一次会话把 a、a1 都收起来了（存在 localStorage 里）
    localStorage.setItem('cheesex.topicCollapsed.v1:p1', JSON.stringify(['a', 'a1']))
    const { container } = mount({ selectedTopicId: 'a1x' })
    expect(visibleTitles(container)).toContain('a1x')
    expect(visibleTitles(container)).toEqual(['a', 'a1', 'a1x', 'b'])
    // 同一支里没被选中的兄弟仍然是收起来的
    expect(visibleTitles(container)).not.toContain('a2')
  })

  it('折叠状态被记住（刷新后重挂仍然是收起来的）', async () => {
    const first = mount({})
    await fireEvent.click(toggleFor(first.container, 'a'))
    first.unmount()

    const { container } = mount({})
    expect(visibleTitles(container)).toEqual(['a', 'b'])
  })

  it('收起来时未读冒到父行上，不会被折叠吞掉', async () => {
    const { container } = mount({ unreadMap: { a1x: 3, a2: 4 } })
    await fireEvent.click(toggleFor(container, 'a'))
    const row = rowFor(container, 'a')
    expect(row.querySelector('.unread-badge')?.textContent?.trim()).toBe('7')
  })

  // 收起来的父话题原先会把子话题的呼吸点整个藏掉：只有未读会聚合，"芝士在跑"
  // 和"等你处理"不会。合槽之后由折叠开关自己带聚合色补上。
  it('收起来时子话题的"芝士在跑"冒到折叠开关上', async () => {
    const running = { ...topic('a1x', 'a1'), running: true } as Topic
    const { container } = mount({ topics: topics.map((t) => (t.id === 'a1x' ? running : t)) })
    // 展开着的时候，动静长在 a1x 自己那一行上
    expect(rowFor(container, 'a1x').querySelector('.running-dot')).not.toBeNull()
    expect(toggleFor(container, 'a').classList.contains('subtree-toggle--running')).toBe(false)

    await fireEvent.click(toggleFor(container, 'a'))
    expect(visibleTitles(container)).toEqual(['a', 'b'])
    expect(toggleFor(container, 'a').classList.contains('subtree-toggle--running')).toBe(true)
    // 底下什么都没有的那一支不能跟着亮
    expect(rowFor(container, 'b').querySelector('.subtree-toggle')).toBeNull()
  })

  it('收起来时子话题的"等你处理"也冒上来，并且压过"在跑"', async () => {
    const patched = topics.map((t) => {
      if (t.id === 'a1x') return { ...t, running: true } as Topic
      if (t.id === 'a2') return { ...t, awaits_me: true } as Topic
      return t
    })
    const { container } = mount({ topics: patched })
    await fireEvent.click(toggleFor(container, 'a'))
    const toggle = toggleFor(container, 'a')
    expect(toggle.classList.contains('subtree-toggle--awaits')).toBe(true)
    // 两种状态同时存在时只显示一种，否则一个槽要上两个颜色
    expect(toggle.classList.contains('subtree-toggle--running')).toBe(false)
    expect(toggle.getAttribute('title')).toBe('展开子话题：里面有事等你处理')
  })

  // 选中 + 收起是最需要看见聚合状态的组合（人正站在这个话题里，子话题在替他跑）。
  it('选中的父话题收起来时，聚合状态仍然挂在开关上', async () => {
    const patched = topics.map((t) => (t.id === 'a2' ? ({ ...t, running: true } as Topic) : t))
    const { container } = mount({ topics: patched, selectedTopicId: 'a' })
    await fireEvent.click(toggleFor(container, 'a'))
    const row = rowFor(container, 'a')
    expect(row.classList.contains('is-active')).toBe(true)
    expect(toggleFor(container, 'a').classList.contains('subtree-toggle--running')).toBe(true)
  })

  it('「已归档」分组不受影响', async () => {
    const archived = { ...topic('old', 'root'), status: 'archived', archived_at: '2026-08-01T00:00:00Z' } as Topic
    const { container } = mount({ topics: [...topics, archived] })
    const group = container.querySelector('.archived-toggle') as HTMLElement | null
    if (!group) throw new Error('没有找到「已归档」分组')
    expect(group.textContent).toContain('已归档')
    // 默认收起，展开后归档话题出现
    expect(visibleTitles(container)).not.toContain('old')
    await fireEvent.click(group)
    expect(visibleTitles(container)).toContain('old')
  })
})
