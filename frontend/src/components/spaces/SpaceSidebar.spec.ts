// 空间侧栏「管理」那一段只给所有者与管理员：成员看不到它，也不去读待审核的题数
// （那个接口对成员不开，读了只会换来一个 403）。管理员看得到待审核的件数。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const taskList = vi.fn()

vi.mock('@/network/api/tasks', () => ({
  TasksApi: { list: (...a: unknown[]) => taskList(...a) },
}))

vi.mock('@/services/account', () => ({
  default: { _user: { value: null as { id: number } | null } },
}))

// 抽屉外壳要应用的布局，这里只看里面那张目录。
vi.mock('@/components/common/Navigation/SecondaryNavigation.vue', async () => {
  const { defineComponent, h } = await import('vue')
  return {
    default: defineComponent({
      setup:
        (_, { slots }) =>
        () =>
          h('nav', slots.default?.()),
    }),
  }
})

import SpaceSidebar from './SpaceSidebar.vue'

import i18n, { setLocale } from '@/i18n'
import AccountService from '@/services/account'
import { useSpaceStore } from '@/stores/space'

const SPACE_ID = 11
const ADMIN_ID = 4
const Blank = defineComponent({ render: () => h('div') }) as Component

async function mount(userId: number) {
  AccountService._user.value = { id: userId } as never

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/inbox', name: 'inbox', component: Blank },
      { path: '/spaces/:spaceId/announcements', name: 'SpacesAnnouncements', component: Blank },
      { path: '/spaces/:spaceId/tasks', name: 'SpacesDetailTasksList', component: Blank },
      { path: '/spaces/:spaceId/manage/audit', name: 'SpacesDetailAuditTasks', component: Blank },
      { path: '/spaces/:spaceId/manage/members', name: 'SpacesDetailMembers', component: Blank },
      { path: '/spaces/:spaceId/manage/analytics', name: 'SpacesDetailAnalytics', component: Blank },
      { path: '/spaces/:spaceId/manage/settings', name: 'SpacesDetailSettings', component: Blank },
    ],
  })
  await router.push(`/spaces/${SPACE_ID}/tasks`)
  await router.isReady()

  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useSpaceStore()
  store.currentSpaceId = SPACE_ID
  store.currentSpace = {
    id: SPACE_ID,
    name: '数据结构空间',
    admins: [{ user: { id: ADMIN_ID }, role: 'ADMIN' }],
  } as never

  render(SpaceSidebar, { global: { plugins: [createVuetify({ components, directives }), router, pinia, i18n] } })
}

describe('空间侧栏的管理一段', () => {
  beforeEach(() => {
    setLocale('zh-CN')
    taskList.mockResolvedValue({ data: { tasks: [], page: { hasMore: true, total: 3 } } })
  })

  afterEach(() => {
    cleanup()
    AccountService._user.value = null
    vi.clearAllMocks()
  })

  it('成员看不到管理，也不读待审核的题数', async () => {
    await mount(99)

    await waitFor(() => expect(screen.getByText('全部题目')).toBeTruthy())
    expect(screen.queryByText('设置')).toBeNull()
    expect(screen.queryByText('待审核')).toBeNull()
    expect(taskList).not.toHaveBeenCalled()
  })

  it('管理员看得到管理，待审核带着件数', async () => {
    await mount(ADMIN_ID)

    await waitFor(() => expect(screen.getByText('设置')).toBeTruthy())
    expect(taskList).toHaveBeenCalledWith(expect.objectContaining({ space: SPACE_ID, approved: 'NONE' }))
    const audit = screen.getByText('待审核').closest('a')
    await waitFor(() => expect(audit?.textContent).toContain('3'))
  })
})
