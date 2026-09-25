// 组件级行为测试：侧栏装了什么、装在哪一段里。
//
// 这一份守的是 P1「项目侧栏内容配比」的几条验收——置顶导航组、私聊未读的落点、
// 项目文档收成一行、行操作收进一颗 ⋯——都是"屏幕上还剩下什么"的问题，所以全部
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
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import TopicSidebar from './TopicSidebar.vue'

import { setLocale } from '@/i18n'

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
  // 整页形态下项目头那一行填进顶栏那一格（Teleport 到 #app-bar-slot，真实环境里
  // 由 MobileAppBar 画）。落点不存在时 Teleport 会在卸载时炸，所以这里把它摆出来
  // ——和 v-navigation-drawer 必须有 v-layout 是同一类前置条件。
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
        topics,
        selectedTopicId: null,
        loadingTopics: false,
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

// 这一份从**渲染出来的字**上断言置顶行，所以语言必须是它断言的那一种。置顶行的
// 文案现在走 i18n（壳能换词，所以只能是 key，不能是字面量），而 happy-dom 的
// navigator.language 是 en-US——不定住的话这些断言问的是英文那套词。
// 别的断言文案的用例（destinations.spec / App.navigation.spec）用的是同一行。
beforeEach(() => setLocale('zh-CN'))

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
  it('侧栏上常驻的是每天要用的那几样：全局、资料库、成员和项目文档', () => {
    const { container } = mount()
    expect(container.querySelector('.proj-pages')).toBeNull()
    // 看板不在这里——它就是首页，项目名那一行点下去就到。日历一年点几次，收进了
    // 项目名旁边那个菜单。成员留在外面不是因为它天天用，而是因为「退出项目」长在
    // 成员页上——名册一收进 ⋯，没注意到那个 ⋯ 的人就连怎么退出都找不到了。项目
    // 文档和它们排在一起，不压在话题列表底下：话题一多，那个位置就看不见了。
    expect(titlesIn(container, '.pinned-row')).toEqual(['全局', '资料库', '成员', '项目文档'])
  })

  it('点项目名回项目首页，不打开任何房间', async () => {
    const onSelectTopic = vi.fn()
    const push = vi.spyOn(router, 'push').mockResolvedValue(undefined)
    const { container } = mount({ onSelectTopic })
    const name = container.querySelector('.rail-header__home') as HTMLElement
    expect(name.textContent?.trim()).toBe('P1')

    await fireEvent.click(name)

    // 首页就是看板那一页（《项目名》/ 做出了什么 / 看板），项目名点下去就到。
    expect(push).toHaveBeenCalledWith(expect.objectContaining({ name: 'workspace-running' }))
    expect(onSelectTopic).not.toHaveBeenCalled()
    push.mockRestore()
  })

  it('名字和菜单是两个按钮，两个都够得着', () => {
    const { container } = mount()
    const home = container.querySelector('.rail-header__home') as HTMLElement
    const more = container.querySelector('[aria-label="项目菜单"]') as HTMLElement
    // 最常做的事（回首页）不该只能通过先开一个菜单达成，所以它自己是一个按钮。
    expect(home.tagName).toBe('BUTTON')
    expect(home.getAttribute('type')).toBe('button')
    expect(more.tagName).toBe('BUTTON')
    expect(more.getAttribute('type')).toBe('button')
    expect(more.querySelector('.mdi-chevron-down')).not.toBeNull()
  })

  it('项目头高度走 48px 基线（.sidebar-header）', () => {
    const { container } = mount()
    expect(container.querySelector('.rail-header')?.classList.contains('sidebar-header')).toBe(true)
  })
})

describe('私聊的未读落在项目名那一行上', () => {
  // 侧栏原来有一整段私聊（芝士 + 有未读的人）。它撤掉了，名册和每个人的未读都归
  // 成员页；成员那一行后来也进了菜单——但「有人找你」必须仍然在主导航上亮，否则
  // 私聊变成一个只有主动去翻才发现的功能。所以这一组钉的是：那一段真的没了，而
  // 未读没有跟着一起没，它跟着菜单入口走。
  function header(container: Element): Element {
    const row = container.querySelector('.rail-header')
    if (!row) throw new Error('没有找到项目头')
    return row
  }

  it('侧栏里不再有私聊那一段', () => {
    const { container } = mount({ privateUnreadMap: { cheese: 1, zhang: 2 } })
    expect(container.querySelector('.rail-foot')).toBeNull()
    expect(container.querySelector('.private-row')).toBeNull()
    expect(titlesIn(container, '.pinned-row')).not.toContain('芝士')
  })

  it('有人私聊你 → 项目名那一行上亮一个数，是所有私聊未读的总和', () => {
    const { container } = mount({ privateUnreadMap: { cheese: 1, zhang: 2, li: 3 } })
    expect(header(container).querySelector('.unread-badge')?.textContent?.trim()).toBe('6')
  })

  it('没有未读就不亮——徽标不是常驻装饰', () => {
    const { container } = mount()
    expect(header(container).querySelector('.unread-badge')).toBeNull()
  })

  it('话题的未读不会漏到项目名那一行上——两种未读不是一回事', () => {
    const { container } = mount({ unreadMap: { a: 4 } })
    expect(header(container).querySelector('.unread-badge')).toBeNull()
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
    // 置顶行的图标留着：# 和资料库两个各不相同，是能区分行的信息
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

// 行首只有一颗点，而且只为「在等你」亮：看板每一列的状态点对每一行都有，画进标题里
// 就是满屏的点，哪一行都不显眼。看板上落在「待处理」列、但等的是别人的房间，在你的
// 侧栏上不该和等你的那一个长得一样。
describe('C6 等你的那一颗点', () => {
  function rooms(): Topic[] {
    return [
      topic('root', null, 'root'),
      {
        ...topic('mine', 'root'),
        awaits_me: true,
        presentation: { column: 'needs_you', display_status: '待回答' },
      },
      {
        ...topic('theirs', 'root'),
        presentation: { column: 'needs_you', display_status: '待审阅' },
      },
      { ...topic('busy', 'root'), presentation: { column: 'building', display_status: '运行中' } },
    ]
  }
  function rowOf(container: Element, title: string): HTMLElement {
    const row = (Array.from(container.querySelectorAll('.topic-row')) as HTMLElement[]).find(
      (r) => r.querySelector('.v-list-item-title')?.textContent?.trim() === title
    )
    if (!row) throw new Error(`没有找到 ${title} 这一行`)
    return row
  }

  it('只有等你的房间亮点', () => {
    const { container } = mount({ topics: rooms() })
    expect(rowOf(container, 'mine').querySelectorAll('[title="有待处理的事项"]').length).toBe(1)
    expect(rowOf(container, 'theirs').querySelector('[title="有待处理的事项"]')).toBeNull()
    expect(rowOf(container, 'busy').querySelector('[title="有待处理的事项"]')).toBeNull()
  })

  it('看板状态不再以任何记号出现在标题里', () => {
    const { container } = mount({ topics: rooms() })
    for (const title of ['mine', 'theirs', 'busy']) {
      const heading = rowOf(container, title).querySelector('.v-list-item-title')
      expect(heading?.querySelector('[title]')).toBeNull()
    }
  })
})

// 整页形态：手机上话题列表是页面栈的一层，占满内容区。抽屉和"拖宽度"这两样
// 在手机上都不成立——但列表本身一条不能少。
describe('整页形态', () => {
  it('不再是抽屉，也没有可拖的宽度', () => {
    const { container } = mount({ page: true })
    expect(container.querySelector('.v-navigation-drawer')).toBeNull()
    expect(container.querySelector('.rail-resizer')).toBeNull()
  })

  it('装的东西和抽屉形态一样', () => {
    const asDrawer = mount().container.querySelectorAll('.topic-row').length
    const asPage = mount({ page: true }).container.querySelectorAll('.topic-row').length
    expect(asPage).toBeGreaterThan(0)
    expect(asPage).toBe(asDrawer)
  })
})
