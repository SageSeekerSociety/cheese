// 组件级行为测试：侧栏装了什么、装在哪一段里。
//
// 这一份守的是 P1「项目侧栏内容配比」的几条验收——置顶导航组、私聊未读的落点、
// 项目文档收成一行、行操作收进一颗 ⋯——都是"屏幕上还剩下什么"的问题，所以全部
// 从渲染结果上断言，不去读组件内部状态。
// （TopicSidebar.collapse.spec.ts 守的是子话题折叠，两份互不重叠。）
import type { Component } from 'vue'
import type { Topic } from '@/cx_types'

import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { VLayout } from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { screenHeader } from '@/composables/useScreenHeader'

import ScreenHeaderTitle from './common/Navigation/ScreenHeaderTitle.vue'
import TopicSidebar from './TopicSidebar.vue'

import { setLocale, t } from '@/i18n'

const Sidebar = TopicSidebar as unknown as Component

function topic(id: string, parentId: string | null, kind = 'channel'): Topic {
  return {
    id,
    project_id: 'p1',
    parent_id: parentId,
    title: id,
    kind,
    status: 'active',
    can_manage: true,
    joined: true,
    created_by: 'u',
    created_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
  } as Topic
}

const topics: Topic[] = [topic('root', null, 'root'), topic('a', 'root'), topic('b', 'root')]

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/teams/:handle', name: 'TeamsDetailDefault', component: defineComponent({ setup: () => () => h('div') }) },
    { path: '/:pathMatch(.*)*', name: 'catch-all', component: defineComponent({ setup: () => () => h('div') }) },
  ],
})

// v-navigation-drawer 必须活在一个 v-layout 里，否则 Vuetify 直接抛
// "Could not find injected layout"——所以挂一个最小宿主把它包起来。
// Reads the declared header in its own render, after the sidebar beside it has set up.
const BarTitle = defineComponent({
  setup: () => () => h(ScreenHeaderTitle, { header: screenHeader.value, fallback: '' }),
})

const Host = defineComponent({
  props: { inner: { type: Object, required: true } },
  setup(props) {
    // ScreenHeaderTitle stands in for the phone top bar: in page form the project row
    // is the bar's content, declared by the sidebar (useScreenHeader).
    return () =>
      h(VLayout, null, {
        default: () => [h(Sidebar, props.inner as Record<string, unknown>), h(BarTitle)],
      })
  },
})

function mount(inner: Record<string, unknown> = {}, pinia = createPinia()) {
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
    global: { plugins: [vuetify, router, pinia] },
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
  // 右键弹出的菜单定位在一个点上，Vuetify 那时要读它；同样是环境缺件。
  if (!document.elementFromPoint) document.elementFromPoint = () => null
})

describe('C1 置顶导航组', () => {
  it('项目名下面是总览和资料库，综合是频道分组里的第一个频道', () => {
    const { container } = mount()
    // 其余的页都在点项目名弹出的菜单里（.claude/rules/project-sidebar.md）。
    expect(titlesIn(container, '[aria-label="项目页面"] .pinned-row')).toEqual(['总览', '资料库'])
    // 综合排在「频道」标题下面，和别的频道同一组。
    const heading = container.querySelector('.side-subhead')!
    const general = container.querySelector('[data-row-actions="root"]')!
    expect(general.textContent).toContain('综合')
    expect(heading.compareDocumentPosition(general) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('点项目名弹出项目菜单，不换页、不打开任何房间', async () => {
    const onSelectTopic = vi.fn()
    const push = vi.spyOn(router, 'push').mockResolvedValue(undefined)
    const { container, baseElement } = mount({ onSelectTopic })
    const name = container.querySelector('[aria-label="项目菜单"]') as HTMLElement
    expect(name.textContent?.trim()).toBe('P1')

    await fireEvent.click(name)

    await waitFor(() => expect(baseElement.querySelector('.v-overlay--active .v-list-item')).not.toBeNull())
    expect(push).not.toHaveBeenCalled()
    expect(onSelectTopic).not.toHaveBeenCalled()
    push.mockRestore()
  })

  it('项目头高度走 48px 基线（.sidebar-header）', () => {
    const { container } = mount()
    expect(container.querySelector('.rail-header')?.classList.contains('sidebar-header')).toBe(true)
  })
})

describe('项目名上面写着它归哪个团队', () => {
  const teamLine = (root: Element) => root.querySelector('.rail-header__team')

  it('团队的项目写团队名，点了去团队的项目列表', () => {
    const { container } = mount({
      projects: [
        { id: 'p1', name: 'P1', created_at: '', owner_handle: 'alice', team_handle: 'zhishi', team_name: '知是' },
      ],
    })
    expect(teamLine(container)?.textContent?.trim()).toBe('知是')
    expect(teamLine(container)?.getAttribute('href')).toBe('/teams/zhishi')
  })

  it('自己名下的项目写「个人」，指向自己那一页', () => {
    localStorage.setItem('user', JSON.stringify({ id: 1, username: 'linxia' }))
    const { container } = mount({
      projects: [
        { id: 'p1', name: 'P1', created_at: '', owner_handle: 'linxia', team_handle: 'linxia', team_name: 'linxia' },
      ],
    })
    expect(teamLine(container)?.textContent?.trim()).toBe('个人')
    expect(teamLine(container)?.getAttribute('href')).toBe('/teams/linxia')
  })

  // 别人名下的个人项目：那一页只有他本人打得开，所以对被邀请进来的人不画这一行。
  it('别人名下的项目不画这一行', () => {
    localStorage.setItem('user', JSON.stringify({ id: 1, username: 'linxia' }))
    const { container } = mount({
      projects: [
        { id: 'p1', name: 'P1', created_at: '', owner_handle: 'alice', team_handle: 'alice', team_name: 'alice' },
      ],
    })
    expect(teamLine(container)).toBeNull()
  })

  it('点团队名不打开项目菜单', async () => {
    const { container, baseElement } = mount({
      projects: [
        { id: 'p1', name: 'P1', created_at: '', owner_handle: 'alice', team_handle: 'zhishi', team_name: '知是' },
      ],
    })
    await fireEvent.click(teamLine(container)!)
    await waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/teams/zhishi'))
    expect(baseElement.querySelector('.v-overlay--active .v-list-item')).toBeNull()
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
    const { container } = mount({ unreadMap: { a: { count: 4, new: true, messages: 4 } } })
    expect(header(container).querySelector('.unread-badge')).toBeNull()
  })
})

describe('C4 项目文档', () => {
  async function docsItem(container: Element, baseElement: Element): Promise<HTMLElement> {
    await fireEvent.click(container.querySelector('[aria-label="项目菜单"]') as HTMLElement)
    let found: HTMLElement | undefined
    await waitFor(() => {
      found = Array.from(baseElement.querySelectorAll('.v-overlay--active .v-list-item')).find(
        (el) => el.textContent?.trim() === '项目文档'
      ) as HTMLElement | undefined
      expect(found).toBeTruthy()
    })
    return found!
  }

  it('在项目菜单里，点它去项目目标', async () => {
    const onSelectDocs = vi.fn()
    const { container, baseElement } = mount({ onSelectDocs })
    ;(await docsItem(container, baseElement)).click()
    expect(onSelectDocs).toHaveBeenCalledWith('charter')
  })

  it('三种文档里的任何一种打开时，菜单里这一项都是选中态', async () => {
    for (const kind of ['charter', 'weeklies', 'memory']) {
      const { container, baseElement, unmount } = mount({ activeDocs: kind })
      expect((await docsItem(container, baseElement)).classList.contains('v-list-item--active')).toBe(true)
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

  it('没有子频道、也没有人等你的频道行，槽里是 #，和综合同一个记号', () => {
    const { container } = mount()
    const row = topicRowFor(container, 'a')
    const slot = slotsOf(row)[0]
    expect(slot.querySelector('.mdi-pound')).not.toBeNull()
    // 没开过任务、也没活在跑的行不亮灯——没有那个空着的第八个点
    expect(row.querySelector('.row-light')).toBeNull()
    expect(container.querySelector('[data-row-actions="root"] .mdi-pound')).not.toBeNull()
  })

  it('频道行不画工作成员头像，只保留一盏有事等你的灯', () => {
    const busy = topics.map((item) =>
      item.id === 'a'
        ? ({ ...item, awaits_me: true, activity: [{ member: 'cheese-a1', kind: 'working', since: 1 }] } as Topic)
        : item
    )
    const { container } = mount({ topics: busy })
    const row = topicRowFor(container, 'a')
    expect(row.querySelector('.row-slot .row-light')).toBeNull()
    expect(row.querySelectorAll('.row-light')).toHaveLength(1)
    expect(row.querySelector('.row-light--yellow')).not.toBeNull()
    expect(row.querySelector('[role="img"]')?.getAttribute('aria-label')).toBe(t('work.sidebar.light.yellow'))
    expect(row.querySelector('.cheese-avatar')).toBeNull()
    expect(row.querySelector('[data-state]')).toBeNull()

    const second = mount({ topics: [...busy, topic('a-sub', 'a')] })
    const parent = topicRowFor(second.container, 'a')
    expect(parent.querySelector('button.subtree-toggle')).not.toBeNull()
    expect(parent.querySelectorAll('.row-light')).toHaveLength(1)
    expect(parent.querySelector('[role="img"]')?.getAttribute('aria-label')).toBe(t('work.sidebar.light.yellow'))
  })

  it('打字的人不画在侧栏上：这份列表隔一阵才读一次，打字几秒就过去了', () => {
    const typing = topics.map((t) =>
      t.id === 'a' ? ({ ...t, activity: [{ member: 'zhang', kind: 'typing', since: 1, expires_in: 5 }] } as Topic) : t
    )
    const { container } = mount({ topics: typing })
    expect(topicRowFor(container, 'a').querySelector('.rail-members__one')).toBeNull()
  })

  it.each(['mention', 'failed'])('等待成员（%s）不再给频道行添加头像或报错红灯', (reason) => {
    const rows = topics.map((item) =>
      item.id === 'a'
        ? ({
            ...item,
            waits: [{ member: 'cheese-a1', reason, since: new Date(Date.now() - 6 * 60_000).toISOString() }],
          } as Topic)
        : item
    )
    const { container } = mount({ topics: rows })
    const row = topicRowFor(container, 'a')
    expect(row.querySelector('.cheese-avatar')).toBeNull()
    expect(row.querySelector('[data-state]')).toBeNull()
    expect(row.querySelector('.row-light')).toBeNull()
  })

  it('未读只给名称加粗，不再显示未读数字，也不把未读当作选中', () => {
    const { container } = mount({ selectedTopicId: 'a', unreadMap: { b: { count: 3, new: true, messages: 3 } } })
    const selected = topicRowFor(container, 'a')
    const unread = topicRowFor(container, 'b')
    expect(selected.classList.contains('is-active')).toBe(true)
    expect(unread.classList.contains('is-active')).toBe(false)
    expect(selected.querySelector('.unread-badge')).toBeNull()
    expect(unread.querySelector('.unread-badge')).toBeNull()
    expect(unread.textContent?.trim()).toBe('b')
    expect(unread.querySelector('.title-unread')?.textContent).toBe('b')
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

  // 右键一行，弹出的就是 ⋯ 那一份操作；右键不是点开这个房间。
  it('右键一行弹出这一行的操作，不打开这个房间', async () => {
    const onSelectTopic = vi.fn()
    const { container } = mount({ onSelectTopic })
    const row = container.querySelector('[data-row-actions="a"]') as HTMLElement
    const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 40, clientY: 60 })
    row.dispatchEvent(event)
    await vi.waitFor(() => {
      const items = Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).map((n) =>
        n.textContent?.trim()
      )
      expect(items).toEqual([
        t('work.room.menu.newTask'),
        t('work.room.menu.allTasks'),
        t('work.room.menu.rename'),
        t('work.channel.leave'),
      ])
    })
    expect(event.defaultPrevented).toBe(true)
    expect(onSelectTopic).not.toHaveBeenCalled()
  })

  // 综合不能改名或退出，不为凑够四项而扩大权限。
  it('综合那一行也有 ⋯，只保留它有权使用的新建任务和全部任务', async () => {
    const { container } = mount()
    const row = container.querySelector('[data-row-actions="root"]') as HTMLElement
    if (!row) throw new Error('没有找到「综合」那一行')
    const more = row.querySelector('.row-actions .v-btn')
    if (!more) throw new Error('「综合」那一行的行尾没有那颗 ⋯')
    await fireEvent.click(more as HTMLElement)
    await vi.waitFor(() => {
      const items = Array.from(document.querySelectorAll('.v-overlay .v-list-item-title')).map((n) =>
        n.textContent?.trim()
      )
      expect(items).toEqual([t('work.room.menu.newTask'), t('work.room.menu.allTasks')])
    })
  })

  it('排序菜单已经不在侧栏里', () => {
    const { container } = mount()
    const buttons = Array.from(container.querySelectorAll('.v-btn')) as HTMLElement[]
    expect(buttons.some((b) => (b.getAttribute('title') ?? '').startsWith('排序'))).toBe(false)
  })
})

// 行尾那一盏黄灯只为「在等你」亮：看板每一列的状态点对每一行都有，画出来就是满屏的
// 灯，哪一行都不显眼。看板上落在「待处理」列、但等的是别人的房间，在你的侧栏上不该
// 和等你的那一个长得一样。
describe('C6 等你的那一盏黄灯', () => {
  it.each([false, true])('综合合并频道和折叠任务的灯、去掉数量，page=%s 的箭头只发折叠事件', async (page) => {
    const onToggleTasksFold = vi.fn()
    const onSelectTopic = vi.fn()
    const { container } = mount({
      page,
      topics: [{ ...topic('root', null, 'root'), awaits_me: true }],
      unreadMap: { root: { count: 4, new: true, messages: 4 } },
      roomTaskTotals: { root: 7 },
      roomTasks: { root: [{ id: 'task-root', room_id: 'root', title: 'Task', status: 'open' }] },
      foldedChannels: { root: true },
      onToggleTasksFold,
      onSelectTopic,
    })
    const row = container.querySelector('[data-row-actions="root"]') as HTMLElement
    const arrow = row.querySelector('button[aria-expanded="false"]') as HTMLElement
    const title = row.querySelector('.v-list-item-title') as HTMLElement
    expect(row.querySelectorAll('.row-light')).toHaveLength(1)
    expect(row.querySelector('[role="img"]')?.getAttribute('aria-label')).toBe(t('work.sidebar.light.yellow'))
    expect(row.querySelector('.unread-badge')).toBeNull()
    expect(row.textContent?.trim()).toBe('综合')
    expect(arrow.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    await fireEvent.click(arrow)

    expect(onToggleTasksFold).toHaveBeenCalledExactlyOnceWith('root')
    expect(onSelectTopic).not.toHaveBeenCalled()
  })

  function rooms(): Topic[] {
    return [
      topic('root', null, 'root'),
      {
        ...topic('mine', 'root'),
        awaits_me: true,
        presentation: { column: 'needs_you', phrase: 'awaiting_answer' },
      },
      {
        ...topic('theirs', 'root'),
        presentation: { column: 'needs_you', phrase: 'awaiting_review' },
      },
      { ...topic('busy', 'root'), presentation: { column: 'building', phrase: 'running' } },
      // 有人回复了我、我还没处理：说「有事等你」的第二件事，画的是同一盏灯。
      { ...topic('answered', 'root'), replied_to_me: true },
    ]
  }
  function rowOf(container: Element, title: string): HTMLElement {
    const row = (Array.from(container.querySelectorAll('.topic-row')) as HTMLElement[]).find(
      (r) => r.querySelector('.v-list-item-title')?.textContent?.trim() === title
    )
    if (!row) throw new Error(`没有找到 ${title} 这一行`)
    return row
  }

  it('只有等你的房间亮灯', () => {
    const { container } = mount({ topics: rooms() })
    const mine = rowOf(container, 'mine').querySelector('.row-light--yellow')
    expect(mine).not.toBeNull()
    // 悬停和读屏都靠它：这一盏说的是「有事等你」，不是看板那一列的名字
    expect(mine?.getAttribute('title')).toBe('有事等你')
    expect(rowOf(container, 'theirs').querySelector('.row-light')).toBeNull()
    expect(rowOf(container, 'busy').querySelector('.row-light')).toBeNull()
  })

  it('有人回复了我、我还没处理，画的也是这同一盏灯', () => {
    const { container } = mount({ topics: rooms() })
    const row = rowOf(container, 'answered').querySelector('.row-light--yellow')
    expect(row).not.toBeNull()
    expect(row?.getAttribute('title')).toBe('有事等你')
  })

  it('看板状态不再以任何记号出现在标题里', () => {
    const { container } = mount({ topics: rooms() })
    for (const title of ['mine', 'theirs', 'busy']) {
      const heading = rowOf(container, title).querySelector('.v-list-item-title')
      expect(heading?.querySelector('[title]')).toBeNull()
    }
  })
})

// 名称前那一格同时可能站着频道记号、子话题箭头和任务箭头。合成一格以后，任何一样
// 都不能因为另一样在就消失——私密频道的锁说的是「谁能进来」，它比「这个频道有几条
// 活」更不能被省略；两个箭头各管一层，也不能互相替代。
describe('名称前的记号与两种折叠并存', () => {
  it('私密频道挂着任务时，锁和私密提示仍在，任务箭头只收任务、不选中频道', async () => {
    const onToggleTasksFold = vi.fn()
    const onSelectTopic = vi.fn()
    const secret = { ...topic('secret', 'root'), members_only: true }
    const { container } = mount({
      topics: [topic('root', null, 'root'), secret],
      roomTaskTotals: { secret: 2 },
      onToggleTasksFold,
      onSelectTopic,
    })
    const row = container.querySelector('[data-row-actions="secret"]') as HTMLElement
    expect(row).not.toBeNull()

    // 成员可见性是这一行的前提，不能拿折叠箭头把它换掉。
    const glyph = row.querySelector('.row-glyph')
    expect(glyph).not.toBeNull()
    expect(glyph?.classList.contains('mdi-lock-outline')).toBe(true)
    expect(glyph?.getAttribute('title')).toBe(t('work.channel.privateTip'))

    const fold = row.querySelector('button.task-fold__btn')
    expect(fold).not.toBeNull()
    await fireEvent.click(fold as Element)

    expect(onToggleTasksFold).toHaveBeenCalledExactlyOnceWith('secret')
    expect(onSelectTopic).not.toHaveBeenCalled()
  })

  it('公共频道挂着任务时不再补一个多余的 #，任务箭头就是那一格里的记号', () => {
    const { container } = mount({
      topics: [topic('root', null, 'root'), topic('a', 'root')],
      roomTaskTotals: { a: 2 },
    })
    const row = container.querySelector('[data-row-actions="a"]') as HTMLElement
    expect(row.querySelector('.row-glyph')).toBeNull()
    expect(row.querySelector('button.task-fold__btn')).not.toBeNull()
  })
})

// 「综合」自己没话说时，收起来的任务仍然要在那一行上说出来。
describe('综合自身无灯时，收起来的任务灯仍落在唯一那盏灯上', () => {
  function task(over: Record<string, unknown>): Record<string, unknown> {
    return {
      id: 'task-root',
      room_id: 'root',
      title: 'Task',
      title_source: 'user',
      status: 'open',
      presentation: { column: 'todo' },
      ...over,
    }
  }

  it.each([
    ['黄：有事等我', task({ awaits_me: true }), '.row-light--yellow'],
    ['呼吸绿：正在跑', task({ moving: true }), '.row-light--breathe'],
  ])('%s', (_name, foldedTask, selector) => {
    const { container } = mount({
      // 「综合」自身没有任何状态事实，灯只能来自收起来的任务块。
      topics: [topic('root', null, 'root')],
      roomTaskTotals: { root: 1 },
      roomTasks: { root: [foldedTask] },
      foldedChannels: { root: true },
    })
    const row = container.querySelector('[data-row-actions="root"]') as HTMLElement
    expect(row.querySelectorAll('.row-light')).toHaveLength(1)
    expect(row.querySelector(selector)).not.toBeNull()
  })
})

// 任务行上「在做」的图标会转，行尾就不再亮呼吸绿：同一行只有一样东西在动。收起来时
// 图标看不见了，那一堆照旧在频道行上说「有任务正在进行」。
describe('在做的任务行不亮呼吸绿', () => {
  const moving = {
    id: 'task-moving',
    room_id: 'root',
    title: 'Moving task',
    title_source: 'user',
    status: 'open',
    owner_handle: 'someone-else',
    moving: true,
    presentation: { column: 'building', phrase: 'running' },
  }

  it('展开时任务行行尾没有那盏呼吸的绿灯', () => {
    const { container } = mount({
      topics: [topic('root', null, 'root')],
      roomTaskTotals: { root: 1 },
      roomTasks: { root: [moving] },
    })
    const row = (Array.from(container.querySelectorAll('.rail-task-wrap')) as HTMLElement[]).find((r) =>
      r.textContent?.includes('Moving task')
    )
    expect(row).toBeTruthy()
    expect(row!.querySelector('.row-light--green')).toBeNull()
  })

  it('收起来时频道行替它说有任务正在进行', () => {
    const { container } = mount({
      topics: [topic('root', null, 'root')],
      roomTaskTotals: { root: 1 },
      roomTasks: { root: [moving] },
      foldedChannels: { root: true },
    })
    const row = container.querySelector('[data-row-actions="root"]') as HTMLElement
    expect(row.querySelector('.row-light--green')).not.toBeNull()
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

  it('整页形态每行都有可点击的 ⋯，综合也一样，点击不打开房间', async () => {
    const onSelectTopic = vi.fn()
    const { container, baseElement } = mount({ page: true, onSelectTopic })
    for (const id of ['root', 'a', 'b']) {
      expect(
        container.querySelector(`[data-row-actions="${id}"] [title="${t('work.sidebar.moreActions')}"]`)
      ).not.toBeNull()
    }
    const more = container.querySelector(`[data-row-actions="a"] [title="${t('work.sidebar.moreActions')}"]`)
    await fireEvent.click(more as HTMLElement)
    await waitFor(() => {
      const items = Array.from(baseElement.querySelectorAll('[role="menuitem"]')).map((item) =>
        item.textContent?.trim()
      )
      expect(items).toEqual([
        t('work.room.menu.newTask'),
        t('work.room.menu.allTasks'),
        t('work.room.menu.rename'),
        t('work.channel.leave'),
      ])
    })
    expect(onSelectTopic).not.toHaveBeenCalled()
  })

  it('装的东西和抽屉形态一样', () => {
    const asDrawer = mount().container.querySelectorAll('.topic-row').length
    const asPage = mount({ page: true }).container.querySelectorAll('.topic-row').length
    expect(asPage).toBeGreaterThan(0)
    expect(asPage).toBe(asDrawer)
  })
})
