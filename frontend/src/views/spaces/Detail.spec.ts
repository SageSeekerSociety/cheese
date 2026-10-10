// 空间外壳替侧栏读待审核的题数：只有所有者与管理员读（那个接口对成员不开，读了
// 只会换来一个 403），读到的数就是侧栏「待审核」旁边那个数。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
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

vi.mock('@/network/api/tasks', () => ({
  TasksApi: { list: (...a: unknown[]) => taskList(...a) },
}))

vi.mock('@/network/api/spaces', () => ({
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
    data: {
      space: { id: SPACE_ID, name: '数据结构空间', admins: [{ user: { id: ADMIN_ID }, role: 'ADMIN' }], ...extra },
    },
  })
  taskList.mockResolvedValue({ data: { tasks: [], page: { total: 3 } } })
  listCategories.mockResolvedValue({ data: { categories: [] } })

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/spaces/:spaceId', component: Detail, children: [{ path: '', component: child }] },
      { path: '/spaces', name: 'HomeSpaces', component: Blank },
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
  return Object.assign(useSpaceStore(), { view })
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

// 没过审的板：后端除了详情一律 404。外壳要先认出来，换成一屏「审核中」，而不是
// 把页面挂上去、让每一页各自取数各自弹一条红色的「获取失败」。
describe('所有者打开一块还没过审的空间', () => {
  afterEach(() => {
    cleanup()
    AccountService._user.value = null
    vi.clearAllMocks()
  })

  it('待审核：说它在审核中，不去读分类，也不弹错', async () => {
    setLocale('zh-CN')
    const { view } = await mount(ADMIN_ID, { reviewStatus: 'PENDING' }, Page)

    await view.findByText('空间还在审核中')
    expect(view.queryByText('题目列表')).toBeNull()
    expect(view.getByRole('link', { name: '回到空间列表' }).getAttribute('href')).toBe('/spaces')
    expect(listCategories).not.toHaveBeenCalled()
    expect(taskList).not.toHaveBeenCalled()
    expect(toastError).not.toHaveBeenCalled()
  })

  it('被驳回：说被驳回了，带上驳回原因', async () => {
    setLocale('zh-CN')
    const { view } = await mount(ADMIN_ID, { reviewStatus: 'REJECTED', reviewReason: '名称不清楚' }, Page)

    await view.findByText('空间申请被驳回')
    view.getByText(/名称不清楚/)
    expect(view.queryByText('题目列表')).toBeNull()
    expect(listCategories).not.toHaveBeenCalled()
  })

  it('过审了：照常挂页面、读分类', async () => {
    setLocale('zh-CN')
    const { view } = await mount(ADMIN_ID, { reviewStatus: 'APPROVED' }, Page)

    await view.findByText('题目列表')
    await waitFor(() => expect(listCategories).toHaveBeenCalled())
    expect(view.queryByText('空间还在审核中')).toBeNull()
  })
})
