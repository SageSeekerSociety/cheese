// 空间侧栏「管理」那一段只给所有者与管理员：成员看不到它。管理员看得到待审核的
// 件数（谁去读这个数、成员读不读，见 `views/spaces/Detail.spec.ts`）。
// 「我的学习洞察」那一格只在这块板开着学情分析时出现，成员也有。
import type { Component } from 'vue'

import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, screen, waitFor } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/services/account', () => ({
  default: { _user: { value: null as { id: number } | null } },
}))

const readLearningAnalysis = vi.fn()
vi.mock('@/api/spaces', () => ({
  SpacesApi: { readLearningAnalysis: (...args: unknown[]) => readLearningAnalysis(...args) },
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
      { path: '/spaces/:spaceId/library', name: 'SpacesLibrary', component: Blank },
      { path: '/spaces/:spaceId/learning-insight', name: 'SpacesMyLearningInsight', component: Blank },
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
    // What the server says this reader may do: an admin manages, anyone else views.
    viewerPermissions: userId === ADMIN_ID ? ['view', 'edit_settings', 'teach'] : ['view', 'publish'],
  } as never
  store.setPendingAuditCount(3)

  render(SpaceSidebar, { global: { plugins: [createVuetify({ components, directives }), router, pinia, i18n] } })
}

function learningAnalysis(enabled: boolean) {
  readLearningAnalysis.mockResolvedValue({
    learningAnalysis: { enabled, enabledBy: null, enabledAt: null },
    knowledgePoints: [],
    canManage: false,
  })
}

describe('空间侧栏的管理一段', () => {
  beforeEach(() => {
    setLocale('zh-CN')
    learningAnalysis(false)
  })

  afterEach(() => {
    cleanup()
    AccountService._user.value = null
    vi.clearAllMocks()
  })

  it('成员看不到管理', async () => {
    await mount(99)

    await waitFor(() => expect(screen.getByText('全部题目')).toBeTruthy())
    expect(screen.queryByText('设置')).toBeNull()
    expect(screen.queryByText('待审核')).toBeNull()
  })

  it('管理员看得到管理，待审核带着件数', async () => {
    await mount(ADMIN_ID)

    await waitFor(() => expect(screen.getByText('设置')).toBeTruthy())
    const audit = screen.getByText('待审核').closest('a')
    await waitFor(() => expect(audit?.textContent).toContain('3'))
  })
})

describe('空间侧栏的「我的学习洞察」', () => {
  beforeEach(() => {
    setLocale('zh-CN')
  })

  afterEach(() => {
    cleanup()
    AccountService._user.value = null
    vi.clearAllMocks()
  })

  it('板开着学情分析时，成员看得到这一格', async () => {
    learningAnalysis(true)
    await mount(99)

    const link = await screen.findByText('我的学习洞察')
    expect(link.closest('a')?.getAttribute('href')).toBe(`/spaces/${SPACE_ID}/learning-insight`)
  })

  it('板没开时不摆', async () => {
    learningAnalysis(false)
    await mount(99)

    await waitFor(() => expect(readLearningAnalysis).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText('全部题目')).toBeTruthy())
    expect(screen.queryByText('我的学习洞察')).toBeNull()
  })

  it('读不到开关时也不摆', async () => {
    readLearningAnalysis.mockRejectedValue(new Error('offline'))
    await mount(99)

    await waitFor(() => expect(readLearningAnalysis).toHaveBeenCalled())
    expect(screen.queryByText('我的学习洞察')).toBeNull()
  })
})
