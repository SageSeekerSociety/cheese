// 空间外壳替侧栏读待审核的题数：只有所有者与管理员读（那个接口对成员不开，读了
// 只会换来一个 403），读到的数就是侧栏「待审核」旁边那个数。
import type { Component } from 'vue'

import { defineComponent, h, markRaw } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

const taskList = vi.fn()
const spacesDetail = vi.fn()
const listCategories = vi.fn()
const toastError = vi.fn()

vi.mock('@/api/taskBoard', () => ({
  TaskBoardApi: { list: (...a: unknown[]) => taskList(...a) },
}))

// 侧栏「我的草稿」旁边的数，换空间时读一次。
vi.mock('@/api/taskDrafts', () => ({
  TaskDraftsApi: { list: vi.fn().mockResolvedValue([]) },
}))

vi.mock('@/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spacesDetail(...a),
    listCategories: (...a: unknown[]) => listCategories(...a),
  },
}))

vi.mock('@/services/account', () => ({
  default: { _user: { value: null as { id: number } | null } },
}))

vi.mock('vuetify-sonner', () => ({
  toast: { success: vi.fn(), error: (...a: unknown[]) => toastError(...a) },
}))

import Detail from './Detail.vue'

import i18n, { setLocale } from '@/i18n'
import AccountService from '@/services/account'
import { useSpaceStore } from '@/stores/space'

const SPACE_ID = 11
const ADMIN_ID = 4
const Blank = defineComponent({ render: () => h('div') }) as Component
const Page = defineComponent({ render: () => h('div', '题目列表') }) as Component

async function mount(userId: number, extra: Record<string, unknown> = {}, child: Component = Blank) {
  AccountService._user.value = { id: userId } as never
  spacesDetail.mockResolvedValue({
    space: { id: SPACE_ID, name: '数据结构空间', admins: [{ user: { id: ADMIN_ID }, role: 'ADMIN' }], ...extra },
  })
  taskList.mockResolvedValue({ tasks: [], page: { total: 3 } })
  listCategories.mockResolvedValue({ categories: [] })

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId', component: Detail, children: [{ path: '', component: child }] },
      { path: '/space-applications/:spaceId', name: 'HomeSpaceApplication', component: Blank },
    ],
  })
  await router.push(`/spaces/${SPACE_ID}`)
  await router.isReady()

  const pinia = createPinia()
  setActivePinia(pinia)
  const view = render(
    { render: () => h(Detail) },
    { global: { plugins: [router, pinia, i18n, createVuetify({ components, directives })] } }
  )
  // markRaw: the store is reactive, and a router put into it would have currentRoute unwrapped.
  return Object.assign(useSpaceStore(), { view, router: markRaw(router) })
}

describe('空间外壳读待审核的题数', () => {
  afterEach(() => {
    cleanup()
    AccountService._user.value = null
    vi.clearAllMocks()
  })

  it('成员不读', async () => {
    const store = await mount(99)

    await waitFor(() => expect(store.currentSpace?.id).toBe(SPACE_ID))
    expect(taskList).not.toHaveBeenCalled()
    expect(store.pendingAuditCount).toBe(0)
  })

  it('管理员读，读到的数给侧栏', async () => {
    const store = await mount(ADMIN_ID)

    await waitFor(() => expect(store.pendingAuditCount).toBe(3))
    expect(taskList).toHaveBeenCalledWith(expect.objectContaining({ space: SPACE_ID, approved: 'NONE' }))
  })
})

// 没过审的板：后端除了详情一律 404。外壳要先认出来，换到这份申请上，而不是
// 把页面挂上去、让每一页各自取数各自弹一条红色的「获取失败」。
describe('所有者打开一块还没过审的空间', () => {
  afterEach(() => {
    cleanup()
    AccountService._user.value = null
    vi.clearAllMocks()
  })

  it.each(['PENDING', 'REJECTED'])('%s：换到这份申请上，不挂页面、不读分类、不弹错', async (reviewStatus) => {
    setLocale('zh-CN')
    const { view, router } = await mount(ADMIN_ID, { reviewStatus }, Page)

    await waitFor(() => expect(router.currentRoute.value.name).toBe('HomeSpaceApplication'))
    expect(router.currentRoute.value.params.spaceId).toBe(String(SPACE_ID))
    expect(view.queryByText('题目列表')).toBeNull()
    expect(listCategories).not.toHaveBeenCalled()
    expect(taskList).not.toHaveBeenCalled()
    expect(toastError).not.toHaveBeenCalled()
  })

  it('过审了：照常挂页面、读分类', async () => {
    setLocale('zh-CN')
    const { view } = await mount(ADMIN_ID, { reviewStatus: 'APPROVED' }, Page)

    await view.findByText('题目列表')
    await waitFor(() => expect(listCategories).toHaveBeenCalled())
  })
})
