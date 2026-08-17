// 组件级行为测试：话题列表按「与我的相关性」分成两组之后，屏幕上到底长什么样。
// （lib/topicTree.spec.ts 测的是分组算法本身；这一份测的是接线——组默认收着、
// 组头只给一个点、以及**下组的行和上组的行是同一种形态**。最后这条是这次改动
// 唯一容易被做丢的东西：分组不该让"别人的话题"退化成「已归档」那种平列表。）
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

// root(本体，不占行) → mine（我参与的，带一个子话题）+ theirs（与我无关，带两层）
const topics: Topic[] = [
  topic('root', null),
  topic('mine', 'root', { i_participate: true }),
  topic('mine1', 'mine', { i_participate: true }),
  topic('theirs', 'root', { i_participate: false }),
  topic('theirs1', 'theirs', { i_participate: false }),
  topic('theirs1x', 'theirs1', { i_participate: false }),
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
// 「其他话题」的组头。底部的「已归档」组头共用 .group-toggle，用文字区分。
function othersHead(container: Element): HTMLElement {
  const head = Array.from(container.querySelectorAll('.group-toggle')).find((el) =>
    el.textContent?.includes('其他话题')
  ) as HTMLElement | undefined
  if (!head) throw new Error('没有找到「其他话题」组头')
  return head
}
function hasOthersHead(container: Element): boolean {
  return Array.from(container.querySelectorAll('.group-toggle')).some((el) => el.textContent?.includes('其他话题'))
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

describe('左侧话题列表：按相关性分两组', () => {
  beforeEach(() => localStorage.clear())

  it('我参与的平铺在上面；无关的整棵子树收进默认折叠的「其他话题」', () => {
    const { container } = mount()
    expect(visibleTitles(container)).toEqual(['mine', 'mine1'])
    const head = othersHead(container)
    // 计数是整组的话题数（含组内的子话题），不是只数顶层。
    expect(head.querySelector('.group-count')?.textContent?.trim()).toBe('3')
  })

  it('展开以后，下组的行和上组的行是同一种形态', async () => {
    const { container } = mount()
    await fireEvent.click(othersHead(container))
    expect(visibleTitles(container)).toEqual(['mine', 'mine1', 'theirs', 'theirs1', 'theirs1x'])

    // 同一种行：树形缩进（每层 20px）、子话题的竖向引导线、16px 状态槽、
    // hover 的 ⋯ 操作入口。这些是「已归档」那种平列表没有的东西。
    const deep = rowFor(container, 'theirs1x')
    expect(deep.style.paddingInlineStart).toBe('48px')
    expect(deep.classList.contains('is-sub')).toBe(true)
    expect(deep.querySelector('.v-list-item__prepend .row-slot')).not.toBeNull()
    expect(deep.querySelector('.row-actions')).not.toBeNull()
    // 上组同深度的行，缩进算出来一模一样。
    expect(rowFor(container, 'mine1').style.paddingInlineStart).toBe('28px')
    // 归档行才有的形态不该出现在这一组里。
    expect(deep.classList.contains('topic-row--archived')).toBe(false)
  })

  it('组内照旧能折叠子话题（折叠开关在下组的行上也在）', async () => {
    const { container } = mount()
    await fireEvent.click(othersHead(container))
    const toggle = rowFor(container, 'theirs').querySelector('button.subtree-toggle') as HTMLElement | null
    if (!toggle) throw new Error('下组的父话题行没有折叠开关')
    await fireEvent.click(toggle)
    expect(visibleTitles(container)).toEqual(['mine', 'mine1', 'theirs'])
  })

  it('组头的未读是一个点，不是数字', () => {
    const { container } = mount({ unreadMap: { theirs1: 3, theirs1x: 9 } })
    const dot = othersHead(container).querySelector('.unread-badge--dot')
    expect(dot).not.toBeNull()
    // 点里不写数量：别人话题里有几条与我无关。
    expect(dot?.textContent?.trim()).toBe('')
    expect(othersHead(container).textContent).not.toContain('12')
  })

  it('展开以后组头的点让位给行上的真实数字', async () => {
    const { container } = mount({ unreadMap: { theirs1: 3 } })
    await fireEvent.click(othersHead(container))
    expect(othersHead(container).querySelector('.unread-badge--dot')).toBeNull()
    expect(rowFor(container, 'theirs1').querySelector('.unread-badge')?.textContent?.trim()).toBe('3')
  })

  it('awaits_me 为真的话题永远不折叠——即使 i_participate 是假', () => {
    // 后端保证这个组合不出现（awaits_me ⇒ i_participate），前端不靠这个保证。
    const patched = topics.map((t) => (t.id === 'theirs1' ? ({ ...t, awaits_me: true } as Topic) : t))
    const { container } = mount({ topics: patched })
    // 整棵 theirs 子树被带上来了，"等我处理"那一行一眼就在。
    expect(visibleTitles(container)).toEqual(['mine', 'mine1', 'theirs', 'theirs1', 'theirs1x'])
    expect(hasOthersHead(container)).toBe(false)
  })

  it('选中的话题落在下组时，只漏出通往它的那条路径，其余仍然收着', () => {
    // theirs2 是 theirs 的另一个子话题，和选中的那条路径无关——它必须仍然藏着，
    // 否则"收起来"就名不副实了。（这条和折叠一个父话题时的 reveal 同一个规则。）
    const withSibling = [...topics, topic('theirs2', 'theirs', { i_participate: false })]
    const { container } = mount({ topics: withSibling, selectedTopicId: 'theirs1x' })
    expect(visibleTitles(container)).toEqual(['mine', 'mine1', 'theirs', 'theirs1', 'theirs1x'])
    expect(rowFor(container, 'theirs1x').classList.contains('is-active')).toBe(true)
    // 组头仍然显示"收起来了"，开关不是一颗按了没反应的按钮。
    expect(othersHead(container).textContent).toContain('其他话题')
    expect(othersHead(container).querySelector('.mdi-chevron-right')).not.toBeNull()
    // 而且不写回偏好：离开之后这一组照旧是收起来的。
    expect(localStorage.getItem('cheesex.railOthersOpen.v1:p1')).toBeNull()
  })

  it('选中的话题在下组、又把这一组展开开来：整组都在，开关照旧能收回去', async () => {
    const { container } = mount({ selectedTopicId: 'theirs1x' })
    await fireEvent.click(othersHead(container))
    expect(visibleTitles(container)).toEqual(['mine', 'mine1', 'theirs', 'theirs1', 'theirs1x'])
    await fireEvent.click(othersHead(container))
    // 收回去以后仍然看得见选中的那一条路径，别的都收了。
    expect(visibleTitles(container)).toEqual(['mine', 'mine1', 'theirs', 'theirs1', 'theirs1x'])
    expect(localStorage.getItem('cheesex.railOthersOpen.v1:p1')).toBeNull()
  })

  it('展开状态按项目记住（刷新后重挂仍然是展开的）', async () => {
    const first = mount()
    await fireEvent.click(othersHead(first.container))
    expect(localStorage.getItem('cheesex.railOthersOpen.v1:p1')).toBe('1')
    first.unmount()

    const { container } = mount()
    expect(visibleTitles(container)).toEqual(['mine', 'mine1', 'theirs', 'theirs1', 'theirs1x'])

    // 收回去也记住
    await fireEvent.click(othersHead(container))
    expect(visibleTitles(container)).toEqual(['mine', 'mine1'])
    expect(localStorage.getItem('cheesex.railOthersOpen.v1:p1')).toBeNull()
  })

  it('没有无关话题时，连组头都不出现', () => {
    const { container } = mount({ topics: topics.filter((t) => !t.id.startsWith('theirs')) })
    expect(hasOthersHead(container)).toBe(false)
    expect(visibleTitles(container)).toEqual(['mine', 'mine1'])
  })

  it('一个都不相关时，上组说清楚空的是这一组、不是这个项目', () => {
    const patched = topics.map((t) => (t.id.startsWith('mine') ? ({ ...t, i_participate: false } as Topic) : t))
    const { container } = mount({ topics: patched })
    expect(visibleTitles(container)).toEqual([])
    expect(container.textContent).toContain('暂无与你相关的话题')
    expect(container.textContent).not.toContain('暂无话题')
    expect(othersHead(container).querySelector('.group-count')?.textContent?.trim()).toBe('5')
  })

  it('后端没给这两个字段时一切照旧（老载荷不会把整个项目折起来）', () => {
    const patched = topics.map((t) => {
      const copy = { ...t } as Record<string, unknown>
      delete copy.i_participate
      return copy as unknown as Topic
    })
    const { container } = mount({ topics: patched })
    expect(visibleTitles(container)).toEqual(['mine', 'mine1', 'theirs', 'theirs1', 'theirs1x'])
    expect(hasOthersHead(container)).toBe(false)
  })

  it('归档的无关话题仍然只出现在「已归档」里，不进「其他话题」', () => {
    const archived = topic('old', 'root', {
      status: 'archived',
      archived_at: '2026-08-01T00:00:00Z',
      i_participate: false,
    })
    const { container } = mount({ topics: [...topics, archived] })
    expect(othersHead(container).querySelector('.group-count')?.textContent?.trim()).toBe('3')
    const archivedHead = container.querySelector('.archived-toggle')
    expect(archivedHead?.textContent).toContain('已归档')
  })
})
