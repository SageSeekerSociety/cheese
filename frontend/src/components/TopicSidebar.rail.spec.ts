// 组件级行为测试：侧栏装了什么、装在哪一段里。
//
// 这一份守的是 P1「项目侧栏内容配比」的四条验收——置顶导航组、私聊固底、项目
// 文档收成一行、行操作收进一颗 ⋯——都是"屏幕上还剩下什么"的问题，所以全部
// 从渲染结果上断言，不去读组件内部状态。
// （TopicSidebar.collapse.spec.ts 守的是子话题折叠，两份互不重叠。）
import type { Component } from 'vue'
import type { ProjectMemberRow, Topic } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VLayout } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, describe, expect, it, vi } from 'vitest'

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

const topics: Topic[] = [topic('root', null, 'root'), topic('a', 'root'), topic('b', 'root')]

function member(handle: string, name: string): ProjectMemberRow {
  return { user_handle: handle, name, role: 'member' } as ProjectMemberRow
}

const members: ProjectMemberRow[] = [
  member('me', '我'),
  member('zhang', '张衡'),
  member('li', '李甘'),
  member('cai', '蔡松洋'),
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
        members,
        meHandle: 'me',
        ...inner,
      },
    },
    global: { plugins: [vuetify, router, createPinia()] },
  })
}

function titlesIn(root: Element, selector: string): string[] {
  return Array.from(root.querySelectorAll(selector)).map(
    (el) => el.querySelector('.v-list-item-title')?.textContent?.trim() ?? ''
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
  // 项目头现在整块是 v-menu 的 activator，所以这份用例里真的会打开一个 overlay。
  // Vuetify 的定位策略直接读全局 visualViewport，happy-dom 没有——同 ResizeObserver
  // 一样是环境缺件，不补的话第一个点击用例炸掉、后面每一个挂载都跟着塌。
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

describe('C1 置顶导航组', () => {
  it('全局 / 总览 / 日历 是三行与话题行同语法的列表行，不再是 pills', () => {
    const { container } = mount()
    expect(container.querySelector('.proj-pages')).toBeNull()
    expect(titlesIn(container, '.pinned-row')).toEqual(['全局', '总览', '日历'])
  })

  it('点项目名不打开任何房间——它开的是项目菜单', async () => {
    const onSelectTopic = vi.fn()
    const { container } = mount({ onSelectTopic })
    const name = container.querySelector('.rail-header__name') as HTMLElement
    expect(name.textContent?.trim()).toBe('P1')
    await fireEvent.click(name)
    expect(onSelectTopic).not.toHaveBeenCalled()
  })

  it('整个项目头是菜单入口，而且键盘够得着', () => {
    const { container } = mount()
    const header = container.querySelector('.rail-header') as HTMLElement
    // 整块可点就得整块可聚焦：activator 必须是真按钮，不是挂了 click 的 div
    expect(header.tagName).toBe('BUTTON')
    expect(header.getAttribute('type')).toBe('button')
    // 名字长在这块里面，所以点名字就是点菜单
    expect(container.querySelector('.rail-header__name')?.closest('button')).toBe(header)
    // 右边的图标只是"这里能展开"的指示，不再是唯一能点的靶子
    expect(header.querySelector('.mdi-chevron-down')).not.toBeNull()
    expect(header.querySelector('.v-btn')).toBeNull()
  })

  it('项目头高度走 48px 基线（.sidebar-header）', () => {
    const { container } = mount()
    expect(container.querySelector('.rail-header')?.classList.contains('sidebar-header')).toBe(true)
  })
})

describe('C3 私聊固底', () => {
  it('私聊住在尾段，不在中段那个唯一的滚动容器里', () => {
    const { container } = mount()
    const foot = container.querySelector('.rail-foot')
    if (!foot) throw new Error('没有找到侧栏尾段')
    expect(foot.querySelector('.private-row')).not.toBeNull()
    const scroll = container.querySelector('.rail-scroll')
    if (!scroll) throw new Error('没有找到侧栏中段的滚动容器')
    expect(scroll.querySelector('.private-row')).toBeNull()
  })

  it('默认只有芝士常驻，全员名单不再铺在侧栏里', () => {
    const { container } = mount()
    expect(titlesIn(container, '.private-row')).toEqual(['芝士'])
  })

  it('有未读的人、以及正在聊的那一个，才会出现在列表里', () => {
    const { container } = mount({ privateUnreadMap: { zhang: 2 }, activePeer: 'li' })
    expect(titlesIn(container, '.private-row')).toEqual(['芝士', '张衡', '李甘'])
    expect(titlesIn(container, '.private-row')).not.toContain('蔡松洋')
  })
})

describe('C4 项目文档', () => {
  it('侧栏只占一行，点它去章程', async () => {
    const onSelectDocs = vi.fn()
    const { container } = mount({ onSelectDocs })
    const rows = container.querySelectorAll('.docs-row')
    expect(rows.length).toBe(1)
    expect(titlesIn(container, '.docs-row')).toEqual(['项目文档'])
    ;(rows[0] as HTMLElement).click()
    expect(onSelectDocs).toHaveBeenCalledWith('charter')
  })

  it('四种文档里的任何一种打开时，这一行都是选中态', () => {
    for (const kind of ['charter', 'decisions', 'weeklies', 'memory']) {
      const { container, unmount } = mount({ activeDocs: kind })
      expect(container.querySelector('.docs-row')?.classList.contains('is-active')).toBe(true)
      unmount()
    }
  })
})

// 行左边只有一个 16px 槽，按优先级换租客。这一组守的是"槽里到底站着谁"，
// 折叠聚合那一半在 TopicSidebar.collapse.spec.ts 里。
describe('行左边那一个槽', () => {
  function slotsOf(row: Element): Element[] {
    return Array.from(row.querySelectorAll('.v-list-item__prepend .row-slot'))
  }
  function topicRowFor(container: Element, title: string): HTMLElement {
    const row = Array.from(container.querySelectorAll('.topic-row')).find(
      (el) => el.querySelector('.topic-title .text-truncate')?.textContent?.trim() === title
    )
    if (!row) throw new Error(`没有找到话题行: ${title}`)
    return row as HTMLElement
  }

  it('每一行都只有一个槽——叶子行不再留一格死占位', () => {
    const { container } = mount()
    const rows = Array.from(container.querySelectorAll('.topic-row, .pinned-row'))
    expect(rows.length).toBeGreaterThan(0)
    for (const row of rows) {
      expect(slotsOf(row).length).toBe(1)
    }
  })

  it('话题行上那颗每行都一样的装饰图标已经不在了', () => {
    const { container } = mount()
    const row = topicRowFor(container, 'a')
    expect(row.querySelector('.mdi-message-text-outline')).toBeNull()
    expect(row.querySelector('.row-glyph')).toBeNull()
    // 置顶行的图标留着：# / 总览 / 日历 三个各不相同，是能区分行的信息
    expect(container.querySelector('.pinned-row .row-glyph')).not.toBeNull()
  })

  it('没有子话题、也没有状态的行，槽是空的', () => {
    const { container } = mount()
    const slot = slotsOf(topicRowFor(container, 'a'))[0]
    expect(slot.querySelector('.await-dot')).toBeNull()
    expect(slot.querySelector('.running-dot')).toBeNull()
    expect(slot.querySelector('.v-icon')).toBeNull()
  })

  it('等你处理压过芝士在跑，两者都压不过折叠开关', () => {
    const both = topics.map((t) => (t.id === 'a' ? ({ ...t, awaits_me: true, running: true } as Topic) : t))
    const { container } = mount({ topics: both })
    const slot = slotsOf(topicRowFor(container, 'a'))[0]
    expect(slot.querySelector('.await-dot')).not.toBeNull()
    expect(slot.querySelector('.running-dot')).toBeNull()

    // 同一个话题一旦有了子话题，槽让给折叠开关
    const withChild = [...both, topic('a-sub', 'a')]
    const second = mount({ topics: withChild })
    const parent = topicRowFor(second.container, 'a')
    expect(parent.querySelector('button.subtree-toggle')).not.toBeNull()
    expect(parent.querySelector('.await-dot')).toBeNull()
  })

  it('选中的行和有未读的行给的不是同一个标记', () => {
    const { container } = mount({ selectedTopicId: 'a', unreadMap: { b: 3 } })
    const selected = topicRowFor(container, 'a')
    const unread = topicRowFor(container, 'b')
    expect(selected.classList.contains('is-active')).toBe(true)
    expect(selected.querySelector('.unread-badge')).toBeNull()
    expect(unread.classList.contains('is-active')).toBe(false)
    expect(unread.querySelector('.unread-badge')?.textContent?.trim()).toBe('3')
  })

  it('右侧不再有相对时间——排序位置已经把它说过一遍了', () => {
    const { container } = mount()
    expect(container.querySelector('.row-time')).toBeNull()
  })
})

describe('C5 行操作', () => {
  it('hover 层里只剩一颗 ⋯，不再是三颗并排的按钮', () => {
    const { container } = mount()
    const actions = container.querySelector('.topic-row .row-actions')
    if (!actions) throw new Error('没有找到行操作层')
    expect(actions.querySelectorAll('.v-btn').length).toBe(1)
  })

  it('排序菜单已经不在侧栏里', () => {
    const { container } = mount()
    const buttons = Array.from(container.querySelectorAll('.v-btn')) as HTMLElement[]
    expect(buttons.some((b) => (b.getAttribute('title') ?? '').startsWith('排序'))).toBe(false)
  })
})
