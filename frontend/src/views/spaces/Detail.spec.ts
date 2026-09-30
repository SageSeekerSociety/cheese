// 空间外壳替侧栏读待审核的题数：只有所有者与管理员读（那个接口对成员不开，读了
// 只会换来一个 403），读到的数就是侧栏「待审核」旁边那个数。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'

const taskList = vi.fn()
const spacesDetail = vi.fn()

vi.mock('@/network/api/tasks', () => ({
  TasksApi: { list: (...a: unknown[]) => taskList(...a) },
}))

vi.mock('@/network/api/spaces', () => ({
  SpacesApi: {
    detail: (...a: unknown[]) => spacesDetail(...a),
    listCategories: vi.fn().mockResolvedValue({ data: { categories: [] } }),
  },
}))

vi.mock('@/services/account', () => ({
  default: { _user: { value: null as { id: number } | null } },
}))

vi.mock('vuetify-sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

import Detail from './Detail.vue'

import i18n from '@/i18n'
import AccountService from '@/services/account'
import { useSpaceStore } from '@/stores/space'

const SPACE_ID = 11
const ADMIN_ID = 4
const Blank = defineComponent({ render: () => h('div') }) as Component

async function mount(userId: number) {
  AccountService._user.value = { id: userId } as never
  spacesDetail.mockResolvedValue({
    data: { space: { id: SPACE_ID, name: '数据结构空间', admins: [{ user: { id: ADMIN_ID }, role: 'ADMIN' }] } },
  })
  taskList.mockResolvedValue({ data: { tasks: [], page: { total: 3 } } })

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/spaces/:spaceId', component: Detail, children: [{ path: '', component: Blank }] }],
  })
  await router.push(`/spaces/${SPACE_ID}`)
  await router.isReady()

  const pinia = createPinia()
  setActivePinia(pinia)
  render({ render: () => h(Detail) }, { global: { plugins: [router, pinia, i18n] } })
  return useSpaceStore()
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
