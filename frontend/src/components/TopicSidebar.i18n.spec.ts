/** 这条侧栏整块跟着语言走。
 *
 * 这一份真的把侧栏挂起来渲染一遍，扫的是**整条侧栏**：置顶的七行、项目头、
 * 每一行的展开开关、行尾那颗 ⋯、以及底下的「已归档」组——文字有的在正文里，有的
 * 只在 hover / 读屏才听得到的 `title` 上。所以这里可以整块扫「一个汉字都不剩」，
 * 不像整页（页面上还有别的切片欠着的字）。
 *
 * 带计数的几句英文写的是「零 / 一 / 多」三截（收起几项、含收起的子话题几条新消息），
 * 它们的出口只有 `title`，别处看不到，所以这里按件数各来一遍。
 *
 * 还有一条边界这里守得住：**同一个「展开」有三种来由**——光秃秃的展开、里面有
 * 待你处理的事、里面有芝士在跑。三句走三个不同的键（`expand` / `expandAwaits` /
 * `expandRunning`），而它们唯一的出口是折叠开关那颗按钮的 `title`。所以这一份
 * 得先把树展开到看得见那三种状态为止，再扫。
 *
 * 菜单里的字（重命名 / 归档 / 项目设置）挂在 teleport 出去的 `.v-overlay` 上，
 * 不在挂载点里——扫的是 `document.body`，不然那一半的字扫不到。
 */
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

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  listProjectAgents: vi.fn().mockResolvedValue({ data: [] }),
}))

import TopicSidebar from './TopicSidebar.vue'

import { setLocale } from '@/i18n'

// h() 对 SFC 的具名 props 类型太严，这里只需要它是个组件。
const Sidebar = TopicSidebar as unknown as Component

/** 这个类里有 U+8C48–U+FAFF 那一段，它把 UTF-16 代理对的一半也算成「汉字」——
 *  所以非 BMP 的 emoji 会误报。侧栏上不许有 emoji，正好当闸门用。 */
const CJK = /[㐀-䶿一-鿿豈-﫿]/

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

// root(本体，不占行)
//   ├ a（我参与）→ a1（我参与）→ a1x（在跑）／ a2（等你处理）
//   ├ b（我参与）→ b1                     ← 光秃秃的那种「展开」
//   ├ old（已归档，有一条新消息）
//   └ other（与我无关，收进默认折叠的「其他话题」）→ other1
// 「其他目录」那一组收着，所以它的行不在这份用例的可点范围里——要断言的只是
// 组头那一句，以及别的行都还够得着。三种「展开」来由、两组分组、归档组，一次摆全。
const topics: Topic[] = [
  topic('root', null, { kind: 'root' }),
  topic('a', 'root', { i_participate: true }),
  topic('a1', 'a', { i_participate: true }),
  topic('a1x', 'a1', { i_participate: true, running: true }),
  topic('a2', 'a', { i_participate: true, awaits_me: true }),
  topic('b', 'root', { i_participate: true, can_archive: true }),
  topic('b1', 'b', { i_participate: true }),
  topic('other', 'root', { i_participate: false }),
  topic('other1', 'other', { i_participate: false }),
  topic('old', 'root', {
    i_participate: true,
    status: 'archived',
    archived_at: '2026-08-01T00:00:00Z',
    // 归档组那一行只有后端说能归档才长还原按钮——不给这个标记，那条断言无从谈起。
    can_archive: true,
  }),
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
        // a 收着，底下 a1 / a1x / a2 三行都看不见；那几条未读于是聚到 a 这一行上。
        unreadMap: { a1x: 3, a2: 4, other1: 2, old: 1 },
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
/** 置顶那七行的名字（全局 / 总览 / …）。 */
function pinnedLabels(container: Element): string[] {
  return Array.from(container.querySelectorAll('.pinned-row')).map(
    (el) => el.querySelector('.v-list-item-title')?.textContent?.trim() ?? ''
  )
}
/** 某几行**画出来的字**（不是 title），按出现顺序。 */
function rowTitles(root: ParentNode, selector: string): string[] {
  return Array.from(root.querySelectorAll(selector)).map(
    (el) => el.querySelector('.v-list-item-title')?.textContent?.trim() ?? ''
  )
}
/** 「已归档」那个组头：底下的分组头共用 `.archived-toggle`，只此一个。 */
function archivedToggle(container: Element): HTMLElement {
  const el = container.querySelector('.archived-toggle') as HTMLElement | null
  if (!el) throw new Error('没有找到「已归档」分组')
  return el
}

/** 扫 `root` 里所有看得见的字，外加 title / aria-label 上的字。返回的是「哪儿有
 *  汉字」的清单，空的才算过——报错信息里带上整句，红的时候不用再猜是哪一处。 */
function cjkHits(root: ParentNode): string[] {
  const hits: string[] = []
  const text = (root as Element).textContent ?? ''
  if (CJK.test(text)) hits.push(`textContent=${text.replace(/\s+/g, ' ')}`)
  for (const el of Array.from(root.querySelectorAll('[title], [aria-label]'))) {
    for (const attr of ['title', 'aria-label']) {
      const value = el.getAttribute(attr) ?? ''
      if (CJK.test(value)) hits.push(`${attr}=${value}`)
    }
  }
  return hits
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
  // 这一份会点开 v-menu（项目头那颗、行尾那颗 ⋯），Vuetify 的浮层定位直接读
  // 这几个全局，happy-dom 没有——环境缺件，照补不顺。
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

beforeEach(() => {
  // 哪一层展开着是记在 localStorage 里的（刷新后还得是展开的），不清的话上一条
  // 用例点开的那棵树会留给下一条——而这一份要断言的正是「收着的时候开关上写着
  // 什么」。菜单挂在 teleport 出去的浮层容器上，也一样要收走。
  localStorage.clear()
  document.querySelectorAll('.v-overlay-container').forEach((el) => el.remove())
})

describe('讲中文', () => {
  it('置顶七行、分组头、项目头和行尾的 title 都是中文', async () => {
    setLocale('zh-CN')
    const { container } = mount()

    expect(pinnedLabels(container)).toEqual(['全局', '总览', '看板', '日历', '导出与发布', 'AI 队友', '成员'])
    expect(container.querySelector('.rail-header')?.getAttribute('title')).toBe('项目菜单')
    expect(container.querySelector('.rail-resizer')?.getAttribute('title')).toBe('拖动调整宽度')
    expect(archivedToggle(container).textContent).toContain('已归档')

    // 三种「展开」：光秃秃的、里面有事的、里面有在跑的
    expect(toggleFor(container, 'a').getAttribute('title')).toBe('展开：里面有待处理的事项')
    await fireEvent.click(toggleFor(container, 'a'))
    expect(toggleFor(container, 'a').getAttribute('title')).toBe('收起')
    expect(toggleFor(container, 'a1').getAttribute('title')).toBe('展开：芝士正在里面工作')
    expect(toggleFor(container, 'b').getAttribute('title')).toBe('展开')

    // 行尾那颗 ⋯ 和它底下那两项，只有 hover 才看得见，字全在 title 上
    expect(rowFor(container, 'b').querySelector('.row-actions__btn')?.getAttribute('title')).toBe('更多操作')
    await fireEvent.click(rowFor(container, 'b').querySelector('.row-actions__btn') as HTMLElement)
    await waitFor(() => expect(document.body.textContent).toContain('重命名'))
    expect(document.body.textContent).toContain('归档')
  })
})

describe('讲英文', () => {
  it('整条侧栏一个汉字都不剩', async () => {
    setLocale('en')
    const { container } = mount()

    expect(pinnedLabels(container)).toEqual([
      'Global',
      'Overview',
      'Board',
      'Calendar',
      'Export & publish',
      'Agents',
      'Members',
    ])
    expect(container.querySelector('.rail-header')?.getAttribute('title')).toBe('Project menu')
    expect(container.querySelector('.rail-resizer')?.getAttribute('title')).toBe('Drag to resize')
    expect(rowTitles(container, '.docs-row')).toEqual(['Project docs'])
    expect(archivedToggle(container).textContent).toContain('Archived')
    expect(container.querySelector('.side-subhead--row')?.textContent?.trim()).toBe('Topic')

    expect(cjkHits(document.body), cjkHits(document.body).join(' / ')).toEqual([])
  })

  it('三种「展开」的来由各说各的，而且计数句按件数选形态', async () => {
    setLocale('en')
    const { container } = mount()

    // a 收着：底下三行（a1 / a1x / a2）看不见，未读 3+4 全聚到它这一行上
    expect(toggleFor(container, 'a').getAttribute('title')).toBe('Expand: something needs you')
    const aRow = rowFor(container, 'a')
    expect(aRow.querySelector('.subtree-count')?.getAttribute('title')).toBe('3 items hidden')
    expect(aRow.querySelector('.unread-badge')?.getAttribute('title')).toBe(
      'Including 7 new messages in collapsed subtopics'
    )

    await fireEvent.click(toggleFor(container, 'a'))
    expect(toggleFor(container, 'a').getAttribute('title')).toBe('Collapse')
    // 换了来由：a1 底下那条在跑，而 a1 自己收着
    expect(toggleFor(container, 'a1').getAttribute('title')).toBe('Expand: Cheese is working in here')
    expect(toggleFor(container, 'b').getAttribute('title')).toBe('Expand')

    expect(cjkHits(document.body), cjkHits(document.body).join(' / ')).toEqual([])
  })

  it('只收起一项时，那两个计数句走的是「正好 1」那一截', () => {
    setLocale('en')
    // 一条没有子话题的话题收着，底下正好一项；未读也只给那一条。
    const { container } = mount({
      topics: [
        topic('root', null, { kind: 'root' }),
        topic('solo', 'root', { i_participate: true }),
        topic('solo1', 'solo', { i_participate: true }),
      ],
      unreadMap: { solo1: 1 },
    })
    expect(toggleFor(container, 'solo').getAttribute('title')).toBe('Expand')
    expect(rowFor(container, 'solo').querySelector('.subtree-count')?.getAttribute('title')).toBe('1 item hidden')
    expect(rowFor(container, 'solo').querySelector('.unread-badge')?.getAttribute('title')).toBe(
      'Including 1 new message in collapsed subtopics'
    )
  })

  it('菜单里那两项也是英文 —— 行尾的 ⋯、以及归档组里那颗还原', async () => {
    setLocale('en')
    const { container } = mount()

    await fireEvent.click(rowFor(container, 'b').querySelector('.row-actions__btn') as HTMLElement)
    await waitFor(() => expect(document.body.textContent).toContain('Rename'))
    expect(document.body.textContent).toContain('Archive')
    expect(cjkHits(document.body), cjkHits(document.body).join(' / ')).toEqual([])

    await fireEvent.keyDown(document.body, { key: 'Escape' })
    await fireEvent.click(archivedToggle(container))
    const unarchive = rowFor(container, 'old').querySelector('.split-btn')
    expect(unarchive?.getAttribute('title')).toBe('Unarchive')
    expect(cjkHits(document.body), cjkHits(document.body).join(' / ')).toEqual([])
  })
})

describe('切一次语言', () => {
  it('已经画出来的行和 title 当场跟着换', async () => {
    setLocale('zh-CN')
    const { container } = mount()
    expect(pinnedLabels(container)[1]).toBe('总览')
    expect(toggleFor(container, 'a').getAttribute('title')).toBe('展开：里面有待处理的事项')

    setLocale('en')
    await waitFor(() => expect(pinnedLabels(container)[1]).toBe('Overview'))
    expect(toggleFor(container, 'a').getAttribute('title')).toBe('Expand: something needs you')
    expect(cjkHits(document.body), cjkHits(document.body).join(' / ')).toEqual([])
  })
})
