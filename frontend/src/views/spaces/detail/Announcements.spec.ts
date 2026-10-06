// 公告页上几条不看长相的规则：
//
// 1. **成员是纯读的**：一个操作按钮都不该出现（发 / 改 / 删 / 置顶只对所有者与管理员
//    开）。漏了就是给成员一颗点下去必然 403 的按钮。
// 2. **置顶只改那一条**：写出去的是那一条公告自己的 `pinned`，不带别的公告，也不带
//    它的标题正文 —— 两位管理员各改各的，谁也不该覆盖谁。
// 3. **已到期的收起来**：点开「已到期」那一行之前，它们不在页面上。
// 4. **取消发布什么也不发**：弹窗关掉，服务端一条公告都没收到。
import type { Component } from 'vue'
import type { SpaceAnnouncement } from '@/types'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const spaceDetail = vi.fn()
const listAnnouncements = vi.fn()
const publishAnnouncement = vi.fn()
const updateAnnouncement = vi.fn()
const deleteAnnouncement = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spaceDetail(...a),
    listAnnouncements: (...a: unknown[]) => listAnnouncements(...a),
    publishAnnouncement: (...a: unknown[]) => publishAnnouncement(...a),
    updateAnnouncement: (...a: unknown[]) => updateAnnouncement(...a),
    deleteAnnouncement: (...a: unknown[]) => deleteAnnouncement(...a),
  },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

// 富文本编辑器不进 happy-dom：这里测的是写出去什么，不是编辑器本身。
vi.mock('@/components/common/Editor/TipTapEditor.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({ name: 'TipTapEditor', setup: () => () => h('div') }),
    // 挂载时测试工具会问这个模块是不是 Teleport / KeepAlive。
    __isTeleport: false,
    __isKeepAlive: false,
  }
})

// 谁看得到那些按钮，是拿登录的人跟 `space.admins` 对出来的（空间 store 的 `isManager`）。
vi.mock('@/services/account', () => ({
  default: { _user: { value: null as { id: number } | null } },
}))

import Announcements from './Announcements.vue'

import DialogContainer from '@/components/common/DialogContainer.vue'
import i18n, { setLocale } from '@/i18n'
import { dialogs } from '@/plugins/dialog'
import AccountService from '@/services/account'

const SPACE_ID = 11
const OWNER_ID = 4
const DAY = 86_400_000
const NOW = Date.now()

function announcement(id: number, title: string, over: Partial<SpaceAnnouncement> = {}): SpaceAnnouncement {
  return {
    id,
    spaceId: SPACE_ID,
    title,
    // 空正文：卡片不画富文本渲染器，这里只看哪几条出现。
    content: '',
    pinned: false,
    expiresAt: null,
    createdAt: NOW - id * DAY,
    updatedAt: NOW - id * DAY,
    author: { id: OWNER_ID, username: 'linxia', nickname: '林夏', avatarId: null },
    ...over,
  }
}

const CURRENT = [
  announcement(1, '置顶的公告', { pinned: true }),
  announcement(2, '普通的公告'),
  announcement(3, '另一条普通公告'),
]
const EXPIRED = [announcement(9, '已经到期的公告', { expiresAt: NOW - DAY })]

function signIn(userId: number) {
  AccountService._user.value = { id: userId } as never
}

const Blank = defineComponent({ render: () => h('div') })
const Page = defineComponent({ render: () => h('div', [h(Announcements), h(DialogContainer)]) })

async function mount() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/spaces/:spaceId/announcements', name: 'SpacesAnnouncements', component: Blank as Component }],
  })
  await router.push(`/spaces/${SPACE_ID}/announcements`)
  await router.isReady()
  const utils = render(Page, {
    global: { plugins: [createVuetify({ components, directives }), router, createPinia(), i18n] },
  })
  await waitFor(() => expect(listAnnouncements).toHaveBeenCalled())
  return utils
}

function cards(): Element[] {
  return Array.from(document.querySelectorAll('article'))
}

function cardTitled(title: string): Element | undefined {
  return cards().find((c) => c.textContent?.includes(title))
}

function buttonLabelled(root: ParentNode, label: string): HTMLElement | undefined {
  return Array.from(root.querySelectorAll<HTMLElement>('button')).find(
    (b) => b.getAttribute('aria-label') === label || b.textContent?.trim() === label
  )
}

beforeEach(() => {
  // Vuetify 的浮层（v-dialog）会读 `visualViewport`，happy-dom 里没有。
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
  setLocale('zh-CN')
  setActivePinia(createPinia())
  spaceDetail.mockImplementation(async () => ({
    data: {
      space: {
        id: SPACE_ID,
        name: '数据分析课',
        admins: [{ user: { id: OWNER_ID, nickname: '林夏' }, role: 'OWNER' }],
        taskTemplates: '[]',
        classificationTopics: [],
      },
    },
  }))
  listAnnouncements.mockImplementation(async () => ({
    data: { current: CURRENT, expired: EXPIRED, notifyCount: 3 },
  }))
  updateAnnouncement.mockImplementation(async () => ({ data: {} }))
  publishAnnouncement.mockImplementation(async () => ({ data: {} }))
})

afterEach(() => {
  dialogs.splice(0, dialogs.length)
  cleanup()
  vi.clearAllMocks()
})

describe('公告页', () => {
  it('成员是纯读的：操作按钮一个都不出现', async () => {
    signIn(99)
    await mount()
    await waitFor(() => expect(cardTitled('置顶的公告')).toBeDefined())

    expect(cards().flatMap((c) => Array.from(c.querySelectorAll('button')))).toHaveLength(0)
    expect(buttonLabelled(document, '发布公告')).toBeUndefined()

    // 反证：同一个空间换成它的所有者，按钮就都在 —— 否则上面那条可能只是这一页
    // 根本画不出按钮。
    cleanup()
    signIn(OWNER_ID)
    await mount()
    await waitFor(() => expect(buttonLabelled(document, '发布公告')).toBeDefined())
    expect(buttonLabelled(cardTitled('普通的公告')!, '置顶')).toBeDefined()
  })

  it('置顶只改那一条自己的置顶，不带别的公告，也不带它的标题正文', async () => {
    signIn(OWNER_ID)
    await mount()
    await waitFor(() => expect(buttonLabelled(cardTitled('另一条普通公告') ?? document, '置顶')).toBeDefined())

    await fireEvent.click(buttonLabelled(cardTitled('另一条普通公告')!, '置顶')!)

    await waitFor(() => expect(updateAnnouncement).toHaveBeenCalledTimes(1))
    expect(updateAnnouncement).toHaveBeenCalledWith(SPACE_ID, 3, { pinned: true })
  })

  it('已到期的公告收在「已到期」里，点开才出现', async () => {
    signIn(99)
    await mount()
    await waitFor(() => expect(cardTitled('置顶的公告')).toBeDefined())

    expect(cardTitled('已经到期的公告')).toBeUndefined()
    const fold = Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.includes('已到期'))!
    await fireEvent.click(fold)

    await waitFor(() => expect(cardTitled('已经到期的公告')).toBeDefined())
  })

  it('打开发布弹窗再取消，什么也没有发出去', async () => {
    signIn(OWNER_ID)
    await mount()
    await waitFor(() => expect(buttonLabelled(document, '发布公告')).toBeDefined())

    await fireEvent.click(buttonLabelled(document, '发布公告')!)
    await waitFor(() => expect(buttonLabelled(document, '取消')).toBeDefined())
    await fireEvent.click(buttonLabelled(document, '取消')!)

    expect(publishAnnouncement).not.toHaveBeenCalled()
  })

  it('一条公告都没有时，所有者也能发第一条', async () => {
    signIn(OWNER_ID)
    listAnnouncements.mockImplementation(async () => ({ data: { current: [], expired: [], notifyCount: 0 } }))
    await mount()

    await waitFor(() => expect(buttonLabelled(document, '发布公告')).toBeDefined())
  })
})

// 读失败和「一条公告都没有」在屏幕上曾经是同一句话：失败留下的也是空数组，
// 「暂无公告」分不出来（docs/design-system.md §3.10）。这一组钉住失败**留在原地**
// ——说没读到、给一条重试的路，绝不替服务端说「一条都没有」；401/403 是「不给你
// 看」，说没权限且不给重试（再试一次还是同一个 401/403）。
describe('公告没读出来', () => {
  /** 让取公告那一次失败，并等错误块出现。 */
  async function failedPage(error: unknown) {
    listAnnouncements.mockRejectedValue(error)
    const view = await mount()
    await waitFor(() => expect(view.container.querySelector('.base-load-error')).toBeTruthy())
    return view
  }

  it('读失败留在原地说明并给重试，不说成「暂无公告」，也不停在转圈上', async () => {
    const view = await failedPage(new Error('炸了'))
    const block = view.container.querySelector('.base-load-error') as HTMLElement

    expect(block.textContent).toContain('加载公告失败')
    expect(view.queryByText('暂无公告')).toBeNull()
    // 错误的这一块替掉的就是那块内容，不该再留一个还在转的载入指示。
    expect(view.container.querySelector('.v-progress-circular')).toBeNull()
    expect(view.container.querySelector('.loading-container')).toBeNull()
    expect(view.container.querySelector('[role="progressbar"]')).toBeNull()
  })

  it('重试那颗按钮再打一次取数的 api，读成了就把公告画出来', async () => {
    const view = await failedPage(new Error('炸了'))
    const block = view.container.querySelector('.base-load-error') as HTMLElement

    // 这一次让它读成了：重试进去之后错误块应当让位给真正的公告。
    listAnnouncements.mockResolvedValue({ data: { current: CURRENT, expired: EXPIRED, notifyCount: 3 } })
    await fireEvent.click(within(block).getByRole('button'))

    await waitFor(() => expect(listAnnouncements).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(cardTitled('置顶的公告')).toBeDefined())
  })

  it('403 说的是「没权限」，并且不给一颗按不动的重试', async () => {
    // `code: 403` 是 axios 拦截器翻出来的那种（`BusinessError`，见
    // `lib/loadFailure.ts` 的 `isForbidden`），不是 `status`。
    const view = await failedPage(Object.assign(new Error('nope'), { code: 403 }))
    const block = view.container.querySelector('.base-load-error') as HTMLElement

    expect(block.textContent).toContain('你没有权限查看')
    expect(within(block).queryByRole('button')).toBeNull()
    expect(view.queryByText('暂无公告')).toBeNull()
  })
})
