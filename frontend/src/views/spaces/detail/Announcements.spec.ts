// 公告页这一批加的是「置顶」。它有两个容易写错的地方，都不是「长什么样」：
//
// 1. **写回用的下标必须是 store 里那份数组的下标，不是排过序的位置。**
//    `stores/space.ts` 的 `updateAnnouncement(index, …)` 是按下标替换的，列表是排过
//    序的副本 —— 把「显示上的第 0 条」当成「数组里的第 0 条」写下去，改的就是**别的
//    公告**。界面上不会报错，只会有一条不相干的公告被置顶。所以这条用例断言的是
//    **PATCH 出去的那份 JSON**，不是屏幕上那颗按钮。
// 2. **成员是纯读的**：一个操作按钮都不该出现（发 / 改 / 删 / 置顶都只对所有者与
//    管理员开）。漏了就是给成员发了一张点下去必然 403 的按钮。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const spaceDetail = vi.fn()
const spaceUpdate = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spaceDetail(...a),
    update: (...a: unknown[]) => spaceUpdate(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

// 读弹窗的正文是 tiptap 富文本；这一批测的是它上面那行「谁 · 什么时候 · 改过没有」，
// 所以把渲染器换成一个只画文本的替身 —— 编辑器本身不进 happy-dom。
vi.mock('@/components/common/Editor/TipTapViewer.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      name: 'TipTapViewer',
      props: { value: { type: String, default: '' } },
      setup: (props) => () => h('div', { class: 'ttv-stub' }, props.value),
    }),
  }
})

// 「谁看得到那些按钮」是拿登录的人跟 `space.admins` 对出来的（空间 store 的 `isManager`）。
vi.mock('@/services/account', () => ({
  default: { _user: { value: null as { id: number; username: string; nickname: string } | null } },
}))

import Announcements from './Announcements.vue'

import DialogContainer from '@/components/common/DialogContainer.vue'
import i18n, { setLocale } from '@/i18n'
import { dialogs } from '@/plugins/dialog'
import AccountService from '@/services/account'

const SPACE_ID = 11
const ROUTE = { name: 'SpacesAnnouncements', params: { spaceId: String(SPACE_ID) } }

/** 原始数组的顺序**故意不是**显示顺序：
 *  - 第 0 格：没置顶的最老一条；
 *  - 第 1 格：没置顶的中间一条；
 *  - 第 2 格：置顶的那一条，而且发布得最早；
 *  - 第 3 格：没置顶的最新一条（不排序的话它会排在最前）。
 *  显示顺序于是是 `[2, 3, 1, 0]` —— **没有一个位置的下标相同**，把显示位置当成数组
 *  下标写下去（这一批最容易写错的地方）一定会被这几条用例抓到。 */
const ANNOUNCEMENTS = [
  { title: '第 0 格：没置顶的老公告', content: '<p>正文</p>', createdAt: 100, updatedAt: 100, publisher: '蔡松洋' },
  { title: '第 1 格：没置顶的中间公告', content: '<p>正文</p>', createdAt: 500, updatedAt: 500, publisher: '马小雨' },
  {
    title: '第 2 格：置顶的公告',
    content: '<p>正文</p>',
    createdAt: 50,
    updatedAt: 50,
    publisher: '蔡松洋',
    pinned: true,
  },
  { title: '第 3 格：没置顶的新公告', content: '<p>正文</p>', createdAt: 900, updatedAt: 900, publisher: '马小雨' },
]

/** 空间随公告一起走：`announcements` 是那一格 **JSON 字符串**。 */
function space(announcements: unknown[]) {
  return {
    id: SPACE_ID,
    name: '数据结构空间',
    intro: '',
    avatarId: null,
    admins: [{ user: { id: 4, username: 'caisongyang', nickname: '蔡松洋' }, role: 'OWNER' }],
    announcements: JSON.stringify(announcements),
    taskTemplates: '[]',
    classificationTopics: [],
    visibleTaskLimit: null,
  }
}

/** 点名前先落一个登录态 —— 角色是拿它跟 `space.admins` 对出来的（4 号是所有者）。 */
function signIn(handle: string) {
  AccountService._user.value = { id: handle === 'caisongyang' ? 4 : 99, username: handle, nickname: '蔡松洋' } as never
}

const Blank = defineComponent({ render: () => h('div') })

/** 对话框本身住在 App.vue（跨路由活着），这一份把这一页和它一起挂起来 —— 读一条公告
 *  是真的弹那个框，`dialog.custom` 里那行「谁 · 什么时候 · 改过没有」于是才看得见。 */
const Page = defineComponent({ render: () => h('div', [h(Announcements), h(DialogContainer)]) })

async function mount() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/spaces/:spaceId/announcements', ...ROUTE, component: Blank as Component }],
  })
  await router.push(`/spaces/${SPACE_ID}/announcements`)
  await router.isReady()

  // 直接挂这一页（和外壳无关）：它自己从路由参数里读空间 id，所以路由得推到那一条上。
  const utils = render(Page, {
    global: { plugins: [createVuetify({ components, directives }), router, createPinia(), i18n] },
  })
  await waitFor(() => expect(spaceDetail).toHaveBeenCalled())
  return utils
}

/** 屏幕上第 n 张公告卡片。 */
function card(n: number): Element | null {
  return document.querySelectorAll('.acard')[n] ?? null
}

/** 卡片上那颗置顶按钮 —— 认图标，不认它是第几颗按钮。 */
function pinButton(n: number): Element | null {
  const buttons = Array.from(card(n)?.querySelectorAll('button') ?? [])
  return buttons.find((b) => b.querySelector('.mdi-pin-outline, .mdi-pin-off-outline')) ?? null
}

describe('公告页的置顶', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    spaceDetail.mockImplementation(async () => ({ data: { space: space(ANNOUNCEMENTS) } }))
    spaceUpdate.mockImplementation(async () => ({ data: { space: space(ANNOUNCEMENTS) } }))
  })

  afterEach(() => {
    // 浮层清单是插件里的模块级状态，跨用例活着：上一个用例没关掉的读弹窗会跟到下一
    // 个用例里。
    dialogs.splice(0, dialogs.length)
    cleanup()
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('置顶的排最前，其余按发布时间倒序', async () => {
    signIn('caisongyang')
    await mount()
    await waitFor(() => expect(card(0)).not.toBeNull())

    const titles = Array.from(document.querySelectorAll('.acard__title')).map((t) => t.textContent)
    expect(titles).toEqual([
      '第 2 格：置顶的公告',
      '第 3 格：没置顶的新公告',
      '第 1 格：没置顶的中间公告',
      '第 0 格：没置顶的老公告',
    ])
  })

  it('置顶的公告带「置顶」标记', async () => {
    signIn('caisongyang')
    await mount()
    await waitFor(() => expect(card(0)).not.toBeNull())

    const chips = Array.from(document.querySelectorAll('.acard__head .v-chip')).map((c) => c.textContent?.trim())
    expect(chips).toEqual(['置顶'])
  })

  it('取消置顶写回的是**数组里那一格**，不是它显示的位置', async () => {
    signIn('caisongyang')
    await mount()
    await waitFor(() => expect(pinButton(0)).not.toBeNull())

    // 屏幕上第 0 张是数组里的第 2 格。按下它，取消的必须是**第 2 格**的置顶。
    await fireEvent.click(pinButton(0)!)

    await waitFor(() => expect(spaceUpdate).toHaveBeenCalled())
    const body = spaceUpdate.mock.calls[0][1] as { announcements: string }
    const written = JSON.parse(body.announcements) as { title: string; pinned?: boolean }[]

    // 顺序也不许变：store 里那份数组是按下标写回的依据。
    expect(written.map((a) => a.title)).toEqual([
      '第 0 格：没置顶的老公告',
      '第 1 格：没置顶的中间公告',
      '第 2 格：置顶的公告',
      '第 3 格：没置顶的新公告',
    ])
    expect(written[2].pinned).toBe(false)
    // 另外三格一个都不许动 —— 按显示下标写就会写到这里来。
    expect(written.map((a) => a.pinned)).toEqual([undefined, undefined, false, undefined])
  })

  it('置顶一条没置顶的，同样落在数组里那一格上', async () => {
    signIn('caisongyang')
    await mount()
    await waitFor(() => expect(pinButton(1)).not.toBeNull())

    // 屏幕上第 1 张是数组里的第 3 格（最新的那条）。
    await fireEvent.click(pinButton(1)!)

    await waitFor(() => expect(spaceUpdate).toHaveBeenCalled())
    const body = spaceUpdate.mock.calls[0][1] as { announcements: string }
    const written = JSON.parse(body.announcements) as { title: string; pinned?: boolean }[]

    expect(written[3].pinned).toBe(true)
    expect(written.map((a) => a.pinned)).toEqual([undefined, undefined, true, true])
  })

  it('成员是纯读的：操作按钮一个都不出现', async () => {
    signIn('someone-else')
    await mount()
    await waitFor(() => expect(card(0)).not.toBeNull())

    expect(document.querySelectorAll('.acard button')).toHaveLength(0)
    expect(Array.from(document.querySelectorAll('button')).some((b) => b.textContent?.includes('发布公告'))).toBe(false)
    // 公告本身还是看得见的 —— 这一页对谁都不设门槛。
    expect(document.body.textContent).toContain('第 2 格：置顶的公告')
    // 下面是**反证**：同一个空间换成它自己的所有者，按钮就都回来了 —— 否则上面那条
    // 「一个按钮都没有」可能只是因为这一页根本渲染不出按钮。
    cleanup()
    signIn('caisongyang')
    await mount()
    await waitFor(() => expect(pinButton(0)).not.toBeNull())
  })
})

// 这一批补的两处：**列表上什么时候发的**（相对时间，超过一周退回日期），以及
// **读一条时框里写着谁、什么时候、改过没有**。相对时间的词全靠「现在」才成立，
// 所以这一组把 Date 钉死。**只假 Date** —— 连 setTimeout 一起假掉，`waitFor`
// 就再也等不到下一拍。
describe('公告页的时间与「已编辑」', () => {
  const NOW = new Date('2026-09-28T12:00:00Z').getTime()
  const DAY = 86_400_000

  /** 三条各占一种：改过的、三天前发的、一周以上的。 */
  const TIMED = [
    {
      title: '改过的一条',
      content: '<p>正文</p>',
      createdAt: NOW - 4 * DAY,
      updatedAt: NOW - DAY,
      publisher: '蔡松洋',
    },
    {
      title: '三天前发的',
      content: '<p>正文</p>',
      createdAt: NOW - 3 * DAY,
      updatedAt: NOW - 3 * DAY,
      publisher: '马小雨',
    },
    {
      title: '很久以前发的',
      content: '<p>正文</p>',
      createdAt: NOW - 40 * DAY,
      updatedAt: NOW - 40 * DAY,
      publisher: '蔡松洋',
    },
  ]

  beforeEach(() => {
    // Vuetify 的浮层（v-dialog）会去读 `visualViewport`，测试环境里没有这个对象，于是
    // 弹窗根本不渲染 —— 断言会以为「读弹窗里什么都没有」。
    if (!('visualViewport' in window)) {
      Object.defineProperty(window, 'visualViewport', {
        configurable: true,
        value: {
          height: 800,
          width: 600,
          offsetTop: 0,
          offsetLeft: 0,
          scale: 1,
          addEventListener: () => {},
          removeEventListener: () => {},
        },
      })
    }
    localStorage.clear()
    setActivePinia(createPinia())
    // 相对时间的词是按语言出的（「3 天前」/ "3 days ago"），这里钉死中文再断言。
    setLocale('zh-CN')
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW)
    spaceDetail.mockImplementation(async () => ({ data: { space: space(TIMED) } }))
    spaceUpdate.mockImplementation(async () => ({ data: { space: space(TIMED) } }))
  })

  afterEach(() => {
    vi.useRealTimers()
    dialogs.splice(0, dialogs.length)
    cleanup()
    vi.clearAllMocks()
    localStorage.clear()
  })

  /** 卡片底部那一行：发布人 · 时间（· 已编辑）。 */
  function foot(n: number): string {
    return (card(n)?.querySelector('.acard__foot')?.textContent || '').trim()
  }

  /** 读弹窗里那行元信息。 */
  function meta(): string {
    return document.querySelector('[data-testid="announcement-meta"]')?.textContent || ''
  }

  it('卡片上的时间说「N 天前」，不说一串日期', async () => {
    await mount()
    await waitFor(() => expect(card(0)).not.toBeNull())

    // 显示顺序（发布时间倒序）：三天前、四天前（改过的那条）、一周以上。
    expect(foot(0)).toContain('3天前')
    expect(foot(1)).toContain('4天前')
  })

  it('超过一周就退回日期，不再说「N 天前」', async () => {
    await mount()
    await waitFor(() => expect(card(2)).not.toBeNull())

    expect(foot(2)).toMatch(/\d{2}-\d{2}/)
    expect(foot(2)).not.toContain('天前')
  })

  it('读弹窗里写着发布人、发布时间，改过的还带「已编辑」', async () => {
    await mount()
    await waitFor(() => expect(card(1)).not.toBeNull())

    await fireEvent.click(card(1)!)
    await waitFor(() => expect(document.querySelector('[data-testid="announcement-meta"]')).not.toBeNull())

    expect(meta()).toContain('蔡松洋')
    expect(meta()).toContain('4天前')
    expect(meta()).toContain('已编辑')
  })

  it('没改过的公告，读弹窗里不写「已编辑」', async () => {
    await mount()
    await waitFor(() => expect(card(0)).not.toBeNull())

    await fireEvent.click(card(0)!)
    await waitFor(() => expect(document.querySelector('[data-testid="announcement-meta"]')).not.toBeNull())

    expect(meta()).toContain('马小雨')
    expect(meta()).toContain('3天前')
    expect(meta()).not.toContain('已编辑')
  })
})

describe('公告页的发布入口', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    spaceUpdate.mockImplementation(async () => ({ data: { space: space([]) } }))
  })

  afterEach(() => {
    dialogs.splice(0, dialogs.length)
    cleanup()
    vi.clearAllMocks()
  })

  it('所有者在一条公告都没有时也能发第一条（空间读回来之后才知道他是所有者）', async () => {
    signIn('caisongyang')
    spaceDetail.mockImplementation(async () => ({ data: { space: space([]) } }))
    await mount()

    await waitFor(() =>
      expect(Array.from(document.querySelectorAll('button')).some((b) => b.textContent?.includes('发布公告'))).toBe(
        true
      )
    )
  })
})
