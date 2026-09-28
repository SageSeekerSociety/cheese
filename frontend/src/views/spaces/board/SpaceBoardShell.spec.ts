// 外壳上「管理员设置」这一格：**谁能点、点了看到什么**。
//
// 原型里这一项只在所有者手上是活的，对管理员是灰的、副标题写清楚为什么点不动，
// 点开是一张只读的名字与角色表。这三件事里前两件在**下拉菜单**里、第三件在弹窗里，
// 所以这份用例从外壳装起，一路点到弹窗上 —— 断言的是屏幕上真的出现了什么。
//
// 名单必须是**接口那份**：`Space.admins`（含所有者那一条），不是本地拼的。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import i18n, { setLocale } from '@/i18n'

const spaceDetail = vi.fn()
const listInviteCodes = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spaceDetail(...a),
    listInviteCodes: (...a: unknown[]) => listInviteCodes(...a),
  },
}))

vi.mock('@/network/api/tasks', () => ({
  TasksApi: { list: vi.fn(async () => ({ data: { tasks: [] } })) },
}))

vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import SpaceBoardShell from './SpaceBoardShell.vue'
import { loadBoard } from './store'

const SPACE_ID = 11

// 下拉与导航都靠命名路由，路由得把它们都认得出来（不然 router-link 会警告）。
const Blank = defineComponent({ render: () => h('div') })
const NAMES = [
  'SpaceBoardHome',
  'SpaceBoardMine',
  'SpaceBoardAnnouncements',
  'SpaceBoardReview',
  'SpaceBoardMembers',
  'SpaceBoardAnalytics',
  'SpacesCourseHome',
  'SpacesDetail',
]

// 下拉是一个 VOverlay：测试环境里 visualViewport 与 devicePixelRatio 都没有，
// 不补上根本挂不起来（Vuetify 摆放浮层时会用到它们）。
beforeAll(() => {
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
  vi.stubGlobal('devicePixelRatio', 1)
})
afterAll(() => vi.unstubAllGlobals())

beforeAll(() => setLocale('zh-CN'))

/** 真接口 `Space.admins` 那一份：所有者与管理员在同一个数组里，各自带角色。 */
const ADMINS = [
  { user: { id: 4, username: 'caisongyang', nickname: '蔡松洋' }, role: 'OWNER' },
  { user: { id: 5, username: 'maxiaoyu', nickname: '马霄宇' }, role: 'ADMIN' },
]

function space() {
  return {
    id: SPACE_ID,
    name: '数据结构空间',
    intro: '',
    avatarId: null,
    admins: ADMINS,
    announcements: '[]',
    taskTemplates: '[]',
    classificationTopics: [],
    visibleTaskLimit: null,
  }
}

/** 「我是谁」是登录态：角色是拿它跟 `space.admins` 对出来的。 */
function signIn(handle: string) {
  const user = ADMINS.find((a) => a.user.username === handle)?.user
  localStorage.setItem('user', JSON.stringify(user))
}

async function mount(handle: string) {
  signIn(handle)
  spaceDetail.mockImplementation(async () => ({ data: { space: space() } }))
  listInviteCodes.mockImplementation(async () => ({ data: { inviteCodes: [] } }))
  // 先把空间装好：外壳挂载时角色就得是对的（它靠 watch immediate，不会重来一次）。
  await loadBoard(SPACE_ID, true)

  const router = createRouter({
    history: createMemoryHistory(),
    // 每一条都带 `:spaceId`：外壳里的 `router-link` 都带着这个参数，路由不声明它
    // 就会一条条警告「参数被丢掉了」。
    routes: NAMES.map((name) => ({ path: `/${name}/:spaceId`, name, component: Blank as Component })),
  })
  await router.push(`/SpaceBoardHome/${SPACE_ID}`)
  await router.isReady()

  const utils = render(SpaceBoardShell, {
    props: { spaceId: SPACE_ID },
    global: { plugins: [createVuetify({ components, directives }), router, createPinia(), i18n] },
  })
  await waitFor(() => expect(document.body.textContent).toContain('数据结构空间'))
  return { ...utils, router }
}

/** 点开头部那块下拉，等那一项画出来。 */
async function openMenu() {
  await fireEvent.click(document.querySelector('.board__title--clickable') as Element)
  await waitFor(() => expect(screen.getByText('管理员设置')).toBeTruthy())
  return screen.getByText('管理员设置')
}

/** 这一项是不是灰的（Vuetify 给禁用的 `v-list-item` 加这个类）。 */
function isDisabled(el: Element): boolean {
  return el.closest('.v-list-item')?.classList.contains('v-list-item--disabled') ?? false
}

afterEach(() => {
  cleanup()
  localStorage.clear()
  vi.clearAllMocks()
})

describe('管理员设置', () => {
  it('管理员看到的那一项是灰的，副标题说清楚为什么点不动', async () => {
    await mount('maxiaoyu')

    const item = await openMenu()
    expect(isDisabled(item)).toBe(true)
    expect(item.closest('.v-list-item')?.textContent).toContain('只有所有者能改')
  })

  it('所有者点得动，打开的是接口那份名单（含角色），而且是只读的', async () => {
    const { router } = await mount('caisongyang')

    const item = await openMenu()
    expect(isDisabled(item)).toBe(false)

    await fireEvent.click(item)

    // 名单来自 `Space.admins`：所有者与管理员都在，各自带着角色。
    await waitFor(() => expect(screen.getByText('蔡松洋')).toBeTruthy())
    expect(screen.getByText('马霄宇')).toBeTruthy()
    expect(screen.getByText('所有者')).toBeTruthy()
    expect(screen.getByText('管理员')).toBeTruthy()

    // 只读：弹窗里只有「关闭」这一个按钮。
    const buttons = screen.getAllByRole('button').filter((b) => b.textContent?.includes('关闭'))
    expect(buttons).toHaveLength(1)

    // 它在原地开一块弹窗，**不把人送到老那一页**去（那是改动前的行为）。
    expect(router.currentRoute.value.name).toBe('SpaceBoardHome')
  })
})
