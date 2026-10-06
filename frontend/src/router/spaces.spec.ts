// 两件事钉在路由层：
// 1. 空间设置的每一栏都能单独链接：把地址发给另一位管理员，他打开的就是那一栏，
//    不是被带回第一栏。
// 2. 管理那一段（`/spaces/:spaceId/manage/*`）有门：所有者与管理员放行，别人被领到
//    无权限页，而不是让页面自己拉到 403 再画一个原始报错框。
//
// 打的是产品用的那份路由表（`./spaces`）和它挂的那个守卫，不是重写一份判据 ——
// 抄一份到测试里，测的就是抄本。
import type { RouteRecordRaw } from 'vue-router'

import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// 守卫会用这两条路取数（`useSpaceData`）：详情给这块板与它的管理员名单，题目列表
// 读待审核数。都挡在假数据上，测试不碰网络。
const detail = vi.fn()

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: { detail: (...a: unknown[]) => detail(...a) },
}))
vi.mock('@/network/api/tasks', () => ({ TasksApi: { list: vi.fn(), applications: vi.fn() } }))
vi.mock('vuetify-sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }))

// `isManager` 读的是「我」，那份身份住在 account 这个单例上。它也是守卫 await 的那
// 一个（冷打开时会话还没恢复完时，一个真管理员不该被挡）。
vi.mock('@/services/account', () => ({
  default: {
    _user: { value: null as { id: number } | null },
    sessionRestored: Promise.resolve(),
  },
}))

import spaces from './spaces'

import AccountService from '@/services/account'

const SPACE_ID = 42
const OWNER_ID = 7
const ADMIN_ID = 8
const MEMBER_ID = 99

type Admin = { user: { id: number }; role: 'OWNER' | 'ADMIN' }

const owner = (id = OWNER_ID): Admin => ({ user: { id }, role: 'OWNER' })

// 路由树和重定向照旧，页面不挂。
function withoutViews(record: RouteRecordRaw): RouteRecordRaw {
  return {
    ...record,
    components: { default: { template: '<div />' } },
    children: record.children?.map(withoutViews),
  } as RouteRecordRaw
}

function router() {
  return createRouter({ history: createMemoryHistory(), routes: [withoutViews(spaces)] })
}

/** 这次导航里「我是谁」和「这块板的管理员是谁」。 */
function signedInAs(userId: number, admins: Admin[]) {
  setActivePinia(createPinia())
  // 只关心「我是谁」这一个字段，界面上别的字段这里用不到。
  ;(AccountService._user as { value: { id: number } | null }).value = { id: userId }
  detail.mockResolvedValue({ data: { space: { id: SPACE_ID, name: '数据结构空间', admins } } })
}

beforeEach(() => {
  detail.mockReset()
  AccountService._user.value = null
})

describe('空间设置的地址', () => {
  beforeEach(() => signedInAs(OWNER_ID, [owner()]))

  it.each([
    ['basic', 'SpacesDetailSettingsBasic'],
    ['categories', 'SpacesDetailSettingsCategories'],
    ['templates', 'SpacesDetailSettingsTemplates'],
    ['invite-codes', 'SpacesDetailSettingsInviteCodes'],
    ['domain-groups', 'SpacesDetailSettingsDomainGroups'],
    ['templates/create', 'SpacesDetailCreateTemplate'],
    ['templates/2/edit', 'SpacesDetailEditTemplate'],
  ])('直接打开 %s 这一栏，停在这一栏', async (tab, name) => {
    const r = router()
    await r.push(`/spaces/${SPACE_ID}/manage/settings/${tab}`)
    expect(r.currentRoute.value.name).toBe(name)
  })
})

describe('管理那一段的门', () => {
  it.each([
    ['manage/audit', 'SpacesDetailAuditTasks'],
    ['manage/members', 'SpacesDetailMembers'],
    ['manage/analytics', 'SpacesDetailAnalyticsOverview'],
    // 设置落在它自己那条地址上（桌面上落到第一栏是组件里的 watch，路由层不管）。
    ['manage/settings', 'SpacesDetailSettings'],
  ])('所有者打开 %s：放行', async (path, name) => {
    signedInAs(OWNER_ID, [owner()])
    const r = router()
    await r.push(`/spaces/${SPACE_ID}/${path}`)
    expect(r.currentRoute.value.name).toBe(name)
  })

  it('管理员放行', async () => {
    signedInAs(ADMIN_ID, [owner(), { user: { id: ADMIN_ID }, role: 'ADMIN' }])
    const r = router()
    await r.push(`/spaces/${SPACE_ID}/manage/members`)
    expect(r.currentRoute.value.name).toBe('SpacesDetailMembers')
  })

  it.each([
    ['manage/audit'],
    ['manage/members'],
    ['manage/analytics'],
    ['manage/settings'],
    ['manage/settings/invite-codes'],
  ])('成员打开 %s：被挡到无权限页', async (path) => {
    signedInAs(MEMBER_ID, [owner()])
    const r = router()
    await r.push(`/spaces/${SPACE_ID}/${path}`)
    expect(r.currentRoute.value.name).toBe('SpaceManageDenied')
    expect(r.currentRoute.value.params.spaceId).toBe(String(SPACE_ID))
  })

  it('这块板我根本不在里面（详情 404）：一样被挡到无权限页', async () => {
    signedInAs(MEMBER_ID, [])
    detail.mockRejectedValueOnce(new Error('Resource space not found'))
    const r = router()
    await r.push(`/spaces/${SPACE_ID}/manage/members`)
    expect(r.currentRoute.value.name).toBe('SpaceManageDenied')
  })
})
